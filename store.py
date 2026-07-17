"""조회한 경기를 SQLite에 누적 — API가 최근 100경기까지만 주기 때문.

넥슨 API는 최근 100경기만 돌려준다. 이 계정은 21시간에 100경기가 쌓여서,
하루만 조회를 걸러도 그 사이 경기는 영영 못 가져온다(과거 조회 수단이 없다).
그래서 조회할 때마다 여기에 쌓아 두고, 화면은 API가 아니라 이 DB를 본다.

저장 기준은 닉네임이 아니라 ouid — 구단주명은 바뀌어도 ouid는 안 바뀐다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
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
"""


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


def remove_account(conn: sqlite3.Connection, ouid: str) -> None:
    """목록에서만 뺀다 — 쌓아 둔 경기는 지우지 않는다."""
    conn.execute("DELETE FROM accounts WHERE ouid = ?", (ouid,))
    conn.commit()
