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

import config
import ranker  # web_get — 넥슨 홈페이지 동시 요청 상한을 같이 쓴다

PLAYER_INFO_URL = "https://m.fconline.nexon.com/datacenter/playerinfo"
# PC 데이터센터 — 강화/적응도/팀컬러(소속·강화·관계 3종)를 반영한 능력치를
# 넥슨 서버가 직접 계산해 돌려준다. 모바일 페이지에는 이 기능이 없다
# (2026-07-22, 사용자가 PC 데이터센터 화면 캡처로 알려줘서 발견).
PLAYER_ABILITY_URL = "https://fconline.nexon.com/datacenter/PlayerAbility"
_HEADERS = {"User-Agent": config.WEB_USER_AGENT}

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
# 2026-07 확인: 일부 카드(신규 시즌 등) 페이지는 마크업이 다르다 —
# OVR 이 먼저, 포지션이 뒤(<span class="ovr _area_point">113</span>
# <span class="position df">CB</span>). 옛 마크업(올리버 칸 등)과 공존하므로
# 둘 다 시도한다. 캡처 순서가 서로 반대인 것에 주의.
_POSITION_OVR_NEW = re.compile(
    r'<span class="ovr _area_point">(\d+)</span>\s*<span class="position[^"]*">([^<]+)</span>')
# 시즌 배지는 페이지에 여러 개다(다른 시즌 동일 선수 목록 등) — 카드 본인
# 것은 nameWrap(이름 옆) 안에 있는 것만.
# 본 카드 구역 — 페이지에 다른 카드(빠른 목록·같은 선수 다른 시즌)의 pay_q·ovr 이 수십 개라(2026-10-06 실측 pay 102개)
# 급여는 이 구역 안에서만 찾는다. 구역은 playerThumb 부터 바로 뒤 infoWrap 까지(라벨이 아니라 wrapper class 로 앵커).
_MAIN_CARD = re.compile(r'<div class="playerThumb">(.*?)<div class="infoWrap">', re.S)
_PAY = re.compile(r'<span class="pay">.*?<span>\s*(\d+)\s*</span>', re.S)
_SEASON_ICON = re.compile(
    r'class="nameWrap">\s*<span class="season">\s*<img src="([^"]+)"')
_STRONG_FOOT = re.compile(r'<strong>([^<]+)</strong>')
_PHOTO = re.compile(r'class="thumb"><span class="img action"><img src="([^"]+)"')
_NATION = re.compile(
    r'class="nationWrap">\s*<span class="nation">\s*<img src="([^"]+)"[^>]*>\s*'
    r'<span class="txt">([^<]+)</span>')
# 4번째 span(발기술)은 마크업이 두 가지다 — 옛: "L2 – <strong>R5</strong>",
# 새: "<strong>L5</strong> – R3". 통째로 잡아서 코드에서 <strong>(주발)을 분리한다.
_STAT_LINE = re.compile(
    r'<div class="statWrap">\s*<span>([^<]+)</span>\s*<span>([^<]+)</span>\s*'
    r'<span>([^<]+)</span>\s*<span>\s*(.*?)\s*</span>', re.S)
_PRICE = re.compile(r'class="span_bp(\d+)"[^>]*>\s*([^<]+?)\s*<')
_SKILLMOVE_BLOCK = re.compile(
    r'개인기</div>\s*<div class="value _area_skillmove">(.*?)</div>', re.S)
_SKILLMOVE_ON = "#F1C018"   # 개인기 별 중 켜진 것의 채움색 — 화면 색이 아니라 넥슨 HTML 의 표식
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
# 포지션별 오버롤 — PC 페이지 오른쪽 축구장 그림의 값들. 응답의
# <div class="ovr_set"> 안에 <div class="position st value">105</div> 꼴로 온다.
_POSITION_OVR_SET = re.compile(r'<div class="position (\w+) value">(\d+)</div>')

# 데이터센터 능력치 색 기준 — 값의 10점 구간마다 class("value overN")가 붙고
# CSS 색이 매겨진다(2026-07-23 브라우저 계산 스타일로 실측). 구간 경계는
# 홈페이지와 동일하게 두되, 80 미만 3단계는 원본이 어두운 회색·검정 계열이라
# (흰 배경용) 다크 테마에서 안 보여서 밝은 회색 3단계로 바꿨다.
STAT_COLOR_BUCKETS = [
    (150, "#FFA800"),  # 주황골드 (원본 rgb(255,168,0))
    (140, "#C99B00"),  # 골드     (원본 rgb(201,155,0))
    (130, "#DC0000"),  # 빨강     (원본 rgb(220,0,0))
    (120, "#CF13C0"),  # 마젠타   (원본 rgb(207,19,192))
    (110, "#B33BFF"),  # 연보라   (원본 rgb(179,59,255))
    (100, "#6E3BFF"),  # 보라     (원본 rgb(110,59,255))
    (90,  "#3D6FE8"),  # 파랑     (원본 rgb(23,93,222) — 다크 배경 대비 소폭 밝게)
    (80,  "#2194D6"),  # 하늘     (원본 rgb(33,148,214))
    (70,  "#C2CAD1"),  # 원본은 거의 검정(31,45,55) — 다크 테마용 밝은 회색
    (60,  "#A5ADB5"),  # 원본은 진회색(96,105,114)
    (0,   "#8F96A0"),  # 60 미만 — 원본 회색 그대로
]


