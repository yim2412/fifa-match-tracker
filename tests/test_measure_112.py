"""112단계 관문 측정 도구(tools/measure_112.py) 테스트 — 지어낸 작은 dev_matches DB 만 읽습니다(네트워크 없음).

pytest 없이 `python tests/test_measure_112.py`. numpy 가 필요해 이 PC 에서만 돕니다(51 측정 테스트와 같습니다).
구단주 · 경기 id 는 전부 지어낸 값입니다. 근거는 ROADMAP "## 112단계 › 관문 측정" 8판과 7회차 검토 [중] 1~3.
"""
from __future__ import annotations

import math
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np  # noqa: E402

import watchdog  # noqa: E402 — config 보다 먼저(FIFA_NO_CRED)
import dev_matches  # noqa: E402
import measure_112 as M  # noqa: E402
import stats  # noqa: E402

POS_4123 = [0, 3, 5, 6, 7, 10, 12, 14, 23, 24, 27]   # GK · 수비 4 · 수미 1 · 미드 2 · 공격 3


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="m112-"))


# ── 선발 풀기 ────────────────────────────────────────────────────────────────

def test_lineup_roundtrip_narrow_and_wide():
    narrow = [{"spId": 100000 + i, "spPosition": p, "spGrade": 5} for i, p in enumerate(POS_4123)]
    body = dev_matches.lineup_body(narrow)
    assert len(body) % 2 == 0
    got = M.lineup_players(body)
    assert sorted(got) == sorted((p["spId"], p["spPosition"], p["spGrade"]) for p in narrow)
    wide = [dict(x) for x in narrow]
    wide[0]["spId"] = 1 << 40          # u32 를 넘어 넓은 꼴
    wb = dev_matches.lineup_body(wide)
    assert wb[0] == 1 and len(wb) % 2 == 1
    assert sorted(M.lineup_players(wb)) == sorted((p["spId"], p["spPosition"], p["spGrade"]) for p in wide)


def test_lineup_formation_matches_app_key():
    ps = [{"spId": 1 + i, "spPosition": p, "spGrade": 1} for i, p in enumerate(POS_4123)]
    key, n = M.lineup_formation(dev_matches.lineup_body(ps))
    assert key == stats.formation_of(ps) == "4-1-2-0-3", key
    assert n == 11


def test_lineup_bad_length_raises():
    for bad in (b"\x00" * 7, b"\x02" + b"\x00" * 12):
        try:
            M.lineup_players(bad)
        except ValueError:
            continue
        raise AssertionError(f"길이가 틀린 {bad!r} 를 받아들였습니다")


# ── 통계 조각 ────────────────────────────────────────────────────────────────

def test_bh_simple():
    p = np.array([0.001, 0.02, 0.03, 0.5])
    assert M.bh(p, 0.05).tolist() == [True, True, True, False]     # 문턱 0.0125 · 0.025 · 0.0375 · 0.05
    p2 = np.array([0.001, 0.02, 0.5, 0.9])
    assert M.bh(p2, 0.05).tolist() == [True, True, False, False]


def test_bh_monotone_step_up():
    # 세 번째가 문턱 안이면 앞의 두 번째도 통과(단계 올림)
    p = np.array([0.04, 0.001, 0.03])
    assert M.bh(p, 0.05).tolist() == [True, True, True]


def test_within_side_formula():
    """사람 둘 — 같은 날 X 와 X 아닌 것으로 Y 를 만남. d · 가중치 · 사람 묶음 SE 를 손으로 센다."""
    X, Y, other = 1, 2, 3
    rows = []   # (사람, 자기 포메이션, 날, 이김)
    rows += [(10, X, 0, 1), (10, X, 0, 1), (10, X, 0, 0), (10, other, 0, 0), (10, other, 0, 1)]   # X 2/3 · 아님 1/2
    rows += [(11, X, 5, 1), (11, other, 5, 0), (11, other, 5, 0)]                                  # X 1/1 · 아님 0/2
    rows += [(12, X, 3, 1), (12, X, 4, 0)]                                                          # 같은 날 짝 없음 → 빠짐
    rec = {"key": np.full(len(rows), Y * 2 + 1), "person": np.array([r[0] for r in rows]),
           "own": np.array([r[1] for r in rows]), "day": np.array([r[2] for r in rows]),
           "win": np.array([bool(r[3]) for r in rows])}
    got = M.within_side(rec, X, Y, 1)
    d = np.array([2 / 3 - 1 / 2, 1 - 0])
    w = np.array([2, 1])
    D = (w * d).sum() / w.sum()
    se = math.sqrt(((w * (d - D)) ** 2).sum()) / w.sum()
    assert got["owners"] == 2 and got["games"] == 8, got
    assert abs(got["d"] - D) < 1e-12 and abs(got["z"] - D / se) < 1e-9, got


