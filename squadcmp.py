"""같은 OVR 비교 자료(122단계 · 조각 ①) — 1만 위 안 구단주의 **마지막 감독모드 선발 11장**을 사용자의 서비스 키로 받아 `squads.db` 에 둔다.

rank.db 마지막 스냅숏 1만 명 → 사람마다 (닉네임 → 계정 → 감독모드 목록 3개 → 상세) → 선발. 계산·화면은 조각 ③, 카드 채우기는 조각 ②.

| 규칙(ROADMAP "## 122단계") | 어디 |
|---|---|
| 저장은 프로필 번호·선발·경기 시각·확인 시각·실패뿐 — **계정 번호·닉네임을 저장하지 않는다**(W2) | `SCHEMA` |
| 끄거나 전제가 꺼지면 전부 지우고 WAL 까지 비운다(W3) · 파일이 없으면 만들지 않는다 | `purge` |
| 14일 정리 — 선발은 `lineup_at`, 줄은 `checked_at` 기준 · 다시 받기는 `checked_at` · 설정과 무관하게 돈다 | `prune` · `targets` |
| 실패를 적을 때 앞서 받은 선발을 덮지 않는다 | `_save_fail` |
| 스냅숏이 36시간보다 오래면 받지 않는다(낡은 명단에 하루 몫을 쓰지 않게) | `collect` |
| 오류는 넥슨 코드로만 가르고 표에 없는 것은 전부 "저장하지 않음"(W6) | `_fetch_one` |
| `OPENAPI00005` 즉시 멈춤 + `key_day` · `00010`·`00011` 즉시 멈춤(표시 없음) | `STOP_CODES` |
| gone·format·빈 목록이 20명 이어지면 저장 없이 멈춤(format 은 그날 `format_day`) | `_Book` |
| 하루 계수와 켠 뒤 누적을 한 트랜잭션에서 · 429 두 번이면 멈춤 · 누적 5,000 미만이면 개발 키 | `store.squad_take` · `_Quota` |
| 상세는 nocache · 전부 재시도 없음 · 늘 직접(중앙 서버 아님) | `_fetch_one` · `app_main.SquadCmpLoader` |

보류 줄(`_Book`)은 **정상으로 끝났을 때만** 저장한다 — 키 거절·점검·연결 실패·429 로 멈춘 실행의 보류(20명 미만)는 버린다. 계획은 "20명 미만으로
끝나면 저장"이지만, 점검이 시작되는 순간의 00004 몇 개를 "사라진 사람" 으로 3일 적게 된다. 버려도 다음 틱에 다시 물을 뿐이다.

동시 요청은 사람끼리 `SQUAD_CMP_WORKERS` — 요청과 하루 계수는 작업 풀에서(스레드마다 fifa.db 연결), `squads.db` 쓰기는 부른 스레드 하나에서만.
"""
from __future__ import annotations

import random
import sqlite3
import struct
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config
import matchtime
import ownerdist
import store
from nexon_api import EP_ID, EP_USER_MATCH, KEY_INVALID_CODE, NexonAPIError

SUB_POSITION = 28
LINEUP_N = 11
GONE_CODE = ownerdist.NICKNAME_GONE_CODE   # 맞는 키로 없는 닉네임
NO_DATA_CODE = "OPENAPI00009"
STOP_CODES = {KEY_INVALID_CODE: "key", "OPENAPI00010": "down", "OPENAPI00011": "down"}
GONE, NOGAME, FORMAT, EMPTY = "gone", "nogame", "format", "empty"   # empty 는 이어짐만 세는 이름 — 저장은 nogame
KEY_DAY, FORMAT_DAY = "key_day", "format_day"
DAY_S = 86_400

