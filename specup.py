"""스펙업 순서(121단계) — 내 팀컬러 랭커들이 많이 쓰는 카드 가운데 1억 BP당 OVR 이 가장 많이 오르는 구매부터 세운다.
계산만(Qt·네트워크 없음 · 화면은 `core_api`). 정본은 ROADMAP "## 121단계".

| 무엇 | 규칙 |
|---|---|
| 기준 한 사람 | 팀컬러·구단가치는 마지막 스냅숏의 **내 계정**(`store.my_account`) 줄, 선발은 그 계정의 선발 11명이 있는 가장 최근 경기(표시한 판은 뺀다 · M6). 검색한 구단주가 아니다 |
| 팀컬러 번호 | 스냅숏은 이름이라 (이름, 엠블럼)을 팀컬러 목록(`teamcolor_meta`)과 맞대 번호로 바꾼다. 랭커도 이름이 아니라 이 번호로 고른다. 겹치는 이름은 목록에서 센다(`config.TEAMCOLOR_DUP_NAMES` 상수는 새로 생긴 쌍을 모른다 — 11-24 실측 목록 844개에 13쌍) |
| 후보 카드 | 같은 팀컬러 랭커의 선발에서 같은 자리 줄(GK·DF·MF·FW)에 `SPECUP_MIN_USERS` 명 넘게 쓴 카드. 강화는 후보 조건이 아니라 사는 값의 종류다(`SPECUP_GRADES`) |
| 팀컬러 유지 | 카드의 소속 ∪ 관계 팀컬러에 내 번호가 있어야 한다. 시뮬레이터를 안 읽은 카드는 모름(빼고 센다) |
| OVR | `cardstats.effective_ovr` — **내 선발 그 자리**의 `spPosition` 으로(랭커가 쓴 자리가 아니다). 모르면 빼고 센다. 지금 카드의 OVR 을 모르면 그 자리는 계산할 수 없음 |
| 걸음 비용 | 다음 카드 값 전체(거쳐 간 카드 값은 돌려받지 않는다 · 판매값은 안 센다 U7) |
| 거르기 | 지금 OVR 이하 · 더 비싼데 OVR 이 같거나 낮은 후보를 지운다(파레토 · 볼록 껍질이 아니다) |
| 묶음 | 앞 카드를 건너뛰고 뒤 카드를 **바로 사는** 1억당 효율이 앞 카드 효율의 `ratio` 배보다 높으면 건너뛴다(앞 카드 값이 안 버려진다). 줄마다 하나씩 줄어 반드시 끝나고, 끝나면 효율이 단조다 |
| 병합 | 자리마다 첫 걸음 가운데 효율이 가장 높은 것을 고르고 **고를 때마다 전체 후보에서 다시 계산**한다 — 그래서 빠진 칸·급여로 건너뛴 칸이 저절로 다시 들어온다(5a ⓒ · M4) |
| 같은 선수 | 지금 선발의 다른 자리에 있는 선수는 처음부터 뺀다. 한 자리가 산 선수는 다른 자리에서 못 쓴다(그 자리가 나중에 다른 카드로 바꿔도) |
| 급여 | 지금 팀 급여 합 − 그 자리 직전 카드 급여 + 새 카드 급여 ≤ 상한. 이미 상한을 넘었으면(라이브 급여가 바뀐 뒤) 합이 안 늘어나는 걸음은 둔다. 지금 카드 중 하나라도 급여를 모르면 안 막는다(`salary_on` 거짓) |
| 예산 | 앞에서부터 누적 금액으로 자른다 — 그 예산의 최적 조합이 아니다(6a) |
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import cardstats
import config
import rankcollect
import rankerpick
import store
import teamcolor
import teamcolor_calc
from stats import position_line

EOK = 100_000_000

# 순서표를 못 세우는 사유 — 화면 글은 notice 한 곳(조각 ③). 빈 글자면 낼 수 있다
NO_ME, NO_SNAPSHOT, NO_COLOR = "no_me", "no_snapshot", "no_color"            # 내 계정 미지정 · 스냅숏 없음 · 1만 위 밖
TC_LIST, TC_SPLIT, TC_ENHANCE = "tc_list", "tc_split", "tc_enhance"          # 팀컬러 목록에 없음 · 이름이 겹쳐 못 가름 · 강화 팀컬러(U15)
NO_LINEUP, FEW_RANKERS = "no_lineup", "few_rankers"                          # 선발 11명 경기 없음 · 같은 팀컬러 랭커가 모자람(U13)

# 자리 하나의 상태
SLOT_OK, SLOT_NO_OVR, SLOT_EMPTY = "", "no_ovr", "empty"                     # 걸음 있음 · 지금 카드 OVR 모름 · 살 카드가 없음


@dataclass(frozen=True)
class Slot:
    po: int
    spid: int
    grade: int | None

    @property
    def line(self) -> str | None:
        return position_line(self.po)


@dataclass(frozen=True)
class CandCard:
    spid: int
    line: str | None            # 자리 줄 — None 이면 어느 줄에나(카드 직접 넣기 · 5명 문턱 면제)
    users: int


@dataclass(frozen=True)
class Option:
    spid: int
    grade: int
    price: int
    ovr: int
    salary: int | None
    users: int = 0
    price_on: str = ""          # 시세를 읽은 날


@dataclass(frozen=True)
class SpecStep:
    slot: int                   # 선발 자리 번호(0~10)
    spid: int
    grade: int
    price: int
    ovr_gain: int               # 그 자리에서 오르는 OVR(기준 = 바로 앞 걸음의 카드)
    per_eok: float              # 1억 BP당 오르는 값 — U5 에서 승률로 바꿔도 이름은 이것 하나
    chunk: tuple = ()           # 건너뛴 카드 ((spid, 강화), …)
    from_spid: int = 0
    from_grade: int | None = None
    spent: int = 0              # 이 칸까지 누적 금액
    salary_sum: int | None = None   # 이 칸까지 산 뒤 팀 급여 합 — 급여를 모르면 None
    price_on: str = ""

    @property
    def spent_before(self) -> int:
        """앞 칸을 다 샀을 때까지 쓴 금액 — 추천 구단가치 구간(U8)은 지금 구단가치 + 이 값."""
        return self.spent - self.price


@dataclass(frozen=True)
class SlotState:
    slot: Slot
    ovr: int | None
    state: str = SLOT_OK


@dataclass
class Inputs:
    """작업 스레드가 한 번에 읽어 넘기는 재료 — 설정(강화·묶음·줄)을 바꿔도 다시 읽지 않는다."""
    reason: str = ""
    nickname: str = ""
    color: str = ""
    color_id: int | None = None
    value: int | None = None                # 지금 구단가치(스냅숏)
    taken_at: str | None = None
    lineup: tuple[Slot, ...] = ()
    rankers: int = 0                        # 선발을 아는 같은 팀컬러 랭커
    by_source: Counter = field(default_factory=Counter)
    cands: tuple[CandCard, ...] = ()
    prices: dict = field(default_factory=dict)      # (spid, 강화) → (값, 읽은 날)
    extras: dict = field(default_factory=dict)      # spid → card_extra 줄
    salaries: dict = field(default_factory=dict)    # spid → 급여
    manual: tuple[int, ...] = ()            # 직접 넣은 카드 — 시세·카드 정보를 같이 읽고 받는다
    records: dict = field(default_factory=dict)     # (spid, 자리) → 오늘 받아 둔 랭커 기록(말풍선에만 · 새로 안 받는다 U16)

    @property
    def want(self) -> tuple[int, ...]:
        """시세·카드 정보가 필요한 카드 — 후보 ∪ 지금 선발 ∪ 직접 넣은 카드."""
        return tuple(sorted({c.spid for c in self.cands} | {s.spid for s in self.lineup} | set(self.manual)))

    @property
    def fetch_order(self) -> tuple[int, ...]:
        """받는 순서(`specupcollect`) — 지금 선발 먼저(지금 카드의 OVR 을 모르면 그 자리 전체가 빈다), 직접 넣은 카드, 많이 쓴 후보."""
        cands = sorted(self.cands, key=lambda c: (-c.users, c.spid))
        return tuple(dict.fromkeys([s.spid for s in self.lineup] + list(self.manual) + [c.spid for c in cands]))

    @property
    def fetchable(self) -> bool:
        """받을 것이 있는 재료인가 — 팀컬러 목록을 못 받았거나(TC_LIST) 낡아서 못 가른(TC_SPLIT) 때는 목록을 새로 받으면 풀릴 수 있다.
        나머지 사유는 받아도 순서표가 안 선다."""
        return self.reason in ("", TC_LIST, TC_SPLIT)


@dataclass
class Plan:
    steps: list[SpecStep] = field(default_factory=list)
    slots: list[SlotState] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)    # 뺀 이유 → 어느 자리에도 못 세운 카드 수(시세·강화 이유는 카드×강화 수)
    why: dict = field(default_factory=dict)              # 어느 자리 어느 강화로도 못 세운 카드 → 뺀 이유(뺀 카드 수 · 직접 넣은 카드의 사유 글)
    salary_on: bool = False                              # 급여 상한을 실제로 걸었나
    salary_now: int | None = None


# ── 기준 · 팀컬러 번호 ────────────────────────────────────────────────────────

def resolve_color(name: str | None, emblem: str | None, meta: list[dict]) -> tuple[int | None, str]:
    """스냅숏의 (팀컬러 이름, 엠블럼 키) → (팀컬러 번호, 못 바꾼 사유). 엠블럼을 아는데 목록의 어느 것과도 안 맞으면 이름이 하나뿐이어도
    안 고른다 — 목록을 받은 뒤 같은 이름의 짝이 새로 생겼을 수 있다(11-24 실측에서 두 쌍이 새로 생겼다)."""
    if not name:
        return None, NO_COLOR
    emblem = emblem or ""
    if emblem.startswith(teamcolor.ENHANCE_EMBLEM):
        return None, TC_ENHANCE
    hits = [m for m in meta if m["name"] == name]
    if not hits:
        return None, TC_LIST
    if emblem:
        same = [m for m in hits if (m.get("emblem") or "") == emblem]
        if len(same) != 1:
            return None, TC_SPLIT
        hit = same[0]
    elif len(hits) == 1:
        hit = hits[0]
    else:
        return None, TC_SPLIT
    if str(hit.get("emblem") or "").startswith(teamcolor.ENHANCE_EMBLEM):
        return None, TC_ENHANCE
    return hit["id"], ""


def same_color_rows(rows: list[dict], emblems: dict[int, str], meta: list[dict], color_id: int,
                    me: str | None) -> list[dict]:
    """같은 팀컬러 **번호**인 랭커 줄(나는 닉네임으로 뺀다)."""
    memo: dict[tuple, int | None] = {}
    out = []
    for r in rows:
        if r["nickname"] == me:
            continue
        key = (r.get("team_color"), emblems.get(r["profile_sn"], ""))
        if key not in memo:
            memo[key] = resolve_color(*key, meta)[0]
        if memo[key] == color_id:
            out.append(r)
    return out


def pick_lineup(details: list[dict], ouid: str) -> tuple[Slot, ...]:
    """최신순 경기 → 선발 11명이 있는 가장 최근 경기의 내 선발(빈 판·몰수 판은 건너뛴다). 없으면 ()."""
    for d in details:
        side = next((s for s in d.get("matchInfo") or [] if s.get("ouid") == ouid), None)
        if side is None:
            continue
        ps = [p for p in cardstats.starters(side) if isinstance(p.get("spId"), int) and isinstance(p.get("spPosition"), int)]
        if len(ps) == 11:
            return tuple(Slot(p["spPosition"], p["spId"], p.get("spGrade")) for p in ps)
    return ()


def candidates(lineups: list, min_users: int | None = None) -> tuple[CandCard, ...]:
    """랭커 선발(rankerpick.Candidate 목록) → 자리 줄마다 min_users 명 넘게 쓴 카드. 한 사람은 같은 줄에서 한 번만 센다."""
    need = config.SPECUP_MIN_USERS if min_users is None else min_users
    users: Counter = Counter()
    for c in lineups:
        users.update({(position_line(p["spPosition"]), p["spId"]) for p in c.players})
    got = [CandCard(spid, line, n) for (line, spid), n in users.items() if line is not None and n >= need]
    return tuple(sorted(got, key=lambda c: (c.line, -c.users, c.spid)))


def load(fconn, rconn, pick=pick_lineup, today: date | None = None, manual=()) -> Inputs:
    """fifa.db · rank.db(둘 다 읽기만 · 요청 0) → 재료. pick 은 선발 고르는 함수 — 화면은 표시한 판을 거르는 `core_api.specup_lineup` 을 넘긴다.
    사유가 있으면 거기까지 읽은 것만 담는다(기준 줄에 보일 닉네임·팀컬러)."""
    out = Inputs(manual=tuple(dict.fromkeys(manual)))
    me = store.my_account(fconn, config.DEFAULT_MATCH_TYPE)
    if me is None:
        out.reason = NO_ME
        return out
    out.nickname = me["nickname"]
    snap = rankerpick.latest_raw_snapshot(rconn)
    if snap is None:
        out.reason = NO_SNAPSHOT
        return out
    out.taken_at, rows = rankerpick.top_rankers(rconn, limit=config.RANK_TIERS[-1], snapshot_id=snap[0])
    emblems = rankcollect.snapshot_emblem_map(rconn, snap[0])
    mine = next((r for r in rows if r["nickname"] == out.nickname), None)
    if mine is None or not mine.get("team_color"):
        out.reason = NO_COLOR
        return out
    out.color, out.value = mine["team_color"], mine.get("team_value")
    meta, _fresh = store.load_teamcolor_meta(fconn)
    out.color_id, out.reason = resolve_color(out.color, emblems.get(mine["profile_sn"], ""), meta)
    if out.reason:
        return out
    for limit in (config.SPECUP_LINEUP_SCAN, None):   # 앞의 판이 전부 표시한 판·빈 판일 때만 저장 전체를 읽는다
        out.lineup = pick(store.load_details(fconn, me["ouid"], config.DEFAULT_MATCH_TYPE, limit=limit), me["ouid"])
        if out.lineup:
            break
    if not out.lineup:
        out.reason = NO_LINEUP
        return out
    known = rankerpick.known_lineups(fconn, same_color_rows(rows, emblems, meta, out.color_id, out.nickname),
                                     config.SPECUP_OPPONENT_MAX, today)
    out.rankers = len(known)
    out.by_source.update(c.source for c in known)
    if out.rankers < config.RECOMMEND_MIN_RANKERS:
        out.reason = FEW_RANKERS
        return out
    out.cands = candidates(known)
    want = out.want
    out.prices = store.load_card_prices(fconn, want)
    out.extras = store.load_card_extras(fconn, want)
    out.salaries = {s: c["salary"] for s, c in store.load_card_info(fconn, want).items() if isinstance(c["salary"], int)}
    pairs = [(c.spid, s.po) for s in out.lineup for c in out.cands if c.line == s.line]
    out.records = store.load_ranker_stats(fconn, pairs, config.DEFAULT_MATCH_TYPE, (today or date.today()).isoformat())
    return out


# ── 자리별 후보 · 걸음 ────────────────────────────────────────────────────────

def keeps_color(extra: dict | None, color_id: int) -> bool | None:
    """그 카드가 내 팀컬러를 받나 — 시뮬레이터를 안 읽었으면 None(모름)."""
    got = teamcolor_calc.card_colors(extra)
    return None if got is None else color_id in got[0] or color_id in got[1]


def slot_options(slot: Slot, cands, inp: Inputs, grades, salary_on: bool) -> tuple[list[Option], dict]:
    """한 자리에 살 수 있는 (카드, 강화) 전부 → (값 오름차순 목록, {(카드, 강화 또는 None): 뺀 이유}).
    팀컬러·OVR·시세·급여를 모르는 것은 뺀다(빈 칸을 채워 넣지 않는다)."""
    out: list[Option] = []
    skipped: dict[tuple, str] = {}
    others = {s.spid % store.PLAYER_ID_MOD for s in inp.lineup if s is not slot}
    for c in cands:
        if c.line is not None and c.line != slot.line:
            continue
        if c.spid % store.PLAYER_ID_MOD in others:
            skipped[c.spid, None] = "same_player"
            continue
        extra = inp.extras.get(c.spid)
        keep = keeps_color(extra, inp.color_id)
        if not keep:
            skipped[c.spid, None] = "color_unknown" if keep is None else "color_out"
            continue
        salary = inp.salaries.get(c.spid)
        if salary_on and salary is None:
            skipped[c.spid, None] = "salary_unknown"
            continue
        for g in grades:
            ovr, why = cardstats.effective_ovr(slot.po, g, extra)
            if ovr is None:
                skipped[c.spid, g] = f"ovr_{why}"
                continue
            price, day = inp.prices.get((c.spid, g), (None, ""))
            if not price:
                skipped[c.spid, g] = "price"
                continue
            out.append(Option(c.spid, g, price, ovr, salary, c.users, day or ""))
    out.sort(key=lambda o: (o.price, -o.ovr, -o.users, o.spid))
    return out, skipped


def frontier(options: list[Option], base_ovr: int) -> list[Option]:
    """지금 카드(값 0 · base_ovr)를 원점으로 한 파레토 집합 — options 는 값 오름차순(`slot_options` 순)."""
    out: list[Option] = []
    best = base_ovr
    for o in options:
        if o.ovr > best:
            out.append(o)
            best = o.ovr
    return out


def per_eok(gain: int, price: int) -> float:
    return gain * EOK / price


def chain(front: list[Option], base_ovr: int, ratio: float = 1.0) -> list[tuple[Option, int, tuple[Option, ...]]]:
    """파레토 집합 → 실제로 살 걸음 [(카드, 오르는 OVR, 건너뛴 카드)]. 앞 걸음을 건너뛰고 바로 사는 효율이 앞 걸음의 ratio 배보다
    높으면 건너뛴다 — ratio 1 이면 바로 사는 편이 나을 때만, 1 보다 작으면 더 굵게 묶는다(묶음 굵기 설정). 앞 걸음 다음에 사는 효율은 바로 사는
    효율보다 늘 낮으므로 남은 걸음의 효율은 단조다. 처음에는 앞 걸음 다음에 사는 효율로 견줘, 5억 +1 뒤 8억 +2(바로 사면 8억 +2)처럼
    앞 카드 값을 버리는 걸음이 남았다(121 조각 ③)."""
    stack: list[tuple[Option, int, tuple[Option, ...]]] = []
    for o in front:
        skip: tuple[Option, ...] = ()
        while stack:
            prev, g, prev_skip = stack[-1]
            if per_eok(o.ovr - (prev.ovr - g), o.price) <= ratio * per_eok(g, prev.price):
                break
            stack.pop()
            skip = prev_skip + (prev,) + skip
        stack.append((o, o.ovr - (stack[-1][0].ovr if stack else base_ovr), skip))
    return stack


def plan(inp: Inputs, grades=None, ratio: float = 1.0, lines=None, manual=(), cap: int | None = None) -> Plan:
    """재료 → 순서표. lines: 걸음을 세울 자리 줄("GK"·"DF"·"MF"·"FW") 묶음(None = 전부) · manual: 직접 넣은 카드(spid) —
    5명 문턱만 면제하고 팀컬러·같은 선수·급여는 그대로 거친다."""
    out = Plan()
    if inp.reason or not inp.lineup:
        return out
    grades = config.SPECUP_GRADES if grades is None else grades
    cap = config.SPECUP_SALARY_CAP if cap is None else cap
    pinned = dict.fromkeys(manual)   # 직접 넣은 카드는 어느 줄에나 — 후보에 이미 있으면 줄 없는 것으로 바꾼다
    cands = [c for c in inp.cands if c.spid not in pinned] + [CandCard(s, None, 0) for s in pinned]
    sal = [inp.salaries.get(s.spid) for s in inp.lineup]
    out.salary_on = all(v is not None for v in sal)
    total = out.salary_now = sum(sal) if out.salary_on else None

    opts: list[list[Option]] = []
    base: list[tuple[int, int | None, int | None]] = []   # 자리마다 지금 (spid, 강화, 급여)
    ovr: list[int | None] = []
    why: dict[tuple, str] = {}
    for i, s in enumerate(inp.lineup):
        v, _why = cardstats.effective_ovr(s.po, s.grade, inp.extras.get(s.spid))
        ovr.append(v)
        base.append((s.spid, s.grade, sal[i]))
        use = v is not None and (lines is None or s.line in lines)
        got, skipped = slot_options(s, cands, inp, grades, out.salary_on) if use else ([], {})
        opts.append(got)
        why.update(skipped)
    usable_keys = {k for got in opts for o in got for k in ((o.spid, None), (o.spid, o.grade))}
    out.skipped.update(v for k, v in why.items() if k not in usable_keys)   # 다른 자리에 세울 수 있는 카드는 뺀 것이 아니다
    for (spid, _g), v in why.items():
        if (spid, None) not in usable_keys:
            out.why.setdefault(spid, v)
    start = list(ovr)

    owner: dict[int, int] = {}      # 산 선수 번호 → 자리
    spent = 0
    while True:
        best = None
        for i, full in enumerate(opts):
            if not full:
                continue
            room = None if total is None else max(cap, total) - total + (base[i][2] or 0)
            usable = [o for o in full if owner.get(o.spid % store.PLAYER_ID_MOD, i) == i
                      and (room is None or o.salary <= room)]
            steps = chain(frontier(usable, ovr[i]), ovr[i], ratio)
            if not steps:
                continue
            o, gain, skip = steps[0]
            key = (-per_eok(gain, o.price), o.price, i)
            if best is None or key < best[0]:
                best = (key, i, o, gain, skip)
        if best is None:
            break
        _key, i, o, gain, skip = best
        spent += o.price
        if total is not None:
            total += o.salary - base[i][2]
        out.steps.append(SpecStep(i, o.spid, o.grade, o.price, gain, per_eok(gain, o.price),
                                  tuple((x.spid, x.grade) for x in skip), base[i][0], base[i][1], spent, total, o.price_on))
        base[i] = (o.spid, o.grade, o.salary)
        ovr[i] = o.ovr
        owner[o.spid % store.PLAYER_ID_MOD] = i
    used = {st.slot for st in out.steps}
    out.slots = [SlotState(s, start[i], SLOT_NO_OVR if start[i] is None else SLOT_OK if i in used else SLOT_EMPTY)
                 for i, s in enumerate(inp.lineup)]
    return out


def budget_cut(steps: list[SpecStep], budget: int) -> int:
    """앞에서부터 예산 안에 드는 칸 수 — 누적 금액으로만 자른다(최적 조합이 아니다 · 6a)."""
    return sum(1 for s in steps if s.spent <= budget)


def value_band(value: int) -> int:
    """구단가치 → 구간 번호(0 ~ len(SPECUP_VALUE_EDGES)) — 칸 테두리 색(U8). 경계값은 위 구간."""
    return bisect_right(config.SPECUP_VALUE_EDGES, value)


def band_value(step: SpecStep, value: int) -> int:
    """그 칸의 추천 구단가치(U8) — 앞 칸을 다 샀을 때의 구단가치(지금 구단가치 + 앞 칸까지 쓴 금액)."""
    return value + step.spent_before


__all__ = ["CandCard", "FEW_RANKERS", "Inputs", "NO_COLOR", "NO_LINEUP", "NO_ME", "NO_SNAPSHOT", "Option", "Plan",
           "SLOT_EMPTY", "SLOT_NO_OVR", "SLOT_OK", "Slot", "SlotState", "SpecStep", "TC_ENHANCE", "TC_LIST", "TC_SPLIT",
           "band_value", "budget_cut", "candidates", "chain", "frontier", "keeps_color", "load", "per_eok", "pick_lineup", "plan",
           "resolve_color", "same_color_rows", "slot_options", "value_band"]
