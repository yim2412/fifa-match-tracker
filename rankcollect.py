"""랭킹 1만 명 수집 — 하루 한 번 감독모드 랭킹 500쪽을 읽어 rank.db 에 쌓는다(1.1.1, 기본 꺼짐).

랭킹 추이·ELO 컷·시즌 말 예측은 지난 값이 있어야 그릴 수 있고, 지난 값은 시간으로만 쌓인다 — 그래서 화면보다 먼저.

- **rank.db 는 fifa.db 와 따로**: 지우기가 파일 삭제라 VACUUM(457MB 전체 재작성 · WAL 이 DB 만큼)이 필요 없고,
  1.0.3 이 여는 fifa.db 는 그대로다.
- **반쪽 저장 안 함**: 한 쪽이라도 못 읽으면 남은 요청을 취소하고 회차를 버린다(빈 쪽만은 그 쪽을
  RANK_EMPTY_RETRIES 번 다시 받아 본 뒤). 쪽 판정은 ranker.judge_page —
  구조가 바뀐 응답이 '정상'으로 읽히면 반쪽 스냅숏이 조용히 쌓인다.
- **실패 종류마다 다르게 센다**(record_result): 실패 → 대기를 1·2·4…24시간으로 · 차단(403·429·Cloudflare)이
  서로 다른 회차에 RANK_BLOCK_ROUNDS 번 이어지면 스스로 끈다(D6 — 넥슨이 막으면 검색 때 쓰는 랭커 카드·팀컬러도
  같이 죽는다) · 연결 안 됨(절전 복귀)·정각 걸침은 세지 않고 다음에 다시.
- **두 실행본(설치판·포터블)**: 수집 직전에 .env 를 다시 읽고(config.read_env_switches), 켜짐 여부·실패 횟수는
  rank.db 의 collect_state 에, 동시 수집은 collect_lock(하트비트)이 막는다.

언제 돌릴지(1시간 확인 · 시작 시각 · 예약 하나)는 앱 쪽 일이고, 여기는 is_due·pick_start·start_still_valid 만 준다.
터미널 스모크: `python rankcollect.py --pages 3`(실제 3쪽을 읽어 판정·집계만 찍는다 — 저장 안 함).
"""
from __future__ import annotations

