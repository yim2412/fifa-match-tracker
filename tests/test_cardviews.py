"""카드 정보 화면 재료(68 일부) — 지어낸 경기·카드 정보(네트워크 없음). `python tests/test_cardviews.py`"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cardstats  # noqa: E402
import cardviews as cv  # noqa: E402
import config  # noqa: E402
import store  # noqa: E402
import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패)

GRADE = 5
BONUS = config.GRADE_OVR_BONUS[GRADE]
POS = next(p for p in sorted(config.SIM_POS_OF) if p != 28)   # 시뮬레이터 칸이 있는 자리
CELL = config.SIM_POS_OF[POS]


def extra(ovr=100, **kw):
    return {"pos_ovr": json.dumps({CELL: ovr}), "height": 180, "body": "보통", **kw}


def side(spids, grade=GRADE, ouid="me", pos=POS):
    return {"ouid": ouid, "player": [{"spId": s, "spPosition": pos, "spGrade": grade} for s in spids]
            + [{"spId": 999, "spPosition": cardstats.SUB_POSITION, "spGrade": grade}]}


def game(spids, **kw):
    return {"matchInfo": [side(spids, **kw), side(range(500, 511), ouid="상대")]}


ELEVEN = list(range(1, 12))


def test_player_card_values_and_unknown():
    ex = {7: extra(120, height=191, body="마름")}
    assert cv.player_card(7, POS, GRADE, ex) == cv.PlayerCard(120 + BONUS, 191, "마름")
    assert cv.player_card(8, POS, GRADE, ex) == cv.PlayerCard(), "카드 정보가 없는 카드"
    # 자리·강화를 몰라도 키·체형은 안다 · 빈 값은 None(빈 칸을 채우지 않는다)
    assert cv.player_card(7, cardstats.SUB_POSITION, GRADE, ex).ovr is None
    assert cv.player_card(7, POS, 99, ex) == cv.PlayerCard(None, 191, "마름")
    assert cv.player_card(1, POS, GRADE, {1: {"pos_ovr": None, "height": 0, "body": ""}}) == cv.PlayerCard()


def test_squad_ovr_by_hand():
    ex = {s: extra(100 + s) for s in ELEVEN}
    ex.update({s: extra(110) for s in range(20, 31)})
    first = sum(100 + s for s in ELEVEN) / 11 + BONUS          # 106 + 보너스
    second = 110 + BONUS
    r = cv.squad_ovr([game(ELEVEN), game(list(range(20, 31)))], "me", ex)
    assert (r.games, r.known) == (2, 2), r
    assert abs(r.avg - (first + second) / 2) < 1e-9, r
    want_sd = (((first - r.avg) ** 2 + (second - r.avg) ** 2) / 1) ** 0.5
    assert abs(r.sd - want_sd) < 1e-9, (r.sd, want_sd)


def test_squad_ovr_same_as_measure_formula():
    # 측정 도구가 쓰는 식과 어긋나면 앱의 Δ 가 측정과 달라진다 — 판마다 같은 값이어야 한다
    ex = {s: extra(90 + s * 2) for s in range(1, 40)}
    lineups = [list(range(k, k + 11)) for k in (1, 5, 13, 25)]
    for lu in lineups:
        for grade in (1, GRADE, 8):
            g = game(lu, grade=grade)
            one = cv.squad_ovr([g], "me", ex)
            want, _why = cardstats.lineup_strength(g["matchInfo"][0], ex)
            assert one.avg == want, (lu, grade, one.avg, want)


def test_squad_ovr_skips_unknown_games():
    ex = {s: extra(100) for s in ELEVEN}
    missing = ELEVEN[:-1] + [77]          # 한 장 모름 — 그 판은 뺀다(옛값으로 채우지 않는다)
    ten = ELEVEN[:-1]                     # 10장뿐 — 안 센다
    r = cv.squad_ovr([game(ELEVEN), game(missing), game(ten)], "me", ex)
    assert (r.games, r.known) == (3, 1) and r.avg == 100 + BONUS and r.sd is None, r
    empty = cv.squad_ovr([game(missing)], "me", ex)
    assert (empty.games, empty.known, empty.avg, empty.sd) == (1, 0, None, None), empty
    assert cv.squad_ovr([], "me", ex) == cv.SquadOvr()
    assert cv.squad_ovr([game(ELEVEN)], "남", ex).games == 0, "내가 안 낀 경기를 셌다"
    # 교체 명단(999)은 11장에 안 든다 — 들면 12장이라 모두 빠진다
    assert 999 not in cv.lineup_cards([game(ELEVEN)], "me")
    assert cv.lineup_cards([game(ELEVEN), game([40])], "me") == set(ELEVEN) | {40}


def test_squad_ovr_budget_many_games():
    import time
    ex = {s: extra(100 + s % 9) for s in range(1, 60)}
    games = [game(list(range(1 + i % 40, 12 + i % 40))) for i in range(20000)]
    t = time.perf_counter()
    r = cv.squad_ovr(games, "me", ex)
    ms = (time.perf_counter() - t) * 1000
    print(f"       2만 경기: {ms:.0f}ms")
    assert r.known == 20000 and ms < 1500, (r.known, ms)


def test_store_load_card_extras():
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            assert store.load_card_extras(conn, []) == {}
            ids = list(range(1000, 1000 + store.SQL_IN_CHUNK + 5))
            conn.executemany("INSERT INTO card_extra (spid, height, weight, body, pos_ovr) VALUES (?, ?, ?, ?, ?)",
                             [(s, 180, 70, "보통", json.dumps({CELL: 100})) for s in ids])
            conn.commit()
            got = store.load_card_extras(conn, ids + [5])
            assert set(got) == set(ids), "묶음 경계에서 빠졌거나 없는 카드가 들어왔다"
            assert got[1000]["height"] == 180 and got[1000]["body"] == "보통" and cardstats.pos_ovrs(got[1000]) == {CELL: 100}
        finally:
            conn.close()


def main() -> int:
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
