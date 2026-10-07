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

import json
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
from dataclasses import dataclass, field
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
-- 팀컬러 엠블럼(2.2.1) — snapshot_rows 에 열을 더하지 않고 옆 표로: 옛 버전의 INSERT 가 열 이름 없이 16칸이라
-- 열이 늘면 옛 버전이 이 DB 로 수집할 때 실패한다(ROADMAP 2.2.1 T13). 원본과 같이 지운다(prune_raw)
CREATE TABLE IF NOT EXISTS snapshot_emblems (
    snapshot_id INTEGER NOT NULL,
    profile_sn  INTEGER NOT NULL,
    emblem      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, profile_sn)
) WITHOUT ROWID;
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
-- 끝난 시즌의 최종 순위 컷 — 영구(끝난 시즌은 안 변한다 · 순위와 점수뿐 — 개인 정보 없음). 예측(predict.py)이 쓴다
CREATE TABLE IF NOT EXISTS season_cuts (
    season_no INTEGER NOT NULL, rank INTEGER NOT NULL, elo REAL NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (season_no, rank)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS collect_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS collect_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1), owner TEXT NOT NULL, pid INTEGER, heartbeat_at TEXT NOT NULL
);
-- ── 2.3.1 랭커 메타 — 전부 옆 표(snapshots·snapshot_rows 의 열과 INSERT 는 그대로 — 옛 버전이 같은 DB 로 수집한다) ──
-- 스냅숏마다 한 줄: 넥슨 데이터 기준 시각(N14) · 빈 순위 · 처리 깃발(그 처리와 같은 트랜잭션에서 올린다)
-- raw_pruned = 새 prune_raw 가 secure_delete 로 원본을 지웠다(아니면 옛 버전이 지운 흔적 → 켤 때 VACUUM)
CREATE TABLE IF NOT EXISTS snapshot_meta (
    snapshot_id INTEGER PRIMARY KEY, ref_time TEXT, rank_gaps INTEGER,
    anon_done INTEGER NOT NULL DEFAULT 0, person_done INTEGER NOT NULL DEFAULT 0, raw_pruned INTEGER NOT NULL DEFAULT 0
);
-- 익명 · 영구: 점수 분포(B5)
CREATE TABLE IF NOT EXISTS elo_hist (
    snapshot_id INTEGER NOT NULL, bin INTEGER NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (snapshot_id, bin)
) WITHOUT ROWID;
-- 사람별(U1) — 닉네임 없이 프로필 번호만 · 그 시즌 동안만(season_at = 그 시즌으로 넘긴 데이터 시각). 끄거나 동의 전이면 지운다
CREATE TABLE IF NOT EXISTS run_open (
    season_at TEXT NOT NULL, profile_sn INTEGER NOT NULL, start_at TEXT NOT NULL, last_at TEXT NOT NULL,
    censored_start INTEGER NOT NULL, PRIMARY KEY (season_at, profile_sn)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS elo_season (
    season_at TEXT NOT NULL, profile_sn INTEGER NOT NULL, peak REAL, peak_at TEXT, max_dd REAL,
    last_elo REAL, last_at TEXT, PRIMARY KEY (season_at, profile_sn)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS champ_watch (
    season_at TEXT NOT NULL, profile_sn INTEGER NOT NULL, first_at TEXT NOT NULL, last_at TEXT NOT NULL,
    state INTEGER NOT NULL, PRIMARY KEY (season_at, profile_sn)
) WITHOUT ROWID;
-- 끝난 기록 — 번호 없는 익명 줄이라 영구(UNIQUE 없음: 같은 값이 정당하게 겹친다)
CREATE TABLE IF NOT EXISTS run_done (season_at TEXT, hours REAL, censored_start INTEGER, censored_end INTEGER);
CREATE TABLE IF NOT EXISTS elo_season_done (season_at TEXT, peak REAL, max_dd REAL, last_elo REAL);
CREATE TABLE IF NOT EXISTS champ_first (season_at TEXT, games INTEGER, team_value INTEGER, at TEXT, first_seen_late INTEGER);
-- 랭커 픽 날짜별(N15) — 익명 · 영구. day = 랭커의 마지막 경기 날짜, rankers = 그 날짜에 센 랭커 수(분모 — 그날 줄 전부 같은 값)
CREATE TABLE IF NOT EXISTS pick_days (
    day TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, n INTEGER NOT NULL, rankers INTEGER NOT NULL,
    PRIMARY KEY (day, kind, key)
) WITHOUT ROWID;
-- 센 경기 — 경기 번호라 원본과 같이 RANK_RAW_KEEP_DAYS 뒤 지운다(prune_raw)
CREATE TABLE IF NOT EXISTS pick_counted (match_id TEXT PRIMARY KEY, day TEXT NOT NULL);
"""
PERSON_TABLES = ("run_open", "elo_season", "champ_watch")
PERSON_STATE = ("person_prev", "person_season")

OPEN_TIMEOUT_S = 15
_OPEN_LOCK = threading.Lock()  # WAL 전환은 잠금 대기를 안 거친다 — store._OPEN_LOCK 과 같은 이유
_THREAD_PRIORITY_BELOW_NORMAL = -1


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def open_rank_db(path: Path | str | None = None) -> sqlite3.Connection:
    """rank.db 를 열고 없으면 만든다. 스레드마다 따로 연다."""
    p = Path(path) if path else config.RANK_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return _prepare(sqlite3.connect(str(p), timeout=OPEN_TIMEOUT_S))


def _prepare(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    # 모든 연결이 secure_delete(R11) — 지우는 연결에서만 켜면 그 전 삽입·재배치로 빈 페이지에 남은 닉네임이 그대로다(store 와 같은 이유)
    conn.execute("PRAGMA secure_delete=ON")
    with _OPEN_LOCK:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    return conn


def open_rank_db_existing(path: Path | str | None = None) -> sqlite3.Connection | None:
    """있을 때만 쓰기로 연다 — 없으면 None(만들지 않는다). exists() 뒤 connect 가 아니라 mode=rw URI 라
    [수집 기록 지우기]가 그 사이 파일을 지워도 되살리지 않는다."""
    p = Path(path) if path else config.RANK_DB_PATH
    try:
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=rw", uri=True, timeout=OPEN_TIMEOUT_S)
    except (sqlite3.Error, ValueError, OSError):
        return None
    try:
        return _prepare(conn)
    except sqlite3.Error:
        conn.close()
        return None


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

def rank_gaps(rows: list[ranker.RankRow]) -> int:
    """1~마지막 순위 중 빈 순위 수(N14 — R4: 지금 0). 같은 순위 둘은 세지 않는다."""
    ranks = {r.rank for r in rows if r.rank is not None}
    return (max(ranks) - len(ranks)) if ranks else 0


def save_snapshot(conn: sqlite3.Connection, rows: list[ranker.RankRow], taken_at: datetime,
                  dup_count: int = 0, ended_season: int | None = None, ref_time: str | None = None) -> int:
    """원본 + 집계(+ snapshot_meta)를 한 트랜잭션으로 — 도중에 죽어도 반쪽이 안 남는다. → 스냅숏 id."""
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
        conn.executemany("INSERT INTO snapshot_emblems (snapshot_id, profile_sn, emblem) VALUES (?,?,?)",
                         [(sid, r.profile_sn, r.team_color_emblem) for r in rows
                          if r.team_color and r.team_color_emblem])
        conn.executemany("INSERT INTO tier_counts VALUES (?,?,?,?,?)", [(sid, *c) for c in counts])
        conn.executemany("INSERT INTO tier_values VALUES (?,?,?,?,?)", [(sid, *v) for v in values])
        conn.executemany("INSERT INTO cut_elo VALUES (?,?,?)", [(sid, *c) for c in cuts])
        conn.execute("INSERT INTO snapshot_meta (snapshot_id, ref_time, rank_gaps) VALUES (?, ?, ?)",
                     (sid, ref_time, rank_gaps(rows)))
    return sid


def prune_raw(conn: sqlite3.Connection, now: datetime) -> int:
    """RANK_RAW_KEEP_DAYS 지난 스냅숏의 원본만 지운다(집계는 남긴다) + 센 랭커 픽 경기 번호. → 지운 원본 행 수.
    지운 스냅숏엔 raw_pruned = 1 — secure_delete 로 지웠으니 VACUUM 이 필요 없다는 표시."""
    cutoff = _iso(now - timedelta(days=config.RANK_RAW_KEEP_DAYS))
    with conn:
        ids = [r[0] for r in conn.execute(
            "SELECT s.id FROM snapshots s WHERE s.taken_at < ? AND EXISTS"
            " (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id)", (cutoff,))]
        cur = conn.execute("DELETE FROM snapshot_rows WHERE snapshot_id IN "
                           "(SELECT id FROM snapshots WHERE taken_at < ?)", (cutoff,))
        # 엠블럼은 원본이 없는 스냅숏 것 전부 — 옛 버전(2.1.1 이하)이 원본만 지우고 남긴 줄까지. snapshots 는 영구라
        # 날짜 조건으로는 그 줄이 안 잡힌다
        conn.execute("DELETE FROM snapshot_emblems WHERE snapshot_id NOT IN (SELECT DISTINCT snapshot_id FROM snapshot_rows)")
        conn.executemany("INSERT INTO snapshot_meta (snapshot_id, raw_pruned) VALUES (?, 1)"
                         " ON CONFLICT(snapshot_id) DO UPDATE SET raw_pruned = 1", [(i,) for i in ids])
        n = cur.rowcount
        conn.execute("DELETE FROM pick_counted WHERE day < ?", (cutoff[:10],))
    return n


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


@dataclass
class RankTrend:
    """랭킹 추이(2.1.1 · P2) — 지금 시즌 스냅숏마다 한 값(하루에 여럿이면 그날 마지막). 영구 집계만 읽는다(원본 14일과 무관).
    shares 의 키 "" 는 팀컬러 안 씀 · 포메이션 모름 — 비율의 분모라 그대로 둔다."""
    taken: list[str] = field(default_factory=list)                       # 스냅숏 시각 ISO, 오래된 것부터
    cuts: dict[int, list[float | None]] = field(default_factory=dict)    # 컷 순위 → 값
    value_avg: dict[int, list[int | None]] = field(default_factory=dict)  # 구간 → 구단가치 평균
    value_median: dict[int, list[int | None]] = field(default_factory=dict)
    shares: dict[tuple[int, str], dict[str, list[float]]] = field(default_factory=dict)  # (구간, 종류) → 키 → 비율(%)


def rank_trend_series(conn: sqlite3.Connection | None) -> RankTrend:
    """rank.db 영구 집계(tier_counts·tier_values·cut_elo) → 지금 시즌의 스냅숏별 값. 없으면 빈 RankTrend."""
    out = RankTrend()
    if conn is None:
        return out
    snaps = conn.execute(
        "SELECT id, taken_at FROM snapshots WHERE season_seq ="
        " (SELECT season_seq FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1) ORDER BY taken_at, id").fetchall()
    by_day: dict[str, tuple[int, str]] = {}
    for sid, taken in snaps:
        by_day[taken[:10]] = (sid, taken)  # 같은 날이면 뒤(늦은) 것 — x 가 날짜라 같은 날 두 점은 겹친다
    picked = sorted(by_day.values(), key=lambda t: t[1])
    out.taken = [t for _sid, t in picked]
    pos = {sid: i for i, (sid, _t) in enumerate(picked)}
    n = len(picked)
    if not n:
        return out
    marks = ",".join("?" * n)
    ids = tuple(pos)
    for sid, rank, elo in conn.execute(f"SELECT snapshot_id, rank, elo FROM cut_elo WHERE snapshot_id IN ({marks})", ids):
        out.cuts.setdefault(rank, [None] * n)[pos[sid]] = elo
    for sid, tier, avg, med in conn.execute(
            f"SELECT snapshot_id, tier, value_avg, value_median FROM tier_values WHERE snapshot_id IN ({marks})", ids):
        out.value_avg.setdefault(tier, [None] * n)[pos[sid]] = avg
        out.value_median.setdefault(tier, [None] * n)[pos[sid]] = med
    counts: dict[tuple[int, str], dict[str, list[int]]] = {}
    for sid, tier, kind, key, cnt in conn.execute(
            f"SELECT snapshot_id, tier, kind, key, n FROM tier_counts WHERE snapshot_id IN ({marks})", ids):
        counts.setdefault((tier, kind), {}).setdefault(key, [0] * n)[pos[sid]] = cnt
    for tk, keys in counts.items():
        totals = [sum(v[i] for v in keys.values()) for i in range(n)]
        out.shares[tk] = {k: [v[i] * 100 / totals[i] if totals[i] else 0.0 for i in range(n)] for k, v in keys.items()}
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


def raw_snapshots(conn: sqlite3.Connection) -> list[dict]:
    """원본이 남은 스냅숏 — [{id, taken_at, season_seq}] 오래된 것부터(예측의 하루 걸음 표본)."""
    return [dict(r) for r in conn.execute(
        "SELECT s.id, s.taken_at, s.season_seq FROM snapshots s WHERE EXISTS"
        " (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id) ORDER BY s.taken_at, s.id")]


def snapshot_games(conn: sqlite3.Connection, snapshot_id: int) -> dict[int, tuple[float, int]]:
    """스냅숏 하나의 {프로필 번호: (ELO, 승+무+패)} — 승무패는 시즌 누적이라(1.3.1 실측) 이웃 차가 그 사이 경기 수."""
    return {r["profile_sn"]: (r["elo"], (r["win"] or 0) + (r["draw"] or 0) + (r["lose"] or 0))
            for r in conn.execute("SELECT profile_sn, elo, win, draw, lose FROM snapshot_rows"
                                  " WHERE snapshot_id = ? AND elo IS NOT NULL", (snapshot_id,))}


def season_cuts(conn: sqlite3.Connection) -> dict[int, dict[int, float]]:
    """받아 둔 지난 시즌 최종 컷 — {시즌 번호: {순위: ELO}}. 순위가 다 찬 시즌만."""
    out: dict[int, dict[int, float]] = {}
    for r in conn.execute("SELECT season_no, rank, elo FROM season_cuts"):
        out.setdefault(r["season_no"], {})[r["rank"]] = r["elo"]
    want = set(config.RANK_CUT_RANKS)
    return {no: c for no, c in out.items() if want <= set(c)}


def fetch_season_cuts(conn: sqlite3.Connection, ended_season: int | None, now: datetime,
                      fetch=None, cancel: threading.Event | None = None) -> int:
    """빠진 지난 시즌(최근 PREDICT_FETCH_SEASONS 개)의 최종 컷을 받는다 — 수집 회차 끝에서, 실패해도 회차는 성공.

    시즌 하나 = RANK_CUT_RANKS 쪽, 다 받았을 때만 한 트랜잭션으로 넣는다(반쯤 받은 시즌이 남지 않게 —
    다음 회차가 그 시즌을 다시 받는다). 쪽마다 cancel 을 본다. → 새로 채운 시즌 수."""
    if not ended_season:
        return 0
    fetch = fetch or ranker.fetch_season_cut
    have = season_cuts(conn)
    filled = 0
    for no in range(ended_season, max(ended_season - config.PREDICT_FETCH_SEASONS, 0), -1):
        if no in have:
            continue
        got = {}
        try:
            for rank in config.RANK_CUT_RANKS:
                if cancel is not None and cancel.is_set():
                    return filled
                elo = fetch(no, rank)
                if elo is None:
                    raise ranker.RankStructureError(f"{no}시즌 {rank}위 컷이 비었습니다")
                got[rank] = elo
        except ranker.RankerError:
            continue          # 그 시즌만 건너뛴다 — 없는 시즌(리다이렉트)·일시 실패는 다음 회차
        with conn:
            conn.executemany("INSERT OR REPLACE INTO season_cuts (season_no, rank, elo, fetched_at) VALUES (?, ?, ?, ?)",
                             [(no, r, e, _iso(now)) for r, e in got.items()])
        filled += 1
    return filled


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
                   last_result=kind, last_message="", same_since="")
        return out
    if kind == "same":
        # 넥슨 데이터가 안 바뀌었다(U3 — 점검 등) — 세지 않고, 처음 같다고 본 시각만 남긴다([정보] "멈춰 있습니다")
        _set_state(conn, last_result=kind, last_message=message, **({} if st.get("same_since") else
                                                                    {"same_since": _iso(now)}))
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
        _set_state(conn, same_since="")
        try:
            config.set_rank_collect(False)
        except OSError:
            pass  # .env 를 못 써도 collect_state 사본이 막는다
        out["disabled"] = True
    return out


def is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    """간격(실패 중이면 늘어난 대기)이 지났나.

    간격은 마지막 회차의 **정각**(_hour)부터 INTERVAL − 1시간(2.3.1 2회차 A) — 마지막 시각 + 24시간이면 시작 지터
    (+5~+50분)가 날마다 쌓여, 정해진 시간에만 켜는 PC 는 하루를 건너뛰었다(연속이 잘린다). 정각으로 내리면 지터가 안
    쌓이고, −1시간이 1시간 확인의 위상을 흡수해 시작은 앞으로만 움직인다. 데이터 시각(ref_time)은 안 쓴다 —
    0시대 회차의 기준 시각은 전날 23시라(R2) 상시 PC 를 하루 두 번에 가뒀다(3회차 A·B)."""
    st = get_state(conn)
    if st.get("enabled") == "0":
        return False
    retry = st.get("retry_at")
    if retry and now < datetime.fromisoformat(retry):
        return False
    last = last_snapshot_at(conn)
    return last is None or now >= _hour(last) + timedelta(hours=config.RANK_COLLECT_INTERVAL_H - 1)


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
    """넥슨 데이터 기준 시각이 쪽마다 다르다(없으면 응답 Date 가 두 시각에 걸쳤다) — 갱신이 끼어 버린다(실패로 안 센다)."""


class SameData(Exception):
    """1쪽의 기준 시각이 마지막 스냅숏과 같다(U3 — 점검 등으로 안 바뀜) — 나머지 쪽을 안 받는다(실패로 안 센다)."""


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


def lower_thread_priority() -> None:
    """부른 스레드의 우선순위를 낮춘다(실패는 조용히) — 색인 백필(app_main.SquadBackfillWorker)도 같은 핸들 형으로."""
    _low_priority()


def _hour_key(date_header: str):
    try:
        d = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    return d.date(), d.hour


def run_round(fetch=None, pages: int = ranker.RANK_PAGES, workers: int | None = None,
              cancel: threading.Event | None = None, progress=None, heartbeat=None
              ) -> tuple[list[ranker.RankRow], int]:
    """1..pages 쪽을 읽어 판정·겹침 제거까지 → (행, 버린 수). 실패는 예외(RankerError 계열·Straddle·Cancelled)."""
    return rows_from_pages(read_pages(fetch, pages, workers, cancel, progress, heartbeat), pages)


def read_pages(fetch=None, pages: int = ranker.RANK_PAGES, workers: int | None = None,
               cancel: threading.Event | None = None, progress=None, heartbeat=None, last_ref: str | None = None
               ) -> dict[int, ranker.RankPageResult]:
    """1쪽을 먼저 혼자 받고(기준 시각이 last_ref 와 같으면 SameData — 나머지 요청 0, U3) 나머지를 나눠 받는다.

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
        first = pool.submit(one, 1).result()    # 작업자에서 — 1쪽도 낮은 우선순위로
        if last_ref and first.ref_time == last_ref:
            raise SameData("넥슨 데이터가 지난 수집 뒤로 갱신되지 않았습니다 — 다음 시각에 다시")
        results[1] = first
        if progress:
            progress(1, pages)
        futs = [pool.submit(one, p) for p in range(2, pages + 1)]
        for done, fut in enumerate(as_completed(futs), 2):
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
    return results


def round_ref(results: dict[int, ranker.RankPageResult]) -> str | None:
    """회차의 넥슨 기준 시각 — 쪽 전부에 있고 하나일 때만(아니면 None)."""
    refs = {r.ref_time for r in results.values()}
    return next(iter(refs)) if len(refs) == 1 and None not in refs else None


def rows_from_pages(results: dict[int, ranker.RankPageResult], pages: int
                    ) -> tuple[list[ranker.RankRow], int]:
    """다 읽은 쪽들 → (행, 버린 수). 갱신 걸침은 Straddle, 구조 변경은 RankStructureError.

    걸침은 기준 시각이 **전부 있으면 그것끼리**(R3 — 데이터는 정각 + 36분 넘어 바뀌어, 응답 Date 의 시(時)로 가르면
    07:40~07:50 회차가 두 데이터를 섞어도 통과했다), 하나라도 없으면 지금처럼 Date 의 시로."""
    refs = [r.ref_time for r in results.values()]
    if refs and all(refs):
        if len(set(refs)) > 1:
            raise Straddle("수집 중에 넥슨 데이터가 갱신됐습니다 — 다음 시각에 다시")
    else:
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


def snapshot_emblem_map(conn: sqlite3.Connection, snapshot_id: int) -> dict[int, str]:
    """그 스냅숏의 프로필 번호 → 팀컬러 엠블럼 키(2.2.1). 옛 버전이 찍은 스냅숏이면 빈 dict."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT profile_sn, emblem FROM snapshot_emblems WHERE snapshot_id = ?", (snapshot_id,))}


def snapshot_colors(nicknames, db_path: Path | str | None = None, now: datetime | None = None
                    ) -> tuple[dict[str, tuple[str, int | None, str]], datetime] | None:
    """팀컬러를 스냅숏에서 — {닉네임: (팀컬러, 구단가치, 엠블럼 키)}, 스냅숏 시각. 목록에 없는 사람은 '랭킹 밖'("", None, "").
    팀컬러가 없는 행은 구단가치도 None(ranker._color_rows 와 같은 규칙). 쓸 스냅숏이 없으면 None."""
    snap = fresh_snapshot(db_path, now)
    if snap is None:
        return None
    sid, taken = snap
    want = set(nicknames)
    conn = open_rank_db(db_path)
    try:
        emblems = snapshot_emblem_map(conn, sid)
        found = {}
        for r in conn.execute("SELECT nickname, profile_sn, team_color, team_value FROM snapshot_rows WHERE snapshot_id = ?",
                              (sid,)):
            if r["nickname"] in want:
                color = r["team_color"] or ""
                found[r["nickname"]] = (color, r["team_value"] if color else None,
                                        emblems.get(r["profile_sn"], "") if color else "")
    finally:
        conn.close()
    for n in want - found.keys():
        found[n] = ("", None, "")
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
    """[정보] 창 표시용 — 마지막 성공·실패 횟수·끈 이유·스냅숏 수 + 메타(meta_status). rank.db 가 없으면 빈 dict.
    화면 스레드가 부른다 — 읽기 전용으로 연다(만들지도, 스키마를 쓰지도 않는다)."""
    conn = open_rank_db_ro(db_path)
    if conn is None:
        return {}
    try:
        st = get_state(conn)
        st["snapshots"] = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        last = conn.execute("SELECT id, taken_at, row_count FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1"
                            ).fetchone()
        if last is not None:
            st["last_taken_at"], st["last_rows"] = last["taken_at"], last["row_count"]
        st.update(meta_status(conn))
        return st
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def sync_enabled_copy(on: bool, reason: str = "", db_path: Path | str | None = None) -> None:
    """토글의 rank.db 쪽(켜짐 사본) — 작업 스레드에서(4회차 A: 화면 스레드가 쓰면 메타 처리 뒤에서 15초 굳었다).
    끌 때 rank.db 가 없으면 만들지 않는다."""
    p = Path(db_path) if db_path else config.RANK_DB_PATH
    if not on and not p.exists():
        return
    conn = open_rank_db(p)
    try:
        set_enabled(conn, on, reason)
    finally:
        conn.close()


def set_enabled_at(on: bool, reason: str = "", db_path: Path | str | None = None) -> None:
    """토글 — .env 와 rank.db 사본을 같이(사본이 D6 끔을 붙잡고 있어 .env 만 켜면 계속 disabled 다).
    앱은 .env 는 화면 스레드에서, 사본은 작업 스레드에서 따로(sync_enabled_copy) — 이건 한 번에 하는 터미널·테스트용."""
    config.set_rank_collect(on)
    sync_enabled_copy(on, reason, db_path)


@dataclass
class Outcome:
    kind: str                 # ok · failed · blocked · offline · straddle · same · cancelled · locked · disabled · fresh
    message: str = ""
    snapshot_id: int | None = None
    rows: int = 0
    disabled_by_block: bool = False
    fail_notice: bool = False


def collect(*, db_path: Path | str | None = None, now_fn=datetime.now, fetch=None,
            cancel: threading.Event | None = None, progress=None, ended_season: int | None = None,
            pages: int = ranker.RANK_PAGES, season_ends: list | None = None) -> Outcome:
    """한 회차 수집 — 스위치 확인 → 잠금 → 읽기 → 저장 → 상태 반영. 예약(언제 부를지)은 부르는 쪽.
    season_ends: 사람별 처리의 시즌표(끝난 시즌 종료일들) — None 이면 fifa.db 캐시(낡았으면 다시 받는다)."""
    web, on = config.read_env_switches()
    if config.notice_needed() or not web or not on:
        return Outcome("disabled", "랭킹 수집이 꺼져 있습니다(넥슨 홈페이지 데이터가 꺼져 있으면 잠깁니다)")
    got, waited = acquire_list_read(lambda: cancel is not None and cancel.is_set())
    if not got:
        return Outcome("cancelled")
    try:
        return _collect_locked(db_path, now_fn, fetch, cancel, progress, ended_season, pages, waited, season_ends)
    finally:
        release_list_read()


def _after_save(conn, taken: datetime, lock: CollectLock, cancel=None, season_ends=None) -> None:
    """스냅숏 커밋 뒤 — 따라가기 ELO → 메타 처리 → 원본 정리 → WAL 비우기. 순서가 뜻이다: 정리를 먼저 하면 미처리
    원본이 틈이 되고(1회차 A), 따라가기는 지울 원본도 한 번 더 옮길 기회다. 메타 실패는 회차 성공과 무관(다음에 이어서)."""
    sync_tracked_elo(conn, cancel=cancel)
    try:
        process_meta(conn, taken, season_ends=season_ends, cancel=cancel, heartbeat=lock.heartbeat)
    except sqlite3.Error:
        pass
    prune_raw(conn, taken)
    checkpoint(conn)


def _collect_locked(db_path, now_fn, fetch, cancel, progress, ended_season, pages, waited, season_ends=None) -> Outcome:
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
                results = read_pages(fetch, pages, cancel=cancel, progress=progress, heartbeat=lock.heartbeat,
                                     last_ref=last_ref_time(conn))
                rows, dups = rows_from_pages(results, pages)
                out.snapshot_id = save_snapshot(conn, rows, taken, dups, ended_season, round_ref(results))
                out.rows = len(rows)
                _after_save(conn, taken, lock, cancel, season_ends)
                try:
                    fetch_season_cuts(conn, ended_season, taken, cancel=cancel)
                except (sqlite3.Error, ranker.RankerError):
                    pass   # 예측용 지난 시즌 컷 — 회차 성공과 무관, 다음 회차가 빠진 시즌만 다시 받는다
            except Cancelled:
                out = Outcome("cancelled")
            except SameData as e:
                out = Outcome("same", str(e))
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
                    db_path: Path | str | None = None, now_fn=datetime.now,
                    season_ends: list | None = None) -> Outcome | None:
    """팀컬러 때문에 500쪽을 다 읽었으면 그걸 스냅숏으로 — 수집이 켜져 있고 간격이 지났을 때만. 목록 읽기 차례
    (acquire_list_read)를 쥔 채 부른다. 저장 안 했으면 None. 쪽 판정·갱신 걸침·안 바뀐 데이터는 collect 와 같은 규칙."""
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
            ref = round_ref(results)
            if ref is not None and ref == last_ref_time(conn):
                return None                      # 안 바뀐 데이터(U3) — 세지 않는다
            try:
                rows, dups = rows_from_pages(results, pages)
            except Straddle:
                return None                      # 세지 않는다 — 다음 확인에 수집이 다시 읽는다
            except ranker.RankerError as e:
                out = Outcome("failed", str(e))
                flags = record_result(conn, "failed", now_fn(), out.message)
                out.disabled_by_block, out.fail_notice = flags["disabled"], flags["fail_notice"]
                return out
            out = Outcome("ok", snapshot_id=save_snapshot(conn, rows, taken, dups, ended_season, ref), rows=len(rows))
            _after_save(conn, taken, lock, season_ends=season_ends)
            record_result(conn, "ok", now_fn())
            return out
        finally:
            lock.release()
    finally:
        conn.close()


# ── 랭커 메타(2.3.1) — 데이터 시각 · 따라잡기 처리 · 사람별 표 · 지우기 ──────────────────────────────
# 처리 표시는 snapshot_meta 의 깃발 둘(anon_done · person_done) 한 곳 — 그 처리와 같은 트랜잭션에서 올린다(두 번 세지 않음).
# 사람별 표는 그 시즌 동안만이고(U1), 수집을 끄거나 동의 전이면 지운다 — 지우기 조건은 표시가 아니라 상태로 판정한다
# (purge_person_if_needed — 화면 스레드는 rank.db 에 쓰지 않는다). 규칙의 근거는 ROADMAP 2.3.1 각 절.

def is_champ(grade) -> bool:
    """챔스 이상 — 지금 등급 칸(grade) 번호가 CHAMP_GRADE_MAX 이하. 등급 칸이 셋(grade·best_grade·prev_grade)이라
    판정을 한 곳에 둔다(2.4.1). rankmeta 가 rankcollect 를 가져다 쓰므로 여기 둔다(거꾸로면 순환 import)."""
    return grade is not None and grade <= config.CHAMP_GRADE_MAX


def data_time(row) -> str:
    """스냅숏의 데이터 시각 — 넥슨 기준 시각(ref_time), 없으면(옛 버전이 쓴 스냅숏) taken_at. 이번에 새로 만든 읽기는
    전부 이걸 쓴다. elo_history 는 그대로 taken_at(R15 — 바꾸면 같은 스냅숏이 다른 시각으로 다시 들어가 ux_elo_src 가 못 막는다)."""
    try:
        ref = row["ref_time"]
    except (KeyError, IndexError):
        ref = None
    return ref or row["taken_at"]


def last_ref_time(conn: sqlite3.Connection) -> str | None:
    """마지막 스냅숏의 넥슨 기준 시각 — 없으면 None(옛 버전이 썼거나 페이지에 없었다)."""
    row = conn.execute("SELECT m.ref_time FROM snapshots s LEFT JOIN snapshot_meta m ON m.snapshot_id = s.id"
                       " ORDER BY s.taken_at DESC, s.id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _put_state(conn: sqlite3.Connection, **kv) -> None:
    """부르는 쪽 트랜잭션 안에서(_set_state 는 스스로 커밋한다)."""
    conn.executemany("INSERT INTO collect_state (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     [(k, None if v is None else str(v)) for k, v in kv.items()])


def _json_state(st: dict, key: str):
    try:
        return json.loads(st.get(key) or "null")
    except ValueError:
        return None


def _hours(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def _day_after(at: str) -> str:
    return (datetime.fromisoformat(at).date() + timedelta(days=1)).isoformat()


# 시즌표 — fifa.db 캐시. 테스트가 바꿔 끼운다(네트워크 없이)
def _fetch_seasons():
    import seasons
    return seasons.fetch_seasons()


def load_season_ends(fifa_path: Path | str | None = None, refresh: bool = True) -> list[str]:
    """끝난 시즌 종료일(ISO 날짜, 오름차순) — fifa.db 시즌표 캐시. 캐시가 SEASON_TTL 보다 낡았으면 먼저 다시 받는다
    (3회차 A·B — 창을 만들 때 한 번만 읽던 시즌표가 트레이 상주면 며칠 낡아 새 시즌 값이 옛 줄에 이어 쓰였다).
    작업 스레드에서만. 실패하면 캐시 그대로, 캐시도 없으면 [] (사람별 시즌은 season_seq 신호로만)."""
    try:
        fconn = store.open_db(fifa_path or config.DB_PATH)
    except sqlite3.Error:
        return []
    try:
        if refresh and config.WEB_DATA and store.seasons_stale(fconn):
            try:
                store.save_seasons(fconn, _fetch_seasons())
            except Exception:
                pass
        return sorted({s.end.isoformat() for s in store.load_seasons(fconn)})
    except sqlite3.Error:
        return []
    finally:
        fconn.close()


def season_start_of(ends: list, when: str) -> str | None:
    """그 시각이 든 시즌의 시작일 — 앞 시즌 종료일 == 다음 시즌 시작일(seasons.py). 모르면 None."""
    d = when[:10]
    starts = [str(e)[:10] for e in ends or () if str(e)[:10] <= d]
    return max(starts) if starts else None


def _pending(conn: sqlite3.Connection, person: bool) -> list[dict]:
    """처리할 스냅숏 — 원본이 남았고 깃발이 0 이거나 meta 줄이 없는 것, 데이터 시각 순. 동의 전엔 person 대상을 안 고른다."""
    cond = "m.snapshot_id IS NULL OR m.anon_done = 0" + (" OR m.person_done = 0" if person else "")
    out = [dict(r) for r in conn.execute(
        "SELECT s.id, s.taken_at, s.season_seq, m.ref_time, m.snapshot_id AS has_meta, m.anon_done, m.person_done"
        " FROM snapshots s LEFT JOIN snapshot_meta m ON m.snapshot_id = s.id"
        f" WHERE ({cond}) AND EXISTS (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id)")]
    for r in out:
        r["dt"] = data_time(r)
    out.sort(key=lambda r: (r["dt"], r["id"]))
    return out


def _anon_one(conn: sqlite3.Connection, s: dict) -> None:
    """익명 집계 — 점수 분포(B5). 옛 버전이 쓴 스냅숏이면 meta 줄도 이때(ref_time NULL)."""
    bins: Counter = Counter()
    ranks = set()
    for rank, elo in conn.execute("SELECT rank, elo FROM snapshot_rows WHERE snapshot_id = ?", (s["id"],)):
        ranks.add(rank)
        if elo is not None:
            bins[int(elo // config.RANK_ELO_BIN) * config.RANK_ELO_BIN] += 1
    gaps = (max(ranks) - len(ranks)) if ranks else 0
    with conn:
        conn.execute("INSERT OR IGNORE INTO snapshot_meta (snapshot_id, rank_gaps) VALUES (?, ?)", (s["id"], gaps))
        conn.execute("DELETE FROM elo_hist WHERE snapshot_id = ?", (s["id"],))
        conn.executemany("INSERT INTO elo_hist (snapshot_id, bin, n) VALUES (?, ?, ?)",
                         [(s["id"], b, n) for b, n in sorted(bins.items())])
        conn.execute("UPDATE snapshot_meta SET anon_done = 1 WHERE snapshot_id = ?", (s["id"],))


def _mark_person(conn: sqlite3.Connection, sid: int) -> None:
    conn.execute("INSERT INTO snapshot_meta (snapshot_id, person_done) VALUES (?, 1)"
                 " ON CONFLICT(snapshot_id) DO UPDATE SET person_done = 1", (sid,))


def _season_verdict(ps: dict, seq: int, start: str | None) -> tuple[str, str]:
    """사람별 표의 시즌 — 신호 둘(season_seq · 시즌표 시작일)을 **같은 출처끼리 숫자·날짜로**(3회차 A·B: 섞어 문자열로
    비교하면 시즌표를 처음 받는 날 전부 '더 옛것'이 됐다). → (판정, 넘긴 신호).
    판정: same · older(건너뜀) · roll(넘김) · update(같은 넘김의 늦은 신호 — 값만 받아 적는다)."""
    pseq, pstart = int(ps.get("seq") or 0), ps.get("start")
    both = bool(start and pstart)
    if seq < pseq or (both and start < pstart):
        return "older", ""
    seq_up, start_up = seq > pseq, both and start > pstart
    if seq_up and start_up:
        return "roll", "both"
    if seq_up:
        if ps.get("via") == "start" and both and start == pstart:
            return "update", ""        # 시즌표로 먼저 넘겼다 — 다음 저장의 ended_season 이 seq 를 따라 올렸다
        return "roll", "seq"
    if start_up:
        if ps.get("via") == "seq" and start <= _day_after(ps["at"]):
            return "update", ""        # seq 로 먼저 넘겼다 — 시즌표가 늦게 따라왔다(4회차 A: 고정 7일은 8일 넘게 늦으면 틀렸다)
        return "roll", "start"
    if start and not pstart:
        return "update", ""            # 한쪽 시작일이 없으면 seq 로만 판정 — 이제 알게 된 시작일만 적는다
    return "same", ""


def _roll(conn: sqlite3.Connection) -> None:
    """시즌 넘김 — 옛 시즌 사람별 줄을 번호 없는 줄로 옮기고 지운다(부르는 쪽 트랜잭션 안). 열린 연속은 잘림으로 닫는다."""
    conn.execute("INSERT INTO elo_season_done (season_at, peak, max_dd, last_elo)"
                 " SELECT season_at, peak, max_dd, last_elo FROM elo_season")
    conn.execute("INSERT INTO run_done (season_at, hours, censored_start, censored_end)"
                 " SELECT season_at, (julianday(last_at) - julianday(start_at)) * 24, censored_start, 1 FROM run_open")
    for t in PERSON_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM collect_state WHERE key = 'person_prev'")


def _late(first_at: str, start: str | None) -> int:
    """N12 — 시즌 시작일보다 N12_START_DAYS 넘게 뒤에 처음 봤나(1회차 A: 앱이 본 첫 스냅숏과 비교하면 시즌 중간에 켠
    사람의 첫날이 '온전'이 됐다). 시즌표를 모르면 1."""
    if not start:
        return 1
    return int(datetime.fromisoformat(first_at) > datetime.fromisoformat(start) + timedelta(days=config.N12_START_DAYS))


def _person_apply(conn: sqlite3.Connection, sid: int, t: str, ps: dict, prev: dict | None) -> None:
    """스냅숏 하나를 사람별 표에 — N3 슈챔 연속 · N11 최고점·낙폭 · N12 첫 챔스(부르는 쪽 트랜잭션 안).
    연속은 날짜가 아니라 경과 시간(1회차 A — 간격 24시간 + 지터라 매일 켜도 달력 날짜가 빈다)."""
    season_at = ps["at"]
    rows = conn.execute("SELECT profile_sn, grade, elo, win, draw, lose, team_value FROM snapshot_rows"
                        " WHERE snapshot_id = ?", (sid,)).fetchall()
    prev_grade = None
    if prev is not None and 0 < _hours(prev["at"], t) <= config.RANK_CONT_MAX_H:
        prev_grade = {r[0]: r[1] for r in conn.execute(
            "SELECT profile_sn, grade FROM snapshot_rows WHERE snapshot_id = ?", (prev["sid"],))} or None
    cont = prev_grade is not None       # 앞 원본이 없으면(옛 버전만 14일 넘게 돈 사이 지워짐) 틈
    # N3 — 등급 0(슈퍼챔피언스, R5) 연속
    open_ = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT profile_sn, start_at, last_at, censored_start FROM run_open WHERE season_at = ?", (season_at,))}
    cur0 = {r["profile_sn"] for r in rows if r["grade"] == 0}
    closes = [(sn, a, b, cs) for sn, (a, b, cs) in open_.items() if not cont or sn not in cur0]
    conn.executemany("INSERT INTO run_done (season_at, hours, censored_start, censored_end) VALUES (?, ?, ?, ?)",
                     [(season_at, _hours(a, b), cs, 0 if cont else 1) for _sn, a, b, cs in closes])
    conn.executemany("DELETE FROM run_open WHERE season_at = ? AND profile_sn = ?", [(season_at, c[0]) for c in closes])
    conn.executemany("UPDATE run_open SET last_at = ? WHERE season_at = ? AND profile_sn = ?",
                     [(t, season_at, sn) for sn in cur0 if cont and sn in open_])
    # 앞에서도 0 이었는데 열린 줄이 없으면(어긋난 상태) 시작을 모른다 — 잘림(2회차 A: 조용히 빠졌다)
    conn.executemany("INSERT OR REPLACE INTO run_open (season_at, profile_sn, start_at, last_at, censored_start)"
                     " VALUES (?, ?, ?, ?, ?)",
                     [(season_at, sn, t, t, 1 if (not cont or prev_grade.get(sn) == 0) else 0)
                      for sn in cur0 if not (cont and sn in open_)])
    # N11 — 1만 위 안에서 본 값만(밖 구간은 안 보여 낙폭이 실제보다 얕다). SET 의 오른쪽은 전부 옛 값을 본다
    conn.executemany(
        "INSERT INTO elo_season (season_at, profile_sn, peak, peak_at, max_dd, last_elo, last_at) VALUES (?, ?, ?, ?, 0, ?, ?)"
        " ON CONFLICT(season_at, profile_sn) DO UPDATE SET"
        " peak_at = CASE WHEN excluded.peak > peak THEN excluded.peak_at ELSE peak_at END,"
        " max_dd = MAX(max_dd, MAX(peak, excluded.peak) - excluded.last_elo),"
        " peak = MAX(peak, excluded.peak), last_elo = excluded.last_elo, last_at = excluded.last_at",
        [(season_at, r["profile_sn"], r["elo"], t, r["elo"], t) for r in rows if r["elo"] is not None])
    # N12 — 챔스(등급 ≤ 1) 처음 닿은 판수. 상태: 0 처음 볼 때 이미 챔스 · 1 보는 중 · 2 기록함 · 3 틈(제외)
    watch = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT profile_sn, first_at, last_at, state FROM champ_watch WHERE season_at = ?", (season_at,))}
    firsts, upd = [], []
    for r in rows:
        sn = r["profile_sn"]
        champ = is_champ(r["grade"])
        w = watch.get(sn)
        if w is None:
            upd.append((season_at, sn, t, t, 0 if champ else 1))
            continue
        first_at, last_at, state = w
        if state == 1:
            if not cont or last_at != prev["at"]:
                state = 3                  # 틈이거나, 그 사이 목록에서 빠졌다 들어왔다 — 처음 닿은 때를 모른다
            elif champ:
                games = (r["win"] or 0) + (r["draw"] or 0) + (r["lose"] or 0)
                firsts.append((season_at, games, r["team_value"], t, _late(first_at, ps.get("start"))))
                state = 2
        upd.append((season_at, sn, first_at, t, state))
    conn.executemany("INSERT INTO champ_first (season_at, games, team_value, at, first_seen_late) VALUES (?, ?, ?, ?, ?)",
                     firsts)
    conn.executemany("INSERT OR REPLACE INTO champ_watch (season_at, profile_sn, first_at, last_at, state)"
                     " VALUES (?, ?, ?, ?, ?)", upd)


def _person_one(conn: sqlite3.Connection, s: dict, ends: list, on_at: str | None) -> str:
    """스냅숏 하나의 사람별 처리 — 시즌 판정 → (넘김) → 적용 → 상태·깃발, 전부 한 트랜잭션. → 판정."""
    sid, t = s["id"], s["dt"]
    start = season_start_of(ends, t)
    st = get_state(conn)
    ps, prev = _json_state(st, "person_season"), _json_state(st, "person_prev")
    with conn:
        if prev and t <= prev["at"]:
            verdict, via = "older", ""   # 데이터 시각이 앞에 처리한 것보다 앞섰다(2회차 A — 경과 시간이 음수가 됐다)
        elif ps is None:
            verdict, via = "init", "init"
        else:
            verdict, via = _season_verdict(ps, int(s["season_seq"]), start)
        if verdict == "older":
            _mark_person(conn, sid)
            return verdict
        if verdict in ("init", "roll"):
            if verdict == "roll":
                _roll(conn)
            ps, prev = {"seq": int(s["season_seq"]), "start": start, "at": t, "via": via}, None
        elif verdict == "update":
            ps = {**ps, "seq": max(int(ps.get("seq") or 0), int(s["season_seq"])),
                  "start": start or ps.get("start"), "via": "both"}
        _person_apply(conn, sid, t, ps, prev)
        _put_state(conn, person_season=json.dumps(ps), person_prev=json.dumps({"sid": sid, "at": t}),
                   person_epoch=on_at or "")
        _mark_person(conn, sid)
    return verdict


def _roll_if_due(conn: sqlite3.Connection, now: datetime, ends: list) -> bool:
    """새 스냅숏 없이 시즌을 넘겨야 하나(앱 켤 때 · 회차 끝) — 시즌 뒤 앱만 켜고 수집이 실패하는 경우.
    시즌표가 없으면 앞 처리가 ELO_FALLBACK_DAYS 넘게 지났을 때(seq 는 스냅숏이 있으면 늘 있어 '둘 다 없음'은 없다). → 넘겼나."""
    st = get_state(conn)
    ps = _json_state(st, "person_season")
    if ps is None:
        return False
    now_iso = _iso(now)
    start = season_start_of(ends, now_iso)
    with conn:
        if start and ps.get("start") and start > ps["start"]:
            if ps.get("via") == "seq" and start <= _day_after(ps["at"]):
                _put_state(conn, person_season=json.dumps({**ps, "start": start, "via": "both"}))
                return False
            _roll(conn)
            _put_state(conn, person_season=json.dumps({"seq": ps.get("seq"), "start": start, "at": now_iso,
                                                       "via": "start"}))
            return True
        if not ends:
            prev = _json_state(st, "person_prev")
            if prev and now - datetime.fromisoformat(prev["at"]) > timedelta(days=config.ELO_FALLBACK_DAYS):
                _roll(conn)
                _put_state(conn, person_season=json.dumps({"seq": ps.get("seq"), "start": None, "at": now_iso,
                                                           "via": "fallback"}))
                return True
    return False


def _has_person(conn: sqlite3.Connection, st: dict) -> bool:
    if any(st.get(k) for k in PERSON_STATE):
        return True
    for t in PERSON_TABLES:     # 생성식 안에서 부르면 SQL 계획 검사(test_rules)가 이 함수로 못 센다
        if conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone():
            return True
    return False


def purge_person_if_needed(conn: sqlite3.Connection) -> bool:
    """사람별 표 지우기 — 표시가 아니라 **상태로** 판정한다(3회차: 화면 스레드가 표시를 쓰면 굳고, 못 쓰면 다시 할 길이
    없었다). ① 꺼짐·동의 전(디스크에서 다시)인데 사람별 줄이 있다 ② 수집을 켠 시각(.env)이 사람별 처리 때와 다르다
    (껐다 켠 사이를 이어진 것으로 읽지 않게 — 2회차 B). 둘 다 없음 = 같음(옛 버전에서 켠 업그레이드 사용자).
    수집 잠금(CollectLock) 안에서 부른다. 꺼져 있으면 '멈춰 있음'(same_since)도 지운다. → 지운 게 있었나."""
    _web, on = config.read_env_switches()
    allowed = config.read_person_allowed()
    on_at = config.read_rank_on_at()
    st = get_state(conn)
    has = _has_person(conn, st)
    if not on and st.get("same_since"):
        _set_state(conn, same_since="")
    if not ((not allowed and has) or (allowed and (st.get("person_epoch") or None) != on_at)):
        return False
    with conn:
        for t in PERSON_TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.execute(f"DELETE FROM collect_state WHERE key IN ({','.join('?' * len(PERSON_STATE))})", PERSON_STATE)
        if allowed:
            _put_state(conn, person_epoch=on_at or "")
    return has


def process_meta(conn: sqlite3.Connection, now: datetime, *, season_ends: list | None = None,
                 fifa_path: Path | str | None = None, cancel: threading.Event | None = None, heartbeat=None) -> dict:
    """따라잡기 처리 — 지우기 조건 → 익명 집계(B5, 동의 무관) → 사람별(N3·N11·N12, 동의 뒤) → 스냅숏 없는 시즌 넘김.
    스냅숏 하나 = 한 트랜잭션(깃발 같이). 수집 잠금 안에서 부른다(저장 커밋 뒤 · 앱 켤 때 작업 스레드).
    사람별 직전마다 켜짐·동의를 디스크에서 다시 읽는다 — 처리 중에 끄면 거기서 멈춘다(1회차 A·B)."""
    out = {"purged": purge_person_if_needed(conn), "anon": 0, "person": 0, "rolled": False}

    def stopped():
        return cancel is not None and cancel.is_set()

    allowed = config.read_person_allowed()
    snaps = _pending(conn, person=allowed)
    for s in snaps:
        if stopped():
            return out
        if not s["has_meta"] or not s["anon_done"]:
            _anon_one(conn, s)
            out["anon"] += 1
            if heartbeat:
                heartbeat()
    if not allowed:
        return out
    ends = season_ends if season_ends is not None else load_season_ends(fifa_path)
    on_at = config.read_rank_on_at()
    for s in snaps:
        if s.get("person_done"):
            continue
        if stopped() or not config.read_person_allowed():
            return out
        _person_one(conn, s, ends, on_at)
        out["person"] += 1
        if heartbeat:
            heartbeat()
    out["rolled"] = _roll_if_due(conn, now, ends)
    return out


def checkpoint(conn: sqlite3.Connection) -> bool:
    """WAL 비우기(TRUNCATE) — 지운 줄이 -wal 프레임에 남지 않게(1회차 B). 화면의 읽기 연결 등으로 막히면(busy)
    다음 1시간 확인에 다시(ckpt_pending · 이어진 실패 수 ckpt_fail). → 비웠나."""
    try:
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    except sqlite3.Error:
        busy = 1
    st = get_state(conn)
    want = {"ckpt_pending": "1" if busy else "0", "ckpt_fail": str(_int(st, "ckpt_fail") + 1 if busy else 0)}
    if any((st.get(k) or "0") != v for k, v in want.items()):
        _set_state(conn, **want)
    return not busy


def vacuum_if_needed(conn: sqlite3.Connection, now: datetime) -> bool:
    """secure_delete 없이 지운 흔적이 있을 때만 VACUUM — 원본이 없는데 raw_pruned 가 아닌 스냅숏(업그레이드 전 ·
    옛 버전으로 되돌렸던 사이에 지워진 것). freelist 로 재면 새 prune_raw 뒤 늘 참이라 켤 때마다 전체 재작성이 된다(2회차).
    실패(잠김)는 하루 한 번만 다시. → 했나."""
    ids = [r[0] for r in conn.execute(
        "SELECT s.id FROM snapshots s LEFT JOIN snapshot_meta m ON m.snapshot_id = s.id"
        " WHERE COALESCE(m.raw_pruned, 0) = 0 AND NOT EXISTS (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id)")]
    if not ids:
        return False
    today = now.date().isoformat()
    if get_state(conn).get("vacuum_tried_on") == today:
        return False
    try:
        conn.commit()
        conn.execute("VACUUM")
    except sqlite3.Error:
        _set_state(conn, vacuum_tried_on=today)
        return False
    with conn:
        conn.executemany("INSERT INTO snapshot_meta (snapshot_id, raw_pruned) VALUES (?, 1)"
                         " ON CONFLICT(snapshot_id) DO UPDATE SET raw_pruned = 1", [(i,) for i in ids])
    return True


def maintenance(db_path: Path | str | None = None, now_fn=datetime.now, *, vacuum: bool = False,
                season_ends: list | None = None, cancel: threading.Event | None = None) -> dict:
    """작업 스레드의 정리 한 번 — 앱 켤 때(vacuum=True) · 1시간 확인 · 끄는 길 · 동의 직후. 수집 잠금 안에서
    지우기 조건 → 메타 처리 → 원본 정리(수집이 꺼져 있어도 — R12) → (VACUUM) → WAL 비우기.
    rank.db 가 없으면 아무것도 안 한다(만들지 않는다). 잠금을 못 잡으면 {"locked": True} — 다음 확인이 같은 조건을 다시 본다."""
    conn = open_rank_db_existing(db_path)
    if conn is None:
        return {"missing": True}
    try:
        lock = CollectLock(conn, now_fn)
        try:
            if not lock.acquire():
                return {"locked": True}
        except sqlite3.Error:
            return {"locked": True}
        try:
            now = now_fn()
            out = process_meta(conn, now, season_ends=season_ends, cancel=cancel, heartbeat=lock.heartbeat)
            out["pruned"] = prune_raw(conn, now)
            if vacuum:
                out["vacuumed"] = vacuum_if_needed(conn, now)
            out["checkpoint"] = checkpoint(conn)
            return out
        finally:
            lock.release()
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ── 랭커 메타 읽기(화면 — 읽기 전용 연결) ─────────────────────────────────────

def _season_snaps(conn: sqlite3.Connection) -> list[dict]:
    """지금 시즌(마지막 season_seq) 스냅숏 — 데이터 시각 순."""
    out = [dict(r) for r in conn.execute(
        "SELECT s.id, s.taken_at, s.season_seq, m.ref_time FROM snapshots s LEFT JOIN snapshot_meta m"
        " ON m.snapshot_id = s.id WHERE s.season_seq ="
        " (SELECT season_seq FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1)")]
    for r in out:
        r["dt"] = data_time(r)
    out.sort(key=lambda r: (r["dt"], r["id"]))
    return out


def _nearest(snaps: list[dict], target: datetime, within: timedelta) -> dict | None:
    best = min(snaps, key=lambda s: abs(datetime.fromisoformat(s["dt"]) - target), default=None)
    if best is None or abs(datetime.fromisoformat(best["dt"]) - target) > within:
        return None
    return best


@dataclass
class MetaRow:
    key: str                # "" = 팀컬러 안 씀 · 포메이션 모름
    now_n: int
    now_pct: float
    then_n: int
    then_pct: float
    diff_pp: float
    thin: bool              # 두 시점 다 META_MIN_USERS 명 미만 — 흐림


@dataclass
class MetaTrend:
    now_at: str | None = None
    then_at: str | None = None
    days: float | None = None            # 실제로 비교한 일수(제목 "N일 전 대비")
    rows: dict = field(default_factory=dict)   # "team_color" · "formation" → [MetaRow] 변화 내림차순
    collecting: bool = True              # 비교할 스냅숏이 아직 없다("모으는 중")


def meta_trend(conn: sqlite3.Connection | None, tier: int, days: int = 7) -> MetaTrend:
    """B1 — 팀컬러·포메이션 비율의 변화. 기준 = 마지막 스냅숏, 비교 = 같은 시즌에서 데이터 시각이 days 일 전에 가장
    가까운 것(±1일). 없으면 그 시즌 가장 오래된 것이 3일 이상 전이면 그것(실제 일수를 제목에), 3일도 안 되면 표 없음.
    영구 집계(tier_counts)만 읽는다."""
    out = MetaTrend()
    if conn is None:
        return out
    snaps = _season_snaps(conn)
    if len(snaps) < 2:
        return out
    base = snaps[-1]
    base_t = datetime.fromisoformat(base["dt"])
    older = snaps[:-1]
    then = _nearest(older, base_t - timedelta(days=days), timedelta(days=1))
    if then is None and base_t - datetime.fromisoformat(older[0]["dt"]) >= timedelta(days=3):
        then = older[0]
    if then is None:
        return out
    out.collecting = False
    out.now_at, out.then_at = base["dt"], then["dt"]
    out.days = (base_t - datetime.fromisoformat(then["dt"])).total_seconds() / 86400
    counts: dict[tuple[int, str], dict[str, int]] = {}
    for sid, kind, key, n in conn.execute(
            "SELECT snapshot_id, kind, key, n FROM tier_counts WHERE snapshot_id IN (?, ?) AND tier = ?",
            (base["id"], then["id"], tier)):
        counts.setdefault((sid, kind), {})[key] = n
    for kind in ("team_color", "formation"):
        a, b = counts.get((base["id"], kind), {}), counts.get((then["id"], kind), {})
        ta, tb = sum(a.values()), sum(b.values())
        rows = []
        for key in set(a) | set(b):
            na, nb = a.get(key, 0), b.get(key, 0)
            pa = na * 100 / ta if ta else 0.0
            pb = nb * 100 / tb if tb else 0.0
            rows.append(MetaRow(key, na, pa, nb, pb, pa - pb,
                                na < config.META_MIN_USERS and nb < config.META_MIN_USERS))
        rows.sort(key=lambda r: (-r.diff_pp, r.key))
        out.rows[kind] = rows
    return out


@dataclass
class EloHist:
    now_at: str | None = None
    bins: dict = field(default_factory=dict)       # 칸 왼쪽 끝 ELO → 인원(마지막 스냅숏)
    cmp_at: str | None = None
    cmp: dict = field(default_factory=dict)        # 비교 스냅숏 — 없으면 {}
    cuts: dict = field(default_factory=dict)       # 순위 → ELO (200 · 1,000 — 막대 스냅숏)
    season_first_at: str | None = None             # 지금 시즌 첫 스냅숏 데이터 시각(검색 ELO 선을 고를 때)
    bin_width: int = config.RANK_ELO_BIN


def elo_hist_series(conn: sqlite3.Connection | None, compare: str = "7d") -> EloHist:
    """B5 — 마지막 스냅숏의 점수 분포 + 비교 선 하나(compare: "7d" · "season"(시즌 첫 스냅숏) · "none")."""
    out = EloHist()
    if conn is None:
        return out
    have = {r[0] for r in conn.execute("SELECT DISTINCT snapshot_id FROM elo_hist")}
    snaps = [s for s in _season_snaps(conn) if s["id"] in have]
    if not snaps:
        return out
    base = snaps[-1]
    out.now_at, out.season_first_at = base["dt"], snaps[0]["dt"]
    cmp = None
    if compare == "7d" and len(snaps) > 1:
        cmp = _nearest(snaps[:-1], datetime.fromisoformat(base["dt"]) - timedelta(days=7), timedelta(days=1))
    elif compare == "season" and len(snaps) > 1:
        cmp = snaps[0]
    ids = (base["id"],) + ((cmp["id"],) if cmp else ())
    for sid, b, n in conn.execute(f"SELECT snapshot_id, bin, n FROM elo_hist WHERE snapshot_id IN ({','.join('?' * len(ids))})",
                                  ids):
        (out.bins if sid == base["id"] else out.cmp)[b] = n
    if cmp:
        out.cmp_at = cmp["dt"]
    out.cuts = {r: e for r, e in cut_elo(conn, base["id"]).items() if r in config.ELO_CUT_LINES and e is not None}
    return out


def elo_marker(rows: list[dict], hist: EloHist) -> tuple[float, str] | None:
    """점수 분포 위 검색 계정의 세로선 — fifa.db elo_history 중 **지금 시즌이고 막대 스냅숏 데이터 시각 ±1일 안**의
    가장 가까운 값(1회차 B: 몇 주 전·지난 시즌 값이 지금 분포 위에 그려졌다). → (ELO, 기록 시각) 또는 None."""
    if not hist.now_at:
        return None
    base = datetime.fromisoformat(hist.now_at)
    first = hist.season_first_at or hist.now_at
    best = None
    for r in rows or ():
        at, elo = r.get("taken_at"), r.get("elo")
        if not at or elo is None or at < first[:10]:
            continue
        gap = abs(datetime.fromisoformat(at) - base)
        if gap <= timedelta(days=1) and (best is None or gap < best[0]):
            best = (gap, float(elo), at)
    return (best[1], best[2]) if best else None


def meta_status(conn: sqlite3.Connection) -> dict:
    """[정보] 수집 상태용 — 마지막 스냅숏의 기준 시각·빈 순위 · 쌓는 중인 수. 새 표가 없는 DB(옛 버전이 만든 채 아직
    새 버전 작업 스레드가 안 연 것)면 빈 값."""
    out: dict = {}
    try:
        r = conn.execute("SELECT m.ref_time, m.rank_gaps FROM snapshots s LEFT JOIN snapshot_meta m ON m.snapshot_id = s.id"
                         " ORDER BY s.taken_at DESC, s.id DESC LIMIT 1").fetchone()
        if r is not None:
            out["meta_ref"], out["meta_gaps"] = r[0], r[1]
        out["n_runs"] = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("run_open", "run_done"))
        out["n_peak"] = conn.execute("SELECT COUNT(*) FROM elo_season").fetchone()[0]
        out["n_first"] = conn.execute("SELECT COUNT(*) FROM champ_first").fetchone()[0]
        out["n_pick_days"] = conn.execute("SELECT COUNT(*) FROM (SELECT day FROM pick_days GROUP BY day)").fetchone()[0]
    except sqlite3.Error:
        pass
    return out


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
