"""봇의 조회 파이프라인 — API·DB를 만지는 유일한 곳.

GUI(app_main.MatchLoader)와 결정적으로 다른 점이 둘 있다.

1. **전량 백필을 하지 않는다.** GUI 는 처음 보는 계정이면 API 가 주는
   최근 100경기를 페이지를 넘겨가며 전부 받는다(수천 건이면 수 분). 채팅방
   에서 그 대기를 시키면 봇으로 못 쓴다. 여기서는 첫 페이지 LIMIT 건만 받고,
   나머지는 이미 DB 에 쌓여 있는 만큼으로 답한다.

2. **store.upsert_account 를 부르지 않는다.** GUI 의 "최근 검색" 목록이
   채팅방 사람들 닉네임으로 뒤덮이면 안 된다. 봇이 받은 경기는 matches/
   match_players 에만 들어가고 accounts 는 건드리지 않는다.

표시는 API 응답이 아니라 DB 에서 다시 읽는다 — GUI 로 예전에 쌓아 둔
경기까지 같이 집계된다.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

import config
import store
from models import MatchSummary, parse_match
from nexon_api import FCOnlineAPI, NexonAPIError

# 한 번에 API 로 새로 받을 최근 경기 수. 처음 보는 계정도 몇 초 안에 끝나야
# 채팅봇으로 쓸 만하다 — 2026-07-29 사용자와 20 으로 합의.
LOOKUP_LIMIT = 20

# 닉네임 → ouid 는 잘 안 변하는데(구단주명을 바꾸면 바뀌지만 드물다) 명령마다
# 조회하면 API 를 한 번씩 더 쓴다. 같은 방에서 같은 사람을 반복해 찾는 게
# 채팅봇의 기본 사용 패턴이라 짧게 캐시한다.
OUID_TTL_SEC = 3600

# 상세를 받는 동시 요청 수. GUI 는 6 이지만 봇은 한 번에 20건이면 충분하고,
# 여러 방에서 동시에 명령이 들어올 수 있어 호출량을 아끼려고 낮게 잡는다.
DETAIL_WORKERS = 4

# 조회 결과 캐시. 병목은 API 가 아니라 DB 에 쌓인 경기를 JSON 으로 되읽는
# 비용이다 — 실측 3,768경기에 5.0초(경기 payload 가 20KB대라 파싱이 지배적).
# 채팅방에서는 !전적 뒤에 !선수·!상대 를 잇달아 치는 게 보통이라, 그 사이만
# 재사용해도 두 번째 명령부터는 즉답이 된다. 짧게 잡아 새 경기가 곧 반영되게
# 한다(방별 쿨다운 10초보다 조금 긴 정도).
LOOKUP_TTL_SEC = 120


@dataclass
class Lookup:
    """한 계정 조회 결과 — 포맷터가 필요한 것만 담는다."""
    nickname: str          # API 가 알려준 정식 표기(입력한 대소문자와 다를 수 있다)
    ouid: str
    basic: dict
    matches: list[MatchSummary]   # 최신순
    details: list[dict] = field(default_factory=list)  # 원본 — stats.* 집계용
    fetched: int = 0       # 이번에 API 로 새로 받은 경기 수


class BotService:
    """스레드 안전. HTTP 서버가 요청마다 스레드를 쓰므로 공유 상태는 잠근다."""

    def __init__(self, api: FCOnlineAPI | None = None,
                 match_type: int | None = None,
                 limit: int = LOOKUP_LIMIT):
        self._api = api or FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)
        self._match_type = (config.DEFAULT_MATCH_TYPE
                            if match_type is None else match_type)
        self._limit = limit
        self._lock = threading.Lock()
        self._ouid_cache: dict[str, tuple[str, float]] = {}
        self._meta_cache: dict[str, dict] = {}
        self._lookup_cache: dict[str, tuple[Lookup, float]] = {}

    # ── 계정 ──────────────────────────────────────────────────────────
    def ouid(self, nickname: str) -> str:
        key = nickname.strip().lower()
        now = time.time()
        with self._lock:
            hit = self._ouid_cache.get(key)
            if hit and now - hit[1] < OUID_TTL_SEC:
                return hit[0]
        try:
            ouid = self._api.get_ouid(nickname.strip())
        except NexonAPIError as e:
            # 없는 구단주명이면 넥슨은 OPENAPI00004("요청 파라미터가 잘못됐습니다")
            # 를 준다. 그 문구는 이 문맥에서 사람에게 아무 정보도 주지 못한다 —
            # 오타를 고칠 수 있게 바꿔 준다.
            if e.code in ("OPENAPI00004", "OPENAPI00009"):
                raise NexonAPIError(
                    f"'{nickname.strip()}' 구단주명을 찾지 못했습니다."
                    " 띄어쓰기·대소문자를 확인해 주세요.",
                    code=e.code, status=e.status) from e
            raise
        with self._lock:
            self._ouid_cache[key] = (ouid, now)
        return ouid

    # ── 조회 ──────────────────────────────────────────────────────────
    def lookup(self, nickname: str, limit: int | None = None) -> Lookup:
        key = nickname.strip().lower()
        now = time.time()
        with self._lock:
            hit = self._lookup_cache.get(key)
        if hit and now - hit[1] < LOOKUP_TTL_SEC:
            # 캐시로 답할 땐 "새로 받은 경기"를 0 으로 — 그 건수는 이번 조회에서
            # 실제로 받아온 수를 뜻하고, 같은 수를 두 번 알리면 거짓말이 된다.
            return replace(hit[0], fetched=0)

        limit = limit or self._limit
        ouid = self.ouid(nickname)
        basic = self._api.get_user_basic(ouid)

        conn = store.open_db(config.DB_PATH)  # 커넥션은 스레드마다 따로 (store 규칙)
        try:
            known = store.known_ids(conn, ouid, self._match_type)
            ids = self._api.get_match_ids(ouid, self._match_type, 0, limit)
            todo = [i for i in ids if i not in known]

            fresh: list[dict] = []
            if todo:
                with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
                    for detail in pool.map(self._safe_detail, todo):
                        if detail is not None:
                            fresh.append(detail)
            store.save_matches(conn, fresh)

            details = store.load_details(conn, ouid, self._match_type)
        finally:
            conn.close()

        matches = [m for m in (parse_match(d, ouid) for d in details) if m]
        matches.sort(key=lambda m: m.match_date or 0, reverse=True)
        result = Lookup(
            nickname=basic.get("nickname") or nickname.strip(),
            ouid=ouid, basic=basic, matches=matches, details=details,
            fetched=len(fresh),
        )
        with self._lock:
            # 입력 표기와 정식 표기 양쪽으로 넣어 둔다 — 방 기억이 정식 표기를
            # 쓰므로(commands._lookup) 그때도 같은 캐시를 맞힌다.
            for k in {key, result.nickname.lower()}:
                self._lookup_cache[k] = (result, now)
        return result

    def _safe_detail(self, match_id: str) -> dict | None:
        """한 경기가 실패해도 나머지 조회를 죽이지 않는다(MatchLoader 와 같은 패턴)."""
        try:
            return self._api.get_match_detail(match_id)
        except (NexonAPIError, Exception):
            return None

    # ── 채팅방 사용자 등록 ────────────────────────────────────────────
    def register(self, room: str, sender: str, nickname: str) -> None:
        """기본 계정 등록. 없는 구단주명이면 NexonAPIError 로 막는다 —
        오타를 등록해 두면 이후 명령이 전부 실패해서 원인을 찾기 어렵다."""
        nickname = nickname.strip()
        self.ouid(nickname)
        conn = store.open_db(config.DB_PATH)
        try:
            store.set_bot_user(conn, room, sender, nickname)
        finally:
            conn.close()

    def registered(self, room: str, sender: str) -> str | None:
        conn = store.open_db(config.DB_PATH)
        try:
            return store.get_bot_user(conn, room, sender)
        finally:
            conn.close()

    def unregister(self, room: str, sender: str) -> bool:
        conn = store.open_db(config.DB_PATH)
        try:
            return store.clear_bot_user(conn, room, sender)
        finally:
            conn.close()

    # ── 메타데이터 ────────────────────────────────────────────────────
    def meta_map(self, name: str, key: str, value: str) -> dict:
        """spid/spposition 같은 코드→이름 표. 8만 건짜리(spid)를 명령마다 파싱하면
        느려서 프로세스 수명 동안 들고 있는다. 실패해도 빈 표로 돌려 조회는 살린다."""
        with self._lock:
            hit = self._meta_cache.get(name)
        if hit is not None:
            return hit
        try:
            rows = self._api.get_meta(name)
            out = {r[key]: r[value] for r in rows if key in r and value in r}
        except Exception:
            out = {}
        with self._lock:
            self._meta_cache[name] = out
        return out

    def player_names(self) -> dict:
        return self.meta_map("spid", "id", "name")

    def position_names(self) -> dict:
        return self.meta_map("spposition", "spposition", "desc")

    def division_names(self) -> dict:
        return self.meta_map("division", "divisionId", "divisionName")
