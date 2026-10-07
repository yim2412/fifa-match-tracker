"""개발용 상시 수집 — 이 PC 에서만, 기능을 공개하기 전부터 쌓아 둔다(2026-10-07 사용자: "수집할 수 있는 건 다 해도 됨").

앱 배포판에는 들어가지 않는다. 작업 스케줄러가 로그온 때·매일 한 번 부른다(pythonw — 창 없음, 게임 중 초점을 안 뺏는다).
30단계(2.3.1 "쌓기 먼저")의 N3·N11·N12·N15 는 쌓인 날만큼만 보이므로, 앱 기능보다 먼저 재료를 모은다.

| # | 무엇 | 왜 | 비용 |
|---|---|---|---|
| ① | rank.db 스냅숏 원본을 보관 DB 로 복사 | 원본은 RANK_RAW_KEEP_DAYS(14일) 뒤 prune_raw 가 지운다 — 연속·첫 달성·최고점은 영구 기록이 필요 | 요청 0 |
| ② | 데이터센터 '기준' 시각 | 페이지에 "YYYY-MM-DD HH:MM:SS 기준"이 있다(10-07 실측) — 수집 시각과 다르다 | 웹 1요청 |
| ③ | 랭커 픽 날짜별 기록 | ranker_squads 는 profile_sn 마다 한 줄을 덮어써 날짜별 기록이 없다(N15) | 오픈API — 앱과 같은 하루 상한·계수 |

③은 앱의 rankerpick.collect 를 그대로 부른다(3일 규칙 · 하루 상한 · 429 · 계수 공유). 그날 끝에 상위 200명의
"지금 알고 있는 마지막 경기·선발"을 날짜별로 적는다 — 3일 규칙 때문에 하루에 다 새로 받지는 않으니 match_day 로 신선도를 본다.

보관 DB(`DATA_DIR/dev_archive.db`)는 다른 구단주 1만 명 닉네임이 들어 있어 저장소 밖이다. 하루 약 1~1.5MB.
⚠ 소스(개발 중 코드)로 공용 fifa.db 를 연다 — 개발 중 store 스키마가 설치판보다 앞서면 설치판이 새 스키마를 보게 된다.
   스키마를 바꾸는 단계에서는 이 작업을 끄거나(`--only 1,2`) 옛 버전 호환을 먼저 확인한다.

    python tools/dev_archive.py            # 오늘 안 돌았으면 ①②③
    python tools/dev_archive.py --force    # 오늘 돌았어도
    python tools/dev_archive.py --only 1,2 # 고른 것만
    python tools/dev_archive.py --status   # 보관 현황


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
**개발용 상시 수집(이 PC 전용 ·
배포판 밖)** — 기능 공개 전부터 재료를 쌓는다(2026-10-07): ① rank.db 스냅숏 원본을 14일 정리 전에 `DATA_DIR/dev_archive.db` 로(키는 `taken_at` — rank.db 를 지우면 id 가 1부터) ② 데이터센터 `rank_advice` 의 기준 시각 ③ `rankerpick.collect` + 상위 200 선발을 날짜별 `pick_days` — `.env` 에 `DEV_NEXON_API_KEY`(서비스 단계 키 ·
2026-10-07 발급)가 있으면 그 키로, 계수 `ranker_pick_dev`·상한 5,000·하루마다 다시(앱 키의 계수·429 와 따로 — `collect` 의 `budget_kind`·`daily_cap`·`stale_days`, 기본값은 앱 그대로).
앱 키 `NEXON_API_KEY` 는 안 바꾼다(바꾸면 거래가 '키 바뀜'으로 `trades_prev` 로 옮겨진다). 작업 스케줄러 `FifaDevArchive`(pythonw ·
로그온 2분 뒤·매일 09:00 · 그날 성공했으면 건너뜀). ⚠ 소스로 공용 fifa.db 를 연다 — store 스키마를 바꾸는 단계에선 `--only 1,2` 로 돌리거나 옛 버전 호환부터 본다.
로그 `logs/dev_archive.log`. 테스트 `tests/test_dev_archive.py`
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import ranker  # noqa: E402

ARCHIVE_PATH = config.DATA_DIR / "dev_archive.db"
LOG_PATH = config.DATA_DIR / "logs" / "dev_archive.log"
PROGRESS_DIR = Path.home() / ".claude" / "bg-progress.d"
REF_RE = ranker._REF_TIME   # 정규식은 ranker 한 곳(2.3.1 N14) — 여기는 페이지 글자(공백 꼴) 그대로 적는다
PICK_TOP = config.RANKER_PICK_TOP
# 서비스 단계 키(2026-10-07 — 하루 2,000만 · 초당 500)가 .env 에 있으면 ③ 은 그 키로, 계수·429 도 앱 키와 따로.
# 앱 키(NEXON_API_KEY)는 그대로 둔다 — 바꾸면 거래 받기가 '키 바뀜'으로 옛 거래를 trades_prev 로 옮긴다
DEV_KEY_VAR = "DEV_NEXON_API_KEY"
DEV_BUDGET_KIND = "ranker_pick_dev"
DEV_DAILY_CAP = 5000         # 상위 200 매일 다시 받기 ≈ 400 — 넉넉히. 넥슨 한도가 아니라 실수(무한 반복)를 막는 상한
DEV_STALE_DAYS = 0.8         # 하루 한 번 다시 받는다(어제 09:00 에 받은 랭커를 오늘 08:59 로그온 때도 다시 — 1일이면 빠진다)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snap (
    id INTEGER PRIMARY KEY,
    taken_at TEXT UNIQUE NOT NULL,          -- rank.db snapshots.taken_at(그 id 는 rank.db 를 지우면 다시 1부터라 키로 안 쓴다)
    row_count INTEGER, dup_count INTEGER, top_elo REAL,
    season_seq INTEGER, new_season INTEGER, ended_season INTEGER,
    archived_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snap_rows (
    snap_id INTEGER NOT NULL, rank INTEGER NOT NULL, profile_sn INTEGER NOT NULL, nickname TEXT NOT NULL,
    level INTEGER, team_value INTEGER, elo REAL, win INTEGER, draw INTEGER, lose INTEGER,
    team_color TEXT, color_count INTEGER, formation TEXT, grade INTEGER, best_grade INTEGER, prev_grade INTEGER,
    emblem TEXT,
    PRIMARY KEY (snap_id, profile_sn)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ref_times (
    fetched_at TEXT PRIMARY KEY,
    ref_time TEXT                            -- None = 페이지에서 못 찾음(구조가 바뀌었을 수 있다)
);
CREATE TABLE IF NOT EXISTS pick_days (
    day TEXT NOT NULL, profile_sn INTEGER NOT NULL, rank INTEGER, nickname TEXT, ouid TEXT,
    match_id TEXT, match_day TEXT, fetched_at TEXT, fail TEXT,
    starters TEXT,                           -- JSON [[spId, spPosition, spGrade], ...] — 경기가 없으면 NULL
    PRIMARY KEY (day, profile_sn)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT PRIMARY KEY, day TEXT NOT NULL, ok INTEGER NOT NULL, summary TEXT
);
"""

