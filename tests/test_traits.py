"""포지션 특성(32단계 단계 1 · traitcollect.py · trait_codes.py) 테스트 — 가짜 응답·가짜 세션, 네트워크 없이.

pytest 없이 `python tests/test_traits.py`. 실제 넥슨에 붙는 스모크는 `python check_api.py <닉네임>` 의 특성 칸 줄.
픽스처 `squadmaker_team.json.gz` 는 실제 응답 한 칸(구단주명·번호·스쿼드 이름·메모를 지운 것) — 선수 spid·특성·코치·강화·state 는 원본 그대로.
이 칸에는 `traits` 가 `[-2]` 한 칸짜리인 카드 3장, 코치를 안 넣은 선발, 코치를 넣은 교체 선수가 모두 있다(실응답의 모양을 다 거친다).
"""
from __future__ import annotations

import copy
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import config
import trait_codes as tcodes
import traitcollect as tc
import watchdog

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "squadmaker_team.json.gz")


def _fixture() -> dict:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as f:
        return json.load(f)


def _cell(spids: list[int], *, state_of=None, build=11, traits=(28, 55, 13), trainer=(2, 12, 42)) -> dict:
    """지어낸 팀 칸 응답 — spids 순서대로 선수, 앞 11명이 선발(state_of 로 바꿀 수 있다)."""
    players = []
    for i, spid in enumerate(spids):
        p = {"spid": spid, "state": 1 if i < 11 else 0, "buildUp": build, "traits": list(traits), "trainer": list(trainer)}
        if state_of is not None:
            p["state"] = state_of(i)
        players.append(p)
    return {"ResultCode": 1, "ResultMsg": "성공", "ResultData": {"Squadinfo": {"players": players}}}


def _starters(spids: list[int], grade=11) -> list[tc.Starter]:
    return [(s, i, grade) for i, s in enumerate(spids)]


MATCH = list(range(1000, 1011))                    # 경기 선발 11 spid
OTHER = list(range(2000, 2011))
BENCH = list(range(3000, 3007))


# ── 이름표 ───────────────────────────────────────────────────────────────────

def test_trait_names():
    assert tcodes.name_of("normal", 5) == "강철몸"
    assert tcodes.name_of("new", 50) == "아크로바틱 피니셔"
    assert tcodes.name_of("train", 0) == "근거리 슛 향상"
    assert tcodes.name_of("coach", 0) == "근거리 슛 향상"          # 훈련 특성과 코치는 같은 표
    assert (len(tcodes.NORMAL), len(tcodes.NEW)) == (31, 17)      # 번들의 5강 31 · 8강 17
    # 종류가 다르면 같은 숫자도 다른 이름 — 28 은 일반에선 "승부욕", 훈련에선 "침투 요청에 즉각 반응"
    assert tcodes.name_of("normal", 28) != tcodes.name_of("train", 28)


def test_trait_names_unknown_and_empty():
    assert tcodes.name_of("normal", 999) == "코드 999"            # 모르는 코드 — 예외 없음
    assert tcodes.name_of("coach", 12345) == "코드 12345"
    assert tcodes.name_of("nonsense", 5) == "코드 5"
    assert tcodes.name_of("new", -1) is None and tcodes.name_of("new", -2) is None   # 빈칸·잠김은 이름이 없다
    assert tcodes.name_of("coach", -7) is None
    for bad in (None, "5", 5.0, True, [5]):
        assert tcodes.name_of("normal", bad) is None, bad
    assert tcodes.is_open_slot(0) and tcodes.is_open_slot(50)
    assert not tcodes.is_open_slot(-1) and not tcodes.is_open_slot(-2) and not tcodes.is_open_slot(None)
    assert not tcodes.is_open_slot(True)


def test_trait_codes_cover_fixture():
    cards = tc.parse_team(_fixture())
    assert cards
    seen = 0
    for c in cards:
        for kind, code in zip(("normal", "new", "train"), c.traits):
            if tcodes.is_open_slot(code):
                seen += 1
                assert not tcodes.name_of(kind, code).startswith("코드"), (kind, code)
        for code in c.trainer or ():
            if tcodes.is_open_slot(code):
                seen += 1
                assert not tcodes.name_of("coach", code).startswith("코드"), code
    assert seen > 30, seen   # 이름표를 한 번도 안 찾았으면 위 단언이 공허하다