SCHEMA = """
CREATE TABLE IF NOT EXISTS squads (
    profile_sn INTEGER PRIMARY KEY,
    match_at   INTEGER,           -- 그 선발로 뛴 경기(epoch 초)
    lineup     BLOB,              -- 정렬한 (spId, spPosition, spGrade) 11개(unpack_lineup)
    lineup_at  INTEGER,           -- 그 선발을 받은 때(실패로는 안 바뀐다)
    checked_at INTEGER NOT NULL,  -- 마지막으로 물은 때
    fail       TEXT               -- NULL · gone · nogame · format
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

OFF_REASON_TEXT = {
    "devkey": "넥슨이 호출 한도를 알려 껐습니다. 개발 단계 키로 보입니다(서비스 단계 키가 필요합니다)",
    "key": "API 키를 바꿔 껐습니다. 새 키가 서비스 단계면 다시 켜세요",
    "blocked": "넥슨이 요청을 계속 막아 스스로 껐습니다",
}


# ── squads.db ────────────────────────────────────────────────────────────────

def db_files(path: Path) -> list[Path]:
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


def open_db(path: Path | str, timeout: float = store.OPEN_TIMEOUT_S) -> sqlite3.Connection:
    """열고 없으면 만든다. 모든 연결 secure_delete(store 머리말과 같은 까닭)."""
    conn = sqlite3.connect(str(path), timeout=timeout)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")   # 잃어도 다시 받으면 된다 — 한 명 = 한 트랜잭션이라 커밋이 1만 번이다
        conn.execute("PRAGMA secure_delete=ON")
        conn.executescript(SCHEMA)
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def open_existing(path: Path | str, timeout: float = store.OPEN_TIMEOUT_S) -> sqlite3.Connection | None:
    """있을 때만 연다 — 읽기·지우기 쪽이 새 설치에 파일을 만들지 않게."""
    return open_db(path, timeout) if Path(path).exists() else None


def is_corrupt(e: BaseException) -> bool:
    """깨진 파일인가(잠김 등은 아니다 — 그건 다음 틱에 다시)."""
    code = getattr(e, "sqlite_errorcode", None)
    return isinstance(e, sqlite3.DatabaseError) and code is not None and (code & 0xFF) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def drop_if_corrupt(path: Path | str) -> bool:
    """squads.db 가 정말 깨졌으면 파일을 지운다 → 지웠나. 부르는 쪽이 자기 연결을 먼저 닫는다.
    오류가 fifa.db·rank.db 에서 났을 수도 있어 이 파일을 따로 검사한 뒤에만 지운다. 다른 쪽이 열고 있어 못 지우면 다음 틱에."""
    path = Path(path)
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path), timeout=config.SQUAD_CMP_UI_TIMEOUT_S)
    try:
        bad = conn.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
    except sqlite3.DatabaseError as e:
        bad = is_corrupt(e)
    finally:
        conn.close()
    if not bad:
        return False
    for f in db_files(path):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            return False
    return True


def has_data(path: Path | str) -> bool:
    """지울 것이 있나 — 줄이 있거나 WAL 이 안 비었다(앞선 지우기의 체크포인트가 다른 연결에 막혔으면 옛 바이트가 거기 남는다)."""
    path = Path(path)
    if not path.exists():
        return False
    try:
        if db_files(path)[1].stat().st_size > 0:
            return True
    except OSError:
        pass
    conn = open_db(path, config.SQUAD_CMP_UI_TIMEOUT_S)
    try:
        return conn.execute("SELECT EXISTS(SELECT 1 FROM squads) OR EXISTS(SELECT 1 FROM meta)").fetchone()[0] == 1
    finally:
        conn.close()


def purge(path: Path | str, timeout: float = store.OPEN_TIMEOUT_S) -> bool:
    """받은 것 전부(그날 멈춤 표시까지) 지우고 WAL 을 비운다 → 지울 것이 있었나. 로더가 도는 동안 부르지 않는다(저장과 겹친다).
    파일 자체는 남긴다 — 다른 실행본이 열고 있으면 윈도우가 삭제를 막는다(비운 바이트는 secure_delete 가 0 으로)."""
    if not has_data(path):
        return False
    conn = open_existing(path, timeout)
    if conn is None:
        return False
    try:
        with conn:
            conn.execute("DELETE FROM squads")
            conn.execute("DELETE FROM meta")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return True


def prune(path: Path | str, now: datetime, timeout: float = store.OPEN_TIMEOUT_S) -> int:
    """14일 정리 → 바뀐 줄 수. 선발은 `lineup_at`, 줄은 `checked_at`(1만 위 밖으로 나가 더 안 묻는 사람). 시계를 뒤로 돌린 PC 에서
    미래 시각 줄이 영영 남지 않게 하루 넘는 미래도 지운다."""
    conn = open_existing(path, timeout)
    if conn is None:
        return 0
    t = int(now.timestamp())
    lo, hi = t - config.SQUAD_CMP_KEEP_DAYS * DAY_S, t + DAY_S
    try:
        with conn:
            n = conn.execute("DELETE FROM squads WHERE checked_at < ? OR checked_at > ?", (lo, hi)).rowcount
            n += conn.execute("UPDATE squads SET lineup = NULL, match_at = NULL, lineup_at = NULL"
                              " WHERE lineup_at < ? OR lineup_at > ?", (lo, hi)).rowcount
        if n:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return n
    finally:
        conn.close()


def meta_get(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def _meta_set(conn, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _save_ok(conn, sn: int, match_at: int, lineup: bytes, now: int) -> None:
    with conn:
        conn.execute("INSERT INTO squads (profile_sn, match_at, lineup, lineup_at, checked_at, fail) VALUES (?, ?, ?, ?, ?, NULL)"
                     " ON CONFLICT(profile_sn) DO UPDATE SET match_at = excluded.match_at, lineup = excluded.lineup,"
                     " lineup_at = excluded.lineup_at, checked_at = excluded.checked_at, fail = NULL",
                     (sn, match_at, lineup, now, now))


def _save_fail(conn, sn: int, fail: str, now: int) -> None:
    """앞서 받은 선발·경기 시각·받은 때는 그대로 둔다."""
    with conn:
        conn.execute("INSERT INTO squads (profile_sn, checked_at, fail) VALUES (?, ?, ?)"
                     " ON CONFLICT(profile_sn) DO UPDATE SET checked_at = excluded.checked_at, fail = excluded.fail", (sn, now, fail))


# ── 선발 ─────────────────────────────────────────────────────────────────────

class BadFormat(Exception):
    """넥슨 응답의 꼴이 다르다."""


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def starters(side) -> list[tuple[int, int, int]]:
    """한쪽의 선발 (카드, 자리, 강화) — 선수 칸이 있는데 읽을 수 없으면 BadFormat(빈 판과 가른다: 열 이름이 바뀐 것을 "몰수" 로 읽으면
    1만 명을 nogame 으로 적는다). 선수가 없으면 []."""
    players = side.get("player") if isinstance(side, dict) else None
    if not isinstance(players, list):
        raise BadFormat("player")
    out = []
    for p in players:
        if not isinstance(p, dict):
            raise BadFormat("player")
        sp, po, gr = _int(p.get("spId")), _int(p.get("spPosition")), _int(p.get("spGrade"))
        if sp is None or po is None or gr is None:
            raise BadFormat("player")
        if po != SUB_POSITION:
            out.append((sp, po, gr))
    return out


def pack_lineup(cards: Iterable[tuple[int, int, int]]) -> bytes:
    """정렬해 묶는다 — 같은 선발이면 같은 바이트(`tools/dev_matches.lineup_body` 와 같은 꼴 · 테스트가 대조)."""
    sp = sorted(cards)
    try:
        return b"".join(struct.pack("<IBB", *x) for x in sp)
    except struct.error:   # u32·u8 를 넘는 값 — 넓은 꼴(맨 앞 0x01 · 길이가 홀수라 안 겹친다)
        return b"\x01" + b"".join(struct.pack("<qhh", *x) for x in sp)


def unpack_lineup(body: bytes) -> list[tuple[int, int, int]]:
    if body[:1] == b"\x01" and len(body) % 6:
        return list(struct.iter_unpack("<qhh", body[1:]))
    return list(struct.iter_unpack("<IBB", body))


def lineup_of(detail, ouid: str) -> tuple[int, bytes | None]:
    """경기 상세 → (경기 시각 epoch, 내 쪽 선발 · 양쪽 다 11명이 아니면 None). 꼴이 다르면 BadFormat."""
    if not isinstance(detail, dict):
        raise BadFormat("detail")
    at = matchtime.epoch(detail.get("matchDate"))
    infos = detail.get("matchInfo")
    if at is None or not isinstance(infos, list) or not all(isinstance(i, dict) for i in infos):
        raise BadFormat("detail")
    mine = [i for i in infos if i.get("ouid") == ouid]
    if len(mine) != 1:
        raise BadFormat("ouid")
    sides = [starters(i) for i in infos]
    if len(infos) != 2 or any(len(s) != LINEUP_N for s in sides):
        return at, None
    return at, pack_lineup(starters(mine[0]))


# ── 받을 차례 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Target:
    profile_sn: int
    nickname: str
    prev_fail: str | None = None


def targets(rows: Iterable[dict], have: dict[int, tuple[int, str | None]], now: datetime) -> list[Target]:
    """스냅숏 줄(profile_sn·nickname) + 저장된 {번호: (checked_at, fail)} → 받을 차례인 사람들(앞에서부터 · 순수).
    ① 줄이 없는 사람 ② `checked_at` 이 7일 지난 사람(오래된 순) ③ 다시 물을 때가 된 실패(format 은 다음 날 · gone·nogame 은 3일 뒤).
    같은 순서 안은 날짜로 섞은 무작위. 미래 시각(시계를 뒤로 돌린 PC)은 지난 것으로 본다."""
    t = int(now.timestamp())
    rng = random.Random(now.date().toordinal())
    out = []
    for r in rows:
        sn, nick = r["profile_sn"], r.get("nickname")
        if not nick:
            continue
        if sn not in have:
            out.append(((0, 0, rng.random()), Target(sn, nick)))
            continue
        checked, fail = have[sn]
        age = t - checked
        if age < -DAY_S or age >= config.SQUAD_CMP_REFRESH_DAYS * DAY_S:
            out.append(((1, checked, rng.random()), Target(sn, nick, fail)))
        elif fail == FORMAT:
            if datetime.fromtimestamp(checked).date() < now.date():
                out.append(((2, 0, rng.random()), Target(sn, nick, fail)))
        elif fail is not None and age >= config.SQUAD_CMP_FAIL_DAYS * DAY_S:
            out.append(((2, 0, rng.random()), Target(sn, nick, fail)))
    out.sort(key=lambda x: x[0])
    return [x[1] for x in out]


# ── 한 명 받기 ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Got:
    kind: str                     # ok · gone · nogame · format
    match_at: int | None = None
    lineup: bytes | None = None
    empty: bool = False           # nogame 이 빈 목록 때문인가(이어짐을 센다)


def _fetch_one(gate, api, t: Target, oldest: int) -> Got:
    """한 명의 마지막 선발. oldest = 이 시각(epoch)보다 옛 경기는 안 쓴다. 저장하지 않을 실패(5xx·연결·깨진 JSON·표에 없는 코드)는
    ownerdist._Transient, 멈춤은 ownerdist._Halt 로 올라간다."""
    def ask(fn):
        try:
            return gate.call(fn)
        except (ownerdist._Halt, NexonAPIError):
            raise
        except Exception as e:  # noqa: BLE001 — 200 인데 JSON 이 아님 등
            raise ownerdist._Transient(e) from e

    try:
        who = ask(lambda: api.get_raw(EP_ID, nickname=t.nickname))
    except NexonAPIError as e:
        if e.code == GONE_CODE:
            return Got(GONE)
        raise ownerdist._Transient(e) from e
    ouid = who.get("ouid") if isinstance(who, dict) else None
    if not isinstance(ouid, str) or not ouid:
        return Got(FORMAT)
    try:
        ids = ask(lambda: api.get_raw(EP_USER_MATCH, ouid=ouid, matchtype=config.DEFAULT_MATCH_TYPE,
                                      offset=0, limit=config.SQUAD_CMP_MATCHES))
    except NexonAPIError as e:
        raise ownerdist._Transient(e) from e
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        return Got(FORMAT)
    if not ids:
        return Got(NOGAME, empty=True)
    for mid in ids[:config.SQUAD_CMP_MATCHES]:
        try:
            detail = ask(lambda m=mid: api.get_match_detail_nocache(m))
        except NexonAPIError as e:
            if e.code == NO_DATA_CODE:
                continue
            raise ownerdist._Transient(e) from e
        try:
            at, lineup = lineup_of(detail, ouid)
        except BadFormat:
            return Got(FORMAT)
        if at < oldest:
            return Got(NOGAME)   # 저장해도 14일 정리가 바로 비운다 — 되풀이해서 받지 않게
        if lineup is not None:
            return Got("ok", at, lineup)
    return Got(NOGAME)


# ── 한 실행 ──────────────────────────────────────────────────────────────────

@dataclass
class Result:
    due: int = 0              # 받을 차례인 사람
    ok: int = 0
    gone: int = 0
    nogame: int = 0
    format: int = 0
    transient: int = 0        # 저장하지 않은 실패
    requests: int = 0
    stop: str = ""            # "" 끝까지 · off · snapshot · marked · cancel · limit · quota · key · down · error · gone · empty · format · corrupt
    devkey: bool = False      # 429 로 멈췄는데 켠 뒤 누적이 적다 — 개발 키
    seconds: float = 0.0
    error: str = ""

    @property
    def saved(self) -> int:
        return self.ok + self.gone + self.nogame + self.format


class _Book:
    """보류 줄 — gone·format·빈 목록은 바로 저장하지 않고 종류마다 모은다. 정상이나 빈 목록 아닌 nogame 이 오면 저장하고 0 부터,
    20명에 닿으면 전부 버리고 그 종류 이름을 돌려준다(실행을 멈춘다). 지난번에도 같은 실패였던 사람은 세지 않고 바로 저장한다."""

    def __init__(self, save: Callable[[Target, Got], None]):
        self._save = save
        self._held: dict[str, list[tuple[Target, Got]]] = {GONE: [], FORMAT: [], EMPTY: []}

    def add(self, t: Target, got: Got) -> str:
        key = EMPTY if got.kind == NOGAME and got.empty else got.kind
        if key not in self._held:
            self.flush()
            self._save(t, got)
            return ""
        if t.prev_fail == got.kind:
            self._save(t, got)
            return ""
        self._held[key].append((t, got))
        if len(self._held[key]) >= config.SQUAD_CMP_STREAK:
            self.drop()
            return key
        return ""

    def flush(self) -> None:
        for held in self._held.values():
            for t, got in held:
                self._save(t, got)
            held.clear()

    def drop(self) -> None:
        for held in self._held.values():
            held.clear()


class _Quota:
    """하루 계수·켠 뒤 누적 · 최종 429 때 남길 표시. 여러 스레드가 부른다(각자 fifa.db 연결로)."""

    def __init__(self, on_at: str, acc: int, rows: int):
        self.on_at, self.acc, self.rows = on_at, acc, rows
        self.devkey = False

    def take(self, conn, day: str) -> bool:
        n = store.squad_take(conn, day, config.SQUAD_CMP_DAILY_REQ, self.on_at)
        if n is None:
            return False
        self.acc = max(self.acc, n)
        return True

    def seen(self) -> int:
        """켠 뒤 보낸 요청(아래쪽 어림) — 복원으로 누적이 줄었을 수 있어 저장된 사람 수로도 본다(3회차 [중]6)."""
        return max(self.acc, self.rows * 3)

    def on_quota(self, conn, day: str) -> None:
        """개발 키로 보이면 이쪽 표시만(다른 로더까지 그날 멈추지 않게 · .env 를 못 써 켜진 채여도 그날은 다시 안 돈다), 아니면 둘 다."""
        store.budget_mark_429(conn, day, store.BUDGET_SQUAD)
        if self.seen() < config.SQUAD_CMP_SELFCHECK_REQ:
            self.devkey = True
        else:
            store.budget_mark_429(conn, day, store.BUDGET_OPENAPI)


def marked_today(conn_fifa, conn_squads, day: str) -> bool:
    """오늘 다시 하지 않을 표시 — 오픈API 429(서로 본다) · 키 거절 · 응답 꼴."""
    if store.budget_hit_429(conn_fifa, day, (store.BUDGET_OPENAPI, store.BUDGET_SQUAD)):
        return True
    return conn_squads is not None and day in (meta_get(conn_squads, KEY_DAY), meta_get(conn_squads, FORMAT_DAY))


def should_run(fifa_path, squads_path, now: datetime) -> bool:
    """틱에서 로더를 띄울까(화면 스레드 · 짧은 대기) — 그날 표시가 없고 하루 상한에 한 명분이 남았을 때. 받을 사람이 있는지는 로더 안에서 센다."""
    day = now.date().isoformat()
    conn = store.open_db(fifa_path)
    try:
        sq = open_existing(squads_path, config.SQUAD_CMP_UI_TIMEOUT_S)
        try:
            if marked_today(conn, sq, day):
                return False
        finally:
            if sq is not None:
                sq.close()
        return config.SQUAD_CMP_DAILY_REQ - store.budget_used(conn, day, store.BUDGET_SQUAD) >= config.SQUAD_CMP_PERSON_REQ
    finally:
        conn.close()


def _snapshot(rank_open, now: datetime) -> list[dict] | None:
    """마지막 스냅숏의 줄 — 없거나 36시간보다 오래면 None. rank.db 는 읽고 바로 닫는다(지우기를 막지 않게)."""
    import rankerpick   # 순환 import 를 피한다(ownerdist 와 같은 까닭)
    rc = rank_open()
    if rc is None:
        return None
    try:
        taken, rows = rankerpick.top_rankers(rc, limit=config.SQUAD_CMP_TOP)
    finally:
        rc.close()
    try:
        fresh = taken is not None and now - datetime.fromisoformat(taken) <= timedelta(hours=config.SQUAD_CMP_SNAP_MAX_H)
    except (ValueError, TypeError):
        return None
    return rows if fresh else None


def collect(squads_path, fifa_path, rank_open: Callable[[], object], api_factory, *, cancel: Callable[[], bool] = lambda: False,
            tries: dict[int, int] | None = None, sleep=time.sleep, clock=time.monotonic, now_fn=datetime.now) -> Result:
    """받을 차례인 사람을 차례로 받는다. 끊겨도 되는 단위는 한 명(한 명 = 한 트랜잭션). api_factory() → FCOnlineAPI 꼴(스레드마다 하나).
    tries = 저장하지 않은 실패로 그날 물은 횟수 {번호: 횟수} — 부르는 쪽(예약)이 그날 동안 쥔다."""
    t0 = clock()
    res = Result()
    if config.squad_cmp_allowed() is not True:
        res.stop = "off"
        return res
    try:
        _collect(res, squads_path, fifa_path, rank_open, api_factory, cancel, {} if tries is None else tries, sleep, clock, now_fn)
    except sqlite3.DatabaseError as e:
        res.error = type(e).__name__
        res.stop = "corrupt" if is_corrupt(e) and drop_if_corrupt(squads_path) else (res.stop or "error")
    res.seconds = clock() - t0
    return res


def _collect(res: Result, squads_path, fifa_path, rank_open, api_factory, cancel, tries, sleep, clock, now_fn) -> None:
    on_at = config.SQUAD_CMP_ON
    now = now_fn()
    fifa = store.open_db(fifa_path)
    try:
        acc = store.squad_req(fifa, on_at)
        rows = _snapshot(rank_open, now)
        if rows is None:
            res.stop = "snapshot"
            return
        prune(squads_path, now)
        conn = open_db(squads_path)
        try:
            if marked_today(fifa, conn, now.date().isoformat()):
                res.stop = "marked"
                return
            have = {r["profile_sn"]: (r["checked_at"], r["fail"]) for r in conn.execute("SELECT profile_sn, checked_at, fail FROM squads")}
            todo = [t for t in targets(rows, have, now) if tries.get(t.profile_sn, 0) < config.SQUAD_CMP_TRY_MAX]
            res.due = len(todo)
            if todo:
                quota = _Quota(on_at, acc, len(have))
                _run(res, conn, fifa_path, todo, quota, set(have), api_factory, cancel, tries, sleep, clock, now_fn)
        finally:
            conn.close()
    finally:
        fifa.close()


def _run(res: Result, conn, fifa_path, todo, quota: _Quota, known: set[int], api_factory, cancel, tries, sleep, clock, now_fn) -> None:
    gate = ownerdist._Gate(fifa_path, cancel, sleep, clock, lambda: now_fn().date().isoformat(), 10 ** 9, take=quota.take,
                           wait_s=config.SQUAD_CMP_429_WAIT_S, on_quota=quota.on_quota, stop_codes=STOP_CODES)
    local = threading.local()

    def api():
        if not hasattr(local, "api"):
            local.api = api_factory()
        return local.api

    def save(t: Target, got: Got) -> None:
        at = int(now_fn().timestamp())
        if got.kind == "ok":
            _save_ok(conn, t.profile_sn, got.match_at, got.lineup, at)
        else:
            _save_fail(conn, t.profile_sn, got.kind, at)
        setattr(res, got.kind, getattr(res, got.kind) + 1)
        if t.profile_sn not in known:
            known.add(t.profile_sn)
            quota.rows = len(known)

    def halt(reason: str) -> None:
        try:
            gate._halt(reason)
        except ownerdist._Halt:
            pass

    book = _Book(save)
    bad_streak = 0
    queue = iter(todo)
    inflight: dict = {}
    exhausted = False

    def fetch(t: Target) -> Got:
        oldest = int(now_fn().timestamp()) - config.SQUAD_CMP_KEEP_DAYS * DAY_S
        return _fetch_one(gate, api(), t, oldest)

    pool = ThreadPoolExecutor(max_workers=config.SQUAD_CMP_WORKERS)
    try:
        while True:
            room = 1 if quota.acc < config.SQUAD_CMP_SERIAL_REQ else config.SQUAD_CMP_WORKERS
            while not exhausted and not gate.reason and not cancel() and len(inflight) < room:
                t = next(queue, None)
                if t is None:
                    exhausted = True
                else:
                    inflight[pool.submit(fetch, t)] = t
            if not inflight:
                break
            for f in wait(inflight, return_when=FIRST_COMPLETED).done:
                t = inflight.pop(f)
                try:
                    got = f.result()
                except ownerdist._Halt:
                    continue
                except Exception as e:  # noqa: BLE001 — _Transient 와 그 밖의 어떤 예외도 그 사람만(저장하지 않음)
                    res.transient += 1
                    cause = getattr(e, "cause", e)
                    res.error = f"{type(cause).__name__} {getattr(cause, 'code', '')}".strip()   # 메시지에는 남의 닉네임이 URL 로 실린다
                    tries[t.profile_sn] = tries.get(t.profile_sn, 0) + 1
                    bad_streak += 1
                    if bad_streak >= config.SQUAD_CMP_FAIL_STREAK:
                        halt("error")
                    continue
                bad_streak = 0
                if gate.reason in (GONE, EMPTY, FORMAT):
                    continue   # 이어짐으로 멈췄다 — 그 뒤에 끝난 것은 버린다(같은 까닭의 실패일 수 있다)
                why = book.add(t, got)
                if why:
                    halt(why)
        if not gate.reason and cancel():
            halt("cancel")
        if gate.reason in ("", "cancel", "limit"):
            book.flush()
        else:
            book.drop()
    except BaseException:
        halt("error")   # squads.db 에 못 쓴다 — 도는 사람들이 저장할 곳도 없이 남은 요청을 마저 보내지 않게
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        gate.close()
        res.requests = gate.requests
    res.stop = gate.reason
    res.devkey = gate.reason == "quota" and quota.devkey
    day = now_fn().date().isoformat()
    if gate.reason == "key":
        _meta_set(conn, KEY_DAY, day)
    elif gate.reason == FORMAT:
        _meta_set(conn, FORMAT_DAY, day)


# ── [정보] 상태 줄 ───────────────────────────────────────────────────────────

def status_text(path: Path | str, now: datetime) -> str:
    """받은 선발 수 · 그날 멈춘 까닭 · 스스로 끈 까닭. 창을 열 때 한 번(화면 스레드)."""
    parts = []
    day = now.date().isoformat()
    conn = open_existing(path, config.SQUAD_CMP_UI_TIMEOUT_S)
    if conn is not None:
        try:
            n = conn.execute("SELECT COUNT(*) FROM squads WHERE lineup IS NOT NULL").fetchone()[0]
            if n:
                parts.append(f"선발 {n:,}명을 받았습니다")
            if meta_get(conn, KEY_DAY) == day:
                parts.append("넥슨이 API 키를 받지 않아 오늘은 멈췄습니다")
            if meta_get(conn, FORMAT_DAY) == day:
                parts.append("넥슨 응답의 형식이 달라 오늘은 멈췄습니다")
        finally:
            conn.close()
    if not config.SQUAD_CMP_ON and config.SQUAD_CMP_OFF in OFF_REASON_TEXT:
        parts.append(OFF_REASON_TEXT[config.SQUAD_CMP_OFF])
    return ". ".join(parts)
