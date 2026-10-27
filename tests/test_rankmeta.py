"""랭커 메타 ② 스냅숏 읽기(rankmeta.py · 2.4.1) 테스트 — 지어낸 rank.db 로, 네트워크 없이.

pytest 없이 `python tests/test_rankmeta.py`. 구단주·닉네임·프로필 번호는 전부 지어낸 값이다.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import statistics
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
    assert '"grade"] <= 1' not in src and '"grade"] == 0' not in src
    # 첫 달성(N12 챔스 · 55 슈챔)은 판정 함수를 받아 같은 코드로 돈다 — 챔스 쪽이 is_champ 를 넘기는지
    assert '_first_reach(conn, "champ_watch", "champ_first", is_champ,' in src
    assert rc.is_top20_mark(0) and not rc.is_top20_mark(1) and not rc.is_top20_mark(None)


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


def test_team_color_board_bundles_by_hand():
    """67 R1 — 가성비·하루 승률을 팀컬러 하나로 묶기만 · 빈 이름·"-" 뺌(점유율 분모도) · 겹치는 이름은 "(구분 전)" 키."""
    dup = sorted(config.TEAMCOLOR_DUP_NAMES)[0]

    def body(db):
        def snap(w, dup_lose):
            return (_team(1000, "P", 20, 4000.0, w, 10, 5_000_000_000, 20) + _team(2000, dup, 10, 3900.0, w // 2, dup_lose, 4, 1)
                    + _team(3000, "-", 5, 3000.0, 1, 1, 1, 0) + _team(4000, "", 5, 3000.0, 1, 1, 1, 0))
        db.snap(T0, snap(10, 10))
        db.snap(T0 + timedelta(hours=24), snap(30, 20))         # P: 하루 +20승 0패 · dup: +10승 +10패
        b = M.team_color_board(db.conn)
        assert [r.key for r in b.rows] == ["P", f"{dup} (구분 전)"], [r.key for r in b.rows]
        assert b.total == 30 and [round(r.share, 4) for r in b.rows] == [round(2000 / 30, 4), round(1000 / 30, 4)]
        assert b.day_ready
        p = b.rows[0]
        assert (p.name, p.users, p.day_rate) == ("P", 20, 100.0), p        # 하루 20승 0패 — 승÷(승+패)
        vs = {r.key: r for r in M.value_score(db.conn).rows}
        assert (p.elo, p.rate, p.value, p.champ) == (vs["P"].elo, vs["P"].rate, vs["P"].value, vs["P"].champ)
        assert b.rows[1].name == dup and abs(b.rows[1].day_rate - 50.0) < 1e-9   # 10승 10패
        assert M.team_color_board(db.conn) is b, "같은 rank.db 인데 캐시를 안 썼다"
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
                             ("team_color_board", lambda: M.team_color_board(conn, 10000)),   # 67 R1 — 위 둘을 처음부터
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
    for name in ("day_winrate", "value_score", "team_color_board", "value_bins", "tier_means", "formation_group", "group_meta", "is_champ",
                 "winrate_to_elo", "weekly", "rank_moves", "find_sn", "ranked_nicknames", "season_end_estimate"):
        assert hasattr(core_api, name) and name in core_api.__all__, name


# ── N23 상대 순위 구간(2.9.1) — 구간 이름 · 손으로 센 표 ────────────────────────────────────────────────

def _opp_detail(mid, result, my_div=1000, opp_div=1000):
    return {"matchId": mid, "matchType": 52, "matchDate": "2026-10-10T08:00:00",
            "matchInfo": [{"ouid": "me", "nickname": "나", "division": my_div, "matchDetail": {"matchResult": result}},
                          {"ouid": "x", "nickname": "상대", "division": opp_div, "matchDetail": {"matchResult": "패"}}]}


def test_opp_labels_follow_config_edges():
    assert [M.opp_rank_label(b) for b in config.OPP_RANK_BANDS] == ["1~200위", "201~1,000위", "1,001~3,000위", "3,001~10,000위"]
    labels = [M.opp_gap_label(i) for i in range(len(config.OPP_GAP_EDGES) + 1)]
    assert labels[0].startswith("-300 미만") and labels[-1].startswith("+300 이상") and labels[3] == "-50 ~ +50", labels
    assert len(set(labels)) == len(labels) == 7


def test_opp_rank_table_counts_by_hand():
    # a~e: 순위 구간 200 에 승 2·패 1 · 1000 에 무 1 · 모름 1(승). 격차 칸: a·b 는 칸 6, c 칸 2, d 칸 0, 나머지 모름
    rows = {"a": (200, 6), "b": (200, 6), "c": (200, 2), "d": (1000, 0), "e": (None, None)}
    ds = [_opp_detail("a", "승"), _opp_detail("b", "승"), _opp_detail("c", "패"), _opp_detail("d", "무"), _opp_detail("e", "승"),
          _opp_detail("f", "승")]                     # f 는 줄이 없다 — 분모에도 안 들어간다(아직 안 채움)
    t = M.opp_rank_table(ds, "me", rows)
    assert (t.games, t.known, t.unknown, t.gap_known) == (5, 4, 1, 4), t
    assert [(b.win, b.draw, b.lose) for b in t.by_rank] == [(2, 0, 1), (0, 1, 0), (0, 0, 0), (0, 0, 0)], t.by_rank
    assert [(b.win, b.draw, b.lose) for b in t.by_gap] == [(0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 0, 0), (0, 0, 0), (0, 0, 0),
                                                           (2, 0, 0)], t.by_gap
    assert t.mixed is False and all(b.mixed is False for b in t.by_rank + t.by_gap), "챔스 아래끼리면 무승부 분모 한 집단"


def _a6(fn):
    """A6 섞임 규칙 자체를 잰다 — 앱은 2026-10-19 부터 승÷전체(config.WIN_RATE_DRAWS_IN)라 스위치를 끈 채로."""
    def run():
        keep = config.WIN_RATE_DRAWS_IN
        config.WIN_RATE_DRAWS_IN = False
        try:
            fn()
        finally:
            config.WIN_RATE_DRAWS_IN = keep
    run.__name__ = fn.__name__
    return run


@_a6
def test_opp_rank_table_mixed_is_one_value_for_the_whole_table():
    # 한 판이라도 양쪽 챔스 이상(무승부 불가)이면 섞인 묶음 — 두 표가 같은 식을 쓴다
    rows = {"a": (200, 6), "b": (1000, 3)}
    ds = [_opp_detail("a", "승", my_div=900, opp_div=900), _opp_detail("b", "승")]
    t = M.opp_rank_table(ds, "me", rows)
    assert t.mixed is True and all(b.mixed is True for b in t.by_rank + t.by_gap), t


def test_opp_scores_nearest_snapshot_same_season_not_saved():
    """57 K3 — 가장 가까운 스냅숏(±24시간)의 점수·순위 · 시즌 경계 앞 경기는 새 시즌 스냅숏과 짝짓지 않는다 · 없는 상대·시각 없음은 빠진다."""
    db = DB()
    try:
        assert M.opp_scores(db.conn, [("m", T0, "가")]) == {}, "사람별 처리 전(person_season 없음)인데 짝을 지었다"
        db.snap(datetime(2026, 9, 30, 23, 0), [person(1, 99, nick="가", elo=9999.0)], seq=1)   # 지난 시즌 마지막 스냅숏
        s1 = db.snap(T0, [person(1, 1, nick="가", elo=4100.0), person(2, 5, nick="나", elo=3900.0)])
        db.snap(T0 + timedelta(days=2), [person(1, 3, nick="가", elo=4000.0)])
        rc._set_state(db.conn, person_season=json.dumps({"seq": 1, "start": "2026-10-01", "at": "2026-10-01T09:00:00",
                                                         "via": "init"}))
        before = db.conn.total_changes
        got = M.opp_scores(db.conn, [
            ("m1", T0 + timedelta(hours=1), "가"),                       # 첫 스냅숏 쪽이 가깝다
            ("m2", T0 + timedelta(days=2, hours=-3), "가"),              # 둘째 쪽이 가깝다 — 순위가 바뀐 값
            ("m3", T0 + timedelta(days=4, hours=1), "가"),               # 가장 가까운 것도 24시간 밖
            ("m4", T0, "모르는 상대"),
            ("m5", None, "가"),
            ("m6", datetime(2026, 9, 30, 20, 0), "나"),                  # 시즌 경계(10-01) 앞 — 13시간 거리여도 안 짝짓는다
            ("m7", T0 - timedelta(hours=2), "나"),
            ("m8", datetime(2026, 10, 1, 0, 30), "가"),                  # 지난 시즌 스냅숏이 더 가깝지만 이번 시즌 것과
        ])
        assert db.conn.total_changes == before, "보여 주기만 해야 하는데 rank.db 에 썼다"
        assert set(got) == {"m1", "m2", "m7", "m8"}, got
        assert got["m8"].rank == 1, ("시즌 경계 앞 스냅숏과 짝지었다", got["m8"])
        assert (got["m1"].elo, got["m1"].rank) == (4100.0, 1) and (got["m2"].elo, got["m2"].rank) == (4000.0, 3), got
        assert (got["m7"].rank, got["m1"].at[:10]) == (5, "2026-10-01"), got
        assert s1 == 2
    finally:
        db.close()


def test_bin_of_value_uses_the_same_edges_as_value_bins():
    def body(db):
        rows = [person(i + 1, i + 1, value=(i + 1) * 100_000_000) for i in range(800)]
        db.snap(T0, rows)
        b = M.value_bins(db.conn)
        assert len(b.bins) == 8
        for r in rows:   # 칸 인원 세기(_value_bins)와 칸 번호(bin_of_value)가 같은 경계
            i = M.bin_of_value(b.bins, r["value"])
            lo, hi = b.bins[i].lo, b.bins[i].hi
            assert (lo is None or lo <= r["value"]) and (hi is None or r["value"] < hi), (r["value"], lo, hi)
        assert sum(1 for r in rows if M.bin_of_value(b.bins, r["value"]) == 0) == b.bins[0].users
        assert M.bin_of_value(b.bins, None) is None and M.bin_of_value([], 5) is None
    using(body)


def test_opp_rank_rows_reads_only_my_account():
    db = DB()
    try:
        with db.conn:
            db.conn.executemany("INSERT INTO match_opp_rank VALUES (?, ?, ?, ?, ?)",
                                [("a", "me", 200, 6, "2026-10-10"), ("b", "me", None, None, "2026-10-10"),
                                 ("c", "other", 200, 1, "2026-10-10")])
        assert M.opp_rank_rows(db.conn, "me") == {"a": (200, 6), "b": (None, None)}
    finally:
        db.conn.close()


# ── 55 구단 머리 줄 · 낙폭 구간 ─────────────────────────────────────────────────

def _club_db(db, *, start="2026-09-28", season_at=None, fc_started=None):
    """시즌 시작 start · 사람별 처리 시작 season_at(기본 = 시즌 시작 하루 뒤) · FC 첫 쓰기 fc_started."""
    season_at = season_at or "2026-09-29T09:00:00"
    db.snap(T0, [person(1, 1, value=900), person(2, 2, value=500), person(7, 3, value=700), person(3, 4)],
            cuts={200: 4400.0, 1000: 4100.0})
    rc._set_state(db.conn, person_season=json.dumps({"seq": 1, "start": start, "at": season_at, "via": "init"}))
    if fc_started:
        rc._set_state(db.conn, **{rc.FC_STARTED + season_at: fc_started})
    with db.conn:
        db.conn.executemany("INSERT INTO fc_season VALUES (?, ?, ?, ?, 0, 1, ?, 0)",
                            [(season_at, sn, lo, hi, season_at) for sn, lo, hi in ((1, 300, 400), (2, 100, 900), (7, 50, 60))])
        db.conn.executemany("INSERT INTO elo_season VALUES (?, ?, ?, ?, ?, 0, ?)",
                            [(season_at, sn, pk, "2026-10-01T09:00:00", dd, season_at)
                             for sn, pk, dd in ((1, 4500.0, 300.0), (2, 4450.0, 100.0), (7, 4520.0, 900.0))])
        db.conn.executemany("INSERT INTO champ_first VALUES (?, ?, 0, ?, 0)",
                            [(season_at, g, season_at) for g in (100, 300, 500)])


def test_club_head_others_only_and_my_row_excluded():
    def body(db):
        _club_db(db, fc_started="2026-09-29T09:00:00")
        h = M.club_head(db.conn, 7)
        assert h.cut == 4400.0, h.cut
        assert h.values == [500, 900, 1_000_000_000] and h.my_value == 700, (h.values, h.my_value)  # 내 줄(7) 뺌
        assert h.fc_lo == [100, 300] and h.fc_hi == [400, 900] and h.me_in_fc, (h.fc_lo, h.fc_hi)
        assert (h.peak, h.peak_at) == (4520.0, "2026-10-01T09:00:00") and h.peaks_dd == [(4450.0, 100.0), (4500.0, 300.0)]
        assert (h.champ_n, h.champ_median) == (3, 300) and not h.champ_hidden and not h.fc_hidden, h
        h2 = M.club_head(db.conn, None)               # 번호를 모르면 아무도 안 빼고 내 최고점도 없다
        assert len(h2.values) == 4 and h2.peak is None and not h2.me_in_fc and len(h2.fc_lo) == 3
        assert h.me_known and not h2.me_known, "번호를 모르는 것과 1만 위 밖을 못 가른다"
    using(body)


def test_club_head_hides_late_seasons():
    """U7·U9 — 시즌 시작 N12_START_DAYS 넘어 시작한 기록은 남들 비교를 숨긴다. FC 는 fc_started 로 따로(3회차 [상]1)."""
    def body(db):       # 업그레이드 경로: 2.11.1 이 시즌 시작부터 사람별 처리 · 2.12.1 의 FC 는 원본 14일부터
        _club_db(db, fc_started="2026-10-10T09:00:00")
        h = M.club_head(db.conn, 7)
        assert not h.champ_hidden and h.fc_hidden, ("업그레이드 경로에서 FC 등수가 보인다", h.champ_hidden, h.fc_hidden)
    using(body)

    def body(db):       # 사람별 처리부터 늦게 시작
        _club_db(db, season_at="2026-10-05T16:28:11", fc_started="2026-10-05T16:28:11")
        h = M.club_head(db.conn, 7)
        assert h.champ_hidden and h.fc_hidden
    using(body)

    def body(db):       # 시즌 시작을 모름(via fallback) — 엄밀한 쪽
        _club_db(db, start=None, fc_started="2026-09-29T09:00:00")
        h = M.club_head(db.conn, 7)
        assert h.champ_hidden and h.fc_hidden
    using(body)

    def body(db):       # 경계 — 정확히 N12_START_DAYS 뒤는 보인다
        late = (datetime(2026, 9, 28) + timedelta(days=config.N12_START_DAYS)).isoformat(timespec="seconds")
        _club_db(db, season_at=late, fc_started=late)
        h = M.club_head(db.conn, 7)
        assert not h.champ_hidden and not h.fc_hidden, late
    using(body)


def test_club_head_without_person_processing_and_old_db():
    def body(db):
        db.snap(T0, [person(1, 1, value=900)], cuts={200: 4400.0})
        h = M.club_head(db.conn, None)
        assert h.cut == 4400.0 and h.values == [900] and h.fc_hidden and h.champ_hidden and h.fc_lo == []
        with db.conn:
            db.conn.execute("DROP TABLE fc_season")       # 옛 DB 흉내(사람별 처리 상태만 있고 55 표 없음)
        rc._set_state(db.conn, person_season='{"seq": 1, "start": "2026-09-28", "at": "2026-09-29T09:00:00"}')
        assert M.club_head(db.conn, None) is None
    using(body)


def test_dd_band_window_and_minimum():
    pairs = sorted([(4000.0 + i, float(i)) for i in range(config.R2_BAND_MIN)] + [(4500.0, 999.0)])
    got = M.dd_band(pairs, 4000.0 + config.R2_BAND)          # 창 ±R2_BAND 의 양끝 포함 · 4500 은 밖
    assert got == (statistics.median(range(config.R2_BAND_MIN)), config.R2_BAND_MIN), got
    assert M.dd_band(pairs[1:], 4000.0 + config.R2_BAND) is None, "R2_BAND_MIN 미만인데 냈다"
    top = 4000.0 + config.R2_BAND_MIN - 1
    assert M.dd_band(pairs, top - config.R2_BAND) is not None, "창 위 끝(최고점 + R2_BAND)을 뺐다"
    assert M.dd_band(pairs, 4500.0 + config.R2_BAND + 1) is None and M.dd_band(pairs, None) is None


def test_rank_of_and_fc_rank_range():
    assert M.rank_of([1, 2, 3], 3) == 1 and M.rank_of([1, 2, 3], 2) == 2 and M.rank_of([1, 2, 3], 0) == 4
    h = M.ClubHead(fc_lo=sorted([100, 300]), fc_hi=sorted([400, 900]))
    assert M.fc_rank_range(h, 350) == (1, 3, 3), M.fc_rank_range(h, 350)     # 300 은 확실히 아래 · 400·900 은 위일 수 있다
    assert M.fc_rank_range(h, 1000) == (1, 1, 3)


# ── 57 K7 상위 랭커 카드 · K11 보상판 ─────────────────────────────────────────────

def test_top3_games_per_day_fc_and_rate():
    def body(db):
        db.snap(T0, [person(1, 1, w=10, d=0, l=0), person(2, 2, w=5, d=1, l=4), person(9, 50)])
        db.snap(T0 + timedelta(days=2), [person(1, 2, w=30, d=2, l=8, nick="첫째"), person(2, 1, w=9, d=1, l=10),
                                         person(5, 3, w=6, d=0, l=4), person(9, 4, w=99)])
        rc._set_state(db.conn, person_season=json.dumps({"seq": 1, "start": "2026-09-28", "at": "2026-09-28T09:00:00"}),
                      **{rc.FC_STARTED + "2026-09-28T09:00:00": "2026-09-28T09:00:00"})
        with db.conn:
            db.conn.execute("INSERT INTO fc_season VALUES ('2026-09-28T09:00:00', 1, 450, 600, 0, 1, '2026-09-28T09:00:00', 0)")
        cards = M.top3(db.conn, person=True)
        assert [c.rank for c in cards] == [1, 2, 3] and cards[1].nickname == "첫째", cards
        two, one, three = cards[1], cards[0], cards[2]
        assert two.games_per_day == (40 - 10) / 2, two.games_per_day       # 2일 사이 30판
        assert one.games_per_day == (20 - 10) / 2
        assert three.games_per_day is None, "한 번만 본 사람에게 하루 판수를 냈다"
        days = (T0 + timedelta(days=2) - datetime(2026, 9, 28)).total_seconds() / 86400
        assert abs(two.fc_lo - 450 / days) < 1e-9 and abs(two.fc_hi - 600 / days) < 1e-9, (two.fc_lo, days)
        assert one.fc_lo is None, "fc_season 에 없는 사람에게 FC 를 냈다"
        assert two.win_pct == 30 * 100 / 40, "승률은 승÷전체(앱 규칙)"
        M.clear_cache()
        assert all(c.fc_lo is None for c in M.top3(db.conn, person=False)), "관문 밖인데 사람별 표를 읽었다"
        rc._set_state(db.conn, **{rc.FC_STARTED + "2026-09-28T09:00:00": "2026-10-05T09:00:00"})
        M.clear_cache()
        assert M.top3(db.conn, person=True)[1].fc_lo is None, "늦게 시작한 시즌 FC 를 냈다"
    using(body)


def test_minor_formations_and_colors_use_the_whole_snapshot():
    """57 K8 — 비주류 = 마지막 스냅숏 1만 명 전체에서 MINOR_USERS 미만. 순위 구간(top)을 좁혀도 같은 기준."""
    def body(db):
        n = config.MINOR_USERS
        rows = [person(i, i, form="4-2-3-1", color="큰팀") for i in range(1, n + 1)]
        rows += [person(n + i, n + i, form="5-3-2", color="작은팀") for i in range(1, n)]     # 하나 모자람
        db.snap(T0, rows)
        assert M.minor_formations(db.conn) == frozenset({"5-3-2"}), M.minor_formations(db.conn)
        counts = M.usage_counts(db.conn)["team_color"]
        assert not M.is_minor_color(counts, "큰팀") and M.is_minor_color(counts, "작은팀")
        assert M.is_minor_color(counts, "아무도안씀"), "1만 명 중 0명인 팀컬러는 비주류다"
        assert M.formation_group("5-3-2", "minor", M.minor_formations(db.conn)) == M.MINOR_LABEL
        assert M.formation_group("4-2-3-1", "minor", M.minor_formations(db.conn)) == "4-2-3-1"
        assert M.formation_group("", "minor", frozenset({""})) == "" and M.blank_label("formation", "minor") == M.BLANK_FORMATION
        tm = M.tier_means(db.conn, n + 50, "minor")       # 위 n+50명만 봐도(그 안 5-3-2 는 50명) —  5-3-2 는 비주류로 묶인다(기준은 전체)
        keys = {r.key: r.users for r in tm.tables["formation"]}
        assert keys == {"4-2-3-1": n, M.MINOR_LABEL: 50}, keys
    using(body)


def _reward_db(db, people, *, fc_started="2026-09-28T09:00:00", fcg_started="2026-09-28T09:00:00"):
    """people: [(sn, rank, fc_lo, fc_hi, g_lo, g_hi)] — rank None 은 마지막 스냅숏에 없음."""
    at = "2026-09-28T09:00:00"
    db.snap(T0, [person(sn, rk) for sn, rk, *_ in people if rk is not None])
    st = {"person_season": json.dumps({"seq": 1, "start": "2026-09-28", "at": at})}
    if fc_started:
        st[rc.FC_STARTED + at] = fc_started
    if fcg_started:
        st[rc.FCG_STARTED + at] = fcg_started
    rc._set_state(db.conn, **st)
    with db.conn:
        db.conn.executemany("INSERT INTO fc_season VALUES (?, ?, ?, ?, 0, 1, ?, 0)",
                            [(at, sn, fl, fh, at) for sn, _r, fl, fh, _gl, _gh in people])
        db.conn.executemany("INSERT INTO fc_games VALUES (?, ?, ?, ?, 0, 1, ?, 0)",
                            [(at, sn, gl, gh, at) for sn, _r, _fl, _fh, gl, gh in people])


def test_reward_board_spans_bands_and_min_games():
    n = config.K11_MIN_PER_BAND
    people = [(i, i, 1500, 2000, 100, 200) for i in range(1, n + 1)]           # 1~200위 · 범위 [750, 2000] 가운데 1375
    people += [(500 + i, 500 + i, 3000, 3000, 200, 200) for i in range(n - 1)]   # 201~1,000위 · 한 명 모자람
    people += [(900, None, 1, 1, 99, 500)]                                     # g_lo < 100 — 대상 밖
    people += [(950, 5, 9000, 9000, 500, 500)]                                 # 시즌 중간에 처음 잡힘 — 아래에서 깃발

    def body(db):
        _reward_db(db, people)
        with db.conn:
            db.conn.execute("UPDATE fc_games SET first_seen_late = 1 WHERE profile_sn = 950")   # 한쪽 표만 깃발이어도 뺀다
        b = M.reward_board(db.conn)
        assert b.state == "ok" and b.seen == len(people) and b.late == 1, (b.state, b.seen, b.late)
        assert b.users == len(people) - 2, ("늦게 잡힌 사람(fc_hi 가 시즌 전체 승리)을 넣었다 — 100판당이 부푼다", b.users)
        top = b.by_rank[0]
        assert top.users == n and top.median == (1500 * 100 / 200 + 2000 * 100 / 100) / 2, top
        assert (top.lo, top.hi) == (750.0, 2000.0), (top.lo, top.hi)
        assert b.by_rank[1].users == n - 1 and b.by_rank[1].median is None, "인원 미달 칸에 중앙값을 냈다"
        assert b.by_rank[-1].users == 0, "대상 밖(g_lo<100)을 셌다"
        assert b.overall.users == 2 * n - 1 and b.overall.median is not None
        assert sum(x.users for x in b.by_value) == b.users, "구단가치 칸 합이 대상 수와 다르다"
    using(body)


def test_reward_board_hidden_by_later_of_two_starts():
    """3판 G3 — FC 는 일찍 · 판수는 늦게 시작했으면 숨긴다(판수가 늦은 사람의 100판당이 부푼다)."""
    people = [(i, i, 10, 20, 100, 100) for i in range(1, 20)]
    for fc, fcg, want in (("2026-09-28T09:00:00", "2026-10-10T09:00:00", "hidden"),
                          ("2026-10-10T09:00:00", "2026-09-28T09:00:00", "hidden"),
                          ("2026-09-28T09:00:00", None, "hidden"),
                          ("2026-09-28T09:00:00", "2026-09-29T09:00:00", "ok")):
        def body(db, fc=fc, fcg=fcg, want=want):
            _reward_db(db, people, fc_started=fc, fcg_started=fcg)
            assert M.reward_board(db.conn).state == want, (fc, fcg, want)
        using(body)

    def body(db):       # 사람별 처리 전 · 옛 rank.db(fc_games 없음)
        db.snap(T0, [person(1, 1)])
        assert M.reward_board(db.conn).state == "no_season"
        rc._set_state(db.conn, person_season=json.dumps({"seq": 1, "start": "2026-09-28", "at": "2026-09-28T09:00:00"}),
                      **{rc.FC_STARTED + "2026-09-28T09:00:00": "2026-09-28T09:00:00",
                         rc.FCG_STARTED + "2026-09-28T09:00:00": "2026-09-28T09:00:00"})
        with db.conn:
            db.conn.execute("DROP TABLE fc_games")
        M.clear_cache()
        assert M.reward_board(db.conn).state == "no_table"
    using(body)


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
