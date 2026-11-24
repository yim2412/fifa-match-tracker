"""랭커 저장 전술(110 ④ · tactics.py · tactic_codes.py) — 이름표 · 칸 해석 · 판정 · 받기 · 관문 · 막힘이 다른 길에 보이는지.

pytest 없이 `python tests/test_tactics.py`. 가짜 스쿼드메이커·가짜 오픈API 라 네트워크가 없습니다.
픽스처 `squadmaker_tactics.json.gz` 는 실제 응답 세 칸(쓰는 키만 — 카드·자리·전술 값 · 닉네임과 번호 없음)과 그 경기 선발입니다.
받아 둔 70명과의 대조는 이 PC 전용(`python tools/measure_110.py --app`)입니다.
"""
from __future__ import annotations

import copy
import gzip
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import watchdog  # noqa: E402 — config 보다 먼저(FIFA_NO_CRED)
import config  # noqa: E402
import matchtime  # noqa: E402
import notice  # noqa: E402
import rankerpick as rp  # noqa: E402
import store  # noqa: E402
import tactic_codes as codes  # noqa: E402
import tactics as tt  # noqa: E402
import traitcollect as tc  # noqa: E402
from demodata import POSITIONS  # noqa: E402
from nexon_api import KEY_INVALID_CODE, NETWORK_CODE, QUOTA_CODE, SERVER_DOWN_CODE, NexonAPIError  # noqa: E402

NOW = datetime(2026, 11, 24, 12, 0, 0)
DAY = NOW.date().isoformat()
LINE = [0, 3, 4, 6, 7, 10, 12, 13, 15, 16, 25]
BASE = [(1000 + i, p) for i, p in enumerate(LINE)]
SN, NICK, OUID = 77, "가랭커", "ouid-a"


class cfg:
    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        self.keep = {k: getattr(config, k) for k in self.kw}
        for k, v in self.kw.items():
            setattr(config, k, v)

    def __exit__(self, *exc):
        for k, v in self.keep.items():
            setattr(config, k, v)
        return False


def team_st(**kw) -> dict:
    return dict({"defensivestyle": 4, "defwidth": 4, "defdepth": 9, "offensive_style_buildup": 2,
                 "offensive_style_chancecreation": 2, "offwidth": 6, "offplayerinbox": 0, "offcornerkick": 0,
                 "offfreekick": 0, "teammentality": 5}, **kw)


def cell(lineup=BASE, subs=range(5000, 5007), strat=None, part=(2, 2), **st) -> dict:
    """지어낸 칸 응답 — strat 은 두 번째 선발(RB)의 개인 전술."""
    pl = [{"spid": s, "role": codes.POSITION_ROLE[p], "state": 1, "participation": list(part)} for s, p in lineup]
    if strat is not None:
        pl[1]["strategy"] = strat
    pl += [{"spid": s, "role": "gk", "state": 0, "participation": []} for s in subs]
    return {"ResultCode": 1, "ResultData": {"Squadinfo": {"players": pl, "teamSt": team_st(**st)}}}


EMPTY = {"ResultCode": -1}


def detail(mid="m1", hours_ago=5.0, lineup=BASE, ouid=OUID) -> dict:
    at = matchtime.raw_bound(NOW - timedelta(hours=hours_ago))
    players = [{"spId": s, "spPosition": p, "spGrade": 8} for s, p in lineup] + [{"spId": 9, "spPosition": 28, "spGrade": 1}]
    return {"matchId": mid, "matchDate": at, "matchInfo": [{"ouid": ouid, "player": players if lineup else []},
                                                           {"ouid": "other", "player": []}]}


LU = tt.lineup_of([{"spId": s, "spPosition": p} for s, p in BASE])


class Web:
    """가짜 스쿼드메이커 — 칸 (종류, 팀, 전술) → 응답(없으면 빈칸). errors: 순번(1부터) → 예외. hook(순번)."""

    def __init__(self, cells=None):
        self.cells, self.calls, self.errors, self.hook = cells or {}, [], {}, None

    def __call__(self, _session, sn, team_type, part, seq):
        self.calls.append((sn, team_type, part, seq))
        if self.hook is not None:
            self.hook(len(self.calls))
        e = self.errors.get(len(self.calls))
        if e is not None:
            raise e
        return copy.deepcopy(self.cells.get((team_type, part, seq), EMPTY))


class API:
    def __init__(self, details, nick=NICK, ouid=OUID):
        self.details, self.nick, self.ouid, self.calls, self.errors = {d["matchId"]: d for d in details}, nick, ouid, [], {}

    def _hit(self, name, arg, attempts):
        self.calls.append((name, arg, attempts))
        e = self.errors.get(len(self.calls))
        if e is not None:
            raise e

    def get_ouid(self, nickname, attempts=3):
        self._hit("ouid", nickname, attempts)
        if nickname != self.nick:
            raise NexonAPIError("없음", code="OPENAPI00004", status=400)
        return self.ouid

    def get_match_ids(self, ouid, matchtype=50, offset=0, limit=20, attempts=3):
        self._hit("ids", (ouid, matchtype, offset, limit), attempts)
        return list(self.details)[:limit]

    def get_match_detail_nocache(self, mid, attempts=1):
        self._hit("detail", mid, attempts)
        return self.details[mid]

    def get_match_detail(self, mid, attempts=3):
        raise AssertionError("남의 경기를 디스크 캐시에 남기는 길로 받았다")


