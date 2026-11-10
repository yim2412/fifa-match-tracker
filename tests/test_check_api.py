"""연결 점검(check_api · 96 W3) — 터미널 출력 바이트·종료 코드 골든 + 앱 [연결 점검] 묶음. 네트워크 없음.
`python tests/test_check_api.py` · 골든을 다시 뜰 때 `--write`(바뀐 줄이 의도인지 먼저 본다)

골든은 떼기(CheckCtx·CHECKS) 전 커밋에서 떴다 — 떼고 나서 한 바이트라도 다르면 FAIL.
넥슨 오픈API·데이터센터·CDN·업데이트 확인·rank.db 를 전부 가짜로, fifa.db·캐시는 임시 폴더로(실제 fifa.db 를 안 연다 — 2회차 [중])."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import check_api  # noqa: E402
import config  # noqa: E402
import core_api  # noqa: E402
import images  # noqa: E402
import ownerdist  # noqa: E402
import playerinfo  # noqa: E402
import rankcollect  # noqa: E402
import ranker  # noqa: E402
import seasons as sn  # noqa: E402
import teamcolor  # noqa: E402
import traitcollect  # noqa: E402
import updatecheck  # noqa: E402
import watchdog  # noqa: E402

FIX = Path(ROOT) / "tests" / "fixtures"
GOLDEN = FIX / "check_api_golden.json"
_MAN = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))
OUID, IDS = _MAN["ouid"], _MAN["match_ids"]
DETAILS = {i: json.loads((FIX / f"{i}.json").read_text(encoding="utf-8")) for i in IDS}
KEY = "testkey0123456789"


class FakeAPI:
    """넥슨 오픈API 가짜 — fail 에 이름을 넣으면 그 요청이 NexonAPIError 를 낸다."""
    made: list = []
    fail: set = set()

    def __init__(self, api_key, timeout=10, cache_dir=None, server=False, server_url="", install_id=""):
        self.key, self.cache_dir, self.server = api_key, cache_dir, server
        self.asked: list = []
        FakeAPI.made.append(self)

    @property
    def server_mode(self):
        return self.server

    def _maybe(self, name):
        self.asked.append(name)
        if name in FakeAPI.fail:
            raise check_api.NexonAPIError(f"{name} 실패", code="OPENAPI00009", status=500)

    def get_ouid(self, nickname, attempts=3):
        self._maybe("ouid")
        return OUID

    def get_user_basic(self, ouid):
        self._maybe("basic")
        return {"nickname": "테스트구단주", "level": 7}

    def get_max_division(self, ouid):
        self._maybe("maxdiv")
        return [{"matchType": config.DEFAULT_MATCH_TYPE, "division": 800, "achievementDate": "2026-10-01T03:00:00"}]

    def get_meta(self, name):
        self._maybe("meta")
        if name == "division":
            return [{"divisionId": 800, "divisionName": "챔피언스"}]
        if name == "spid":
            return [{"id": 1, "name": "가짜선수"}]
        return []

    def get_trades(self, kind):
        self._maybe("trades")
        return [{"tradeDate": "2026-11-01T01:00:00"}]

    def get_match_ids(self, ouid, matchtype=50, offset=0, limit=20, attempts=3):
        self._maybe("ids")
        if "ids_empty" in FakeAPI.fail:
            return []
        return list(IDS)[offset:offset + limit]

    def get_match_detail(self, mid, attempts=3):
        self._maybe("detail")
        return DETAILS[mid]

    def get_match_detail_nocache(self, mid, attempts=1):
        self._maybe("nocache")
        return DETAILS[mid]

    def get_ranker_stats(self, matchtype, pairs):
        self._maybe("ranker_stats")
        return [{"status": {"matchCount": 10, "shoot": 1.5}, "createDate": "2026-11-01T00:00:00"}]

    def _cache_path(self, mid):
        return Path(self.cache_dir or ".") / f"{mid}.json"


def _icon(url, folder, kind="", timeout=6):
    p = Path(folder) / (url.rsplit("/", 1)[-1] or "x")
    p.write_bytes(b"x" * 12)
    return p


@contextlib.contextmanager
def fakes(tmp: Path, web: bool = True):
    """바꿔 끼울 것 전부 — 끝나면 되돌린다."""
    seasons = [sn.Season(3, "시즌 3", date(2026, 8, 1), date(2026, 9, 1)),
               sn.Season(2, "시즌 2", date(2026, 7, 1), date(2026, 8, 1))]
    swaps = [
        (check_api, "FCOnlineAPI", FakeAPI),
        (config, "API_KEY", KEY), (config, "SERVER_URL", ""), (config, "WEB_DATA", web),
        (config, "DB_PATH", tmp / "fifa.db"), (config, "CACHE_DIR", tmp / "cache"),
        (config, "owner_dist_allowed", lambda: False),
        (images, "fetch", lambda sp, folder, timeout=6: Path(folder) / f"{sp}.png"),
        (images, "fetch_icon", _icon),
        (ranker, "fetch_manager_rank", lambda nick: NS(ranked=True, rank=123, team_value_text="1조 2,000억",
                                                       elo=1500, record_text="10승 2무 3패")),
        (ranker, "fetch_rank_rows", lambda n: NS(rows=[NS(nickname="가", team_color="리버풀", team_color_emblem="crests/1")],
                                                 date="Mon, 10 Nov 2026 05:00:00 GMT", ref_time=None)),
        (ranker, "judge_page", lambda n, rows, prev: "ok"),
        (ranker, "fetch_season_cut", lambda no, cut: 1234.0),
        (teamcolor, "fetch_list", lambda: [NS(name="리버풀", members=5, max_step=3, id=7, emblem="crests/1"),
                                           NS(name="한국", members=2, max_step=1, id=8, emblem="countries/1")]),
        (teamcolor, "fetch_detail", lambda tid: ("설명", [NS(step=1, members=5), NS(step=2, members=3)])),
        (teamcolor, "fetch_players", lambda tid: [NS(ovr=110, prices={1: 1000}, pay=12, name="가짜선수", spid=1)]),
        (playerinfo, "fetch_player_info", lambda sp: NS(salary=12, name="가짜선수", position="ST", ovr=110,
                                                        abilities=[1, 2], traits=[1], prices={1: 1000},
                                                        height="180cm", body_type="보통")),
        (playerinfo, "prices_as_int", lambda info: {1: 1000}),
        (playerinfo, "fetch_player_ability",
         lambda sp, strong=1: NS(ovr=110 + config.GRADE_OVR_BONUS[strong], abilities=[1], club_options=[1],
                                 enhance_options=[1, 2], feature_options=[], position_ovrs={"ST": 110})),
        (playerinfo, "fetch_boost_rows", lambda sp, level: []),
        (playerinfo, "page_ok", lambda info: True),
        (playerinfo, "sim_ok", lambda sim: True),
        (traitcollect, "new_session", lambda: None),
        (traitcollect, "fetch_owner_sn", lambda sess, nick: None),
        (sn, "fetch_seasons", lambda: seasons),
        (updatecheck, "check_full", lambda: NS(season_notice=None)),
        (rankcollect, "open_rank_db_ro", lambda: None),
        (ownerdist, "status_text", lambda conn, now: "받은 바퀴 없음"),
        (core_api, "predict_for", lambda **k: None),
        (core_api, "describe_prediction", lambda pr: "아직 모으는 중"),
    ]
    old = [(m, n, getattr(m, n)) for m, n, _v in swaps]
    FakeAPI.made, FakeAPI.fail = [], set()
    try:
        for m, n, v in swaps:
            setattr(m, n, v)
        yield
    finally:
        for m, n, v in old:
            setattr(m, n, v)


def run_terminal(argv: list[str], fail=(), web: bool = True) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d, fakes(Path(d), web=web):
        FakeAPI.fail = set(fail)
        buf = io.StringIO()
        saved = sys.argv
        sys.argv = ["check_api.py", *argv]
        try:
            with contextlib.redirect_stdout(buf):
                code = check_api.main()
        finally:
            sys.argv = saved
        return code, buf.getvalue()


# 골든 사례 — (이름, argv, 실패시킬 요청, 웹 데이터)
CASES = [
    ("usage", [], (), True),
    ("all", ["테스트구단주"], (), True),
    ("web_off", ["테스트구단주"], (), False),
    ("ouid_fail", ["테스트구단주"], ("ouid",), True),
    ("ids_fail", ["테스트구단주"], ("ids",), True),
    ("no_matches", ["테스트구단주"], ("ids_empty",), True),
    ("detail_fail", ["테스트구단주"], ("detail",), True),
    ("meta_fail", ["테스트구단주"], ("meta",), True),
    ("side_warns", ["테스트구단주"], ("basic", "maxdiv", "trades", "ranker_stats", "nocache"), True),
]


def _all_cases() -> dict:
    out = {}
    for name, argv, fail, web in CASES:
        code, text = run_terminal(argv, fail, web)
        out[name] = {"code": code, "text": text}
    return out


def test_terminal_output_matches_golden():
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = _all_cases()
    assert set(got) == set(want), sorted(set(got) ^ set(want))
    for name in want:
        assert got[name]["code"] == want[name]["code"], (name, got[name]["code"], want[name]["code"])
        if got[name]["text"] != want[name]["text"]:
            a, b = got[name]["text"].splitlines(), want[name]["text"].splitlines()
            diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            raise AssertionError(f"{name} {diff + 1}번째 줄이 다르다:\n  지금 {a[diff:diff + 1]}\n  골든 {b[diff:diff + 1]}")


def test_golden_is_not_empty():
    # 빈 골든은 빈 출력과 같다고 통과한다 — 사례마다 실제로 무언가를 찍었고, 실패 사례는 실제로 멈췄는지
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert want["all"]["code"] == 0 and "전부 통과" in want["all"]["text"], want["all"]
    assert want["all"]["text"].count("\n") > 30, "점검 줄이 너무 적다"
    for name in ("usage", "ouid_fail", "ids_fail", "detail_fail", "meta_fail"):
        assert want[name]["code"] == 1 and "전부 통과" not in want[name]["text"], name
    assert "[FAIL] 닉네임 조회" in want["ouid_fail"]["text"]
    assert KEY[:8] in want["all"]["text"], "터미널은 키 앞 8자를 보인다(앱은 안 보인다)"


def test_terminal_survives_cp949_console():
    # 남의 PC(CP949) 콘솔 — check_api 가 stdout 을 utf-8 로 다시 잡아 한글·기호에서 안 죽는다
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    r = subprocess.run([sys.executable, __file__, "--print-all"], cwd=ROOT, env=env, capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-500:]
    text = r.stdout.decode("utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))["all"]["text"]
    assert text.replace("\r\n", "\n") == want, text[-300:]


def main() -> int:
    if "--write" in sys.argv:
        GOLDEN.write_text(json.dumps(_all_cases(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"썼음: {GOLDEN}")
        return 0
    if "--print-all" in sys.argv:
        with tempfile.TemporaryDirectory() as d, fakes(Path(d)):
            sys.argv = ["check_api.py", "테스트구단주"]
            return check_api.main()
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
