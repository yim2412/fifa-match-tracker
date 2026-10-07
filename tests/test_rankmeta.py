"""랭커 메타 ② 스냅숏 읽기(rankmeta.py · 2.4.1) 테스트 — 지어낸 rank.db 로, 네트워크 없이.

pytest 없이 `python tests/test_rankmeta.py`. 구단주·닉네임·프로필 번호는 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import predict
import rankcollect as rc
import rankmeta as M
from seasons import Season

T0 = datetime(2026, 10, 1, 9, 0, 0)


class DB:
    """지어낸 rank.db — snap(...) 로 스냅숏을 하나씩 더한다."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="rankmeta_"))
        self.path = self.dir / "rank.db"
        self.conn = rc.open_rank_db(self.path)
        self.sid = 0
        M.clear_cache()

    def snap(self, at: datetime, rows: list[dict], *, ref: datetime | None = None, seq: int = 1,
             cuts: dict | None = None) -> int:
        """rows: [{sn, rank, nick, elo, w, d, l, color, form, grade, value}] — 빠진 칸은 기본값."""
        self.sid += 1
        sid = self.sid
        with self.conn:
            self.conn.execute("INSERT INTO snapshots (id, taken_at, row_count, dup_count, top_elo, season_seq, new_season,"
                              " ended_season) VALUES (?, ?, ?, 0, NULL, ?, 0, 90)",
                              (sid, at.isoformat(timespec="seconds"), len(rows), seq))
            self.conn.executemany(
                "INSERT INTO snapshot_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(sid, r["rank"], r["sn"], r.get("nick", f"n{r['sn']}"), 1, r.get("value", 1_000_000_000),
                  r.get("elo", 3000.0), r.get("w", 0), r.get("d", 0), r.get("l", 0), r.get("color", "A"), 11,
                  r.get("form", "4-2-3-1"), r.get("grade", 2), 2, 2) for r in rows])
            self.conn.execute("INSERT INTO snapshot_meta (snapshot_id, ref_time) VALUES (?, ?)",
                              (sid, ref.isoformat(timespec="seconds") if ref else None))
            self.conn.executemany("INSERT INTO cut_elo VALUES (?,?,?)", [(sid, k, v) for k, v in (cuts or {}).items()])
        return sid

    def ro(self):
        return rc.open_rank_db_ro(self.path)

    def close(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def using(fn):
    db = DB()
    try:
        fn(db)
    finally:
        db.close()


def person(sn, rank, **kw):
    return {"sn": sn, "rank": rank, **kw}


# ── 공통 ─────────────────────────────────────────────────────────────────

def test_latest_pair_gap_and_ref_time():
    def body(db):
        db.snap(T0, [person(1, 1)])
        db.snap(T0 + timedelta(hours=37), [person(1, 1)])
        p = M.latest_pair(db.conn)
        assert p is not None and not p.ok and round(p.hours) == 37, p
        assert "37시간" in M.pair_text(p)
        # 수집 시각은 40시간 떨어졌지만 넥슨 기준 시각은 24시간 — 기준 시각으로 잰다
        db.snap(T0 + timedelta(hours=77), [person(1, 1)], ref=T0 + timedelta(hours=61))
        p = M.latest_pair(db.conn)
        assert p.ok and round(p.hours) == 24, p
    using(body)


def test_latest_pair_only_current_season():
    def body(db):
        db.snap(T0, [person(1, 1)], seq=1)
        db.snap(T0 + timedelta(hours=24), [person(1, 1)], seq=2)
        assert M.latest_pair(db.conn) is None
        assert "둘 이상" in M.pair_text(None)
    using(body)


def test_pair_without_raw_rows_is_skipped():
    def body(db):
        db.snap(T0, [person(1, 1)])
        db.snap(T0 + timedelta(hours=24), [person(1, 1)])
        db.snap(T0 + timedelta(hours=48), [person(1, 1)])
        with db.conn:
            db.conn.execute("DELETE FROM snapshot_rows WHERE snapshot_id = 3")   # 원본이 지워진 스냅숏(집계만 남음)
        p = M.latest_pair(db.conn)
        assert (p.a["id"], p.b["id"]) == (1, 2), p
    using(body)


def test_formation_group():
    g = M.formation_group
    assert g("4-1-2-3", "raw") == "4-1-2-3" and g("-", "raw") == "" and g("", "back") == ""
    assert g("4-1-2-3", "back") == "4백" and g("4-1-2-3", "front") == "앞줄 3명"
    assert g("5-2-1-2", "back") == "5백" and g("4-2-4", "front") == "앞줄 4명"
    assert g("1-1-4-1-3", "back") == "" and g("4-x-3", "front") == "" and g("-", "front") == ""
    assert M.blank_label("formation", "back") == M.BLANK_GROUP and M.blank_label("formation") == M.BLANK_FORMATION


def test_group_meta_regroups_and_recomputes():
    t = rc.MetaTrend("2026-10-08T09:00:00", "2026-10-01T09:00:00", 7.0, {
        "team_color": [rc.MetaRow("A", 10, 100.0, 10, 100.0, 0.0, False)],
        "formation": [rc.MetaRow("4-2-3-1", 3, 30.0, 1, 10.0, 20.0, True),
                      rc.MetaRow("4-1-2-3", 3, 30.0, 1, 10.0, 20.0, True),
                      rc.MetaRow("5-2-1-2", 4, 40.0, 8, 80.0, -40.0, True)]}, False)
    g = M.group_meta(t, "back")
    rows = {r.key: r for r in g.rows["formation"]}
    assert set(rows) == {"4백", "5백"}, rows
    assert rows["4백"].now_n == 6 and rows["4백"].then_n == 2 and abs(rows["4백"].now_pct - 60) < 1e-9
    assert abs(rows["4백"].diff_pp - 40) < 1e-9
    assert not rows["4백"].thin and not rows["5백"].thin   # 묶은 인원으로 다시(각 줄은 흐림이었다)
    assert g.rows["team_color"] is t.rows["team_color"]
    assert M.group_meta(t, "raw") is t


def test_is_champ_one_place():
    assert rc.is_champ(0) and rc.is_champ(1) and not rc.is_champ(2) and not rc.is_champ(None)
    src = Path(rc.__file__).read_text(encoding="utf-8")
    assert '"grade"] <= 1' not in src and "is_champ(r[\"grade\"])" in src


# ── B2 하루 승률 ─────────────────────────────────────────────────────────

def _day_world(db):
    a = [person(1, 1, w=10, l=10, color="A"), person(2, 2, w=10, l=10, color="A"),
         person(3, 150, w=10, l=10, color="B"),
         person(4, 300, w=10, l=10, color="B"),          # 앞 순위 300 → 상위 200 밖(뒤에서 150 이어도)
         person(5, 5, w=10, l=10, color="A"),            # 그 사이 팀컬러를 바꿈 → 팀컬러 표에서 뺌
         person(6, 6, w=10, l=10, color="A"),            # 승이 줄었음(넥슨 보정) → 뺌
         person(7, 7, w=10, l=10, color="C")]            # 1만 위 밖으로 빠짐
    b = [person(1, 1, w=16, d=1, l=14, color="A"), person(2, 3, w=12, l=18, color="A"),
         person(3, 140, w=13, l=12, color="B"), person(4, 150, w=40, l=10, color="B"),
         person(5, 4, w=15, l=10, color="B"), person(6, 8, w=8, l=11, color="A")]
    db.snap(T0, a)
    db.snap(T0 + timedelta(hours=24), b)


def test_day_winrate_rules():
    def body(db):
        _day_world(db)
        r = M.day_winrate(db.conn, top=200)
        assert r.ready
        assert r.users == 4 and r.negative == 1, (r.users, r.negative)   # 1·2·3·5 (4 는 앞 순위로 밖 · 6 음수 · 7 빠짐)
        assert (r.win, r.draw, r.lose) == (6 + 2 + 3 + 5, 1, 4 + 8 + 2 + 0)
        assert r.changed["team_color"] == 1 and r.changed["formation"] == 0
        colors = {x.key: x for x in r.tables["team_color"]}
        assert set(colors) == {"A", "B"} and colors["A"].users == 2 and colors["B"].users == 1, colors
        assert colors["A"].win == 8 and colors["A"].lose == 12
        assert abs(colors["A"].rate - 8 * 100 / 20) < 1e-9
        assert colors["A"].thin and colors["A"].no_diff        # 2명 · 21판 — 구간이 넓다
        # 전체(구간 무관 · 남은 사람): 1·2·3·4·5 — 6 은 음수
        assert abs(r.all_rate - (6 + 2 + 3 + 30 + 5) * 100 / (46 + 4 + 8 + 2 + 0 + 0)) < 1e-9, r.all_rate
        assert sum(x.users for x in r.tables["formation"]) == 4
    using(body)


def test_day_winrate_not_ready_when_gap_too_long():
    def body(db):
        db.snap(T0, [person(1, 1, w=1)])
        db.snap(T0 + timedelta(hours=40), [person(1, 1, w=3)])
        r = M.day_winrate(db.conn)
        assert not r.ready and r.tables == {}
    using(body)


def test_wilson():
    lo, hi = M.wilson(150, 300)
    assert abs((hi - lo) / 2 - 0.0560) < 0.002, (lo, hi)    # R6: 300판 ±5.6%p
    assert M.wilson(0, 0) == (0.0, 1.0)


# ── B3 가성비 ────────────────────────────────────────────────────────────

def _team(sn0, color, n, elo, w, lose, value, champs):
    return [person(sn0 + i, sn0 + i, color=color, elo=elo, w=w, l=lose, value=value,
                   grade=1 if i < champs else 3) for i in range(n)]


def test_value_score_percentiles_by_hand():
    def body(db):
        rows = (_team(1000, "P", 20, 4000.0, 60, 40, 5_000_000_000, 20)      # ELO 1등 · 승률 1등 · 가치 비쌈 5등
                + _team(2000, "Q", 20, 3900.0, 55, 45, 4_000_000_000, 20)
                + _team(3000, "R", 20, 3800.0, 50, 50, 3_000_000_000, 20)
                + _team(4000, "S", 20, 3700.0, 45, 55, 2_000_000_000, 20)
                + _team(5000, "U", 20, 3600.0, 40, 60, 1_000_000_000, 20)    # 가치 가장 쌈
                + _team(6000, "V", 19, 4500.0, 90, 10, 1, 19))               # 19명 — 점수 없음
        db.snap(T0, rows)
        s = M.value_score(db.conn)
        assert s.scored and s.eligible == 5
        by = {r.key: r for r in s.rows}
        # 챔스 비율이 모두 100% → 그 몫은 모두 50 백분위(가르지 않는다)
        assert all(by[k].pct["champ"] == 50.0 for k in "PQRSU")
        # P: ELO 100 · 승률 100 · 가치 0(가장 비쌈) → 300 + 200 + 0 + 100 = 600
        assert abs(by["P"].score - 600) < 1e-9, by["P"].score
        # U: ELO 0 · 승률 0 · 가치 100 → 0 + 0 + 300 + 100 = 400 · R(가운데): 150 + 100 + 150 + 100 = 500
        assert abs(by["U"].score - 400) < 1e-9 and abs(by["R"].score - 500) < 1e-9
        assert by["V"].score is None and s.rows[-1].key == "V"
        assert sum(config.B3_WEIGHTS.values()) == 1000
    using(body)


def test_value_score_too_few_teams():
    def body(db):
        db.snap(T0, _team(1000, "P", 20, 4000.0, 6, 4, 5, 20) + _team(2000, "Q", 20, 3900.0, 5, 5, 4, 1))
        s = M.value_score(db.conn)
        assert not s.scored and all(r.score is None for r in s.rows)
    using(body)


def test_percentiles_ties():
    p = M._percentiles({"a": 1, "b": 1, "c": 3})
    assert p["a"] == p["b"] == 25.0 and p["c"] == 100.0
    assert M._percentiles({"a": 5, "b": 1}, lower_better=True)["b"] == 100.0


# ── B4 구단가치 · B6 구간 평균 ────────────────────────────────────────────

def test_value_bins():
    def body(db):
        rng = random.Random(3)
        rows = []
        for i in range(800):
            color = "X" if i % 2 else ("Y" if i % 4 == 0 else "Z")
            rows.append(person(i + 1, i + 1, value=int(rng.uniform(1e9, 4e10)), elo=4000 - i, color=color,
                               grade=1 if i < 300 else 3, w=10, l=10))
        db.snap(T0, rows)
        b = M.value_bins(db.conn)
        assert len(b.bins) == 8 and sum(x.users for x in b.bins) == 800
        assert all(x.hi == M.round2(x.hi) for x in b.bins[:-1])
        assert b.bins[0].lo is None and b.bins[-1].hi is None
        assert {c for c, _n, _cells in b.cross} == {"X", "Y", "Z"}
        for _c, n, cells in b.cross:
            assert abs(sum(c.share for c in cells if c) - 100) < 1e-6 or any(c is None for c in cells)
        assert all(len(best) <= 3 for best in b.best)
        assert M.round2(2_740_000_000) == 2_700_000_000 and M.round2(29720) == 30000
    using(body)


def test_value_bins_cells_and_best():
    """칸 인원 5 미만은 빈칸 · 팀컬러 안 씀("")은 구간 상위에 안 나옴 · 구간 값이 채워짐(변이 생존 3줄)."""
    def body(db):
        rows = []
        for i in range(400):
            rows.append(person(i + 1, i + 1, value=(i + 1) * 100_000_000, elo=3000.0 + i, color="X", w=10, l=10))
        for i in range(20):                       # W 20명 — 맨 위 구간에 16 · 맨 아래에 4(빈칸)
            rows.append(person(1000 + i, 1000 + i, value=(390 + i) * 100_000_000 if i < 16 else 100_000_000 + i,
                               elo=2000.0, color="W"))
        for i in range(10):                       # 팀컬러 안 씀 — ELO 가 가장 높아도 상위 목록엔 없다
            rows.append(person(2000 + i, 2000 + i, value=(395 + i) * 100_000_000, elo=9999.0, color=""))
        db.snap(T0, rows)
        b = M.value_bins(db.conn)
        assert all(x.elo is not None and x.users for x in b.bins), b.bins
        cross = {c: cells for c, _n, cells in b.cross}
        assert cross["W"][0] is None and cross["W"][-1] is not None and cross["W"][-1].users == 16, cross["W"]
        assert all(c != "" for best in b.best for c, _e, _n in best)
        assert b.best[-1][0][0] == "X"
    using(body)


def test_winrate_peers_band():
    steps = sorted([(60.0, 3500.0 + (i % 50), 10.0) for i in range(400)]
                   + [(60.0, 4500.0, -50.0) for _ in range(400)])
    res = M.WinrateElo(pairs=3, steps=steps)
    p = M.winrate_peers(res, 60.0, 3520.0)
    assert p.band == 100 and p.median == 10.0 and p.n == 400, p
    p = M.winrate_peers(res, 60.0, None)
    assert p.band is None and p.n == 800
    assert M.winrate_peers(res, 20.0, 3500.0) is None


def test_tier_means_tier_cut_and_grouping():
    def body(db):
        db.snap(T0, [person(1, 1, elo=4000.0, form="4-2-3-1"), person(2, 2, elo=3800.0, form="4-1-2-3"),
                     person(3, 300, elo=3000.0, form="5-3-2")])
        m = M.tier_means(db.conn, top=200, how="back")
        f = {x.key: x for x in m.tables["formation"]}
        assert set(f) == {"4백"} and f["4백"].users == 2 and f["4백"].elo == 3900.0
    using(body)


# ── N10 승률 → 하루 점수 ──────────────────────────────────────────────────

def test_winrate_to_elo_uses_taken_at_gap():
    """수집 시각 간격 29시간 · 데이터 시각 간격 26시간 → N10 은 빼고(예측과 같이) B2 는 쓴다(2회차 [중])."""
    def body(db):
        rows = [person(i, i, w=0, l=0) for i in range(1, 6)]
        db.snap(T0, rows, ref=T0)
        db.snap(T0 + timedelta(hours=29), [person(i, i, w=10, l=10) for i in range(1, 6)],
                ref=T0 + timedelta(hours=26))
        assert M.winrate_to_elo(db.conn).pairs == 0
        assert M.day_winrate(db.conn).ready
    using(body)


def _wr_world(db, days=4, people=400, seed=5):
    rng = random.Random(seed)
    elo = {i: 3500.0 + rng.uniform(-300, 300) for i in range(people)}
    w = {i: 0 for i in range(people)}
    lose = {i: 0 for i in range(people)}
    for d in range(days):
        order = sorted(elo, key=lambda i: -elo[i])
        db.snap(T0 + timedelta(hours=24 * d), [person(i, r + 1, elo=elo[i], w=w[i], l=lose[i])
                                                for r, i in enumerate(order)])
        for i in elo:
            g = rng.choice((0, 5, 20, 30))
            k = sum(1 for _ in range(g) if rng.random() < 0.3 + 0.4 * (i % 10) / 9)
            w[i] += k
            lose[i] += g - k
            elo[i] += (k - (g - k)) * 10          # 이기면 +10 · 지면 −10 → 손익분기 50%


def test_winrate_to_elo_bins_and_breakeven():
    def body(db):
        _wr_world(db)
        r = M.winrate_to_elo(db.conn)
        assert r.ready and r.pairs == 3
        assert r.few > 0                                   # 5판 걸음은 뺐다
        assert all(s[0] >= 0 for s in r.steps)
        assert r.breakeven is not None and 45 <= r.breakeven <= 55, r.breakeven
        assert len(r.bins) == len(config.N10_BINS) + 1
        low, high = r.bins[0], r.bins[-1]
        assert low.median < 0 < high.median
        p = M.winrate_peers(r, 70.0, 3500.0)
        assert p is not None and p.median > 0 and p.band is None   # 걸음이 300 미만 → 점수대 없이
    using(body)


def test_winrate_to_elo_collecting_and_negative():
    def body(db):
        db.snap(T0, [person(1, 1, w=50, l=50), person(2, 2, w=0, l=0)])
        db.snap(T0 + timedelta(hours=24), [person(1, 1, w=40, l=70), person(2, 2, w=10, l=10)])
        r = M.winrate_to_elo(db.conn)
        assert not r.ready and r.negative == 1 and len(r.steps) == 1
    using(body)


def test_winrate_to_elo_tier_by_front_rank():
    def body(db):
        db.snap(T0, [person(1, 300, w=0, l=0), person(2, 10, w=0, l=0)])
        db.snap(T0 + timedelta(hours=24), [person(1, 5, w=20, l=0, elo=3500.0), person(2, 20, w=10, l=10)])
        r = M.winrate_to_elo(db.conn, top=200)
        assert len(r.steps) == 1 and r.steps[0][0] == 50.0     # 뒤에서만 상위 200 인 1 은 안 셈
    using(body)


def test_my_day_rate():
    from models import MatchSummary

    def m(h, res):
        return MatchSummary("x", T0 + timedelta(hours=h), 52, "me", "o", res, 0, 0, 50, 0, 0, 0, 0, 0.0)
    ms = [m(30, "승")] * 7 + [m(29, "패")] * 3 + [m(28, "무")] * 5 + [m(1, "승")] * 50
    rate, n = M.my_day_rate(ms)
    assert n == 10 and rate == 70.0
    assert M.my_day_rate([m(30, "승")] * 9) is None


# ── N8·N9 ────────────────────────────────────────────────────────────────

def test_judge_order():
    j = M.judge
    a = M.Row(1, 100, "n", 1, 3000.0, 10, 0, 10, "A", "4-2-3-1", 2)
    assert j(a, a, 40)[0] == M.V_GAP
    assert j(a, None, 24)[0] == M.V_OUT and j(None, a, 24)[0] == M.V_OUT
    assert j(a, a._replace(win=12, color="B"), 24)[0] == M.V_GAME_SQUAD
    assert j(a, a._replace(win=12, value=999), 24)[0] == M.V_GAME          # 구단가치는 안 본다
    assert j(a, a._replace(formation="4-1-2-3"), 24)[0] == M.V_SQUAD
    assert j(a, a._replace(rank=300, elo=2970.0), 24)[0] == M.V_ELO       # 0판에 둘 다 바뀜 → 점수만 바뀜
    assert j(a, a._replace(rank=300), 24)[0] == M.V_PUSHED
    assert j(a, a._replace(grade=1), 24)[0] == M.V_SAME                    # 등급은 안 본다
    assert j(a, a._replace(win=5), 24)[0] == M.V_ODD


def test_rank_moves_middle_missing_and_history():
    def body(db):
        others = [person(9, 1)]
        db.snap(T0, others + [person(7, 50, nick="old", form="4-2-3-1")])
        db.snap(T0 + timedelta(hours=24), others)                         # 가운데에서 빠짐
        db.snap(T0 + timedelta(hours=48), others + [person(7, 60, nick="new", form="4-1-2-3", w=5)])
        r = M.rank_moves(db.conn, 7)
        assert r.found
        assert [m.verdict for m in r.moves] == [M.V_OUT, M.V_OUT], [m.verdict for m in r.moves]
        assert [h.row is None for h in r.history] == [False, True, False]
        assert r.history[2].changed == {"nickname", "formation"}
        assert not M.rank_moves(db.conn, 12345).found
        assert M.rank_moves(db.conn, None).moves == []
    using(body)


def test_find_sn():
    def body(db):
        db.snap(T0, [person(5, 1, nick="old5")])
        db.snap(T0 + timedelta(hours=24), [person(5, 1, nick="new5")])
        assert M.find_sn(db.conn, 77, "아무개") == 77
        assert M.find_sn(db.conn, None, "new5") == 5
        assert M.find_sn(db.conn, None, "old5") is None              # 마지막 스냅숏에서만
        assert M.find_sn(db.conn, None, None) is None
    using(body)


def test_ranked_nicknames():
    def body(db):
        db.snap(T0, [person(1, 1, nick="a")], seq=1)
        db.snap(T0 + timedelta(hours=24), [person(2, 1, nick="b")], seq=2)
        db.snap(T0 + timedelta(hours=48), [person(3, 1, nick="c")], seq=2)
        assert M.ranked_nicknames(db.conn) == {"b", "c"}             # 지난 시즌 것은 안 본다
    using(body)


# ── N13 주간 요약 ─────────────────────────────────────────────────────────

def test_weekly():
    def body(db):
        cuts0 = {200: 4400.0, 1000: 4000.0, 10000: 3000.0}
        cuts1 = {200: 4450.0, 1000: 4020.0, 10000: 3010.0}
        db.snap(T0, [person(1, 10, elo=4000.0, w=10), person(2, 20, elo=3900.0), person(3, 30, elo=3800.0)], cuts=cuts0)
        for d in range(1, 7):
            db.snap(T0 + timedelta(days=d), [person(1, 10, elo=4000.0, w=10), person(2, 20, elo=3900.0)], cuts=cuts0)
        last = [person(1, 5, elo=4100.0, w=10, color="A", value=100),     # 0판 · 200위 안 → 주차
                person(2, 15, elo=3950.0, w=7, l=0, color="A", value=50),  # 하루 7승
                person(4, 25, elo=3990.0, color="A", value=70),            # 7일 전 없음 → 급상승에 없음
                person(5, 26, color="A", value=60), person(6, 27, color="A", value=80)]
        db.snap(T0 + timedelta(days=7), last, cuts=cuts1)
        w = M.weekly(db.conn, my_sn=2, end_days=3)
        assert round(w.cut_days) == 7 and w.cuts[0] == (200, 4400.0, 4450.0)
        assert [r[0] for r in w.risers] == ["n1", "n2"], w.risers
        assert w.risers[0][1:] == (10, 5, 100.0)
        assert w.day_wins[0][0] == "n2" and w.day_wins[0][2] == 7
        assert w.park_state == "shown" and [p[0] for p in w.parked] == ["n1"]
        assert w.extremes[0][0] == "A" and w.extremes[0][2][0] == "n1" and w.extremes[0][3][0] == "n2"
        assert w.my_value == (5, 5)
        assert M.weekly(db.conn, None, 30).park_state == "far"
        assert M.weekly(db.conn, None, None).park_state == "unknown"
    using(body)


def test_weekly_short_history():
    def body(db):
        db.snap(T0, [person(1, 1)])
        db.snap(T0 + timedelta(days=1), [person(1, 1, w=3)])
        w = M.weekly(db.conn, None, None)
        assert w.risers == [] and w.rise_at is None and w.day_wins      # 급상승만 모으는 중
    using(body)


def test_season_end_estimate_shared():
    seasons = [Season(90, "시즌 9", date(2026, 7, 9), date(2026, 9, 10)),
               Season(89, "시즌 8", date(2026, 5, 7), date(2026, 7, 9))]
    e = predict.season_end_estimate(seasons, None, date(2026, 10, 20))
    assert e.start == date(2026, 9, 10) and e.guess.source == "estimate" and e.days_left() == 23, e
    e = predict.season_end_estimate(seasons, (date(2026, 10, 25), date(2026, 9, 10)), date(2026, 10, 20))
    assert e.guess.source == "notice" and e.days_left() == 5
    assert predict.season_end_estimate([], None, date(2026, 10, 20)).days_left() is None


# ── 캐시 · 예산 ──────────────────────────────────────────────────────────

def test_cache_follows_new_snapshot():
    def body(db):
        db.snap(T0, [person(1, 1, w=0, l=0)])
        db.snap(T0 + timedelta(hours=24), [person(1, 1, w=2, l=0)])
        a = M.day_winrate(db.conn)
        assert M.day_winrate(db.conn) is a                               # 같은 원본 → 캐시
        db.snap(T0 + timedelta(hours=48), [person(1, 1, w=2, l=2)])
        b = M.day_winrate(db.conn)
        assert b is not a and b.win == 0 and b.lose == 2
    using(body)


def make_big(db, people=10000, days=14, seed=11):
    rng = random.Random(seed)
    colors = [f"C{i}" for i in range(60)] + [""]
    forms = ["4-2-3-1", "4-1-2-3", "4-4-2", "5-2-1-2", "-"]
    st = {i: [3000.0 + rng.random() * 1600, 0, 0, 0, rng.choice(colors), rng.choice(forms),
              int(rng.uniform(3e9, 3e11)), rng.choice((0, 1, 2, 3))] for i in range(people)}
    for d in range(days):
        order = sorted(st, key=lambda i: -st[i][0])
        h = 24 + (5 if d % 5 == 4 else 0)                                 # 간격 섞음
        db.snap(T0 + timedelta(hours=h * d), [
            person(i, r + 1, elo=st[i][0], w=st[i][1], d=st[i][2], l=st[i][3], color=st[i][4], form=st[i][5],
                   value=st[i][6], grade=st[i][7], nick=f"p{i}") for r, i in enumerate(order)],
            cuts={200: 4400.0, 1000: 4100.0, 10000: 3000.0})
        for i, s in st.items():
            g = rng.choice((0, 10, 30, 60))
            k = rng.randint(0, g)
            s[1] += k
            s[3] += g - k
            s[0] += (2 * k - g) * 4.0
            s[6] = int(s[6] * rng.uniform(0.98, 1.02))


def test_budget_first_call_cpu():
    """지어낸 1만 행 × 14스냅숏 — 첫 계산(캐시 없음)이 함수마다 CPU 0.3초 안(⑦ · 넘으면 RankMetaLoader 로)."""
    def body(db):
        make_big(db)
        conn = db.ro()
        try:
            out = {}
            for name, fn in (("day_winrate", lambda: M.day_winrate(conn, 10000)),
                             ("value_score", lambda: M.value_score(conn, 10000)),
                             ("value_bins", lambda: M.value_bins(conn)),
                             ("tier_means", lambda: M.tier_means(conn, 10000)),
                             ("winrate_to_elo", lambda: M.winrate_to_elo(conn, 10000)),
                             ("weekly", lambda: M.weekly(conn, 5, 3)),
                             ("rank_moves", lambda: M.rank_moves(conn, 5)),
                             ("ranked_nicknames", lambda: M.ranked_nicknames(conn))):
                M.clear_cache()
                t = time.process_time()
                fn()
                out[name] = time.process_time() - t
            print("      CPU 초:", " · ".join(f"{k} {v:.3f}" for k, v in out.items()))
            slow = {k: v for k, v in out.items() if v > BUDGET_S.get(k, 0.3)}
            assert not slow, slow
        finally:
            conn.close()
    using(body)


# 함수마다 예외 예산 — 지금은 없다(전부 0.3초 안 · 2026-10-07 이 PC 최대 0.16초라 작업 스레드를 안 둔다)
BUDGET_S: dict = {}


def test_rankmeta_behind_core_api():
    import core_api
    for name in ("day_winrate", "value_score", "value_bins", "tier_means", "formation_group", "group_meta", "is_champ",
                 "winrate_to_elo", "weekly", "rank_moves", "find_sn", "ranked_nicknames", "season_end_estimate"):
        assert hasattr(core_api, name) and name in core_api.__all__, name


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
