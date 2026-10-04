"""랭킹 1만 명 수집 — 하루 한 번 감독모드 랭킹 500쪽을 읽어 rank.db 에 쌓는다(1.1.1, 기본 꺼짐).

랭킹 추이·ELO 컷·시즌 말 예측은 지난 값이 있어야 그릴 수 있고, 지난 값은 시간으로만 쌓인다 — 그래서 화면보다 먼저.

- **rank.db 는 fifa.db 와 따로**: 지우기가 파일 삭제라 VACUUM(457MB 전체 재작성 · WAL 이 DB 만큼)이 필요 없고,
  1.0.3·봇이 여는 fifa.db 는 그대로다.
- **반쪽 저장 안 함**: 한 쪽이라도 못 읽으면 남은 요청을 취소하고 회차를 버린다. 쪽 판정은 ranker.judge_page —
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
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import config
import ranker

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

    def one(page: int):
        if stop.is_set() or (cancel is not None and cancel.is_set()):
            raise Cancelled()
        return fetch(page)

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


@dataclass
class Outcome:
    kind: str                 # ok · failed · blocked · offline · straddle · cancelled · locked · disabled
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
    conn = open_rank_db(db_path)
    try:
        if get_state(conn).get("enabled") == "0":
            return Outcome("disabled", "랭킹 수집이 꺼져 있습니다")
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
