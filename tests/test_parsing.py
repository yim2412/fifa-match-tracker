"""파싱·집계 회귀 테스트 — 실제 넥슨 응답 픽스처로 골든값을 고정한다.

넥슨이 필드를 바꾸거나(오픈API는 공식 문서가 JS 렌더라 자동 대조가 안 된다)
파싱·집계 로직을 잘못 건드리면 여기서 값이 어긋나 바로 잡힌다. API 응답
파싱은 회귀가 잘 어울리는 영역이라(CLAUDE.md "나중에" 항목), 실응답을 받은
지금 도입했다.

픽스처는 tests/fixtures/ 의 매치 상세 JSON 4개 + manifest.json(기준 ouid).
네트워크 없이 즉시 돈다. pytest 없이도 `python tests/test_parsing.py` 로 실행.

**픽스처의 식별자는 익명화되어 있다.** 실제 응답을 받아 만들되 ouid·구단주명·matchId·
경기 날짜는 가짜 값으로 바꿨다. 이 저장소는 공개이고, 그 넷은 넥슨 API 로 실제 계정을
되짚을 수 있는 값이다(matchId 하나만 있어도 조회하면 참가자가 그대로 나온다).
반대로 골·점유율·슛 좌표·선수ID 는 **원본 그대로**다 — 회귀 테스트의 의미가 거기 있다.

픽스처를 새로 받으면 같은 방식으로 익명화한 뒤 커밋할 것:
  ouid → 000…0001~0005 · 구단주명 → 테스트구단주/상대구단주N
  matchId → aaa…0001~0004 (파일명도 함께) · matchDate → 순서만 보존한 가짜 날짜

포함된 4경기(다양성 확보): 4:3 승 · 1:1 무 · 1:2 패 · 0:5 "오류"(중단).
"오류" 경기는 승/무/패 문자열이 아니라, 그런 경기를 오집계하지 않는지까지 본다.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta
from pathlib import Path

import config
import models
import ranker
import seasons as sn
import stats as st

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load():
    man = json.load(open(os.path.join(_DIR, "manifest.json"), encoding="utf-8"))
    ouid = man["ouid"]
    details = [json.load(open(os.path.join(_DIR, m + ".json"), encoding="utf-8"))
               for m in man["match_ids"]]
    return ouid, man["match_ids"], details


def test_parse_match():
    ouid, mids, details = _load()
    by_id = {d["matchId"]: d for d in details}
    expect = {
        "aaaaaaaaaaaaaaaaaaaa0001": (1, 2, "패", 46, 2),
        "aaaaaaaaaaaaaaaaaaaa0002": (4, 3, "승", 53, 8),
        "aaaaaaaaaaaaaaaaaaaa0003": (1, 1, "무", 58, 7),
        "aaaaaaaaaaaaaaaaaaaa0004": (0, 5, "오류", 0, 0),
    }
    for mid, (gf, ga, res, poss, shoot) in expect.items():
        ms = models.parse_match(by_id[mid], ouid)
        assert ms.my_goals == gf, (mid, "my_goals", ms.my_goals)
        assert ms.opp_goals == ga, (mid, "opp_goals", ms.opp_goals)
        assert res in ms.result, (mid, "result", ms.result)
        assert ms.possession == poss, (mid, "possession", ms.possession)
        assert ms.shoot_total == shoot, (mid, "shoot_total", ms.shoot_total)


def test_summarize():
    ouid, _, details = _load()
    s = models.summarize([models.parse_match(d, ouid) for d in details])
    # 4경기 중 하나는 "오류"(중단) — 집계에서 빠져 total=3, 그 0:5 실점도 제외.
    assert (s.total, s.win, s.draw, s.lose) == (3, 1, 1, 1), \
        (s.total, s.win, s.draw, s.lose)
    assert (s.goals_for, s.goals_against) == (6, 6), (s.goals_for, s.goals_against)


def test_shot_map():
    ouid, _, details = _load()
    sm = st.shot_map(details, ouid, mine=True)
    assert (sm.total, sm.goals, sm.on_target, sm.off_target) == (17, 6, 8, 3), \
        (sm.total, sm.goals, sm.on_target, sm.off_target)
    # 골 수는 매치 요약(goalTotal)과도 일치해야 한다.
    goals_from_summary = sum(models.parse_match(d, ouid).my_goals for d in details)
    assert sm.goals == goals_from_summary, (sm.goals, goals_from_summary)


def test_clutch_summary():
    ouid, _, details = _load()
    cs = st.clutch_summary(details, ouid)
    assert cs.first_scored == [1, 1, 1], cs.first_scored
    assert cs.first_conceded == [0, 0, 0], cs.first_conceded  # "오류"는 승/무/패 아님
    assert (cs.comeback_win, cs.comeback_lose, cs.goalless) == (0, 1, 0), \
        (cs.comeback_win, cs.comeback_lose, cs.goalless)


def test_team_profile():
    # 손계산: 승·무·패 3경기("오류" 제외). 슈팅 2+8+7=17 vs 6+7+3=16, 골 6 vs 6,
    # 점유 46+53+58, 태클 성공 5+3+3/7+4+7 vs 8+9+3/12+11+8 — 비율은 합의 비율.
    ouid, _, details = _load()
    p = st.team_profile(details, ouid)
    assert p.games == 3, p.games
    got = {a.name: (round(a.mine, 2), round(a.opp, 2)) for a in p.axes}
    assert got["슈팅"] == (5.67, 5.33), got["슈팅"]
    assert got["득점"] == (2.0, 2.0), got["득점"]
    assert got["점유율"] == (52.33, 47.67), got["점유율"]
    assert got["태클 성공률"] == (61.11, 64.52), got["태클 성공률"]
    assert [a.name for a in p.axes] == [n for n, _ in st.PROFILE_AXES]
    assert next(a for a in p.axes if a.name == "득점").share == 0.5
    empty = st.team_profile([], ouid)
    assert empty.games == 0 and empty.axes == []


def test_result_breakdown():
    ouid, _, details = _load()
    rb = st.result_breakdown(details, ouid)
    assert rb.normal == [0, 1, 1], rb.normal
    assert rb.extra == [1, 0, 0], rb.extra          # 4:3 은 연장까지 간 경기
    assert rb.shootout == [0, 0, 0], rb.shootout
    assert rb.forfeit == [0, 0, 0], rb.forfeit


def test_finishing_ranking():
    ouid, _, details = _load()
    fr = st.finishing_ranking(details, ouid)
    top = fr[0]
    assert top.sp_id == 839167198, top.sp_id
    assert (top.shots, top.goals) == (8, 4), (top.shots, top.goals)


def test_goal_minute_buckets_added_time():
    # 전반 추가시간(45분+) 골이 후반 첫 구간(45~60)으로 새면 안 된다 — 30~45 에.
    def gt(period, sec):
        return (period << 24) | sec

    def one(period, sec):
        d = {"matchInfo": [
            {"ouid": "me", "matchDetail": {},
             "shootDetail": [{"result": 3, "goalTime": gt(period, sec), "type": 1}]},
            {"ouid": "op", "matchDetail": {}, "shootDetail": []}]}
        return [b.label for b in st.goal_minute_buckets([d], "me") if b.scored][0]

    assert one(0, 2820) == "30~45", one(0, 2820)   # 전반 47분
    assert one(0, 2000) == "30~45", one(0, 2000)   # 전반 33분
    assert one(1, 300) == "45~60", one(1, 300)     # 후반 50분
    assert one(1, 2820) == "75~90", one(1, 2820)   # 후반 92분(추가시간)


def test_division_stats():
    ouid, _, details = _load()
    ds = st.division_stats(details, ouid)
    by = {s.division_id: s for s in ds}
    # 상대 900 두 경기(승·패), 상대 1000 한 경기(무). "오류"(상대 900)는 승/무/패가
    # 아니라 제외 → 총 3경기.
    assert by[900].games == 2 and [by[900].win, by[900].draw, by[900].lose] == [1, 0, 1]
    assert (by[900].goals_for, by[900].goals_against) == (5, 5)
    assert [by[1000].win, by[1000].draw, by[1000].lose] == [0, 1, 0]
    assert sum(s.games for s in ds) == 3, sum(s.games for s in ds)


def test_possession_stats():
    ouid, _, details = _load()
    bands = {b.label: b for b in st.possession_stats(details, ouid)}
    bal = bands["균형"]  # 세 경기 모두 점유율 46~58 → 균형 구간
    assert bal.games == 3 and [bal.win, bal.draw, bal.lose] == [1, 1, 1]
    assert (bal.goals_for, bal.goals_against) == (6, 6)
    # "오류" 경기는 점유율이 null(0)이라 제외 → 열세·우세는 비어 있다.
    assert bands["열세"].games == 0 and bands["우세"].games == 0


def test_pair_synergy():
    ouid, _, details = _load()
    pairs = st.pair_synergy(details, ouid, min_games=1)
    # 유효 3경기가 같은 선발 10명(SUB·GK 제외) → C(10,2)=45 조합, 각 3경기.
    assert len(pairs) == 45, len(pairs)
    assert all(p.games == 3 for p in pairs)
    assert st.pair_synergy(details, ouid, min_games=4) == []


def test_shot_xg_deterministic():
    # 순수 함수 — 좌표만으로 결정. 계수를 바꾸면(모델 재튜닝) 여기가 깨진다(의도).
    assert abs(st.shot_xg(0.90, 0.50, True, "일반(D)") - 0.7482) < 1e-3
    assert st.shot_xg(0.88, 0.50, True, "페널티킥") == 0.76
    assert st.decode_goal_time((1 << 24) | 300) == (1, 300)


def test_shot_breakdown():
    """유형·거리별 효율 — 쪼갠 합이 원본 슛 수와 맞는지가 핵심."""
    ouid, _, details = _load()
    for mine, total, goals in ((True, 17, 6), (False, 23, 11)):
        sm = st.shot_map(details, ouid, mine=mine)
        types = st.shot_type_breakdown(sm)
        dists = st.shot_distance_breakdown(sm)
        # 어떤 슛도 흘리거나 두 번 세면 안 된다.
        assert sum(b.shots for b in types) == total, (mine, types)
        assert sum(b.shots for b in dists) == total, (mine, dists)
        assert sum(b.goals for b in types) == goals, (mine, types)
        assert sum(b.goals for b in dists) == goals, (mine, dists)
        # 유형은 슛 많은 순, 거리는 가까운 순 고정.
        assert [b.shots for b in types] == sorted(
            (b.shots for b in types), reverse=True)
        assert [b.label for b in dists] == [
            l for l in st._DIST_ORDER if l in {b.label for b in dists}]

    sm = st.shot_map(details, ouid, mine=True)
    got = {b.label: (b.goals, b.shots) for b in st.shot_type_breakdown(sm)}
    assert got["일반(D)"] == (2, 8), got
    assert got["헤더"] == (2, 2), got
    got = {b.label: (b.goals, b.shots) for b in st.shot_distance_breakdown(sm)}
    # 이 4경기의 골은 전부 박스 안에서 나왔다.
    assert got[st.DIST_IN_BOX] == (6, 9), got
    assert got[st.DIST_FAR][0] == 0, got

    # 표본 미달 칸은 비율을 내지 않는다(화면에서 "—"로 나가는 근거).
    small = [b for b in st.shot_type_breakdown(sm) if b.shots < st.MIN_BUCKET_SHOTS]
    assert small and all(not b.enough for b in small)
    assert all(b.enough for b in st.shot_type_breakdown(sm)
               if b.shots >= st.MIN_BUCKET_SHOTS)
    assert st.shot_type_breakdown(st.ShotMap()) == []
    assert st.shot_distance_breakdown(st.ShotMap()) == []


# ── 시즌(seasons.py) ──────────────────────────────────────────────────
# 가짜 시즌표. 실제처럼 앞 시즌 종료일 == 다음 시즌 시작일이고, 마지막 시즌이
# 끝난 뒤(07-30~)는 진행 중이라 목록에 없다.
_S2 = sn.Season(no=88, name="시즌 2", start=date(2026, 4, 2), end=date(2026, 5, 28))
_S3 = sn.Season(no=89, name="시즌 3", start=date(2026, 5, 28), end=date(2026, 7, 30))
_SEASONS = [_S3, _S2]


def test_season_boundary_goes_to_new_season():
    # [시작, 종료) — 경계일 경기는 새 시즌에만. 닫힌 구간이면 두 시즌에 겹친다.
    assert sn.season_of(_SEASONS, datetime(2026, 5, 28, 0, 0)) == _S3
    assert sn.season_of(_SEASONS, datetime(2026, 5, 27, 23, 59)) == _S2
    assert sn.season_of(_SEASONS, date(2026, 4, 2)) == _S2
    assert not _S2.contains(date(2026, 5, 28)), "종료일이 포함됐다"
    # 진행 중(마지막 종료일 당일부터)·날짜 없음·목록 이전은 시즌 없음
    assert sn.season_of(_SEASONS, datetime(2026, 7, 30, 9, 0)) is None
    assert sn.season_of(_SEASONS, None) is None
    assert sn.season_of(_SEASONS, date(2026, 4, 1)) is None


def test_group_by_season_order_and_buckets():
    items = [datetime(2026, 8, 1), datetime(2026, 7, 30),     # 진행 중
             datetime(2026, 7, 29), datetime(2026, 5, 28),    # 시즌 3
             datetime(2026, 5, 27),                           # 시즌 2
             datetime(2026, 1, 1),                            # 목록 이전 — 버린다
             None]                                            # 날짜 없음 — 버린다
    got = sn.group_by_season(_SEASONS, items, key=lambda x: x)
    assert [(s.no if s else None, len(v)) for s, v in got] == [(None, 2), (89, 2), (88, 1)], got
    # 진행 중 그룹에 목록 이전·날짜 없는 경기가 섞이면 안 된다
    assert datetime(2026, 1, 1) not in got[0][1] and None not in got[0][1]
    # 입력 순서와 무관하게 최신 시즌이 앞
    got_rev = sn.group_by_season(list(reversed(_SEASONS)), list(reversed(items)), key=lambda x: x)
    assert [s.no if s else None for s, _ in got_rev] == [None, 89, 88]
    assert sn.group_by_season([], items, key=lambda x: x) == []


def test_season_label_has_year():
    # 해마다 "시즌 3" 이 반복되므로 시작 연도로 가른다
    assert _S3.label == "2026 시즌 3", _S3.label
    nxt = sn.Season(no=95, name="시즌 3", start=date(2027, 5, 27), end=date(2027, 7, 29))
    assert nxt.label != _S3.label
    assert _S3.span_text == "2026-05-28 ~ 2026-07-30"


class _FakeRes:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _fetch_with(html):
    orig_get, orig_on = ranker._session.get, config.WEB_DATA
    ranker._session.get = lambda *a, **k: _FakeRes(html)
    config.WEB_DATA = True
    try:
        return sn.fetch_seasons()
    finally:
        ranker._session.get, config.WEB_DATA = orig_get, orig_on


def test_fetch_seasons_parses_page():
    # 실제 페이지 모양(seasons.py 머리말) — "전체 시즌"은 기간이 없어 빠져야 하고,
    # 날짜가 깨진 줄 하나 때문에 나머지를 잃으면 안 된다.
    html = """
      <a onclick="ChangeSeason(0);"><span>전체 시즌</span></a>
      <a onclick="ChangeSeason(88);" class="on"><span>시즌  2 (2026-04-02~2026-05-28)</span></a>
      <a onclick="ChangeSeason(89);"><span>시즌 3 (2026-05-28~2026-07-30)</span></a>
      <a onclick="ChangeSeason(90);"><span>시즌 9 (2026-13-01~2026-14-01)</span></a>
    """
    got = _fetch_with(html)
    assert got == [_S3, _S2], got  # 최신순, 공백 정리("시즌  2" → "시즌 2")
    for bad, why in (("<html>점검 진행 중</html>", "점검"), ("<html></html>", "구조")):
        try:
            _fetch_with(bad)
            raise AssertionError(f"{why}: 예외가 없다")
        except sn.SeasonError as e:
            assert why in str(e), (why, e)


def test_player_record_matches_finishing_table():
    # 선수 카드 '내 기록'의 합계는 선수별 결정력 표와 같은 숫자여야 한다(같은 슛을 다르게 세면 안 된다)
    ouid, matches, details = _load()
    table = {p.sp_id: p for p in st.finishing_ranking(details, ouid)}
    assert table, "픽스처에 슛이 없다"
    total_mapped = 0
    for sp, pf in table.items():
        if pf.shots == 0:
            continue
        weeks = st.player_finishing_trend(details, ouid, sp)
        assert sum(w.shots for w in weeks) == pf.shots, (sp, [w.shots for w in weeks], pf.shots)
        assert sum(w.goals for w in weeks) == pf.goals and sum(w.on_target for w in weeks) == pf.on_target
        assert abs(sum(w.xg for w in weeks) - pf.xg) < 1e-9, (sp, pf.xg)
        assert [w.week_start for w in weeks] == sorted(w.week_start for w in weeks)
        assert all(w.week_start.weekday() == 0 for w in weeks), "주 시작이 월요일이 아니다"
        n = len(st.shot_map(details, ouid, mine=True, sp_id=sp).shots)
        assert n <= pf.shots, (sp, n, pf.shots)  # 좌표가 빈 슛은 맵에서만 빠진다
        total_mapped += n
    assert total_mapped == len(st.shot_map(details, ouid, mine=True).shots), "선수별 맵을 합치면 전체 맵이어야 한다"


def test_played_counts_used_subs_only():
    sub_idle = {"spId": 1, "spPosition": st.SUB_POSITION, "status": {}}
    sub_used = {"spId": 1, "spPosition": st.SUB_POSITION, "status": {"passTry": 3}}
    starter = {"spId": 1, "spPosition": 5, "status": {}}
    assert (st._played(sub_idle), st._played(sub_used), st._played(starter)) == (False, True, True)
    d = {"matchDate": "2026-10-01T10:00:00",
         "matchInfo": [{"ouid": "me", "player": [sub_idle], "shootDetail": []}, {"ouid": "x"}]}
    assert st.player_finishing_trend([d], "me", 1) == [], "벤치에만 있던 경기를 출전으로 셌다"
    d["matchInfo"][0]["player"] = [sub_used]
    weeks = st.player_finishing_trend([d], "me", 1)
    assert len(weeks) == 1 and weeks[0].games == 1 and str(weeks[0].week_start) == "2026-09-28", weeks
    # 슛 기록은 있는데 출전 기록이 없는 경기(넥슨이 값을 비운 경우) — 슛은 세되 출전으로는 안 센다
    d["matchInfo"][0]["player"] = [sub_idle]
    d["matchInfo"][0]["shootDetail"] = [{"spId": 1, "result": st.SHOT_GOAL, "x": 0.9, "y": 0.5}]
    weeks = st.player_finishing_trend([d], "me", 1)
    assert [(w.games, w.shots, w.goals) for w in weeks] == [(0, 1, 1)], weeks
    # 경기 순서가 섞여 와도(DB 는 최신순) 주는 오래된 것부터
    later = {"matchDate": "2026-10-08T10:00:00",
             "matchInfo": [{"ouid": "me", "player": [starter], "shootDetail": []}, {"ouid": "x"}]}
    weeks = st.player_finishing_trend([later, d], "me", 1)
    assert [str(w.week_start) for w in weeks] == ["2026-09-28", "2026-10-05"], weeks


def test_store_uses_orjson_and_merge_keeps_db_order():
    # 설치돼 있는데 대체 경로(json)로 돌면 조용히 2배 느려진다 — requirements.txt 에 있다
    import store
    import orjson
    assert store.JSON_ENGINE == "orjson" and store._loads is orjson.loads, (store.JSON_ENGINE, store._loads)
    old = [{"matchId": "b", "matchDate": "2026-01-02"}, {"matchId": "d", "matchDate": "2026-01-01"}]
    new = [{"matchId": "a", "matchDate": "2026-01-03"},          # 더 새 경기
           {"matchId": "c", "matchDate": "2026-01-01T12"},       # 이어 받기로 들어온 옛 경기(가운데)
           {"matchId": "b", "matchDate": "2026-01-02"}]          # 이미 있는 경기 — 한 번만
    merged = store.merge_details(old, new)
    assert [d["matchId"] for d in merged] == ["a", "b", "c", "d"], merged
    assert [d["matchId"] for d in old] == ["b", "d"], "옛 목록을 고쳤다"


def test_formation_of_matches_reference():
    # 한 번만 훑게 바꾼 것(2026-10-02)이 예전 방식(라인마다 전부 훑기)과 같은 답을 내야 한다.
    # 경계(0=GK·28=SUB·각 라인 끝)와 이상한 값(None·문자·실수)까지.
    def reference(players):
        return "-".join(str(sum(1 for p in players if isinstance(p.get("spPosition"), int)
                                and p["spPosition"] in rng)) for _, rng in st._LINES)

    import random
    rnd = random.Random(7)
    odd = [None, "5", 5.0, -1, 0, 28, 29, 100]
    for _ in range(500):
        players = [{"spPosition": rnd.choice(list(range(0, 30)) + odd)} for _ in range(rnd.randint(0, 18))]
        players += [{}]  # 키가 없는 선수
        assert st.formation_of(players) == reference(players), players
    assert st.formation_of([{"spPosition": p} for p in (5, 5, 5, 5, 10, 13, 14, 18, 25, 0, 28)]) == "4-1-2-1-1"


def _rank_row(no, nick, color, value):
    tc = (f'<span class="ico_rank"></span><span class="name"></span>'
          f'<span class="inner">{color} <small>(11명)</small></span>' if color else
          '<span class="ico_rank"> </span> <span class="name"> </span>')
    return (f'<div class="tr"><span class="td rank_no">{no}</span>'
            f'<span class="td rank_coach"><span class="name profile_pointer" data-x="1">{nick}</span>'
            f'<span class="price" alt="{value}">1억</span></span>'
            f'<span class="td team_color">{tc}</span>'
            f'<span class="td formation">-</span><span class="td rank_best"></span></div>')


def test_rank_page_rows_stay_aligned_when_a_color_is_empty():
    # 실측 475페이지: 팀컬러를 안 쓰는 사람이 있다. 페이지 전체에서 따로 뽑아 짝지으면
    # 그 행이 다음 사람 팀컬러를 집어 뒤가 전부 한 칸씩 밀린다.
    html = "<div>" + "".join([_rank_row(1, "가", "네덜란드", "1,000"),
                              _rank_row(2, "나&amp;다", "", "2,000"),
                              _rank_row(3, "라", "잉글랜드", "3,000")]) + "</div>"
    rows = ranker.parse_rank_page(html)
    assert rows == [("가", "네덜란드", 1000), ("나&다", "", None), ("라", "잉글랜드", 3000)], rows


def test_fetch_rank_page_refuses_empty_page():
    # 구조가 바뀌어 0행으로 읽히면 상대 수백 명이 '랭킹 밖'으로 30일 캐시된다 — 실패여야 한다
    orig_get, orig_on = ranker._session.get, config.WEB_DATA
    config.WEB_DATA = True
    try:
        ranker._session.get = lambda *a, **k: _FakeRes("<div>바뀐 구조</div>")
        try:
            ranker.fetch_rank_page(1)
            raise AssertionError("빈 페이지를 정상으로 읽었다")
        except ranker.RankerError:
            pass
        ranker._session.get = lambda *a, **k: _FakeRes(_rank_row(1, "가", "네덜란드", "1"))
        assert ranker.fetch_rank_page(1) == [("가", "네덜란드", 1)]
    finally:
        ranker._session.get, config.WEB_DATA = orig_get, orig_on


# ── 변이 전수 측정 보강(2026-10-02, docs/mutation) ─────────────────────────
# 판정을 뒤집어도 초록이던 줄들. 기대값은 전부 손으로 셌다 — 코드로 다시 계산하면 같은 실수를 같이 한다.
def _m(day, result, opp="가", gf=1, ga=0, hour=12, **kw):
    """합성 경기 하나 — 날짜는 2026-09-xx/10-xx(day 가 30 넘으면 10월로)."""
    d = datetime(2026, 9, day, hour) if day <= 30 else datetime(2026, 10, day - 30, hour)
    return models.MatchSummary(match_id=f"{day}-{hour}-{opp}", match_date=d, match_type=52,
                               my_nickname="나", opponent=opp, result=result, my_goals=gf,
                               opp_goals=ga, possession=50, shoot_total=0, shoot_effective=0,
                               pass_try=0, pass_success=0, rating=0.0, **kw)


def test_score_shows_shootout_only_when_there_was_one():
    assert _m(1, "승", gf=1, ga=1, my_shootout=4, opp_shootout=3).score == "1 : 1 (승부차기 4:3)"
    assert _m(1, "승", gf=2, ga=1).score == "2 : 1"


def test_opponent_stats_counts_each_result():
    ms = [_m(5, "승", "가"), _m(4, "무", "가"), _m(3, "패", "가"), _m(2, "몰수승", "가"), _m(1, "패", "나")]
    got = {o.nickname: (o.games, o.win, o.draw, o.lose) for o in models.opponent_stats(ms)}
    assert got == {"가": (4, 2, 1, 1), "나": (1, 0, 0, 1)}, got
    assert [o.nickname for o in models.opponent_stats(ms)] == ["가", "나"]  # 많이 붙은 순


def test_current_streak_by_kind():
    cs = models.current_streak
    assert cs([_m(3, "패"), _m(2, "패"), _m(1, "승")]) == ("패", 2)
    assert cs([_m(3, "무"), _m(2, "무"), _m(1, "패")]) == ("무", 2)
    assert cs([_m(3, "몰수승"), _m(2, "승"), _m(1, "패")]) == ("승", 2)
    assert cs([]) == ("", 0) and cs([_m(1, "오류")]) == ("", 0)


def test_longest_streaks_draw_breaks_run():
    seq = ["승", "승", "무", "승", "패", "패", "패", "승"]          # 날짜 순서대로
    ms = [_m(i + 1, r) for i, r in enumerate(seq)][::-1]          # 넘겨줄 땐 섞여 있어도 된다
    assert models.longest_streaks(ms) == (2, 3)
    assert models.longest_streaks([_m(1, "패"), _m(2, "승")]) == (1, 1)


def test_period_stats_groups_by_calendar():
    # 09-30(수) 승 · 10-01(목) 무 · 10-05(월) 패 2:3
    ms = [_m(30, "승", gf=2, ga=1), _m(31, "무", gf=1, ga=1), _m(35, "패", gf=2, ga=3)]
    rows = lambda days: [(p.label, p.win, p.draw, p.lose, p.goals_for, p.goals_against)  # noqa: E731
                         for p in models.period_stats(ms, days=days)]
    assert rows(30) == [("2026-10", 0, 1, 1, 3, 4), ("2026-09", 1, 0, 0, 2, 1)], rows(30)
    assert rows(7) == [("10/05~10/11", 0, 0, 1, 2, 3), ("09/28~10/04", 1, 1, 0, 3, 2)], rows(7)
    assert [r[0] for r in rows(1)] == ["2026-10-05", "2026-10-01", "2026-09-30"], rows(1)


def _p(sp, pos, **status):
    return {"spId": sp, "spPosition": pos, "spGrade": 1, "status": status}


def _d(day, res, me_players=(), opp_players=(), opp="상대", me_shots=(), opp_shots=(),
       division=None, hour=12, possession=50):
    """합성 경기 상세 — 내 ouid 는 'me'. res 는 내 결과."""
    when = datetime(2026, 9, day, hour) if day <= 30 else datetime(2026, 10, day - 30, hour)
    opp_res = {"승": "패", "패": "승"}.get(res, res)
    return {"matchId": f"m{day}-{hour}-{opp}", "matchDate": when.isoformat(),
            "matchInfo": [
                {"ouid": "me", "nickname": "나", "division": division,
                 "matchDetail": {"matchResult": res, "possession": possession},
                 "player": list(me_players), "shootDetail": list(me_shots)},
                {"ouid": "o", "nickname": opp, "matchDetail": {"matchResult": opp_res},
                 "player": list(opp_players), "shootDetail": list(opp_shots)}]}


def test_champion_boundary_and_division_trend_skips_missing():
    assert [st.is_champion_or_above(x) for x in (800, 900, 901, None)] == [True, True, False, False]
    ds = [_d(3, "승", division=900), _d(2, "승"), _d(1, "패", division=1000)]
    ds[1]["matchInfo"][0]["division"] = None
    no_date = _d(4, "승", division=800)
    no_date["matchDate"] = ""
    assert [div for _, div in st.division_trend(ds + [no_date], "me")] == [1000, 900]


def test_opponent_squad_picks_that_opponent():
    ds = [_d(3, "승", opp="가", opp_players=[_p(1, 5)]), _d(2, "패", opp="나", opp_players=[_p(2, 5)]),
          _d(1, "무", opp="나", opp_players=[_p(3, 5)])]
    ds.insert(0, {"matchId": "x", "matchInfo": [{"ouid": "남"}]})  # 내가 없는 경기 — 건너뛴다
    players, _, res = st.opponent_squad(ds, "me", "나")
    assert [p["spId"] for p in players] == [2] and res == "패", (players, res)  # 가장 최근 '나' 경기
    assert st.opponent_squad(ds, "me", "다") is None
    # 내 스쿼드도 — 내가 없는 경기를 건너뛰고 가장 최근 경기
    ds[1]["matchInfo"][0]["player"] = [_p(50, 25)]
    mine, _, res = st.own_squad(ds, "me")
    assert [p["spId"] for p in mine] == [50] and res == "승", (mine, res)
    assert st.own_squad(ds[:1], "me") is None


def test_aggregate_players_counts_results_position_and_gk():
    X, GK, SUB = 100, 200, 300
    air = {"aerialTry": 4, "aerialSuccess": 2, "passTry": 1}
    ds = [_d(3, "승", [_p(X, 25, **air), _p(GK, 0, **air), _p(SUB, 28)]),
          _d(2, "무", [_p(X, 25, **air), _p(GK, 0, **air)]),
          _d(1, "패", [_p(X, 5, **air), _p(GK, 0, **air)])]
    names = {0: "GK", 5: "CB", 25: "ST"}
    got = {s.sp_id: s for s in st.aggregate_players(ds, "me", pos_name=names.get)}
    assert SUB not in got, "벤치에만 있던 선수를 출전으로 셌다"
    x, gk = got[X], got[GK]
    assert (x.games, x.win, x.draw, x.lose) == (3, 1, 1, 1), (x.games, x.win, x.draw, x.lose)
    assert x.position == "ST" and gk.position == "GK", (x.position, gk.position)  # 가장 자주 선 자리
    # 공중볼%(50)는 필드 선수에게만 더한다 — 같은 기록이면 GK 가 정확히 그만큼 낮다
    assert abs((x.attack_power - gk.attack_power) - x.aerial_rate) < 1e-9 and x.aerial_rate == 50
    assert abs((x.defense_power - gk.defense_power) - x.aerial_rate) < 1e-9


def test_formation_stats_by_opponent_shape():
    four = [_p(i, 5) for i in range(4)] + [_p(9, 13), _p(10, 13), _p(11, 25)]
    ds = [_d(3, "승", opp_players=four), _d(2, "무", opp_players=four), _d(1, "패", opp_players=[])]
    got = {f.formation: (f.games, f.win, f.draw, f.lose) for f in st.formation_stats(ds, "me")}
    assert got == {"4-0-2-0-1": (2, 1, 1, 0), "0-0-0-0-0": (1, 0, 0, 1)}, got


def _shot(period, sec, result=3, sp=None, **kw):
    sd = {"goalTime": (period << 24) + sec, "result": result, "x": 0.9, "y": 0.5, **kw}
    if sp is not None:
        sd["spId"] = sp
    return sd


def test_result_breakdown_extra_time_and_goal_sides():
    ds = [_d(3, "승", me_shots=[_shot(2, 10)]),                       # 연장(구간 2) 골 → 연장 경기
          _d(2, "패", me_shots=[_shot(1, 30, result=1)], opp_shots=[_shot(1, 40)]),  # 유효슛은 골 아님
          _d(1, "무", me_shots=[_shot(0, 5)], opp_shots=[_shot(0, 6)])]
    rb = st.result_breakdown(ds, "me")
    assert (rb.extra, rb.normal) == ([1, 0, 0], [0, 1, 1]), (rb.extra, rb.normal)
    got = {k: (v.scored, v.conceded) for k, v in rb.periods.items()}
    assert got == {2: (1, 0), 1: (0, 1), 0: (1, 1)}, got


def test_clutch_first_goal_ties_and_comebacks():
    ds = [_d(4, "패", me_shots=[_shot(0, 100)], opp_shots=[_shot(0, 200), _shot(1, 5)]),  # 선제골 후 패
          _d(3, "승", me_shots=[_shot(1, 50)], opp_shots=[_shot(0, 300)]),               # 선제 실점 후 승
          _d(2, "무", me_shots=[_shot(0, 60)], opp_shots=[_shot(0, 60)]),                # 같은 시각 — 보류
          _d(1, "무")]                                                                   # 무득점
    cs = st.clutch_summary(ds, "me")
    assert (cs.first_scored, cs.first_conceded) == ([0, 0, 1], [1, 0, 0]), (cs.first_scored, cs.first_conceded)
    assert (cs.comeback_lose, cs.comeback_win, cs.goalless) == (1, 1, 2)


def test_minute_buckets_extra_and_halves():
    ds = [_d(1, "승", me_shots=[_shot(2, 0), _shot(1, 0), _shot(0, 2999)], opp_shots=[_shot(3, 9)])]
    got = {b.label: (b.scored, b.conceded) for b in st.goal_minute_buckets(ds, "me") if b.scored or b.conceded}
    # 후반 0초 = 45분 → 45~60 · 전반 2999초(49분, 추가시간) → 30~45 에 남는다 · 구간 2·3 → 연장
    assert got == {"45~60": (1, 0), "30~45": (1, 0), "연장": (1, 1)}, got


def test_time_of_day_band_edges():
    ms = [_m(1, "승", hour=5), _m(2, "무", hour=6), _m(3, "패", hour=11), _m(4, "승", hour=12),
          _m(5, "패", hour=23)]
    ms.append(models.MatchSummary(**{**ms[0].__dict__, "match_id": "x", "match_date": None}))
    got = {b.label: (b.win, b.draw, b.lose) for b in st.time_of_day_rates(ms)}
    assert got == {"심야": (1, 0, 0), "오전": (0, 1, 1), "오후": (1, 0, 0), "저녁·밤": (0, 0, 1)}, got


def _with(d, me_goals=0, opp_goals=0, opp_div=None):
    d["matchInfo"][0]["shoot"] = {"goalTotal": me_goals}
    d["matchInfo"][1]["shoot"] = {"goalTotal": opp_goals}
    d["matchInfo"][1]["division"] = opp_div
    return d


def test_division_stats_unknown_bucket_and_results():
    ds = [_with(_d(4, "승"), 2, 1, 900), _with(_d(3, "무"), 1, 1, 900), _with(_d(2, "패"), 0, 3),
          _with(_d(1, "오류"), 9, 9, 900)]
    got = {s.name: (s.win, s.draw, s.lose, s.goals_for, s.goals_against)
           for s in st.division_stats(ds, "me", name_of=lambda i: f"등급{i}")}
    assert got == {"등급900": (1, 1, 0, 3, 2), "미상": (0, 0, 1, 0, 3)}, got


def test_possession_band_edges():
    # 무·패 개수를 다르게(2:1) — 같으면 둘을 바꿔 세도 숫자가 같아 안 잡힌다(재측정에서 실제로 그랬다)
    ds = [_with(_d(i + 1, r, possession=p), 1, 0) for i, (p, r) in enumerate(
        [(39, "승"), (40, "무"), (50, "무"), (60, "패"), (61, "승"), (0, "승")])]
    got = {b.label: (b.win, b.draw, b.lose) for b in st.possession_stats(ds, "me")}
    assert got == {"열세": (1, 0, 0), "균형": (0, 2, 1), "우세": (1, 0, 0)}, got  # 0 은 무기록이라 뺀다


def test_pair_synergy_counts_and_min_games_edge():
    xi = [_p(1, 5), _p(2, 25), _p(9, 0), _p(7, 28)]          # GK(9)·SUB(7)는 조합에서 뺀다
    ds = [_d(4, "승", xi), _d(3, "무", xi), _d(2, "무", xi), _d(1, "패", xi)]   # 무 2 · 패 1(비대칭)
    got = [(s.a_id, s.b_id, s.win, s.draw, s.lose) for s in st.pair_synergy(ds, "me", min_games=4)]
    assert got == [(1, 2, 1, 2, 1)], got
    assert st.pair_synergy(ds, "me", min_games=5) == []       # 4경기 조합은 5경기 기준에서 빠진다


def test_shot_buckets_on_target_distance_and_enough():
    S = st.Shot
    # 막힌 유효슛 2 · 빗나감 1 · 골 1 — 유효슛과 빗나감 개수를 다르게(같으면 바꿔 세도 안 잡힌다)
    shots = [S(0.9, 0.5, st.SHOT_ON_TARGET, "일반", False, False, 0.1),
             S(0.9, 0.5, st.SHOT_ON_TARGET, "일반", False, False, 0.1),
             S(0.9, 0.5, 2, "일반", False, False, 0.1), S(0.9, 0.5, st.SHOT_GOAL, "일반", False, False, 0.1)]
    b = st._bucketize(shots, lambda s: "x")["x"]
    assert (b.shots, b.goals, b.on_target) == (4, 1, 3), (b.shots, b.goals, b.on_target)
    b.shots = st.MIN_BUCKET_SHOTS
    assert b.enough
    b.shots -= 1
    assert not b.enough
    orig = st.shot_distance_m
    try:
        st.shot_distance_m = lambda s: st.FAR_SHOT_M          # 딱 25m 는 먼 쪽
        assert st._distance_label(shots[0]) == st.DIST_FAR
        st.shot_distance_m = lambda s: st.FAR_SHOT_M - 0.01
        assert st._distance_label(shots[0]) == st.DIST_MID
    finally:
        st.shot_distance_m = orig
    # 골대 바로 앞일수록 기대득점이 높다(각도가 음수로 꺾이는 구간 포함)
    assert st.shot_xg(0.999, 0.5, True, "일반") > st.shot_xg(0.95, 0.5, True, "일반") > 0.3


def test_position_group_edges_and_opponent_positions():
    assert [st._position_group_rank(p) for p in (0, 1, 8, 9, 19, 20, 27)] == [3, 2, 2, 1, 1, 0, 0]
    opp_a = [_p(11, 25), _p(12, 5), _p(13, 28), {"spId": "x", "spPosition": 5}]  # SUB·이상한 값 제외
    ds = [_d(3, "승", opp="가", opp_players=opp_a), _d(2, "승", opp="가", opp_players=[_p(11, 25)]),
          _d(1, "승", opp="나", opp_players=[_p(21, 25), _p(22, 0)])]
    got = [(r.pos_code, r.sp_id, r.count, r.total) for r in st.opponent_position_players(ds, "me")]
    # 공격(25) → 수비(5) → GK(0) 순 · 25 자리는 11번이 3경기 중 2번
    assert got == [(25, 11, 2, 3), (5, 12, 1, 1), (0, 22, 1, 1)], got
    only_ga = [(r.pos_code, r.count, r.total) for r in st.opponent_position_players(ds, "me", nicknames={"가"})]
    assert only_ga == [(25, 2, 2), (5, 1, 1)], only_ga


def test_team_color_stats_counts_and_values_once_per_opponent():
    ms = [_m(4, "승", "가"), _m(3, "무", "가"), _m(2, "패", "나"), _m(1, "승", "몰라")]
    colors = {"가": "레알", "나": "레알"}
    values = {"가": 10, "나": 30}
    got = st.team_color_stats(ms, colors.get, team_value_of=values.get)
    assert [(s.team_color, s.games, s.win, s.draw, s.lose) for s in got] == [("레알", 3, 1, 1, 1)]
    assert sorted(got[0].team_values) == [10, 30], "같은 상대 팀가치를 두 번 셌다"
    assert st.team_color_stats(ms, colors.get)[0].team_values == []   # 팀가치 함수가 없으면 비운다


def test_finishing_assists_only_on_goals_with_valid_assister():
    me_shots = [_shot(0, 1, result=3, sp=1, assist=True, assistSpId=2),
                _shot(0, 2, result=3, sp=1, assist=True, assistSpId=None),
                _shot(0, 3, result=1, sp=1, assist=True, assistSpId=2)]
    got = {p.sp_id: (p.goals, p.assists) for p in st.finishing_ranking([_d(1, "승", me_shots=me_shots)], "me")}
    assert got == {1: (2, 0), 2: (0, 1)}, got


def test_match_day_needs_full_date():
    assert st._match_day({"matchDate": "2026-10-02T10:00:00"}).isoformat() == "2026-10-02"
    assert st._match_day({"matchDate": "2026-10-02"}).isoformat() == "2026-10-02"  # 시각 없이 날짜만(딱 10자)
    assert st._match_day({"matchDate": "2026-10-0"}) is None and st._match_day({}) is None


# ── 넥슨 API 클라이언트 — 재시도·캐시(변이 4순위, 2026-10-02) ─────────────────────────────
class _Res:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body, ""

    def json(self):
        return self._body


class _Seq:
    """session.get 대역 — 정한 순서대로 응답(또는 예외)을 낸다."""

    def __init__(self, *items):
        self.items, self.calls = list(items), 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def _api_with(*items):
    import nexon_api
    api = nexon_api.FCOnlineAPI("k")
    api._session = _Seq(*items)
    return api


def test_api_retries_only_transient_errors():
    import nexon_api
    import requests
    orig = nexon_api.time.sleep
    nexon_api.time.sleep = lambda s: None       # 실제로 기다리지 않는다
    try:
        api = _api_with(_Res(429), _Res(200, {"ok": 1}))
        assert api._get("/x") == {"ok": 1} and api._session.calls == 2
        # 재시도로 넘어간 429 도 센다 — 로더가 이걸 보고 동시 요청을 줄인다(개발 단계 키 초당 5건)
        assert api.throttled == 1, api.throttled
        api = _api_with(_Res(500), _Res(503), _Res(200, [1]))
        assert api._get("/x") == [1] and api._session.calls == 3
        assert api.throttled == 0, "429 가 아닌 오류를 호출 한도로 셌다"
        for items, status in (([_Res(429)] * 3, 429), ([_Res(400, {"error": {"name": "OPENAPI00004"}})], 400)):
            api = _api_with(*items)
            try:
                api._get("/x")
                raise AssertionError("실패인데 통과했다")
            except nexon_api.NexonAPIError as e:
                assert e.status == status and api._session.calls == len(items), (status, api._session.calls)
        err = requests.ConnectionError("끊김")
        api = _api_with(err, _Res(200, {"ok": 2}))
        assert api._get("/x") == {"ok": 2}                      # 네트워크 오류도 다시 해 본다
        api = _api_with(err, err, err)
        try:
            api._get("/x")
            raise AssertionError("세 번 끊겼는데 통과했다")
        except nexon_api.NexonAPIError as e:
            assert "네트워크 오류" in e.message and api._session.calls == 3
        api = _api_with(_Res(200, {"ouid": ""}))
        try:
            api.get_ouid("없음")
            raise AssertionError("빈 ouid 를 받아들였다")
        except nexon_api.NexonAPIError:
            pass
    finally:
        nexon_api.time.sleep = orig


def test_api_detail_and_meta_cache():
    import tempfile
    import shutil
    import nexon_api
    tmp = Path(tempfile.mkdtemp())
    try:
        api = nexon_api.FCOnlineAPI("k", cache_dir=tmp)
        api._session = _Seq(_Res(200, {"matchId": "abc"}))
        assert api.get_match_detail("abc") == {"matchId": "abc"}
        assert api.get_match_detail("abc") == {"matchId": "abc"} and api._session.calls == 1, "캐시를 안 썼다"
        (tmp / "bad.json").write_text("{깨짐", encoding="utf-8")
        assert api._cache_read("bad") is None                       # 깨진 캐시는 없는 셈
        no_cache = nexon_api.FCOnlineAPI("k")
        no_cache._session = _Seq(_Res(200, {"a": 1}), _Res(200, {"a": 1}))
        no_cache.get_match_detail("abc"), no_cache.get_match_detail("abc")
        assert no_cache._session.calls == 2                          # 캐시 폴더가 없으면 매번
        # 메타: 이번 주 갱신일 뒤에 쓴 캐시는 쓰고, 그 전 것은 버린다
        api._meta_write("spid", [{"id": 1}])
        assert api._meta_read("spid") == [{"id": 1}]
        # get_meta 도 캐시가 있으면 받지 않는다 — 받으러 가면 실패하게 막아 둔다
        orig_get = nexon_api.requests.get
        nexon_api.requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("캐시가 있는데 받으러 갔다"))
        try:
            assert api.get_meta("spid") == [{"id": 1}]
        finally:
            nexon_api.requests.get = orig_get
        edge = nexon_api._week_boundary().timestamp()                  # 딱 갱신 기준 시각에 쓴 캐시는 유효
        os.utime(tmp / "meta_spid.json", (edge, edge))
        assert api._meta_read("spid") == [{"id": 1}], "갱신 기준 시각에 쓴 캐시를 버렸다"
        old = (nexon_api._week_boundary() - timedelta(seconds=1)).timestamp()
        os.utime(tmp / "meta_spid.json", (old, old))
        assert api._meta_read("spid") is None, "지난 갱신일 이전 메타를 그대로 썼다"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_week_boundary_is_refresh_weekday_midnight():
    import nexon_api
    wd = nexon_api.META_REFRESH_WEEKDAY
    base = datetime(2026, 10, 5)                                   # 월요일
    on_day = base + timedelta(days=(wd - base.weekday()) % 7, hours=15)
    assert nexon_api._week_boundary(on_day) == on_day.replace(hour=0)
    assert nexon_api._week_boundary(on_day - timedelta(days=1)) == (on_day - timedelta(days=7)).replace(hour=0)


# ── 랭킹 한 사람 읽기·구단가치 표기(변이 4순위) ─────────────────────────────────────────
_RANK_HTML = ('<div class="tr"><span class="td rank_no">4,500</span>'
              '<span class="lv"><span class="txt">3823</span></span>'
              '<span class="price" alt="9,356,900,000">93억 5,690만</span>'
              '<span class="td rank_r_win_point">3398.92</span>'
              '<span class="top">41.6%</span><span class="bottom">959<em>|</em>395<em>|</em>949</span>'
              '<span class="td team_color"><span class="inner">맨체스터  유나이티드 <small>(11명)</small></span></span></div>')


def _rank_from(html):
    orig_get, orig_on = ranker._session.get, config.WEB_DATA
    ranker._session.get = lambda *a, **k: _FakeRes(html)
    config.WEB_DATA = True
    try:
        return ranker.fetch_manager_rank("닉")
    finally:
        ranker._session.get, config.WEB_DATA = orig_get, orig_on


def test_fetch_manager_rank_reads_every_field():
    i = _rank_from(_RANK_HTML)
    assert (i.rank, i.level, i.team_value, i.team_value_text, i.elo) == (4500, 3823, 9356900000, "93억 5,690만", 3398.92)
    assert (i.win_rate, i.win, i.draw, i.lose) == ("41.6%", 959, 395, 949)
    assert i.team_color == "맨체스터 유나이티드" and i.ranked, i.team_color      # 겹친 공백 정리
    out = _rank_from('<div>순위 내 포함되어 있지 않습니다</div>')
    assert not out.ranked and out.team_color == "" and out.team_value == 0       # 랭킹 밖은 빈 값
    try:
        _rank_from('<div class="fc_logo_inspection">점검 진행 중</div>')
        raise AssertionError("점검 페이지를 '랭킹 밖'으로 읽었다")
    except ranker.RankerError:
        pass


def test_format_team_value_units():
    f = ranker.format_team_value
    assert f(9356900000) == "93억 5,690만"
    assert f(10 ** 16 + 9631 * 10 ** 12) == "1경 9,631조"
    assert f(3 * 10 ** 8) == "3억"                      # 아래 단위가 0이면 붙이지 않는다
    assert f(10 ** 4) == "1만" and f(9999) == "9,999"    # 단위 경계 딱 1만
    assert f(5 * 10 ** 4 + 7) == "5만"                   # 마지막 단위(만) 아래는 없다


# ── DB 필터·시즌표 갱신·이미지 캐시(변이 4순위) ─────────────────────────────────────────
def test_store_match_type_filters_and_season_staleness():
    import tempfile
    import shutil
    import store
    tmp = Path(tempfile.mkdtemp())
    try:
        conn = store.open_db(tmp / "t.db")
        ds = [_d(3, "승"), _d(2, "패"), _d(1, "무")]
        ds[0]["matchType"] = ds[1]["matchType"] = 52
        ds[2]["matchType"] = 50
        store.save_matches(conn, ds)
        assert store.match_count(conn, "me") == 3 and store.match_count(conn, "me", 52) == 2
        assert len(store.load_details(conn, "me")) == 3 and len(store.load_details(conn, "me", 50)) == 1
        assert store.known_ids(conn, "me", 50) == {ds[2]["matchId"]}
        a, b = store.date_range(conn, "me", 52)
        assert (a[:10], b[:10]) == ("2026-09-02", "2026-09-03"), (a, b)    # 50 경기(09-01)는 빠진다
        assert store.date_range(conn, "me")[0][:10] == "2026-09-01"
        # 시즌표: 비어 있으면 낡음 → 방금 저장하면 새것 → TTL 지나면 낡음
        # 봇 등록 해제 — 지운 게 없으면 False("해제했다"고 거짓으로 답하지 않게)
        assert store.clear_bot_user(conn, "방", "사람") is False
        store.set_bot_user(conn, "방", "사람", "닉")
        assert store.clear_bot_user(conn, "방", "사람") is True
        assert store.clear_bot_user(conn, "방", "사람") is False
        assert store.seasons_stale(conn)
        store.save_seasons(conn, [sn.Season(no=1, name="시즌 1", start=date(2026, 1, 1), end=date(2026, 3, 1))])
        assert not store.seasons_stale(conn)
        conn.execute("UPDATE seasons SET fetched_at = '2000-01-01T00:00:00'")
        assert store.seasons_stale(conn)
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_store_open_refreshes_query_stats():
    """통계가 없으면 계정별 경기 수 세기가 감독모드 경기 전체를 계정마다 훑는다(1만 경기·11계정 0.8초).
    open_db 가 PRAGMA optimize 로 통계를 채우는지 — 쌓인 뒤 다시 열 때 잰다."""
    import tempfile
    import shutil
    import sqlite3
    import store
    tmp = Path(tempfile.mkdtemp())
    try:
        conn = store.open_db(tmp / "t.db")
        store.save_matches(conn, [_d(3, "승"), _d(2, "패"), _d(1, "무")])
        conn.close()
        raw = sqlite3.connect(tmp / "t.db")   # 전제: 저장만으로는 통계가 생기지 않는다
        has = lambda c: c.execute("SELECT name FROM sqlite_master WHERE name='sqlite_stat1'").fetchone() and \
            c.execute("SELECT count(*) FROM sqlite_stat1 WHERE tbl='matches'").fetchone()[0]  # noqa: E731
        assert not has(raw), "저장만 했는데 통계가 있다 — 이 테스트가 open_db 를 재지 못한다"
        raw.close()
        conn = store.open_db(tmp / "t.db")
        assert has(conn), "open_db 가 통계를 채우지 않았다"
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_details_reads_in_date_order_without_sorting_payloads():
    """통계가 생기면 SQLite 는 계정 인덱스부터 골라 본문을 통째로 임시 정렬했다(1만 경기 SQL 0.7초 → 2.3초).
    load_details 가 (종류, 날짜) 인덱스로 읽는지 — 상대가 경기마다 다른 실제 모양으로 잰다."""
    import tempfile
    import shutil
    import store
    tmp = Path(tempfile.mkdtemp())
    try:
        conn = store.open_db(tmp / "t.db")
        ds = []
        for i in range(60):
            d = _d(1 + i % 28, "승", hour=i % 24, opp=f"상대{i}")
            d["matchType"] = 52
            if i >= 20:
                d["matchInfo"][0]["ouid"] = f"x{i % 7}"   # 다른 계정의 경기
            d["matchInfo"][1]["ouid"] = f"opp{i}"         # 상대는 경기마다 다르다
            ds.append(d)
        store.save_matches(conn, ds)
        conn.close()
        conn = store.open_db(tmp / "t.db")                 # 여기서 통계가 생긴다
        plain = ("SELECT m.payload FROM matches m JOIN match_players p ON p.match_id = m.match_id"
                 " WHERE p.ouid = ? AND m.match_type = ? ORDER BY m.match_date DESC")
        plan = lambda sql, args=(): " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, args))  # noqa: E731
        assert "TEMP B-TREE" in plan(plain, ("me", 52)), "전제: 인덱스를 안 정하면 임시 정렬이 나와야 한다"
        ran = []
        conn.set_trace_callback(ran.append)
        got = store.load_details(conn, "me", 52)
        conn.set_trace_callback(None)
        sql = next(s for s in ran if "payload" in s)
        assert "TEMP B-TREE" not in plan(sql), plan(sql)
        assert len(got) == 20 and [d["matchDate"] for d in got] == sorted(
            (d["matchDate"] for d in ds[:20]), reverse=True)
        # 멈추기 — stop 이 참이면 거기서 끊고 읽은 데까지만(미리 읽기를 버릴 때)
        n = []
        assert store.load_details(conn, "me", 52, stop=lambda: n.append(1) or len(n) > 5) == got[:5]
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_image_fetch_cache_and_failures():
    import tempfile
    import shutil
    import images
    import requests

    class _Img:
        def __init__(self, status, content):
            self.status_code, self.content = status, content

    tmp = Path(tempfile.mkdtemp())
    calls = []

    def respond(status, content):
        def get(url, timeout=None, headers=None):
            calls.append(url)
            if status is None:
                raise requests.ConnectionError("끊김")
            return _Img(status, content)
        return get

    orig = images._session.get
    try:
        cases = [  # (함수, 인자)
            (images.fetch, (101,)),
            (images.fetch_division_icon, (3,)),
            (images.fetch_season_icon, (201, "https://x/s.png")),
            (images.fetch_url, ("https://x/a.png",)),
        ]
        for fn, args in cases:
            for status, content in ((404, b"x"), (200, b""), (None, b"")):   # 실패는 전부 None, 파일 없음
                images._session.get = respond(status, content)
                assert fn(*args, tmp) is None, (fn.__name__, status, content)
            images._session.get = respond(200, b"PNG")
            p = fn(*args, tmp)
            assert p is not None and p.read_bytes() == b"PNG", fn.__name__
            n = len(calls)
            images._session.get = respond(500, b"")                        # 캐시가 있으면 묻지 않는다
            assert fn(*args, tmp) == p and len(calls) == n, fn.__name__
        assert images.fetch_url("", tmp) is None
    finally:
        images._session.get = orig
        shutil.rmtree(tmp, ignore_errors=True)


# ── 설정 — exe 로 묶였을 때의 경로·데이터 폴더·옛 위치 이관(변이 4순위) ──────────────────
def test_config_paths_when_frozen_and_data_dir_order():
    import tempfile
    import shutil
    saved = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None), sys.executable,
             os.environ.get("FIFA_DATA_DIR"), os.environ.get("LOCALAPPDATA"))
    tmp = Path(tempfile.mkdtemp())
    try:
        sys.frozen, sys._MEIPASS, sys.executable = True, str(tmp / "_internal"), str(tmp / "app.exe")
        assert config._root() == tmp.resolve(), config._root()                 # exe 옆
        assert config.asset_path("a.ico") == tmp / "_internal" / "a.ico"        # 묶인 리소스는 _MEIPASS
        del sys._MEIPASS
        assert config.asset_path("a.ico") == config.ROOT / "a.ico"
        del sys.frozen
        assert config._root() == Path(config.__file__).resolve().parent         # 소스 실행은 소스 폴더
        os.environ["FIFA_DATA_DIR"] = str(tmp / "d")
        assert config._data_dir() == tmp / "d"                                  # 덮어쓰기가 먼저
        os.environ.pop("FIFA_DATA_DIR")
        os.environ["LOCALAPPDATA"] = str(tmp / "local")
        assert config._data_dir() == tmp / "local" / config.DATA_DIR_NAME
        os.environ.pop("LOCALAPPDATA")
        assert config._data_dir() == Path.home() / f".{config.DATA_DIR_NAME}"   # 윈도우가 아닐 때
    finally:
        for attr, v in (("frozen", saved[0]), ("_MEIPASS", saved[1])):
            if v is None:
                if hasattr(sys, attr):
                    delattr(sys, attr)
            else:
                setattr(sys, attr, v)
        sys.executable = saved[2]
        for k, v in (("FIFA_DATA_DIR", saved[3]), ("LOCALAPPDATA", saved[4])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def test_migrate_moves_only_missing_files():
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp())
    saved = config.ROOT, config.DATA_DIR
    try:
        config.ROOT, config.DATA_DIR = tmp / "src", tmp / "data"
        (tmp / "src").mkdir()
        (tmp / "data").mkdir()
        (tmp / "src" / ".env").write_text("옛", encoding="utf-8")
        (tmp / "src" / "fifa.db").write_text("옛db", encoding="utf-8")
        (tmp / "data" / "fifa.db").write_text("정본", encoding="utf-8")   # 이미 있으면 그쪽이 정본
        assert config._migrate_from_source() == [".env"]
        assert (tmp / "data" / ".env").read_text(encoding="utf-8") == "옛" and not (tmp / "src" / ".env").exists()
        assert (tmp / "data" / "fifa.db").read_text(encoding="utf-8") == "정본"
        assert (tmp / "src" / "fifa.db").exists(), "정본이 있는데 옛 DB 를 옮겨 덮었다"
    finally:
        config.ROOT, config.DATA_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_stat_color_bucket_edges():
    import playerinfo as pi
    floors = [f for f, _ in pi.STAT_COLOR_BUCKETS]
    for i, f in enumerate(floors[:-1]):                     # 딱 하한이면 그 색, 하나 아래면 다음 색
        assert pi.stat_color(f) == pi.STAT_COLOR_BUCKETS[i][1], f
        assert pi.stat_color(f - 1) == pi.STAT_COLOR_BUCKETS[i + 1][1], f - 1
    assert pi.stat_color(-5) == pi.STAT_COLOR_BUCKETS[-1][1]


# ── 선수 카드 파싱 — 실제 페이지(2026-10-03 받음, 슈바인슈타이거 WS)로 고정 ──────────────────
# 지어낸 HTML 은 정규식을 베낀 테스트가 된다 — 넥슨이 페이지를 바꾸면 이 자료를 새로 받는다.
def _pi_parse(kind):
    import playerinfo as pi
    html = (Path(_DIR) / f"{kind}_848121944.html").read_text(encoding="utf-8")

    class _R:
        text = html

        def raise_for_status(self):
            pass

    saved = (pi._session.get, pi._session.post, config.WEB_DATA)
    pi._session.get = pi._session.post = lambda *a, **k: _R()
    config.WEB_DATA = True
    try:
        return pi.fetch_player_info(848121944) if kind == "playerinfo" else pi.fetch_player_ability(848121944)
    finally:
        pi._session.get, pi._session.post, config.WEB_DATA = saved


def test_player_info_real_page():
    i = _pi_parse("playerinfo")
    assert (i.name, i.position, i.ovr, i.nation) == ("슈바인슈타이거", "CM", 119, "독일"), vars(i)
    assert (i.height, i.weight, i.body_type, i.strong_foot, i.weak_foot) == ("183cm", "79kg", "보통", "R5", "L3")
    assert (i.fame, i.skill_moves, i.skill_moves_max) == ("월드클래스", 3, 6)
    assert i.photo_url.startswith("https://") and "848121944" in i.photo_url
    assert i.nation_flag_url.endswith("/21.png") and i.season_icon_url.endswith("/WS.png")
    assert len(i.abilities) >= 30 and i.abilities["속력"] == 115 and i.abilities["슛 파워"] == 124
    assert i.prices[1] == "308,000 BP" and i.prices[0] == "-"
    assert i.traits and i.traits[0].name == "중거리 슛 선호" and i.traits[0].icon_url.endswith(".png")
    assert i.club_history[0].period == "2017 ~ 2019" and i.club_history[0].club == "시카고 파이어 FC"
    assert i.group_stats() == {"스피드": 114, "슛": 114, "패스": 121, "드리블": 118, "수비": 118, "피지컬": 118}


def test_player_ability_real_page():
    s = _pi_parse("playerability")
    assert s.ovr == 119
    # '드리블'은 요약과 개별 능력치에 같은 이름 — 요약은 첫 번째(118), 개별은 120
    assert s.groups == {"스피드": 114, "슛": 115, "패스": 120, "드리블": 118, "수비": 117, "피지컬": 118}, s.groups
    assert s.abilities["드리블"] == 120 and s.abilities["속력"] == 115
    assert (1010, "바이에른 뮌헨") in s.club_options and (0, "") not in s.club_options
    assert s.feature_options == [(20011, "독일 황금세대"), (40189, "바이언 첫번째 트레블")]
    assert s.enhance_options == [] and s.club_levels == []
    assert len(s.position_ovrs) == 16 and s.position_ovrs["CM"] == 119 and s.position_ovrs["GK"] == 33


def test_player_parsing_missing_parts():
    import playerinfo as pi
    assert pi._selector_items("<div>목록 없음</div>", "tdefault_wrap") == []
    # 능력치가 없는 분류는 평균을 내지 않는다(0 으로 나누거나 0 으로 보이지 않게)
    assert pi.PlayerInfo(sp_id=1, abilities={"속력": 100, "가속력": 90}).group_stats() == {"스피드": 95}
    assert pi.PlayerInfo(sp_id=1).group_stats() == {}


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:  # 픽스처 누락 등
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
