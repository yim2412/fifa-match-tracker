"""개발 PC 경기 수집(④) — 1만 위 랭커의 감독모드 경기를 요약해 쌓는다. 이 PC 전용 · 배포판 밖 · 서비스 키에만.

근거·결정은 ROADMAP "개발 PC 수집 확장" 2판. 목표 (a) 같은 OVR 대비 · (b) 정확한 시즌 FC · (c) 팀컬러 상성의 **재료만** 쌓는다
(계산은 58·59). 원자료는 `DATA_DIR/dev_matches.db` 에만 — 다른 구단주 경기라 저장소·배포판에 안 들어간다.
`tools/dev_archive.py`(①②③ · FifaDevArchive)와 **따로 돈다** — 거기 넣으면 ran_ok_today 에 엮여 홈페이지 요청(②)이 2시간마다
다시 돌거나 ④ 가 같이 건너뛰어진다(1회차 검토 H2).

| 규칙 | 어디 |
|---|---|
| 서비스 키(`DEV_NEXON_API_KEY`)가 없으면 요청 0 — 앱 키로 넘어가지 않는다(앱 한도를 태우면 앱이 원인 모를 429 를 받는다 · H4) | `run` |
| 같은 날 ③(랭커 픽 dev)이 429 를 받았으면 시작 안 함 — fifa.db 는 읽기 전용 URI 로만, **한 줄도 안 쓴다** | `_pick_dev_hit_429` |
| 하루 상한 `next_cap`: 전날 막힘 0 · 끝냄 · 6시간 안 → ×1.5(끝 35만) · 막힘 → 절반 + 달력 3일 유지 · 그 밖 그대로 | `next_cap` |
| 요청마다: 취소·멈춤 → 429 쉼 대기 → 상한 → 요청(재시도 끔). 429 → 전원 60초 쉬고 한 번 더 → 또 429 면 막힘. 403 막힘 · 키 거절·400 → 그날 멈춤 · 5xx·네트워크 20연속 → 그날 멈춤(막힘 아님) | `_Gate.call` |
| 목록 훑기는 **커서 `top_id` 에서만** 멈춘다 — games 에 있는지는 상세를 건너뛸지에만(상대가 먼저 넣은 경기에서 멈추면 그 아래가 빈다 · H3) | `_Run.scan` |
| 커서는 옛 커서(또는 바닥)까지 다 훑은 뒤에만 올린다 — 중간에 끊기면 그대로, 다음 실행이 맨 앞부터 | `_Run.scan` |
| 커서 없는 사람은 실행 날 `DEV_MATCH_FLOOR_DAYS` 전 00:00 KST 보다 옛 경기에서 멈춘다(그 앞은 조각 ② 단 4 — 거슬러 받기) | `_Run.floor_at` |
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
    back_done INTEGER NOT NULL DEFAULT 0   -- 거슬러 받기 끝(조각 ②)
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
    stage TEXT                      -- 끝낸 단: '1' · 'done'
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
    return conn


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
            r = self.conn.execute("SELECT top_id, top_at FROM cursor WHERE owner = ?", (owner,)).fetchone()
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
                     now: str = "") -> None:
        """한 사람(또는 missing 한 묶음)의 결과를 한 트랜잭션으로. missing = [(match_id, owner, 더할 tries)]."""
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
            if gap is not None:
                self.conn.execute("UPDATE owners SET gap_at = MAX(COALESCE(gap_at, 0), ?) WHERE id = ?", (gap[1], gap[0]))

    def set_used(self, day: str, used: int) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE days SET used = ? WHERE day = ?", (used, day))


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


class _Run:
    def __init__(self, db: _Db, gate: _Gate, api_for_thread, now_fn, floor_at: int):
        self.db, self.gate, self.api, self.now_fn = db, gate, api_for_thread, now_fn
        self.floor_at = floor_at
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
                        complete = True
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
                    if at is not None:
                        oldest_seen = at if oldest_seen is None else min(oldest_seen, at)
                        if stop_at is not None and at < stop_at:
                            complete = True   # 커서·바닥보다 옛 경기 — 커서 id 가 목록에서 사라졌어도 여기서 멈춘다
                            break
                if complete:
                    break
                if len(page) < config.DEV_MATCH_PAGE:
                    complete = True
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
        new_cur = None
        if complete:
            if newest is not None:
                new_cur = (oid, newest[0], newest[1])
            elif cur is None or top_id is None:
                new_cur = (oid, None, int(self.now_fn().timestamp()))   # 경기가 없었다 — 다음엔 지금부터
        self.db.write_person(games=games, missing=missing, cursor=new_cur,
                             gap=(oid, oldest_seen) if gap and oldest_seen is not None else None, now=self._now())
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
            force: bool = False, cap_override: int | None = None, progress: _Progress | None = None) -> DayResult:
    """하루 몫을 받는다(이어 받기 포함). api_factory() → FCOnlineAPI 꼴(스레드마다 하나). deadline 은 clock 기준 —
    지나면 돌던 사람을 마친 뒤 멈춘다(finished_at 은 안 적는다 — 다음 트리거가 이어 받는다)."""
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
        stage = today["stage"]
        pool = ThreadPoolExecutor(max_workers=config.DEV_MATCH_WORKERS, initializer=_worker_init)

        soft = []   # 새 사람만 안 받는 멈춤(시간 다 됨 · 취소) — 돌던 사람은 마친다. gate.reason 에 넣으면 돌던 사람이 반쪽으로 끊긴다

        def over_deadline() -> bool:
            if not soft and deadline is not None and clock() >= deadline:
                soft.append("deadline")
            if not soft and cancel():
                soft.append("cancel")
            return bool(soft or gate.reason)

        def each(fn, name: str) -> bool:
            """대상마다 fn — 동시 WORKERS 명. 다 돌았으면 True."""
            pending: set = set()
            it = iter(targets)
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
                db.set_used(day, gate.used)
                if progress:
                    progress.set(f"{name} · {done_n:,}/{len(targets):,}명 · 요청 {gate.used:,}/{gate.cap:,}")
            res.persons += done_n
            return exhausted and not gate.reason

        try:
            if not over_deadline():
                if progress:
                    progress.set("missing 다시", force=True)
                run.retry_missing()
            if stage is None and each(run.latest, "단 1"):
                stage = "1"
                with conn:
                    conn.execute("UPDATE days SET stage = ? WHERE day = ?", (stage, day))
            if stage == "1" and each(run.scan, "단 2"):
                stage = "done"
                with conn:
                    conn.execute("UPDATE days SET stage = ? WHERE day = ?", (stage, day))
        except _Halt:
            pass
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        db.set_used(day, gate.used)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="개발 PC 경기 수집(이 PC 전용)")
    ap.add_argument("--force", action="store_true", help="오늘 끝났거나 멈췄어도 다시")
    ap.add_argument("--cap", type=int, help="오늘 상한을 이 값으로(시험용)")
    ap.add_argument("--status", action="store_true", help="현황만")
    a = ap.parse_args(argv)
    if a.status:
        return status()
    return run(force=a.force, cap=a.cap)


if __name__ == "__main__":
    raise SystemExit(main())
