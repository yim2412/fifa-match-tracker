"""봇 서버 회귀 테스트 — 네트워크 없이 명령 처리 전 구간을 돌린다.

test_parsing.py 와 같은 픽스처(익명화된 실응답 4경기)를 쓰되, 여기서는
"채팅 한 줄 → 답장 텍스트" 경로를 본다. API 를 타는 BotService 는 가짜로
갈아끼우므로 넥슨 호출도, DB 쓰기도 일어나지 않는다.

  python tests/test_bot.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from bot import formatters as fmt
from bot.commands import CommandRouter
from bot.server import build_server
from bot.service import BotService, Lookup
from models import parse_match

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _lookup() -> Lookup:
    man = json.load(open(os.path.join(_DIR, "manifest.json"), encoding="utf-8"))
    ouid = man["ouid"]
    details = [json.load(open(os.path.join(_DIR, m + ".json"), encoding="utf-8"))
               for m in man["match_ids"]]
    matches = [m for m in (parse_match(d, ouid) for d in details) if m]
    matches.sort(key=lambda m: m.match_date or 0, reverse=True)
    return Lookup(nickname="테스트구단주", ouid=ouid, basic={},
                  matches=matches, details=details, fetched=2)


class _FakeService:
    """BotService 자리에 끼우는 가짜 — 호출 인자를 기록만 한다."""

    def __init__(self):
        self.calls: list[tuple[str, int | None]] = []
        self.saved: dict[tuple[str, str], str] = {}

    def lookup(self, nickname: str, limit: int | None = None) -> Lookup:
        self.calls.append((nickname, limit))
        return _lookup()

    def register(self, room: str, sender: str, nickname: str) -> None:
        self.saved[(room, sender)] = nickname

    def registered(self, room: str, sender: str) -> str | None:
        return self.saved.get((room, sender))

    def unregister(self, room: str, sender: str) -> bool:
        return self.saved.pop((room, sender), None) is not None

    def division_names(self) -> dict:
        return {900: "챔피언스", 1000: "슈퍼챌린지"}

    def player_names(self) -> dict:
        return {}

    def position_names(self) -> dict:
        return {}


# ── 포맷터 ──────────────────────────────────────────────────────────────
def test_summary_counts():
    text = fmt.summary(_lookup(), {900: "챔피언스"})
    # 유효 3경기(1승 1무 1패) — "오류"(중단) 경기는 집계에서 빠진다.
    assert "3경기 1승 1무 1패" in text, text
    assert "승률 33.3%" in text, text
    assert "챔피언스" in text, text
    assert "새로 받은 경기 2건" in text, text


def test_summary_empty():
    empty = Lookup(nickname="아무개", ouid="x", basic={}, matches=[], details=[])
    assert "전적이 없습니다" in fmt.summary(empty, {})


def test_recent_limits():
    text = fmt.recent(_lookup(), 2)
    assert "최근 2경기" in text, text
    # 헤더 3줄(제목·전적·구분선) + 경기 2줄
    assert len(text.splitlines()) == 5, text


def test_opponents_specific_and_missing():
    lookup = _lookup()
    target = lookup.matches[0].opponent
    text = fmt.opponents(lookup, target=target)
    assert f"vs {target}" in text, text
    assert "없는닉네임" not in fmt.opponents(lookup)          # 목록 모드
    assert "없습니다" in fmt.opponents(lookup, target="없는닉네임")


def test_opponents_drops_unnamed():
    """상대 기록이 없는 경기(닉네임 "-")는 목록에서 뺀다 — 서로 다른 사람들이
    한 명처럼 묶여 맨 위를 차지했다(실사용에서 발견)."""
    lookup = _lookup()
    for m in lookup.matches:
        m.opponent = "-"
    assert "상대 전적이 없습니다" in fmt.opponents(lookup)

    lookup = _lookup()
    lookup.matches[0].opponent = "-"
    text = fmt.opponents(lookup)
    assert "\n- " not in text and not text.endswith("\n-"), text


def test_players_uses_details():
    text = fmt.players(_lookup(), {}, {}, n=3)
    assert "주요 선수 3명" in text, text
    assert "평점" in text
    # 선수당 한 줄 — 두 줄씩 쓰면 카톡이 말풍선을 접는다
    assert len(text.splitlines()) == 5, text


def test_today_counts_only_that_day():
    lookup = _lookup()
    played = next(m for m in lookup.matches if "승" in m.result)
    day = played.match_date.date()
    text = fmt.today(lookup, on=day)

    assert f"{day:%m/%d}" in text, text
    same_day = [m for m in lookup.matches
                if m.match_date and m.match_date.date() == day]
    assert f"{len(same_day)}경기" in text, text
    assert played.opponent in text, text
    # 다른 날 경기는 섞이지 않는다
    other = next(m for m in lookup.matches if m.match_date.date() != day)
    assert other.opponent not in text, text


def test_today_when_only_aborted_matches():
    """그 날 경기가 전부 "오류"(중단)면 summarize 가 0을 준다. 그때 요약 줄이
    통째로 빠져 머리글과 목록 사이가 비어 보이던 것을 막는다."""
    lookup = _lookup()
    aborted = next(m for m in lookup.matches
                   if not any(k in m.result for k in ("승", "무", "패")))
    text = fmt.today(lookup, on=aborted.match_date.date())
    assert "집계된 경기가 없습니다" in text, text
    assert "\n\n" not in text, text  # 빈 줄이 남지 않는다


def test_unnamed_opponent_is_labeled():
    """상대 기록이 없는 경기를 "vs -" 로 두면 무슨 경기인지 알 수 없다."""
    lookup = _lookup()
    lookup.matches[0].opponent = "-"
    assert "(상대 기록 없음)" in fmt.recent(lookup, 1), fmt.recent(lookup, 1)


# ── 라우터 ──────────────────────────────────────────────────────────────
def test_router_ignores_non_commands():
    r = CommandRouter(service=_FakeService())
    assert r.handle("방", "나", "안녕하세요") is None
    assert r.handle("방", "나", "!없는명령 어쩌고") is None
    assert r.handle("방", "나", "") is None


def test_router_help_needs_no_api():
    fake = _FakeService()
    r = CommandRouter(service=fake)
    assert "!전적" in r.handle("방", "나", "!도움")
    assert fake.calls == []  # 도움말은 조회를 타지 않는다


def test_router_cooldown_is_per_person():
    """쿨다운이 방 단위면 한 명이 치는 동안 그 방의 다른 사람이 막힌다."""
    fake = _FakeService()
    r = CommandRouter(service=fake)
    assert "테스트구단주" in r.handle("방A", "갑", "!전적 홍길동")
    assert r.handle("방A", "갑", "!전적 홍길동") is None       # 같은 사람 — 쿨다운
    assert r.handle("방A", "을", "!전적 홍길동") is not None   # 옆 사람은 통과
    assert r.handle("방B", "갑", "!전적 홍길동") is not None   # 방이 달라도 통과


def test_router_remembers_per_person():
    """방 단위로 기억하면 옆 사람이 조회한 순간 내 !선수 가 남의 계정을 본다."""
    fake = _FakeService()
    r = CommandRouter(service=fake)
    r.handle("방A", "갑", "!전적 홍길동")
    r.handle("방A", "을", "!전적 다른사람")
    assert "주요 선수" in r.handle("방A", "갑", "!선수")
    assert fake.calls[-1][0] == "테스트구단주", fake.calls


def test_register_then_short_commands():
    fake = _FakeService()
    r = CommandRouter(service=fake)
    assert "등록했습니다" in r.handle("방", "갑", "!등록 홍길동")
    assert fake.saved[("방", "갑")] == "홍길동", fake.saved

    # 등록 뒤에는 구단주명 없이 친다
    assert "3경기 1승 1무 1패" in r.handle("방", "갑", "!전적")
    assert fake.calls[-1][0] == "홍길동", fake.calls
    # 등록은 사람마다 따로 — 옆 사람은 그대로 안내를 받는다
    assert "!등록" in r.handle("방", "을", "!전적")

    # 등록·해제는 쿨다운을 타지 않는다 — 오타를 냈을 때 바로 다시 쳐야 한다
    assert "홍길동" in r.handle("방", "갑", "!등록")      # 인자 없이 = 확인
    assert "해제했습니다" in r.handle("방", "갑", "!해제")
    assert "등록된 계정이 없습니다" in r.handle("방", "갑", "!해제")


def test_register_has_no_cooldown():
    """오타를 등록한 직후 바로 고쳐 칠 수 있어야 한다(실사용 스모크에서 겪음)."""
    r = CommandRouter(service=_FakeService())
    assert r.handle("방", "갑", "!등록 오타") is not None
    assert r.handle("방", "갑", "!등록 제대로") is not None
    # 조회 명령은 그대로 쿨다운이 걸린다
    assert r.handle("방", "갑", "!전적 홍길동") is not None
    assert r.handle("방", "갑", "!전적 홍길동") is None


def test_register_rejects_unknown_account():
    """오타를 등록해 두면 이후 명령이 전부 실패해서 원인을 찾기 어렵다."""
    from nexon_api import NexonAPIError

    fake = _FakeService()

    def boom(room, sender, nickname):
        raise NexonAPIError("'오타닉' 구단주명을 찾지 못했습니다.",
                            code="OPENAPI00004")
    fake.register = boom

    r = CommandRouter(service=fake)
    assert "찾지 못했습니다" in r.handle("방", "갑", "!등록 오타닉")
    assert fake.saved == {}, fake.saved


def test_today_command():
    fake = _FakeService()
    r = CommandRouter(service=fake)
    reply = r.handle("방", "갑", "!오늘 홍길동")
    # 픽스처 경기는 과거 날짜라 "오늘" 에는 잡히지 않는다
    assert "오늘 치른 경기가 없습니다" in reply, reply


def test_router_needs_nickname():
    r = CommandRouter(service=_FakeService())
    assert "구단주명" in r.handle("새방", "나", "!전적")


def test_router_recent_parses_count():
    fake = _FakeService()
    r = CommandRouter(service=fake)
    assert "최근 2경기" in r.handle("방", "나", "!최근 홍길동 2")
    r._last.clear()                                    # 쿨다운 해제
    assert "최근 3경기" in r.handle("방", "나", "!최근 3")  # 방 기억 + 개수


def test_router_global_limit():
    fake = _FakeService()
    r = CommandRouter(service=fake)
    replies = [r.handle(f"방{i}", "나", "!전적 홍길동") for i in range(25)]
    assert any(x and "몰려" in x for x in replies), "전역 상한이 안 걸렸다"


# ── 조회 파이프라인 ─────────────────────────────────────────────────────
class _FakeAPI:
    """FCOnlineAPI 자리에 끼우는 가짜 — 호출 횟수를 센다."""

    def __init__(self, details: list[dict], ouid: str):
        self._details = {d["matchId"]: d for d in details}
        self._ouid = ouid
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def get_ouid(self, nickname: str) -> str:
        self._count("ouid")
        return self._ouid

    def get_user_basic(self, ouid: str) -> dict:
        self._count("basic")
        return {"nickname": "테스트구단주"}

    def get_match_ids(self, ouid, matchtype, offset, limit) -> list[str]:
        self._count("ids")
        return list(self._details)[:limit]

    def get_match_detail(self, match_id: str) -> dict:
        self._count("detail")
        return self._details[match_id]


def _fake_service(tmpdir: str) -> tuple[BotService, _FakeAPI]:
    """실제 DB 를 건드리지 않게 config.DB_PATH 를 임시 파일로 돌려놓는다.
    service 는 config.DB_PATH 를 모듈 경유로 읽으므로 이 교체가 먹는다."""
    lookup = _lookup()
    api = _FakeAPI(lookup.details, lookup.ouid)
    config.DB_PATH = os.path.join(tmpdir, "test.db")
    return BotService(api=api), api


def test_lookup_saves_without_touching_accounts():
    import store
    with tempfile.TemporaryDirectory() as tmp:
        svc, api = _fake_service(tmp)
        got = svc.lookup("아무개")
        assert got.nickname == "테스트구단주"
        assert got.fetched == 4 and len(got.matches) == 4, got.fetched

        conn = store.open_db(config.DB_PATH)
        try:
            # 봇은 GUI "최근 검색"을 오염시키지 않는다 — accounts 는 비어야 한다
            assert store.list_accounts(conn) == []
            assert store.match_count(conn, got.ouid,
                                     config.DEFAULT_MATCH_TYPE) == 4
        finally:
            conn.close()


def test_lookup_cache_skips_api():
    with tempfile.TemporaryDirectory() as tmp:
        svc, api = _fake_service(tmp)
        svc.lookup("아무개")
        before = dict(api.calls)
        again = svc.lookup("아무개")
        assert api.calls == before, api.calls      # 두 번째는 API 를 안 탄다
        assert again.fetched == 0, again.fetched   # 받은 건수를 두 번 알리지 않는다
        # 방 기억이 쓰는 정식 표기로도 같은 캐시를 맞힌다
        assert svc.lookup("테스트구단주").matches
        assert api.calls == before, api.calls


def test_unknown_nickname_message():
    """없는 구단주명에 넥슨은 OPENAPI00004(요청 파라미터 오류)를 준다 —
    그 원문은 이 문맥에서 쓸모가 없어 사람 말로 바꿔 준다."""
    from nexon_api import NexonAPIError

    with tempfile.TemporaryDirectory() as tmp:
        svc, api = _fake_service(tmp)

        def boom(nickname):
            raise NexonAPIError("요청 파라미터가 잘못됐습니다.",
                                code="OPENAPI00004", status=400)
        api.get_ouid = boom

        router = CommandRouter(service=svc)
        reply = router.handle("방", "나", "!전적 없는계정")
        assert "찾지 못했습니다" in reply, reply


def test_second_lookup_refetches_only_new():
    with tempfile.TemporaryDirectory() as tmp:
        svc, api = _fake_service(tmp)
        svc.lookup("아무개")
        svc._lookup_cache.clear()                  # 캐시 만료 상황을 만든다
        svc.lookup("아무개")
        # 이미 DB 에 있는 4경기는 상세를 다시 받지 않는다
        assert api.calls["detail"] == 4, api.calls


# ── HTTP 서버 ───────────────────────────────────────────────────────────
def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.loads(res.read().decode("utf-8"))


def test_server_roundtrip():
    server = build_server("127.0.0.1", 0, CommandRouter(service=_FakeService()))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base}/health", timeout=5) as res:
            assert json.loads(res.read().decode("utf-8"))["ok"] is True

        got = _post(f"{base}/message",
                    {"room": "방", "sender": "나", "text": "!전적 홍길동"})
        assert "테스트구단주" in got["reply"], got
        # 봇 명령이 아니면 reply 가 null — 어댑터는 이걸 보고 침묵한다
        got = _post(f"{base}/message",
                    {"room": "방", "sender": "나", "text": "그냥 잡담"})
        assert got["reply"] is None, got
    finally:
        server.shutdown()
        server.server_close()


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
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
