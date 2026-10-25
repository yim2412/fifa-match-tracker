"""개발 PC 경기 수집(④) — 1만 위 랭커의 감독모드 경기를 요약해 쌓는다. 이 PC 전용 · 배포판 밖 · 서비스 키에만.

근거·결정은 ROADMAP "개발 PC 수집 확장" 2판. 목표 (a) 같은 OVR 대비 · (b) 정확한 시즌 FC · (c) 팀컬러 상성의 **재료만** 쌓는다
(계산은 58·59). 원자료는 `DATA_DIR/dev_matches.db` 에만 — 다른 구단주 경기라 저장소·배포판에 안 들어간다.
`tools/dev_archive.py`(①②③ · FifaDevArchive)와 **따로 돈다** — 거기 넣으면 ran_ok_today 에 엮여 홈페이지 요청(②)이 2시간마다
다시 돌거나 ④ 가 같이 건너뛰어진다(1회차 검토 H2).

| 규칙 | 어디 |
|---|---|
| 서비스 키(`DEV_NEXON_API_KEY`)가 없으면 요청 0 — 앱 키로 넘어가지 않는다(앱 한도를 태우면 앱이 원인 모를 429 를 받는다 · H4) | `run` |
| 같은 날 ③(랭커 픽 dev)이 429 를 받았으면 시작 안 함 — fifa.db 는 읽기 전용 URI 로만, **한 줄도 안 쓴다** | `_pick_dev_hit_429` |
| 하루 상한 `next_cap`: 전날 막힘 0 · 끝냄 · 실제로 돈 시간(`days.run_s`) 6시간 안 → ×1.5(끝 35만) · 막힘 → 절반 + 달력 3일 유지 · 그 밖 그대로 | `next_cap` |
| "제시간"은 시작~끝 벽시계가 아니라 **실행들이 실제로 돈 시간의 합** — 00:00 실행이 PC 꺼짐으로 끊기고 아침에 이어 끝내면 벽시계로 11시간이 돼 10-22~24 사흘 상한이 안 올랐다(실제 7~45분). 사람마다 반영하니 끊긴 실행도 끊기기 전까지는 센다 | `_good_day` · `run_day` |
| 요청마다: 취소·멈춤 → 429 쉼 대기 → 상한 → 요청(재시도 끔). 429 → 전원 60초 쉬고 한 번 더 → 또 429 면 막힘. 403 막힘 · 키 거절·400 → 그날 멈춤 · 5xx·네트워크 20연속 → 그날 멈춤(막힘 아님) | `_Gate.call` |
| 목록 훑기는 **커서 `top_id` 에서만** 멈춘다 — games 에 있는지는 상세를 건너뛸지에만(상대가 먼저 넣은 경기에서 멈추면 그 아래가 빈다 · H3) | `_Run.scan` |
| 커서는 옛 커서(또는 바닥)까지 다 훑은 뒤에만 올린다 — 중간에 끊기면 그대로, 다음 실행이 맨 앞부터 | `_Run.scan` |
| 커서 없는 사람은 실행 날 `DEV_MATCH_FLOOR_DAYS` 전 00:00 KST 보다 옛 경기에서 멈춘다(그 앞은 단 4 — 거슬러 받기) | `_Run.floor_at` |
| 단 순서 1 → 2 → 3 → 4(`days.stage` '1'·'2'·'3'·'done') — 앞 단이 하루 몫을 다 돌아야 다음 단 | `run_day` |
| 단 3 = 이번 시즌 1만 위에 든 적 있는 사람 − 지금 1만 명 · 시즌은 dev_archive 의 **마지막 `new_season=1` 스냅숏부터**(`season_seq` 는 rank.db 를 지우면 1부터) · 받기는 단 2 와 같다 | `plan_outside` |
| 끝까지 훑은 아래 끝은 `cursor.depth`·`base_id` 로 그 사람 목록 안에서 잰다 — `games` 의 MIN(at) 이 아니다(남이 넣은 상대 경기가 섞여 그 사이가 빈다 · 조각 ② H1) | `_Run._new_base` |
| 단 4 는 목록 `depth−1` 쪽에서 base_id 를 찾아 밀린 칸을 잰다(새 경기는 앞에만 쌓인다) · 꽉 찬 쪽에 없으면 그날 건너뜀 · 짧은 쪽에 없으면 오픈API 창 밖 → 끝 + `gap_at` · depth 를 모르면 맨 앞부터 걷는다 · 사람별 하루 상한 `back_day`·`back_req` · top 은 안 건드린다 | `_Run.back` |
| 시즌 시작은 fifa.db 시즌표를 읽기 전용으로 + rank.db 새 시즌 스냅숏 중 늦은 쪽 · 모르면 단 4 안 함(`load_season_ends` 는 쓰기로 열고 웹을 불러 금지) | `season_start_at` |
| 오픈API 가 옛 커서보다 먼저 끝나면 커서를 올리고 `owners.gap_at` 에 빈 날을 적는다 | `_Run.scan` |
| 상세 실패 → `missing`(커서는 넘어간다) → 다음 실행 맨 앞에서 다시 · 3번 실패면 tries 만 남긴다 | `_Run.retry_missing` |
| 같은 경기 id 를 두 사람이 동시에 → 처리 중 집합으로 **상세는 한 번** · 넣기는 INSERT OR IGNORE | `_Db.claim` |
| 쓰기는 **한 사람 = 한 트랜잭션**(경기·선발·커서·missing) · 계수(`days.used`)는 메모리에서 세고 사람마다 반영 | `_Db.write_person` |
| 선발은 한 벌만(`lineups` — 정렬한 (spId, spPosition, spGrade) 11개) · 경기 줄은 번호만(E1) | `lineup_body` |
| 저장하지 않는 것: 경기 상세 원본 · 슛·패스 · 상대 닉네임 · `.cache`(`get_match_detail_nocache`) | `summarize` |
| 두 개가 동시에 못 돈다 — `msvcrt.locking`(프로세스가 죽으면 OS 가 푼다 · M7) | `acquire_lock` |

**시각**: `games.at` 은 경기 상세 `matchDate`(앱 전체가 KST 로 읽는다)를 KST 로 본 epoch 초. "날"은 실행을 **시작한** KST 날짜(L3).

작업 스케줄러 `FifaDevMatches`(로그온 5분 뒤 + 2시간마다 · pythonw · 실행 제한 PT7H · 겹침 IgnoreNew) — 등록은 XML 로
(`schtasks /create /tn FifaDevMatches /xml <파일>`). XML 은 이 PC 경로가 들어가 저장소에 두지 않는다.

    python tools/dev_matches.py                 # 오늘 끝났으면 바로 나간다
    python tools/dev_matches.py --cap 300 --force   # 오늘 상한을 300 으로 · 끝났어도 다시
    python tools/dev_matches.py --status        # 현황
    python tools/dev_matches.py --check-fc      # 내 계정 경기를 앱 fifa.db 와 대조(dev 가 빠뜨린 경기)

로그 `logs/dev_matches.log` · 진행 줄 `~/.claude/bg-progress.d/dev-matches-<PID>.txt` · 테스트 `tests/test_dev_matches.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import sqlite3
import struct
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from nexon_api import KEY_INVALID_CODE, NETWORK_CODE, QUOTA_CODE, NexonAPIError  # noqa: E402

DB_PATH = config.DATA_DIR / "dev_matches.db"
LOCK_PATH = config.DATA_DIR / "dev_matches.lock"
ARCHIVE_PATH = config.DATA_DIR / "dev_archive.db"   # 단 3 대상(읽기만) — dev_archive.ARCHIVE_PATH 와 같은 파일
LOG_PATH = config.DATA_DIR / "logs" / "dev_matches.log"
PROGRESS_DIR = Path.home() / ".claude" / "bg-progress.d"
KST = timezone(timedelta(hours=9))
GONE_CODE = "OPENAPI00004"      # 맞는 키로 없는 닉네임 — get_ouid 에서만 "그 사람 건너뜀", 다른 요청이면 우리 요청이 틀린 것(멈춤)
PICK_DEV_KIND = "ranker_pick_dev"   # dev_archive ③ 의 계수 이름(fifa.db api_budget)
SUB_POSITION = 28               # 교체 명단 — 선발이 아니다(stats.SUB_POSITION 과 같은 값)
RESULT_CODE = {"승": 1, "무": 0, "패": -1}
PROGRESS_EVERY_S = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    id INTEGER PRIMARY KEY,
    ouid TEXT UNIQUE NOT NULL,
    profile_sn INTEGER,             -- 대상(1만 위)으로 찾은 사람만 — 상대로만 본 사람은 NULL
    nickname TEXT,                  -- ouid 를 다시 찾을 때만(상대 닉네임은 안 적는다)
    ouid_at TEXT,
    gap_at INTEGER                  -- 오픈API 가 옛 커서보다 먼저 끝남: 이 시각(epoch) 앞이 빈다
);
CREATE INDEX IF NOT EXISTS owners_sn ON owners(profile_sn);
CREATE TABLE IF NOT EXISTS lineups (
    id INTEGER PRIMARY KEY,
    h INTEGER UNIQUE NOT NULL,
    body BLOB NOT NULL              -- 정렬한 (spId u32, spPosition u8, spGrade u8) × 선발 수 — 넓은 꼴은 맨 앞 0x01
);
CREATE TABLE IF NOT EXISTS games (
    match_id TEXT PRIMARY KEY,
    at INTEGER NOT NULL,            -- matchDate 를 KST 로 본 epoch 초
    end_type INTEGER,
    o1 INTEGER NOT NULL, d1 INTEGER, r1 INTEGER, g1 INTEGER, l1 INTEGER,
    o2 INTEGER, d2 INTEGER, r2 INTEGER, g2 INTEGER, l2 INTEGER
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS games_o1 ON games(o1, at);
CREATE INDEX IF NOT EXISTS games_o2 ON games(o2, at);
CREATE TABLE IF NOT EXISTS cursor (
    owner INTEGER PRIMARY KEY,
    top_id TEXT,                    -- 끝까지 훑었을 때의 맨 앞 경기(NULL = 그때 경기가 없었다 — top_at 이 바닥)
    top_at INTEGER,
    back_done INTEGER NOT NULL DEFAULT 0,  -- 거슬러 받기 끝(시즌 시작 · 오픈API 끝)
    depth INTEGER,                  -- top_id 를 0 번으로 셀 때 base_id 까지의 목록 길이(끝 포함) · NULL = 모름(→ 단 4 가 맨 앞부터 걸어 다시 잰다)
    base_id TEXT,                   -- 끝까지 훑은 아래 끝 경기(단 4 는 여기서 옛날로) — games 의 MIN(at) 이 아니다: 남이 넣은 상대 경기가 섞인다
    base_at INTEGER,
    back_day TEXT,                  -- 단 4 를 마지막으로 돈 날 · back_req = 그날 쓴 요청(같은 날 다시 돌아도 사람별 상한이 다시 안 찬다)
    back_req INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS missing (
    match_id TEXT PRIMARY KEY,
    owner INTEGER,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT
);
CREATE TABLE IF NOT EXISTS days (
    day TEXT PRIMARY KEY,
    cap INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    key_rejected INTEGER NOT NULL DEFAULT 0,
    stop TEXT,                      -- 그날 멈춘 사유(outage · key · blocked) — 다음 트리거가 그날 다시 안 돈다
    started_at TEXT NOT NULL,
    finished_at TEXT,               -- 할 일을 다 끝냈거나 상한을 다 썼다(제시간 판정 · 그날 다시 안 돈다)
    stage TEXT,                     -- 끝낸 단: '1' · '2' · '3' · 'done'(단 4 까지)
    run_s REAL                      -- 그날 실행들이 실제로 돈 초의 합(제시간 판정) · NULL = 이 열 전의 줄(벽시계로 본다)
);
"""