class Clock:
    def __init__(self):
        self.t, self.waits = 0.0, []

    def wait(self, s):
        self.waits.append(s)
        self.t += s

    def __call__(self):
        return self.t


def run(web=None, *, api=None, details=None, conn=None, target=None, **kw):
    """(결과, web, api, conn, clock) — details 를 주면 E2(오픈API 없이), 아니면 api 로 E1."""
    conn = conn if conn is not None else store.open_db(":memory:")
    web, clk = web if web is not None else Web(), Clock()
    if target is None:
        target = (tt.Target(SN, NICK, OUID, tuple(details)) if details is not None else tt.Target(SN, NICK))
    kw.setdefault("allowed", lambda: "")
    with cfg(API_KEY=kw.pop("key", "k")):
        res = tt.collect(None, conn, target, api, wait=clk.wait, clock=clk, now_fn=lambda: NOW, fetch=web, **kw)
    return res, web, api, conn, clk


# ── 이름표 ───────────────────────────────────────────────────────────────────

def test_position_role_matches_app_positions():
    assert len(codes.POSITION_ROLE) == 28 and set(codes.POSITION_ROLE) == set(range(28))
    assert {i: POSITIONS[i].lower() for i in range(28)} == codes.POSITION_ROLE
    assert set(codes.POSITION_ROLE.values()) == set(codes.PLAYER_NAMES)


def test_team_names_golden():
    # 응답은 0부터 · 화면은 1부터(번들 yV) — 폭·깊이·박스·코너킥·프리킥만
    want = {("defdepth", 9): "10", ("defwidth", 0): "1", ("offwidth", 6): "7", ("offplayerinbox", 4): "5",
            ("offcornerkick", 0): "1", ("offfreekick", 4): "5", ("defensivestyle", 4): "지속적인 압박",
            ("defensivestyle", 0): "후퇴", ("offensive_style_buildup", 2): "긴 패스", ("offensive_style_chancecreation", 3): "빠른 빌드업",
            ("teammentality", 0): "전원수비", ("teammentality", 5): "매우 공격적", ("teammentality", 6): "전원공격"}
    assert {k: codes.team_value(*k) for k in want} == want
    for key, v in (("defdepth", 10), ("offcornerkick", 5), ("defwidth", -1), ("defensivestyle", 5), ("teammentality", 7), ("없는키", 1)):
        assert codes.team_value(key, v) == f"코드 {v}" and codes.is_unknown(codes.team_value(key, v)), (key, v)
    assert codes.team_bar("defdepth", 9) == (10, 10) and codes.team_bar("offcornerkick", 0) == (1, 5)
    assert codes.team_bar("teammentality", 5) is None and codes.team_bar("defwidth", 10) is None
    assert set(codes.TEAM_KEYS) == set(codes.TEAM_NAMES) | set(codes.TEAM_BARS) and len(codes.TEAM_KEYS) == 10


def test_player_names_golden():
    want = {("rb", "AR", 2): "공격 시 후방 대기", ("lb", "AR", 1): "공격 가담", ("cdm", "AS", 3): "수비수 사이에 위치",
            ("cdm", "DB", 2): "대인 수비", ("rcm", "SC", 1): "페널티 박스 안으로 침투", ("cam", "PF", 2): "넓은 지역으로",
            ("rm", "PF", 1): "자유 역할", ("st", "AR", 3): "가짜 공격수", ("lw", "CC", 3): "자유 역할", ("cb", "IN", 1): "적극적 차단",
            ("lwb", "SR", 2): "오버랩", ("rf", "DP", 1): "항상 수비 지원", ("sw", "AS", 2): "공격수로 플레이",
            ("st", "AR", 0): "균형잡힌 공격"}
    assert {k: codes.player_value(*k) for k in want} == want
    assert codes.player_value("rb", "SR0", 2) == codes.player_value("rb", "sr", 2) == "오버랩"     # 코드 끝 숫자는 뗀다
    for role, code, lv in (("st", "AR", 5), ("st", "AR", 4), ("gk", "AR", 1), ("zz", "AR", 0), ("rb", "QQ", 1), ("rb", "AR", -1)):
        assert codes.player_value(role, code, lv) == f"코드 {code} {lv}", (role, code, lv)
    assert codes.player_value("rb", "AR2", 9) == "코드 AR 9"
    shape = sorted((r, "".join(f"{c}{len(n)}" for c, n in sorted(t.items()))) for r, t in codes.PLAYER_NAMES.items())
    assert shape[:4] == [("cam", "DS3IN3PF3SC3"), ("cb", "AS3IN3"), ("cdm", "AS4DB3DP2IN3"), ("cf", "AR4DP3IN3SR3")], shape[:4]
    assert dict(shape)["gk"] == "" and dict(shape)["rw"] == "CC4DS3IN3SC3SR4" and dict(shape)["lm"] == "CC3DS3IN3PF2SC3SR3"


