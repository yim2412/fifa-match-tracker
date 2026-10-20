"""서버 저장소 — SQLite(WAL). 연결 하나를 잠금 하나로 줄 세운다(쓰기 경합·busy 를 안 만든다 · worker 는 프로세스 하나).

표:
- detail  : 경기 상세 **영구**(9절 6 사용자 결정) · gzip · 양쪽 ouid 열 — 삭제 요청을 ouid 로 찾으려고(영구 표라 나중에 열을 더하기 어렵다)
- erased  : 지운 ouid — 그 ouid 가 낀 경기는 다시 받아도 저장하지 않는다(중계는 한다 — 저장만 안 한다)
- short   : 짧게 사는 응답(경로+정규화한 인자 → 상태·본문·만료) · 부정 캐시도 여기
- budget  : 하루 넥슨 요청 수(KST 날짜 · 범위 'all' 또는 'ip:<주소>')
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS detail (
    match_id   TEXT PRIMARY KEY,
    ouid_a     TEXT,
    ouid_b     TEXT,
    match_date TEXT,
    body       BLOB NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS detail_ouid_a ON detail(ouid_a);
CREATE INDEX IF NOT EXISTS detail_ouid_b ON detail(ouid_b);
CREATE TABLE IF NOT EXISTS erased (ouid TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS short (
    key     TEXT PRIMARY KEY,
    status  INTEGER NOT NULL,
    body    BLOB NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS budget (
    day   TEXT NOT NULL,
    scope TEXT NOT NULL,
    n     INTEGER NOT NULL,
    PRIMARY KEY (day, scope)
) WITHOUT ROWID;
"""


def _ouids(data: dict) -> list[str]:
    infos = data.get("matchInfo") if isinstance(data, dict) else None
    out = []
    for m in infos if isinstance(infos, list) else []:
        o = m.get("ouid") if isinstance(m, dict) else None
        if isinstance(o, str) and o:
            out.append(o)
    return out


class Cache:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            # 새 파일에만 먹는다(WAL 로 만든 뒤엔 바뀌지 않는다) — 상세 한 줄(gzip 약 2.5KB)이 4KB 쪽에 하나씩만 들어가
            # 쪽마다 남는 자리가 버려졌다. ⓐ 실측 2,999경기 정리본: 4KB 4.2KB/경기 · 16KB 2.95KB · 64KB 3.0KB
            self._conn.execute(f"PRAGMA page_size = {settings.DB_PAGE_SIZE}")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 경기 상세(영구) ────────────────────────────────────────────
    def get_detail(self, match_id: str) -> bytes | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM detail WHERE match_id = ?", (match_id,)).fetchone()
        return gzip.decompress(row[0]) if row else None

    def put_detail(self, match_id: str, raw: bytes, data: dict) -> bool:
        """저장했으면 True — 본문의 matchId 가 요청 id 와 다르거나, 지운 ouid 가 끼었으면 저장 안 한다."""
        if not isinstance(data, dict) or data.get("matchId") != match_id:
            return False
        ouids = _ouids(data)
        with self._lock:
            if ouids and self._conn.execute(
                    f"SELECT 1 FROM erased WHERE ouid IN ({','.join('?' * len(ouids))}) LIMIT 1", ouids).fetchone():
                return False
            a = ouids[0] if ouids else None
            b = ouids[1] if len(ouids) > 1 else None
            self._conn.execute(
                "INSERT OR IGNORE INTO detail(match_id, ouid_a, ouid_b, match_date, body, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (match_id, a, b, str(data.get("matchDate") or ""), gzip.compress(raw),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return True

    def detail_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM detail").fetchone()[0]

    def erase(self, ouid: str) -> int:
        """그 ouid 가 낀 경기를 지우고 다시 쌓지 않게 적는다 → 지운 경기 수. 지우는 길(화면·확인)은 ⓑ."""
        with self._lock:
            self._conn.execute("BEGIN")
            n = self._conn.execute("DELETE FROM detail WHERE ouid_a = ? OR ouid_b = ?", (ouid, ouid)).rowcount
            self._conn.execute("INSERT OR REPLACE INTO erased(ouid, at) VALUES (?, ?)",
                               (ouid, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            self._conn.execute("COMMIT")
        return n

    # ── 짧게 사는 응답 ─────────────────────────────────────────────
    def get_short(self, key: str, now: float) -> tuple[int, bytes] | None:
        with self._lock:
            row = self._conn.execute("SELECT status, body, expires FROM short WHERE key = ?", (key,)).fetchone()
        if not row or row[2] <= now:
            return None
        return row[0], row[1]

    def put_short(self, key: str, status: int, body: bytes, expires: float) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO short(key, status, body, expires) VALUES (?, ?, ?, ?)",
                               (key, status, body, expires))

    # ── 하루 계수 ──────────────────────────────────────────────────
    def take_budget(self, day: str, scopes: list[tuple[str, int]]) -> bool:
        """범위마다 (이름, 상한) — 하나라도 상한이면 아무것도 안 세고 False. 전부 한 트랜잭션."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for scope, cap in scopes:
                    row = self._conn.execute("SELECT n FROM budget WHERE day = ? AND scope = ?",
                                             (day, scope)).fetchone()
                    if (row[0] if row else 0) >= cap:
                        self._conn.execute("ROLLBACK")
                        return False
                for scope, _cap in scopes:
                    self._conn.execute(
                        "INSERT INTO budget(day, scope, n) VALUES (?, ?, 1) "
                        "ON CONFLICT(day, scope) DO UPDATE SET n = n + 1", (day, scope))
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def budget_used(self, day: str, scope: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT n FROM budget WHERE day = ? AND scope = ?", (day, scope)).fetchone()
        return row[0] if row else 0


def dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