import os
import random
import sqlite3
import statistics
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import config
import ranker
import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    taken_at     TEXT NOT NULL,      -- 회차 시작(이 PC 시각)
    row_count    INTEGER NOT NULL,
    dup_count    INTEGER NOT NULL,   -- 순위 이동으로 쪽 경계에서 겹쳐 버린 행
    top_elo      REAL,
    season_seq   INTEGER NOT NULL,   -- 1부터. 새 시즌으로 보면 하나 오른다
    new_season   INTEGER NOT NULL,
    ended_season INTEGER             -- 그때 시즌표에서 마지막으로 끝난 시즌 번호(모르면 앞 값)
);
CREATE INDEX IF NOT EXISTS idx_snap_taken ON snapshots(taken_at);
-- 원본 — 다른 구단주 1만 명분이라 RANK_RAW_KEEP_DAYS 만 둔다(prune_raw)
CREATE TABLE IF NOT EXISTS snapshot_rows (
    snapshot_id INTEGER NOT NULL,
    rank        INTEGER NOT NULL,
    profile_sn  INTEGER NOT NULL,
    nickname    TEXT NOT NULL,
    level INTEGER, team_value INTEGER, elo REAL, win INTEGER, draw INTEGER, lose INTEGER,
    team_color TEXT, color_count INTEGER, formation TEXT,
    grade INTEGER, best_grade INTEGER, prev_grade INTEGER,
    PRIMARY KEY (snapshot_id, profile_sn)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_rows_sn ON snapshot_rows(profile_sn);
-- 집계 — 영구. 순위 구간(tier = 구간 끝 순위)별 포메이션·팀컬러 인원
CREATE TABLE IF NOT EXISTS tier_counts (
    snapshot_id INTEGER NOT NULL, tier INTEGER NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, n INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, tier, kind, key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tier_values (
    snapshot_id INTEGER NOT NULL, tier INTEGER NOT NULL, rows INTEGER NOT NULL,
    value_avg INTEGER, value_median INTEGER,
    PRIMARY KEY (snapshot_id, tier)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS cut_elo (
    snapshot_id INTEGER NOT NULL, rank INTEGER NOT NULL, elo REAL,
    PRIMARY KEY (snapshot_id, rank)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS collect_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS collect_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1), owner TEXT NOT NULL, pid INTEGER, heartbeat_at TEXT NOT NULL
);
"""

OPEN_TIMEOUT_S = 15
_OPEN_LOCK = threading.Lock()  # WAL 전환은 잠금 대기를 안 거친다 — store._OPEN_LOCK 과 같은 이유
_THREAD_PRIORITY_BELOW_NORMAL = -1


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def open_rank_db(path: Path | str | None = None) -> sqlite3.Connection:
    """rank.db 를 열고 없으면 만든다. 스레드마다 따로 연다."""
    p = Path(path) if path else config.RANK_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=OPEN_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    with _OPEN_LOCK:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    return conn


# ── 집계 ────────────────────────────────────────────────────────────────────

def tier_of(rank: int) -> int | None:
    for t in config.RANK_TIERS:
        if rank <= t:
            return t
    return None


def aggregate(rows: list[ranker.RankRow]):
    """→ (tier_counts 행, tier_values 행, cut_elo 행). 팀컬러를 안 쓰는 사람은 key "" 로 센다(비율의 분모)."""
    counts: dict[tuple[int, str], Counter] = {}
    values: dict[int, list[int]] = {}
    members: Counter = Counter()
    for r in rows:
        t = tier_of(r.rank)
        if t is None:
            continue
        members[t] += 1
        counts.setdefault((t, "formation"), Counter())[r.formation or ""] += 1
        counts.setdefault((t, "team_color"), Counter())[r.team_color or ""] += 1
        if r.team_value is not None:
            values.setdefault(t, []).append(r.team_value)
    count_rows = [(t, kind, key, n) for (t, kind), c in sorted(counts.items()) for key, n in sorted(c.items())]
    value_rows = []
    for t in sorted(members):
        v = values.get(t, [])
        value_rows.append((t, members[t], round(sum(v) / len(v)) if v else None,
                           round(statistics.median(v)) if v else None))
    by_rank = sorted(rows, key=lambda r: r.rank)
    cut_rows = []
    for target in config.RANK_CUT_RANKS:
        # 정확히 그 순위가 빠졌으면(쪽 경계 이동) 바로 아래 사람 — 순위가 컷보다 위인 사람을 쓰면 컷이 부풀려진다
        # 목록이 그 순위까지 안 가면(시즌 초) 그 컷은 비워 둔다
        hit = next((r for r in by_rank if r.rank >= target), None)
        if hit is not None:
            cut_rows.append((target, hit.elo))
    return count_rows, value_rows, cut_rows


def dedup(rows: list[ranker.RankRow]) -> tuple[list[ranker.RankRow], int]:
    """프로필 번호로 겹침 제거(앞 쪽 것을 남긴다) → (행, 버린 수)."""
    seen, out = set(), []
    for r in rows:
        if r.profile_sn in seen:
            continue
        seen.add(r.profile_sn)
        out.append(r)
    return out, len(rows) - len(out)


# ── 저장 ────────────────────────────────────────────────────────────────────

def save_snapshot(conn: sqlite3.Connection, rows: list[ranker.RankRow], taken_at: datetime,
                  dup_count: int = 0, ended_season: int | None = None) -> int:
    """원본 + 집계를 한 트랜잭션으로 — 도중에 죽어도 반쪽이 안 남는다. → 스냅숏 id."""
    prev = conn.execute("SELECT row_count, season_seq, ended_season FROM snapshots "
                        "ORDER BY taken_at DESC, id DESC LIMIT 1").fetchone()
    if ended_season is None and prev is not None:
        ended_season = prev["ended_season"]
    new_season = bool(prev) and (
        len(rows) < prev["row_count"] * config.RANK_SEASON_DROP_RATIO
        or (ended_season is not None and prev["ended_season"] is not None
            and ended_season > prev["ended_season"]))
    seq = (prev["season_seq"] + (1 if new_season else 0)) if prev else 1
    top = min(rows, key=lambda r: r.rank).elo if rows else None
    counts, values, cuts = aggregate(rows)
    with conn:
        cur = conn.execute(
            "INSERT INTO snapshots (taken_at, row_count, dup_count, top_elo, season_seq, new_season, ended_season)"
            " VALUES (?,?,?,?,?,?,?)",
            (_iso(taken_at), len(rows), dup_count, top, seq, int(new_season), ended_season))
        sid = cur.lastrowid
        conn.executemany(
            "INSERT INTO snapshot_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(sid, r.rank, r.profile_sn, r.nickname, r.level, r.team_value, r.elo, r.win, r.draw, r.lose,
              r.team_color, r.color_count, r.formation, r.grade, r.best_grade, r.prev_grade) for r in rows])
        conn.executemany("INSERT INTO tier_counts VALUES (?,?,?,?,?)", [(sid, *c) for c in counts])
        conn.executemany("INSERT INTO tier_values VALUES (?,?,?,?,?)", [(sid, *v) for v in values])
        conn.executemany("INSERT INTO cut_elo VALUES (?,?,?)", [(sid, *c) for c in cuts])
    return sid


def prune_raw(conn: sqlite3.Connection, now: datetime) -> int:
    """RANK_RAW_KEEP_DAYS 지난 스냅숏의 원본만 지운다(집계는 남긴다). → 지운 행 수."""
    cutoff = _iso(now - timedelta(days=config.RANK_RAW_KEEP_DAYS))
    with conn:
        cur = conn.execute("DELETE FROM snapshot_rows WHERE snapshot_id IN "
                           "(SELECT id FROM snapshots WHERE taken_at < ?)", (cutoff,))
    return cur.rowcount


def last_snapshot_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT MAX(taken_at) AS t FROM snapshots").fetchone()
    return datetime.fromisoformat(row["t"]) if row and row["t"] else None


def cut_elo(conn: sqlite3.Connection, snapshot_id: int | None = None) -> dict[int, float | None]:
    """정해 둔 순위의 ELO — 기본은 마지막 스냅숏."""
    if snapshot_id is None:
        row = conn.execute("SELECT id FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1").fetchone()
        if row is None:
            return {}
        snapshot_id = row["id"]
    return {r["rank"]: r["elo"] for r in
            conn.execute("SELECT rank, elo FROM cut_elo WHERE snapshot_id=? ORDER BY rank", (snapshot_id,))}


def open_rank_db_ro(path: Path | str | None = None) -> sqlite3.Connection | None:
    """읽기 전용으로 연다 — 없으면 None(만들지 않는다: 수집을 켠 적 없는 사람에게 파일이 생기지 않게).
    스키마를 안 건드리고 query_only 로 둔다. 읽고 바로 닫을 것 — 열린 연결이 delete_db 를 막는다."""
    p = Path(path) if path else config.RANK_DB_PATH
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(str(p), timeout=OPEN_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")
        return conn
    except sqlite3.Error:
        return None


def cut_series(conn: sqlite3.Connection, ranks=config.ELO_CUT_LINES) -> dict[int, list[tuple[str, float]]]:
    """순위 컷의 스냅숏별 시계열 — {순위: [(taken_at ISO, elo)]} 오래된 것부터. cut_elo 는 영구라 14일 넘게 남는다."""
    out: dict[int, list[tuple[str, float]]] = {r: [] for r in ranks}
    marks = ",".join("?" * len(ranks))
    for r in conn.execute(f"SELECT s.taken_at, c.rank, c.elo FROM cut_elo c JOIN snapshots s ON s.id = c.snapshot_id"
                          f" WHERE c.rank IN ({marks}) AND c.elo IS NOT NULL ORDER BY s.taken_at", tuple(ranks)):
        out[r["rank"]].append((r["taken_at"], r["elo"]))
    return out


def season_start_by_snapshots(conn: sqlite3.Connection) -> str | None:
    """시즌표가 없을 때 "지금 시즌"의 시작 — 마지막 season_seq 의 첫 스냅숏 시각(ISO). 스냅숏이 없으면 None."""
    row = conn.execute("SELECT MIN(taken_at) AS t FROM snapshots WHERE season_seq ="
                       " (SELECT season_seq FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1)").fetchone()
    return row["t"] if row and row["t"] else None


def sync_tracked_elo(rank_conn: sqlite3.Connection, fifa_path: Path | str | None = None,
                     cancel: threading.Event | None = None) -> int:
    """따라가기 계정(fifa.db elo_track)의 ELO 를 원본이 남은 스냅숏 전부(14일)에서 찾아 elo_history 에 옮긴다.

    멱등(ux_elo_src)이고 매번 14일을 다 보므로 따로 backfill 이 없다 — 새로 넣은 계정도 다음 회차에 14일치가 찬다.
    save_snapshot 커밋 **뒤** 부른다(rank.db 긴 트랜잭션 중이 아니게). fifa.db 가 잠겼거나 실패하면 건너뛴다 —
    다음 회차가 다시 채운다. 계정마다 cancel 을 보고 한 계정 한 트랜잭션(끊을 때 쓰기 잠금이 남지 않게). → 넣은 줄 수."""
    if not config.track_allowed():
        return 0
    added = 0
    try:
        fconn = store.open_db(fifa_path or config.DB_PATH)
    except sqlite3.Error:
        return 0
    try:
        for t in store.track_list(fconn):
            if cancel is not None and cancel.is_set():
                break
            sn = t["profile_sn"]
            if sn is None:
                # 검색 때 랭킹을 못 받아 프로필 번호가 없던 계정 — 마지막 스냅숏에서 닉네임으로 찾아 둔다
                hit = rank_conn.execute(
                    "SELECT profile_sn FROM snapshot_rows WHERE nickname = ? AND snapshot_id ="
                    " (SELECT id FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1)",
                    (t["nickname"] or "",)).fetchone()
                if hit is None:
                    continue
                sn = hit["profile_sn"]
                store.track_add(fconn, t["ouid"], sn, t["nickname"] or "")
            rows = [(r["taken_at"], r["elo"], r["rank"], r["profile_sn"], r["nickname"]) for r in rank_conn.execute(
                "SELECT s.taken_at, r.elo, r.rank, r.profile_sn, r.nickname FROM snapshot_rows r"
                " JOIN snapshots s ON s.id = r.snapshot_id WHERE r.profile_sn = ? AND r.elo IS NOT NULL", (sn,))]
            if rows:
                added += store.save_elo_snapshots(fconn, t["ouid"], rows)
    except sqlite3.Error:
        pass       # 잠김 등 — 회차 성공에는 영향 없음, 다음 회차가 14일치를 다시 본다
    finally:
        fconn.close()
    return added


# ── 수집 상태 (실패 대기 · D6 · 켜짐 사본) ──────────────────────────────────────

def get_state(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM collect_state")}


def _set_state(conn: sqlite3.Connection, **kv) -> None:
    with conn:
        conn.executemany("INSERT INTO collect_state (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         [(k, None if v is None else str(v)) for k, v in kv.items()])


def set_enabled(conn: sqlite3.Connection, on: bool, reason: str = "") -> None:
    """켜짐 사본 — .env 쓰기가 끝내 실패해도(다른 실행본이 열고 있음) D6 로 끈 것이 남게. 켜면 실패 기록을 비운다."""
    if on:
        _set_state(conn, enabled=1, off_reason="", fail_count=0, block_rounds=0, retry_at="")
    else:
        _set_state(conn, enabled=0, off_reason=reason)


def _int(state: dict, key: str) -> int:
    try:
        return int(state.get(key) or 0)
    except ValueError:
        return 0


def record_result(conn: sqlite3.Connection, kind: str, now: datetime, message: str = "") -> dict:
    """회차 결과를 상태에 반영 → {"disabled": D6 로 껐나, "fail_notice": 실패가 이어져 알릴 때인가}."""
    st = get_state(conn)
    fails, blocks = _int(st, "fail_count"), _int(st, "block_rounds")
    out = {"disabled": False, "fail_notice": False}
    if kind == "ok":
        _set_state(conn, fail_count=0, block_rounds=0, retry_at="", last_success_at=_iso(now),
                   last_result=kind, last_message="")
        return out
    if kind not in ("failed", "blocked"):
        # 연결 안 됨 · 정각 걸침 · 취소 · 잠김 — 세지 않는다(다음 확인에 그냥 다시)
        _set_state(conn, last_result=kind, last_message=message)
        return out
    fails += 1
    blocks = blocks + 1 if kind == "blocked" else 0   # 차단 아닌 실패가 끼면 '연속'이 끊긴다
    backoff = config.RANK_RETRY_BACKOFF_H[min(fails, len(config.RANK_RETRY_BACKOFF_H)) - 1]
    _set_state(conn, fail_count=fails, block_rounds=blocks, retry_at=_iso(now + timedelta(hours=backoff)),
               last_result=kind, last_message=message)
    out["fail_notice"] = fails >= config.RANK_FAIL_NOTICE_ROUNDS
    if blocks >= config.RANK_BLOCK_ROUNDS:
        set_enabled(conn, False, "blocked")
        try:
            config.set_rank_collect(False)
        except OSError:
            pass  # .env 를 못 써도 collect_state 사본이 막는다
        out["disabled"] = True
    return out


def is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    """간격(실패 중이면 늘어난 대기)이 지났나."""
    st = get_state(conn)
    if st.get("enabled") == "0":
        return False
    retry = st.get("retry_at")
    if retry and now < datetime.fromisoformat(retry):
        return False
    last = last_snapshot_at(conn)
    return last is None or now >= last + timedelta(hours=config.RANK_COLLECT_INTERVAL_H)


def _hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def pick_start(now: datetime, rng: random.Random | None = None) -> datetime:
    """이번 시각의 +lo~+hi분 사이 무작위(이미 지났으면 남은 구간에서, hi 도 지났으면 다음 시각)."""
    lo, hi = config.RANK_START_JITTER_MIN
    base = _hour(now)
    if now >= base + timedelta(minutes=hi):
        base += timedelta(hours=1)
    first = max(base + timedelta(minutes=lo), now)
    last = base + timedelta(minutes=hi)
    span = (last - first).total_seconds()
    return first + timedelta(seconds=(rng or random).uniform(0, span))


def start_still_valid(planned: datetime, now: datetime) -> bool:
    """예약한 시작이 터졌을 때 그대로 시작해도 되나 — 절전 복귀 등으로 늦게 터졌으면 다시 고른다."""
    _, hi = config.RANK_START_JITTER_MIN
    return _hour(planned) == _hour(now) and now <= _hour(now) + timedelta(minutes=hi)


# ── 잠금 (두 실행본이 같이 수집하지 않게) ────────────────────────────────────

class CollectLock:
    def __init__(self, conn: sqlite3.Connection, now_fn=datetime.now):
        self.conn, self.now_fn = conn, now_fn
        self.token = f"{os.getpid()}-{uuid.uuid4().hex}"

    def acquire(self) -> bool:
        conn = self.conn
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT owner, heartbeat_at FROM collect_lock WHERE id = 1").fetchone()
            stale = _iso(self.now_fn() - timedelta(minutes=config.RANK_LOCK_STALE_MIN))
            if row is not None and row["owner"] != self.token and row["heartbeat_at"] >= stale:
                conn.rollback()
                return False
            conn.execute("INSERT OR REPLACE INTO collect_lock (id, owner, pid, heartbeat_at) VALUES (1, ?, ?, ?)",
                         (self.token, os.getpid(), _iso(self.now_fn())))
            conn.commit()
            return True
        except BaseException:
            conn.rollback()
            raise

    def heartbeat(self) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE collect_lock SET heartbeat_at = ? WHERE id = 1 AND owner = ?",
                                    (_iso(self.now_fn()), self.token))
        return cur.rowcount == 1

    def release(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM collect_lock WHERE id = 1 AND owner = ?", (self.token,))


# ── 한 회차 ─────────────────────────────────────────────────────────────────

class Cancelled(Exception):
    pass


class Straddle(Exception):
    """응답 Date 가 두 시각에 걸쳤다 — 넥슨 갱신이 끼었을 수 있어 버린다(실패로 안 센다)."""


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentThread.restype = wintypes.HANDLE
    k.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
    k.SetThreadPriority.restype = wintypes.BOOL
    k.GetThreadPriority.argtypes = (wintypes.HANDLE,)
    k.GetThreadPriority.restype = ctypes.c_int
    return k


def _low_priority() -> None:
    # 작업자는 QThread 가 아니라 우선순위를 Qt 로 못 낮춘다 — 게임 중에도 돌기 때문
    if sys.platform == "win32":
        try:
            # 핸들 형을 적어야 한다 — 안 적으면 의사 핸들(-2)이 32비트 int 로 넘어가 64비트에서 조용히 실패한다
            k = _kernel32()
            k.SetThreadPriority(k.GetCurrentThread(), _THREAD_PRIORITY_BELOW_NORMAL)
        except Exception:
            pass


def _hour_key(date_header: str):
    try:
        d = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    return d.date(), d.hour


def run_round(fetch=None, pages: int = ranker.RANK_PAGES, workers: int | None = None,
              cancel: threading.Event | None = None, progress=None, heartbeat=None
              ) -> tuple[list[ranker.RankRow], int]:
    """1..pages 쪽을 읽어 판정·겹침 제거까지 → (행, 버린 수). 실패는 예외(RankerError 계열·Straddle·Cancelled).

    첫 실패에서 아직 안 나간 요청을 전부 취소한다 — 이미 버릴 회차에 넥슨 요청을 더 보내지 않게.
    """
    fetch = fetch or ranker.fetch_rank_rows
    stop = threading.Event()

    def stopped():
        return stop.is_set() or (cancel is not None and cancel.is_set())

    def one(page: int):
        if stopped():
            raise Cancelled()
        res = fetch(page)
        for _ in range(config.RANK_EMPTY_RETRIES):
            # 일시적인 빈 응답일 수 있다(10-05 실측: 같은 쪽을 바로 다시 받으니 정상) — 그 쪽만 다시 받는다.
            # 끝까지 비면 그대로 돌려줘 judge_page 가 구조 변경으로 판정한다.
            if res.rows:
                break
            end = time.monotonic() + config.RANK_EMPTY_RETRY_WAIT_S
            while not stopped() and time.monotonic() < end:   # 창 닫기(cancel)도 대기를 끊게 잘게 나눠 기다린다
                stop.wait(min(0.1, max(0.0, end - time.monotonic())))
            if stopped():
                raise Cancelled()
            res = fetch(page)
        return res

    results: dict[int, ranker.RankPageResult] = {}
    pool = ThreadPoolExecutor(max_workers=workers or config.RANK_COLLECT_WORKERS, initializer=_low_priority, thread_name_prefix="rankcollect")
    try:
        futs = [pool.submit(one, p) for p in range(1, pages + 1)]
        for done, fut in enumerate(as_completed(futs), 1):
            res = fut.result()           # 첫 실패가 여기서 올라와 finally 에서 나머지를 취소한다
            results[res.page] = res
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if progress:
                progress(done, pages)
            if heartbeat and done % config.RANK_LOCK_HEARTBEAT_PAGES == 0:
                heartbeat()
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
    return rows_from_pages(results, pages)


def rows_from_pages(results: dict[int, ranker.RankPageResult], pages: int
                    ) -> tuple[list[ranker.RankRow], int]:
    """다 읽은 쪽들 → (행, 버린 수). 정각 걸침은 Straddle, 구조 변경은 RankStructureError."""
    keys = {k for k in (_hour_key(r.date) for r in results.values() if r.date) if k}
    if len(keys) > 1:
        raise Straddle("수집 중에 정각을 넘겼습니다 — 다음 시각에 다시")
    out, prev = [], None
    for p in range(1, pages + 1):
        rows = results[p].rows
        if ranker.judge_page(p, rows, prev) == "end":
            break
        out.extend(rows)
        prev = rows
    return dedup(out)


# ── 목록 읽기는 한 프로세스에 하나 ────────────────────────────────────────────
# 수집과 팀컬러 목록(app_main.RankListLoader)이 같은 500쪽을 읽는다. 둘이 겹치면 뒤에 온 쪽이 앞 작업이 끝나기를
# 기다렸다가 그 스냅숏을 쓴다(요청 0) — 앞 작업이 실패·취소로 끝났으면 기다린 쪽이 스스로 읽는다.
_LIST_READ = threading.Lock()
_LIST_POLL_S = 0.2


def acquire_list_read(cancelled=lambda: False, on_wait=None) -> tuple[bool, bool]:
    """목록 읽기 차례를 얻는다 → (얻었나, 기다렸나). 기다리는 동안 cancelled() 가 참이면 (False, True).
    on_wait: 기다리기 시작할 때 한 번(화면에 '수집이 읽는 중' 표시)."""
    if _LIST_READ.acquire(blocking=False):
        return True, False
    if on_wait is not None:
        on_wait()
    while not cancelled():
        if _LIST_READ.acquire(timeout=_LIST_POLL_S):
            return True, True
    return False, True


def release_list_read() -> None:
    _LIST_READ.release()


def fresh_snapshot(db_path: Path | str | None = None, now: datetime | None = None
                   ) -> tuple[int, datetime] | None:
    """간격(RANK_COLLECT_INTERVAL_H) 안의, 원본이 남아 있는 마지막 스냅숏 → (id, 시각). 없으면 None.
    rank.db 가 없으면 만들지 않는다 — 수집을 안 켠 사람에게 빈 파일이 생기지 않게."""
    p = Path(db_path) if db_path else config.RANK_DB_PATH
    if not p.exists():
        return None
    now = now or datetime.now()
    conn = open_rank_db(p)
    try:
        row = conn.execute("SELECT id, taken_at FROM snapshots WHERE taken_at >= ? "
                           "ORDER BY taken_at DESC, id DESC LIMIT 1",
                           (_iso(now - timedelta(hours=config.RANK_COLLECT_INTERVAL_H)),)).fetchone()
        if row is None:
            return None
        if conn.execute("SELECT 1 FROM snapshot_rows WHERE snapshot_id = ? LIMIT 1", (row["id"],)).fetchone() is None:
            return None
        return row["id"], datetime.fromisoformat(row["taken_at"])
    finally:
        conn.close()


def snapshot_colors(nicknames, db_path: Path | str | None = None, now: datetime | None = None
                    ) -> tuple[dict[str, tuple[str, int | None]], datetime] | None:
    """팀컬러를 스냅숏에서 — {닉네임: (팀컬러, 구단가치)}, 스냅숏 시각. 목록에 없는 사람은 '랭킹 밖'("", None).
    팀컬러가 없는 행은 구단가치도 None(ranker._color_rows 와 같은 규칙). 쓸 스냅숏이 없으면 None."""
    snap = fresh_snapshot(db_path, now)
    if snap is None:
        return None
    sid, taken = snap
    want = set(nicknames)
    conn = open_rank_db(db_path)
    try:
        found = {}
        for r in conn.execute("SELECT nickname, team_color, team_value FROM snapshot_rows WHERE snapshot_id = ?",
                              (sid,)):
            if r["nickname"] in want:
                color = r["team_color"] or ""
                found[r["nickname"]] = (color, r["team_value"] if color else None)
    finally:
        conn.close()
    for n in want - found.keys():
        found[n] = ("", None)
    return found, taken


# ── 지우기 ───────────────────────────────────────────────────────────────────

def _pending_marker(p: Path) -> Path:
    return p.with_name(p.name + ".delete-pending")


def delete_db(db_path: Path | str | None = None) -> bool:
    """rank.db(+-wal·-shm)를 지운다 → 다 지웠나. 다른 실행본이 열고 있어 못 지우면 표시를 남겨
    다음에 켤 때(delete_pending) 먼저 지운다. 부르는 쪽이 수집을 먼저 멈추고 자기 연결을 닫는다."""
    p = Path(db_path) if db_path else config.RANK_DB_PATH
    ok = True
    for f in (p, p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            ok = False
    marker = _pending_marker(p)
    if ok:
        marker.unlink(missing_ok=True)
    else:
        try:
            marker.write_text(_iso(datetime.now()), encoding="utf-8")
        except OSError:
            pass
    return ok


def delete_pending(db_path: Path | str | None = None) -> bool:
    """켤 때 — 지난번에 못 지운 rank.db 가 있으면 지금 지운다. → 지울 게 있었나."""
    p = Path(db_path) if db_path else config.RANK_DB_PATH
    if not _pending_marker(p).exists():
        return False
    delete_db(p)
    return True


def read_status(db_path: Path | str | None = None) -> dict:
    """[정보] 창 표시용 — 마지막 성공·실패 횟수·끈 이유·스냅숏 수. rank.db 가 없으면 빈 dict(만들지 않는다)."""
    p = Path(db_path) if db_path else config.RANK_DB_PATH
    if not p.exists():
        return {}
    conn = open_rank_db(p)
    try:
        st = get_state(conn)
        st["snapshots"] = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        last = conn.execute("SELECT taken_at, row_count FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1"
                            ).fetchone()
        if last is not None:
            st["last_taken_at"], st["last_rows"] = last["taken_at"], last["row_count"]
        return st
    finally:
        conn.close()


def set_enabled_at(on: bool, reason: str = "", db_path: Path | str | None = None) -> None:
    """토글 — .env 와 rank.db 사본을 같이(사본이 D6 끔을 붙잡고 있어 .env 만 켜면 계속 disabled 다).
    끌 때 rank.db 가 없으면 만들지 않는다."""
    config.set_rank_collect(on)
    p = Path(db_path) if db_path else config.RANK_DB_PATH
    if not on and not p.exists():
        return
    conn = open_rank_db(p)
    try:
        set_enabled(conn, on, reason)
    finally:
        conn.close()


@dataclass
class Outcome:
    kind: str                 # ok · failed · blocked · offline · straddle · cancelled · locked · disabled · fresh
    message: str = ""
    snapshot_id: int | None = None
    rows: int = 0
    disabled_by_block: bool = False
    fail_notice: bool = False


def collect(*, db_path: Path | str | None = None, now_fn=datetime.now, fetch=None,
            cancel: threading.Event | None = None, progress=None, ended_season: int | None = None,
            pages: int = ranker.RANK_PAGES) -> Outcome:
    """한 회차 수집 — 스위치 확인 → 잠금 → 읽기 → 저장 → 상태 반영. 예약(언제 부를지)은 부르는 쪽."""
    web, on = config.read_env_switches()
    if config.notice_needed() or not web or not on:
        return Outcome("disabled", "랭킹 수집이 꺼져 있습니다(넥슨 홈페이지 데이터가 꺼져 있으면 잠깁니다)")
    got, waited = acquire_list_read(lambda: cancel is not None and cancel.is_set())
    if not got:
        return Outcome("cancelled")
    try:
        return _collect_locked(db_path, now_fn, fetch, cancel, progress, ended_season, pages, waited)
    finally:
        release_list_read()


def _collect_locked(db_path, now_fn, fetch, cancel, progress, ended_season, pages, waited) -> Outcome:
    conn = open_rank_db(db_path)
    try:
        if get_state(conn).get("enabled") == "0":
            return Outcome("disabled", "랭킹 수집이 꺼져 있습니다")
        if waited and not is_due(conn, now_fn()):
            # 기다리는 사이 팀컬러 목록 읽기가 스냅숏을 남겼다 — 같은 500쪽을 다시 읽지 않는다
            return Outcome("fresh", "방금 읽은 랭킹 목록을 썼습니다")
        lock = CollectLock(conn, now_fn)
        if not lock.acquire():
            return Outcome("locked", "다른 실행본이 수집 중입니다")
        try:
            taken = now_fn()
            out = Outcome("ok")
            try:
                rows, dups = run_round(fetch, pages=pages, cancel=cancel, progress=progress, heartbeat=lock.heartbeat)
                out.snapshot_id = save_snapshot(conn, rows, taken, dups, ended_season)
                out.rows = len(rows)
                sync_tracked_elo(conn, cancel=cancel)   # 커밋 뒤 · prune 전(지울 원본도 한 번 더 옮길 기회)
                prune_raw(conn, taken)
            except Cancelled:
                out = Outcome("cancelled")
            except Straddle as e:
                out = Outcome("straddle", str(e))
            except ranker.RankBlocked as e:
                out = Outcome("blocked", str(e))
            except ranker.RankOffline as e:
                out = Outcome("offline", str(e))
            except ranker.RankerError as e:
                out = Outcome("failed", str(e))
            if out.kind == "cancelled":
                return out             # 창을 닫는 중 — 상태를 건드리지 않는다
            flags = record_result(conn, out.kind, now_fn(), out.message)
            out.disabled_by_block, out.fail_notice = flags["disabled"], flags["fail_notice"]
            return out
        finally:
            lock.release()
    finally:
        conn.close()


def save_from_pages(results: dict[int, ranker.RankPageResult], taken: datetime, *,
                    pages: int = ranker.RANK_PAGES, ended_season: int | None = None,
                    db_path: Path | str | None = None, now_fn=datetime.now) -> Outcome | None:
    """팀컬러 때문에 500쪽을 다 읽었으면 그걸 스냅숏으로 — 수집이 켜져 있고 간격이 지났을 때만. 목록 읽기 차례
    (acquire_list_read)를 쥔 채 부른다. 저장 안 했으면 None. 쪽 판정·정각 걸침은 collect 와 같은 규칙."""
    web, on = config.read_env_switches()
    if config.notice_needed() or not web or not on:
        return None
    if any(not r.rows for r in results.values()):
        return None      # 빈 쪽은 일시적일 수 있다 — 세지 않고, 수집이 다시 받아 보고(run_round) 판정하게 둔다
    conn = open_rank_db(db_path)
    try:
        if get_state(conn).get("enabled") == "0" or not is_due(conn, now_fn()):
            return None
        lock = CollectLock(conn, now_fn)
        if not lock.acquire():
            return None
        try:
            try:
                rows, dups = rows_from_pages(results, pages)
            except Straddle:
                return None                      # 세지 않는다 — 다음 확인에 수집이 다시 읽는다
            except ranker.RankerError as e:
                out = Outcome("failed", str(e))
                flags = record_result(conn, "failed", now_fn(), out.message)
                out.disabled_by_block, out.fail_notice = flags["disabled"], flags["fail_notice"]
                return out
            out = Outcome("ok", snapshot_id=save_snapshot(conn, rows, taken, dups, ended_season), rows=len(rows))
            sync_tracked_elo(conn)
            prune_raw(conn, taken)
            record_result(conn, "ok", now_fn())
            return out
        finally:
            lock.release()
    finally:
        conn.close()


def _main(argv: list[str]) -> int:
    pages = 3
    if "--pages" in argv:
        pages = int(argv[argv.index("--pages") + 1])
    if not config.WEB_DATA:
        print(f"[FAIL] {config.WEB_DATA_OFF_MSG}")
        return 1
    try:
        rows, dups = run_round(pages=pages, progress=lambda d, n: print(f"  {d}/{n}쪽", end="\r"))
        print()
    except (ranker.RankerError, Straddle) as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return 1
    counts, values, cuts = aggregate(rows)
    print(f"[OK]   {pages}쪽 {len(rows)}행(겹침 {dups}) · {rows[0].rank}~{rows[-1].rank}위 · 1위 ELO {rows[0].elo}")
    print(f"[OK]   구간 {[(t, n) for t, n, _, _ in values]} · 컷 {cuts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
