"""조회한 경기를 SQLite에 누적 — API가 오래된 경기를 버리기 때문.

넥슨 API는 한 번에 100경기씩(offset 페이징) 주지만, 일정 기간이 지난 경기는
아예 안 준다(2026-09-05 실측: 3,024경기·약 한 달까지. 같은 시점 DB 는 7,859경기).
한동안 조회를 걸러 그 범위를 벗어난 경기는 영영 못 가져온다(과거 조회 수단이 없다).
그래서 조회할 때마다 여기에 쌓아 두고, 화면은 API가 아니라 이 DB를 본다.
(예전에 여기 "최근 100경기만 준다"고 적혀 있었다 — 100은 한 번에 받는 상한이다.)

저장 기준은 닉네임이 아니라 ouid — 구단주명은 바뀌어도 ouid는 안 바뀐다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

# 경기 기록 해석 — orjson 이 있으면 그걸로. 실데이터 1만 경기 2.05초 → 0.87초(중앙값 2.37배,
# 2026-10-02, 결과 동일 확인). "2배 이상일 때만 넣는다"는 합의를 넘어 들였다. 없으면(소스 실행) json.
# 배포판에 빠지면 조용히 느려지기만 하니 tools/release.py 가 들어갔는지 확인한다.
try:
    import orjson
    _loads = orjson.loads
    JSON_ENGINE = "orjson"
except ImportError:  # pragma: no cover — 설치 안 된 환경
    _loads = json.loads
    JSON_ENGINE = "json"

from seasons import Season

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id   TEXT PRIMARY KEY,
    match_type INTEGER,
    match_date TEXT,
    payload    TEXT NOT NULL
);
-- 한 경기에 두 명이 나온다. 원본을 사람마다 복사하지 않으려고 관계를 분리한다.
CREATE TABLE IF NOT EXISTS match_players (
    match_id TEXT NOT NULL,
    ouid     TEXT NOT NULL,
    PRIMARY KEY (match_id, ouid)
);
CREATE INDEX IF NOT EXISTS idx_players_ouid ON match_players(ouid);
CREATE INDEX IF NOT EXISTS idx_matches_type_date ON matches(match_type, match_date);
CREATE TABLE IF NOT EXISTS accounts (
    ouid      TEXT PRIMARY KEY,
    nickname  TEXT,
    last_seen TEXT
);
-- 상대 팀컬러(넥슨 데이터센터 감독모드 랭킹 스크래핑, top 10,000 안에서만
-- 잡히는 근사치·"지금" 값). 매번 다시 긁으면 느리니 fetched_at 기준
-- TTL(TEAM_COLOR_TTL_DAYS, 7일) 안에서는 재사용한다 — 그 이상 지나면 상대가 팀컬러를
-- 바꿨을 수 있어 다시 조회한다.
CREATE TABLE IF NOT EXISTS team_colors (
    nickname   TEXT PRIMARY KEY,
    team_color TEXT NOT NULL,
    team_value INTEGER,
    fetched_at TEXT NOT NULL
);
-- 감독모드 랭킹 시즌표(데이터센터 스크래핑, seasons.py). 확정된 과거 시즌은
-- 안 변하지만 새 시즌이 시작되면 목록에 한 줄이 붙으므로 TTL 안에서만 쓴다.
CREATE TABLE IF NOT EXISTS seasons (
    no         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
-- 카톡 봇(2026-10-05 삭제)이 쓰던 표 — 채팅방·보낸 사람 이름이 들어 있어 남기지 않는다.
DROP TABLE IF EXISTS bot_users;
-- 검색한 구단주의 ELO(랭킹 점수) 기록 — 메인 검색에서만 적는다(구단주 비교는 안 적는다).
-- 다른 구단주 1만 명분(스냅숏 출처)은 rank.db 에 있고 여기 쌓지 않는다 — 두 DB 에 걸친 트랜잭션이 없게.
CREATE TABLE IF NOT EXISTS elo_history (
    ouid       TEXT NOT NULL,
    profile_sn INTEGER,
    nickname   TEXT,
    taken_at   TEXT NOT NULL,
    elo        REAL NOT NULL,
    rank       INTEGER,
    source     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_elo_ouid_taken ON elo_history(ouid, taken_at);
"""