# ── 응답 해석 ────────────────────────────────────────────────────────────────

def test_parse_team_fixture():
    cards = tc.parse_team(_fixture())
    assert len(cards) == 18 and sum(c.state == 1 for c in cards) == 11
    first = cards[0]
    assert (first.spid, first.state, first.build_up, first.traits, first.trainer) == \
        (863204935, 1, 11, (43, 68, 5), (1, 3, 7))
    # `traits: [-2]` 한 칸짜리 카드는 길이 3 으로 펴진다 — 형식 이상이 아니다
    assert next(c for c in cards if c.spid == 861230025).traits == (-2, -2, -2)
    # 코치를 안 넣은 카드는 trainer 키가 없다 → None
    assert next(c for c in cards if c.spid == 851262118).trainer is None
    # 교체 선수도 코치를 가질 수 있다
    assert next(c for c in cards if c.spid == 851224371).trainer == (2, 12, 42)
    raw = _fixture()["ResultData"]["Squadinfo"]["players"]
    assert sum("trainer" not in p for p in raw) == sum(c.trainer is None for c in cards)


def test_trait_slots_ordered_by_table():
    # 칸0 에 신규(8강) 코드·칸1 에 일반(5강) 코드가 오면 바꿔 읽는다(번들 yv) — 위치만 믿으면 이름이 엉뚱하게 나온다
    d = copy.deepcopy(_fixture())
    d["ResultData"]["Squadinfo"]["players"][0]["traits"] = [68, 43, 5]
    assert tc.parse_team(d)[0].traits == (43, 68, 5)
    d["ResultData"]["Squadinfo"]["players"][0]["traits"] = [43, 68, 5]          # 보통 순서는 그대로
    assert tc.parse_team(d)[0].traits == (43, 68, 5)
    d["ResultData"]["Squadinfo"]["players"][0]["traits"] = [-1, 68, -2]         # 제 순서 증거(칸1 신규)만 — 그대로
    assert tc.parse_team(d)[0].traits == (-1, 68, -2)
    d["ResultData"]["Squadinfo"]["players"][0]["traits"] = [68, -1, -2]         # 거꾸로 증거만(칸0 신규) — 바꾼다
    assert tc.parse_team(d)[0].traits == (-1, 68, -2)
    d["ResultData"]["Squadinfo"]["players"][0]["traits"] = [-1, 43, -2]         # 칸1 에 일반 코드 — 바꾼다
    assert tc.parse_team(d)[0].traits == (43, -1, -2)


def test_parse_team_blank_and_codes():
    assert tc.parse_team({"ResultCode": -1, "ResultMsg": "x"}) is None        # 저장 안 된 칸 — 정상
    for bad in ({"ResultCode": 0}, {"ResultCode": -105}, {"ResultCode": "1"}, {}, [], None, "x"):
        try:
            tc.parse_team(bad)
        except tc.TraitError as e:
            assert e.kind == "format", (bad, e.kind)
        else:
            raise AssertionError(f"형식 이상을 못 잡았다: {bad!r}")


