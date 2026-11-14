"""넥슨 데이터센터에서 감독모드 랭킹 시즌표를 가져온다.

경기 상세의 matchDetail.seasonId(202606 꼴)가 시즌 번호다 — 108 B 부터 화면은 이 번호로 묶는다
(아래 SeasonMap). 데이터센터 랭킹 페이지의 시즌 목록은 그 번호 묶음에 이름·기간을 붙이고, 번호가 없는
경기·달력 칸·거래 같은 "시각" 은 지금처럼 그 기간표로 시즌에 나눠 담는다.

    https://fconline.nexon.com/datacenter/rank?rt=manager
    → <a onclick="ChangeSeason(89);"><span>시즌 3 (2026-05-28~2026-07-30)</span></a>

주의 1: rt 에 따라 시즌 번호가 다르다(공식경기 91 = 감독모드 89, 기간은 같다).
        이 앱은 감독모드만 다루므로 항상 rt=manager 로 받는다.
주의 2: 앞 시즌의 종료일 == 다음 시즌의 시작일이다. 그래서 종료일은 포함이
        아니라 경계로 보고 **[시작, 종료)** 반개구간으로 자른다 — 안 그러면
        경계일 경기가 두 시즌에 겹쳐 들어간다.
        경계 시각은 종료일 0시가 아니라 **종료일 config.SEASON_CUTOFF_H 시**(KST) — 넥슨은
        종료일 새벽까지 옛 시즌 경기를 받고 점검 뒤에 새 시즌을 연다(2026-11-02 실측).
        시각(datetime)은 이 시각으로 가르고, 날짜(date)만 주면 하루를 쪼갤 수 없어 경계일은
        새 시즌이다(달력 칸). 시즌표 날짜 자체(start·end)는 넥슨 글 그대로 둔다.
주의 3: 진행 중인 시즌은 목록에 안 올라온다(2026-08-03 확인 — 최신 항목이
        07-30 에 끝나 있었다). 마지막 종료일 이후 경기는 시즌 없음(None)으로
        오고, 화면에서 "진행 중"으로 묶는다.

ranker.py 와 같은 원칙: URL·정규식 지식을 이 파일에만 둔다. 넥슨이 페이지를
바꾸면 아래 상수만 고치면 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time

import config
import ranker  # 세션(연결 재사용·UA 헤더)과 동시 상한(web_get)을 그대로 쓴다

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

    @property
    def start_at(self) -> datetime:
        return boundary_at(self.start)

    @property
    def end_at(self) -> datetime:
        return boundary_at(self.end)

    def contains(self, when: datetime | date) -> bool:
        if isinstance(when, datetime):
            return self.start_at <= when < self.end_at
        return self.start <= when < self.end


def boundary_at(d: date) -> datetime:
    """시즌표 날짜(종료일 == 다음 시작일) → 시즌이 실제로 바뀌는 KST 시각(머리말 주의 2)."""
    return datetime.combine(d, time(config.SEASON_CUTOFF_H))


def in_ongoing(seasons: list[Season], when: datetime | date | None) -> bool:
    """진행 중 시즌(마지막 종료 경계 이후)인가 — 시즌표가 비면 거짓."""
    last_end = max((s.end for s in seasons), default=None)
    if when is None or last_end is None:
        return False
    if isinstance(when, datetime):
        return when >= boundary_at(last_end)
    return when >= last_end


class SeasonError(Exception):
    pass


def fetch_seasons(timeout: int = 10) -> list[Season]:
    """시즌 목록을 최신순으로. 하나도 못 뽑으면 SeasonError."""
    if not config.WEB_DATA:
        raise SeasonError(config.WEB_DATA_OFF_MSG)
    try:
        res = ranker.web_get(ranker._session, SEASON_LIST_URL, params={"rt": "manager"},
                             timeout=timeout)
        res.raise_for_status()
    except Exception as e:  # requests 예외 전부 — 호출부는 SeasonError 만 안다
        raise SeasonError(f"시즌 목록 조회 실패: {e}") from e

    html = res.text
    if "점검 진행 중" in html or "fc_logo_inspection" in html:
        raise SeasonError("넥슨 웹 점검 중입니다. 잠시 뒤 다시 시도해 주세요.")

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
    buckets: dict[int, list] = {}
    ongoing: list = []
    for item in items:
        when = key(item)
        s = season_of(seasons, when)
        if s is not None:
            buckets.setdefault(s.no, []).append(item)
        elif in_ongoing(seasons, when):
            ongoing.append(item)
    out: list[tuple[Season | None, list]] = []
    if ongoing:
        out.append((None, ongoing))
    for s in sorted(seasons, key=lambda x: x.start, reverse=True):
        if buckets.get(s.no):
            out.append((s, buckets[s.no]))
    return out


# ── 108 B — 경기의 시즌 번호(matchDetail.seasonId)로 묶기 ───────────────────
# 시즌표가 아는 시즌은 지금과 똑같이(Season), 시즌표가 모르는 번호만 저장된 경기로 세운다(DataSeason).
# 2026-11-13 실측: 34,659경기 전부 번호가 있고, 번호 묶음과 시즌표(8시 경계) 묶음의 어긋남은 0 이다.
# 시즌표 이름("시즌 N")은 해마다 겹쳐(68번 "시즌 1" 이 2023-12 시작) 이름으로는 짝짓지 않는다 —
# 번호 묶음의 경기 시각 중앙값이 든 시즌표 줄로 짝짓는다.

ONGOING = "ongoing"   # 고른 시즌이 "진행 중" 일 때의 값(app_main 콤보 항목 데이터와 같다)


class _Outside:
    """`.at(when)` — 저장 시작 전이나 묶음 사이 빈 틈. "이번 시즌"(None)과 다르다."""
    label = "시즌 밖"

    def __repr__(self) -> str:
        return "OUTSIDE"


OUTSIDE = _Outside()


class DataSeason:
    """시즌표가 모르는 시즌 — 그 번호의 저장된 경기로 기간을 세운다. 같음·해시는 번호만(새 경기 한 판에
    last·games 가 바뀌어도 콤보가 같은 줄을 되찾게)."""

    __slots__ = ("sid", "start_at", "end_at", "first", "last", "games")

    def __init__(self, sid: int, start_at: datetime, end_at: datetime | None,
                 first: datetime, last: datetime, games: int):
        self.sid, self.start_at, self.end_at = sid, start_at, end_at
        self.first, self.last, self.games = first, last, games

    @property
    def no(self) -> int:
        return -self.sid   # 시즌표 번호와 안 겹치게(설정 키 s-202606)

    @property
    def name(self) -> str:
        return f"시즌 {self.sid % 100}"

    @property
    def label(self) -> str:
        return f"{self.sid // 100} {self.name}"

    @property
    def start(self) -> date:
        return self.first.date()

    @property
    def end(self) -> date:
        return self.last.date()

    @property
    def span_text(self) -> str:
        return f"저장된 경기 {self.start:%Y-%m-%d} ~ {self.end:%Y-%m-%d}"

    def contains(self, when: datetime | date) -> bool:
        if isinstance(when, datetime):
            return self.start_at <= when and (self.end_at is None or when < self.end_at)
        return self.start_at.date() <= when and (self.end_at is None or when < self.end_at.date())

    def __eq__(self, other):
        if not isinstance(other, DataSeason):
            return NotImplemented
        return self.sid == other.sid

    def __hash__(self) -> int:
        return hash(("DataSeason", self.sid))

    def __repr__(self) -> str:
        return f"DataSeason({self.sid}, {self.games}경기)"


class SeasonMap:
    """경기 목록을 시즌으로 한 번 나눈 것 — 콤보·시즌별 표·필터·달력이 같이 쓴다.

    `.groups` 는 group_by_season 과 같은 꼴(최신순 · None = 진행 중). 경기는 자기 번호의 묶음에 든다
    (시각 경계 8시는 어림이고 번호가 정본이다). 번호가 없는 경기는 시각으로(시즌표 → 진행 중 → DataSeason)."""

    def __init__(self, table: list[Season], matches: list, key=lambda m: m.match_date,
                 sid=lambda m: getattr(m, "season_id", None)):
        self.table = table
        last_end = max((s.end for s in table), default=None)
        self._table_end = boundary_at(last_end) if last_end else None

        by_sid: dict[int, list] = {}
        no_sid: list = []
        for m in matches:
            v = sid(m)
            if isinstance(v, int) and not isinstance(v, bool) and key(m) is not None:
                by_sid.setdefault(v, []).append(m)
            else:
                no_sid.append(m)
        times = {v: sorted(key(m) for m in ms) for v, ms in by_sid.items()}

        # 번호 → 시즌(규칙 1~3)
        sid_season: dict[int, object] = {}
        unmatched: list[int] = []
        for v in sorted(times):
            ts = times[v]
            s = season_of(table, ts[len(ts) // 2])
            if s is not None:
                sid_season[v] = s
            else:
                unmatched.append(v)
        newest = max(unmatched, default=None)
        firsts = {v: times[v][0] for v in times}
        data_sids = []
        for v in unmatched:
            mid = times[v][len(times[v]) // 2]
            if (table and v == newest and self._table_end is not None and mid >= self._table_end):
                sid_season[v] = None   # 진행 중
            else:
                data_sids.append(v)
        for v in data_sids:
            later = [firsts[w] for w in times if w > v]
            sid_season[v] = DataSeason(v, firsts[v], min(later) if later else None,
                                       times[v][0], times[v][-1], len(times[v]))
        self._sid_season = sid_season
        self._data = sorted((s for s in sid_season.values() if isinstance(s, DataSeason)),
                            key=lambda s: s.sid, reverse=True)

        # 진행 중의 시작: 시즌표 마지막 경계와 가장 새 DataSeason 의 끝 중 늦은 쪽
        cands = [t for t in (self._table_end,
                             self._data[0].end_at if self._data else None) if t is not None]
        self.ongoing_start: datetime | None = max(cands) if cands else None

        # 경기 → 시즌(받은 순서 그대로 — group_by_season 과 같다)
        of: dict[int, object] = {}
        buckets: dict[object, list] = {}
        ongoing: list = []
        no_sid_ids = {id(m) for m in no_sid}
        for m in matches:
            if id(m) in no_sid_ids:
                s = self._at_time(key(m))
                if s is OUTSIDE:
                    continue   # 어느 시즌에도 안 걸린다(group_by_season 과 같다 — 묶음에서 뺀다)
            else:
                s = sid_season[sid(m)]
            of[id(m)] = s
            (ongoing if s is None else buckets.setdefault(s, [])).append(m)
        self._of = of
        self._keep = matches   # id(m) 가 재사용되지 않게 목록을 쥔다
        groups: list[tuple[object, list]] = [(None, ongoing)] if ongoing else []
        for s in sorted(buckets, key=lambda s: s.start, reverse=True):
            groups.append((s, buckets[s]))
        self.groups = groups
        self.known = bool(table) or bool(self._data)

    def _at_time(self, when):
        if when is None:
            return OUTSIDE
        s = season_of(self.table, when)
        if s is not None:
            return s
        for d in self._data:
            if d.contains(when):
                return d
        if self.ongoing_start is not None:
            start = self.ongoing_start if isinstance(when, datetime) else self.ongoing_start.date()
            if when >= start:
                return None
        return OUTSIDE

    def at(self, when: datetime | date | None):
        """시각·날짜 → Season | DataSeason | None(진행 중) | OUTSIDE — 경기가 아닌 것(달력 칸 · 거래)."""
        return self._at_time(when)

    def of(self, m):
        """경기 → 시즌(번호로). 이 맵을 세운 목록의 경기면 그 묶음 그대로."""
        if id(m) in self._of:
            return self._of[id(m)]
        v = getattr(m, "season_id", None)
        if v in self._sid_season:
            return self._sid_season[v]
        return self._at_time(getattr(m, "match_date", None))

    def has(self, m, selected) -> bool:
        """고른 시즌(Season · DataSeason · ONGOING)에 드는 경기인가 — 남의 계정 경기도 답한다.
        번호가 이 맵에 없으면(번호 없음 · 내가 안 뛴 시즌) 고른 시즌의 기간으로."""
        if id(m) in self._of or getattr(m, "season_id", None) in self._sid_season:
            s = self.of(m)
            return s is None if selected == ONGOING else (s is not None and s is not OUTSIDE and s == selected)
        when = getattr(m, "match_date", None)
        if when is None:
            return False
        if selected == ONGOING:
            return self.ongoing_start is not None and when >= self.ongoing_start
        return selected.contains(when)

    def by_sid(self, sid: int):
        """그 번호가 든 묶음의 시즌 — 진행 중이거나 그 번호가 없으면 None(가르려면 has_sid)."""
        return self._sid_season.get(sid)

    @property
    def odd_boundary_days(self) -> set[date]:
        """경계가 8시가 아닌 날(DataSeason 의 시작·끝 = 그 번호의 첫 경기 시각) — 날짜·8시 앞뒤로 캐시하는 쪽이 피한다."""
        return ({d.start_at.date() for d in self._data}
                | {d.end_at.date() for d in self._data if d.end_at is not None}
                | ({self.ongoing_start.date()} if self.ongoing_start is not None else set()))

    def has_sid(self, sid: int) -> bool:
        return sid in self._sid_season