# ── 요약 ─────────────────────────────────────────────────────────────────────

@dataclass
class Side:
    ouid: str
    division: int | None
    result: int | None
    goals: int | None
    lineup: bytes | None


@dataclass
class GameRow:
    match_id: str
    at: int
    end_type: int | None
    sides: list[Side]


def kst_epoch(s: str) -> int | None:
    try:
        return int(datetime.fromisoformat(s).replace(tzinfo=KST).timestamp())
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def lineup_body(players) -> bytes | None:
    """선발(교체 명단 뺌)을 정렬해 묶는다 — 같은 선발이면 같은 바이트. 선발이 없으면 None."""
    sp = sorted((p["spId"], p["spPosition"], _int(p.get("spGrade")) or 0) for p in players or []
                if isinstance(p, dict) and _int(p.get("spId")) is not None and _int(p.get("spPosition")) is not None
                and p["spPosition"] != SUB_POSITION)
    if not sp:
        return None
    try:
        return b"".join(struct.pack("<IBB", *x) for x in sp)
    except struct.error:   # u32·u8 를 넘는 값이 오면 넓은 꼴(맨 앞 0x01 — 길이가 홀수라 좁은 꼴(6의 배수)과 안 겹친다)
        return b"\x01" + b"".join(struct.pack("<qhh", *x) for x in sp)


def summarize(detail) -> GameRow | None:
    """경기 상세 → 한 경기 한 줄(양쪽). 깨졌으면 None — 그 경기만."""
    try:
        mid = detail.get("matchId")
        at = kst_epoch(detail.get("matchDate") or "")
        infos = [i for i in detail.get("matchInfo") or [] if isinstance(i, dict) and i.get("ouid")]
        if not mid or at is None or not infos:
            return None
        sides = []
        for i in infos[:2]:
            md = i.get("matchDetail") or {}
            sides.append(Side(i["ouid"], _int(i.get("division")), RESULT_CODE.get(md.get("matchResult")),
                              _int((i.get("shoot") or {}).get("goalTotal")), lineup_body(i.get("player"))))
        end = _int((infos[0].get("matchDetail") or {}).get("matchEndType"))
        return GameRow(mid, at, end, sides)
    except Exception:  # noqa: BLE001 — dict 가 아닌 값 등 어떤 깨짐도 그 경기만
        return None


# ── 하루 상한 ─────────────────────────────────────────────────────────────────

def _good_day(r: dict) -> bool:
    if r["blocked"] or r["key_rejected"] or not r.get("finished_at"):
        return False
    if r.get("run_s") is not None:
        return r["run_s"] <= config.DEV_MATCH_DAY_HOURS * 3600
    took = datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])
    return took <= timedelta(hours=config.DEV_MATCH_DAY_HOURS)