# 팀컬러는 잘 안 바뀌지만 팀가치(구단가치)는 강화로 계속 오르는 값이라
# 같이 저장하는 이상 짧게 간다 — 2026-07-23 사용자와 합의(둘 다 7일).
TEAM_COLOR_TTL_DAYS = 7

# 시즌은 두 달에 한 번꼴로 바뀐다. 하루에 한 번만 확인해도 새 시즌을 늦게
# 알아차릴 일이 없고, 조회 실패해도 지난 캐시를 계속 쓴다(load_seasons).
SEASON_TTL_DAYS = 1

# 열 때마다 PRAGMA optimize 에 줄 값 — 0x10000(필요한 표만 다시 재기) | 0x02(재는 행 수에 상한).
# SQLite 문서가 "연결을 열 때" 권하는 값이다.
OPTIMIZE_ON_OPEN = 0x10002

# 다른 연결이 쓰는 중이면 이만큼 기다린다(초) — 다른 프로세스(check_api 등)가 같은 DB 를 열 때.
OPEN_TIMEOUT_S = 15
# 여는 순간의 설정(WAL 전환 · 표 만들기 · 통계 갱신)은 쓰기라, 같은 프로세스의 두 스레드가 동시에 하면 한쪽이
# "database is locked" 로 죽었다 — 검색이 넥슨 조회와 DB 읽기를 나란히 돌리면서(03eeb08) 생겼다. 새 DB 를
# 세 스레드가 동시에 열면 60번 중 56번(2026-10-04 실측). WAL 전환은 잠금 대기(timeout)를 안 거쳐서 기다려도 안 된다.
_OPEN_LOCK = threading.Lock()


def open_db(path: Path | str) -> sqlite3.Connection:
    """DB를 열고 없으면 만든다. 스레드마다 따로 열 것 — 커넥션 공유 금지."""
    conn = sqlite3.connect(str(path), timeout=OPEN_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    with _OPEN_LOCK:
        # 조회(UI)와 저장(워커)이 겹칠 수 있어 WAL 로 둔다.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # team_value 열은 나중에 생겼다 — 그 전에 만들어진 DB 는 여기서 늘려준다.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(team_colors)")}
        if "team_value" not in cols:
            conn.execute("ALTER TABLE team_colors ADD COLUMN team_value INTEGER")
        conn.commit()
        # 통계가 없거나 낡았으면 다시 잰다(아니면 거의 0초). 통계가 없을 때 SQLite 는 계정별 경기 수를 셀 때
        # 감독모드 경기 2만 개를 매번 다 훑었다 — 계정 11개에 0.79초 → 0.07초(2026-10-04 실측).
        # 빈 DB 에서 쌓인 경우도 다시 잰다(같은 날 실측 0.88초 → 0.08초).
        conn.execute(f"PRAGMA optimize={OPTIMIZE_ON_OPEN}")
    return conn


def _match_ouids(detail: dict) -> list[str]:
    return [p.get("ouid") for p in (detail.get("matchInfo") or [])
            if isinstance(p.get("ouid"), str)]


def save_matches(conn: sqlite3.Connection, details: list[dict]) -> int:
    """새로 저장한 경기 수를 돌려준다. 이미 있는 경기는 건너뛴다.

    matchInfo 에 있는 모든 ouid 를 연결해 둔다 — 상대를 검색할 때도 재활용된다.
    """
    new = 0
    for d in details:
        mid = d.get("matchId")
        if not isinstance(mid, str) or not mid:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO matches (match_id, match_type, match_date, payload)"
            " VALUES (?, ?, ?, ?)",
            (mid, d.get("matchType"), d.get("matchDate"),
             json.dumps(d, ensure_ascii=False)),
        )
        new += cur.rowcount
        for ouid in _match_ouids(d):
            conn.execute(
                "INSERT OR IGNORE INTO match_players (match_id, ouid) VALUES (?, ?)",
                (mid, ouid),
            )
    conn.commit()
    return new