def stat_color(value: int) -> str:
    """능력치 값 → 데이터센터와 같은 구간 기준의 색."""
    for floor, color in STAT_COLOR_BUCKETS:
        if value >= floor:
            return color
    return STAT_COLOR_BUCKETS[-1][1]

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
    salary: int | None = None   # 급여 — 본 카드 구역에서만(못 찾으면 None)
    photo_url: str = ""
    nation_flag_url: str = ""
    nation: str = "-"
    height: str = "-"
    weight: str = "-"
    body_type: str = "-"
    weak_foot: str = "-"
    strong_foot: str = "-"
    fame: str = "-"
    season_icon_url: str = ""   # 시즌 배지(FAC 등) — 카드 이름 옆에 뜨는 그 아이콘
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
    # 포지션별 오버롤 — {"ST": 105, "GK": 29, ...} 16개(대문자 코드)
    position_ovrs: dict[str, int] = field(default_factory=dict)


# 적응도 선택지 — PC 데이터센터 드롭다운과 동일(1 또는 5, 기본 1).
# 서버 파라미터 n1Grow 는 "적응도 - 1" 을 받는다(브라우저 실측: 드롭다운
# +1 은 n1Grow=0, +5 는 n1Grow=4 를 보낸다). 예전에 grow=5 를 그대로
# 보냈더니 존재하지 않는 "적응도 6"으로 계산돼 홈페이지와 전 능력치가
# 5씩 어긋났다.
ADAPT_CHOICES = [1, 5]
ADAPT_DEFAULT = 1


def fetch_player_ability(sp_id: int, strong: int = 1, adapt: int = ADAPT_DEFAULT,
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
        "spid": sp_id, "n1Strong": strong, "n1Grow": adapt - 1,
        "n4TeamColorId": teamcolor_id, "n4TeamColorLv": teamcolor_lv,
        "n4TeamColorId_Enhance": teamcolor_id_enhance,
        "n4TeamColorLv_Enhance": teamcolor_lv_enhance,
        "n4TeamColorId_Feature": teamcolor_id_feature,
        "n1Change": 0, "strPlayerImg": "",
    }
    if not config.WEB_DATA:
        raise PlayerInfoError(config.WEB_DATA_OFF_MSG)
    try:
        res = ranker.web_get(_session, PLAYER_ABILITY_URL, "post", data=data, timeout=timeout)
        res.raise_for_status()
    except requests.RequestException as e:
        raise PlayerInfoError(f"능력치 시뮬레이터 조회 실패: {e}") from e

    html = res.text
    groups: dict[str, int] = {}
    abilities: dict[str, int] = {}
    # 6분류 요약이 개별 능력치보다 먼저 온다. "드리블"은 요약과 개별 능력치
    # 양쪽에 같은 이름으로 있으므로 첫 번째 것만 요약으로 받는다 — 안 그러면
    # 개별 드리블(예: 107)이 요약 드리블(108)을 덮어써서 홈페이지와 1 어긋난다.
    for name, value in _ABILITY_LI_PC.findall(html):
        name = name.strip()
        if name in GROUP_NAMES and name not in groups:
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
                      club_levels=sorted({int(v) for v in _CLUB_LV_ITEM.findall(html)}),
                      position_ovrs={p.upper(): int(v)
                                     for p, v in _POSITION_OVR_SET.findall(html)})


