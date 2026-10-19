"""중앙 서버 상수 — 전부 여기(docs/CENTRAL_SERVER.md 11절). 표시 없는 숫자는 시작값이고 ⓐ 실측으로 고친다.

config 를 import 하지 않는다 — config 가 import 때 사용자 .env 를 읽어 키를 서버 환경에 싣는다.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta, timezone
from pathlib import Path

import nexon_api

KST = timezone(timedelta(hours=9))  # 하루 예산·메타 금요일 경계 — Oracle(UTC)로 옮겨도 9시간 밀리지 않게

HOST = "127.0.0.1"  # ⓐ 는 이 PC 안에서만 — 바깥 길(터널·도메인)은 ⓑ
PORT = int(os.getenv("FIFA_SERVER_PORT", "8765"))
KEY_VAR = "NEXON_SERVICE_KEY"


def _default_db() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "피파전적관리" / "server" / "server.db"  # 저장소 밖 — 남의 경기가 커밋되지 않게


DB_PATH = Path(os.getenv("FIFA_SERVER_DB") or _default_db())
ENV_FILE = DB_PATH.parent / "server.env"  # 서비스 키 — run_local 이 읽어 이 프로세스 환경에만

UPSTREAM_CONCURRENCY = 24     # 10-04 직접 실측(3,130건 38초 · 429 0)과 같은 조건
UPSTREAM_TIMEOUT_S = 10
UPSTREAM_ATTEMPTS = 3         # 넥슨 429·500·503 을 서버가 다시 해 보는 횟수(앱 _get 과 같다)
DAILY_BUDGET = 1_000_000      # 서버가 하루 넥슨에 보내는 상한(서비스 키 2천만의 5% — dev_matches 몫과 겹칠 때를 생각해 낮게)
IP_DAILY = 10_000             # IP 하나가 하루 넥슨까지 보내게 하는 몫 — 첫 검색(3,124건 · 10-02 실측) 세 번
RETRY_AFTER_S = 60            # SERVER_BUSY 에 실어 보내는 Retry-After

# 경로별 저장(초). match-detail 은 영구라 여기 없다. ranker-stats 는 앱도 하루 캐시(ranker_stats)
TTL_S = {
    nexon_api.EP_ID: 600,            # 닉네임 변경이 드물지만 바뀌면 남의 ouid 를 줄 수 있어 짧게
    nexon_api.EP_USER_MATCH: 60,     # 새 경기 확인용 — 길면 방금 한 경기가 안 보인다
    nexon_api.EP_USER_BASIC: 3600,
    nexon_api.EP_MAX_DIVISION: 3600,
    nexon_api.EP_RANKER_STATS: 86400,
}
NEG_CODES = frozenset({"OPENAPI00004", "OPENAPI00009"})  # 요청 자체가 틀렸다 — 짧게 기억해 예산을 태우는 길을 막는다
NEG_TTL_DETAIL_S = 60   # 막 끝난 경기의 상세가 언제부터 준비되는지 안 쟀다 — 길면 방금 한 경기가 가려진다
NEG_TTL_S = 600

META_NAMES = frozenset({"matchtype", "division", "seasonid", "spid", "spposition"})
RANKER_PAIRS_MAX = 81   # 넥슨 URL 상한 — nexon_api.RANKER_STATS_BATCH 주석의 실측

# 경로 → 허용 인자와 검사. 허용 밖 인자는 버린다(캐시 키에도 안 들어간다)
_HEX24 = re.compile(r"[0-9a-f]{24}")
_HEX32 = re.compile(r"[0-9a-f]{32}")


def _int_range(lo: int, hi: int):
    def ok(v: str) -> bool:
        return v.isdigit() and lo <= int(v) <= hi
    return ok


def _nickname(v: str) -> bool:
    return 1 <= len(v) <= 30 and all(c.isprintable() for c in v)


def _players(v: str) -> bool:
    import json
    try:
        rows = json.loads(v)
    except ValueError:
        return False
    return (isinstance(rows, list) and 0 < len(rows) <= RANKER_PAIRS_MAX
            and all(isinstance(r, dict) and set(r) == {"id", "po"}
                    and all(type(r[k]) is int for k in r) for r in rows))


PARAMS = {
    nexon_api.EP_ID: {"nickname": _nickname},
    nexon_api.EP_USER_BASIC: {"ouid": _HEX32.fullmatch},
    nexon_api.EP_MAX_DIVISION: {"ouid": _HEX32.fullmatch},
    nexon_api.EP_USER_MATCH: {"ouid": _HEX32.fullmatch, "matchtype": _int_range(0, 999),
                              "offset": _int_range(0, 100_000), "limit": _int_range(1, 100)},
    nexon_api.EP_MATCH_DETAIL: {"matchid": _HEX24.fullmatch},
    nexon_api.EP_RANKER_STATS: {"matchtype": _int_range(0, 999), "players": _players},
}
REQUIRED = {
    nexon_api.EP_ID: ("nickname",),
    nexon_api.EP_USER_BASIC: ("ouid",),
    nexon_api.EP_MAX_DIVISION: ("ouid",),
    nexon_api.EP_USER_MATCH: ("ouid", "matchtype"),
    nexon_api.EP_MATCH_DETAIL: ("matchid",),
    nexon_api.EP_RANKER_STATS: ("matchtype", "players"),
}
# user/trade 는 일부러 없다 — 넥슨이 키 주인 거래만 준다(서버 키로 물으면 운영자 거래가 전원에게 나간다)

# run_local: 이 프로세스가 보이면 서버를 안 띄우고, 도는 중 보이면 내린다. 비어 있으면 띄우기를 거부한다
# (이름이 틀리거나 비면 감시가 조용히 안 걸린다). 이름은 이 PC 에서 게임을 켜고 확인해 채운다.
GAME_PROCESSES: tuple[str, ...] = ()
WATCH_S = 30
