"""넥슨 데이터센터에서 감독모드 랭킹 시즌표를 가져온다.

오픈API 는 경기에 시즌을 달아주지 않는다(matchDate 만 준다). 대신 데이터센터
랭킹 페이지가 시즌 목록을 기간과 함께 갖고 있어서, 그 기간표로 경기를 시즌에
나눠 담는다.

    https://fconline.nexon.com/datacenter/rank?rt=manager
    → <a onclick="ChangeSeason(89);"><span>시즌 3 (2026-05-28~2026-07-30)</span></a>

주의 1: rt 에 따라 시즌 번호가 다르다(공식경기 91 = 감독모드 89, 기간은 같다).
        이 앱은 감독모드만 다루므로 항상 rt=manager 로 받는다.
주의 2: 앞 시즌의 종료일 == 다음 시즌의 시작일이다. 그래서 종료일은 포함이
        아니라 경계로 보고 **[시작, 종료)** 반개구간으로 자른다 — 안 그러면
        경계일 경기가 두 시즌에 겹쳐 들어간다.
주의 3: 진행 중인 시즌은 목록에 안 올라온다(2026-08-03 확인 — 최신 항목이
        07-30 에 끝나 있었다). 마지막 종료일 이후 경기는 시즌 없음(None)으로
        오고, 화면에서 "진행 중"으로 묶는다.

ranker.py 와 같은 원칙: URL·정규식 지식을 이 파일에만 둔다. 넥슨이 페이지를
바꾸면 아래 상수만 고치면 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import ranker  # 세션(연결 재사용·UA 헤더)을 그대로 쓴다

SEASON_LIST_URL = "https://fconline.nexon.com/datacenter/rank"

# onclick="ChangeSeason(89);" ... <span>시즌 3 (2026-05-28~2026-07-30)</span>
# "전체 시즌"(ChangeSeason(0))은 기간이 없어 이 정규식에 안 걸린다 — 의도한 것.
_SEASON_ROW = re.compile(
    r'ChangeSeason\((\d+)\);"[^>]*>\s*<span>\s*([^<(]*?)\s*'
    r'\((\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})\)\s*</span>')


@dataclass(frozen=True)
class Season:
    no: int
    name: str        # "시즌 3" — 연도가 안 붙어 있어 그대로는 해마다 겹친다
    start: date
    end: date        # 다음 시즌 시작일과 같다. 포함하지 않는 경계다

    @property
    def label(self) -> str:
        """해마다 "시즌 3"이 반복되므로 시작 연도를 붙여 구분한다."""
        return f"{self.start.year} {self.name}"

    @property
    def span_text(self) -> str:
        return f"{self.start:%Y-%m-%d} ~ {self.end:%Y-%m-%d}"

    def contains(self, when: datetime | date) -> bool:
        d = when.date() if isinstance(when, datetime) else when
        return self.start <= d < self.end


class SeasonError(Exception):
    pass


def fetch_seasons(timeout: int = 10) -> list[Season]:
    """시즌 목록을 최신순으로. 하나도 못 뽑으면 SeasonError."""
    try:
        res = ranker._session.get(SEASON_LIST_URL, params={"rt": "manager"},
                                  timeout=timeout)
        res.raise_for_status()
    except Exception as e:  # requests 예외 전부 — 호출부는 SeasonError 만 안다
        raise SeasonError(f"시즌 목록 조회 실패: {e}") from e

    html = res.text
    if "점검 진행 중" in html or "fc_logo_inspection" in html:
        raise SeasonError("넥슨 웹 점검 중입니다 — 잠시 후 다시 시도해주세요")

    out: list[Season] = []
    for no, name, start, end in _SEASON_ROW.findall(html):
        try:
            out.append(Season(no=int(no), name=" ".join(name.split()),
                              start=date.fromisoformat(start),
                              end=date.fromisoformat(end)))
        except ValueError:
            continue  # 한 줄이 깨져도 나머지 시즌은 살린다
    if not out:
        raise SeasonError("시즌 목록을 찾지 못했습니다 — 페이지 구조가 바뀐 듯합니다")
    out.sort(key=lambda s: s.start, reverse=True)
    return out


def season_of(seasons: list[Season], when: datetime | date | None) -> Season | None:
    """이 경기가 속한 시즌. 진행 중(마지막 종료일 이후)이거나 날짜가 없으면 None."""
    if when is None:
        return None
    for s in seasons:
        if s.contains(when):
            return s
    return None


def group_by_season(seasons: list[Season], items: list,
                    key) -> list[tuple[Season | None, list]]:
    """items 를 시즌별로 묶어 최신순으로 돌려준다. key(item) → datetime|None.

    시즌이 없는 것(진행 중)은 맨 앞에 (None, [...]) 으로 온다. 어느 시즌에도
    안 걸리는 아주 오래된 경기는 데이터센터 목록이 2018년까지 있어 사실상
    없지만, 있으면 같은 None 그룹에 섞이지 않도록 날짜로 한 번 걸러 낸다.
    """
    ongoing_from = max((s.end for s in seasons), default=None)
    buckets: dict[int, list] = {}
    ongoing: list = []
    for item in items:
        when = key(item)
        s = season_of(seasons, when)
        if s is not None:
            buckets.setdefault(s.no, []).append(item)
        elif when is not None and ongoing_from is not None:
            d = when.date() if isinstance(when, datetime) else when
            if d >= ongoing_from:
                ongoing.append(item)
    out: list[tuple[Season | None, list]] = []
    if ongoing:
        out.append((None, ongoing))
    for s in sorted(seasons, key=lambda x: x.start, reverse=True):
        if buckets.get(s.no):
            out.append((s, buckets[s.no]))
    return out