def fetch_player_info(sp_id: int, timeout: int = 10) -> PlayerInfo:
    """선수 카드 상세를 가져온다. 네트워크·파싱 실패는 PlayerInfoError."""
    if not config.WEB_DATA:
        raise PlayerInfoError(config.WEB_DATA_OFF_MSG)
    try:
        res = ranker.web_get(_session, PLAYER_INFO_URL, params={"spid": sp_id}, timeout=timeout)
        res.raise_for_status()
    except requests.RequestException as e:
        raise PlayerInfoError(f"선수 정보 조회 실패: {e}") from e

    html = res.text
    info = PlayerInfo(sp_id=sp_id)

    m = _NAME.search(html)
    if m:
        info.name = m.group(1).strip()
    card = _MAIN_CARD.search(html)
    # OVR·포지션도 본 카드 구역 먼저 — 구역을 못 찾으면(마크업 변경) 예전처럼 페이지 전체의 첫 값
    for scope in ((card.group(1), html) if card else (html,)):
        m = _POSITION_OVR.search(scope)
        if m:
            info.position, info.ovr = m.group(1).strip(), int(m.group(2))
            break
        m = _POSITION_OVR_NEW.search(scope)
        if m:
            info.ovr, info.position = int(m.group(1)), m.group(2).strip()
            break
    if card:
        m = _PAY.search(card.group(1))
        if m:
            info.salary = int(m.group(1))
    m = _SEASON_ICON.search(html)
    if m:
        info.season_icon_url = m.group(1)
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
        foot = m.group(4)
        sm = _STRONG_FOOT.search(foot)
        if sm:
            info.strong_foot = sm.group(1).strip()
        rest = _STRONG_FOOT.sub("", foot)
        info.weak_foot = re.sub(r"[–\-\s]+", " ", rest).strip() or "-"
    m = _FAME.search(html)
    if m:
        info.fame = m.group(1).strip()
    m = _SKILLMOVE_BLOCK.search(html)
    if m:
        block = m.group(1)
        info.skill_moves = block.count(_SKILLMOVE_ON)
        info.skill_moves_max = block.count("<svg")

    info.abilities = {name.strip(): int(value) for name, value in _ABILITY.findall(html)}
    info.prices = {int(grade): text.strip() for grade, text in _PRICE.findall(html)}
    info.traits = [Trait(name=n.strip(), desc=d.strip(), icon_url=icon)
                   for n, d, icon in _TRAIT.findall(html)]
    info.club_history = [ClubStint(period=p.strip(), club=c.strip(), loan=bool(r.strip()))
                         for p, c, r in _CLUB_ITEM.findall(html)]
    return info


# ── 시세 → 정수 · 카드 시세 캐시 채우기(공용 부품 B — 1.4.1) ─────────────────────────

_BP_DIGITS = re.compile(r"[0-9][0-9,]*")


def parse_bp(text) -> int | None:
    """"3,660,000,000,000 BP" → 3660000000000. "-"·빈칸·숫자 없음은 None(그 단계만 버린다 — R13)."""
    m = _BP_DIGITS.search(text or "")
    return int(m.group(0).replace(",", "")) if m else None


def prices_as_int(info: PlayerInfo) -> dict[int, int]:
    out = {}
    for grade, text in info.prices.items():
        v = parse_bp(text)
        if v is not None and v > 0:
            out[grade] = v
    return out


def save_card(conn, info: PlayerInfo, day: str) -> bool:
    """선수 페이지 한 번 읽은 것을 두 캐시에 — 시세(card_prices · 하루)와 카드 정보(card_info · 30일). 시세가 있었나를 돌려준다."""
    import store  # store → seasons → … 순환을 피해 여기서
    prices = prices_as_int(info)
    if prices:
        store.save_card_prices(conn, info.sp_id, prices, day)
    store.save_card_info(conn, info.sp_id, info.name if info.name != "-" else None,
                         info.position if info.position != "-" else None, info.ovr, info.salary, day)
    return bool(prices)


# 쓰임별 하루 계수 이름(store.api_budget.kind)
KIND_LEDGER = "card_ledger"
KIND_CHIP = "card_chip"


def collect_cards(conn, spids, day: str, kind: str, cap: int, cancel=lambda: False, fetch=None,
                  need=None, on_card=None) -> tuple[int, int]:
    """선수 페이지를 읽어 시세·카드 정보 캐시를 채운다 → (이번에 읽은 카드 수, 상한에 걸려 못 읽은 카드 수).

    need(spid) 가 참인 카드만(기본: 오늘 시세가 없는 카드). 요청 직전에만 그날 그 쓰임(kind)의 계수를 1 더하고
    상한(cap)에 닿으면 멈춘다 — 실행이 여러 번이어도, 시세가 비어 와 저장되지 않는 카드도 센다.
    웹 데이터가 꺼졌으면 요청도 계수도 없이 멈춘다. 한 장 = 한 트랜잭션, 카드 사이마다 cancel.
    한 장 실패는 건너뛴다(나머지를 막지 않게). on_card(PlayerInfo) — 읽은 카드를 화면에 바로 넘길 때."""
    import store
    fetch = fetch or fetch_player_info
    need = need or (lambda s: not store.card_price_fresh(conn, s, day))
    done = skipped = 0
    todo = [s for s in dict.fromkeys(spids) if need(s)]
    for i, spid in enumerate(todo):
        if cancel() or not config.WEB_DATA:
            break
        if not store.budget_take(conn, day, kind, cap):
            skipped = len(todo) - i
            break
        try:
            info = fetch(spid)
        except PlayerInfoError:
            continue
        save_card(conn, info, day)
        done += 1
        if on_card is not None:
            on_card(info)
    return done, skipped


def collect_prices(conn, spids, day: str, cap: int, cancel=lambda: False, fetch=None) -> tuple[int, int]:
    """가계부 평가용 시세(쓰임 KIND_LEDGER) — collect_cards 와 같고 계수만 가계부 몫."""
    return collect_cards(conn, spids, day, KIND_LEDGER, cap, cancel=cancel, fetch=fetch)
