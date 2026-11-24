"""110 ④ 관문 측정 도구 — 지어낸 칸·가짜 받기로(네트워크 없음)."""
from __future__ import annotations

import copy
import struct
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import watchdog  # noqa: E402
import measure_110 as m  # noqa: E402
import traitcollect  # noqa: E402
from demodata import POSITIONS  # noqa: E402

LINE = [0, 3, 4, 6, 7, 10, 12, 13, 15, 16, 25]
BASE = [(1000 + i, p) for i, p in enumerate(LINE)]


def resp(lineup=BASE, team_st=5, strat=None, subs=(), part=(2, 2)):
    pl = [{"spid": s, "role": POSITIONS[p].lower(), "state": 1, "buildUp": 8, "traits": [-2], "participation": list(part)}
          for s, p in lineup]
    if strat:
        pl[1]["strategy"] = strat
    pl += [{"spid": s, "role": "gk", "state": 0, "buildUp": 8, "traits": [-2]} for s in subs]
    return {"ResultCode": 1, "ResultData": {"strCharacterName": "x", "Squadinfo": {"players": pl, "teamSt": {"defdepth": team_st}}}}


EMPTY = {"ResultCode": -1}


def sq(**kw):
    return m.squad(resp(**kw))


def test_positions_table():
    assert POSITIONS[0] == "GK" and POSITIONS[25] == "ST" and len(POSITIONS) >= 28, POSITIONS


def test_unpack_lineup():
    narrow = b"".join(struct.pack("<IBB", s, p, 8) for s, p in BASE)
    wide = b"\x01" + b"".join(struct.pack("<qhh", s, p, 300) for s, p in BASE)
    odd = b"".join(struct.pack("<IBB", s, p, 8) for s, p in [(0x01_00_00_01, 3)] + BASE)   # 첫 바이트가 0x01 인 좁은 꼴
    assert m.unpack_lineup(narrow) == BASE and m.unpack_lineup(wide) == BASE
    assert odd[:1] == b"\x01" and m.unpack_lineup(odd)[0] == (0x01_00_00_01, 3)


def test_match_needs_position():
    lu = m.distinct_lineups([BASE])
    assert m.verdict(lu, {"1-0-0": sq()})["cells"] == ["1-0-0"]
    moved = [(BASE[0][0], BASE[0][1]), (BASE[1][0], BASE[2][1]), (BASE[2][0], BASE[1][1])] + BASE[3:]
    assert m.verdict(lu, {"1-0-0": sq(lineup=moved)})["cells"] == []          # 카드는 같고 자리만 다름
    bench = resp()
    bench["ResultData"]["Squadinfo"]["players"][3]["state"] = 0
    assert m.verdict(lu, {"1-0-0": m.squad(bench)})["cells"] == []            # 교체로 빠진 카드


def test_distinct_content():
    lu = m.distinct_lineups([BASE])
    same = {"1-0-0": sq(), "1-0-1": sq(), "1-0-2": None}
    assert m.verdict(lu, same)["distinct"] == 1 and len(m.verdict(lu, same)["cells"]) == 2
    assert m.verdict(lu, {"a": sq(), "b": sq(team_st=6)})["distinct"] == 2    # 팀 전술
    assert m.verdict(lu, {"a": sq(), "b": sq(strat=[["SR", "1"]])})["distinct"] == 2   # 개인 전술
    assert m.verdict(lu, {"a": sq(), "b": sq(part=(3, 1))})["distinct"] == 2  # 참여도
    x, y = [["SR", "1"], ["AR", "2"]], [["AR", "2"], ["SR", "1"]]
    assert m.verdict(lu, {"a": sq(strat=x), "b": sq(strat=y)})["distinct"] == 1   # 순서만 다름
    assert m.verdict(lu, {"a": sq(subs=[7]), "b": sq(subs=[8, 9])})["distinct"] == 1     # 교체 명단만 다름
    assert m.verdict(lu, {"a": sq(), "b": sq(strat=[["SR", "0"]])})["distinct"] == 1     # 0 단계 = 기본값 = 키 없음
    gone = resp()
    for p in gone["ResultData"]["Squadinfo"]["players"]:
        p.pop("participation")
    assert m.verdict(lu, {"a": sq(), "b": m.squad(gone)})["distinct"] == 1    # 참여도 키가 없으면 [2, 2]


def test_back_to_older_lineup():
    old = [(s + 500, p) for s, p in BASE]
    lu = m.distinct_lineups([BASE, BASE, old])
    assert len(lu) == 2
    v = m.verdict(lu, {"1-0-0": sq(lineup=old)})
    assert v["back"] == 1 and m.verdict(lu, {"1-0-0": sq(lineup=old)}, last_only=True)["cells"] == []
    many = [[(s + 100 * i, p) for s, p in BASE] for i in range(8)]
    assert len(m.distinct_lineups(many)) == m.DISTINCT_LINEUPS


def test_format_error_stops():
    broken = resp()
    del broken["ResultData"]["Squadinfo"]["players"][0]["spid"]
    try:
        m.squad(broken)
        raise AssertionError("깨진 칸을 받아들였다")
    except traitcollect.TraitError as e:
        assert e.kind == "format"
    assert m.squad(EMPTY) is None