def test_parse_team_structure_missing_field_is_format():
    def broken(mutate):
        d = copy.deepcopy(_fixture())
        mutate(d)
        try:
            tc.parse_team(d)
        except tc.TraitError as e:
            return e.kind
        return None

    def players(d):
        return d["ResultData"]["Squadinfo"]["players"]

    cases = {
        "players 없음": lambda d: d["ResultData"]["Squadinfo"].pop("players"),
        "Squadinfo 없음": lambda d: d["ResultData"].pop("Squadinfo"),
        "ResultData 없음": lambda d: d.pop("ResultData"),
        "players 가 목록 아님": lambda d: d["ResultData"]["Squadinfo"].__setitem__("players", {}),
        "spid 없음": lambda d: players(d)[3].pop("spid"),
        "state 없음": lambda d: players(d)[3].pop("state"),
        "state 2": lambda d: players(d)[3].__setitem__("state", 2),
        "state True": lambda d: players(d)[3].__setitem__("state", True),
        "traits 없음": lambda d: players(d)[3].pop("traits"),
        "traits 길이 2": lambda d: players(d)[3].__setitem__("traits", [1, 2]),
        "traits 길이 1 인데 -2 아님": lambda d: players(d)[3].__setitem__("traits", [5]),
        "traits 값이 글자": lambda d: players(d)[3].__setitem__("traits", [1, "2", 3]),
        "선수가 객체 아님": lambda d: players(d).__setitem__(3, 7),
    }
    for name, mutate in cases.items():
        assert broken(mutate) == "format", name
    # 필수가 아닌 칸이 이상해도 오류가 아니다 — trainer 모양이 이상하면 코치 없음
    d = copy.deepcopy(_fixture())
    players(d)[0]["trainer"] = [1, 2]
    assert tc.parse_team(d)[0].trainer is None
    d = copy.deepcopy(_fixture())
    del players(d)[0]["buildUp"]
    assert tc.parse_team(d)[0].build_up is None


def test_parse_owner():
    assert tc.parse_owner({"ResultCode": 1, "ResultData": {"sn": 123}}) == 123
    assert tc.parse_owner({"ResultCode": -1, "ResultData": None}) is None     # 없는 닉네임
    try:
        tc.parse_owner({"ResultCode": 1, "ResultData": {}})
    except tc.TraitError as e:
        assert e.kind == "format"
    else:
        raise AssertionError("sn 없는 성공 응답을 못 잡았다")


# ── 판정 ─────────────────────────────────────────────────────────────────────

def _fetcher(cells: dict[tuple[int, int], dict | None]):
    """팀 → 응답 dict(또는 None=빈칸). 부른 순서를 기록한다."""
    calls: list[tuple[int, int]] = []

    def fetch(tt: int, part: int):
        calls.append((tt, part))
        return tc.parse_team(cells.get((tt, part)) or {"ResultCode": -1})
    fetch.calls = calls
    return fetch


def test_trait_find_stops_at_first_matching_team():
    cells = {(1, 0): _cell(OTHER + BENCH), (1, 1): _cell(MATCH + BENCH), (1, 2): _cell(MATCH + BENCH),
             (0, 0): _cell(MATCH + BENCH)}
    fetch = _fetcher(cells)
    res = tc.find_team(_starters(MATCH), fetch)
    assert res.state == "ok" and res.pick.team == (1, 1)
    assert res.calls == 2 and fetch.calls == [(1, 0), (1, 1)]    # 맞는 팀을 찾으면 거기서 멈춘다 — 뒤 칸은 안 부른다
    assert len(res.pick.cards) == 11 and res.pick.dropped == 0
    c = res.pick.cards[0]
    assert (c.spid, c.position, c.grade, c.traits, c.trainer) == (1000, 0, 11, (28, 55, 13), (2, 12, 42))


def test_trait_find_stale_when_no_team_matches():
    cells = {(1, 0): _cell(OTHER + BENCH), (1, 2): _cell(OTHER + BENCH), (0, 1): _cell(MATCH[:10] + [9999] + BENCH)}
    fetch = _fetcher(cells)
    res = tc.find_team(_starters(MATCH), fetch)
    assert res.state == "stale" and res.pick is None
    assert res.calls == 6 and len(fetch.calls) == 6              # 6칸을 다 봤다
    assert res.blank == 3                                        # 저장 안 된 칸 셋
    assert tc.find_team([], fetch).state == "stale"              # 경기 선발이 없으면 아무 팀도 맞지 않는다


def test_trait_team_needs_starters_not_bench():
    # 경기 선발 카드가 칸의 교체 명단에 있는 팀은 그 팀이 아니다 — 전체 명단으로 보면 첫 팀이 맞는다고 오판한다
    wrong = _cell(OTHER + MATCH)               # 선발 11 = OTHER · 교체 = 경기 선발 11 카드 전부
    right = _cell(MATCH + BENCH)
    fetch = _fetcher({(1, 0): wrong, (1, 1): right})
    res = tc.find_team(_starters(MATCH), fetch)
    assert res.state == "ok" and res.pick.team == (1, 1), res
    assert tc.team_matches(_starters(MATCH), tc.parse_team(wrong)) is False
    assert tc.team_matches(_starters(MATCH), tc.parse_team(right)) is True