def known_ids(conn: sqlite3.Connection, ouid: str,
              match_type: int | None = None) -> set[str]:
    """이 계정으로 이미 저장해 둔 매치 id 전체 — '새 경기까지만' 받기용."""
    sql = ("SELECT p.match_id FROM match_players p"
           " JOIN matches m ON m.match_id = p.match_id"
           " WHERE p.ouid = ?")
    args: list = [ouid]
    if match_type is not None:
        sql += " AND m.match_type = ?"
        args.append(match_type)
    return {r["match_id"] for r in conn.execute(sql, args)}


def load_details(conn: sqlite3.Connection, ouid: str,
                 match_type: int | None = None,
                 limit: int | None = None,
                 stop: Callable[[], bool] | None = None) -> list[dict]:
    """저장된 경기를 최신순으로. 깨진 행은 건너뛴다(하나 때문에 전체가 죽지 않게).
    stop 이 참을 돌려주면 거기서 멈추고 읽은 데까지만 — 미리 읽기를 버릴 때(1만 경기 약 2초)."""
    # 종류가 정해지면 (종류, 날짜) 인덱스 순서로 읽는다 — 이미 최신순이라 정렬이 없다. 통계(open_db 의
    # PRAGMA optimize)가 생기자 SQLite 는 계정 인덱스부터 고르고 본문(1만 경기 212MB)을 통째로 임시
    # 정렬했다: SQL 0.7~1.0초 → 1.5~2.3초(2026-10-04 실측). 계정 쪽은 (경기, 계정) 키로 바로 찾으니
    # 경기가 적은 계정도 다른 계정 경기의 인덱스만 훑고 지나간다.
    table = "matches m INDEXED BY idx_matches_type_date" if match_type is not None else "matches m"
    sql = (f"SELECT m.payload FROM {table}"
           " JOIN match_players p ON p.match_id = m.match_id"
           " WHERE p.ouid = ?")
    args: list = [ouid]
    if match_type is not None:
        sql += " AND m.match_type = ?"
        args.append(match_type)
    sql += " ORDER BY m.match_date DESC"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)

    out = []
    for row in conn.execute(sql, args):
        if stop is not None and stop():
            break
        try:
            out.append(_loads(row["payload"]))
        except Exception:
            continue
    return out


def all_match_ids(conn: sqlite3.Connection) -> set[str]:
    """저장된 경기 id 전부(계정 무관) — 디스크 캐시 정리용."""
    return {r["match_id"] for r in conn.execute("SELECT match_id FROM matches")}


def load_details_by_ids(conn: sqlite3.Connection, match_ids) -> list[dict]:
    """그 경기들만(순서 없음) — 같은 계정을 다시 검색할 때 새로 생긴 경기만 읽으려고.

    전부 읽으면 1만 경기에 3.6초(그중 JSON 해석 2.8초)였다(2026-10-02 실측)."""
    ids = list(match_ids)
    out = []
    for i in range(0, len(ids), 500):  # SQLite 변수 개수 상한 회피
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT payload FROM matches WHERE match_id IN ({q})", chunk):
            try:
                out.append(_loads(row["payload"]))
            except Exception:
                continue
    return out


def merge_details(old: list[dict], new: list[dict]) -> list[dict]:
    """이미 가진 목록 + 새로 읽은 것 → load_details 와 같은 순서(matchDate 내림차순)의 새 목록.

    옛 목록은 건드리지 않는다(화면이 아직 쓰고 있다). 같은 경기가 양쪽에 있으면 한 번만.
    새 경기가 늘 앞이라고 가정하지 않는다 — 처음 검색이 한도에 걸렸다 이어 받으면 옛 경기가 새로 온다."""
    seen = {d.get("matchId") for d in old}
    merged = list(old) + [d for d in new if d.get("matchId") not in seen]
    merged.sort(key=lambda d: d.get("matchDate") or "", reverse=True)
    return merged