def test_bundle_table_and_readable():
    js = "x;var mR=pR({gk:[],rb:[[{tit:`AR`,desc:`가`},{tit:`AR`,desc:`나`}],[{tit:`SR`,desc:`다`},{tit:`SR`,desc:`라`},{tit:`SR`,desc:`마`}]],st:[[{tit:`AR`,desc:`바`}]]}),q=[{tit:`ZZ`}]"
    table = m.bundle_table(js)
    assert table == {"gk": {}, "rb": {"AR": 2, "SR": 3}, "st": {"AR": 1}}, table
    ok = ["st|defwidth|10", "st|teammentality|6", "pl|rb|SR|2", "pl|rb|SR0|1", "pa|(3, 1)"]
    bad = ["st|defwidth|11", "st|newkey|1", "pl|rb|SR|3", "pl|rb|QQ|1", "pl|gk|AR|1", "pl|zz|AR|0", "pa|(4, 1)", "st|defwidth|x"]
    assert all(m.readable(k, table) for k in ok), [k for k in ok if not m.readable(k, table)]
    assert not any(m.readable(k, table) for k in bad), [k for k in bad if m.readable(k, table)]


class FakeFetcher:
    def __init__(self, cells):
        self.cells, self.log, self.asked = cells, [], []

    def cell(self, sn, team, seq):
        self.log.append(0.1)
        self.asked.append((team, seq))
        return copy.deepcopy(self.cells.get((team, seq), EMPTY))


def test_fetch_plan():
    t = {"no": 0, "group": "a", "sn": 1, "lineups": [BASE], "age_h": 1.0}
    f = FakeFetcher({((1, 1), 0): resp(), ((0, 0), 0): resp(lineup=[(9, 0)])})
    d = m.fetch_person(f, t)
    assert d["teams"] == ["1-1"] and d["main"]["req"] == 15 and not d["extra"], d["main"]
    # 선발이 다르게 저장돼도 18명 안에 11장이 다 있으면 그 팀
    f = FakeFetcher({((1, 1), 0): resp(lineup=[(7, 0)], subs=[s for s, _ in BASE])})
    assert m.fetch_person(f, t)["teams"] == ["1-1"]
    # 팀이 없으면 60칸 — 그때 찾은 것은 판정이 아니라 거르기가 놓친 것으로
    f = FakeFetcher({((0, 2), 4): resp()})
    d = m.fetch_person(f, t)
    assert d["main"]["req"] == 6 and len(d["extra"]) == 54 and len(f.asked) == 60
    r = m.person_result(t, d)
    assert not r["seen"] and r["filter_missed"] and r["req"] == 6, r


def test_fetch_uses_older_lineup_for_team():
    old = [(s + 500, p) for s, p in BASE]
    t = {"no": 0, "group": "a", "sn": 1, "lineups": [BASE, old], "age_h": 1.0}
    d = m.fetch_person(FakeFetcher({((1, 2), 0): resp(lineup=old)}), t)
    r = m.person_result(t, d)
    assert d["teams"] == ["1-2"] and r["seen"] and r["back"] == 1 and not r["seen_last"], r


def test_gate_edges():
    s = {"n": 10, "seen": 7, "few": 6, "req_mean": 25, "sec_p90": 20.0}
    assert m.gate(s, None, 0.99) == {"1_seen": True, "2_few": True, "3_cost": True, "4_known": True}
    assert not m.gate(dict(s, seen=6), None, 1)["1_seen"] and not m.gate(dict(s, few=5), None, 1)["2_few"]
    for bad in (dict(s, req_mean=25.1), dict(s, sec_p90=20.1)):
        assert not m.gate(bad, None, 1)["3_cost"]
    assert not m.gate(s, "403", 1)["3_cost"] and not m.gate(s, None, 0.98)["4_known"] and m.gate(s, None, None)["4_known"] is None
    rows = [{"seen": d > 0, "seen_last": d > 0, "distinct": d, "filter_missed": False, "plain": False, "req": 15, "sec": 1.0,
             "age_h": 1.0} for d in (0, 1, 1, 2, 3, 4, 9)]
    x = m.summarize(rows)
    assert (x["few"], x["one"], x["seen"]) == (4, 2, 6), x       # 0 과 넷 이상은 "1~3개" 가 아니다


def test_pctile():
    assert m.pctile(list(range(1, 51)), 0.9) == 45 and m.pctile([3, 1, 2], 0.5) == 2 and m.pctile([1], 0.9) == 1


def test_blocked_stops(tmp=None):
    import tempfile
    keep = m.OUT, m.gameguard.game_running, traitcollect._post, m.GAP_S
    calls = []
    try:
        m.OUT, m.GAP_S = Path(tempfile.mkdtemp()), 0
        m.gameguard.game_running = lambda: False

        def post(_s, data):
            calls.append(data)
            raise traitcollect.TraitError("막힘", "rate")
        traitcollect._post = post
        for _ in range(2):
            try:
                m.Fetcher().cell(1, (1, 0), 0)
                raise AssertionError("안 멈췄다")
            except (m.Stop, traitcollect.TraitError):
                pass
        assert len(calls) == 1, "막힌 뒤에도 요청했다"
        m._save("meter.json", {"days": {}, "blocked": None})
        m.gameguard.game_running = lambda: True
        try:
            m.Fetcher().cell(1, (1, 0), 0)
            raise AssertionError("게임 중에 요청했다")
        except m.Stop:
            pass
        m.gameguard.game_running = lambda: False
        f = m.Fetcher()
        f.meter["days"][f.day] = m.DAILY_REQ
        try:
            f.cell(1, (1, 0), 0)
            raise AssertionError("상한을 넘겼다")
        except m.Stop:
            pass
        assert len(calls) == 1
    finally:
        m.OUT, m.gameguard.game_running, traitcollect._post, m.GAP_S = keep


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            with watchdog.limit(name):
                fn()
            print("[OK]", name)
        except Exception:
            bad += 1
            print("[FAIL]", name)
            traceback.print_exc()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
