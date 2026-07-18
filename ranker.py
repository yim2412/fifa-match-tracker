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
import time
from dataclasses import dataclass

import requests

RANK_URL = "https://fconline.nexon.com/datacenter/rank_inner"
# 브라우저처럼 보이지 않으면 넥슨이 응답을 안 줄 수 있어 UA 를 넣는다.
# no-cache 류 헤더는 중간 프록시가 예전 응답을 재활용하는 걸 막는 안전장치다
# (Cloudflare 는 이 페이지를 DYNAMIC 으로 표시해 자체 캐싱은 안 하는 걸 확인했지만,
# 검색할 때마다 최신값을 받는다는 걸 보장하려고 남겨 둔다).
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Cache-Control": "no-cache", "Pragma": "no-cache"}

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

    @property
    def ranked(self) -> bool:
        return self.rank is not None

    @property
    def record_text(self) -> str:
        return f"{self.win}승 {self.draw}무 {self.lose}패"


class RankerError(Exception):
    pass


def _to_int(s: str) -> int:
    try:
        return int(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def fetch_manager_rank(nickname: str, timeout: int = 10) -> RankerInfo:
    """감독모드 랭킹을 가져온다. 랭킹 밖이면 ranked=False 로 돌아온다.

    네트워크·파싱 실패는 RankerError. 호출부에서 잡아 카드를 비워도 앱은 산다.
    """
    if not nickname:
        raise RankerError("닉네임이 비어 있습니다.")
    try:
        res = requests.get(
            RANK_URL,
            # _ts: 캐시 방지용 — URL 이 매번 달라야 어떤 프록시도 이전 응답을
            # 재사용하지 못한다. 넥슨이 이 값을 쓰지 않으니 결과엔 영향 없다.
            params={"rt": "manager", "strCharacterName": nickname,
                    "n4seasonno": 0, "n4pageno": 1, "_ts": int(time.time() * 1000)},
            headers=_HEADERS, timeout=timeout)
        res.raise_for_status()
    except requests.RequestException as e:
        raise RankerError(f"랭킹 조회 실패: {e}") from e

    html = res.text
    info = RankerInfo(nickname=nickname)

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
    return info
