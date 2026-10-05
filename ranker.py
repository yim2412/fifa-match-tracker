"""넥슨 데이터센터에서 감독모드 랭킹을 가져온다.

오픈API(JSON)에는 순위·구단가치·랭킹점수가 없다. 넥슨 공식 데이터센터
웹페이지(HTML)에는 있어서 여기서 긁어온다.

    https://fconline.nexon.com/datacenter/rank_inner?rt=manager&strCharacterName=<닉>

주의: 이건 JSON API 가 아니라 HTML 스크래핑이다. 넥슨이 페이지 구조(class 이름)를
바꾸면 파싱이 깨진다. 그래서 URL·class 지식을 전부 이 파일에만 둔다 —
깨지면 실제 응답과 대조해 아래 상수·정규식만 고치면 된다.
공식 데이터는 매시각 갱신되고, 여기 전적은 감독모드 통산(오픈API 의 최근
3천 경기보다 많다)이지만 요약 숫자일 뿐 경기별 상세는 아니다.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from html import unescape

import requests

import config

RANK_URL = "https://fconline.nexon.com/datacenter/rank_inner"
# UA 는 config.WEB_USER_AGENT(앱 이름) — 비우면 넥슨이 일부 페이지에 500 을 준다.
# no-cache 류 헤더는 중간 프록시가 예전 응답을 재활용하는 걸 막는 안전장치다
# (Cloudflare 는 이 페이지를 DYNAMIC 으로 표시해 자체 캐싱은 안 하는 걸 확인했지만,
# 검색할 때마다 최신값을 받는다는 걸 보장하려고 남겨 둔다).
_HEADERS = {"User-Agent": config.WEB_USER_AGENT,
            "Cache-Control": "no-cache", "Pragma": "no-cache"}

# 팀컬러 조회(TeamColorLoader)가 상대 수십~수백 명을 연달아 부르는데, 매번
# requests.get() 을 새로 쓰면 매 호출마다 TCP/TLS 핸드셰이크가 다시 열린다.
# Session 으로 연결을 재사용하면 넥슨 서버가 받는 요청 자체는 그대로면서
# 체감 속도만 빨라진다. GET 만 하고 세션 상태를 바꾸지 않으니 여러 스레드가
# 공유해도 안전하다(requests 문서 권고 범위).
_session = requests.Session()
_session.headers.update(_HEADERS)

# 넥슨 홈페이지 요청은 전부 web_get 을 거친다(seasons·playerinfo 포함) — 수집(작업자 6)·팀컬러(8)·검색이
# 한꺼번에 돌아도 한 프로세스에서 동시에 나가는 요청이 config.RANK_MAX_CONCURRENT 를 넘지 않게.
# 연결 풀(기본 10)이 아니라 이 세마포어가 동시 수를 정한다.
_web_slots = threading.BoundedSemaphore(config.RANK_MAX_CONCURRENT)


def web_get(session: requests.Session, url: str, method: str = "get", **kw) -> requests.Response:
    """session.get/post 를 동시 상한 안에서 부른다. 예외는 그대로 올린다(감싸는 건 호출부)."""
    with _web_slots:
        return getattr(session, method)(url, **kw)

# 데이터 행에서 값을 뽑는 정규식. class 이름이 바뀌면 여기만 고친다.
_RANK_NO = re.compile(r'class="td rank_no">\s*([\d,]+)\s*<')
_LEVEL = re.compile(r'class="lv">.*?class="txt">\s*(\d+)\s*<', re.S)
_PRICE = re.compile(r'class="price"[^>]*\balt="([\d,]+)"[^>]*>\s*([^<]+?)\s*<')
_ELO = re.compile(r'class="td rank_r_win_point">\s*([\d.]+)\s*<')
_WINRATE = re.compile(r'class="top">\s*([\d.]+%)\s*<')
_WDL = re.compile(r'class="bottom">\s*([\d,]+)\s*<em>\|</em>\s*([\d,]+)\s*'
                  r'<em>\|</em>\s*([\d,]+)\s*<')
# 팀컬러: class="td team_color">...<span class="inner">이름 <small>(11명)</small>
_TEAM_COLOR = re.compile(
    r'class="td team_color">.*?class="inner">\s*([^<]+?)\s*<small>', re.S)
_NOT_RANKED = "순위 내 포함되어 있지"
_NAME = re.compile(r'class="name profile_pointer"[^>]*>\s*([^<]+?)\s*<')
# 프로필 번호 — 닉네임을 바꿔도 같은 사람을 잇는 열쇠(2026-10-04 실측 20/20 행에 있음)
_SN = re.compile(r'class="name profile_pointer"[^>]*\bdata-sn="(\d+)"')
_ROW_START = 'class="td rank_no"'
# 행 조각은 _ROW_START 바로 뒤에서 시작한다 — 그래서 _RANK_NO(앞에 class 를 요구)는 조각에서 None 이다.
_ROW_RANK = re.compile(r'^\s*>\s*([\d,]+)\s*<')
_COLOR_COUNT = re.compile(r'class="td team_color">.*?<small>\s*\((\d+)명\)', re.S)
_FORMATION = re.compile(r'class="td formation">\s*([^<]*?)\s*<')
_GRADE = re.compile(r'ico_rank(\d+)_m\.png')
_BEST_START = 'class="td rank_best"'
# 차단으로 보는 응답 — D6: 서로 다른 회차에 이어지면 수집을 스스로 끈다(rankcollect)
_BLOCK_STATUS = (403, 429)
_CF_MARKERS = ("challenge-platform", "cf-chl-", "Just a moment...")

# 랭킹 목록(닉네임 없이 n4pageno 만) — 한 페이지 20명, 1만 위까지 500페이지.
# 2026-10-02 실측: 501페이지 이후는 500페이지(9,981~10,000위)를 그대로 반복한다.
# 상대가 많을 때 상대마다 검색하는 대신 이걸 통째로 읽는다 — 진행 중 시즌 상대 1,491명
# 검색 환산 158초 vs 목록 500페이지 환산 49초(동시 8, 표본 80건씩). 덮는 범위(1만 위 안)는 같다.
RANK_PAGE_SIZE = 20
RANK_PAGES = 500


@dataclass
class RankerInfo:
    nickname: str
    rank: int | None = None          # 감독모드 순위. 랭킹 밖이면 None
    level: int | None = None
    team_value_text: str = ""        # "10경 3,411조"
    team_value: int = 0              # 정확한 원 단위 값
    elo: float | None = None         # 랭킹점수(ELO) = 화면의 '점수'
    win_rate: str = ""               # "47.7%"
    win: int = 0
    draw: int = 0
    lose: int = 0
    team_color: str = ""             # "가장 최근 사용" 기준(그 경기 당시 값 아님)
    profile_sn: int | None = None    # 데이터센터 프로필 번호(data-sn) — 닉네임이 바뀌어도 같은 사람

    @property
    def ranked(self) -> bool:
        return self.rank is not None

    @property
    def record_text(self) -> str:
        return f"{self.win}승 {self.draw}무 {self.lose}패"


@dataclass
class RankRow:
    """랭킹 목록 한 줄. 필수 칸(rank·profile_sn·nickname)이 None/"" 이면 구조가 바뀐 것이다(judge_page)."""
    rank: int | None
    profile_sn: int | None
    nickname: str
    level: int | None = None
    team_value: int | None = None    # 원 단위. 팀컬러가 없는 행은 parse_rank_page 에서 None 으로 본다
    team_value_text: str = ""
    elo: float | None = None
    win_rate: str = ""
    win: int = 0
    draw: int = 0
    lose: int = 0
    team_color: str = ""
    color_count: int | None = None   # 팀컬러 인원 "(11명)"
    formation: str = ""
    grade: int | None = None         # 지금 등급 아이콘 번호
    best_grade: int | None = None    # 역대 최고 등급
    prev_grade: int | None = None    # 이전 시즌 최고 등급


@dataclass
class RankPageResult:
    page: int
    rows: list[RankRow]
    date: str = ""                   # 응답 Date 헤더 — 회차가 정각을 걸쳤는지 가른다


class RankerError(Exception):
    pass


class RankBlocked(RankerError):
    """403·429·Cloudflare 확인 페이지 — 차단 의심."""


class RankOffline(RankerError):
    """연결 자체가 안 됨(절전 복귀·부팅 직후) — 실패 횟수에 세지 않는다."""


class RankStructureError(RankerError):
    """행은 왔는데 필수 칸이 비었거나 중간 쪽이 비었음 — 넥슨이 페이지 구조를 바꾼 것."""


def _to_int(s: str) -> int:
    try:
        return int(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def fetch_manager_rank(nickname: str, timeout: int = 10, season_no: int = 0) -> RankerInfo:
    """감독모드 랭킹을 가져온다. 랭킹 밖이면 ranked=False 로 돌아온다. season_no 0 = 지금 시즌
    (지난 시즌 최종 순위는 그 번호 — tools/check_predictions.py 가 쓴다).

    네트워크·파싱 실패는 RankerError. 호출부에서 잡아 카드를 비워도 앱은 산다.
    """
    if not config.WEB_DATA:
        raise RankerError(config.WEB_DATA_OFF_MSG)
    if not nickname:
        raise RankerError("닉네임이 비어 있습니다.")
    try:
        res = web_get(
            _session, RANK_URL,
            # _ts: 캐시 방지용 — URL 이 매번 달라야 어떤 프록시도 이전 응답을
            # 재사용하지 못한다. 넥슨이 이 값을 쓰지 않으니 결과엔 영향 없다.
            params={"rt": "manager", "strCharacterName": nickname,
                    "n4seasonno": season_no, "n4pageno": 1, "_ts": int(time.time() * 1000)},
            timeout=timeout)
        res.raise_for_status()
    except requests.RequestException as e:
        raise RankerError(f"랭킹 조회 실패: {e}") from e

    html = res.text
    info = RankerInfo(nickname=nickname)

    # 넥슨 웹 점검 페이지 — "랭킹 밖"과 마크업이 똑같이 rank_no 가 없어서,
    # 여기서 구분하지 않으면 점검 시간에 조회된 상대 전부가 "못 찾음"으로
    # TTL 기간 내내 잘못 캐시된다(2026-07-23 실제 점검 중 발견).
    if "점검 진행 중" in html or "fc_logo_inspection" in html:
        raise RankerError("넥슨 웹 점검 중입니다 — 잠시 후 다시 시도해주세요")

    if _NOT_RANKED in html or 'class="td rank_no"' not in html:
        return info  # 랭킹 1만 위 밖 — 순위 없음

    m = _RANK_NO.search(html)
    if m:
        info.rank = _to_int(m.group(1))
    m = _LEVEL.search(html)
    if m:
        info.level = _to_int(m.group(1))
    m = _PRICE.search(html)
    if m:
        info.team_value = _to_int(m.group(1))
        info.team_value_text = m.group(2).strip()
    m = _ELO.search(html)
    if m:
        try:
            info.elo = float(m.group(1))
        except ValueError:
            pass
    m = _WINRATE.search(html)
    if m:
        info.win_rate = m.group(1)
    m = _WDL.search(html)
    if m:
        info.win, info.draw, info.lose = (_to_int(m.group(i)) for i in (1, 2, 3))
    m = _TEAM_COLOR.search(html)
    if m:
        info.team_color = " ".join(m.group(1).split())  # 개행·중복 공백 정리
    m = _SN.search(html)
    if m:
        info.profile_sn = int(m.group(1))
    return info


def parse_rank_page(html: str) -> list[tuple[str, str, int | None]]:
    """목록 페이지 → [(닉네임, 팀컬러, 구단가치)]. 팀컬러를 안 쓰는 사람은 "".

    행 단위로 잘라서 읽는다 — 페이지 전체에서 닉네임·팀컬러를 따로 findall 하면 팀컬러가
    빈 행(실측 475페이지에 있었다)에서 _TEAM_COLOR 의 `.*?` 가 다음 행 팀컬러를 집어
    그 뒤가 전부 한 칸씩 밀린다.
    """
    return _color_rows(parse_rank_rows(html))


def _color_rows(rows: list[RankRow]) -> list[tuple[str, str, int | None]]:
    # 팀컬러가 없으면 구단가치도 None — 팀컬러 표가 그 행을 '팀가치 모름'으로 보여 준다
    return [(r.nickname, r.team_color, r.team_value if r.team_color else None)
            for r in rows if r.nickname]


def parse_rank_rows(html: str) -> list[RankRow]:
    """목록 페이지 → RankRow 전부. 못 읽은 칸은 None/"" 로 둔다 — 판정은 judge_page 가 한다.

    행 단위로 자르는 이유는 parse_rank_page 와 같다(팀컬러 빈 행에서 뒤가 밀린다).
    """
    out = []
    for row in html.split(_ROW_START)[1:]:
        m = _ROW_RANK.match(row)
        rank = _to_int(m.group(1)) if m else None
        m = _SN.search(row)
        sn = int(m.group(1)) if m else None
        m = _NAME.search(row)
        r = RankRow(rank=rank, profile_sn=sn, nickname=unescape(m.group(1)).strip() if m else "")
        if m := _LEVEL.search(row):
            r.level = _to_int(m.group(1))
        if m := _PRICE.search(row):
            r.team_value, r.team_value_text = _to_int(m.group(1)), m.group(2).strip()
        if m := _ELO.search(row):
            try:
                r.elo = float(m.group(1))
            except ValueError:
                pass
        if m := _WINRATE.search(row):
            r.win_rate = m.group(1)
        if m := _WDL.search(row):
            r.win, r.draw, r.lose = (_to_int(m.group(i)) for i in (1, 2, 3))
        if m := _TEAM_COLOR.search(row):
            r.team_color = " ".join(m.group(1).split())
        if m := _COLOR_COUNT.search(row):
            r.color_count = int(m.group(1))
        if m := _FORMATION.search(row):
            r.formation = m.group(1)
        head, _, best = row.partition(_BEST_START)
        if m := _GRADE.search(head):
            r.grade = int(m.group(1))
        grades = _GRADE.findall(best)
        if len(grades) >= 2:
            r.best_grade, r.prev_grade = int(grades[0]), int(grades[1])
        out.append(r)
    return out


def judge_page(page: int, rows: list[RankRow], prev_rows: list[RankRow] | None) -> str:
    """수집용 쪽 판정 — "ok"(정상) · "end"(목록 끝, 앞 쪽 되풀이). 실패는 예외.

    구조가 바뀐 응답이 '정상'이나 '끝'으로 읽히면 반쪽 스냅숏이 조용히 쌓인다 — 그래서 필수 칸이
    하나라도 비면 실패이고, 1쪽부터 0행이어도 실패다(시즌 첫날인지 구조 변경인지 가를 수 없다).
    """
    if not rows:
        if page == 1:
            raise RankerError("랭킹 목록 1쪽이 비어 있습니다")
        raise RankStructureError(f"랭킹 목록 {page}쪽이 비어 있습니다(구조가 바뀌었을 수 있음)")
    if any(r.rank is None or r.profile_sn is None or not r.nickname for r in rows):
        raise RankStructureError(f"랭킹 목록 {page}쪽에 순위·프로필 번호·닉네임이 빠진 행이 있습니다")
    if prev_rows and [r.profile_sn for r in rows] == [r.profile_sn for r in prev_rows]:
        return "end"
    return "ok"


def fetch_rank_rows(page: int, timeout: int = config.RANK_PAGE_TIMEOUT_S, season_no: int = 0) -> RankPageResult:
    """랭킹 목록 한 쪽을 그대로 받는다 — 0행이어도 예외 없이 돌려준다(판정은 judge_page). season_no 0 = 지금 시즌.

    실패 종류를 가른다: RankBlocked(403·429·Cloudflare) · RankOffline(연결 안 됨) · RankerError(그 밖 —
    리다이렉트·점검·HTTP 오류). 없는 시즌은 302 로 랭킹 첫 화면에 보내므로 리다이렉트는 실패다(실측).
    """
    if not config.WEB_DATA:
        raise RankerError(config.WEB_DATA_OFF_MSG)
    try:
        res = web_get(_session, RANK_URL,
                      params={"rt": "manager", "n4seasonno": season_no, "n4pageno": page,
                              "_ts": int(time.time() * 1000)},
                      timeout=timeout)
    except requests.ConnectionError as e:
        raise RankOffline(f"랭킹 목록 연결 실패: {e}") from e
    except requests.RequestException as e:
        raise RankerError(f"랭킹 목록 조회 실패: {e}") from e
    html = res.text
    if getattr(res, "status_code", 200) in _BLOCK_STATUS or any(m in html for m in _CF_MARKERS):
        raise RankBlocked(f"랭킹 목록 {page}쪽이 막혔습니다(HTTP {getattr(res, 'status_code', '?')})")
    try:
        res.raise_for_status()
    except requests.RequestException as e:
        raise RankerError(f"랭킹 목록 조회 실패: {e}") from e
    if getattr(res, "history", None):
        raise RankerError(f"랭킹 목록 {page}쪽이 다른 페이지로 넘어갔습니다")
    if "점검 진행 중" in html or "fc_logo_inspection" in html:
        raise RankerError("넥슨 웹 점검 중입니다 — 잠시 후 다시 시도해주세요")
    return RankPageResult(page, parse_rank_rows(html), getattr(res, "headers", {}).get("Date", ""))


def fetch_season_cut(season_no: int, rank: int, timeout: int = config.RANK_PAGE_TIMEOUT_S) -> float | None:
    """끝난 시즌의 최종 순위 컷 — 그 순위가 든 쪽 하나(ceil(rank/20))만 받는다(1.3.1 예측 · ROADMAP R2).

    같은 점수면 순위가 건너뛰므로 rank 이하 중 가장 아래 행의 ELO. 행이 비면 RankStructureError,
    그 밖 실패는 fetch_rank_rows 와 같다(리다이렉트 = 없는 시즌)."""
    if not config.WEB_DATA:
        raise RankerError(config.WEB_DATA_OFF_MSG)
    if season_no <= 0 or rank <= 0:
        raise ValueError("끝난 시즌 번호와 순위가 필요합니다")
    page = -(-rank // RANK_PAGE_SIZE)
    rows = fetch_rank_rows(page, timeout, season_no=season_no).rows
    usable = [r for r in rows if r.rank is not None and r.elo is not None]
    if not usable:
        raise RankStructureError(f"{season_no}시즌 랭킹 {page}쪽을 읽지 못했습니다")
    at_or_above = [r for r in usable if r.rank <= rank]
    return (max(at_or_above, key=lambda r: r.rank) if at_or_above else usable[0]).elo


def fetch_rank_page(page: int, timeout: int = 10) -> list[tuple[str, str, int | None]]:
    """감독모드 랭킹 목록 한 페이지(1..RANK_PAGES) — 팀컬러용 (닉네임, 팀컬러, 구단가치). 실패는 RankerError."""
    rows = _color_rows(fetch_rank_rows(page, timeout).rows)
    if not rows:  # 빈 페이지를 "아무도 없음"으로 읽으면 상대 수백 명이 '랭킹 밖'으로 캐시된다
        raise RankerError(f"랭킹 목록 {page}페이지를 읽지 못했습니다(구조가 바뀌었을 수 있음)")
    return rows


# 넥슨 데이터센터가 구단가치를 표기하는 방식("10경 9,631조")과 같은 축약 —
# 상대 팀가치는 원 단위 정수(team_value)만 저장하므로 표시할 때 이걸로 만든다.
_VALUE_UNITS = [(10 ** 16, "경"), (10 ** 12, "조"), (10 ** 8, "억"), (10 ** 4, "만")]


def format_team_value(value: int) -> str:
    for i, (unit, name) in enumerate(_VALUE_UNITS):
        if value >= unit:
            top = value // unit
            text = f"{top:,}{name}"
            if i + 1 < len(_VALUE_UNITS):
                sub_unit, sub_name = _VALUE_UNITS[i + 1]
                sub = value % unit // sub_unit
                if sub:
                    text += f" {sub:,}{sub_name}"
            return text
    return f"{value:,}"