def test_trait_grade_mismatch_dropped():
    cell = _cell(MATCH + BENCH, build=11)
    cell["ResultData"]["Squadinfo"]["players"][4]["buildUp"] = 9     # 경기 뒤 강화를 바꾼 카드
    starters = _starters(MATCH, grade=11)
    res = tc.find_team(starters, _fetcher({(1, 0): cell}))
    assert res.state == "ok"                                         # 강화가 달라도 팀은 맞다(카드 집합으로 가른다)
    assert res.pick.dropped == 1 and len(res.pick.cards) == 10
    assert MATCH[4] not in {c.spid for c in res.pick.cards}          # 그 카드만 빠진다
    # 경기 쪽 강화를 모르면(None) 비교할 수 없으니 뺀다
    none_grade = [(s, p, None) for s, p, _ in starters]
    assert tc.find_team(none_grade, _fetcher({(1, 0): _cell(MATCH + BENCH)})).pick.cards == []


def test_trait_pick_keeps_trainer_none():
    cell = _cell(MATCH + BENCH)
    for p in cell["ResultData"]["Squadinfo"]["players"][:3]:
        del p["trainer"]
    res = tc.find_team(_starters(MATCH), _fetcher({(1, 0): cell}))
    assert [c.trainer for c in res.pick.cards[:4]] == [None, None, None, (2, 12, 42)]


def test_trait_position_comes_from_match():
    # 칸의 role·formation 글자는 안 읽는다 — 자리는 경기의 spPosition 그대로
    cell = _cell(MATCH + BENCH)
    for p in cell["ResultData"]["Squadinfo"]["players"]:
        p["role"] = "zz"
    cell["ResultData"]["Squadinfo"]["formation"] = "9-9-9"
    starters = [(s, 20 + i, 11) for i, s in enumerate(MATCH)]
    res = tc.find_team(starters, _fetcher({(1, 0): cell}))
    assert [c.position for c in res.pick.cards] == list(range(20, 31))


def test_trait_team_order_comes_from_config():
    # 배선 — 기본값이 아닌 순서로 잰다(기본값이면 하드코딩이어도 통과한다)
    saved = config.TRAIT_TEAM_ORDER
    config.TRAIT_TEAM_ORDER = ((0, 2), (0, 0))
    try:
        fetch = _fetcher({})
        res = tc.find_team(_starters(MATCH), fetch)
        assert fetch.calls == [(0, 2), (0, 0)] and res.calls == 2 and res.state == "stale"
    finally:
        config.TRAIT_TEAM_ORDER = saved
    fetch = _fetcher({})
    tc.find_team(_starters(MATCH), fetch, order=[(0, 1)])
    assert fetch.calls == [(0, 1)]                                  # order 인자가 설정보다 먼저


def test_trait_find_propagates_fetch_errors():
    def fetch(tt, part):
        raise tc.TraitError("막힘", "rate")
    try:
        tc.find_team(_starters(MATCH), fetch)
    except tc.TraitError as e:
        assert e.kind == "rate"
    else:
        raise AssertionError("fetch 오류가 삼켜졌다")


# ── 요청 ─────────────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body or {})
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.posts = resp, exc, []

    def post(self, url, **kw):
        self.posts.append((url, kw))
        if self.exc:
            raise self.exc
        return self.resp


def _fetch_with(sess, **kw):
    saved = config.WEB_DATA
    config.WEB_DATA = True
    try:
        return tc.fetch_team(sess, 1234, 1, 0, **kw)
    finally:
        config.WEB_DATA = saved


def _kind(sess) -> str:
    try:
        _fetch_with(sess)
    except tc.TraitError as e:
        assert str(e) and e.kind in tc.MESSAGES, e.kind
        return e.kind
    raise AssertionError("오류가 안 났다")