def next_cap(days_rows: list[dict], today: str) -> int:
    """오늘 상한(순수 함수 — days 줄들과 오늘 날짜만 본다). 오늘 줄은 무시한다."""
    rows = sorted((r for r in days_rows if r["day"] < today), key=lambda r: r["day"])
    if not rows:
        return config.DEV_MATCH_CAP_START
    t = date.fromisoformat(today)
    for r in reversed(rows):   # 가장 최근 막힘이 유지 기간 안이면 그 날의 절반(달력 날 — 꺼진 날도 센다)
        if r["blocked"]:
            if (t - date.fromisoformat(r["day"])).days <= config.DEV_MATCH_BLOCK_HOLD_DAYS:
                return max(1, r["cap"] // 2)
            break
    last = rows[-1]
    if last["day"] == (t - timedelta(days=1)).isoformat() and _good_day(last):
        return min(config.DEV_MATCH_CAP_MAX, math.ceil(last["cap"] * config.DEV_MATCH_CAP_GROWTH))
    return min(config.DEV_MATCH_CAP_MAX, last["cap"])


# ── 잠금 ─────────────────────────────────────────────────────────────────────

def acquire_lock(path: Path | str | None = None):
    """다른 실행이 돌고 있으면 None. 잡으면 열린 파일(쥐고 있는 동안 잠김 · 프로세스가 죽으면 OS 가 푼다)."""
    p = Path(path) if path else LOCK_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def release_lock(f) -> None:
    if f is not None:
        try:
            f.close()   # 닫으면 잠금도 풀린다
        except OSError:
            pass


# ── 요청 게이트 ───────────────────────────────────────────────────────────────

class _Halt(Exception):
    """이번 실행을 끝낸다(사유는 Gate.reason)."""


class _Fail(Exception):
    """그 요청만 실패 — 상세면 missing 으로, 목록이면 그 사람을 이번에 버린다."""


class _Gone(Exception):
    """닉네임이 없다(OPENAPI00004) — 그 사람 건너뜀."""


class _Gate:
    """요청 하나마다: 멈춤·취소 → 429 쉼 대기 → 상한 → 요청(재시도 없음). 여러 스레드가 같이 부른다."""

    def __init__(self, cap: int, used: int, cancel, sleep, clock):
        self.cap, self.used = cap, used
        self.cancel, self.sleep, self.clock = cancel, sleep, clock
        self.requests = 0
        self.reason = ""          # "" | cap | blocked | key | outage | cancel | deadline
        self._pause_until = 0.0
        self._streak = 0
        self._lock = threading.Lock()

    def halt(self, reason: str):
        with self._lock:
            self.reason = self.reason or reason
        raise _Halt

    def take(self) -> None:
        while True:
            if self.reason:
                raise _Halt
            if self.cancel():
                self.halt("cancel")
            w = self._pause_until - self.clock()
            if w <= 0:
                break
            self.sleep(min(1.0, w))
        with self._lock:
            if self.used >= self.cap:
                self.reason = self.reason or "cap"
                raise _Halt
            self.used += 1
            self.requests += 1

    def call(self, fn, gone_ok: bool = False):
        for attempt in (0, 1):
            self.take()
            try:
                out = fn()
            except NexonAPIError as e:
                if e.code == QUOTA_CODE or e.status == 429:
                    if attempt == 1:
                        self.halt("blocked")
                    with self._lock:
                        now = self.clock()
                        if self._pause_until <= now:   # 여러 스레드가 같은 순간 429 를 받아도 쉼은 한 번
                            self._pause_until = now + config.DEV_MATCH_429_WAIT_S
                    continue
                if e.status == 403:
                    self.halt("blocked")
                if gone_ok and (e.code == GONE_CODE or (e.status is None and e.code != NETWORK_CODE)):
                    raise _Gone from e   # 없는 닉네임 · 응답에 ouid 가 없음
                if e.code == KEY_INVALID_CODE or e.status == 400:
                    self.halt("key")
                if e.code == NETWORK_CODE or (e.status is not None and e.status >= 500):
                    with self._lock:
                        self._streak += 1
                        out_ = self._streak >= config.DEV_MATCH_FAIL_STREAK
                    if out_:
                        self.halt("outage")
                raise _Fail(f"{e.code or e.status}") from e
            except _Halt:
                raise
            except Exception as e:  # noqa: BLE001 — 깨진 JSON 등
                raise _Fail(type(e).__name__) from e
            with self._lock:
                self._streak = 0
            return out
        raise AssertionError("unreachable")


# ── DB ───────────────────────────────────────────────────────────────────────

def open_db(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for table, col, decl in _ADDED_COLUMNS:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    return conn


_ADDED_COLUMNS = (   # 조각 ① 의 dm.db 가 이 PC 에 있다 — 새 열은 여기서 붙인다(옛 커서는 depth NULL)
    ("days", "run_s", "REAL"),
    ("cursor", "depth", "INTEGER"), ("cursor", "base_id", "TEXT"), ("cursor", "base_at", "INTEGER"),
    ("cursor", "back_day", "TEXT"), ("cursor", "back_req", "INTEGER NOT NULL DEFAULT 0"),
)


def lineup_hash(body: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(body, digest_size=8).digest(), "big", signed=True)


class _Db:
    """연결 하나를 잠금으로 나눠 쓴다 — 쓰기는 사람 단위라 짧고, 요청(200ms)이 대부분의 시간이다."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.RLock()
        self._inflight: set[str] = set()

    def known_at(self, mid: str) -> int | None:
        with self.lock:
            r = self.conn.execute("SELECT at FROM games WHERE match_id = ?", (mid,)).fetchone()
        return r[0] if r else None

    def claim(self, mid: str, retry: bool = False) -> tuple[str, int | None]:
        """('known', at) · ('busy', None) — 다른 사람이 받는 중이거나 missing 에 있음 · ('mine', None) — 내가 받는다(끝나면 release).
        missing 의 경기는 다시 받기(retry=True)만 받는다 — 단 1·2 가 또 받으면 하루에 여러 번 실패해 tries 상한이 의미가 없다."""
        with self.lock:
            at = self.known_at(mid)
            if at is not None:
                return "known", at
            if mid in self._inflight:
                return "busy", None
            if not retry and self.conn.execute("SELECT 1 FROM missing WHERE match_id = ?", (mid,)).fetchone():
                return "busy", None
            self._inflight.add(mid)
            return "mine", None

    def release(self, mids) -> None:
        with self.lock:
            self._inflight.difference_update(mids)

    def owner_of_target(self, profile_sn: int) -> tuple[int, str] | None:
        with self.lock:
            r = self.conn.execute("SELECT id, ouid FROM owners WHERE profile_sn = ? ORDER BY ouid_at DESC LIMIT 1",
                                  (profile_sn,)).fetchone()
        return (r[0], r[1]) if r else None

    def set_target_ouid(self, profile_sn: int, nickname: str, ouid: str, now: str) -> int:
        """닉네임으로 찾은 ouid — 이미 다른 줄(상대로 본 사람 · 옛 번호)에 있으면 최신이 이긴다(L4)."""
        with self.lock, self.conn:
            self.conn.execute("UPDATE owners SET profile_sn = NULL WHERE profile_sn = ? AND ouid != ?", (profile_sn, ouid))
            self.conn.execute("INSERT INTO owners (ouid, profile_sn, nickname, ouid_at) VALUES (?, ?, ?, ?)"
                              " ON CONFLICT(ouid) DO UPDATE SET profile_sn = excluded.profile_sn,"
                              " nickname = excluded.nickname, ouid_at = excluded.ouid_at",
                              (ouid, profile_sn, nickname, now))
            return self.conn.execute("SELECT id FROM owners WHERE ouid = ?", (ouid,)).fetchone()[0]

    def cursor_of(self, owner: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM cursor WHERE owner = ?", (owner,)).fetchone()
        return dict(r) if r else None

    def _owner_id(self, ouid: str) -> int:
        self.conn.execute("INSERT OR IGNORE INTO owners (ouid) VALUES (?)", (ouid,))
        return self.conn.execute("SELECT id FROM owners WHERE ouid = ?", (ouid,)).fetchone()[0]

    def _lineup_id(self, body: bytes | None) -> int | None:
        if body is None:
            return None
        h = lineup_hash(body)
        self.conn.execute("INSERT OR IGNORE INTO lineups (h, body) VALUES (?, ?)", (h, body))
        return self.conn.execute("SELECT id FROM lineups WHERE h = ?", (h,)).fetchone()[0]

    def _insert_game(self, g: GameRow) -> None:
        vals = []
        for s in (g.sides + [None])[:2]:
            vals += ([None] * 5 if s is None
                     else [self._owner_id(s.ouid), s.division, s.result, s.goals, self._lineup_id(s.lineup)])
        self.conn.execute("INSERT OR IGNORE INTO games (match_id, at, end_type, o1, d1, r1, g1, l1, o2, d2, r2, g2, l2)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (g.match_id, g.at, g.end_type, *vals))

    def write_person(self, *, games: list[GameRow], missing: list[tuple[str, int | None, int]], done_missing=(),
                     cursor: tuple[int, str | None, int | None] | None = None, gap: tuple[int, int] | None = None,
                     base: tuple | None = None, back: tuple[int, str, int] | None = None, now: str = "") -> None:
        """한 사람(또는 missing 한 묶음)의 결과를 한 트랜잭션으로. missing = [(match_id, owner, 더할 tries)].
        base = (owner, depth, base_id, base_at, back_done) · back = (owner, 날, 그날 쓴 요청) — 둘 다 top_id·top_at 은 안 건드린다."""
        with self.lock, self.conn:
            for g in games:
                self._insert_game(g)
            for mid, owner, add in missing:
                self.conn.execute("INSERT INTO missing (match_id, owner, tries, last_at) VALUES (?, ?, ?, ?)"
                                  " ON CONFLICT(match_id) DO UPDATE SET tries = tries + excluded.tries,"
                                  " last_at = excluded.last_at", (mid, owner, add, now))
            self.conn.executemany("DELETE FROM missing WHERE match_id = ?", [(m,) for m in done_missing])
            if cursor is not None:
                self.conn.execute("INSERT INTO cursor (owner, top_id, top_at) VALUES (?, ?, ?)"
                                  " ON CONFLICT(owner) DO UPDATE SET top_id = excluded.top_id, top_at = excluded.top_at",
                                  cursor)
            if base is not None:
                self.conn.execute("UPDATE cursor SET depth = ?, base_id = ?, base_at = ?, back_done = ? WHERE owner = ?",
                                  (*base[1:], base[0]))
            if back is not None:
                self.conn.execute("UPDATE cursor SET back_day = ?, back_req = ? WHERE owner = ?", (*back[1:], back[0]))
            if gap is not None:
                self.conn.execute("UPDATE owners SET gap_at = MAX(COALESCE(gap_at, 0), ?) WHERE id = ?", (gap[1], gap[0]))

    def set_used(self, day: str, used: int, run_s: float = 0.0) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE days SET used = ?, run_s = COALESCE(run_s, 0) + ? WHERE day = ?",
                              (used, run_s, day))


# ── 한 번 실행 ────────────────────────────────────────────────────────────────

@dataclass
class Target:
    rank: int
    profile_sn: int
    nickname: str


@dataclass
class DayResult:
    day: str = ""
    used: int = 0
    cap: int = 0
    requests: int = 0             # 이번 실행에서 보낸 요청
    reason: str = ""              # 멈춘 사유(빈 문자열 = 할 일 끝)
    blocked: bool = False
    key_rejected: bool = False
    finished: bool = False
    stage_done: str | None = None
    skipped: str = ""             # 시작도 안 한 사유
    persons: int = 0
    new_games: int = 0
    errors: int = 0
    notes: list[str] = field(default_factory=list)


def plan_targets(rank_conn) -> list[Target]:
    """rank.db 마지막 스냅숏의 1~DEV_MATCH_TOP 위(순위 순 — 상한이 모자란 날 위에서부터 · E3)."""
    import rankerpick
    _taken, rows = rankerpick.top_rankers(rank_conn, limit=config.DEV_MATCH_TOP)
    return [Target(r["rank"], r["profile_sn"], r["nickname"]) for r in rows if r.get("nickname")]


def _pick_dev_hit_429(fifa_db: Path | str | None, day: str) -> bool:
    """③ 이 오늘 429 를 받았나 — fifa.db 를 읽기 전용 URI 로(만들지도 쓰지도 않는다 · L5)."""
    p = Path(fifa_db) if fifa_db else config.DB_PATH
    if not p.exists():
        return False
    try:
        c = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            return c.execute("SELECT 1 FROM api_budget WHERE day = ? AND kind = ? AND hit_429 = 1",
                             (day, PICK_DEV_KIND)).fetchone() is not None
        finally:
            c.close()
    except sqlite3.Error:
        return False


def _ro(p: Path) -> sqlite3.Connection:
    """남의 DB 를 읽기 전용 URI 로 — 만들지도 쓰지도 않는다(L5)."""
    c = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def plan_outside(archive_db: Path | str | None, exclude: set[int]) -> tuple[list[Target], str]:
    """단 3 — 이번 시즌 1만 위 안에 든 적 있는 사람 중 지금 밖(exclude 밖). 순서는 가장 높았던 순위.
    시즌은 season_seq 가 아니라 **마지막 new_season 스냅숏부터** — rank.db 를 지우면 seq 가 1부터 다시 세어
    보관에 옛 2 와 새 1 이 섞인다(조각 ② M2). 보관이 시작한 10-05 앞에만 든 사람은 없다(E5)."""
    p = Path(archive_db) if archive_db else ARCHIVE_PATH
    if not p.exists():
        return [], "단 3: dev_archive.db 없음 — 건너뜀"
    try:
        c = _ro(p)
        try:
            since = c.execute("SELECT MAX(taken_at) FROM snap WHERE new_season = 1").fetchone()[0] or ""
            seen: dict[int, list] = {}
            for r in c.execute("SELECT r.profile_sn, r.rank, r.nickname FROM snap_rows r JOIN snap s ON s.id = r.snap_id"
                               " WHERE s.taken_at >= ? ORDER BY s.taken_at", (since,)):
                v = seen.setdefault(r[0], [r[1], r[2]])
                v[0], v[1] = min(v[0], r[1]), r[2]   # 가장 높았던 순위 · 닉네임은 마지막 스냅숏 것
        finally:
            c.close()
    except sqlite3.Error as e:
        return [], f"단 3: dev_archive.db 를 못 읽음({e}) — 건너뜀"
    out = [Target(best, sn, nick) for sn, (best, nick) in seen.items() if sn not in exclude and nick]
    return sorted(out, key=lambda t: t.rank), ""


def season_start_at(fifa_db: Path | str | None, rank_conn) -> int | None:
    """지금 시즌 시작(epoch · KST 00:00) — 단 4 가 여기서 멈춘다. fifa.db 시즌표 캐시를 읽기 전용으로만
    (rankcollect.load_season_ends 는 store.open_db 로 열고 웹에서 다시 받는다 — 둘 다 이 수집기에 금지 · M1).
    시즌표가 늦어 새 시즌을 모르면 rank.db 가 새 시즌으로 본 첫 스냅숏이 더 늦다 — 늦은 쪽. 둘 다 모르면 None."""
    best = None
    p = Path(fifa_db) if fifa_db else config.DB_PATH
    if p.exists():
        try:
            c = _ro(p)
            try:
                end = c.execute("SELECT MAX(end_date) FROM seasons").fetchone()[0]
            finally:
                c.close()
            if end:
                best = int(datetime.combine(date.fromisoformat(end), datetime.min.time(), KST).timestamp())
        except (sqlite3.Error, ValueError):
            pass
    try:
        r = rank_conn.execute("SELECT taken_at, new_season FROM snapshots WHERE season_seq ="
                              " (SELECT season_seq FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1)"
                              " ORDER BY taken_at, id LIMIT 1").fetchone()
        if r and r[1]:
            t = kst_epoch(r[0])
            if t is not None:
                best = t if best is None else max(best, t)
    except sqlite3.Error:
        pass
    return best


def back_order(conn: sqlite3.Connection, targets: list[Target]) -> list[Target]:
    """단 4 순서 — depth 를 모르는 사람(빈칸이 있을 수 있다) 먼저, 그다음 아래 끝이 최근인 사람(가까운 빈칸 · E4), 같으면 순위."""
    state = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT o.profile_sn, c.depth, c.base_at FROM cursor c JOIN owners o ON o.id = c.owner"
        " WHERE o.profile_sn IS NOT NULL AND c.back_done = 0")}
    pick = [t for t in targets if t.profile_sn in state]
    return sorted(pick, key=lambda t: (state[t.profile_sn][0] is not None, -(state[t.profile_sn][1] or 0), t.rank))


class _Run:
    def __init__(self, db: _Db, gate: _Gate, api_for_thread, now_fn, floor_at: int):
        self.db, self.gate, self.api, self.now_fn = db, gate, api_for_thread, now_fn
        self.floor_at = floor_at
        self.day = now_fn().date().isoformat()
        self.season_at: int | None = None             # 지금 시즌 시작(epoch) — 단 4 가 여기서 멈춘다 · None 이면 단 4 안 함
        self.first_page: dict[int, list[str]] = {}   # 단 1 이 받은 첫 쪽 — 단 2 가 다시 쓴다(L2 · 같은 실행 안에서만)
        self.gone: set[int] = set()                   # 이번 실행에서 닉네임이 없던 사람 — 단 2 가 다시 묻지 않게
        self._count_lock = threading.Lock()
        self.new_games = 0
        self.errors = 0

    def _now(self) -> str:
        return self.now_fn().isoformat(timespec="seconds")

    def _bump(self, games: int = 0, errors: int = 0) -> None:
        with self._count_lock:
            self.new_games += games
            self.errors += errors

    def _owner(self, t: Target) -> tuple[int, str] | None:
        have = self.db.owner_of_target(t.profile_sn)
        if have is not None:
            return have
        if t.profile_sn in self.gone:
            return None
        try:
            ouid = self.gate.call(lambda: self.api().get_ouid(t.nickname, attempts=1), gone_ok=True)
        except _Gone:
            self.gone.add(t.profile_sn)
            return None
        return self.db.set_target_ouid(t.profile_sn, t.nickname, ouid, self._now()), ouid

    def _list(self, ouid: str, offset: int) -> list[str]:
        return self.gate.call(lambda: self.api().get_match_ids(
            ouid, matchtype=config.DEFAULT_MATCH_TYPE, offset=offset, limit=config.DEV_MATCH_PAGE, attempts=1))

    def _detail(self, mid: str) -> GameRow | None:
        """None 이면 실패(→ missing). _Halt 는 그대로 올라간다."""
        try:
            d = self.gate.call(lambda: self.api().get_match_detail_nocache(mid))
        except _Fail:
            return None
        return summarize(d)

    def latest(self, t: Target) -> None:
        """단 1 — 받는 순간의 최신 경기 한 판(커서를 안 건드린다)."""
        own = self._owner(t)
        if own is None:
            return
        try:
            page = self._list(own[1], 0)
        except _Fail:
            return
        self.first_page[t.profile_sn] = page
        if not page:
            return
        mid = page[0]
        state, _ = self.db.claim(mid)
        if state != "mine":
            return
        try:
            g = self._detail(mid)
        except _Halt:
            self.db.write_person(games=[], missing=[(mid, own[0], 0)], now=self._now())
            self.db.release([mid])
            raise
        if g is None:
            self.db.write_person(games=[], missing=[(mid, own[0], 1)], now=self._now())
        else:
            self.db.write_person(games=[g], missing=[], now=self._now())
            self._bump(games=1)
        self.db.release([mid])

    def scan(self, t: Target) -> None:
        """단 2 — 지난번 커서 뒤의 새 경기 전부(커서 규칙은 머리말 표)."""
        own = self._owner(t)
        if own is None:
            return
        oid, ouid = own
        cur = self.db.cursor_of(oid)
        top_id = cur["top_id"] if cur else None
        stop_at = (cur["top_at"] if cur and cur["top_at"] is not None else None) if cur else self.floor_at
        games: list[GameRow] = []
        missing: list[tuple[str, int, int]] = []
        mine: list[str] = []
        newest: tuple[str, int | None] | None = None
        oldest_seen: int | None = None
        complete = gap = False
        how, stop_g, last = "", 0, None   # 어떻게 끝났나(top · old · end) · 그 자리 · 마지막으로 본 (id, at)
        offset = 0
        try:
            while True:
                page = self.first_page.pop(t.profile_sn, None) if offset == 0 else None
                if page is None:
                    try:
                        page = self._list(ouid, offset)
                    except _Fail:
                        self._bump(errors=1)
                        break   # 목록이 실패하면 커서를 안 올린다 — 다음 실행이 맨 앞부터
                for i, mid in enumerate(page):
                    if top_id is not None and mid == top_id:
                        complete, how, stop_g = True, "top", offset + i
                        break
                    state, at = self.db.claim(mid)
                    if state == "mine":
                        mine.append(mid)
                        g = self._detail(mid)
                        if g is None:
                            missing.append((mid, oid, 1))
                        else:
                            games.append(g)
                            at = g.at
                    if offset == 0 and i == 0:
                        newest = (mid, at)
                    last = (mid, at)
                    if at is not None:
                        oldest_seen = at if oldest_seen is None else min(oldest_seen, at)
                        if stop_at is not None and at < stop_at:
                            complete, how, stop_g = True, "old", offset + i   # 커서·바닥보다 옛 경기 — 커서 id 가 목록에서 사라졌어도 여기서 멈춘다
                            break
                if complete:
                    break
                if len(page) < config.DEV_MATCH_PAGE:
                    complete, how, stop_g = True, "end", offset + len(page)
                    gap = top_id is not None   # 옛 커서를 못 만나고 끝났다 — 그 사이가 빈다
                    break
                offset += len(page)
        except _Halt:
            missing += [(m, oid, 0) for m in mine if m not in {g.match_id for g in games} and
                        m not in {x[0] for x in missing}]
            self.db.write_person(games=games, missing=missing, now=self._now())   # 받은 것은 남기고 커서는 그대로
            self.db.release(mine)
            self._bump(games=len(games))
            raise
        new_cur = base = None
        if complete:
            if newest is not None:
                new_cur = (oid, newest[0], newest[1])
            elif cur is None or top_id is None:
                new_cur = (oid, None, int(self.now_fn().timestamp()))   # 경기가 없었다 — 다음엔 지금부터
            base = self._new_base(oid, cur, how, stop_g, last)
        self.db.write_person(games=games, missing=missing, cursor=new_cur, base=base,
                             gap=(oid, oldest_seen) if gap and oldest_seen is not None else None, now=self._now())
        self.db.release(mine)
        self._bump(games=len(games))

    @staticmethod
    def _new_base(oid: int, cur: dict | None, how: str, stop_g: int, last) -> tuple | None:
        """단 2·3 이 끝까지 훑은 뒤의 아래 끝 — depth 는 새 top(목록 0 번)에서 base 까지 길이. None = 안 건드림."""
        if last is None and how == "end":
            return oid, 0, None, None, 1                       # 목록이 비었다 — 거슬러 받을 것이 없다
        if how == "top":
            if cur is None or cur.get("depth") is None:
                return None                                    # 모르는 채로 둔다 — 단 4 가 걸어서 잰다
            return oid, cur["depth"] + stop_g, cur["base_id"], cur["base_at"], cur["back_done"]
        if how == "old" and cur is None:
            return oid, stop_g + 1, last[0], last[1], 0         # 첫 훑기 — 바닥 밖 첫 경기까지
        if how == "end":
            return oid, stop_g, last[0], last[1], 1             # 오픈API 끝까지 다 봤다
        keep = cur or {}
        return oid, None, keep.get("base_id"), keep.get("base_at"), keep.get("back_done", 0)   # 옛 커서가 목록에서 사라짐

    def back(self, t: Target) -> None:
        """단 4 — 아래 끝(base_id)에서 옛날로. 목록이 밀려도 base_id 를 그 쪽에서 찾아 자리를 맞춘다(시각이 없어서).
        depth 를 모르면 맨 앞부터 걸어 top_id 자리로 다시 잰다(받은 경기는 games 로 건너뛰어 목록 요청만 든다)."""
        own = self.db.owner_of_target(t.profile_sn)
        if own is None or self.season_at is None:
            return
        oid, ouid = own
        cur = self.db.cursor_of(oid)
        if cur is None or cur["back_done"]:
            return
        spent = cur["back_req"] if cur["back_day"] == self.day else 0
        budget = config.DEV_MATCH_BACK_PER_PERSON - spent
        if budget <= 0:
            return
        depth, walk = cur["depth"], cur["depth"] is None
        anchor = walk or depth == 0                # depth 0 = 아는 경기가 없다 — 맨 앞부터
        offset = 0 if anchor else depth - 1
        shift = 0                                  # 목록 자리 − depth 자리(그 사이 맨 앞에 쌓인 새 경기 수)
        top_g = None if cur["top_id"] else 0
        games: list[GameRow] = []
        missing: list[tuple[str, int, int]] = []
        mine: list[str] = []
        done_at = None                             # 마지막으로 끝낸 (목록 자리, id, at)
        done = end = False
        used = 0
        try:
            while used < budget:
                try:
                    used += 1
                    page = self._list(ouid, offset)
                except _Fail:
                    self._bump(errors=1)
                    break
                start = 0
                if not anchor:
                    if cur["base_id"] not in page:
                        # 새 경기는 맨 앞에만 쌓이고 옛 경기는 뒤에서 빠질 뿐이라 base 는 이 쪽(밀린 0~99칸) 안에 있다.
                        # 쪽이 짧은데 없으면 base 가 오픈API 창 밖으로 밀려났다 — 그 앞은 이제 못 받는다
                        if len(page) < config.DEV_MATCH_PAGE:
                            done = end = True
                        else:
                            self._bump(errors=1)   # 꽉 찬 쪽에 없다(100판 넘게 밀림 · 이상) — 오늘은 건너뛴다
                        break
                    i = page.index(cur["base_id"])
                    shift, anchor, start = offset + i - (depth - 1), True, i + 1
                stop = False
                for i in range(start, len(page)):
                    mid, g = page[i], offset + i
                    if walk and top_g is None and mid == cur["top_id"]:
                        top_g = g
                    state, at = self.db.claim(mid)
                    if state == "mine":
                        if used >= budget:
                            self.db.release([mid])
                            stop = True
                            break
                        mine.append(mid)
                        used += 1
                        gr = self._detail(mid)
                        if gr is None:
                            missing.append((mid, oid, 1))
                        else:
                            games.append(gr)
                            at = gr.at
                    done_at = (g, mid, at)
                    if at is not None and at < self.season_at:
                        done = stop = True
                        break
                if stop:
                    break
                if len(page) < config.DEV_MATCH_PAGE:
                    done = end = True
                    break
                offset += len(page)
        except _Halt:
            missing += [(m, oid, 0) for m in mine if m not in {g.match_id for g in games} and
                        m not in {x[0] for x in missing}]
            self._save_back(oid, cur, games, missing, mine, done_at, shift, top_g, walk, False, False, spent + used)
            raise
        self._save_back(oid, cur, games, missing, mine, done_at, shift, top_g, walk, done, end, spent + used)

    def _save_back(self, oid, cur, games, missing, mine, done_at, shift, top_g, walk, done, end, spent) -> None:
        base = gap = None
        if done_at is not None:
            g, mid, at = done_at
            if walk:
                if top_g is not None and g >= top_g:   # top 보다 아래까지 왔을 때만 — 그 위는 단 2 몫
                    base = (oid, g - top_g + 1, mid, at, int(done))
            else:
                base = (oid, g - shift + 1, mid, at, int(done))
            if end and at is not None and at >= self.season_at:
                gap = (oid, at)                    # 시즌 시작 전에 오픈API 가 끝났다 — 그 앞이 빈다
        elif done:
            base = (oid, cur["depth"], cur["base_id"], cur["base_at"], 1)
            if end and cur["base_at"] is not None and cur["base_at"] >= self.season_at:
                gap = (oid, cur["base_at"])
        self.db.write_person(games=games, missing=missing, base=base, gap=gap,
                             back=(oid, self.day, spent), now=self._now())
        self.db.release(mine)
        self._bump(games=len(games))

    def retry_missing(self) -> None:
        with self.db.lock:
            rows = self.db.conn.execute("SELECT match_id, owner FROM missing WHERE tries < ?",
                                        (config.DEV_MATCH_MISSING_TRIES,)).fetchall()
        for mid, owner in rows:
            state, _ = self.db.claim(mid, retry=True)
            if state == "known":
                self.db.write_person(games=[], missing=[], done_missing=[mid])
                continue
            if state != "mine":
                continue
            try:
                g = self._detail(mid)
            except _Halt:
                self.db.release([mid])
                raise
            if g is None:
                self.db.write_person(games=[], missing=[(mid, owner, 1)], now=self._now())
            else:
                self.db.write_person(games=[g], missing=[], done_missing=[mid])
                self._bump(games=1)
            self.db.release([mid])


class _Progress:
    """상태줄 한 줄(전역 규칙 — bg-progress.d/<이름>-<PID>.txt). 끝나면 지운다."""

    def __init__(self, clock=time.monotonic):
        self.path = PROGRESS_DIR / f"dev-matches-{os.getpid()}.txt"
        self.clock = clock
        self.t0 = clock()
        self._last = -1e9

    def set(self, msg: str, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last < PROGRESS_EVERY_S:
            return
        self._last = now
        try:
            PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"개발 경기 · {msg} · 경과 {int((now - self.t0) // 60)}분\n", encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _lower_process_priority() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL_PRIORITY_CLASS
        except Exception:  # noqa: BLE001
            pass


def _worker_init() -> None:
    import rankcollect
    rankcollect.lower_thread_priority()   # 그 함수는 부른 스레드만 낮춘다(M4)


def run_day(db_path, rank_conn, api_factory, *, cancel=lambda: False, clock=time.monotonic, sleep=time.sleep,
            deadline: float | None = None, now_fn=lambda: datetime.now(KST), fifa_db=None,
            force: bool = False, cap_override: int | None = None, progress: _Progress | None = None,
            archive_db=None, season_at: int | None | object = ...) -> DayResult:
    """하루 몫을 받는다(이어 받기 포함). api_factory() → FCOnlineAPI 꼴(스레드마다 하나). deadline 은 clock 기준 —
    지나면 돌던 사람을 마친 뒤 멈춘다(finished_at 은 안 적는다 — 다음 트리거가 이어 받는다).
    season_at 을 안 주면 fifa.db·rank.db 에서 읽는다(`season_start_at`)."""
    start = now_fn()
    day = start.date().isoformat()
    res = DayResult(day=day)
    if _pick_dev_hit_429(fifa_db, day):
        res.skipped = "③ 랭커 픽이 오늘 429 — 시작 안 함"
        return res
    conn = open_db(db_path)
    db = _Db(conn)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM days")]
        today = next((r for r in rows if r["day"] == day), None)
        if today is None:
            today = {"day": day, "cap": next_cap(rows, day), "used": 0, "blocked": 0, "key_rejected": 0,
                     "stop": None, "started_at": start.isoformat(timespec="seconds"), "finished_at": None, "stage": None}
            with conn:
                conn.execute("INSERT INTO days (day, cap, started_at) VALUES (?, ?, ?)",
                             (day, today["cap"], today["started_at"]))
        if cap_override is not None:
            today["cap"] = cap_override
            with conn:
                conn.execute("UPDATE days SET cap = ? WHERE day = ?", (cap_override, day))
        res.cap, res.used = today["cap"], today["used"]
        if not force and (today["finished_at"] or today["stop"]):
            res.skipped = f"오늘 {'끝남' if today['finished_at'] else '멈춤(' + today['stop'] + ')'} — 건너뜀"
            res.stage_done = today["stage"]
            return res
        targets = plan_targets(rank_conn)
        if not targets:
            res.skipped = "rank.db 스냅숏 없음 — 건너뜀"
            return res
        floor_at = int(datetime.combine(start.date() - timedelta(days=config.DEV_MATCH_FLOOR_DAYS),
                                        datetime.min.time(), KST).timestamp())
        gate = _Gate(today["cap"], today["used"], cancel, sleep, clock)
        local = threading.local()

        def api_for_thread():
            if not hasattr(local, "api"):
                local.api = api_factory()
            return local.api

        run = _Run(db, gate, api_for_thread, now_fn, floor_at)
        run.day = day
        run.season_at = season_start_at(fifa_db, rank_conn) if season_at is ... else season_at
        stage = today["stage"]
        flushed = [clock()]

        def save_used():
            t = clock()
            db.set_used(day, gate.used, t - flushed[0])
            flushed[0] = t
        pool = ThreadPoolExecutor(max_workers=config.DEV_MATCH_WORKERS, initializer=_worker_init)

        soft = []   # 새 사람만 안 받는 멈춤(시간 다 됨 · 취소) — 돌던 사람은 마친다. gate.reason 에 넣으면 돌던 사람이 반쪽으로 끊긴다

        def over_deadline() -> bool:
            if not soft and deadline is not None and clock() >= deadline:
                soft.append("deadline")
            if not soft and cancel():
                soft.append("cancel")
            return bool(soft or gate.reason)

        def each(fn, name: str, who: list[Target]) -> bool:
            """대상마다 fn — 동시 WORKERS 명. 다 돌았으면 True."""
            pending: set = set()
            it = iter(who)
            done_n = 0
            exhausted = False
            while True:
                while not exhausted and len(pending) < config.DEV_MATCH_WORKERS and not over_deadline():
                    t = next(it, None)
                    if t is None:
                        exhausted = True
                        break
                    pending.add(pool.submit(fn, t))
                if not pending:
                    break
                fin, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in fin:
                    done_n += 1
                    e = f.exception()
                    if e is not None and not isinstance(e, _Halt):
                        run._bump(errors=1)
                        res.notes.append(f"{name}: {type(e).__name__}: {e}"[:300])
                save_used()
                if progress:
                    progress.set(f"{name} · {done_n:,}/{len(who):,}명 · 요청 {gate.used:,}/{gate.cap:,}")
            res.persons += done_n
            return exhausted and not gate.reason

        try:
            if not over_deadline():
                if progress:
                    progress.set("missing 다시", force=True)
                run.retry_missing()
            def advance(to: str) -> None:
                nonlocal stage
                stage = to
                with conn:
                    conn.execute("UPDATE days SET stage = ? WHERE day = ?", (stage, day))

            if stage is None and each(run.latest, "단 1", targets):
                advance("1")
            if stage == "1" and each(run.scan, "단 2", targets):
                advance("2")
            outside: list[Target] = []
            if stage in ("2", "3"):   # 단 4 순서에도 단 3 사람이 든다
                outside, note = plan_outside(archive_db, {t.profile_sn for t in targets})
                if note:
                    res.notes.append(note)
            if stage == "2" and each(run.scan, "단 3", outside):
                advance("3")
            if stage == "3":
                if run.season_at is None:
                    res.notes.append("단 4: 시즌 시작을 모름(fifa.db 시즌표·rank.db 새 시즌 둘 다 없음) — 건너뜀")
                    advance("done")
                elif each(run.back, "단 4", back_order(conn, targets + outside)):
                    advance("done")
        except _Halt:
            pass
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        save_used()
        res.used, res.requests, res.reason = gate.used, gate.requests, gate.reason or (soft[0] if soft else "")
        res.new_games, res.errors = run.new_games, run.errors
        res.stage_done = stage
        res.blocked = gate.reason == "blocked"
        res.key_rejected = gate.reason == "key"
        res.finished = gate.reason == "cap" or (not gate.reason and stage == "done")
        with conn:
            if res.finished:
                conn.execute("UPDATE days SET finished_at = ? WHERE day = ?", (now_fn().isoformat(timespec="seconds"), day))
            if gate.reason in ("blocked", "key", "outage"):
                conn.execute("UPDATE days SET stop = ?, blocked = MAX(blocked, ?), key_rejected = MAX(key_rejected, ?)"
                             " WHERE day = ?", (gate.reason, int(res.blocked), int(res.key_rejected), day))
        return res
    finally:
        conn.close()


# ── 실행 · 현황 ───────────────────────────────────────────────────────────────

def _log(lines: list[str]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    if sys.stdout is not None:  # pythonw 면 None
        print("\n".join(lines), flush=True)


def run(*, force: bool = False, cap: int | None = None, db_path=None, lock_path=None, fifa_db=None,
        rank_open=None, api_cls=None, clock=time.monotonic, sleep=time.sleep) -> int:
    from dev_archive import DEV_KEY_VAR
    t0 = clock()
    lines = [f"── {datetime.now(KST).isoformat(timespec='seconds')} dev_matches"]
    key = os.getenv(DEV_KEY_VAR, "").strip()
    if not key:
        _log(lines + [f"[OK]   {DEV_KEY_VAR} 없음 — 건너뜀(앱 키로 넘어가지 않는다)"])
        return 0
    lock = acquire_lock(lock_path)
    if lock is None:
        _log(lines + ["[OK]   다른 실행이 돌고 있음 — 건너뜀"])
        return 0
    _lower_process_priority()
    prog = _Progress(clock)
    rconn = None
    try:
        if api_cls is None:
            from nexon_api import FCOnlineAPI as api_cls
        if rank_open is None:
            import rankcollect
            rank_open = rankcollect.open_rank_db_ro
        rconn = rank_open()
        res = run_day(db_path, rconn, lambda: api_cls(key), clock=clock, sleep=sleep,
                      deadline=clock() + config.DEV_MATCH_DAY_HOURS * 3600, fifa_db=fifa_db,
                      force=force, cap_override=cap, progress=prog)
    except Exception as e:  # noqa: BLE001
        _log(lines + [f"[FAIL] {type(e).__name__}: {e}", traceback.format_exc().rstrip()])
        return 1
    finally:
        if rconn is not None:
            rconn.close()
        prog.clear()
        release_lock(lock)
    took = clock() - t0
    if res.skipped:
        lines.append(f"[OK]   {res.skipped}")
    else:
        rate = res.requests / took if took > 0 else 0
        mark = "[FAIL]" if res.key_rejected else "[OK]  "
        lines.append(f"{mark} {res.day} · 요청 {res.requests:,}(오늘 {res.used:,}/{res.cap:,}) · 새 경기 {res.new_games:,}"
                     f" · {res.persons:,}명 · 단 {res.stage_done or '-'} · 멈춤 {res.reason or '없음(끝)'}"
                     f" · 오류 {res.errors} · {int(took)}초 · 초당 {rate:.1f}")
        lines += [f"       {n}" for n in res.notes[:5]]
    _log(lines)
    return 1 if res.key_rejected else 0


def status(db_path=None) -> int:
    p = Path(db_path) if db_path else DB_PATH
    if not p.exists():
        print("아직 없음")
        return 0
    conn = open_db(p)
    try:
        q = lambda s: conn.execute(s).fetchone()  # noqa: E731
        size = sum(f.stat().st_size for f in p.parent.glob(p.name + "*"))
        print(f"파일 {size / 1e6:.1f}MB · 경기 {q('SELECT COUNT(*) FROM games')[0]:,}"
              f" · 사람 {q('SELECT COUNT(*) FROM owners')[0]:,}(대상 {q('SELECT COUNT(*) FROM owners WHERE profile_sn IS NOT NULL')[0]:,})"
              f" · 선발 {q('SELECT COUNT(*) FROM lineups')[0]:,} · 커서 {q('SELECT COUNT(*) FROM cursor')[0]:,}"
              f" · 빈 구간 {q('SELECT COUNT(*) FROM owners WHERE gap_at IS NOT NULL')[0]:,}")
        print("missing", tuple(q("SELECT COUNT(*), SUM(tries >= %d) FROM missing" % config.DEV_MATCH_MISSING_TRIES)),
              "(전체, 포기)")
        for r in conn.execute("SELECT * FROM days ORDER BY day DESC LIMIT 7"):
            print("날", dict(r))
    finally:
        conn.close()
    return 0


def check_fc(db_path=None, fifa_db=None, out=print) -> int:
    """(b) 의 전제 확인 — 내 계정(fifa.db trade_state.my_ouid)의 감독모드 경기를, 겹치는 창 [dev 첫 경기, dev 마지막 경기]
    안에서 앱 fifa.db 와 dev 양쪽 id 집합으로 대조한다. 같은 경기의 FC 는 둘 다 그 경기 등급을 읽어 늘 같으니 재지 않는다 —
    (b) 를 틀리게 하는 것은 dev 가 **빠뜨린 경기**다(조각 ② M4). 둘 다 읽기만."""
    fp = Path(fifa_db) if fifa_db else config.DB_PATH
    dp = Path(db_path) if db_path else DB_PATH
    if not fp.exists() or not dp.exists():
        out(f"[FAIL] {'fifa.db' if not fp.exists() else 'dev_matches.db'} 없음")
        return 1
    open_db(dp).close()   # 우리 파일 — 조각 ① 때 만든 파일이면 새 열(depth·base_at)을 먼저 붙인다
    f = _ro(fp)
    d = _ro(dp)
    try:
        r = f.execute("SELECT value FROM trade_state WHERE key = 'my_ouid'").fetchone()
        mine = r[0] if r else None
        if not mine:
            out("[FAIL] 내 계정 확인 전 — trade_state.my_ouid 가 없다(API 키가 바뀐 뒤엔 거래를 한 번 받아야 생긴다)")
            return 1
        o = d.execute("SELECT id FROM owners WHERE ouid = ?", (mine,)).fetchone()
        if o is None:
            out("[FAIL] dev_matches.db 에 내 계정 경기가 없다(1만 위 밖이거나 아직 못 받음)")
            return 1
        c = d.execute("SELECT base_at, top_at FROM cursor WHERE owner = ? AND depth IS NOT NULL", (o[0],)).fetchone()
        if c is None or c[0] is None or c[1] is None:
            out("[FAIL] 내 계정의 끝까지 훑은 구간을 아직 모른다(커서 depth 없음 — 단 4 가 한 번 돌면 생긴다)")
            return 1
        lo, hi = c[0], c[1]   # 끝까지 훑었다고 아는 구간만 — 그 앞의 빈칸은 단 4 몫이라 빠짐으로 세지 않는다
        dev = {r[0]: r[1] for r in d.execute("SELECT match_id, at FROM games WHERE (o1 = ? OR o2 = ?) AND at BETWEEN ? AND ?",
                                             (o[0], o[0], lo, hi))}
        app = set()
        for mid, when in f.execute("SELECT m.match_id, m.match_date FROM matches m JOIN match_players p"
                                   " ON p.match_id = m.match_id WHERE p.ouid = ? AND m.match_type = ?",
                                   (mine, config.DEFAULT_MATCH_TYPE)):
            at = kst_epoch(when)
            if at is not None and lo <= at <= hi:
                app.add(mid)
    finally:
        f.close()
        d.close()
    only_app, only_dev = sorted(app - set(dev)), sorted(set(dev) - app)
    span = (f"{datetime.fromtimestamp(lo, KST):%m-%d %H:%M} ~ {datetime.fromtimestamp(hi, KST):%m-%d %H:%M}")
    out(f"       창 {span} · 앱 {len(app):,}판 · dev {len(dev):,}판")
    out(f"{'[OK]  ' if not only_app else '[FAIL]'} dev 가 빠뜨린 경기 {len(only_app)}"
        + (f" — 예: {', '.join(only_app[:5])}" if only_app else ""))
    out(f"       앱에 없는 dev 경기 {len(only_dev)}(앱이 아직 안 받은 경기 — 앱에서 검색하면 줄어야 한다)")
    return 1 if only_app else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="개발 PC 경기 수집(이 PC 전용)")
    ap.add_argument("--force", action="store_true", help="오늘 끝났거나 멈췄어도 다시")
    ap.add_argument("--cap", type=int, help="오늘 상한을 이 값으로(시험용)")
    ap.add_argument("--status", action="store_true", help="현황만")
    ap.add_argument("--check-fc", action="store_true", help="내 계정 경기를 앱 fifa.db 와 대조((b) 의 전제)")
    a = ap.parse_args(argv)
    if a.status:
        return status()
    if a.check_fc:
        return check_fc()
    return run(force=a.force, cap=a.cap)


if __name__ == "__main__":
    raise SystemExit(main())
