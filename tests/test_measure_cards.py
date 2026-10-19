"""51단계 측정 도구(tools/measure_cards.py) 테스트 — 지어낸 fifa.db·rank.db·가짜 백필, 네트워크 없이.

pytest 없이 `python tests/test_measure_cards.py`. 닉네임·ouid·경기 id·카드 번호는 전부 지어낸 값이다.
근거는 ROADMAP "## 51단계 › 측정으로 먼저 정할 것"(8판) — 일정 · 고정 수요 · 방향 · 뺀 이유 · B 짝짓기(가장 가까운 · 24시간 · 같은 시즌 ·
한 번만 · 닉네임 안 남김) · 절반 · C 의 카드 집합 · 토글 켜짐 거부.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np  # noqa: E402

import cardcollect  # noqa: E402
import config  # noqa: E402
import measure_cards as M  # noqa: E402
import store  # noqa: E402
from seasons import Season  # noqa: E402

D0 = date(2026, 11, 1)
ME, YOU, OTHER = "acc-1", "opp-1", "acc-2"


# ── 가짜 ──────────────────────────────────────────────────────────────────────

def side(ouid, res, cards, *, nick="n", so=0, end=0, gk=None):
    """한쪽 — cards: [(spid, 강화)] 11장(자리는 0·4·5·… 순) + 교체 한 장(28)."""
    pos = [0, 4, 6, 3, 7, 10, 13, 15, 23, 24, 27]
    ps = [{"spId": s, "spPosition": pos[i], "spGrade": g} for i, (s, g) in enumerate(cards)]
    ps.append({"spId": 999, "spPosition": 28, "spGrade": 1})
    return {"ouid": ouid, "nickname": nick, "matchDetail": {"matchResult": res, "matchEndType": end},
            "shoot": {"goalTotal": 1, "shootOutScore": so}, "player": ps}


def match(mid, at, a, b):
    return {"matchId": mid, "matchType": 52, "matchDate": at.isoformat(), "matchInfo": [a, b]}


def cards(base, n=11, g=1):
    return [(base + i, g) for i in range(n)]


@contextmanager
def env():
    d = Path(tempfile.mkdtemp(prefix="mc_"))
    try:
        conn = store.open_db(d / "fifa.db")
        conn.execute("INSERT INTO accounts (ouid, nickname) VALUES (?, ?), (?, ?)", (ME, "나", OTHER, "나2"))
        conn.commit()
        yield d, conn
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def put_extra(conn, spid, ovr=80, *, height=180, club=(), rel=(), sim=True, page=True):
    conn.execute("INSERT OR REPLACE INTO card_extra (spid, height, body, page_on, pos_ovr, club_tc, rel_tc, sim_on)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (spid, height if page else None, "보통" if page else None, "2026-11-02" if page else None,
                  json.dumps({c: ovr for c in set(config.SIM_POS_OF.values())}) if sim else None,
                  json.dumps([[t, f"tc{t}"] for t in club]) if sim else None,
                  json.dumps([[t, f"tc{t}"] for t in rel]) if sim else None, "2026-11-02" if sim else None))
    conn.commit()


def rank_db(path, snaps):
    """snaps: [(id, 시각, {닉네임: (팀컬러, 인원)})]."""
    r = sqlite3.connect(path)
    r.executescript("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, taken_at TEXT);"
                    "CREATE TABLE snapshot_meta (snapshot_id INTEGER PRIMARY KEY, ref_time TEXT);"
                    "CREATE TABLE snapshot_rows (snapshot_id INTEGER, nickname TEXT, team_color TEXT, color_count INTEGER);")
    for sid, at, people in snaps:
        r.execute("INSERT INTO snapshots VALUES (?, ?)", (sid, at.isoformat()))
        for nick, (tc, k) in people.items():
            r.execute("INSERT INTO snapshot_rows VALUES (?, ?, ?, ?)", (sid, nick, tc, k))
    r.commit()
    r.row_factory = sqlite3.Row
    return r


# ── 일정 ─────────────────────────────────────────────────────────────────────

def test_start_once_and_schedule():
    with env() as (d, _c):
        ok, _t = M.start(d / "m", D0)
        assert ok
        ok, _t = M.start(d / "m", D0 + timedelta(days=3))
        assert not ok, "D0 를 두 번 잡았다"
        assert M.day_no(M.load_json(d / "m" / "meta.json"), D0 + timedelta(days=21)) == 21
        assert M.load_json(d / "m" / "meta.json")["rule_commit"] == M.RULE_COMMIT


# ── A 재료 ───────────────────────────────────────────────────────────────────

def test_outcome_drops():
    t = datetime(2026, 10, 20, 12)
    a, b = cards(100), cards(200)
    assert M.outcome("x", match("x", t, side(ME, "무", a), side(YOU, "무", b))) == (None, "draw")
    assert M.outcome("x", match("x", t, side(ME, "승", a, so=5), side(YOU, "패", b, so=4)))[1] == "shootout"
    assert M.outcome("x", match("x", t, side(ME, "몰수승", a, end=1), side(YOU, "몰수패", b, end=2)))[1] == "forfeit"
    # 방향: sha256 비트 쪽이 "나" — 그 쪽이 이겼으면 1
    for mid in ("m0", "m1", "m2", "m3"):
        det = match(mid, t, side(ME, "승", a), side(YOU, "패", b))
        win_side = M.sides(det)[M.me_index(mid)]["ouid"]
        assert M.outcome(mid, det) == (1 if win_side == ME else 0, "")
    assert {M.me_index(f"m{i}") for i in range(16)} == {0, 1}, "방향이 한쪽으로만 간다"


def test_window_order_and_bounds():
    with env() as (d, conn):
        inside = D0 - timedelta(days=10)
        store.save_matches(conn, [
            match("w1", datetime.combine(inside, datetime.min.time()), side(ME, "승", cards(100)), side(YOU, "패", cards(200))),
            match("w2", datetime.combine(inside, datetime.min.time()) + timedelta(hours=1),
                  side(ME, "승", cards(100)), side(YOU, "패", cards(300))),
            # 창 밖(D0 당일 · 61일 전) · 계정 경기가 아님
            match("o1", datetime.combine(D0, datetime.min.time()), side(ME, "승", cards(400)), side(YOU, "패", cards(400))),
            match("o2", datetime.combine(D0 - timedelta(days=61), datetime.min.time()), side(ME, "승", cards(500)), side(YOU, "패", cards(500))),
            match("o3", datetime.combine(inside, datetime.min.time()), side("x", "승", cards(600)), side("y", "패", cards(600))),
        ])
        frozen = [{"opp": [[700, 1, 0], [701, 1, 4]]}]
        w = M.window_cards(conn, D0, frozen)
        assert w[:2] == [700, 701], "B 얼린 경기 상대가 먼저가 아니다"
        assert w[2:13] == list(range(100, 111)), "등장 많은 카드(두 경기)가 다음이 아니다"
        assert set(w[13:]) == set(range(200, 211)) | set(range(300, 311))
        assert not {400, 500, 600, 999} & set(w), "창 밖·계정 밖·교체 카드가 수요에 들었다"


def test_a_rows_delta_and_reasons():
    with env() as (d, conn):
        t = datetime.combine(D0 - timedelta(days=5), datetime.min.time())
        store.save_matches(conn, [
            match("k1", t, side(ME, "승", cards(100)), side(YOU, "패", cards(200))),
            match("k2", t + timedelta(hours=1), side(ME, "승", cards(100)), side(YOU, "패", cards(300))),   # 300 모름
            match("k3", t + timedelta(hours=2), side(ME, "승", cards(100)), side(YOU, "패", cards(400))),   # 400 형식 실패
            match("k4", t + timedelta(hours=3), side(ME, "승", cards(100, g=8)), side(YOU, "패", cards(200))),
            match("k5", t + timedelta(hours=4), side(ME, "승", cards(100)), side(YOU, "패", cards(500))),   # 500 모름(둘째)
        ])
        for s in range(100, 111):
            put_extra(conn, s, 90)
        for s in range(200, 211):
            put_extra(conn, s, 80)
        conn.execute("INSERT INTO card_tries (key, day, fails) VALUES ('c:400:sim', '2026-11-02', 1)")
        conn.commit()
        r = M.a_rows(conn, D0)
        assert r.total == 5
        assert r.dropped == {"none": 2, "format": 1}, r.dropped   # 둘이 다른 수라야 이유를 뒤바꾼 것이 보인다
        assert r.dropped_season == {("none", 0): 2, ("format", 0): 1}, r.dropped_season
        assert len(r.delta) == 2
        for mid, dl, y, want in zip(("k1", "k4"), r.delta, r.y, (10, 25)):
            mine_is_me = M.sides(M.store._loads(conn.execute("SELECT payload FROM matches WHERE match_id = ?", (mid,)).fetchone()[0]))[M.me_index(mid)]["ouid"] == ME
            assert abs(dl - (want if mine_is_me else -want)) < 1e-9, (mid, dl)   # 강화 8 = +15
            assert y == (1 if mine_is_me else 0)
        assert all(s in (1, -1) for s in r.acc_sign)


# ── A 판정 ───────────────────────────────────────────────────────────────────

def test_judge_a_null_and_strong():
    rng = np.random.default_rng(6)   # 5 는 ③ 오경보(맞는 모델도 약 17% 미달 — 관문 검토 거리)에 걸린다
    n = 3000
    x = rng.normal(0, 4, n)
    when = M._fake_when(n)
    r = M.judge_a(when, x, (rng.random(n) < 0.5).astype(int))
    assert r["verdict"] != "통과" and not r.get("pass2"), r["verdict"]
    r = M.judge_a(when, x, (rng.random(n) < M.predict(0, 0.5, x)).astype(int))
    assert r["verdict"] == "통과", r
    assert 0.3 < r["b"] < 0.7, r["b"]


def test_judge_a_undecidable_is_material_short():
    """구간이 1% 를 품으면 ②~④ 를 안 본다 — 효과 ≈ 1% · 작은 n."""
    rng = np.random.default_rng(9)
    n = 600
    x = rng.normal(0, 4, n)
    b = M.b_for_gain(x, 0.01)
    r = M.judge_a(M._fake_when(n), x, (rng.random(n) < M.predict(0, b, x)).astype(int))
    assert r["verdict"] == "재료 부족" and "calib" not in r, r


def test_calib_catches_shift():
    rng = np.random.default_rng(3)
    p = M.predict(0, 0.15, rng.normal(0, 4, 4000))
    y = (rng.random(4000) < p).astype(float)
    assert M.calib(p, y, np.random.default_rng(1))["pass"]
    assert not M.calib(np.clip(p + 0.08, 0, 1), y, np.random.default_rng(1))["pass"]


def test_b_for_gain():
    x = np.random.default_rng(1).normal(0, 4, 20000)
    b = M.b_for_gain(x, 0.02)
    p = M.predict(0, b, x)
    h = lambda q: -(q * np.log(q) + (1 - q) * np.log(1 - q))  # noqa: E731
    assert abs((1 - h(p).mean() / h(p.mean())) - 0.02) < 1e-4


# ── B ────────────────────────────────────────────────────────────────────────

def test_b_freeze_pairing():
    with env() as (d, conn):
        conn.execute("INSERT INTO seasons (no, name, start_date, end_date, fetched_at) VALUES (1, '시즌 1', '2026-09-01', '2026-11-03', '')")
        conn.commit()
        t = datetime(2026, 11, 1, 12)
        store.save_matches(conn, [
            match("b1", t, side(ME, "승", cards(100)), side(YOU, "패", cards(200), nick="상대")),
            match("b2", t + timedelta(days=4, hours=12), side(ME, "승", cards(100)), side(YOU, "패", cards(200), nick="상대")),  # 양쪽 스냅숏 36시간
            match("b3", t, side(ME, "승", cards(100)), side(OTHER, "패", cards(200))),                                 # 두 쪽 다 계정
            match("b4", t + timedelta(days=1, hours=13), side(ME, "승", cards(100)), side(YOU, "패", cards(200), nick="상대")),  # 시즌 경계 너머 스냅숏
            match("b5", t + timedelta(days=6, hours=1), side(ME, "승", cards(100)), side(YOU, "패", cards(200), nick="상대")),   # 24시간 안 지남
        ])
        rk = rank_db(d / "rank.db", [
            (1, t - timedelta(hours=10), {"상대": ("첼시", 9)}),
            (2, t + timedelta(hours=3), {"상대": ("리버풀", 11)}),     # b1 에 가장 가깝다
            (3, t + timedelta(days=1, hours=11), {"상대": ("리버풀", 11)}),   # 11-02 23시 — b4(11-03 01시)와 시즌이 다르다
            (4, t + timedelta(days=3), {"상대": ("리버풀", 11)}),
            (5, t + timedelta(days=6), {"상대": ("리버풀", 11)}),
        ])
        fp = d / "m" / "b_frozen.jsonl"
        now = t + timedelta(days=6, hours=5)
        s1 = M.b_freeze(conn, rk, fp, now)
        fr = M.read_frozen(fp)
        assert [f["match_id"] for f in fr] == ["b1"], (fr, s1)
        assert fr[0]["team_color"] == "리버풀" and fr[0]["dt_h"] == 3.0
        assert s1["far"] == 1 and s1["both_accounts"] == 1 and s1["season"] == 1 and s1["dt_h_p50"] == 3.0, s1
        assert "상대" not in fp.read_text(encoding="utf-8") and YOU not in fp.read_text(encoding="utf-8"), "닉네임·ouid 가 남았다"
        # 한 번만 — 더 가까운 스냅숏이 나중에 생겨도 다시 얼리지 않는다
        rk.execute("INSERT INTO snapshots VALUES (9, ?)", ((t + timedelta(minutes=5)).isoformat(),))
        rk.execute("INSERT INTO snapshot_rows VALUES (9, '상대', '아스널', 11)")
        rk.commit()
        assert M.b_freeze(conn, rk, fp, now + timedelta(days=1)).get("frozen") == 1   # b5 만 새로
        fr = M.read_frozen(fp)
        assert [f["match_id"] for f in fr] == ["b1", "b5"] and fr[0]["team_color"] == "리버풀"
        rk.close()


def test_rules_r1_r2():
    need = {1: [5, 8], 2: [3, 6], 3: [3]}
    # 8장이 1(소속) · 그중 5장이 2(관계)도 · 3장이 3(관계)
    cs = [([1], [2]) for _ in range(5)] + [([1], [3]) for _ in range(3)] + [([], []) for _ in range(3)]
    r1, r2 = M.active_r1(cs, need), M.active_r2(cs, need)
    assert r1 == [(1, 8, 2)], r1                      # 카드가 1 에 다 가서 2·3 은 0장
    assert sorted(r2) == [(1, 8, 2), (2, 5, 1), (3, 3, 1)], r2
    assert M.main_tc(r1)[0] == M.main_tc(r2)[0] == 1


def _frozen(n, start, tc_name="tc1"):
    return [{"match_id": f"f{start + i}", "match_date": (datetime(2026, 11, 1) + timedelta(minutes=start + i)).isoformat(),
             "team_color": tc_name, "color_count": 11, "opp": [[1000 + j, 1, 0] for j in range(11)]} for i in range(n)]


def test_judge_b_halves():
    with env() as (d, conn):
        for j in range(11):
            put_extra(conn, 1000 + j, club=[1])
        conn.execute("INSERT INTO teamcolor_meta (id, name, emblem, fetched_on) VALUES (1, 'tc1', 'x', '2026-11-01')")
        conn.execute("INSERT INTO teamcolor_steps (id, step, members, fetched_on) VALUES (1, 1, 5, '2026-11-01')")
        conn.commit()
        mat = M.b_material(conn)
        r = M.judge_b(_frozen(400, 0), *mat)
        assert r["verdict"] == "재료 부족", r["verdict"]           # 절반 200 < 300
        r = M.judge_b(_frozen(700, 0), *mat)
        assert r["verdict"] == "통과" and r["front"]["R1"]["rate"] == 1.0, r
        # 뒤 절반만 어긋나면 미달 — 앞 절반으로 고른 규칙을 뒤에서 한 번
        r = M.judge_b(_frozen(350, 0) + _frozen(350, 1000, tc_name="다른"), *mat)
        assert r["verdict"] == "미달" and r["back"]["rate"] == 0.0, r
        # 앞 절반 미달이면 뒤 절반을 열지 않는다
        r = M.judge_b(_frozen(350, 0, tc_name="다른") + _frozen(350, 1000), *mat)
        assert r["verdict"] == "앞 절반 미달" and "back" not in r, r
        # 팀컬러 모르는 카드가 하나라도 있으면 그 경기는 뺀다
        conn.execute("UPDATE card_extra SET sim_on = NULL, club_tc = NULL WHERE spid = 1003")
        conn.commit()
        e = M.b_eval(_frozen(10, 0), *M.b_material(conn), "R1")
        assert e["n"] == 0 and e["dropped"] == {"tc_unknown": 10}, e
        assert M._same_main(_frozen(10, 0), *M.b_material(conn)[:2]) is None, "모르는 카드 경기를 R1·R2 비교에 넣었다"


def test_b_eval_ties_and_counts():
    with env() as (d, conn):
        for j in range(11):
            put_extra(conn, 1000 + j, club=[1, 2] if j < 6 else [1])
        conn.execute("INSERT INTO teamcolor_meta (id, name, emblem, fetched_on) VALUES (1, 'tc1', 'x', '2026-11-01'), (2, 'tc2', 'x', '2026-11-01'),"
                     " (3, 'tc3', 'x', '2026-11-01')")
        conn.executemany("INSERT INTO teamcolor_steps (id, step, members, fetched_on) VALUES (?, 1, ?, '2026-11-01')", [(1, 5), (2, 3)])
        conn.commit()
        fr = _frozen(4, 0)
        for f in fr:
            f["color_count"] = 9
        fr[0]["team_color"] = ""
        e = M.b_eval(fr, *M.b_material(conn), "R2")
        assert e["n"] == 3 and e["dropped"] == {"snap_empty": 1} and e["agree"] == 3, e
        assert e["ties"] == 0 and e["count_diff_mean"] == 2.0, e   # 주 팀컬러 11장 − 스냅숏 9
        # 동률 — 5장 · 5장(같은 단계) → 셈 · 주 팀컬러는 id 작은 쪽
        conn.execute("UPDATE teamcolor_steps SET members = 3")
        for j in range(11):
            put_extra(conn, 1000 + j, club=[1] if j < 5 else [2] if j < 10 else [3])
        e = M.b_eval(fr[1:], *M.b_material(conn), "R1")
        assert e["ties"] == 3 and e["agree"] == 3, e
        # 단계를 모르는 팀컬러(3)가 1장뿐이면 그 경기는 남는다 · 3장 이상이면 뺀다
        assert e["dropped"] == {}, e
        for j in range(8, 11):
            put_extra(conn, 1000 + j, club=[3])
        assert M.b_eval(fr[1:], *M.b_material(conn), "R1")["dropped"] == {"steps_unknown": 3}


# ── C ────────────────────────────────────────────────────────────────────────

def test_count_c_known_window():
    with env() as (d, conn):
        old = datetime.combine(D0 - timedelta(days=50), datetime.min.time())
        new = datetime.combine(D0 - timedelta(days=5), datetime.min.time())
        ms = []
        for i in range(31):   # 승부차기 31판 — 내 GK 100(190cm) vs 상대 GK 200(185) → 차 +5
            ms.append(match(f"s{i}", new + timedelta(minutes=i), side(ME, "무", cards(100), so=5), side(YOU, "무", cards(200), so=4)))
        ms.append(match("old", old, side(ME, "승", cards(300)), side(YOU, "패", cards(400))))   # 창 밖 경기의 카드
        store.save_matches(conn, ms)
        for s in range(100, 111):
            put_extra(conn, s, 80, height=190)
        for s in range(200, 211):
            put_extra(conn, s, 80, height=185)
        for s in range(300, 311):
            put_extra(conn, s, 80, height=170)
        rows = {r["matches"]: r for r in M.count_c(conn, D0)}
        me = rows[32]
        assert me["n21"] == [0, 0, 31], me["n21"]
        assert not me["n21_pass"], "칸 셋 모두 30 이어야 한다"
        assert me["c3_ovr_bins"] == [], "키 칸이 하나뿐인데 C3 이 통과"
        assert me["known_cards"] == 22, "창 밖 경기(300번대) 카드가 아는 것으로 들어갔다"


def test_count_c_c3_two_height_bins_and_median():
    with env() as (d, conn):
        t = datetime.combine(D0 - timedelta(days=5), datetime.min.time())
        ms = [match(f"a{i}", t + timedelta(minutes=i), side(ME, "승", cards(100)), side(YOU, "패", cards(200))) for i in range(30)]
        ms += [match(f"b{i}", t + timedelta(hours=1, minutes=i), side(ME, "승", cards(300)), side(YOU, "패", cards(200))) for i in range(30)]
        ms += [match(f"s{i}", t + timedelta(hours=2, minutes=i), side(ME, "무", cards(100), so=5),
                     side(YOU, "무", cards(400 + 20 * i, n=11), so=4)) for i in range(4)]
        store.save_matches(conn, ms)
        for s in range(100, 111):
            put_extra(conn, s, 80, height=180)
        for s in range(300, 311):
            put_extra(conn, s, 80, height=190)        # 같은 OVR 칸 · 다른 키 칸
        for i in range(4):
            put_extra(conn, 400 + 20 * i, 70 + i, height=180 + i)   # 상대 GK 키 180~183 · OVR 70~73
        me = next(r for r in M.count_c(conn, D0) if r["matches"] == 64)
        assert me["c3_ovr_bins"] == [80] and me["c3_pass"], me
        assert me["n21_median_split"] == {"height": [2, 2], "ovr": [2, 2]}, me["n21_median_split"]
        assert me["n21"] == [1, 3, 0], me["n21"]   # 내 GK 180 − 상대 180~183 → 0·−1·−2 셋 · −3 하나


# ── 명령 관문 ────────────────────────────────────────────────────────────────

def test_cmd_b_c_gates_once():
    with env() as (d, conn):
        m = d / "m"
        M.start(m, D0)
        logs = []
        assert M.cmd_b(d / "fifa.db", m, D0 + timedelta(days=34), log=logs.append) == 2, "D0+34 에 B"
        assert M.cmd_c(d / "fifa.db", m, D0 + timedelta(days=34), log=logs.append) == 2, "D0+34 에 C"
        day = D0 + timedelta(days=35)
        assert M.cmd_b(d / "fifa.db", m, day, retry="R1", log=logs.append) == 2, "첫 판정 전에 retry"
        assert M.cmd_b(d / "fifa.db", m, day, log=logs.append) == 0
        first = M.load_json(m / "b_result.json")
        assert first["verdict"] == "재료 부족"
        assert M.cmd_b(d / "fifa.db", m, day, log=logs.append) == 0 and M.load_json(m / "b_result.json") == first, "두 번 쟀다"
        assert M.cmd_b(d / "fifa.db", m, day, retry="R1", log=logs.append) == 2, "앞 절반 미달이 아닌데 retry"
        M.save_json(m / "b_result.json", {"verdict": "앞 절반 미달"})
        assert M.cmd_b(d / "fifa.db", m, day, retry="R9", log=logs.append) == 2, "모르는 규칙"
        assert M.cmd_b(d / "fifa.db", m, day, retry="R2", log=logs.append) == 0
        r = M.load_json(m / "b_result.json")
        assert r["verdict"] == "앞 절반 미달" and r["verdict_final"] == "재료 부족" and r["retry"]["back"]["rule"] == "R2", r
        assert M.cmd_b(d / "fifa.db", m, day, retry="R2", log=logs.append) == 2, "retry 두 번"
        assert M.cmd_c(d / "fifa.db", m, day, log=logs.append) == 0
        c1 = M.load_json(m / "c_result.json")
        assert c1["day_no"] == 35 and c1["n21"] is False
        assert M.cmd_c(d / "fifa.db", m, day + timedelta(days=1), log=logs.append) == 0
        assert M.load_json(m / "c_result.json") == c1, "C 를 두 번 셌다"


def test_status_todo():
    with env() as (d, conn):
        m = d / "m"
        out = []
        M.cmd_status(m, D0, log=out.append)
        assert "D0 없음" in out[0]
        M.start(m, D0)

        def todo(k):
            out.clear()
            M.cmd_status(m, D0 + timedelta(days=k), log=out.append)
            return next(x for x in out if x.startswith("오늘 할 일")).split(": ", 1)[1].split(" · ")[0].split()
        assert todo(0) == ["--backfill", "--b-freeze"]
        assert todo(21) == ["--backfill", "--b-freeze", "--a"]
        assert todo(28) == ["--backfill", "--a"]
        assert todo(35) == ["--backfill", "--a", "--b", "--c"]
        assert todo(36) == ["--a", "--b", "--c"]
        M.save_json(m / "a_result.json", {"attempts": [{}], "final": "통과"})
        M.save_json(m / "b_result.json", {"verdict": "통과"})
        assert todo(36) == ["--c"]
        assert any(x.startswith("A: 통과") for x in out) and any(x.startswith("B: 통과") for x in out)


def test_main_dispatch():
    with env() as (d, conn):
        saved = (M.MEASURE_DIR, config.DB_PATH, M.cmd_a, M.cmd_b, M.cmd_c, M.cmd_status, M.selftest, M.backfill)
        calls = []
        M.MEASURE_DIR, config.DB_PATH = d / "m", d / "fifa.db"
        M.cmd_a = lambda *a, **k: calls.append("a") or 0
        M.cmd_b = lambda *a, retry=None, **k: calls.append(("b", retry)) or 0
        M.cmd_c = lambda *a, **k: calls.append("c") or 0
        M.cmd_status = lambda *a, **k: calls.append("status") or 0
        M.selftest = lambda delta=None, **k: calls.append(("self", delta)) or 0
        M.backfill = lambda *a, **k: calls.append(("fill", a[3])) or 0
        try:
            assert M.main(["--status"]) == 0 and calls[-1] == "status"
            assert M.main(["--selftest"]) == 0 and calls[-1] == ("self", None), "D0 없이 실제 Δ 를 넘겼다"
            assert M.main(["--backfill", "--cap", "77"]) == 0 and calls[-1] == ("fill", 77)
            for argv in (["--a"], ["--b"], ["--c"], ["--count"], ["--b-freeze"]):
                n = len(calls)
                assert M.main(argv) == 2 and len(calls) == n, f"D0 없이 {argv} 가 돌았다"
            assert M.main(["--start"]) == 0 and M.main(["--start"]) == 2
            assert M.main(["--a"]) == 0 and calls[-1] == "a"
            assert M.main(["--b"]) == 0 and calls[-1] == ("b", None)
            assert M.main(["--b-retry", "R2"]) == 0 and calls[-1] == ("b", "R2")
            assert M.main(["--c"]) == 0 and calls[-1] == "c"
            assert M.main(["--selftest"]) == 0 and calls[-1][0] == "self" and calls[-1][1] == [], "D0 뒤엔 실제 Δ 를 넘긴다"
            assert M.main(["--count"]) == 0
            meta = M.load_json(d / "m" / "meta.json")
            meta["d0"] = (date.today() - timedelta(days=28)).isoformat()
            M.save_json(d / "m" / "meta.json", meta)
            assert M.main(["--b-freeze"]) == 2, "D0+28 에 얼렸다"
        finally:
            M.MEASURE_DIR, config.DB_PATH, M.cmd_a, M.cmd_b, M.cmd_c, M.cmd_status, M.selftest, M.backfill = saved


# ── 백필 ─────────────────────────────────────────────────────────────────────

class _FakeRun:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def __call__(self, db, today, cap, **kw):
        self.calls.append(kw)
        return self.results.pop(0)


def _patch_toggle(on: str, web=True):
    saved = (config.read_card_backfill, config.read_env_switches, config.CARD_BACKFILL_ON, config.WEB_DATA)

    def rcb():
        config.CARD_BACKFILL_ON = on
        return False
    config.read_card_backfill = rcb
    config.read_env_switches = lambda: (web, False)
    config.WEB_DATA = web
    return saved


def _unpatch(saved):
    config.read_card_backfill, config.read_env_switches, config.CARD_BACKFILL_ON, config.WEB_DATA = saved


def test_backfill_refuses_and_runs():
    with env() as (d, conn):
        m = d / "m"
        fake = _FakeRun([])
        assert M.backfill(d / "fifa.db", m, D0, 600, run=fake, log=lambda *_: None) == 2, "D0 없이 돌았다"
        M.start(m, D0)
        saved = _patch_toggle("2026-11-01T10:00:00")
        try:
            assert M.backfill(d / "fifa.db", m, D0, 600, run=fake, log=lambda *_: None) == 2, "앱 토글이 켜졌는데 돌았다"
        finally:
            _unpatch(saved)
        saved = _patch_toggle("")
        try:
            assert M.backfill(d / "fifa.db", m, D0 + timedelta(days=36), 600, run=fake, log=lambda *_: None) == 2, "D0+36 에 돌았다"
            assert fake.calls == []
            fake = _FakeRun([cardcollect.RunResult(requests=40, skipped_cap=10), cardcollect.RunResult(requests=40, skipped_cap=3),
                             cardcollect.RunResult(requests=0, skipped_cap=3)])
            assert M.backfill(d / "fifa.db", m, D0 + timedelta(days=2), 600, run=fake, log=lambda *_: None) == 0
            assert len(fake.calls) == 3, "틱 상한 뒤 이어 돌지 않았다"
            assert all(c["kind"] == store.BUDGET_CARD_DEV and isinstance(c["window"], list) for c in fake.calls)
            line = json.loads((m / "daily.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            assert line["requests"] == 80 and line["end"] == "하루 상한" and line["day_no"] == 2, line
            fake = _FakeRun([cardcollect.RunResult(requests=5, blocked=True, skipped_cap=9)])
            M.backfill(d / "fifa.db", m, D0 + timedelta(days=2), 600, run=fake, log=lambda *_: None)
            assert len(fake.calls) == 1, "막혔는데 이어 돌았다"
        finally:
            _unpatch(saved)


def test_app_toggle_unreadable_counts_as_on():
    saved = config.read_card_backfill
    config.read_card_backfill = lambda: None
    try:
        assert M.app_toggle_on()
    finally:
        config.read_card_backfill = saved


def test_cmd_a_schedule():
    with env() as (d, conn):
        m = d / "m"
        M.start(m, D0)
        logs = []
        assert M.cmd_a(d / "fifa.db", m, D0 + timedelta(days=20), log=logs.append) == 2, "D0+20 에 A 를 쟀다"
        saved = (M.selftest, M.judge_a)
        verdicts = iter(["재료 부족", "재료 부족", "재료 부족"])
        M.selftest = lambda *a, **k: 0
        M.judge_a = lambda *a, **k: {"verdict": next(verdicts), "n": 0}
        try:
            assert M.cmd_a(d / "fifa.db", m, D0 + timedelta(days=21), log=logs.append) == 0
            assert M.cmd_a(d / "fifa.db", m, D0 + timedelta(days=22), log=logs.append) == 2, "둘째 번을 D0+28 전에"
            assert M.cmd_a(d / "fifa.db", m, D0 + timedelta(days=28), log=logs.append) == 0
            assert M.cmd_a(d / "fifa.db", m, D0 + timedelta(days=35), log=logs.append) == 0
            res = M.load_json(m / "a_result.json")
            assert [a["day_no"] for a in res["attempts"]] == [21, 28, 35]
            assert res["final"].startswith("미달"), res["final"]
            M.selftest = lambda *a, **k: 1
        finally:
            M.selftest, M.judge_a = saved


def test_cmd_a_selftest_fail_blocks_judge():
    with env() as (d, conn):
        m = d / "m"
        M.start(m, D0)
        saved = (M.selftest, M.judge_a)
        called = []
        M.selftest = lambda *a, **k: 1
        M.judge_a = lambda *a, **k: called.append(1) or {"verdict": "통과", "n": 0}
        try:
            assert M.cmd_a(d / "fifa.db", m, D0 + timedelta(days=21), log=lambda *_: None) == 1
            assert not called and M.load_json(m / "a_result.json") is None
        finally:
            M.selftest, M.judge_a = saved


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


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