# ── 작은 DB(A7 2번) — 7회차 [중] 1 · 2 ────────────────────────────────────────

def test_small_db_three_modes():
    d = _tmp()
    try:
        r = M.selftest(d, log=lambda *_: None)
        assert r["pre"]["conv"] == "pre" and r["post"]["conv"] == "post" and r["cross"]["conv"] is None, r
        assert all(r[m]["planted"] >= 400 for m in ("pre", "post", "cross"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_small_db_seed_is_fixed():
    d = _tmp()
    try:
        a = M.make_small_db(d / "a.db", "post")
        b = M.make_small_db(d / "b.db", "post")
        ca = sqlite3.connect(d / "a.db").execute("SELECT group_concat(r1) FROM games").fetchone()
        cb = sqlite3.connect(d / "b.db").execute("SELECT group_concat(r1) FROM games").fetchone()
        assert a == b and ca == cb
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_small_db_hot_only_on_promotions():
    """7회차 [중] 2 — 경기 뒤 꼴에서 0.9 는 승격을 끝낸 판에만. 강등을 끝낸 판의 승·무 비율은 0.6 언저리여야 합니다."""
    d = _tmp()
    try:
        M.make_small_db(d / "s.db", "post")
        G = M.freeze(d / "s.db", 0, 1 << 40, log=lambda *_: None)
        seqs = M.sequences(G)
        promo_ok = promo_n = dem_ok = dem_n = 0
        for _, seq in seqs.items():
            own, _, r, _, _ = M.seq_arrays(G, seq)
            for i in range(1, len(own)):
                if own[i] != own[i - 1]:
                    if own[i] < own[i - 1]:
                        promo_n += 1
                        promo_ok += r[i] >= 0
                    else:
                        dem_n += 1
                        dem_ok += r[i] >= 0
        assert promo_n >= 400 and dem_n >= 30, (promo_n, dem_n)
        assert promo_ok / promo_n > 0.8, promo_ok / promo_n
        assert dem_ok / dem_n < 0.75, dem_ok / dem_n
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_w0_uses_promotion_people_and_skips_w_games():
    """7회차 [중] 1 — w0 는 승격이 있는 사람만 · w 를 잰 판은 w0 에서 뺍니다."""
    d = _tmp()
    try:
        p = d / "w.db"
        c = sqlite3.connect(p)
        c.executescript(dev_matches.SCHEMA)
        # 사람 1: 1200 → 1100 승격(처음 보인 판 이김) · 그 밖 판은 전부 짐
        # 사람 2: 1100 → 1200 강등만 · 판은 전부 이김(w0 에 들어가면 w0 가 오른다)
        rows = [(1, 1200, -1), (1, 1200, -1), (1, 1100, 1), (1, 1100, -1),
                (2, 1100, 1), (2, 1100, 1), (2, 1200, 1), (2, 1200, 1)]
        t = 1000
        for k, (o, dv, res) in enumerate(rows):
            t += 60
            c.execute("INSERT INTO games(match_id, at, end_type, o1, d1, r1, o2, d2, r2) VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"m{k}", t, 0, o, dv, res, 50 + o, dv, -res))
        for o in (1, 2):
            c.execute("INSERT INTO owners(id, ouid, profile_sn) VALUES (?, ?, ?)", (o, f"u{o}", o))
            c.execute("INSERT INTO cursor(owner, top_id, top_at, back_done, base_at) VALUES (?, 'x', ?, 0, 0)", (o, t))
        for o in (51, 52):
            c.execute("INSERT INTO owners(id, ouid) VALUES (?, ?)", (o, f"v{o}"))
        c.commit()
        c.close()
        G = M.freeze(p, 0, 1 << 40, log=lambda *_: None)
        s = M.step2(G, M.sequences(G))
        assert s["w"] == 1.0 and s["w_n"] == 1, s
        assert s["w0_n"] == 3 and s["w0"] == 0.0, s           # 사람 1 의 나머지 세 판(전부 패)만
        assert s["promo_people"] == 1, s
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 회차 자르기 ──────────────────────────────────────────────────────────────

def _seq_db(path, divs, results, *, ends=None):
    c = sqlite3.connect(path)
    c.executescript(dev_matches.SCHEMA)
    t = 1000
    for k, (dv, res) in enumerate(zip(divs, results)):
        t += 60
        c.execute("INSERT INTO games(match_id, at, end_type, o1, d1, r1, o2, d2, r2) VALUES (?,?,?,?,?,?,?,?,?)",
                  (f"m{k:04d}", t, (ends or {}).get(k, 0), 1, dv, res, 2, dv, -res))
    c.execute("INSERT INTO owners(id, ouid, profile_sn) VALUES (1, 'u1', 1)")
    c.execute("INSERT INTO owners(id, ouid) VALUES (2, 'u2')")
    c.execute("INSERT INTO cursor(owner, top_id, top_at, back_done, base_at) VALUES (1, 'x', ?, 0, 0)", (t,))
    c.commit()
    c.close()


def test_sets_post_convention_chain_and_promo():
    """경기 뒤 값: 판 2 가 끝나고 1300 → 1200(아래에서 들어옴). 그 뒤 10판 유지 → 사슬 회차 6판째 승격(전승 18점)."""
    d = _tmp()
    try:
        divs = [1300, 1300, 1200] + [1200] * 10 + [1200] * 5 + [1100] + [1100] * 3
        res = [1] * len(divs)
        res[3:13] = [1, 0, -1, -1, 1, 0, -1, -1, 1, 0]   # 유지 회차 10판: 3승 3무 4패 = 12점
        _seq_db(d / "s.db", divs, res)
        G = M.freeze(d / "s.db", 0, 1 << 40, log=lambda *_: None)
        bs = M.build_sets(G, M.sequences(G), "post")
        got = [(x[1], x[2], x[3], x[4], x[5]) for x in bs["rows"]]
        assert got[0] == (1200, "up", 10, 12, "stay"), got
        assert got[1] == (1200, "stay", 6, 18, "promo"), got
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sets_pre_convention_shifts_one_game():
    """경기 전 값이면 바뀐 값이 처음 보인 판이 새 회차 첫 판입니다(경기 뒤 값과 한 칸 다름)."""
    d = _tmp()
    try:
        divs = [1300, 1300, 1200] + [1200] * 9 + [1100, 1100]
        res = [1] * len(divs)
        res[2] = -1                                              # 판 2 는 경기 전 꼴에서만 새 회차에 든다
        _seq_db(d / "s.db", divs, res)
        G = M.freeze(d / "s.db", 0, 1 << 40, log=lambda *_: None)
        bs = M.build_sets(G, M.sequences(G), "pre")
        got = [(x[1], x[2], x[3], x[4], x[5]) for x in bs["rows"]]
        assert got == [(1200, "up", 10, 27, "promo")], got        # 판 2~11 이 회차 · 판 12 에 1100 이 보임
        bs2 = M.build_sets(G, M.sequences(G), "post")
        got2 = [(x[1], x[2], x[3], x[4], x[5]) for x in bs2["rows"]]
        assert got2 == [(1200, "up", 10, 30, "promo")], got2      # 판 3~12 가 회차
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sets_forfeit_marked():
    d = _tmp()
    try:
        divs = [1300, 1200] + [1200] * 10 + [1200]
        _seq_db(d / "s.db", divs, [1] * len(divs), ends={4: 2})
        G = M.freeze(d / "s.db", 0, 1 << 40, log=lambda *_: None)
        rows = M.build_sets(G, M.sequences(G), "post")["rows"]
        assert rows and rows[0][6] == "몰수·탈주", rows
        assert M.clean_sets(rows)["sets"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fit_picks_planted_early_rule():
    """R3(조기 확정)로 만든 회차는 R1 · R2 가 못 맞히고 R3 를 고릅니다 — 회차 자르기가 한 칸 밀리면 FAIL."""
    rng = np.random.default_rng(7)
    sets = []
    for n in range(4000):
        res = rng.choice([1, 0, -1], size=10, p=[0.45, 0.2, 0.35])
        cum, pts, k, out = [], 0, 0, "stay"
        for k in range(1, 11):
            pts += M.PTS[int(res[k - 1])]
            cum.append(pts)
            if pts >= 15:
                out = "promo"
                break
            if pts + 3 * (10 - k) <= 8:
                out = "demote"
                break
        sets.append((n, 1200, "up", k, pts, out, "", cum + [cum[-1]] * (10 - len(cum)), n & 1))
    fit = M.fit_rules(sets)
    assert fit["pick"] == "R3", fit
    assert fit["rules"]["R3"]["th"]["up"] == (15, 8), fit["rules"]["R3"]
    assert not fit["rules"]["R1"]["ok"]


def test_clean_sets_drops_cell_over_one_percent():
    good = [(i, 1200, "up", 10, 16, "promo", "", [0] * 10, 0) for i in range(99)]
    bad = [(500, 1200, "up", 2, 6, "promo", "", [0] * 10, 0)] * 2       # 2/101 > 1%
    r = M.clean_sets(good + bad)
    assert "1200·up" in r["dropped"] and r["sets"] == []
    r2 = M.clean_sets(good + bad[:1] + [(i, 1200, "up", 10, 16, "promo", "", [0] * 10, 0) for i in range(99, 200)])
    assert r2["dropped"] == [] and len(r2["sets"]) == 200                # 1/201 ≤ 1% → 그 줄만 뺌


def test_clean_sets_removes_1000_promotions():
    rows = [(i, 1000, "up", 10, 16, "promo", "", [0] * 10, 0) for i in range(5)] + \
           [(i, 1000, "up", 10, 5, "demote", "", [0] * 10, 0) for i in range(5)]
    r = M.clean_sets(rows)
    assert {x[5] for x in r["sets"]} == {"demote"}


def test_assume_10_flags_long_sets():
    assert M.assume_10(np.array([7, 8, 9, 10, 3, 17, 0]))["ok"]
    assert not M.assume_10(np.array([12, 13, 14, 12, 3]))["ok"]


# ── 가짜 A7 심기 ─────────────────────────────────────────────────────────────

def test_plant_r1_post_then_tool_recovers():
    """심은 R1(15 · 8)을 경기 뒤 값으로 쓰면 도구가 R1 을 고르고 거의 다 맞힙니다."""
    d = _tmp()
    try:
        rng = np.random.default_rng(3)
        res = rng.choice([1, 0, -1], size=3000, p=[0.35, 0.2, 0.45]).tolist()
        _seq_db(d / "s.db", [1700] * len(res), res)
        G = M.freeze(d / "s.db", 0, 1 << 40, log=lambda *_: None)
        seqs = M.sequences(G)
        D1, D2 = M.plant_divisions(G, seqs, "R1", "post")
        bs = M.build_sets(G, seqs, "post", D1, D2)
        rows = [x[:8] + ((k & 1),) for k, x in enumerate(bs["rows"])]   # 한 사람이라 회차 번호로 반 나누기
        fit = M.fit_rules(M.clean_sets(rows)["sets"])
        assert fit["pick"] == "R1", fit
        tp, td = fit["rules"]["R1"]["th"]["all"]
        sets = M.clean_sets(rows)["sets"]
        # 강등 문턱은 자료에 그 점수로 끝난 회차가 없으면 한 칸 아래와 구별이 안 됩니다(맞힘률이 같습니다)
        lows = {x[4] for x in sets if x[3] == 10 and x[4] <= 8}
        assert tp == 15 and (td == 8 or (8 not in lows and td == max(v for v in lows if v < 8))), (tp, td, lows)
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
