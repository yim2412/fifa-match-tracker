"""넥슨 오픈API — EA SPORTS FC 온라인 클라이언트.

엔드포인트 경로는 전부 이 파일 위쪽 상수에 모아 뒀다.
공식 문서(https://openapi.nexon.com/ko/game/fconline/)와 어긋나면 여기만 고치면 된다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://open.api.nexon.com"
META_URL = f"{BASE_URL}/static/fconline/meta"

# get_meta(spid/division/seasonid 등)는 한 번 받으면 디스크에 영구 캐시했는데,
# 넥슨이 새 시즌 카드·선수·등급을 추가해도 이 앱이 그걸 영영 모르는 문제가
# 있었다. team_colors 캐시(store.TEAM_COLOR_TTL_DAYS=30)와 같은 방식으로
# 파일 mtime 기준 TTL을 둔다 — spid.json 이 8만 건대라 너무 짧게 잡으면
# 재다운로드 낭비가 크고, 새 시즌은 보통 한 달 단위로 나오므로 30일보다
# 짧은 14일로 잡아 새 시즌을 너무 오래 놓치지 않게 한다.
META_TTL_DAYS = 14

EP_ID = "/fconline/v1/id"                    # 닉네임 → ouid
EP_USER_BASIC = "/fconline/v1/user/basic"    # 계정 기본 정보
EP_MAX_DIVISION = "/fconline/v1/user/maxdivision"  # 역대 최고 등급
EP_USER_MATCH = "/fconline/v1/user/match"    # 매치 id 목록
EP_MATCH_DETAIL = "/fconline/v1/match-detail"  # 매치 상세

# 넥슨 에러코드 → 사람이 읽는 말
ERROR_MESSAGES = {
    "OPENAPI00001": "넥슨 서버 내부 오류입니다. 잠시 후 다시 시도하세요.",
    "OPENAPI00004": "요청 파라미터가 잘못됐습니다.",
    "OPENAPI00005": "API 키가 유효하지 않습니다. .env 의 NEXON_API_KEY를 확인하세요.",
    "OPENAPI00007": "API 호출량을 초과했습니다. 잠시 후 다시 시도하세요.",
    "OPENAPI00009": "존재하지 않는 데이터입니다.",
    "OPENAPI00010": "게임 점검 중입니다.",
    "OPENAPI00011": "API 점검 중입니다.",
}


class NexonAPIError(Exception):
    """API가 에러를 돌려줬거나 네트워크가 실패한 경우."""

    def __init__(self, message: str, code: str = "", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class FCOnlineAPI:
    def __init__(self, api_key: str, timeout: int = 10, cache_dir: Path | None = None):
        if not api_key:
            raise NexonAPIError("API 키가 비어 있습니다. .env 파일에 NEXON_API_KEY를 넣어주세요.")
        self._session = requests.Session()
        self._session.headers.update({"x-nxopen-api-key": api_key})
        self._timeout = timeout
        self._cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 공통 ──────────────────────────────────────────────────────────
    def _get(self, path: str, **params: Any) -> Any:
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            try:
                res = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as e:
                if attempt == 2:
                    raise NexonAPIError(f"네트워크 오류: {e}") from e
                time.sleep(1.0 * (attempt + 1))
                continue

            if res.status_code == 200:
                return res.json()

            code, msg = self._parse_error(res)
            # 호출량 초과·일시적 서버 오류는 백오프 후 재시도
            if res.status_code in (429, 500, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise NexonAPIError(msg, code=code, status=res.status_code)

        raise NexonAPIError("요청에 반복 실패했습니다.")

    @staticmethod
    def _parse_error(res: requests.Response) -> tuple[str, str]:
        code = ""
        try:
            body = res.json().get("error", {})
            code = body.get("name", "")
            raw = body.get("message", "")
        except Exception:
            raw = res.text[:200]
        msg = ERROR_MESSAGES.get(code) or raw or f"HTTP {res.status_code}"
        return code, msg

    # ── 계정 ──────────────────────────────────────────────────────────
    def get_ouid(self, nickname: str) -> str:
        data = self._get(EP_ID, nickname=nickname)
        ouid = data.get("ouid")
        if not ouid:
            raise NexonAPIError(f"'{nickname}' 계정을 찾지 못했습니다.")
        return ouid

    def get_user_basic(self, ouid: str) -> dict:
        return self._get(EP_USER_BASIC, ouid=ouid)

    def get_max_division(self, ouid: str) -> list[dict]:
        data = self._get(EP_MAX_DIVISION, ouid=ouid)
        return data if isinstance(data, list) else []

    # ── 매치 ──────────────────────────────────────────────────────────
    def get_match_ids(self, ouid: str, matchtype: int = 50,
                      offset: int = 0, limit: int = 20) -> list[str]:
        data = self._get(EP_USER_MATCH, ouid=ouid, matchtype=matchtype,
                         offset=offset, limit=limit)
        return data if isinstance(data, list) else []

    def get_match_detail(self, match_id: str) -> dict:
        """매치 상세. 이미 끝난 경기는 내용이 안 변하므로 디스크에 캐시한다."""
        cached = self._cache_read(match_id)
        if cached is not None:
            return cached
        data = self._get(EP_MATCH_DETAIL, matchid=match_id)
        self._cache_write(match_id, data)
        return data

    # ── 메타데이터 ────────────────────────────────────────────────────
    def get_meta(self, name: str) -> list[dict]:
        """name: matchtype | division | seasonid | spid | spposition
        — 인증 헤더 없이도 열리는 정적 파일. 잘 안 변하므로 디스크에 캐시한다
        (spid 는 8만 건이 넘어 매번 받으면 낭비).
        """
        cached = self._meta_read(name)
        if cached is not None:
            return cached
        try:
            res = requests.get(f"{META_URL}/{name}.json", timeout=self._timeout)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            raise NexonAPIError(f"메타데이터({name}) 조회 실패: {e}") from e
        self._meta_write(name, data)
        return data

    def _meta_path(self, name: str) -> Path | None:
        if not self._cache_dir:
            return None
        return self._cache_dir / f"meta_{''.join(c for c in name if c.isalnum())}.json"

    def _meta_read(self, name: str) -> list[dict] | None:
        p = self._meta_path(name)
        if p and p.exists():
            age_days = (time.time() - p.stat().st_mtime) / 86400
            if age_days > META_TTL_DAYS:
                return None  # 오래된 캐시 — 없는 셈 치고 새로 받는다
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _meta_write(self, name: str, data: list[dict]) -> None:
        p = self._meta_path(name)
        if p:
            try:
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    # ── 캐시 ──────────────────────────────────────────────────────────
    def _cache_path(self, match_id: str) -> Path | None:
        if not self._cache_dir:
            return None
        safe = "".join(c for c in match_id if c.isalnum())
        return self._cache_dir / f"{safe}.json"

    def _cache_read(self, match_id: str) -> dict | None:
        p = self._cache_path(match_id)
        if p and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None  # 깨진 캐시는 무시하고 다시 받는다
        return None

    def _cache_write(self, match_id: str, data: dict) -> None:
        p = self._cache_path(match_id)
        if p:
            try:
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass  # 캐시 실패가 조회를 막으면 안 된다