def match_count(conn: sqlite3.Connection, ouid: str,
                match_type: int | None = None) -> int:
    sql = ("SELECT COUNT(*) AS n FROM matches m"
           " JOIN match_players p ON p.match_id = m.match_id"
           " WHERE p.ouid = ?")
    args: list = [ouid]
    if match_type is not None:
        sql += " AND m.match_type = ?"
        args.append(match_type)
    return conn.execute(sql, args).fetchone()["n"]


def date_range(conn: sqlite3.Connection, ouid: str,
               match_type: int | None = None) -> tuple[str | None, str | None]:
    """쌓인 기간 — 화면에 '언제부터 언제까지'를 보여주려고."""
    sql = ("SELECT MIN(m.match_date) AS a, MAX(m.match_date) AS b FROM matches m"
           " JOIN match_players p ON p.match_id = m.match_id"
           " WHERE p.ouid = ?")
    args: list = [ouid]
    if match_type is not None:
        sql += " AND m.match_type = ?"
        args.append(match_type)
    r = conn.execute(sql, args).fetchone()
    return r["a"], r["b"]


# ── 등록 계정(즐겨찾기 겸 수집 대상) ────────────────────────────────────
def upsert_account(conn: sqlite3.Connection, ouid: str, nickname: str) -> None:
    conn.execute(
        "INSERT INTO accounts (ouid, nickname, last_seen) VALUES (?, ?, ?)"
        " ON CONFLICT(ouid) DO UPDATE SET nickname=excluded.nickname,"
        " last_seen=excluded.last_seen",
        (ouid, nickname, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT ouid, nickname, last_seen FROM accounts ORDER BY nickname")]


def recent_searches(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """최근 검색 기록 — 조회할 때마다 upsert_account 가 last_seen 을 갱신하므로
    그 최신순이 곧 검색 기록이다."""
    return [dict(r) for r in conn.execute(
        "SELECT ouid, nickname, last_seen FROM accounts"
        " ORDER BY last_seen DESC LIMIT ?", (limit,))]


def remove_account(conn: sqlite3.Connection, ouid: str) -> None:
    """목록에서만 뺀다 — 쌓아 둔 경기는 지우지 않는다."""
    conn.execute("DELETE FROM accounts WHERE ouid = ?", (ouid,))
    conn.commit()


# ── 상대 팀컬러·팀가치 캐시 ──────────────────────────────────────────────
def load_team_colors(conn: sqlite3.Connection, nicknames: list[str],
                     ttl_days: int = TEAM_COLOR_TTL_DAYS
                     ) -> dict[str, tuple[str, int | None]]:
    """TTL 안에 있는 캐시만 {닉네임: (팀컬러, 팀가치)} 로 돌려준다.

    팀컬러는 있는데 팀가치가 NULL 인 행(팀가치 저장 이전 버전이 남긴 것)은
    빼고 돌려준다 — 없는 셈 쳐야 호출부가 다시 긁어서 팀가치까지 채운다."""
    if not nicknames:
        return {}
    cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat(timespec="seconds")
    out: dict[str, tuple[str, int | None]] = {}
    for i in range(0, len(nicknames), 500):  # SQLite 변수 개수 상한 회피
        chunk = nicknames[i:i + 500]
        q = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT nickname, team_color, team_value FROM team_colors"
            f" WHERE nickname IN ({q}) AND fetched_at >= ?", (*chunk, cutoff)):
            if row["team_color"] and row["team_value"] is None:
                continue  # 구버전 캐시 — 팀가치 백필을 위해 재조회 대상으로 남긴다
            out[row["nickname"]] = (row["team_color"], row["team_value"])
    return out


def load_seasons(conn: sqlite3.Connection) -> list[Season]:
    """캐시된 시즌표를 최신순으로. 오래됐어도 그대로 준다 — 지난 시즌 구간은
    변하지 않으므로, 네트워크가 안 될 때 빈 목록을 주는 것보다 낫다.
    (새 시즌이 붙었는지는 seasons_stale 로 따로 판단한다.)"""
    out = []
    for r in conn.execute("SELECT no, name, start_date, end_date FROM seasons"
                          " ORDER BY start_date DESC"):
        try:
            out.append(Season(no=r["no"], name=r["name"],
                              start=date.fromisoformat(r["start_date"]),
                              end=date.fromisoformat(r["end_date"])))
        except ValueError:
            continue
    return out


def seasons_stale(conn: sqlite3.Connection,
                  ttl_days: int = SEASON_TTL_DAYS) -> bool:
    """다시 긁어와야 하는지. 비어 있으면 당연히 True."""
    row = conn.execute("SELECT MAX(fetched_at) AS t FROM seasons").fetchone()
    if not row or not row["t"]:
        return True
    cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat(timespec="seconds")
    return row["t"] < cutoff


def save_seasons(conn: sqlite3.Connection, items: list[Season]) -> None:
    if not items:
        return
    now = datetime.now().isoformat(timespec="seconds")
    for s in items:
        conn.execute(
            "INSERT INTO seasons (no, name, start_date, end_date, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(no) DO UPDATE SET name=excluded.name,"
            " start_date=excluded.start_date, end_date=excluded.end_date,"
            " fetched_at=excluded.fetched_at",
            (s.no, s.name, s.start.isoformat(), s.end.isoformat(), now))
    conn.commit()


def save_team_colors(conn: sqlite3.Connection,
                     colors: dict[str, tuple[str, int | None]],
                     fetched_at: datetime | None = None) -> None:
    """team_color 가 빈 문자열("찾지 못함")이어도 저장한다 — TTL 안에는
    없는 상대를 매번 다시 조회하지 않게. 팀가치는 랭커로 찾아진 상대만
    있고(top 10,000 밖이면 None) 팀컬러와 항상 같이 갱신된다.
    fetched_at: 값을 실제로 읽은 시각 — 랭킹 스냅숏에서 채웠으면 스냅숏 시각(지금 시각을 넣으면
    7일 유효기간이 최대 8일이 된다)."""
    if not colors:
        return
    now = (fetched_at or datetime.now()).isoformat(timespec="seconds")
    for nickname, (color, value) in colors.items():
        conn.execute(
            "INSERT INTO team_colors (nickname, team_color, team_value, fetched_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(nickname) DO UPDATE SET team_color=excluded.team_color,"
            " team_value=excluded.team_value, fetched_at=excluded.fetched_at",
            (nickname, color, value, now))
    conn.commit()


# ── ELO 기록 (1.1.1 — 그래프는 1.3.1) ─────────────────────────────────────────

def save_elo(conn: sqlite3.Connection, ouid: str, elo: float, rank: int | None,
             profile_sn: int | None = None, nickname: str = "", source: str = "search",
             taken_at: datetime | None = None) -> bool:
    """검색 때 받은 ELO 를 한 줄 — 마지막 줄과 점수·순위가 같으면 안 적는다(같은 날 재검색이 줄을 불리지 않게).
    → 적었나."""
    last = conn.execute("SELECT elo, rank FROM elo_history WHERE ouid = ? ORDER BY taken_at DESC LIMIT 1",
                        (ouid,)).fetchone()
    if last is not None and last["elo"] == elo and last["rank"] == rank:
        return False
    conn.execute("INSERT INTO elo_history (ouid, profile_sn, nickname, taken_at, elo, rank, source)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (ouid, profile_sn, nickname, (taken_at or datetime.now()).isoformat(timespec="seconds"),
                  elo, rank, source))
    conn.commit()
    return True


def elo_history(conn: sqlite3.Connection, ouid: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT taken_at, elo, rank, profile_sn, nickname, source FROM elo_history"
        " WHERE ouid = ? ORDER BY taken_at", (ouid,))]


def clear_elo(conn: sqlite3.Connection, keep_ouid: str | None = None) -> int:
    """ELO 기록 지우기 — keep_ouid 가 있으면 그 계정 것만 남긴다. → 지운 줄 수."""
    if keep_ouid:
        cur = conn.execute("DELETE FROM elo_history WHERE ouid <> ?", (keep_ouid,))
    else:
        cur = conn.execute("DELETE FROM elo_history")
    conn.commit()
    return cur.rowcount
