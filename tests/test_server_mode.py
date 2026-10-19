"""중앙 서버 1단계 ⓐ — 앱 쪽(docs/CENTRAL_SERVER.md 11절). 네트워크 없이 가짜 어댑터·가짜 세션으로.

- 서버 주소가 없으면 요청이 바꾸기 전과 같다(골든 — 2026-10-20 바꾸기 전에 뜬 값)
- 서버 모드: 키 헤더가 서버로 안 나간다(set_key 뒤에도) · 거래는 늘 직접 · 서버 사정은 서버 전용 코드
- 서버 신호로 실패한 요청은 실패 수와 상관없이 직접으로 한 번 더 · 연속 실패 수는 다음 요청의 경로만
- 키 확인·구단주 분포·dev 도구는 서버 주소가 있어도 직접
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402

import nexon_api  # noqa: E402
import watchdog  # noqa: E402

# 바꾸기 전 nexon_api 가 보낸 요청(메서드 · URL · 헤더 — User-Agent·Accept-Encoding 은 설치된 라이브러리 따라 달라 뺀다)
_KEYED = (("accept", "*/*"), ("connection", "keep-alive"), ("x-nxopen-api-key", "KEY"))
_PLAIN = (("accept", "*/*"), ("connection", "keep-alive"))
GOLDEN = [
    ("GET", "https://open.api.nexon.com/fconline/v1/id?nickname=%EB%8B%89+%EB%84%A4%EC%9E%84", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/user/basic?ouid=o1", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/user/maxdivision?ouid=o1", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/user/match?ouid=o1&matchtype=52&offset=0&limit=100", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/match-detail?matchid=m1", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/match-detail?matchid=m2", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/user/trade?tradetype=buy&offset=0&limit=100", _KEYED),
    ("GET", "https://open.api.nexon.com/fconline/v1/ranker-stats?matchtype=52"
            "&players=%5B%7B%22id%22%3A101%2C%22po%22%3A3%7D%5D", _KEYED),
    ("GET", "https://open.api.nexon.com/static/fconline/meta/spid.json", _PLAIN),
]


class _Capture:
    """HTTPAdapter.send 를 바꿔 끼워 나가는 요청을 모은다. route(url) → (status, 본문 bytes)."""

    def __init__(self, route=None):
        self.seen: list[tuple] = []
        self.route = route or (lambda url: (200, b'{"ouid":"o1"}' if "/id?" in url else b"[]"))
        self._orig = HTTPAdapter.send
        self._lock = threading.Lock()

    def __enter__(self):
        cap = self

        def send(adapter, request, **kw):
            hdr = tuple(sorted((k.lower(), v) for k, v in request.headers.items()
                               if k.lower() not in ("user-agent", "accept-encoding")))
            with cap._lock:
                cap.seen.append((request.method, request.url, hdr))
            got = cap.route(request.url)
            if isinstance(got, Exception):
                raise got
            status, body = got
            r = requests.Response()
            r.status_code, r._content, r.url = status, body, request.url
            return r

        HTTPAdapter.send = send
        return self

    def __exit__(self, *a):
        HTTPAdapter.send = self._orig


def _drive(api) -> None:
    api.get_ouid("닉 네임")
    api.get_user_basic("o1")
    api.get_max_division("o1")
    api.get_match_ids("o1", matchtype=52, offset=0, limit=100)
    api.get_match_detail("m1")
    api.get_match_detail_nocache("m2")
    api.get_trades("buy", offset=0)
    api.get_ranker_stats(52, [(101, 3)])
    api.get_meta("spid")


def test_no_server_url_requests_unchanged():
    with _Capture() as cap:
        _drive(nexon_api.FCOnlineAPI("KEY", cache_dir=None))
    assert cap.seen == GOLDEN, "\n".join(map(str, cap.seen))
    # server=True 라도 주소가 비면 지금과 같다(app_main 은 server=bool(config.SERVER_URL) 로 넘긴다)
    with _Capture() as cap:
        _drive(nexon_api.FCOnlineAPI("KEY", cache_dir=None, server=True, server_url="", install_id="iid"))
    assert cap.seen == GOLDEN, "서버 주소가 빈 서버 모드가 요청을 바꿨다"


SRV = "http://srv.test"
NEXON = nexon_api.BASE_URL
_BUSY = (503, b'{"error":{"name":"SERVER_BUSY","message":"m"}}')
_HTML502 = (502, b"<html>bad gateway</html>")


def _server_api(key="USERKEY"):
    nexon_api.reset_server_health()
    return nexon_api.FCOnlineAPI(key, cache_dir=None, server=True, server_url=SRV, install_id="iid")


def _no_sleep():
    orig = nexon_api.time.sleep
    nexon_api.time.sleep = lambda s: None
    return orig


def _hdr(seen_row) -> dict:
    return dict(seen_row[2])


def test_server_mode_never_sends_key_to_server_even_after_set_key():
    api = _server_api()
    with _Capture() as cap:
        _drive(api)
        api.set_key("NEWKEY")
        _drive(api)
    to_server = [s for s in cap.seen if s[1].startswith(SRV)]
    assert to_server, "서버로 간 요청이 없다(검사가 빈다)"
    for s in to_server:
        h = _hdr(s)
        assert "x-nxopen-api-key" not in h, f"키가 서버로 나갔다: {s[1]}"
        assert h.get(nexon_api.SERVER_INSTALL_HEADER) == "iid"
    # 메타도 서버로(설치 ID 를 싣고) — 직접 쪽으로 새지 않는다
    assert any("/static/fconline/meta/spid.json" in s[1] for s in to_server)


def test_trade_always_direct():
    api = _server_api()
    with _Capture() as cap:
        api.get_trades("buy")
    assert [s[1].split("?")[0] for s in cap.seen] == [NEXON + nexon_api.EP_USER_TRADE], cap.seen
    assert _hdr(cap.seen[0]).get("x-nxopen-api-key") == "USERKEY"


def test_server_busy_no_retry_not_throttled_then_direct():
    orig = _no_sleep()
    try:
        api = _server_api()
        route = lambda url: _BUSY if url.startswith(SRV) else (200, b'{"ouid":"o1"}')  # noqa: E731
        with _Capture(route) as cap:
            assert api.get_ouid("가") == "o1"
        srv_calls = [s for s in cap.seen if s[1].startswith(SRV)]
        assert len(srv_calls) == 1, f"SERVER_BUSY(503)를 {len(srv_calls)}번 보냈다 — 코드를 상태보다 먼저 봐야 한다"
        assert api.throttled == 0, "서버 사정을 넥슨 429 로 셌다"
        assert not nexon_api._server_health.use_server(), "BUSY 뒤에도 바로 서버로 간다"
    finally:
        nexon_api.time.sleep = orig


def test_one_failed_detail_in_hundred_is_retried_direct():
    """연속 실패 수가 문턱에 안 닿는 한 건도 직접으로 다시 묻는다(3회차 검토 [상]1 — 전부 실패하는 가짜로는 이 구멍을 못 잡는다)."""
    orig = _no_sleep()
    try:
        api = _server_api()
        bad = "m50"

        def route(url):
            if url.startswith(SRV) and f"matchid={bad}" in url:
                return _HTML502
            mid = url.split("matchid=")[-1]
            return 200, json.dumps({"matchId": mid}).encode()

        with _Capture(route) as cap:
            got = [api.get_match_detail(f"m{i}") for i in range(100)]
        assert [g["matchId"] for g in got] == [f"m{i}" for i in range(100)]
        direct = [s for s in cap.seen if s[1].startswith(NEXON)]
        assert [s[1].split("matchid=")[-1] for s in direct] == [bad], direct
        assert nexon_api._server_health.use_server(), "한 건 실패로 직접 모드가 됐다(문턱 3)"
    finally:
        nexon_api.time.sleep = orig


def test_fallback_after_threshold_and_back_with_threads():
    orig_sleep, orig_mono = _no_sleep(), nexon_api.time.monotonic
    now = [1000.0]
    nexon_api.time.monotonic = lambda: now[0]
    try:
        api = _server_api()
        state = {"down": True}

        def route(url):
            if url.startswith(SRV) and state["down"]:
                return requests.ConnectionError("서버 꺼짐")
            return 200, b'{"ouid":"o1"}'

        with _Capture(route) as cap:
            out = []
            ts = [threading.Thread(target=lambda: out.append(api.get_ouid("가"))) for _ in range(24)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            assert out == ["o1"] * 24, "서버가 꺼졌을 때 직접으로 못 받은 요청이 있다"
            assert not nexon_api._server_health.use_server(), "연속 실패 뒤 직접 모드가 안 됐다"
            for s in cap.seen:
                if s[1].startswith(SRV):
                    assert "x-nxopen-api-key" not in _hdr(s), "경합 중 키가 서버로 나갔다"
            n_srv = sum(1 for s in cap.seen if s[1].startswith(SRV))
            api.get_ouid("가")
            assert sum(1 for s in cap.seen if s[1].startswith(SRV)) == n_srv, "직접 모드인데 서버로 갔다"
            state["down"] = False
            now[0] += nexon_api.SERVER_FALLBACK_S + 1
            api.get_ouid("가")
            assert cap.seen[-1][1].startswith(SRV), "직접 모드가 끝났는데 서버로 안 돌아갔다"
    finally:
        nexon_api.time.sleep, nexon_api.time.monotonic = orig_sleep, orig_mono
        nexon_api.reset_server_health()


def test_html_never_reaches_message_and_both_down_is_server_code():
    orig = _no_sleep()
    try:
        api = _server_api()

        def route(url):
            if url.startswith(SRV):
                return 200, b"<html>tunnel error</html>"
            return requests.ConnectionError("넥슨도 끊김")

        with _Capture(route):
            try:
                api.get_ouid("가")
                raise AssertionError("둘 다 죽었는데 통과했다")
            except nexon_api.NexonAPIError as e:
                assert e.code in nexon_api.SERVER_CODES, e.code
                assert "<html" not in e.message
    finally:
        nexon_api.time.sleep = orig


def test_nexon_5xx_relayed_by_server_is_not_a_server_signal():
    orig = _no_sleep()
    try:
        api = _server_api()
        body = b'{"error":{"name":"OPENAPI00001","message":"m"}}'
        with _Capture(lambda url: (500, body) if url.startswith(SRV) else (200, b'{"ouid":"o1"}')) as cap:
            try:
                api.get_ouid("가")
                raise AssertionError("넥슨 오류인데 통과했다")
            except nexon_api.NexonAPIError as e:
                assert e.code == "OPENAPI00001"
        assert not any(s[1].startswith(NEXON) for s in cap.seen), "넥슨이 준 5xx 를 서버 고장으로 보고 직접 갔다"
        assert nexon_api._server_health.use_server()
    finally:
        nexon_api.time.sleep = orig


def test_install_id_only_in_server_mode():
    import config
    saved = []
    orig_url, orig_save = config.SERVER_URL, config._save_env
    config._save_env = lambda var, val: saved.append(var)
    try:
        config.SERVER_URL = ""
        assert config.install_id() == "" and not saved, "서버를 안 쓰는데 설치 ID 를 만들었다"
        config.SERVER_URL = SRV
        import os
        os.environ.pop(config.INSTALL_ID_VAR, None)
        iid = config.install_id()
        assert len(iid) == 32 and saved == [config.INSTALL_ID_VAR]
    finally:
        config.SERVER_URL, config._save_env = orig_url, orig_save


def test_only_two_construction_sites_enable_server():
    """키 확인·구단주 분포·dev 도구는 서버 주소가 있어도 직접 — server= 를 넘기는 생성 자리는 둘뿐이어야 한다."""
    import re
    hits = []
    for p in ROOT.rglob("*.py"):
        if any(part in ("tests", "server", ".venv", "build", "dist") for part in p.relative_to(ROOT).parts):
            continue
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"FCOnlineAPI\((?:[^()]|\([^()]*\))*\)", text):
            if "server=" in m.group(0):
                hits.append(p.relative_to(ROOT).as_posix())
    assert sorted(hits) == ["app_main.py", "check_api.py"], hits


def test_check_key_goes_direct_even_with_server_url():
    import config
    orig = config.SERVER_URL
    config.SERVER_URL = SRV
    try:
        with _Capture(lambda url: (400, b'{"error":{"name":"OPENAPI00004","message":"m"}}')) as cap:
            assert nexon_api.check_key("KEY") is None
        assert cap.seen and all(s[1].startswith(NEXON) for s in cap.seen), cap.seen
    finally:
        config.SERVER_URL = orig


def test_rankerpick_stops_round_on_server_codes():
    """SERVER_* 는 NETWORK 처럼 이번 회차 멈춤 — '그 랭커만 실패'로 적고 계속 돌면 서버 몫을 태운다."""
    import inspect
    import rankerpick
    src = inspect.getsource(rankerpick.collect)
    assert "SERVER_CODES" in src, "랭커 픽이 서버 코드를 회차 멈춤으로 안 본다"


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
