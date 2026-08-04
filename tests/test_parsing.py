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

import models
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