# ── 칸 해석 ──────────────────────────────────────────────────────────────────

def _fixture() -> dict:
    with gzip.open(ROOT / "tests" / "fixtures" / "squadmaker_tactics.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def test_parse_fixture_and_match():
    fx = _fixture()
    cells = {tuple(map(int, k.split("-"))): tt.parse_cell(v) for k, v in fx["cells"].items()}
    assert sorted(c is None for c in cells.values()) == [False, False, True], "선발·교체 칸 둘과 빈칸 하나"
    lu = tt.lineup_of([{"spId": s, "spPosition": p} for s, p in fx["lineup"]])
    got = tt.match(lu, cells)
    assert len(got) == 1 and got[0].cells == ((1, 2, 3),), got
    c = cells[(1, 2, 3)]
    assert len(c.cards) == 18 and len(c.players) == 11
    by = {p.role: p for p in c.players}
    assert by["rcm"].strategy == (("AS", 2), ("SC", 1)) and by["rcm"].part == (3, 3)
    assert by["gk"].strategy == () and by["gk"].part == (2, 2)
    assert dict(c.team)["defdepth"] == 9 and [k for k, _v in c.team] == list(codes.TEAM_KEYS)
    assert not any(codes.is_unknown(codes.player_value(p.role, x, lv)) for p in c.players for x, lv in p.strategy)


def test_parse_cell_defaults():
    def second(**kw):
        r = cell(**kw)
        return tt.parse_cell(r).players[1]
    assert second(strat=[["AR", "2"], ["IN", "0"]]).strategy == (("AR", 2),), "0 은 기본값 = 키 없음"
    assert second(strat=[["SR", 2], ["AR1", "1"]]).strategy == (("AR", 1), ("SR", 2)), "숫자·코드 끝 숫자·순서"
    assert second(strat=[["AR", "1"], ["AR", "2"]]).strategy == (("AR", 1),), "같은 코드는 앞의 것"
    assert second(strat=[]).strategy == ()
    assert second(part=()).part == (2, 2), "빈 목록 [] 은 형식 이상이 아니다(실측 6,003번)"
    assert second(part=(1, 3)).part == (1, 3) and second(part=(0, 4)).part == (2, 2)
    r = cell()
    for p in r["ResultData"]["Squadinfo"]["players"]:
        p.pop("participation")
    assert tt.parse_cell(r).players[0].part == (2, 2)
    assert tt.parse_cell(EMPTY) is None
    r = cell()
    r["ResultData"]["Squadinfo"]["players"][0]["role"] = " GK "
    assert tt.parse_cell(r).players[0].role == "gk"


def test_parse_cell_format_errors():
    def broken(fn):
        r = cell()
        fn(r["ResultData"]["Squadinfo"])
        try:
            tt.parse_cell(r)
        except tc.TraitError as e:
            assert e.kind == "format"
            return True
        return False
    assert not broken(lambda s: None), "멀쩡한 칸이 형식 이상이면 아래가 빈 검사다"
    for key in codes.TEAM_KEYS:
        assert broken(lambda s, k=key: s["teamSt"].pop(k)), key
    assert broken(lambda s: s["teamSt"].update(defwidth=None)) and broken(lambda s: s.update(teamSt=[]))
    assert broken(lambda s: s.update(players=None)) and broken(lambda s: s["players"].append("x"))
    for k in ("spid", "state", "role"):
        assert broken(lambda s, k=k: s["players"][0].pop(k)), k
    assert broken(lambda s: s["players"][0].update(state=2)) and broken(lambda s: s["players"][0].update(state=True))
    for bad in ("AR1", {"AR": 1}, [["AR"]], [["AR", "x"]], [[1, "1"]], ["AR1"]):
        assert broken(lambda s, b=bad: s["players"][1].update(strategy=b)), bad
    for bad in ("22", [None, 2], ["x", "y"]):
        assert broken(lambda s, b=bad: s["players"][1].update(participation=b)), bad
    for bad in ([], None, {"ResultCode": 2}, {"ResultCode": 1}, {"ResultCode": 1, "ResultData": {"Squadinfo": None}}):
        try:
            tt.parse_cell(bad)
            raise AssertionError(bad)
        except tc.TraitError as e:
            assert e.kind == "format", bad


def test_parse_cell_survives_trait_format_change():
    r = cell()
    for p in r["ResultData"]["Squadinfo"]["players"]:
        p["traits"] = "바뀐 꼴"
    try:
        tc.parse_team(copy.deepcopy(r))
        raise AssertionError("특성 해석이 안 깨졌다 — 아래가 빈 검사다")
    except tc.TraitError:
        pass
    assert len(tt.parse_cell(r).players) == 11
    r["ResultData"]["Squadinfo"]["players"][-1]["strategy"] = "교체 선수는 안 본다"
    assert len(tt.parse_cell(r).players) == 11


# ── 판정 ─────────────────────────────────────────────────────────────────────

def C(**kw):
    return tt.parse_cell(cell(**kw))


def test_lineup_of():
    ps = [{"spId": s, "spPosition": p} for s, p in BASE]
    assert tt.lineup_of(ps) == {(1000, "gk"), (1001, "rb"), (1002, "rcb"), (1003, "lcb"), (1004, "lb"), (1005, "cdm"), (1006, "rm"),
                                (1007, "rcm"), (1008, "lcm"), (1009, "lm"), (1010, "st")}
    assert tt.lineup_of(ps[:10]) is None and tt.lineup_of([]) is None
    assert tt.lineup_of(ps[:10] + [{"spId": 1, "spPosition": 77}]) is None, "모르는 자리"


def test_match_needs_all_eleven_pairs():
    assert [t.cells for t in tt.match(LU, {(1, 0, 0): C()})] == [((1, 0, 0),)]
    moved = [BASE[0], (BASE[1][0], BASE[2][1]), (BASE[2][0], BASE[1][1])] + BASE[3:]
    assert tt.match(LU, {(1, 0, 0): C(lineup=moved)}) == [], "카드는 같고 자리만 다르면 다른 전술"
    other = BASE[:10] + [(4242, BASE[10][1])]
    assert tt.match(LU, {(1, 0, 0): C(lineup=other)}) == [], "한 장만 달라도 아니다"
    assert tt.match(LU, {(1, 0, 0): C(lineup=other, subs=[BASE[10][0]])}) == [], "교체에 있는 건 선발이 아니다"
    assert tt.match(LU, {(1, 0, 0): None}) == [] and tt.match(frozenset(), {(1, 0, 0): C()}) == []


def test_match_merges_same_content_and_keeps_cell_order():
    cells = {(0, 1, 3): C(), (0, 0, 9): C(defdepth=1), (1, 2, 2): C(), (1, 2, 0): C(defdepth=1), (1, 0, 5): C(part=(3, 1))}
    got = tt.match(LU, cells)
    assert [t.cells for t in got] == [((1, 0, 5),), ((1, 2, 0), (0, 0, 9)), ((1, 2, 2), (0, 1, 3))], [t.cells for t in got]
    assert dict(got[1].team)["defdepth"] == 1 and got[0].players[0].part == (3, 1)

    def n(a, b):
        return len(tt.match(LU, {(1, 0, 0): C(**a), (1, 0, 1): C(**b)}))
    assert n({}, {}) == 1 and n({}, dict(subs=[1, 2, 3])) == 1, "교체 명단은 내용이 아니다"
    assert n({}, dict(offwidth=1)) == 2 and n({}, dict(part=(3, 1))) == 2 and n({}, dict(strat=[["AR", "1"]])) == 2
    assert n({}, dict(strat=[["AR", "0"]])) == 1 and n(dict(strat=[["AR", "1"], ["SR", "2"]]), dict(strat=[["SR", "2"], ["AR", "1"]])) == 1


def test_candidate_teams_use_eighteen_cards():
    bench = C(lineup=[(7, 0)] + BASE[1:], subs=[BASE[0][0]])
    short = C(lineup=[(7, 0)] + BASE[1:])
    first = {(0, 2): C(), (1, 1): bench, (1, 0): short, (1, 2): None}
    assert tt.candidate_teams(first, LU) == [(1, 1), (0, 2)], "선발이 달라도 18명 안이면 팀 · 순서는 대표 A → 클럽 C"
    assert tt.candidate_teams({(1, 0): C()}, frozenset()) == []
    assert tt.candidate_teams({(1, 0): C(subs=())}, LU) == [(1, 0)], "교체 없이 선발 11장뿐인 칸"


def test_cell_label():
    assert tt.cell_label((1, 0, 2)) == "대표 A팀 전술 3" and tt.cell_label((0, 2, 9)) == "클럽 C팀 전술 10"


# ── 받기 ─────────────────────────────────────────────────────────────────────

def test_collect_six_requests_when_no_team():
    res, web, _a, conn, _c = run(Web({(1, 0, 0): cell(lineup=BASE[:10] + [(4242, 25)])}), details=[detail()])
    assert res.state == "none" and res.requests == res.total == 6 and len(web.calls) == 6, res
    assert [c[1:] for c in web.calls] == [(*t, 0) for t in config.TRAIT_TEAM_ORDER] and {c[0] for c in web.calls} == {SN}
    assert store.budget_used(conn, DAY, store.BUDGET_TACTIC_WEB) == 6 and store.budget_used(conn, DAY, store.BUDGET_TRAIT_WEB) == 0


def test_collect_nine_more_per_team_and_both_teams():
    web = Web({(1, 1, 0): cell(), (1, 1, 4): cell(defdepth=1), (0, 0, 0): cell(lineup=[(7, 0)] + BASE[1:], subs=[BASE[0][0]]),
               (0, 0, 7): cell()})
    res, web, _a, conn, _c = run(web, details=[detail()])
    assert res.state == "ok" and res.requests == res.total == 24 == len(web.calls) == len(set(web.calls)), res
    assert [t.cells for t in res.tactics] == [((1, 1, 0), (0, 0, 7)), ((1, 1, 4),)], "맞는 칸이 팀 둘에 걸친다"
    assert res.formation and res.match_id == "m1" and res.match_at == NOW - timedelta(hours=5)


def test_collect_progress_and_gap():
    seen = []
    res, _w, _a, _cn, clk = run(Web({(1, 1, 0): cell()}), details=[detail()], progress=lambda n, t: seen.append((n, t)))
    assert seen == [(i, None) for i in range(1, 7)] + [(i, 15) for i in range(7, 16)], seen
    assert len(clk.waits) == 14 and all(abs(w - config.RANKER_PICK_GAP_S) < 1e-9 for w in clk.waits), clk.waits


def _left(n: int):
    """오늘 남은 몫이 n 인 DB."""
    conn = store.open_db(":memory:")
    conn.execute("INSERT INTO api_budget (day, kind, attempts) VALUES (?, ?, ?)",
                 (DAY, store.BUDGET_TACTIC_WEB, config.TACTIC_WEB_DAILY_REQ - n))
    conn.commit()
    return conn


def test_daily_cap_edges():
    two = {(1, 1, 0): cell(), (0, 0, 0): cell()}
    for left, calls, state in ((5, 0, "limit"), (6, 6, "limit"), (23, 6, "limit"), (24, 24, "ok")):
        res, web, *_ = run(Web(two), details=[detail()], conn=_left(left))
        assert (res.state, len(web.calls)) == (state, calls), (left, res.state, len(web.calls))
        assert state == "ok" or res.tactics == []
    res, web, *_ = run(Web(), details=[detail()], conn=_left(6))
    assert res.state == "none" and len(web.calls) == 6, "맞는 팀이 없으면 6으로 끝난다"
    conn = _left(20)   # 받는 도중 다른 창이 몫을 썼다
    web = Web({(1, 1, 0): cell()})
    web.hook = lambda n: n == 10 and [store.budget_take(conn, DAY, store.BUDGET_TACTIC_WEB, None) for _ in range(10)]
    res, web, *_ = run(web, details=[detail()], conn=conn)
    assert res.state == "limit" and len(web.calls) == 10 and res.tactics == [], res


def test_block_and_format_stop_the_day():
    for kind, mark, state in (("rate", store.BUDGET_TACTIC_WEB_BLOCK, "rate"), ("format", store.BUDGET_TACTIC_WEB_FORMAT, "format")):
        web = Web({(1, 1, 0): cell()})
        web.errors[3] = tc.TraitError("x", kind)
        res, web, _a, conn, _c = run(web, details=[detail()])
        assert res.state == state and len(web.calls) == 3 and store.budget_hit_429(conn, DAY, (mark,)), res
        res, web, *_ = run(Web({(1, 1, 0): cell()}), details=[detail()], conn=conn)
        assert res.state == state and web.calls == [], "그날 다시 요청했다"
    web = Web({(1, 1, 0): {"ResultCode": 9}})   # 응답 해석에서 나온 형식 이상도 같은 표시
    res, _w, _a, conn, _c = run(web, details=[detail()])
    assert res.state == "format" and store.budget_hit_429(conn, DAY, (store.BUDGET_TACTIC_WEB_FORMAT,))
    web = Web({(1, 1, 0): cell()})
    web.errors[1] = tc.TraitError("x", "down")
    res, _w, _a, conn, _c = run(web, details=[detail()])
    assert res.state == "down" and conn.execute("SELECT COUNT(*) FROM api_budget WHERE hit_429 = 1").fetchone()[0] == 0
    assert run(Web({(1, 1, 0): cell()}), details=[detail()], conn=conn)[0].state == "ok", "연결 실패로 그날을 막았다"


def test_other_web_blocks_stop_tactics():
    for k in store.card_block_kinds():
        conn = store.open_db(":memory:")
        store.budget_mark_429(conn, DAY, k)
        api = API([detail()])
        res, web, *_ = run(Web({(1, 1, 0): cell()}), api=api, conn=conn)
        assert res.state == "rate" and web.calls == [] and api.calls == [], k
    conn = store.open_db(":memory:")
    store.budget_mark_429(conn, DAY, store.BUDGET_TRAIT_WEB_FORMAT)   # 특성 형식 이상은 전술을 안 막는다
    assert run(Web({(1, 1, 0): cell()}), details=[detail()], conn=conn)[0].state == "ok"


def test_gate_checked_before_every_request():
    for why in ("notice", "off_web", "off_collect"):
        api = API([detail()])
        res, web, *_ = run(Web(), api=api, allowed=lambda w=why: w)
        assert res.state == why and web.calls == [] and api.calls == [], why
    web = Web({(1, 1, 0): cell()})
    res, web, _a, conn, _c = run(web, details=[detail()], allowed=lambda: "off_collect" if len(web.calls) >= 8 else "")
    assert res.state == "off_collect" and len(web.calls) == 8 and store.budget_used(conn, DAY, store.BUDGET_TACTIC_WEB) == 8
    api = API([detail()])   # 오픈API 단계에서도
    res, web, *_ = run(Web({(1, 1, 0): cell()}), api=api, allowed=lambda: "off_web" if api.calls else "")
    assert res.state == "off_web" and len(api.calls) == 1 and web.calls == []


def test_cancel_returns_without_request():
    stop = [False]
    web = Web({(1, 1, 0): cell()})
    web.hook = lambda n: n == 4 and stop.__setitem__(0, True)
    res, web, *_ = run(web, details=[detail()], cancel=lambda: stop[0])
    assert res.state == "cancelled" and len(web.calls) == 4 and res.tactics == []
    stop, web, clk = [False], Web({(1, 1, 0): cell()}), Clock()   # 기다리는 동안 닫음

    def wait(s):
        clk.wait(s)
        stop[0] = len(web.calls) >= 2
    conn = store.open_db(":memory:")
    with cfg(API_KEY="k"):
        res = tt.collect(None, conn, tt.Target(SN, NICK, OUID, (detail(),)), None, allowed=lambda: "", cancel=lambda: stop[0],
                         wait=wait, clock=clk, now_fn=lambda: NOW, fetch=web)
    assert res.state == "cancelled" and len(web.calls) == 2 == store.budget_used(conn, DAY, store.BUDGET_TACTIC_WEB)
    api = API([detail()])
    res, web, *_ = run(Web(), api=api, cancel=lambda: bool(api.calls))
    assert res.state == "cancelled" and len(api.calls) == 1 and web.calls == []


def test_cached_result_before_cap():
    keep = tt.TacticResult("ok", requests=15)
    asked = []
    res, web, *_ = run(Web({(1, 1, 0): cell()}), details=[detail()], conn=_left(0), cached=lambda mid: (asked.append(mid), keep)[1])
    assert res is keep and asked == ["m1"] and web.calls == [], "몫이 0 이어도 받아 둔 사람은 보인다"
    assert run(Web(), details=[detail()], conn=_left(0), cached=lambda mid: None)[0].state == "limit"


# ── 마지막 경기 ──────────────────────────────────────────────────────────────

def test_lineup_skips_forfeit_and_stops_at_old():
    ok = Web({(1, 1, 0): cell()})
    res, *_ = run(ok, details=[detail("m0", 1, lineup=[]), detail("m1", 5)])
    assert res.state == "ok" and res.match_id == "m1", "몰수 빈 선발을 건너뛴다"
    blank = [detail(f"b{i}", i + 1, lineup=[]) for i in range(config.TACTIC_RECENT)]
    res, web, *_ = run(Web(), details=blank + [detail("m9", 9)])
    assert res.state == "nomatch" and web.calls == [], "TACTIC_RECENT 판까지만 본다"
    assert run(Web(), details=[])[0].state == "nomatch"
    edge = config.RANKER_PICK_MAX_AGE_DAYS * 24
    assert [run(Web(), details=[detail("m1", edge - x)])[0].state for x in (1, 0)] == ["none", "none"], "꼭 14일은 아직 본다"
    res, web, *_ = run(Web(), details=[detail("m1", edge + 1)])
    assert res.state == "old" and web.calls == []
    assert run(Web(), details=[detail("m0", 1, lineup=BASE[:10]), detail("m1", edge + 1)])[0].state == "old"
    broken = dict(detail(), matchDate="깨짐")
    assert run(Web({(1, 1, 0): cell()}), details=[broken, detail("m2", 3)])[0].match_id == "m2"
    res, *_ = run(ok, details=[detail(ouid="딴사람")])
    assert res.state == "nomatch", "그 사람 쪽 선발만 본다"


def test_e1_requests_and_no_store():
    api = API([detail("m0", 1, lineup=[]), detail("m1", 5), detail("m2", 7)])
    res, web, _a, conn, _c = run(Web({(1, 1, 0): cell()}), api=api)
    assert res.state == "ok" and res.match_id == "m1" and res.requests == 15, "오픈API 는 진행 수에 안 섞인다"
    assert [(n, a) for n, _x, a in api.calls] == [("ouid", 1), ("ids", 1), ("detail", 1), ("detail", 1)], api.calls
    assert api.calls[1][1] == (OUID, config.DEFAULT_MATCH_TYPE, 0, config.TACTIC_RECENT)
    assert store.budget_used(conn, DAY, store.BUDGET_TACTIC_API) == 4 and store.budget_used(conn, DAY, store.BUDGET_RANKER_PICK) == 0
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0, "남의 경기를 남겼다"
    assert run(Web(), details=[detail()], conn=conn)[0].state == "none"
    assert store.budget_used(conn, DAY, store.BUDGET_TACTIC_API) == 4, "경기를 넘겨받으면 오픈API 0"


def test_e1_uses_saved_ouid_and_saved_match():
    conn = store.open_db(":memory:")
    store.save_ranker_squad(conn, SN, nickname=NICK, ouid=OUID, rank=5, match_id="m1", match_day=DAY, fetched_at=DAY, fail=None,
                            source=store.PICK)
    store.save_matches(conn, [detail("m1", 5)])
    api = API([detail("m1", 5)], nick="바뀐이름")   # 스냅숏 뒤에 닉네임이 바뀌었다
    res, *_ = run(Web({(1, 1, 0): cell()}), api=api, conn=conn)
    assert res.state == "ok" and [c[0] for c in api.calls] == ["ids"], api.calls
    api = API([detail("m1", 5)])
    store.save_ranker_squad(conn, SN, nickname="옛이름", ouid="딴사람", rank=5, match_id="m1", match_day=DAY, fetched_at=DAY, fail=None,
                            source=store.PICK)
    run(Web(), api=api, conn=conn)
    assert [c[0] for c in api.calls] == ["ouid", "ids"] and api.calls[1][1][0] == OUID, "스냅숏과 닉네임이 다른 줄은 안 쓴다"


def test_e1_states():
    assert run(Web(), api=API([]))[0].state == "nomatch"
    res, web, api, *_ = run(Web(), api=API([detail()]), key="")
    assert res.state == "nokey" and api.calls == []
    assert run(Web({(1, 1, 0): cell()}), details=[detail()], key="")[0].state == "ok", "경기를 넘겨받으면 키를 안 본다"

    def failing(at, exc):
        api = API([detail()])
        api.errors[at] = exc
        return run(Web({(1, 1, 0): cell()}), api=api)
    for e in (NexonAPIError("x", code="OPENAPI00004", status=400), NexonAPIError("x", code="OPENAPI00009", status=400),
              NexonAPIError("계정을 찾지 못했습니다.")):
        assert failing(1, e)[0].state == "nouser", e.code
    assert failing(1, NexonAPIError("x", code="", status=502))[0].state == "server", "JSON 이 아닌 502 는 없는 사람이 아니다"
    assert failing(2, NexonAPIError("x", code="OPENAPI00004", status=400))[0].state == "server", "목록에서는 nouser 가 아니다"
    assert failing(1, NexonAPIError("x", code=KEY_INVALID_CODE, status=403))[0].state == "key"
    assert failing(2, NexonAPIError("x", code=NETWORK_CODE))[0].state == "net"
    assert failing(3, NexonAPIError("x", code=SERVER_DOWN_CODE, status=503))[0].state == "server"
    with cfg(TACTIC_API_DAILY_REQ=2):
        res, web, _a, conn, _c = run(Web(), api=API([detail()]))
        assert res.state == "limit" and web.calls == [] and store.budget_used(conn, DAY, store.BUDGET_TACTIC_API) == 2


def test_e1_429():
    quota = NexonAPIError("x", code=QUOTA_CODE, status=429)
    api = API([detail()])
    api.errors[2] = quota
    res, _w, _a, conn, clk = run(Web({(1, 1, 0): cell()}), api=api)
    assert res.state == "ok" and [c[0] for c in api.calls] == ["ouid", "ids", "ids", "detail"]
    assert clk.waits.count(1.0) == config.TACTIC_429_WAIT_S == 3, clk.waits
    assert not store.budget_hit_429(conn, DAY, (store.BUDGET_OPENAPI, store.BUDGET_TACTIC_API)), "한 번은 표시하지 않는다"
    api = API([detail()])
    api.errors[2] = api.errors[3] = quota
    res, web, _a, conn, _c = run(Web({(1, 1, 0): cell()}), api=api)
    assert res.state == "quota" and web.calls == []
    for k in (store.BUDGET_OPENAPI, store.BUDGET_TACTIC_API):
        assert store.budget_hit_429(conn, DAY, (k,)), k
    for mark in (store.BUDGET_OPENAPI, store.BUDGET_TACTIC_API):
        conn = store.open_db(":memory:")
        store.budget_mark_429(conn, DAY, mark)
        api = API([detail()])
        assert run(Web({(1, 1, 0): cell()}), api=api, conn=conn)[0].state == "quota" and api.calls == [], mark
        assert run(Web({(1, 1, 0): cell()}), details=[detail()], conn=conn)[0].state == "ok", "오픈API 를 안 쓰는 길은 안 본다"


def test_ranker_pick_429_wait_unchanged():
    conn, clk, n = store.open_db(":memory:"), Clock(), [0]

    def fn():
        n[0] += 1
        raise NexonAPIError("x", code=QUOTA_CODE, status=429)
    try:
        rp._Session(conn, DAY, lambda: False, clk.wait, clk, rp.PickResult()).call(fn)
        raise AssertionError("안 멈췄다")
    except rp._Quota:
        pass
    assert n[0] == 2 and clk.waits.count(1.0) == config.RANKER_PICK_429_WAIT_S == 60


# ── 상태 ─────────────────────────────────────────────────────────────────────

def test_state_of_every_exception():
    want = [(tt._End("outside"), "outside"), (rp._Stop(), "cancelled"), (rp._Limit(), "limit"), (rp._Quota(), "quota"),
            (tc.TraitError("x", "off"), "off_web"), (tc.TraitError("x", "down"), "down"), (tc.TraitError("x", "format"), "format"),
            (tc.TraitError("x", "rate"), "rate"), (tc.TraitError("x", "새갈래"), "error"),
            (NexonAPIError("x", code=KEY_INVALID_CODE, status=400), "key"), (NexonAPIError("x", code=QUOTA_CODE, status=429), "quota"),
            (NexonAPIError("x", code="", status=429), "quota"), (NexonAPIError("x", code=NETWORK_CODE), "net"),
            (NexonAPIError("x", code="", status=502), "server"), (NexonAPIError("x", code="OPENAPI00004", status=400), "server"),
            (NexonAPIError("x", code=SERVER_DOWN_CODE, status=503), "server"), (sqlite3.OperationalError("locked"), "dbbusy"),
            (ValueError("json"), "error")]
    assert [tt.state_of(e) for e, _s in want] == [s for _e, s in want], [tt.state_of(e) for e, _s in want]
    assert set(tc.MESSAGES) == set(tt._TRAIT_STATE), "특성 오류 갈래가 늘었다"


def test_texts_cover_states_both_ways():
    assert set(notice.TACTIC_TEXTS) == set(tt.STATES) - {"ok"} and len(set(tt.STATES)) == len(tt.STATES)
    assert set(tt.FINAL_STATES) <= set(tt.STATES) and all(t.strip() for t in notice.TACTIC_TEXTS.values())


def test_unknown_error_is_reported_and_db_error_is_not():
    seen = []
    web = Web()
    web.errors[2] = ValueError("json")
    res, *_ = run(web, details=[detail()], on_error=seen.append)
    assert res.state == "error" and [type(e) for e in seen] == [ValueError]
    web = Web()
    web.errors[2] = sqlite3.OperationalError("locked")
    assert run(web, details=[detail()], on_error=seen.append)[0].state == "dbbusy" and len(seen) == 1


def test_mark_failure_keeps_reason():
    keep = store.budget_mark_429

    def locked(*_a):
        raise sqlite3.OperationalError("locked")
    web = Web()
    web.errors[1] = tc.TraitError("x", "rate")
    try:
        store.budget_mark_429 = locked
        assert run(web, details=[detail()])[0].state == "rate"
    finally:
        store.budget_mark_429 = keep


# ── 관문 ─────────────────────────────────────────────────────────────────────

def test_gate_order_and_notice18():
    v = config.TACTIC_NOTICE_VERSION
    on = dict(WEB_DATA=True, RANK_COLLECT=True, NOTICE_ACCEPTED=v)
    for kw, want in ((on, ""), (dict(on, NOTICE_ACCEPTED=v - 1), "notice"), (dict(on, WEB_DATA=False), "off_web"),
                     (dict(on, RANK_COLLECT=False), "off_collect"), (dict(on, WEB_DATA=False, RANK_COLLECT=False), "off_web"),
                     (dict(on, RANK_COLLECT=False, NOTICE_ACCEPTED=v - 1), "off_collect"),
                     (dict(on, WEB_DATA=False, NOTICE_ACCEPTED=config.NOTICE_BASE_VERSION - 1), "notice")):
        with cfg(**kw):
            assert config.tactic_block() == want and config.tactic_allowed() is (want == ""), (kw, want)
    assert v == 18 == config.NOTICE_VERSION and config.NOTICE_COLLECT_ONLY_FROM == 16, "18 은 수집을 켠 사람 몫이다(U7)"
    with cfg(WEB_DATA=True, RANK_COLLECT=True, NOTICE_ACCEPTED=17):
        res, web, *_ = run(Web({(1, 1, 0): cell()}), details=[detail()], allowed=None)
        assert res.state == "notice" and web.calls == [], "안내 18 전에 요청이 나갔다"
    with cfg(WEB_DATA=True, RANK_COLLECT=True, NOTICE_ACCEPTED=v):
        assert run(Web({(1, 1, 0): cell()}), details=[detail()], allowed=None)[0].state == "ok", "관문이 열려도 안 받는다(빈 검사)"


# ── 막힘이 특성에 보이나 ─────────────────────────────────────────────────────

def test_tactic_block_rests_traits_without_turning_them_off():
    conn = store.open_db(":memory:")
    for d in ("2026-11-21", "2026-11-22", "2026-11-23"):   # [다시 켜기] 전 사흘 장부
        store.budget_take(conn, d, store.BUDGET_TRAIT_WEB, None)
        store.budget_mark_429(conn, d, store.BUDGET_TRAIT_WEB_BLOCK)
    assert store.trait_block_streak(conn) == config.TRAIT_BLOCK_DAYS, "장부가 사흘이 아니면 아래가 빈 검사"
    store.budget_mark_429(conn, DAY, store.BUDGET_TACTIC_WEB_BLOCK)
    assert store.trait_block_streak(conn) == config.TRAIT_BLOCK_DAYS, "전술 막힘이 특성 장부에 섞였다"
    out = tc.run(None, None, conn, [], on_at=None, now_fn=lambda: NOW)
    assert out.last_web.stop == "rate" and out.block_streak == 0, out
    assert tc.collect_mine(None, conn, "me", now_fn=lambda: NOW) == "rate"
    store.budget_mark_429(conn, DAY, store.BUDGET_TRAIT_WEB_BLOCK)
    out = tc.run(None, None, conn, [], on_at=None, now_fn=lambda: NOW)
    assert out.block_streak == config.TRAIT_BLOCK_DAYS + 1, "특성 자신이 막힌 날은 센다"
    conn = store.open_db(":memory:")
    store.budget_mark_429(conn, DAY, store.BUDGET_TACTIC_WEB_FORMAT)
    assert tc.run(API([]), None, conn, [], on_at=None, now_fn=lambda: NOW).last_web.stop == "", "전술 형식 이상이 특성을 멈췄다"
    assert store.BUDGET_TACTIC_WEB_BLOCK in store.card_block_kinds()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
