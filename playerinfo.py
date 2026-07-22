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
# PC 데이터센터 — 강화/적응도/팀컬러(소속·강화·관계 3종)를 반영한 능력치를
# 넥슨 서버가 직접 계산해 돌려준다. 모바일 페이지에는 이 기능이 없다
# (2026-07-22, 사용자가 PC 데이터센터 화면 캡처로 알려줘서 발견).
PLAYER_ABILITY_URL = "https://fconline.nexon.com/datacenter/PlayerAbility"
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

# PC 데이터센터 PlayerAbility 응답 전용 — 마크업이 모바일과 달라
# (class="value _area_point over130">131 대신 class="value over130">\n131
# <span class="diff">) 위 _ABILITY 로는 안 잡힌다. 닫는 태그를 요구하지
# 않고 숫자만 뽑는다 — 개별 능력치(<li class="ab" data-positon="...">)와
# 6분류 요약(<li class="ab">, data-positon 없음)이 같은 마크업을 쓰므로
# 파싱 후 GROUP_NAMES 로 구분한다.
_ABILITY_LI_PC = re.compile(
    r'<li class="ab"[^>]*>\s*<div class="txt">([^<]+)</div>\s*'
    r'<div class="value[^"]*">\s*(\d+)', re.S)
_OVR_PC = re.compile(r'<div class="ovr value">(\d+)</div>')
GROUP_NAMES = {"스피드", "슛", "패스", "드리블", "수비", "피지컬"}

# PlayerAbility 응답 안에는 소속/강화/관계 팀컬러 드롭다운의 선택지 목록이
# "이 선수 전용으로 이미 필터링된 채로" 들어 있다(<div class="selector_list">
# <ul>…</ul> 블록). 전체 599개 목록 페이지(/datacenter/teamcolor)를 따로
# 긁을 필요가 없다. 앵커는 라벨 텍스트가 아니라 감싸는 wrapper class 를
# 쓴다 — 드롭다운 버튼 텍스트는 뭔가 선택되는 순간 라벨("소속 팀컬러")에서
# 선택된 이름("아스널")으로 바뀌어서 라벨 기반 매칭이 깨진다(실측).
#   tdefault_wrap  = 소속 팀컬러  /  tspecial_wrap = 관계 팀컬러
#   teamcolor_selector_wrap 바로 다음 첫 목록 = 강화 팀컬러
_TEAMCOLOR_ITEM = re.compile(r'data-no="(\d+)"[^>]*>([^<]+)</a>')
_TEAMCOLOR_LV_PREFIX = re.compile(r'^Lv\.\s*(\d+)\s*')
# 소속 팀컬러를 선택한 응답에만 나타나는 레벨 선택지(Lv.1~최대) — 팀컬러마다
# 최대 레벨이 다르다(실측: 대부분 4, Winning Streak 는 3). 범위 밖 레벨을
# 보내면 넥슨이 에러 페이지를 돌려준다.
_CLUB_LV_ITEM = re.compile(r'class="selector_item tlv(\d+)"')


def _selector_items(html: str, wrapper_class: str) -> list[tuple[int, str]]:
    """wrapper class 뒤 첫 selector_list 의 (id, 표시명) 목록.
    data-no="0" 은 "선택 안 함" 플레이스홀더라 뺀다."""
    m = re.search(r'<div class="' + wrapper_class +
                  r'[^"]*">.*?<div class="selector_list">\s*<ul>(.*?)</ul>',
                  html, re.S)
    if not m:
        return []
    return [(int(i), name.strip()) for i, name in _TEAMCOLOR_ITEM.findall(m.group(1))
            if i != "0"]


class PlayerInfoError(Exception):
    pass


# 강화(1~13강) 선택지 — PC 데이터센터 선수 정보 변경 팝업과 동일한 범위.
STRONG_LEVELS = list(range(1, 14))


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
        """스피드/슛/패스/드리블/수비/피지컬 6분류 평균(반올림) — 근사치.

        이 사이트에서 그대로 서버 렌더링되는 값이 아니라 abilities 로
        재계산한 것이라 넥슨 내부 가중치와 완전히 같다는 보장은 없다.
        정확한 값이 필요하면 fetch_player_ability()(PC 데이터센터가 직접
        계산해 돌려주는 값)를 쓴다."""
        out: dict[str, int] = {}
        for group, keys in self.GROUP_DEFS.items():
            vals = [self.abilities[k] for k in keys if k in self.abilities]
            if vals:
                out[group] = round(sum(vals) / len(vals))
        return out