_COLS = ("rank", "profile_sn", "nickname", "level", "team_value", "elo", "win", "draw", "lose",
         "team_color", "color_count", "formation", "grade", "best_grade", "prev_grade")


def open_archive(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else ARCHIVE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# ── ① 스냅숏 원본 ──────────────────────────────────────────────────────

def archive_snapshots(rank_conn: sqlite3.Connection | None, arc: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    """원본이 남은 스냅숏 중 보관에 없는 것을 옮긴다(멱등 — taken_at 으로 대조). → (새 스냅숏 수, 옮긴 행 수)."""
    if rank_conn is None:
        return 0, 0
    have = {r[0] for r in arc.execute("SELECT taken_at FROM snap")}
    snaps = rank_conn.execute(
        "SELECT s.id, s.taken_at, s.row_count, s.dup_count, s.top_elo, s.season_seq, s.new_season, s.ended_season"
        " FROM snapshots s WHERE EXISTS (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id)").fetchall()
    has_emblems = rank_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshot_emblems'").fetchone() is not None
    new_snaps = new_rows = 0
    for s in snaps:
        if s[1] in have:
            continue
        rows = rank_conn.execute(f"SELECT {', '.join(_COLS)} FROM snapshot_rows WHERE snapshot_id = ?", (s[0],)).fetchall()
        emb = {}
        if has_emblems:
            emb = dict(rank_conn.execute("SELECT profile_sn, emblem FROM snapshot_emblems WHERE snapshot_id = ?", (s[0],)))
        with arc:  # 스냅숏 하나 = 한 트랜잭션 — 반쪽 보관이 남지 않게
            cur = arc.execute(
                "INSERT INTO snap (taken_at, row_count, dup_count, top_elo, season_seq, new_season, ended_season, archived_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*s[1:], now.isoformat(timespec="seconds")))
            sid = cur.lastrowid
            arc.executemany(
                f"INSERT INTO snap_rows (snap_id, {', '.join(_COLS)}, emblem) VALUES (?, {', '.join('?' * len(_COLS))}, ?)",
                [(sid, *r, emb.get(r[1])) for r in rows])
        new_snaps += 1
        new_rows += len(rows)
    return new_snaps, new_rows


# ── ② 기준 시각 ────────────────────────────────────────────────────────

def parse_ref_time(html: str) -> str | None:
    m = REF_RE.search(html or "")
    return m.group(1) if m else None


def record_ref_time(arc: sqlite3.Connection, now: datetime, fetch=None) -> str | None:
    """1쪽 하나를 받아 기준 시각을 적는다. 못 찾으면 None 을 적는다(구조 변경 신호)."""
    if fetch is None:
        if not config.WEB_DATA:
            raise RuntimeError(config.WEB_DATA_OFF_MSG)
        res = ranker.web_get(ranker._session, ranker.RANK_URL,
                             params={"rt": "manager", "n4seasonno": 0, "n4pageno": 1, "_ts": int(time.time() * 1000)},
                             timeout=config.RANK_PAGE_TIMEOUT_S)
        res.raise_for_status()
        html = res.text
    else:
        html = fetch()
    ref = parse_ref_time(html)
    with arc:
        arc.execute("INSERT OR REPLACE INTO ref_times (fetched_at, ref_time) VALUES (?, ?)",
                    (now.isoformat(timespec="seconds"), ref))
    return ref


# ── ③ 랭커 픽 날짜별 ────────────────────────────────────────────────────

def snapshot_pick_day(conn: sqlite3.Connection, arc: sqlite3.Connection, targets: list[dict], day: str) -> int:
    """상위 랭커마다 fifa.db 에 지금 있는 마지막 경기·선발을 그날 줄로(같은 날 다시 돌면 덮어쓴다). → 적은 줄 수."""
    import rankerpick
    import store
    have = store.ranker_squads(conn)
    out = []
    for t in targets:
        h = have.get(t["profile_sn"])
        if h is None:
            continue
        starters = None
        if h.get("match_id") and h.get("ouid"):
            detail = store.load_match(conn, h["match_id"])
            if detail is not None:
                starters = json.dumps([[p.get("spId"), p.get("spPosition"), p.get("spGrade")]
                                       for p in rankerpick._starters(detail, h["ouid"])], separators=(",", ":"))
        out.append((day, t["profile_sn"], t.get("rank"), h.get("nickname"), h.get("ouid"), h.get("match_id"),
                    h.get("match_day"), h.get("fetched_at"), h.get("fail"), starters))
    with arc:
        arc.executemany("INSERT OR REPLACE INTO pick_days (day, profile_sn, rank, nickname, ouid, match_id, match_day,"
                        " fetched_at, fail, starters) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", out)
    return len(out)


def collect_picks(arc: sqlite3.Connection, now: datetime) -> str:
    import rankcollect
    import rankerpick
    import store
    from nexon_api import FCOnlineAPI
    dev_key = os.getenv(DEV_KEY_VAR, "").strip()
    key = dev_key or config.API_KEY
    if not key:
        return "API 키 없음 — 건너뜀"
    opts = ({"budget_kind": DEV_BUDGET_KIND, "daily_cap": DEV_DAILY_CAP, "stale_days": DEV_STALE_DAYS}
            if dev_key else {})
    if not config.ranker_pick_allowed():
        return "랭커 픽 동의·수집 토글이 꺼져 있음 — 건너뜀"
    rconn = rankcollect.open_rank_db_ro()
    try:
        _taken, targets = rankerpick.top_rankers(rconn, limit=PICK_TOP)
    finally:
        if rconn is not None:
            rconn.close()
    if not targets:
        return "스냅숏 없음 — 건너뜀"
    api = FCOnlineAPI(key, cache_dir=config.CACHE_DIR)
    conn = store.open_db(config.DB_PATH)
    try:
        res = rankerpick.collect(api, conn, targets, now_fn=datetime.now, **opts)
        n = snapshot_pick_day(conn, arc, targets, now.date().isoformat())
    finally:
        conn.close()
    flags = [k for k in ("limit", "quota", "cancelled") if getattr(res, k)]
    return (f"{'서비스 키' if dev_key else '앱 키'} · 확인 {res.checked}명 · 요청 {res.requests} · 기록 {n}줄"
            + (f" · 멈춤({','.join(flags)})" if flags else "") + (f" · 오류 {res.error}" if res.error else ""))


# ── 실행 ──────────────────────────────────────────────────────────────

def ran_ok_today(arc: sqlite3.Connection, day: str) -> bool:
    return arc.execute("SELECT 1 FROM runs WHERE day = ? AND ok = 1", (day,)).fetchone() is not None


class _Progress:
    """상태줄 한 줄(전역 규칙 — bg-progress.d/<이름>-<PID>.txt). 끝나면 지운다."""

    def __init__(self):
        self.path = PROGRESS_DIR / f"dev_archive-{os.getpid()}.txt"
        self.t0 = time.monotonic()

    def set(self, msg: str) -> None:
        try:
            PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"개발 수집 · {msg} · {int(time.monotonic() - self.t0)}초\n", encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _log(lines: list[str]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    if sys.stdout is not None:  # pythonw 면 None
        print("\n".join(lines), flush=True)


def run(parts: set[int], force: bool = False, archive_path=None) -> int:
    import rankcollect
    now = datetime.now()
    day = now.date().isoformat()
    arc = open_archive(archive_path)
    lines = [f"── {now.isoformat(timespec='seconds')} dev_archive {sorted(parts)}"]
    if not force and ran_ok_today(arc, day):
        _log(lines + ["[OK]   오늘 이미 돌았음 — 건너뜀"])
        arc.close()
        return 0
    rankcollect.lower_thread_priority()
    prog = _Progress()
    ok = True
    try:
        steps = [(1, "① 원본 보관", lambda: "새 스냅숏 {} · 행 {}".format(*_arch(rankcollect, arc, now))),
                 (2, "② 기준 시각", lambda: f"기준 {record_ref_time(arc, now)}"),
                 (3, "③ 랭커 픽", lambda: collect_picks(arc, now))]
        for n, name, fn in steps:
            if n not in parts:
                continue
            prog.set(name)
            try:
                lines.append(f"[OK]   {name}: {fn()}")
            except Exception as e:  # 한 단계 실패가 나머지를 막지 않는다
                ok = False
                lines.append(f"[FAIL] {name}: {type(e).__name__}: {e}")
                lines.append(traceback.format_exc().rstrip())
        with arc:
            arc.execute("INSERT OR REPLACE INTO runs (started_at, day, ok, summary) VALUES (?, ?, ?, ?)",
                        (now.isoformat(timespec="seconds"), day, int(ok), " | ".join(lines[1:])[:2000]))
    finally:
        prog.clear()
        arc.close()
    _log(lines)
    return 0 if ok else 1


def _arch(rankcollect, arc, now):
    rconn = rankcollect.open_rank_db_ro()
    try:
        return archive_snapshots(rconn, arc, now)
    finally:
        if rconn is not None:
            rconn.close()


def status(archive_path=None) -> int:
    arc = open_archive(archive_path)
    try:
        q = lambda s: tuple(arc.execute(s).fetchone())  # noqa: E731
        print("스냅숏", q("SELECT COUNT(*), MIN(taken_at), MAX(taken_at) FROM snap"),
              "행", q("SELECT COUNT(*) FROM snap_rows")[0])
        print("기준 시각", [tuple(r) for r in arc.execute("SELECT fetched_at, ref_time FROM ref_times ORDER BY fetched_at DESC LIMIT 5")])
        print("랭커 픽 날짜별", [tuple(r) for r in arc.execute("SELECT day, COUNT(*), COUNT(starters) FROM pick_days"
                                                        " GROUP BY day ORDER BY day DESC LIMIT 7")])
        for r in arc.execute("SELECT started_at, ok, summary FROM runs ORDER BY started_at DESC LIMIT 3"):
            print("실행", tuple(r))
    finally:
        arc.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="개발용 상시 수집(이 PC 전용)")
    ap.add_argument("--force", action="store_true", help="오늘 돌았어도 다시")
    ap.add_argument("--only", default="1,2,3", help="고른 단계만 — 예: 1,2")
    ap.add_argument("--status", action="store_true", help="보관 현황만 보기")
    a = ap.parse_args(argv)
    if a.status:
        return status()
    try:
        parts = {int(x) for x in a.only.split(",") if x.strip()}
    except ValueError:
        ap.error("--only 는 1,2,3 중 쉼표로")
    if not parts <= {1, 2, 3}:
        ap.error("--only 는 1,2,3 중 쉼표로")
    return run(parts, force=a.force)


if __name__ == "__main__":
    raise SystemExit(main())
