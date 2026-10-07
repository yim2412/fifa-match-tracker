"""팀컬러 효과표(2.2.1) — 넥슨 데이터센터의 팀컬러 목록·단계별 효과·적용 선수.

    목록  GET  https://fconline.nexon.com/datacenter/teamcolor                      (헤더 없이 200 · 801개 · 1.2MB)
    상세  GET  https://fconline.nexon.com/datacenter/TeamColorDetail?teamcolorid=N  (X-Requested-With 필요)
    선수  GET  https://fconline.nexon.com/DataCenter/TeamColorPlayerList?<양식>     (X-Requested-With 필요 · JSON · 최대 100명)

문서 없는 주소라 형식이 바뀌면 깨진다 — 주소·앵커는 전부 이 파일 상단에. 상세·선수는 넥슨 사이트 자신의 스크립트가
같은 페이지에서 보내는 표시 헤더(X-Requested-With)가 있어야 200 이고, 없으면 302 로 오류 페이지에 넘긴다(ROADMAP 2.2.1 T4·T5·U4).
그래서 **넘김은 따라가지 않고 오류**로 본다(allow_redirects=False — 오류 페이지 주소엔 가지 않는다).
요청은 사용자가 효과 창을 열거나 [더 보기]를 누를 때만(자동 요청 0) · 웹 데이터가 꺼져 있으면 0.

이름이 같은 팀컬러 11쌍(config.TEAMCOLOR_DUP_NAMES)은 엠블럼으로 가른다 — 화면의 팀컬러 키는 label() 한 글자.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
팀컬러 효과표(2.2.1) — 데이터센터 목록(`fetch_list`)·상세 단계(`fetch_detail`)·적용 선수 JSON(`fetch_players` — 한 번 100명, `more_players` 가 OVR 상한으로 이어 받고 같은 OVR 이 100명을 넘으면 상한 −1).
상세·선수는 **`X-Requested-With` 헤더가 있어야** 200(사용자 U4 — 붙인다) · `allow_redirects=False`(302 = 오류, 오류 페이지 주소엔 안 간다) ·
실패 종류 `TeamColorError.kind`(off·down·format·rate) → 문구 `MESSAGES`.
**이름이 같은 팀컬러 11쌍**(`config.TEAMCOLOR_DUP_NAMES` — 상수, 받은 목록으로 넓히지 않는다: 창을 열기 전후로 표가 바뀐다)은 화면 키 `label(이름, 엠블럼)`("이름 ·
강화"/"이름 · 클럽"/"이름 (구분 전)")으로 가르고, 랭커 쪽과 맞댈 땐 `name_of`. 효과 창은 `app_main.TeamColorDialog`(모달 아님 ·
하나만) + `TeamColorEffectLoader`(캐시 `store.load_teamcolor_meta/steps` 7일 ·
받은 선수 급여는 `store.save_card_salary` — 급여 열만, JSON 의 OVR 은 선수 페이지와 달라 안 쓴다)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape

import requests

import config
import ranker  # web_get(동시 상한) · emblem_key

LIST_URL = "https://fconline.nexon.com/datacenter/teamcolor"
DETAIL_URL = "https://fconline.nexon.com/datacenter/TeamColorDetail"
PLAYERS_URL = "https://fconline.nexon.com/DataCenter/TeamColorPlayerList"
_XHR = {"X-Requested-With": "XMLHttpRequest"}
TIMEOUT_S = 10   # 앱 종료 표가 이 + 2초를 기다린다(app_main.shutdown)
# 선수 목록 양식 — 상세 페이지의 teacolorSearchform 그대로(2026-10-06). 포지션 필터는 안 쓴다(GK 코드가 0명 — T8)
PLAYERS_ORDER = "overallrating descending, salary descending"
# 오류 페이지·점검 표시 — 200 으로 와도 이걸 품으면 실패
_ERROR_MARKERS = ("bulletin.nexon.com", "nxk/error", "점검 진행 중", "fc_logo_inspection")

# 목록 한 항목 — 앵커는 wrapper class(라벨 글자 아님)
_ITEM_START = '<div class="teamcolor_item">'
_ITEM_ID = re.compile(r"GetTeamColorDetail\((\d+)\)")
_ITEM_IMG = re.compile(r'<img src="([^"]+)"')
_ITEM_NUM = re.compile(r'<div class="num">\s*(\d+)\s*<')
_ITEM_NAME = re.compile(r'<div class="name">\s*([^<]+?)\s*</div>')
_ITEM_LEVEL = re.compile(r'<div class="level">\s*(\d+)\s*단계')
_ITEM_EFFECT = re.compile(r'<span class="item">\s*([^<]+?)\s*</span>')
# 상세의 단계 — <div class="level lv1"> · 단계 하나짜리는 "lvs1"
_STEP_START = re.compile(r'<div class="level lvs?\d+">')
_STEP_NO = re.compile(r'<div class="tit">\s*(\d+)\s*단계\s*</div>')
_STEP_NUM = _ITEM_NUM
_STEP_EFFECT = re.compile(r"<li>\s*([^<]+?)\s*</li>")
_DETAIL_DESC = re.compile(r'<div class="tit"><strong>[^<]*</strong><span>\s*([^<]*?)\s*</span>')
_DETAIL_END = 'class="content_bottom"'

# 이름 꼬리표 — 이름이 같은 두 팀컬러를 가르는 엠블럼 종류(ranker.emblem_key 의 앞 칸)
KIND_LABEL = {"teamcolorboost": "강화", "crests": "클럽", "countries": "국가"}
UNSPLIT = "구분 전"
_SEP = " · "


class TeamColorError(Exception):
    """kind: "off"(웹 데이터 꺼짐) · "down"(넘김·5xx·연결 실패·점검) · "format"(형식이 바뀜) · "rate"(429)."""

    def __init__(self, message: str, kind: str = "down"):
        super().__init__(message)
        self.kind = kind


MESSAGES = {
    "off": config.WEB_DATA_OFF_MSG,
    "down": "넥슨 홈페이지가 응답하지 않습니다(점검 중일 수 있음)",
    "format": "넥슨 페이지 형식이 바뀌었을 수 있습니다",
    "rate": "요청이 많아 넥슨이 잠시 막았습니다 — 잠시 뒤 다시 시도해 주세요",
}


@dataclass
class TeamColorMeta:
    id: int
    name: str
    emblem: str            # ranker.emblem_key
    emblem_url: str
    max_step: int | None
    members: int | None    # 최고 단계 인원
    effects: list[str] = field(default_factory=list)   # 최고 단계 효과

    @property
    def label(self) -> str:
        return label(self.name, self.emblem)


@dataclass
class Step:
    step: int
    members: int | None
    effects: list[str] = field(default_factory=list)


@dataclass
class TeamColorPlayer:
    spid: int
    name: str
    position: str
    ovr: int | None
    pay: int | None
    prices: dict[int, int] = field(default_factory=dict)   # 강화 → BP(모르는 값은 빠진다 — 0 이 아니다)


# ── 팀컬러 키(글자 하나) ──────────────────────────────────────────────────────

emblem_key = ranker.emblem_key


def label(name: str, emblem: str | None) -> str:
    """화면·표의 팀컬러 키 — 겹치는 이름이 아니면 이름 그대로(99.4%). 겹치면 "이름 · 강화"/"이름 · 클럽",
    엠블럼을 모르면 "이름 (구분 전)"."""
    if not name or name not in config.TEAMCOLOR_DUP_NAMES:
        return name or ""
    if not emblem:
        return f"{name} ({UNSPLIT})"
    kind = emblem.split("/", 1)[0]
    return f"{name}{_SEP}{KIND_LABEL.get(kind, kind)}"


def name_of(text: str) -> str:
    """label → 넥슨 이름. 꼬리표를 뗀 이름이 겹치는 이름일 때만 뗀다 — "20시즌 울산 (ACL 우승)" 같은 진짜 이름은 그대로(T18)."""
    if not text:
        return ""
    for tail in [f" ({UNSPLIT})"] + [f"{_SEP}{v}" for v in KIND_LABEL.values()]:
        if text.endswith(tail) and text[:-len(tail)] in config.TEAMCOLOR_DUP_NAMES:
            return text[:-len(tail)]
    if _SEP in text and text.rsplit(_SEP, 1)[0] in config.TEAMCOLOR_DUP_NAMES:
        return text.rsplit(_SEP, 1)[0]
    return text


def is_unsplit(text: str) -> bool:
    return text.endswith(f" ({UNSPLIT})") and name_of(text) != text


# ── 읽기(파싱) ───────────────────────────────────────────────────────────────

def parse_list(html: str) -> list[TeamColorMeta]:
    """목록 페이지 → 항목 전부. 한 항목 실패는 그 항목만 빼고, 절반 넘게 실패하거나 0개면 TeamColorError("format")
    — 빈 목록을 "팀컬러 없음"으로 읽지 않게."""
    chunks = html.split(_ITEM_START)[1:]
    out = []
    for c in chunks:
        try:
            tid = int(_ITEM_ID.search(c).group(1))
            name = " ".join(unescape(_ITEM_NAME.search(c).group(1)).split())
            img = _ITEM_IMG.search(c).group(1)
        except (AttributeError, ValueError):
            continue
        num = _ITEM_NUM.search(c)
        lv = _ITEM_LEVEL.search(c)
        out.append(TeamColorMeta(tid, name, ranker.emblem_key(img), img,
                                 int(lv.group(1)) if lv else None, int(num.group(1)) if num else None,
                                 [unescape(e) for e in _ITEM_EFFECT.findall(c)]))
    if not out or len(out) * 2 < len(chunks):
        raise TeamColorError(f"팀컬러 목록을 읽지 못했습니다({len(out)}/{len(chunks)})", "format")
    return out


def parse_detail(html: str) -> tuple[str, list[Step]]:
    """상세 → (설명 한 줄, 단계들). 단계를 하나도 못 읽으면 TeamColorError("format")."""
    end = html.find(_DETAIL_END)
    head = html[:end] if end >= 0 else html
    m = _DETAIL_DESC.search(head)
    desc = unescape(m.group(1)) if m else ""
    starts = [m.start() for m in _STEP_START.finditer(head)] + [len(head)]
    steps = []
    for a, b in zip(starts, starts[1:]):
        c = head[a:b]
        no = _STEP_NO.search(c)
        if not no:
            continue
        num = _STEP_NUM.search(c)
        steps.append(Step(int(no.group(1)), int(num.group(1)) if num else None,
                          [unescape(e) for e in _STEP_EFFECT.findall(c) if e.strip() not in ("-", "")]))
    if not steps:
        raise TeamColorError("팀컬러 단계를 읽지 못했습니다", "format")
    return desc, steps


def _int(v) -> int | None:
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_prices(each: str) -> dict[int, int]:
    """"0|152,000|…" 14칸(0~13강, 0번 칸은 "0") → {강화: BP}. 0 이하·빈 값은 안 넣는다(playerinfo.prices_as_int 와 같은 규칙)."""
    out = {}
    for grade, text in enumerate((each or "").split("|")):
        v = _int(text)
        if 1 <= grade <= 13 and v is not None and v > 0:
            out[grade] = v
    return out


def parse_players(text: str) -> list[TeamColorPlayer]:
    """선수 JSON → 목록(받은 순서 = OVR 높은 순). 한 명 실패는 그 사람만. JSON 이 아니거나 players 가 없으면 format."""
    try:
        data = json.loads(text)
        items = data["players"]
        if not isinstance(items, list):
            raise TypeError
    except (ValueError, KeyError, TypeError) as e:
        raise TeamColorError(f"팀컬러 선수 목록을 읽지 못했습니다: {e}", "format") from e
    out = []
    for p in items:
        if not isinstance(p, dict):
            continue
        spid = _int(p.get("spid"))
        if spid is None:
            continue
        out.append(TeamColorPlayer(spid, str(p.get("name") or "-"), str(p.get("position") or "").upper(),
                                   _int(p.get("ovr")), _int(p.get("pay")), parse_prices(p.get("eachPrice") or "")))
    return out


# ── 받기 ─────────────────────────────────────────────────────────────────────

_session = requests.Session()
_session.headers.update({"User-Agent": config.WEB_USER_AGENT})


def _get(url: str, params=None, xhr: bool = True) -> str:
    if not config.WEB_DATA:
        raise TeamColorError(config.WEB_DATA_OFF_MSG, "off")
    try:
        res = ranker.web_get(_session, url, params=params, headers=_XHR if xhr else None,
                             timeout=TIMEOUT_S, allow_redirects=False)
    except requests.RequestException as e:
        raise TeamColorError(f"팀컬러 조회 실패: {e}", "down") from e
    status = getattr(res, "status_code", 200)
    if status == 429:
        raise TeamColorError("팀컬러 조회가 막혔습니다(HTTP 429)", "rate")
    if status != 200:   # 302(오류 페이지로 넘김) · 5xx 등 — 넘김은 따라가지 않는다
        raise TeamColorError(f"팀컬러 조회 실패(HTTP {status})", "down")
    body = res.text
    if any(m in body[:4096] for m in _ERROR_MARKERS):
        raise TeamColorError("넥슨 오류·점검 페이지가 왔습니다", "down")
    return body


def fetch_list() -> list[TeamColorMeta]:
    return parse_list(_get(LIST_URL, xhr=False))


def fetch_detail(teamcolor_id: int) -> tuple[str, list[Step]]:
    return parse_detail(_get(DETAIL_URL, {"teamcolorid": teamcolor_id}))


def players_form(teamcolor_id: int, ovr_max: int | None = None) -> dict:
    return {"n1Confederation": 0, "n4LeagueId": 0, "teamcolorid": teamcolor_id, "n4TeamId": 0, "n4NationId": 0,
            "strSeason": "", "strPosition": "", "strOrderby": PLAYERS_ORDER, "strPlayerName": "",
            "n4OvrMin": "", "n4OvrMax": "" if ovr_max is None else ovr_max, "n4SalaryMin": "", "n4SalaryMax": ""}


def fetch_players(teamcolor_id: int, ovr_max: int | None = None) -> list[TeamColorPlayer]:
    """OVR 높은 순 최대 TEAMCOLOR_PLAYERS_PAGE 명. ovr_max 를 주면 그 OVR 이하부터(이어 받기 — 쪽 넘김이 없다, T6·T7)."""
    return parse_players(_get(PLAYERS_URL, players_form(teamcolor_id, ovr_max)))


def more_players(teamcolor_id: int, have: list[TeamColorPlayer], fetch=None
                 ) -> tuple[list[TeamColorPlayer], bool]:
    """[더 보기] — 다음 OVR 상한으로 받아 spid 로 겹침을 뺀다 → (새로 받은 선수, 끝났나).
    새 선수가 0 이면 같은 OVR 이 한 쪽(100명)을 넘는 경우라 상한을 1 내려 한 번 더. 그래도 0 이거나 받은 수 < 100 이면 끝."""
    fetch = fetch or fetch_players
    page = config.TEAMCOLOR_PLAYERS_PAGE
    if not have:
        got = fetch(teamcolor_id, None)
        return got, len(got) < page
    seen = {p.spid for p in have}
    ovr = next((p.ovr for p in reversed(have) if p.ovr is not None), None)
    if ovr is None:
        return [], True
    for cap in (ovr, ovr - 1):
        got = fetch(teamcolor_id, cap)
        new = [p for p in got if p.spid not in seen]
        if new:
            return new, len(got) < page
        if len(got) < page:
            return [], True
    return [], True
