"""넥슨 데이터센터에서 선수 카드 상세(능력치·특성·시세·클럽 경력)를 가져온다.

오픈API(get_meta("spid"))는 {id, name} 뿐이라 카드 오버롤·세부 능력치·시세·
특성·클럽 경력이 없다. 그 데이터는 넥슨 공식 모바일 데이터센터 페이지에
서버 렌더링된 채로 들어있어서(ranker.py 와 같은 방식의 HTML 스크래핑) 여기서
긁어온다.

    https://m.fconline.nexon.com/datacenter/playerinfo?spid=<spId>

주의: JSON API 가 아니라 HTML 스크래핑이다. 넥슨이 페이지 구조(class 이름)를
바꾸면 파싱이 깨진다 — 그러면 실제 응답과 대조해 아래 정규식만 고치면 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

PLAYER_INFO_URL = "https://m.fconline.nexon.com/datacenter/playerinfo"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

# ranker.py 와 같은 이유로 Session 재사용 — 선수 카드를 여러 번 열어봐도
# 매번 TCP/TLS 핸드셰이크를 새로 열지 않는다.
_session = requests.Session()
_session.headers.update(_HEADERS)

# 이름 다음에 오는 순수 숫자만 "능력치"로 잡는다 — 같은 class="txt"/"value"
# 구조를 쓰는 "출생"(날짜 문자열)·"명성"(등급 문자열) 항목은 값이 숫자만이
# 아니라서 자연히 걸러진다.
_ABILITY = re.compile(
    r'<div class="txt">([^<]+)</div>\s*<div class="value[^"]*">\s*(\d+)\s*</div>')
_NAME = re.compile(r'<div class="name">([^<]+)</div>')
_POSITION_OVR = re.compile(
    r'<strong class="(?:st|gk)">([^<]+)</strong><span class="_area_point">(\d+)</span>')
_PHOTO = re.compile(r'class="thumb"><span class="img action"><img src="([^"]+)"')
_NATION = re.compile(
    r'class="nationWrap">\s*<span class="nation">\s*<img src="([^"]+)"[^>]*>\s*'
    r'<span class="txt">([^<]+)</span>')
_STAT_LINE = re.compile(
    r'<div class="statWrap">\s*<span>([^<]+)</span>\s*<span>([^<]+)</span>\s*'
    r'<span>([^<]+)</span>\s*<span>\s*([^<]+?)\s*<strong>([^<]+)</strong>')
_PRICE = re.compile(r'class="span_bp(\d+)"[^>]*>\s*([^<]+?)\s*<')
_SKILLMOVE_BLOCK = re.compile(
    r'개인기</div>\s*<div class="value _area_skillmove">(.*?)</div>', re.S)
_FAME = re.compile(r'명성</div>\s*<div class="value">([^<]+)</div>')
_TRAIT = re.compile(
    r'<li class="ab feature">\s*<div class="txt">\s*<div class="txtTop">([^<]+)</div>'
    r'\s*<div class="txtBottom">\s*<span>([^<]*)</span>.*?'
    r'<img src="([^"]+)"', re.S)
_CLUB_ITEM = re.compile(
    r'<div class="listItem">\s*<div class="year">([^<]+)</div>\s*'
    r'<div class="club">([^<]*)</div>\s*<div class="rent">([^<]*)</div>')


class PlayerInfoError(Exception):
    pass


@dataclass
class Trait:
    name: str
    desc: str
    icon_url: str


@dataclass
class ClubStint:
    period: str
    club: str
    loan: bool


@dataclass
class PlayerInfo:
    sp_id: int
    name: str = "-"
    position: str = "-"
    ovr: int | None = None
    photo_url: str = ""
    nation_flag_url: str = ""
    nation: str = "-"
    height: str = "-"
    weight: str = "-"
    body_type: str = "-"
    weak_foot: str = "-"
    strong_foot: str = "-"
    fame: str = "-"
    skill_moves: int = 0
    skill_moves_max: int = 0
    abilities: dict[str, int] = field(default_factory=dict)
    prices: dict[int, str] = field(default_factory=dict)  # 강화단계 -> 시세 문자열
    traits: list[Trait] = field(default_factory=list)
    club_history: list[ClubStint] = field(default_factory=list)

    # 6분류 요약치 — FIFA/FC 시리즈에 공통적인 표준 조합(속력=가속력+스피드
    # 평균, 슛=슈팅 계열 6개 평균 …)이다. 이 사이트에서 그대로 서버 렌더링
    # 되는 값이 아니라 우리가 abilities 로 재계산한 것이라, 넥슨 내부
    # 가중치와 완전히 똑같다는 보장은 없다 — 근사치로 본다.
    GROUP_DEFS = {
        "스피드": ["가속력", "속력"],
        "슛": ["위치 선정", "골 결정력", "슛 파워", "중거리 슛", "발리슛", "페널티 킥"],
        "패스": ["시야", "크로스", "프리킥", "짧은 패스", "긴 패스", "커브"],
        "드리블": ["민첩성", "밸런스", "반응 속도", "볼 컨트롤", "드리블", "침착성"],
        "수비": ["가로채기", "헤더", "대인 수비", "태클", "슬라이딩 태클"],
        "피지컬": ["점프", "스태미너", "몸싸움", "적극성"],
    }

    def group_stats(self) -> dict[str, int]:
        """스피드/슛/패스/드리블/수비/피지컬 6분류 평균(반올림)."""
        out: dict[str, int] = {}
        for group, keys in self.GROUP_DEFS.items():
            vals = [self.abilities[k] for k in keys if k in self.abilities]
            if vals:
                out[group] = round(sum(vals) / len(vals))
        return out


def fetch_player_info(sp_id: int, timeout: int = 10) -> PlayerInfo:
    """선수 카드 상세를 가져온다. 네트워크·파싱 실패는 PlayerInfoError."""
    try:
        res = _session.get(PLAYER_INFO_URL, params={"spid": sp_id}, timeout=timeout)
        res.raise_for_status()
    except requests.RequestException as e:
        raise PlayerInfoError(f"선수 정보 조회 실패: {e}") from e

    html = res.text
    info = PlayerInfo(sp_id=sp_id)

    m = _NAME.search(html)
    if m:
        info.name = m.group(1).strip()
    m = _POSITION_OVR.search(html)
    if m:
        info.position = m.group(1).strip()
        info.ovr = int(m.group(2))
    m = _PHOTO.search(html)
    if m:
        info.photo_url = m.group(1)
    m = _NATION.search(html)
    if m:
        info.nation_flag_url = m.group(1)
        info.nation = m.group(2).strip()
    m = _STAT_LINE.search(html)
    if m:
        info.height, info.weight, info.body_type = (g.strip() for g in m.groups()[:3])
        info.weak_foot, info.strong_foot = m.group(4).strip(), m.group(5).strip()
    m = _FAME.search(html)
    if m:
        info.fame = m.group(1).strip()
    m = _SKILLMOVE_BLOCK.search(html)
    if m:
        block = m.group(1)
        info.skill_moves = block.count("#F1C018")
        info.skill_moves_max = block.count("<svg")

    info.abilities = {name.strip(): int(value) for name, value in _ABILITY.findall(html)}
    info.prices = {int(grade): text.strip() for grade, text in _PRICE.findall(html)}
    info.traits = [Trait(name=n.strip(), desc=d.strip(), icon_url=icon)
                   for n, d, icon in _TRAIT.findall(html)]
    info.club_history = [ClubStint(period=p.strip(), club=c.strip(), loan=bool(r.strip()))
                         for p, c, r in _CLUB_ITEM.findall(html)]
    return info
