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

from datetime import date, datetime

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
