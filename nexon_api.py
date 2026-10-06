"""넥슨 오픈API — EA SPORTS FC 온라인 클라이언트.

엔드포인트 경로는 전부 이 파일 위쪽 상수에 모아 뒀다.
공식 문서(https://openapi.nexon.com/ko/game/fconline/)와 어긋나면 여기만 고치면 된다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter

BASE_URL = "https://open.api.nexon.com"
META_URL = f"{BASE_URL}/static/fconline/meta"

# get_meta(spid/division/seasonid 등)는 한 번 받으면 디스크에 영구 캐시했는데,
# 넥슨이 새 시즌 카드·선수·등급을 추가해도 이 앱이 그걸 영영 모르는 문제가
# 있었다. "받은 지 N일" 대신 "이번 주 금요일이 지났나"로 기준을 잡는다 —
# 캐시가 이번 주 금요일 0시 이전에 받아둔 거면 낡은 것으로 치고 새로 받는다.
# spid.json 이 8만 건대라 매 실행마다 새로 받으면 낭비지만, 주 단위로는
# 넉넉히 갱신되게 하려는 절충이다. 0=월요일 … 4=금요일.
META_REFRESH_WEEKDAY = 4


def _week_boundary(now: datetime | None = None) -> datetime:
    """이번 주(또는 지난 주) META_REFRESH_WEEKDAY 요일의 자정."""
    now = now or datetime.now()
    delta_days = (now.weekday() - META_REFRESH_WEEKDAY) % 7
    return datetime.combine((now - timedelta(days=delta_days)).date(), datetime.min.time())

EP_ID = "/fconline/v1/id"                    # 닉네임 → ouid
EP_USER_BASIC = "/fconline/v1/user/basic"    # 계정 기본 정보
EP_MAX_DIVISION = "/fconline/v1/user/maxdivision"  # 역대 최고 등급
EP_USER_MATCH = "/fconline/v1/user/match"    # 매치 id 목록
EP_MATCH_DETAIL = "/fconline/v1/match-detail"  # 매치 상세
# 거래 기록 — ouid 를 **무시하고 API 키 주인 계정**의 거래만 준다(2026-10-06 실측, ROADMAP 1.4.1 R1·R2).
# 그래서 get_trades 는 ouid 를 받지 않는다 — 받는 척하면 남의 거래로 읽힌다. ouid 없이 불러도 200(같은 날 실측).
EP_USER_TRADE = "/fconline/v1/user/trade"
TRADE_KINDS = ("buy", "sell")
TRADE_PAGE = 100  # 한 번에 받는 상한 — 200 이면 OPENAPI00004(R4)
# 랭커 기록 — 그 (카드, 포지션)을 쓴 상위 랭커들의 **경기당 평균**(값이 matchCount 분의 1 단위 — 2026-10-06 실측,
# 다시 나누면 조용히 틀린다). 데이터 없는 쌍은 응답에서 빠진다(오류 아님). 쌍 목록은 URL 에 JSON 으로 실려
# 한 요청 상한이 URL 길이다 — 9자리 spid 로 81쌍(URL 3,691자)까지 200, 82쌍부터 400 · 100쌍 414(같은 날 실측).
EP_RANKER_STATS = "/fconline/v1/ranker-stats"
RANKER_STATS_BATCH = 50  # 한 카드 × 포지션 28개(N2)가 한 번에 · 81 상한에 여유

# 넥슨 에러코드 → 사람이 읽는 말
ERROR_MESSAGES = {
    "OPENAPI00001": "넥슨 서버 내부 오류입니다. 잠시 후 다시 시도하세요.",
    "OPENAPI00004": "요청 파라미터가 잘못됐습니다.",
    "OPENAPI00005": "API 키가 유효하지 않습니다. 키를 다시 확인하세요.",
    "OPENAPI00007": "API 호출량을 초과했습니다. 잠시 후 다시 시도하세요.",
    "OPENAPI00009": "존재하지 않는 데이터입니다.",
    "OPENAPI00010": "게임 점검 중입니다.",
    "OPENAPI00011": "API 점검 중입니다.",
}

# 키 확인 — 2026-10-02 실측: 틀린 키는 OPENAPI00005, 맞는 키로 없는 닉네임을 물으면
# OPENAPI00004 가 온다. 그래서 "00005 가 아니면 통과"로 판정한다(닉네임 존재 여부와 무관).
KEY_INVALID_CODE = "OPENAPI00005"
# 호출 한도(429). 개발 단계 키는 초당 5·하루 1,000건, 서비스 단계는 초당 500·하루 2천만건
# (공식 FAQ 「API 이용 제한이 있나요?」). 첫 조회가 3천 건을 넘어 개발 단계 키로는 못 끝낸다
# (2026-10-02 실측: 빈 데이터 첫 검색 3,124건 · 서비스 키 113초 · 429 0건 — 동시 6개.
#  2026-10-04 동시 24개로 3,130건 38초 · 429 0건, 같은 때 6개는 119초).
QUOTA_CODE = "OPENAPI00007"
# 동시에 열어 둘 연결 수 — 상세 조회를 여러 스레드로 할 때(app_main.DETAIL_WORKERS) 그보다 커야 한다.
# requests 기본은 10이라 그 이상이면 연결을 버리고 다시 맺는다.
HTTP_POOL_SIZE = 32
KEY_CHECK_NICKNAME = "키확인용"
KEY_ISSUE_URL = "https://openapi.nexon.com/"

# 오픈API 약관 제6조④ — 결과 데이터에 출처를 명시. 문구는 공식 가이드(API 사용하기)가 정한 것.
ATTRIBUTION = "Data based on NEXON Open API"


class NexonAPIError(Exception):
    """API가 에러를 돌려줬거나 네트워크가 실패한 경우."""

    def __init__(self, message: str, code: str = "", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def check_key(api_key: str, timeout: int = 10) -> str | None:
    """키가 쓸 만하면 None, 아니면 사람이 읽을 이유를 돌려준다."""
    api_key = api_key.strip()
    if not api_key:
        return "키를 입력하세요."
    try:
        FCOnlineAPI(api_key, timeout=timeout)._get(EP_ID, nickname=KEY_CHECK_NICKNAME)
    except NexonAPIError as e:
        # 네트워크 실패·호출량 초과·서버 오류는 키가 맞는지 모르는 상태라 통과시키지 않는다.
        if e.code == KEY_INVALID_CODE or e.status is None or e.status >= 429:
            return e.message
    return None


class FCOnlineAPI:
    def __init__(self, api_key: str, timeout: int = 10, cache_dir: Path | None = None):
        if not api_key:
            raise NexonAPIError("API 키가 비어 있습니다. .env 파일에 NEXON_API_KEY를 넣어주세요.")
        self._session = requests.Session()
        self._session.headers.update({"x-nxopen-api-key": api_key})
        self._session.mount("https://", HTTPAdapter(pool_maxsize=HTTP_POOL_SIZE))
        self._timeout = timeout
        self.throttled = 0  # 429(호출 한도)를 받은 횟수 — 재시도로 넘어간 것도 센다. 로더가 동시 요청을 줄일 신호
        self._cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def set_key(self, api_key: str) -> None:
        self._session.headers.update({"x-nxopen-api-key": api_key})

    # ── 공통 ──────────────────────────────────────────────────────────
    def _get(self, path: str, attempts: int = 3, **params: Any) -> Any:
        url = f"{BASE_URL}{path}"
        last = attempts - 1
        for attempt in range(attempts):
            try:
                res = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as e:
                if attempt == last:
                    raise NexonAPIError(f"네트워크 오류: {e}") from e
                time.sleep(1.0 * (attempt + 1))
                continue

            if res.status_code == 200:
                return res.json()

            code, msg = self._parse_error(res)
            if res.status_code == 429:
                self.throttled += 1
            # 호출량 초과·일시적 서버 오류는 백오프 후 재시도
            if res.status_code in (429, 500, 503) and attempt < last:
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
        # 재시도 없이 한 번만 — 부가 정보라 429·5xx 에 최대 4.5초 잠들며 검색 뒤끝을 붙잡을 이유가 없다.
        data = self._get(EP_MAX_DIVISION, attempts=1, ouid=ouid)
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

    # ── 거래 ──────────────────────────────────────────────────────────
    def get_trades(self, tradetype: str, offset: int = 0, limit: int = TRADE_PAGE) -> list[dict]:
        """키 주인 계정의 거래(최신순) — tradetype 은 buy | sell. 끝을 지나면 빈 목록."""
        data = self._get(EP_USER_TRADE, tradetype=tradetype, offset=offset, limit=limit)
        return data if isinstance(data, list) else []

    # ── 랭커 기록 ──────────────────────────────────────────────────────
    def get_ranker_stats(self, matchtype: int, pairs) -> list[dict]:
        """(spid, 포지션) 쌍들의 랭커 경기당 평균 — RANKER_STATS_BATCH 개씩 나눠 묻는다. 데이터 없는 쌍은 결과에 없다."""
        pairs = list(pairs)
        out: list[dict] = []
        for i in range(0, len(pairs), RANKER_STATS_BATCH):
            body = json.dumps([{"id": int(s), "po": int(p)} for s, p in pairs[i:i + RANKER_STATS_BATCH]],
                              separators=(",", ":"))
            data = self._get(EP_RANKER_STATS, matchtype=matchtype, players=body)
            if isinstance(data, list):
                out.extend(r for r in data if isinstance(r, dict))
        return out

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
            cached_at = datetime.fromtimestamp(p.stat().st_mtime)
            if cached_at < _week_boundary():
                return None  # 이번 주 갱신 기준일 이전 캐시 — 없는 셈 치고 새로 받는다
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

    def forget_details(self, match_ids) -> None:
        """DB 에 저장한 경기의 디스크 캐시를 지운다 — 그 뒤로는 DB 가 정본이다."""
        for mid in match_ids:
            p = self._cache_path(mid) if mid else None
            if p:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass  # 열려 있거나 권한 — 다음 정리(prune_detail_cache) 때 다시

    def prune_detail_cache(self, stored_ids: set[str], stop=lambda: False) -> tuple[int, int]:
        """캐시 폴더에서 이미 DB 에 있는 경기 파일만 지운다 → (지운 수, 바이트).

        v1.0.1 전에는 받은 경기를 캐시와 DB 에 둘 다 남겨 2만 개·407MB 가 쌓였다(2026-10-02 실측).
        메타 파일(meta_*.json)과 DB 에 없는 경기(한도에 걸려 아직 못 넣은 것 — 이어 받기에 쓴다)는 둔다.
        """
        if not self._cache_dir or not self._cache_dir.is_dir():
            return 0, 0
        keep_safe = {"".join(c for c in i if c.isalnum()) for i in stored_ids}
        n = size = 0
        for p in self._cache_dir.glob("*.json"):
            if stop():  # 창을 닫는 중 — 남은 건 다음에 켤 때
                break
            if p.stem.startswith("meta_") or p.stem not in keep_safe:
                continue
            try:
                s = p.stat().st_size
                p.unlink()
                n += 1
                size += s
            except OSError:
                continue
        return n, size

    def _cache_write(self, match_id: str, data: dict) -> None:
        p = self._cache_path(match_id)
        if p:
            try:
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass  # 캐시 실패가 조회를 막으면 안 된다
