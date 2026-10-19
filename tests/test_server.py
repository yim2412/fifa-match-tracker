"""중앙 서버 1단계 ⓐ — 서버 쪽(docs/CENTRAL_SERVER.md 11절 "완료 기준"). 가짜 넥슨 세션, 네트워크 없음.

끝에서 끝 하나(test_end_to_end_app_through_server)는 이 PC 의 127.0.0.1 임시 포트에 진짜 서버를 띄워 앱 클라이언트로 묻는다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

import nexon_api  # noqa: E402
import watchdog  # noqa: E402
from server import settings  # noqa: E402
from server.app import Service, make_server, next_meta_boundary  # noqa: E402
from server.cache import Cache  # noqa: E402
from server.upstream import Upstream  # noqa: E402

KEY = "SERVICE-KEY-SECRET-123"
OUID_A = "a" * 32
OUID_B = "b" * 32
MID = "0123456789abcdef01234567"


class _Res:
    def __init__(self, status: int, body):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()


def _detail(mid: str = MID, a: str = OUID_A, b: str = OUID_B) -> dict:
    return {"matchId": mid, "matchDate": "2026-10-20T00:00:00",
            "matchInfo": [{"ouid": a, "nickname": "가"}, {"ouid": b, "nickname": "나"}]}


class _FakeNexon:
    """requests.Session 대역 — route(url, params) → _Res. 호출을 센다(스레드 안전)."""

    def __init__(self, route):
        self.route = route
        self.headers: dict = {}
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self.delay = 0.0

    def mount(self, *a):
        pass

    def get(self, url, params=None, timeout=None):
        with self._lock:
            self.calls.append((url, dict(params or {})))
        if self.delay:
            time.sleep(self.delay)
        r = self.route(url, params or {})
        if isinstance(r, Exception):
            raise r
        return r


def _ok_route(url, params):
    if url.endswith(nexon_api.EP_MATCH_DETAIL):
        return _Res(200, _detail(params["matchid"]))
    if url.endswith(nexon_api.EP_ID):
        return _Res(200, {"ouid": OUID_A})
    return _Res(200, [])


class _Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _service(route=_ok_route, log: Path | None = None):
    fake = _FakeNexon(route)
    clock = _Clock()
    svc = Service(Cache(":memory:"), Upstream(KEY, session=fake, sleep=lambda s: None), now=clock, log_path=log)
    return svc, fake, clock


def _q(**kw) -> list[tuple[str, str]]:
    return list(kw.items())


def test_detail_cache_hit_no_upstream():
    svc, fake, _ = _service()
    r1 = svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
    r2 = svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
    assert r1.status == r2.status == 200 and r1.body == r2.body
    assert len(fake.calls) == 1, fake.calls          # 두 번째는 영구 저장에서
    assert svc.cache.detail_count() == 1


def test_same_detail_concurrently_hits_nexon_once():
    svc, fake, _ = _service()
    fake.delay = 0.2
    out = []
    ts = [threading.Thread(target=lambda: out.append(svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")))
          for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(out) == 8 and all(r.status == 200 for r in out)
    assert len(fake.calls) == 1, f"같은 경기 동시 8건에 넥슨 {len(fake.calls)}번"


def test_negative_cache_detail_is_short():
    err = _Res(400, {"error": {"name": "OPENAPI00009", "message": "x"}})
    svc, fake, clock = _service(lambda u, p: err)
    for _ in range(3):
        r = svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
        assert r.status == 400 and b"OPENAPI00009" in r.body
    assert len(fake.calls) == 1, "부정 캐시가 안 걸렸다"
    clock.t += settings.NEG_TTL_DETAIL_S + 1
    svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
    assert len(fake.calls) == 2, "상세의 부정 캐시가 60초를 넘겨 살아 있다"
    # 상세가 아닌 경로는 10분
    svc2, fake2, clock2 = _service(lambda u, p: err)
    svc2.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    clock2.t += settings.NEG_TTL_DETAIL_S + 1
    svc2.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert len(fake2.calls) == 1


def test_detail_body_must_match_requested_id():
    svc, fake, _ = _service(lambda u, p: _Res(200, _detail("f" * 24)))
    r = svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
    assert r.status == 503 and nexon_api.SERVER_UNAVAILABLE_CODE.encode() in r.body
    assert svc.cache.detail_count() == 0, "다른 경기 본문을 영구 저장했다"
    svc, fake, _ = _service(lambda u, p: _Res(200, {}))
    assert svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1").status == 503
    assert svc.cache.detail_count() == 0


def test_cache_itself_refuses_mismatched_detail():
    # 저장소 쪽 대조도 따로 — 서비스의 대조가 앞에서 막아 서비스 테스트로는 이 줄을 못 잰다(변이 2026-10-20)
    c = Cache(":memory:")
    assert not c.put_detail(MID, b"{}", _detail("f" * 24)) and c.detail_count() == 0
    assert not c.put_detail(MID, b"{}", {}) and c.detail_count() == 0
    assert c.put_detail(MID, b"{}", _detail(MID)) and c.detail_count() == 1


def test_not_allowed_paths_404_and_trade_absent():
    svc, fake, _ = _service()
    for path in (nexon_api.EP_USER_TRADE, "/fconline/v1/whatever", "/../etc/passwd", "/static/fconline/meta/x.json",
                 "/static/fconline/meta/spid.txt"):
        r = svc.handle(path, _q(tradetype="buy"), "1.1.1.1")
        assert r.status == 404, (path, r.status)
    assert not fake.calls, "허용 밖 경로가 넥슨에 갔다"


def test_params_normalized_and_validated():
    svc, fake, _ = _service()
    a = svc.handle(nexon_api.EP_USER_MATCH, [("ouid", OUID_A), ("matchtype", "52"), ("offset", "0"), ("limit", "100"),
                                             ("evil", "1")], "1.1.1.1")
    b = svc.handle(nexon_api.EP_USER_MATCH, [("limit", "100"), ("offset", "0"), ("matchtype", "52"), ("ouid", OUID_A)],
                   "1.1.1.1")
    assert a.status == b.status == 200
    assert len(fake.calls) == 1, "인자 순서만 다른 요청이 따로 캐시됐다"
    assert "evil" not in fake.calls[0][1], "허용 밖 인자를 넥슨에 넘겼다"
    for q in (_q(ouid="xyz", matchtype="52"), _q(matchtype="52"), _q(ouid=OUID_A, matchtype="52", limit="101")):
        assert svc.handle(nexon_api.EP_USER_MATCH, q, "1.1.1.1").status == 400, q
    assert svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid="../x"), "1.1.1.1").status == 400
    assert len(fake.calls) == 1


def test_ranker_stats_pairs_cap():
    svc, fake, _ = _service()
    ok = json.dumps([{"id": i, "po": 3} for i in range(81)])
    bad = json.dumps([{"id": i, "po": 3} for i in range(82)])
    assert svc.handle(nexon_api.EP_RANKER_STATS, _q(matchtype="52", players=ok), "1.1.1.1").status == 200
    assert svc.handle(nexon_api.EP_RANKER_STATS, _q(matchtype="52", players=bad), "1.1.1.1").status == 400
    assert svc.handle(nexon_api.EP_RANKER_STATS, _q(matchtype="52", players='[{"id":"1","po":3}]'),
                      "1.1.1.1").status == 400
    assert len(fake.calls) == 1


def test_server_key_rejected_becomes_server_unavailable():
    svc, fake, _ = _service(lambda u, p: _Res(400, {"error": {"name": "OPENAPI00005", "message": "키"}}))
    r = svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert r.status == 503 and json.loads(r.body)["error"]["name"] == nexon_api.SERVER_UNAVAILABLE_CODE
    assert b"OPENAPI00005" not in r.body, "서버 키 거절을 그대로 넘겼다 — 앱에 '키가 틀렸다' 창이 뜬다"


def test_nexon_429_becomes_server_busy_with_retry_after():
    svc, fake, _ = _service(lambda u, p: _Res(429, {"error": {"name": "OPENAPI00007", "message": "한도"}}))
    r = svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert r.status == 503 and json.loads(r.body)["error"]["name"] == nexon_api.SERVER_BUSY_CODE
    assert r.headers.get("Retry-After") == str(settings.RETRY_AFTER_S)
    assert len(fake.calls) == settings.UPSTREAM_ATTEMPTS  # 서버가 다시 해 본 뒤에야 BUSY


def test_nexon_down_and_html_become_unavailable():
    svc, _, _ = _service(lambda u, p: requests.ConnectionError("끊김"))
    assert svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1").status == 503
    svc, _, _ = _service(lambda u, p: _Res(502, b"<html>bad gateway</html>"))
    r = svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert r.status == 503 and b"<html" not in r.body
    # 넥슨이 OPENAPI 코드를 단 5xx 는 그대로(요청 탓일 수 있다 — 앱이 판단)
    svc, _, _ = _service(lambda u, p: _Res(500, {"error": {"name": "OPENAPI00001", "message": "내부"}}))
    r = svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert r.status == 500 and b"OPENAPI00001" in r.body


def test_ip_budget_counted_separately_and_only_upstream():
    orig = settings.IP_DAILY
    settings.IP_DAILY = 2
    try:
        svc, fake, _ = _service()
        for i in range(2):
            assert svc.handle(nexon_api.EP_ID, _q(nickname=f"n{i}"), "1.1.1.1").status == 200
        # 캐시 적중은 안 센다
        assert svc.handle(nexon_api.EP_ID, _q(nickname="n0"), "1.1.1.1").status == 200
        r = svc.handle(nexon_api.EP_ID, _q(nickname="n9"), "1.1.1.1")
        assert r.status == 503 and nexon_api.SERVER_BUSY_CODE.encode() in r.body, "IP 몫을 넘겼는데 보냈다"
        assert svc.handle(nexon_api.EP_ID, _q(nickname="n9"), "2.2.2.2").status == 200, "다른 IP 까지 막혔다"
        assert len(fake.calls) == 3
    finally:
        settings.IP_DAILY = orig


def test_daily_budget_shared_across_ips():
    orig = settings.DAILY_BUDGET
    settings.DAILY_BUDGET = 1
    try:
        svc, fake, _ = _service()
        assert svc.handle(nexon_api.EP_ID, _q(nickname="a"), "1.1.1.1").status == 200
        assert svc.handle(nexon_api.EP_ID, _q(nickname="b"), "2.2.2.2").status == 503
        assert len(fake.calls) == 1
    finally:
        settings.DAILY_BUDGET = orig


def test_ttl_expires_with_fake_clock():
    svc, fake, clock = _service()
    svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    clock.t += settings.TTL_S[nexon_api.EP_ID] - 1
    svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert len(fake.calls) == 1
    clock.t += 2
    svc.handle(nexon_api.EP_ID, _q(nickname="x"), "1.1.1.1")
    assert len(fake.calls) == 2, "TTL 이 지나도 옛 값을 줬다"


def test_meta_until_friday_kst():
    from datetime import datetime
    # 2026-10-20 은 화요일 — 다음 경계는 금요일(10-23) 0시 KST
    tue = datetime(2026, 10, 20, 12, 0, tzinfo=settings.KST).timestamp()
    b = datetime.fromtimestamp(next_meta_boundary(tue), settings.KST)
    assert (b.year, b.month, b.day, b.hour) == (2026, 10, 23, 0), b
    fri0 = datetime(2026, 10, 23, 0, 0, tzinfo=settings.KST).timestamp()
    b = datetime.fromtimestamp(next_meta_boundary(fri0), settings.KST)
    assert (b.month, b.day) == (10, 30), b                     # 경계 그 순간이면 다음 주


def test_erased_ouid_not_stored_but_relayed():
    svc, fake, _ = _service()
    svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
    assert svc.cache.erase(OUID_B) == 1 and svc.cache.detail_count() == 0
    r = svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=MID), "1.1.1.1")
    assert r.status == 200 and svc.cache.detail_count() == 0, "지운 ouid 의 경기를 다시 쌓았다"
    other = "1" * 24
    svc.handle(nexon_api.EP_MATCH_DETAIL, _q(matchid=other), "1.1.1.1")
    assert svc.cache.detail_count() == 0  # 기본 경로가 늘 OUID_B 를 넣는다 — 이것도 안 쌓인다


def test_key_and_nickname_never_in_response_or_log():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "server.log"
        routes = [lambda u, p: _Res(400, {"error": {"name": "OPENAPI00005", "message": f"bad {KEY}"}}),
                  lambda u, p: requests.ConnectionError(f"to {u} key {KEY}"),
                  _ok_route]
        for route in routes:
            svc, _, _ = _service(route, log)
            for path, q in ((nexon_api.EP_ID, _q(nickname="비밀닉네임")), (nexon_api.EP_MATCH_DETAIL, _q(matchid=MID))):
                r = svc.handle(path, q, "1.1.1.1")
                assert KEY.encode() not in r.body, "응답에 서버 키가 실렸다"
        text = log.read_text(encoding="utf-8")
        assert text.count("\n") >= 6, "로그가 안 남았다(검사가 빈다)"
        assert KEY not in text and "비밀닉네임" not in text, "로그에 키나 닉네임 원문이 있다"


def test_server_import_does_not_load_config():
    # config 가 따라 들어오면 load_dotenv 가 사용자 .env 의 키 둘을 서버 프로세스 환경에 싣는다
    code = "import sys, server.app, server.settings; print('config' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", "server 가 config 를 import 한다"


def test_run_local_refuses_on_empty_game_list_game_or_measurement():
    from server import run_local
    procs = [("explorer.exe", "C:\\Windows\\explorer.exe"), ("python.exe", "python app_main.py")]
    assert run_local.blockers(procs, games=()), "게임 목록이 비었는데 띄우게 했다(감시가 조용히 빈다)"
    assert run_local.blockers(procs, games=("Game.exe",)) == []
    assert run_local.blockers(procs + [("GAME.EXE", "")], games=("game.exe",)), "게임이 떠 있는데 못 잡았다"
    for s in run_local.BLOCKING_SCRIPTS:
        why = run_local.blockers(procs + [("python.exe", f"python tools/{s} --daily")], games=("g.exe",))
        assert why and s in why[0], (s, why)
    assert settings.GAME_PROCESSES == () or all(n.lower().endswith(".exe") for n in settings.GAME_PROCESSES)


def test_run_local_reads_key_only_from_server_env():
    from server import run_local
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "server.env"
        p.write_text("\ufeff# 주석\nNEXON_SERVICE_KEY= abc \n", encoding="utf-8")
        assert run_local.read_key(p) == "abc"
        p.write_text("NEXON_SERVICE_KEY=\n", encoding="utf-8")
        assert run_local.read_key(p) == ""
        assert run_local.read_key(Path(d) / "없음.env") == ""
    assert settings.ENV_FILE.name == "server.env" and settings.ENV_FILE.parent == settings.DB_PATH.parent


def test_server_db_outside_repo():
    assert ROOT not in settings.DB_PATH.resolve().parents, f"서버 DB 가 저장소 안: {settings.DB_PATH}"


def test_end_to_end_app_through_server():
    nexon_api.reset_server_health()
    svc, fake, _ = _service()
    srv = make_server(svc, "127.0.0.1", 0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        api = nexon_api.FCOnlineAPI("USERKEY", cache_dir=None, server=True, server_url=url, install_id="iid")
        assert api.get_ouid("가") == OUID_A
        d1 = api.get_match_detail(MID)
        d2 = api.get_match_detail(MID)
        assert d1 == d2 == _detail(MID)
        assert len(fake.calls) == 2, fake.calls      # ouid 1 + 상세 1(두 번째는 서버 저장에서)
        assert requests.get(f"{url}/health", timeout=5).status_code == 200
        r = requests.get(f"{url}{nexon_api.EP_USER_TRADE}", params={"tradetype": "buy"}, timeout=5)
        assert r.status_code == 404
    finally:
        srv.shutdown()
        srv.server_close()


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