def test_fetch_team_error_kinds():
    assert _kind(FakeSession(FakeResp(403, text="forbidden"))) == "rate"
    assert _kind(FakeSession(FakeResp(429, text="slow down"))) == "rate"
    assert _kind(FakeSession(FakeResp(500, text="oops"))) == "down"
    assert _kind(FakeSession(FakeResp(503, text="oops"))) == "down"
    assert _kind(FakeSession(FakeResp(302, text=""))) == "down"       # 넘김은 따라가지 않는다
    assert _kind(FakeSession(exc=requests.ConnectionError("끊김"))) == "down"
    assert _kind(FakeSession(exc=requests.Timeout("느림"))) == "down"
    assert _kind(FakeSession(FakeResp(200, text="<html>Just a moment...</html>"))) == "rate"     # Cloudflare 확인
    assert _kind(FakeSession(FakeResp(200, text="<html>cf-chl-bypass</html>"))) == "rate"
    assert _kind(FakeSession(FakeResp(200, headers={"cf-mitigated": "challenge"}, text="x"))) == "rate"
    assert _kind(FakeSession(FakeResp(200, text="<html>이상한 페이지</html>"))) == "format"          # JSON 이 아님
    assert _kind(FakeSession(FakeResp(200, {"ResultCode": 1, "ResultData": {"Squadinfo": {}}}))) == "format"
    assert _kind(FakeSession(FakeResp(200, {"ResultCode": 77}))) == "format"


def test_fetch_team_blank_and_ok():
    assert _fetch_with(FakeSession(FakeResp(200, {"ResultCode": -1, "ResultMsg": "없음"}))) is None
    cards = _fetch_with(FakeSession(FakeResp(200, _fixture())))
    assert len(cards) == 18


def test_fetch_team_request_shape():
    sess = FakeSession(FakeResp(200, {"ResultCode": -1}))
    saved = config.TRAIT_TACTIC_SEQ
    config.TRAIT_TACTIC_SEQ = 4                                        # 기본값(0)이 아닌 값 — 배선을 잰다
    try:
        _fetch_with(sess)
    finally:
        config.TRAIT_TACTIC_SEQ = saved
    (url, kw), = sess.posts
    assert url == tc.URL and url.startswith("https://fconline.nexon.com/")
    assert kw["data"] == {"strMethod": "getingameinfo", "n1TeamType": 1, "n1TeamPart": 0,
                          "n4TeamSeq": 4, "n8TargetNexonSN": 1234}
    assert kw["headers"].get("X-Requested-With") == "XMLHttpRequest"
    assert kw["allow_redirects"] is False and kw["timeout"] == config.TRAIT_TIMEOUT_S


def test_fetch_owner_sn():
    saved = config.WEB_DATA
    config.WEB_DATA = True
    try:
        sess = FakeSession(FakeResp(200, {"ResultCode": 1, "ResultData": {"sn": 555}}))
        assert tc.fetch_owner_sn(sess, "닉") == 555
        assert sess.posts[0][1]["data"] == {"strMethod": "getownerinfo", "strCharacterName": "닉"}
        assert tc.fetch_owner_sn(FakeSession(FakeResp(200, {"ResultCode": -1})), "없는닉") is None
    finally:
        config.WEB_DATA = saved


def test_fetch_off_sends_nothing():
    saved = config.WEB_DATA
    config.WEB_DATA = False
    try:
        for call in (lambda s: tc.fetch_team(s, 1, 1, 0), lambda s: tc.fetch_owner_sn(s, "닉")):
            sess = FakeSession(FakeResp(200, {"ResultCode": -1}))
            try:
                call(sess)
            except tc.TraitError as e:
                assert e.kind == "off" and str(e) == config.WEB_DATA_OFF_MSG
            else:
                raise AssertionError("꺼졌는데 예외가 없다")
            assert sess.posts == [], "꺼졌는데 요청을 보냈다"
    finally:
        config.WEB_DATA = saved


def test_trait_session_names_the_app():
    ua = tc.new_session().headers.get("User-Agent", "")
    assert ua == config.WEB_USER_AGENT and "Mozilla" not in ua, ua


def test_trait_messages_cover_every_kind():
    assert set(tc.MESSAGES) == {"off", "down", "format", "rate"}
    assert all(isinstance(m, str) and m for m in tc.MESSAGES.values())


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
