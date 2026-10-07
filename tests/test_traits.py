"""포지션 특성(32단계 · traitcollect.py · trait_codes.py · store.trait_squads) 테스트 — 가짜 응답·가짜 세션, 네트워크 없이.

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
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import config
import store
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


# ── 저장 · 지우기(단계 2 — store.trait_squads) ──────────────────────────────────

TODAY = date(2026, 10, 7)
ON_AT = "2026-10-01T09:00:00.000000"


def _file_db():
    d = Path(tempfile.mkdtemp())
    return d, store.open_db(d / "fifa.db")


def _real_body() -> list:
    """실응답 픽스처의 선발 11 — 실제 크기·모양(코치 없는 카드 포함)."""
    starters = [c for c in tc.parse_team(_fixture()) if c.state == 1][:11]
    return [[c.spid, i, c.build_up, list(c.traits), list(c.trainer) if c.trainer else None] for i, c in enumerate(starters)]


def _save(conn, sn, *, checked=TODAY, on_at=ON_AT, rank=1, nick_mark=None):
    body = _real_body()
    if nick_mark:   # 파일 바이트 검사용 표시(실제 줄엔 닉네임이 없다 — 다른 칸에 실어 지운 뒤 남는지 본다)
        body[0].append(nick_mark)
    store.save_trait_squad(conn, sn, source=store.PICK if rank <= 200 else store.TRAIT, rank=rank, match_id=f"m{sn}",
                           formation="4-2-3-1", team="1-0", body=body, state="ok", collect_on_at=on_at,
                           match_day=checked.isoformat(), checked_at=f"{checked.isoformat()}T10:00:00")


def test_trait_row_roundtrip():
    conn = store.open_db(":memory:")
    _save(conn, 7)
    row = store.trait_squads(conn)[7]
    assert row["body"] == _real_body() and row["state"] == "ok" and row["collect_on_at"] == ON_AT, row
    assert any(c[4] is None for c in row["body"]), "코치 없는 카드(null)가 픽스처에 있어야 — 없으면 이 왕복이 그 모양을 안 잰다"
    _save(conn, 7, rank=300)                                  # 한 사람 한 줄(덮어씀)
    assert len(store.trait_squads(conn)) == 1 and store.trait_squads(conn)[7]["rank"] == 300
    conn.execute("UPDATE trait_squads SET body = '[1,' WHERE profile_sn = 7")
    conn.commit()
    assert store.trait_squads(conn)[7]["body"] == [], "깨진 body 한 줄에 읽기가 죽었다"


def test_trait_row_size():
    """계획: 한 줄 약 0.5KB · 500명 약 0.25MB — 실응답 11장으로 잰다(상한은 그 2배)."""
    conn = store.open_db(":memory:")
    _save(conn, 123456789)
    size = conn.execute("SELECT LENGTH(body) + LENGTH(collect_on_at) + LENGTH(checked_at) + LENGTH(match_id)"
                        " + LENGTH(formation) + LENGTH(team) + 40 FROM trait_squads").fetchone()[0]
    assert 200 < size < 1000, size


def test_trait_purge_on_every_off_path():
    """D1·D2 — 끄는 길 전부와 [수집 기록 지우기]는 purge_ranker_data(everything) 를 거친다(길은 화면 스모크
    test_every_off_path_purges_ranker_pick) → 특성 줄도 전부, 파일 바이트까지(secure_delete · WAL 비우기)."""
    d, conn = _file_db()
    for sn in (1, 2, 3):
        _save(conn, sn, nick_mark=f"특성표시{sn}")
    raw = (d / "fifa.db").read_bytes() + (d / "fifa.db-wal").read_bytes()
    assert "특성표시2".encode() in raw, "지우기 전에 있어야 — 없으면 아래 단언이 빈 검사"
    store.purge_ranker_data(conn, everything=False, today=TODAY)
    assert len(store.trait_squads(conn)) == 3, "14일 정리(everything=False)가 특성 줄을 지웠다"
    store.purge_ranker_data(conn, everything=True, today=TODAY)
    assert store.trait_squads(conn) == {}
    conn.close()
    raw = (d / "fifa.db").read_bytes()
    wal = d / "fifa.db-wal"
    raw += wal.read_bytes() if wal.exists() else b""
    assert "특성표시".encode() not in raw, "지운 특성 줄이 파일에 남았다"


def test_trait_prune_14d():
    conn = store.open_db(":memory:")
    _save(conn, 1, checked=TODAY - timedelta(days=config.RANK_RAW_KEEP_DAYS))
    _save(conn, 2, checked=TODAY - timedelta(days=config.RANK_RAW_KEEP_DAYS + 1))
    assert store.prune_trait_squads(conn, on_at=ON_AT, today=TODAY) == 1
    assert sorted(store.trait_squads(conn)) == [1], "14일째는 남고 15일째는 지운다"


def test_trait_prune_out_of_range():
    conn = store.open_db(":memory:")
    for sn in (1, 2, 3):
        _save(conn, sn)
    assert store.prune_trait_squads(conn, on_at=ON_AT, keep={1, 3, 99}, today=TODAY) == 1
    assert sorted(store.trait_squads(conn)) == [1, 3]


def test_trait_prune_skips_without_snapshot():
    """목록이 비면(원본 스냅숏 없음 · rank.db 못 엶) 순위 정리를 건너뛴다 — 안 그러면 특성 줄이 전부 지워진다(검토 3회차)."""
    conn = store.open_db(":memory:")
    for sn in (1, 2):
        _save(conn, sn)
    assert store.prune_trait_squads(conn, on_at=ON_AT, keep={99}, today=TODAY) == 2, "keep 이 순위 정리를 하는지 먼저"
    for sn in (1, 2):
        _save(conn, sn)
    for keep in (None, set()):
        assert store.prune_trait_squads(conn, on_at=ON_AT, keep=keep, today=TODAY) == 0, keep
    assert len(store.trait_squads(conn)) == 2


def test_trait_purge_when_reenabled_in_old_version():
    """D5 ② — 줄에 적힌 켠 시각이 지금 값과 다르면 지운다(옛 버전에서 껐다 다시 켠 경우 — 꺼짐 판정만으론 놓친다).
    둘 다 None 이면 같음(켠 시각을 안 적는 옛 버전에서 켠 사람)."""
    conn = store.open_db(":memory:")
    _save(conn, 1, on_at=ON_AT)
    _save(conn, 2, on_at=None)
    assert store.prune_trait_squads(conn, on_at=ON_AT, today=TODAY) == 1
    assert sorted(store.trait_squads(conn)) == [1]
    _save(conn, 2, on_at=None)
    assert store.prune_trait_squads(conn, on_at="2026-10-07T08:00:00.000000", today=TODAY) == 2, "다시 켰는데 남았다"
    _save(conn, 3, on_at=None)
    assert store.prune_trait_squads(conn, on_at=None, today=TODAY) == 0, "둘 다 None 인데 지웠다"


# ── 단계 3 — 받기(오픈API 대상별 주기·몫 · 웹 · 한 바퀴) ─────────────────────────
# 오픈API 쪽 가짜(FakeAPI · 지어낸 경기)는 test_rankerpick 것을 그대로 — 랭커 i 의 선발은 _squad(7000 + i % 3) · 강화 1 + spid % 8

import test_rankerpick as trp  # noqa: E402

NOW = trp.NOW
DAY = NOW.date().isoformat()


def _ranked(ranks) -> tuple[list[dict], "trp.FakeAPI"]:
    """순위마다 랭커 하나 — sn·닉네임·ouid·경기 id 가 순위로 정해져 구간을 섞어도 안 겹친다. 선발 = _squad_of."""
    targets, players = [], {}
    for rank in ranks:
        nick, ouid = f"랭커{rank}", f"r{rank}"
        targets.append({"rank": rank, "profile_sn": 5000 + rank, "nickname": nick, "team_color": "팀A", "formation": "4-1-2-3"})
        players[nick] = (ouid, trp._match(f"rm{rank}", NOW.date(), [(ouid, nick, _squad_of(rank)),
                                                                   (f"x{rank}", f"상대{rank}", trp._squad(8000))]))
    return targets, trp.FakeAPI(players)


def _squad_of(rank: int) -> list[int]:
    return trp._squad(7000 + 100 * (rank % 3))


def _targets(n: int, start_rank: int = 1):
    return _ranked(range(start_rank, start_rank + n))


class _Web:
    """가짜 스쿼드메이커 — sn → {팀: 칸}. 칸이 없으면 빈칸(None). calls 에 (sn, 팀). errors: 순번(1부터) → TraitError."""

    def __init__(self):
        self.cells: dict[int, dict[tuple[int, int], list]] = {}
        self.calls: list[tuple[int, tuple[int, int]]] = []
        self.errors: dict[int, Exception] = {}

    def put(self, sn: int, team: tuple[int, int], spids: list[int], build=None, traits=(28, 55, 13)):
        self.cells.setdefault(sn, {})[team] = [
            tc.TeamCard(s, 1, (1 + s % 8) if build is None else build, traits, None) for s in spids]

    def __call__(self, _session, sn, team_type, part):
        self.calls.append((sn, (team_type, part)))
        e = self.errors.get(len(self.calls))
        if e is not None:
            raise e
        return self.cells.get(sn, {}).get((team_type, part))


def _picked(api, conn, targets, **kw):
    """랭커 픽 길로 마지막 경기를 받아 둔다(1~200 이 이미 받아 둔 상태)."""
    return trp._collect(api, conn, targets, **kw)


def _web_collect(web, conn, targets, now=NOW, **kw):
    clk = trp.Clock()
    return tc.collect(None, conn, targets, on_at=ON_AT, now_fn=lambda: now, sleep=clk.sleep, clock=clk, fetch=web, **kw)


def _cfg(**kw):
    """config 상수를 잠깐 바꾼다 — with 문으로."""
    class _C:
        def __enter__(self):
            self.keep = {k: getattr(config, k) for k in kw}
            for k, v in kw.items():
                setattr(config, k, v)

        def __exit__(self, *exc):
            for k, v in self.keep.items():
                setattr(config, k, v)
            return False
    return _C()


def test_due_mixed_targets_order():
    """한 번에 부를 때 대상마다 주기가 다르다 — 1~200(3일)은 4일 전이면 다시, 201~500(10일)은 4일 전이면 아직.
    순서: 정리 임박(받은 지 12일 넘음) → 처음 보는 사람(순위 순) → 나머지 다시 확인(오래된 순)."""
    have = {}
    t = []
    for sn, rank, ago, cycle in ((1, 1, 4, None), (2, 300, 4, 10), (3, 301, 11, 10), (4, 2, 13, None),
                                 (5, 302, None, 10), (6, 3, None, None), (7, 303, 13, 10)):
        d = {"rank": rank, "profile_sn": sn, "nickname": f"n{sn}"}
        if cycle:
            d["stale_days"] = cycle
        t.append(d)
        if ago is not None:
            have[sn] = {"nickname": f"n{sn}", "fetched_at": (NOW - timedelta(days=ago)).isoformat(timespec="seconds")}
    got = [d["profile_sn"] for d in tc.rankerpick.due(t, have, NOW)]
    assert 2 not in got, "201~500 이 3일 주기로 다시 물렸다(대상별 주기가 안 먹었다)"
    assert got == [4, 7, 5, 6, 3, 1], got


def test_trait_recheck_before_prune():
    """정리(14일)에 닿기 전에 다시 묻는다 — 몫이 한 명분뿐인 날, 처음 보는 사람보다 12일 넘은 줄이 먼저(검토 A·B)."""
    conn = store.open_db(":memory:")
    targets, api = _targets(2, start_rank=201)
    old = trp._collect(api, conn, targets[:1], now_fn=lambda: NOW - timedelta(days=config.RANK_RAW_KEEP_DAYS - 1))
    assert old.checked == 1
    with _cfg(TRAIT_API_SHARE=2):   # 첫 사람은 ouid 가 있어 2요청이면 한 명분
        res = trp._collect(api, conn, tc.api_targets(targets))
    rows = store.ranker_squads(conn)
    assert rows[targets[0]["profile_sn"]]["fetched_at"].startswith(DAY), "정리 임박 줄을 다시 안 물었다"
    assert targets[1]["profile_sn"] not in rows and res.share_spent, "몫이 없는데 처음 보는 사람을 받았다"


def test_trait_share_leaves_pick_reserve():
    """특성 몫(201~500)은 TRAIT_API_SHARE 까지만 — 그 뒤 1~200 은 합계(300)가 남는 한 계속 받는다."""
    conn = store.open_db(":memory:")
    both, api_a = _ranked([201, 202, 203, 204, 205, 1, 2, 3])
    low, top = both[:5], both[5:]
    with _cfg(TRAIT_API_SHARE=6):
        res = trp._collect(api_a, conn, tc.api_targets(low + top))
    used = store.budget_used(conn, DAY, store.BUDGET_TRAIT_SHARE)
    assert used == 6 and res.share_spent, (used, res)
    rows = store.ranker_squads(conn)
    assert sum(1 for t in low if t["profile_sn"] in rows) == 2, "몫 6 = 두 명분(3요청씩)"
    assert all(t["profile_sn"] in rows for t in top), "몫이 바닥난 뒤 1~200 을 안 받았다"
    assert store.budget_used(conn, DAY, store.BUDGET_RANKER_PICK) == 6 + 9, "합계는 한 장부(ranker_pick)"


def test_pick_recheck_continues_after_share_spent():
    """몫 바닥은 그 대상만 건너뛴다(_ShareLimit) · 합계 바닥만 반복을 끝낸다(_Limit) — 몫 대상이 앞에 줄 서 있어도
    1~200 다시 확인이 매일 잘리지 않게. 한 명분 검사를 지나도 몫이 바닥나는 길(429 다시 시도)을 직접 만든다."""
    conn = store.open_db(":memory:")
    both, api = _ranked([201, 1])
    low, top = both[:1], both[1:]
    api.errors[1] = trp.NexonAPIError("한도", code=trp.QUOTA_CODE, status=429)   # 첫 요청이 429 → 다시 시도가 몫을 하나 더
    with _cfg(TRAIT_API_SHARE=3, RANKER_PICK_429_WAIT_S=1):
        res = trp._collect(api, conn, tc.api_targets(low + top))
    rows = store.ranker_squads(conn)
    assert res.share_spent and not res.limit, res
    assert low[0]["profile_sn"] not in rows, "몫이 중간에 바닥났는데 반쪽 줄을 저장했다"
    sn = top[0]["profile_sn"]
    assert sn in rows and rows[sn]["match_id"], "몫 바닥 뒤 1~200 을 끝내 버렸다(합계 바닥과 같은 예외)"


def test_trait_share_whole_person():
    """몫 대상은 한 명분(ouid 없으면 3)을 남은 몫으로 시작할 수 있을 때만 — 몫 경계에서 ouid 만 받고 끊기지 않는다.
    몫은 합계를 센 뒤에만 센다(합계가 바닥나 못 보낸 요청이 몫에 잡히지 않게)."""
    conn = store.open_db(":memory:")
    low, api = _targets(3, start_rank=201)
    with _cfg(TRAIT_API_SHARE=5):
        res = trp._collect(api, conn, tc.api_targets(low))
    assert res.requests == 3 and store.budget_used(conn, DAY, store.BUDGET_TRAIT_SHARE) == 3, res
    assert all(name != "ouid" for name, *_ in api.calls[3:]), "남은 몫 2 로 새 사람을 시작했다"
    conn2 = store.open_db(":memory:")
    with _cfg(TRAIT_API_SHARE=100, RANKER_PICK_DAILY_REQ=4):
        low2, api2 = _targets(3, start_rank=201)
        res = trp._collect(api2, conn2, tc.api_targets(low2))
    assert res.limit and store.budget_used(conn2, DAY, store.BUDGET_RANKER_PICK) == 4
    assert store.budget_used(conn2, DAY, store.BUDGET_TRAIT_SHARE) == 4, "합계에서 막힌 요청이 몫에 잡혔다"


def test_trait_respects_search_429():
    """429 는 한 키 한 장부 — 검색·거래·랭커 기록의 최종 429(openapi) 표시가 있으면 특성 로더의 오픈API 요청도 0."""
    conn = store.open_db(":memory:")
    targets, api = _targets(3, start_rank=201)
    store.budget_mark_429(conn, DAY, store.BUDGET_OPENAPI)
    out = tc.run(api, None, conn, targets, on_at=ON_AT, now_fn=lambda: NOW, fetch=_Web(), sleep=lambda s: None)
    assert out.pick is not None and out.pick.quota and api.calls == [], (out.pick, api.calls)


def test_trait_web_collects_and_stops_at_matching_team():
    conn = store.open_db(":memory:")
    targets, api = _targets(2)
    _picked(api, conn, targets)
    web = _Web()
    for t in targets:
        web.put(t["profile_sn"], (1, 1), _squad_of(t["rank"]))
    res = _web_collect(web, conn, targets)
    assert res.checked == 2 and res.cards == 22 and res.requests == 4, res
    row = store.trait_squads(conn)[targets[0]["profile_sn"]]
    assert row["state"] == "ok" and row["team"] == "1-1" and row["collect_on_at"] == ON_AT, row
    assert row["formation"] and row["match_day"] == DAY and len(row["body"]) == 11, row
    assert store.budget_used(conn, DAY, store.BUDGET_TRAIT_WEB) == 4, "실제 요청만큼 계수"
    # 같은 경기면 요청 0 · 다음에 경기가 바뀌면 지난번 팀부터(1요청)
    res = _web_collect(web, conn, targets)
    assert res.requests == 0 and res.checked == 0
    store.save_matches(conn, [trp._match("새경기", NOW.date(), [("r1", "랭커1", _squad_of(1))])])
    store.save_ranker_squad(conn, targets[0]["profile_sn"], nickname=targets[0]["nickname"], ouid="r1", rank=1,
                            match_id="새경기", match_day=DAY, fetched_at=NOW.isoformat(), fail=None, source=store.PICK)
    web.calls.clear()
    res = _web_collect(web, conn, targets[:1])
    assert web.calls == [(targets[0]["profile_sn"], (1, 1))], web.calls


def test_trait_stale_retried_after_days():
    """6칸 다 안 맞으면 stale 로 저장 — 경기가 그대로면 TRAIT_STALE_RETRY_DAYS 뒤에만 다시."""
    conn = store.open_db(":memory:")
    targets, api = _targets(1)
    _picked(api, conn, targets)
    web = _Web()
    res = _web_collect(web, conn, targets)
    assert res.stale == 1 and res.requests == 6 and store.trait_squads(conn)[targets[0]["profile_sn"]]["state"] == "stale"
    days = config.TRAIT_STALE_RETRY_DAYS
    assert _web_collect(web, conn, targets, now=NOW + timedelta(days=days - 1)).requests == 0, "stale 을 바로 다시 봤다"
    assert _web_collect(web, conn, targets, now=NOW + timedelta(days=days, seconds=1)).requests == 6


def test_trait_row_survives_same_match_recheck():
    """경기 id 가 같아도 오픈API 다시 확인이 지나가면 checked_at 을 새로(요청 0) — 안 그러면 경기를 안 한 랭커의 줄이
    14일 정리에 지워진다(검토 B). 줄이 없으면 경기 id 와 상관없이 웹을 본다."""
    conn = store.open_db(":memory:")
    targets, api = _targets(1)
    _picked(api, conn, targets)
    web = _Web()
    web.put(targets[0]["profile_sn"], (1, 0), _squad_of(1))
    _web_collect(web, conn, targets)
    later = NOW + timedelta(days=4)
    trp._collect(api, conn, targets, now_fn=lambda: later)            # 3일 지나 다시 확인 — 같은 경기
    res = _web_collect(web, conn, targets, now=later)
    assert res.touched == 1 and res.requests == 0, res
    row = store.trait_squads(conn)[targets[0]["profile_sn"]]
    assert row["checked_at"].startswith(later.date().isoformat()), row["checked_at"]
    store.prune_trait_squads(conn, on_at=ON_AT, today=NOW.date() + timedelta(days=config.RANK_RAW_KEEP_DAYS + 2))
    assert store.trait_squads(conn), "다시 확인한 줄이 14일 정리에 지워졌다"
    conn.execute("DELETE FROM trait_squads")
    conn.commit()
    assert _web_collect(web, conn, targets, now=later).checked == 1, "줄이 없는데 같은 경기라고 안 봤다"


def test_trait_skips_old_match_and_failed_rows():
    """마지막 경기가 RANKER_PICK_MAX_AGE_DAYS 넘었거나 · 실패 줄 · 닉네임이 바뀐 사람은 웹을 안 본다(요청 0)."""
    conn = store.open_db(":memory:")
    targets, api = _targets(1)
    _picked(api, conn, targets)
    web = _Web()
    later = NOW + timedelta(days=config.RANKER_PICK_MAX_AGE_DAYS + 1)
    assert _web_collect(web, conn, targets, now=later).requests == 0, "오래된 경기에 웹을 썼다"
    renamed = [dict(targets[0], nickname="새이름")]
    assert _web_collect(web, conn, renamed).requests == 0, "닉네임이 바뀌었는데 옛 경기로 봤다"
    assert _web_collect(web, conn, targets).requests == 6, "볼 사람을 거른 게 아니라 다 막았다(위 단언이 빈 검사)"


def test_trait_loader_budget():
    """웹 하루 상한 trait_web — 실제 요청 직전에만 세고, 닿으면 그 사람 저장 없이 멈춘다 · 다시 켜도 계수가 남는다."""
    conn = store.open_db(":memory:")
    targets, api = _targets(3)
    _picked(api, conn, targets)
    with _cfg(TRAIT_WEB_DAILY_REQ=8):
        res = _web_collect(_Web(), conn, targets)
        assert res.limit and res.requests == 8 and res.checked == 1, res
        assert len(store.trait_squads(conn)) == 1, "상한에 끊긴 사람을 반쪽으로 저장했다"
        assert _web_collect(_Web(), conn, targets).requests == 0


def test_trait_web_gap_between_requests():
    conn = store.open_db(":memory:")
    targets, api = _targets(1)
    _picked(api, conn, targets)
    clk = trp.Clock()
    tc.collect(None, conn, targets, on_at=ON_AT, now_fn=lambda: NOW, sleep=clk.sleep, clock=clk, fetch=_Web())
    assert len(clk.slept) == 5 and all(abs(s - config.RANKER_PICK_GAP_S) < 1e-9 for s in clk.slept), clk.slept


def test_trait_web_stops_for_the_day_on_block_and_format():
    """막힘(rate) · 형식 바뀜(format) → 그날 멈춤 표시(각각 따로) · 다시 켜도 요청 0. 넘김·연결(down)은 이번만."""
    for kind, mark in (("rate", store.BUDGET_TRAIT_WEB_BLOCK), ("format", store.BUDGET_TRAIT_WEB_FORMAT)):
        conn = store.open_db(":memory:")
        targets, api = _targets(2)
        _picked(api, conn, targets)
        web = _Web()
        web.errors[2] = tc.TraitError("x", kind)
        res = _web_collect(web, conn, targets)
        assert res.stop == kind and res.error == tc.MESSAGES[kind] and store.budget_hit_429(conn, DAY, (mark,)), res
        assert store.trait_squads(conn) == {}, "멈춘 사람을 저장했다"
        n = len(web.calls)
        res = _web_collect(web, conn, targets)
        assert res.stop == kind and len(web.calls) == n, "그날 다시 요청했다"
    conn = store.open_db(":memory:")
    targets, api = _targets(1)
    _picked(api, conn, targets)
    web = _Web()
    web.errors[1] = tc.TraitError("x", "down")
    assert _web_collect(web, conn, targets).stop == "down"
    assert _web_collect(web, conn, targets).requests == 6, "연결 실패로 그날을 막았다"


def test_trait_block_streak():
    """막힌 날이 이어진 수 — 막힘 없이 받은 날에서 끊는다 · 안 연 날은 세지도 끊지도 않는다."""
    conn = store.open_db(":memory:")

    def web_day(day, blocked):
        store.budget_take(conn, day, store.BUDGET_TRAIT_WEB, None)
        if blocked:
            store.budget_mark_429(conn, day, store.BUDGET_TRAIT_WEB_BLOCK)

    web_day("2026-10-01", True)
    web_day("2026-10-02", False)
    web_day("2026-10-03", True)
    web_day("2026-10-05", True)
    assert store.trait_block_streak(conn) == 2
    web_day("2026-10-06", True)
    assert store.trait_block_streak(conn) == config.TRAIT_BLOCK_DAYS == 3


def test_trait_run_order_and_records_pick_days():
    """한 바퀴 = 웹(이미 경기가 있는 1~200) → 오픈API(1~200 + 201~500 몫) → 웹(새로 받은 경기).
    오픈API 바퀴가 끝까지 돌면 1~200 만 record_days 로(N15 — 검토 3회차) · cancel 이면 안 센다."""
    conn = store.open_db(":memory:")
    both, api = _ranked([1, 2, 201])
    top, low = both[:2], both[2:]
    _picked(api, conn, top[:1])
    web = _Web()
    for t in both:
        web.put(t["profile_sn"], (1, 0), _squad_of(t["rank"]))
    days = []
    out = tc.run(api, None, conn, top + low, on_at=ON_AT, now_fn=lambda: NOW, fetch=web, sleep=lambda s: None,
                 record_days=days.append)
    assert len(out.web) == 2 and out.web[0].checked == 1 and out.web[1].checked == 2, out
    assert [t["rank"] for t in days[0]] == [1, 2], days
    assert sorted(store.trait_squads(conn)) == sorted(t["profile_sn"] for t in top + low)
    days.clear()
    n = len(api.calls)
    stop = [False]
    ids = api.get_match_ids
    api.get_match_ids = lambda *a, **k: (stop.__setitem__(0, True), ids(*a, **k))[1]   # 오픈API 단계 안에서 끊는다
    out = tc.run(api, None, conn, top + low, on_at=ON_AT, now_fn=lambda: NOW + timedelta(days=11), fetch=web,
                 sleep=lambda s: None, record_days=days.append, cancel=lambda: stop[0])
    assert out.pick is not None and out.pick.cancelled and len(api.calls) > n, "오픈API 단계까지 안 갔다(빈 검사)"
    assert days == [], "cancel 로 끊긴 바퀴를 셌다"


def test_trait_run_skips_openapi_when_web_stopped():
    conn = store.open_db(":memory:")
    targets, api = _targets(1)
    _picked(api, conn, targets)
    store.budget_mark_429(conn, DAY, store.BUDGET_TRAIT_WEB_BLOCK)
    n = len(api.calls)
    out = tc.run(api, None, conn, targets, on_at=ON_AT, now_fn=lambda: NOW, fetch=_Web(), sleep=lambda s: None)
    assert out.pick is None and len(api.calls) == n, out
    assert out.block_streak == 1, "막힘으로 멈춘 바퀴가 이어진 날 수를 안 실었다(화면이 스스로 끄는 근거)"


def test_trait_api_targets():
    rows = [{"rank": r, "profile_sn": r, "nickname": str(r)} for r in (1, 200, 201, 500, 501)]
    got = {t["rank"]: t for t in tc.api_targets(rows)}
    assert sorted(got) == [1, 200, 201, 500], "501위가 들어갔다"
    assert "share" not in got[200] and "stale_days" not in got[200], "1~200 에 몫·주기를 실었다"
    assert got[201]["share"] == "trait" and got[201]["stale_days"] == config.TRAIT_STALE_DAYS


def test_trait_share_constant():
    """몫 = 300 − 1~200 다시 확인(67명 × 2) − 추천 ③(20 × 3) = 106 — 숫자를 박지 않고 상수에서 나온다."""
    assert config.TRAIT_API_SHARE == 106
    assert config.TRAIT_STALE_DAYS < config.RANK_RAW_KEEP_DAYS - config.TRAIT_RECHECK_MARGIN_DAYS, "주기가 정리 임박보다 길다"


def test_trait_disk_budget():
    """201~500 의 마지막 경기 300개가 matches 에 같이 쌓인다 — 가짜 경기 300개(양쪽 선발 11 + 교체) · 상한 15MB."""
    d, conn = _file_db()
    ds = [trp._match(f"t{i:04d}", NOW.date(), [(f"a{i}", f"가{i}", trp._squad(100000000 + i * 11)),
                                              (f"b{i}", f"나{i}", trp._squad(200000000 + i * 11))]) for i in range(300)]
    store.save_matches(conn, ds)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    size = (d / "fifa.db").stat().st_size
    assert size < 15 * 1024 * 1024, size


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