@dataclass
class AbilitySim:
    """강화·적응도·팀컬러 조합 하나에 대한 PC 데이터센터 계산 결과."""
    ovr: int | None
    groups: dict[str, int]      # 스피드/슛/패스/드리블/수비/피지컬
    abilities: dict[str, int]   # 개별 능력치 30여개
    # 이 선수 전용 팀컬러 선택지 — 응답 HTML에 같이 들어온다. 강화 팀컬러는
    # 강화 단계에 따라 목록이 달라지고(1강이면 빈 목록) 항목마다 레벨이
    # 박혀 있어 (id, lv, "Lv.N 이름") 3튜플이다.
    club_options: list[tuple[int, str]] = field(default_factory=list)
    enhance_options: list[tuple[int, int, str]] = field(default_factory=list)
    feature_options: list[tuple[int, str]] = field(default_factory=list)
    # 선택된 소속 팀컬러의 유효 레벨 목록(선택 없으면 빈 리스트) — 최대
    # 레벨이 팀컬러마다 달라서, 호출자가 이걸 보고 최대 레벨로 재조회한다.
    club_levels: list[int] = field(default_factory=list)


def fetch_player_ability(sp_id: int, strong: int = 1, grow: int = 5,
                         teamcolor_id: int = 0, teamcolor_lv: int = 0,
                         teamcolor_id_enhance: int = 0, teamcolor_lv_enhance: int = 0,
                         teamcolor_id_feature: int = 0, timeout: int = 10) -> AbilitySim:
    """강화·적응도·팀컬러(소속/강화/관계) 조합을 넥슨 서버에 그대로 넘겨서
    계산된 능력치를 받는다 — PC 데이터센터의 "선수 정보 변경" 팝업이 호출하는
    바로 그 엔드포인트(datacenter.js DataCenter.GetPlayerAbility). 로컬에서
    직접 계산하지 않고 매번 조회하는 이유: 팀컬러 보너스가 종류(수백 가지
    엔티티)·레벨마다 다르고 넥슨이 그 조합표를 공개하지 않아, 근사치보다
    서버가 계산한 값을 그대로 받는 쪽이 확실하다."""
    data = {
        "spid": sp_id, "n1Strong": strong, "n1Grow": grow,
        "n4TeamColorId": teamcolor_id, "n4TeamColorLv": teamcolor_lv,
        "n4TeamColorId_Enhance": teamcolor_id_enhance,
        "n4TeamColorLv_Enhance": teamcolor_lv_enhance,
        "n4TeamColorId_Feature": teamcolor_id_feature,
        "n1Change": 0, "strPlayerImg": "",
    }
    try:
        res = _session.post(PLAYER_ABILITY_URL, data=data, timeout=timeout)
        res.raise_for_status()
    except requests.RequestException as e:
        raise PlayerInfoError(f"능력치 시뮬레이터 조회 실패: {e}") from e

    html = res.text
    groups: dict[str, int] = {}
    abilities: dict[str, int] = {}
    for name, value in _ABILITY_LI_PC.findall(html):
        name = name.strip()
        if name in GROUP_NAMES:
            groups[name] = int(value)
        else:
            abilities[name] = int(value)
    enhance_options: list[tuple[int, int, str]] = []
    for eid, label in _selector_items(html, "teamcolor_selector_wrap"):
        lv_m = _TEAMCOLOR_LV_PREFIX.match(label)
        enhance_options.append((eid, int(lv_m.group(1)) if lv_m else 1, label))

    m = _OVR_PC.search(html)
    return AbilitySim(ovr=int(m.group(1)) if m else None,
                      groups=groups, abilities=abilities,
                      club_options=_selector_items(html, "tdefault_wrap"),
                      enhance_options=enhance_options,
                      feature_options=_selector_items(html, "tspecial_wrap"),
                      club_levels=sorted({int(v) for v in _CLUB_LV_ITEM.findall(html)}))


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
