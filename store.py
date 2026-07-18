"""조회한 경기를 SQLite에 누적 — API가 최근 100경기까지만 주기 때문.

넥슨 API는 최근 100경기만 돌려준다. 이 계정은 21시간에 100경기가 쌓여서,
하루만 조회를 걸러도 그 사이 경기는 영영 못 가져온다(과거 조회 수단이 없다).
그래서 조회할 때마다 여기에 쌓아 두고, 화면은 API가 아니라 이 DB를 본다.

저장 기준은 닉네임이 아니라 ouid — 구단주명은 바뀌어도 ouid는 안 바뀐다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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
-- TTL(기본 30일) 안에서는 재사용한다 — 그 이상 지나면 상대가 팀컬러를
-- 바꿨을 수 있어 다시 조회한다.
CREATE TABLE IF NOT EXISTS team_colors (
    nickname   TEXT PRIMARY KEY,
    team_color TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

TEAM_COLOR_TTL_DAYS = 30


def open_db(path: Path | str) -> sqlite3.Connection:
    """DB를 열고 없으면 만든다. 스레드마다 따로 열 것 — 커넥션 공유 금지."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # 조회(UI)와 저장(워커)이 겹칠 수 있어 WAL 로 둔다.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
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


def existing_ids(conn: sqlite3.Connection, match_ids: list[str]) -> set[str]:
    """이미 저장된 경기ID — 이건 API를 다시 부를 필요가 없다."""
    if not match_ids:
        return set()
    out: set[str] = set()
    for i in range(0, len(match_ids), 500):  # SQLite 변수 개수 상한 회피
        chunk = match_ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        out |= {r["match_id"] for r in conn.execute(
            f"SELECT match_id FROM matches WHERE match_id IN ({q})", chunk)}
    return out


def load_details(conn: sqlite3.Connection, ouid: str,
                 match_type: int | None = None,
                 limit: int | None = None) -> list[dict]:
    """저장된 경기를 최신순으로. 깨진 행은 건너뛴다(하나 때문에 전체가 죽지 않게)."""
    sql = ("SELECT m.payload FROM matches m"
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
        try:
            out.append(json.loads(row["payload"]))
        except Exception:
            continue
    return out


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


# ── 상대 팀컬러 캐시 ─────────────────────────────────────────────────────
def load_team_colors(conn: sqlite3.Connection, nicknames: list[str],
                     ttl_days: int = TEAM_COLOR_TTL_DAYS) -> dict[str, str]:
    """TTL 안에 있는 캐시만 돌려준다 — 지난 건 없는 셈 치고 다시 조회해야 한다."""
    if not nicknames:
        return {}
    cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat(timespec="seconds")
    out: dict[str, str] = {}
    for i in range(0, len(nicknames), 500):  # SQLite 변수 개수 상한 회피
        chunk = nicknames[i:i + 500]
        q = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT nickname, team_color FROM team_colors"
            f" WHERE nickname IN ({q}) AND fetched_at >= ?", (*chunk, cutoff)):
            out[row["nickname"]] = row["team_color"]
    return out


def save_team_colors(conn: sqlite3.Connection, colors: dict[str, str]) -> None:
    """team_color 가 빈 문자열("찾지 못함")이어도 저장한다 — TTL 안에는
    없는 상대를 매번 다시 조회하지 않게."""
    if not colors:
        return
    now = datetime.now().isoformat(timespec="seconds")
    for nickname, color in colors.items():
        conn.execute(
            "INSERT INTO team_colors (nickname, team_color, fetched_at) VALUES (?, ?, ?)"
            " ON CONFLICT(nickname) DO UPDATE SET team_color=excluded.team_color,"
            " fetched_at=excluded.fetched_at",
            (nickname, color, now))
    conn.commit()
