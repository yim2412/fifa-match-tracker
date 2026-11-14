"""표시한 판 거르기 — 화면 끝-끝(109 조각 ①-2). `python tests/test_filter_screens.py` 로 실행(offscreen · 네트워크 없음 · QThread.start 막음).

지어낸 300판(`demodata`)을 창에 넣고, **모든 화면을 그려 둔 뒤**(캐시가 차 있다) 표시한 판을 바꿔 다시 그린다. 화면마다 그림(픽셀) 지문을 재서:
- 스위치 끔이면 표시가 있어도 숫자 화면은 표시가 없을 때와 같다(경기 목록만 알약·흐림이 붙어 다르다).
- 스위치를 켜면 숫자를 내는 화면은 전부 바뀐다 — **캐시를 하나라도 빠뜨리면 그 화면이 옛 그림 그대로라 여기서 잡힌다.**
- 넥슨이 매긴 기록(등급·보상·스쿼드) 화면은 스위치와 무관하게 그대로다. 경기 상세 창의 슛 맵도 스위치 끔 그림과 같다(R3).
- 모든 화면 키는 숫자 화면(`MUST_CHANGE`)·넥슨 기록 화면(`MUST_SAME`)·예외(`EXEMPT` — 이유와) 중 하나에 있어야 한다. 새 화면을 더하면 여기서 고른다.
- 켠 채 다시 계산하는 시간(`test_refilter_redraw_time` — 이 PC 의 실제 fifa.db 를 읽기 전용으로 사본을 떠서 · 없으면 건너뜀).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FIFA_FILTER_TRACE", "1")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="fifa109s_"))
os.environ["FIFA_DATA_DIR"] = str(SCRATCH)
for _k in ("NEXON_API_KEY", "FIFA_SERVER_URL", "FIFA_DEMO"):
    os.environ.pop(_k, None)

from PySide6.QtCore import QEvent, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QThread.start = lambda self, *a, **k: None   # 백그라운드 조회 차단 — 이 파일은 화면 계산만 본다(실제 스레드는 test_notes_thread)

# offscreen 기본 가상 화면(800x800)이면 "좁은 화면" 판정이 걸려 창이 최대화된다 — test_ui_smoke 와 같은 FHD 가상 화면
SCREENS_DIR = os.path.join(_ROOT, "tests", "screens")
if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and QApplication.instance() is None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=fhd100.json"
    _cwd = os.getcwd()
    os.chdir(SCREENS_DIR)
    try:
        _app = QApplication(sys.argv)
    finally:
        os.chdir(_cwd)
_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import config  # noqa: E402
import core_api as core  # noqa: E402
import demodata  # noqa: E402
import models  # noqa: E402
import sessions  # noqa: E402
import store  # noqa: E402
import theme as T  # noqa: E402
from widgets import MatchCard, MatchCardDelegate  # noqa: E402

T.apply(_app)
assert config.DATA_DIR == SCRATCH, config.DATA_DIR
config.SINGLE_INSTANCE = False
config.UPDATE_CHECK = False
config.UI_ANIMATE = False
app_main.GAME_CHECK = lambda: False
OUID, NICK = demodata.OUID, demodata.NICK

# ── 모든 화면 키의 분류 ───────────────────────────────────────────────────────
# 숫자를 내는 화면(경기 결과·내용에서 센다) — 표시한 판을 빼면 그림이 바뀌어야 한다
MUST_CHANGE = {
    "dashboard", "matches", "opponents", "analysis", "period", "records", "playlog", "review", "clutch", "goalflow",
    "diagnosis", "luck", "habits", "tactics", "shotmap", "route", "players", "finishing", "teamcolor", "oppcards",
    "calendar", "timeline", "rankercmp",
}
# 넥슨이 매긴 기록·저장 범위·경기와 무관한 화면 — 스위치와 무관하게 그대로(J3)
MUST_SAME = {"divstay", "rewards", "poslevel", "distcmp", "history", "ranktrend", "metatrend", "elodist", "weekly",
             "predcheck", "daywr", "tcboard", "valuebins", "tiermeans", "wrelo", "traits", "rankmove", "rewards_rank",
             "pricew", "rankerpick", "recommend", "opprank", "trades"}
# 그림이 안 바뀌어도 되는 숫자 화면 — 이 지어낸 자료·창 상태에서 바뀔 이유가 없다(이유를 적는다)
REDRAW_MAX_S = 2.0   # ROADMAP 109 J6 — 상한 2초. 전 화면 한꺼번에는 15,000판에서 4~5초라(위 시험이 찍는다) 열린 화면 하나에 건다
EXEMPT = {
    "arrivals": "지어낸 자료에 영입(거래) 기록이 없어 빈 화면이다 — 영입 전후 숫자는 test_filter 의 arrival_report 가 잰다",
}


def _build():
    data = demodata.generate(n=300)
    matches = [m for m in (models.parse_match(d, OUID) for d in data.details.values()) if m]
    matches.sort(key=lambda m: m.match_date or 0, reverse=True)
    details = [data.details[i] for i in data.ids]
    win = app_main.MainWindow(demodata.DemoAPI(data, cache_dir=SCRATCH / ".cache"))
    win.resize(1600, 900)
    win.LAZY_RENDER = False
    win._on_fetch_team_colors = lambda **k: None
    names = {r["id"]: r["name"] for r in data.meta["spid"]}
    positions = {r["spposition"]: r["desc"] for r in data.meta["spposition"]}
    div_names = {r["divisionId"]: r["divisionName"] for r in data.meta["division"]}
    win._rank_seasons = data.seasons         # 시즌 돌아보기가 시즌표가 있어야 그려진다(없으면 "시즌표가 없어" 한 줄 — 빈 화면은 바뀌었나를 못 잰다)
    win._on_loaded(matches, details, OUID, {"nickname": NICK, "level": 7}, names, positions, 0, len(matches),
                   None, "-", False, "", {}, div_names)
    # 상대 팀컬러 — 조회 결과가 없으면 [상대 팀컬러]·[포지션별 최다 상대]가 빈 안내만 그린다. 지어낸 값을 넣는다(네트워크 없이)
    opps = sorted({m.opponent for m in matches})
    win._team_colors = {n: f"팀컬러{i % 4}" for i, n in enumerate(opps)}
    win._team_values = {n: 1_000_000_000 * (1 + i % 5) for i, n in enumerate(opps)}
    win._teamcolor_raw = {n: (c, win._team_values[n], "") for n, c in win._team_colors.items()}
    win.show()
    _app.processEvents()
    win.LAZY_RENDER = True          # 실제 앱처럼 열린 화면만 그린다 — 숨은 채 그린 그림은 열 때와 배치가 달라 지문이 흔들린다
    return win, matches, details


WIN, MATCHES, DETAILS = _build()
IDS = [m.match_id for m in MATCHES]
MARKED = IDS[3::25]             # 300판 중 12판 — 결과가 섞여 있다


def _views():
    return {key: views for key, views in WIN.VIEW_OF_KEY.items()}


_SKIP_ATTR = ("cache", "fit", "pix", "icon", "hover", "timer", "anim", "font", "size", "width", "height", "geom", "rect",
              "pos", "emblem", "style", "tip", "hit")    # 그릴 때마다 바뀌는 배치·그림 캐시 — 숫자가 아니다
_PLAIN = (int, float, str, bool, list, tuple, dict, set, frozenset, type(None))
DUMPS: dict = {}     # 마지막으로 연 자리의 글(실패했을 때 무엇이 달랐는지 보려고)


def _dump(root) -> str:
    """위젯 아래 글자·표 칸·줄 카드 목록·그래프의 데이터 속성을 글로 모은 것 — 그림(픽셀)은 열 때의 배치·글자 번짐에 따라 흔들려
    "숫자가 바뀌었나" 를 재는 데 못 쓴다(같은 상태를 두 번 그려도 달라졌다). 그림만 있는 그래프는 데이터 속성으로 잰다."""
    from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QTableWidget, QWidget
    parts: list[str] = []
    for w in [root, *root.findChildren(QWidget)]:
        if isinstance(w, QLabel):
            parts.append(w.text())
        elif isinstance(w, QAbstractButton):
            parts.append(w.text())
        elif isinstance(w, QLineEdit):
            parts.append(w.text())
        elif isinstance(w, QTableWidget):
            for r in range(w.rowCount()):
                parts.append("|".join((w.item(r, c).text() if w.item(r, c) is not None else "")
                                      for c in range(w.columnCount())))
        elif hasattr(w, "all_rows"):
            parts.append(repr(w.all_rows()))
        elif type(w).__module__.split(".")[0] in ("charts", "widgets", "dashboard"):
            for k, v in sorted(vars(w).items()):
                if isinstance(v, _PLAIN) and not any(t in k.lower() for t in _SKIP_ATTR) and not k.startswith("__"):
                    parts.append(f"{type(w).__name__}.{k}={v!r}")
    return chr(10).join(parts)


def _scope(menu: str, tab: str | None):
    tabs = WIN._page_tabs.get(menu)
    if tabs is not None and tab is not None:
        return tabs.page(tabs.names().index(tab))
    return WIN.pages.currentWidget().widget()


def _hash_view(menu: str, tab: str | None) -> str:
    WIN._go_page(menu, tab)
    _app.processEvents()
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # 다시 그리며 지운 위젯이 아직 자식으로 남아 글에 섞인다
    text = _dump(_scope(menu, tab))
    DUMPS[(menu, tab)] = text
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _visit() -> dict:
    """모든 자리를 차례로 열어(낡은 화면은 열 때 그려진다) 키마다 그림 지문을 이어 붙인 것."""
    out = {}
    for key, views in _views().items():
        out[key] = "|".join(_hash_view(m, t) for m, t in views)
    return out


def _fingerprints() -> dict:
    """거르기 길(_refilter)을 한 번 지난 뒤 모든 자리를 연다 — 두 상태를 같은 이력으로 견주려고 늘 이 꼴."""
    WIN._refilter()
    return _visit()


def _reset(win=WIN) -> None:
    win._exclude_on = False
    win._notes = store.EMPTY_NOTES
    core.set_excluded(())
    core.bump_filter_version()
    win.chk_exclude.blockSignals(True)
    win.chk_exclude.setChecked(False)
    win.chk_exclude.blockSignals(False)
    win._write_exclude_switch(False)      # 값은 settings.ini 에 남는다 — 다음 창이 켜진 채로 시작하지 않게
    win._refilter()


def _mark(ids, mark="cut") -> None:
    win = WIN
    win._notes = store.Notes({i: mark for i in ids}, {}, {})
    win._opp_ouid_cache = None


def test_every_screen_key_is_classified():
    keys = set(WIN._renderers())
    classified = MUST_CHANGE | MUST_SAME | set(EXEMPT)
    assert not (MUST_CHANGE & MUST_SAME) and not (MUST_CHANGE & set(EXEMPT)) and not (MUST_SAME & set(EXEMPT))
    assert keys == classified, f"분류 안 된 화면 키: {sorted(keys - classified)} · 없는 키: {sorted(classified - keys)}"


def test_rendering_is_deterministic_so_a_fingerprint_change_means_a_real_change():
    """같은 상태를 두 번 그리면 지문이 같다 — 아니면 아래 비교가 시계·애니메이션 때문에 흔들린다(대조)."""
    _reset()
    a, b = _fingerprints(), _fingerprints()
    flaky = sorted(k for k in a if a[k] != b[k])
    assert not flaky, f"두 번 그려 달라지는 화면: {flaky}"


def test_switch_off_keeps_every_number_screen_even_with_marks_present():
    """스위치 끔이면 표시가 있어도 지금 그림 그대로 — 경기 목록(알약·흐림)만 다르다."""
    _reset()
    base = _fingerprints()
    _mark(MARKED)
    WIN._refilter()                       # 스위치는 꺼진 채 — 거를 묶음은 비어 있어야
    assert core.excluded() == frozenset() and core.filter_version() == 0
    after = _visit()
    changed = sorted(k for k in base if base[k] != after[k])
    assert changed == ["matches"], f"스위치가 꺼져 있는데 달라진 화면: {changed}"
    _reset()


def test_switch_on_changes_every_number_screen_and_keeps_nexon_records():
    _reset()
    base = _fingerprints()               # 모든 캐시가 찬다
    _mark(MARKED)
    WIN.chk_exclude.setChecked(True)     # 위쪽 막대 스위치를 실제로 눌러 본다 — 배선(toggled → _on_exclude_toggled → _refilter)까지
    assert WIN._exclude_on and core.excluded() == frozenset(MARKED), core.excluded()
    after = _visit()
    same = sorted(k for k in base if base[k] == after[k])
    stale = sorted(set(same) & MUST_CHANGE)
    assert not stale, f"표시한 판을 뺐는데 그림이 안 바뀐 숫자 화면(캐시를 빠뜨렸을 수 있다): {stale}"
    moved = sorted(set(MUST_SAME) & {k for k in base if base[k] != after[k]})
    assert not moved, f"넥슨 기록 화면이 스위치에 따라 바뀌었다: {moved}"
    # 막대: 몇 판을 뺐는지가 늘 보인다(U7)
    assert WIN.lb_excluded.isVisibleTo(WIN) and f"{len(MARKED)}" in WIN.lb_excluded.text(), WIN.lb_excluded.text()
    # 끄면 처음과 같다
    WIN.chk_exclude.setChecked(False)
    assert core.excluded() == frozenset() and not WIN.lb_excluded.isVisibleTo(WIN)
    back = _visit()
    # 표시(알약·흐림)는 남아 있으니 경기 목록만 다르다 — 나머지는 처음과 같다
    assert sorted(k for k in base if base[k] != back[k]) == ["matches"], \
        f"스위치를 끄고도 처음과 다른 화면: {sorted(k for k in base if base[k] != back[k])}"
    _reset()


def test_marked_matches_stay_in_the_list_dimmed_and_nexon_numbers_do_not_move():
    _reset()
    _mark(MARKED)
    WIN.chk_exclude.setChecked(True)
    WIN._go_page("경기 목록")                                 # 열린 화면만 그린다 — 카드가 새로 만들어지게
    cards = {c.match_id: c for c in WIN._match_cards}
    in_range = [m.match_id for m in WIN._slice()[0]]          # 빠른 범위 안의 판(기본은 최근 N판) — 표시와 무관하게 그만큼이 남는다
    assert set(cards) == set(in_range), "표시한 판이 목록에서 빠졌다(숨기지 않는다 — U1)"
    marked_in = [i for i in MARKED if i in cards]
    assert marked_in, "시험이 공허하다 — 범위 안에 표시한 판이 없다"
    assert all(cards[i].mark == "끊김" for i in marked_in) and not any(cards[i].mark for i in cards if i not in MARKED)
    dele = MatchCardDelegate
    assert dele.opacity(cards[marked_in[0]]) == T.MARK_DIM_OPACITY < 1.0
    assert dele.opacity(next(c for i, c in cards.items() if i not in MARKED)) == 1.0
    # 넥슨이 매긴 값 — 등급 이력·보상은 표시와 무관(같은 판 묶음이어야 견줘진다)
    on = (len(WIN._season_wins()), [(w.when, w.division) for w in WIN._season_wins()],
          core.win_rewards([w for w in WIN._season_wins() if w.division is not None]))
    WIN.chk_exclude.setChecked(False)
    off = (len(WIN._season_wins()), [(w.when, w.division) for w in WIN._season_wins()],
           core.win_rewards([w for w in WIN._season_wins() if w.division is not None]))
    assert on == off, "표시한 판을 빼는 것이 등급·보상 숫자를 움직였다"
    assert len(WIN._matches_all) == len(IDS)
    _reset()


def test_dimmed_card_is_really_painted_fainter():
    """흐리게 그리기를 빼면(변이) 표시한 카드와 아닌 카드의 그림이 같아진다 — 같은 카드 글에 표시만 켜고 그려 견준다."""
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QStyleOptionViewItem
    view = WIN.match_list
    dele = view.card_delegate
    base = WIN._match_cards[0]
    marked = MatchCard(**{**base.__dict__, "mark": "끊김"})

    def paint(card) -> bytes:
        w, h = 900, dele.row_height(view.font())
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        opt = QStyleOptionViewItem()
        opt.rect = QRectF(0, 0, w, h).toRect()
        opt.font = view.font()
        class _Idx:                       # 대리자의 진짜 paint 길(투명도를 거는 자리)을 탄다
            @staticmethod
            def data(_role):
                return card
        dele.paint(p, opt, _Idx())
        p.end()
        return bytes(img.constBits())

    a, b = paint(base), paint(marked)
    assert a != b, "표시한 카드가 안 달라 보인다"
    # 카드 바탕의 빈 한 점(위 여백 안 · 글이 없다)의 알파 — 표시한 쪽이 낮다(투명도 0.5). 흐림이 실제로 그림에 들어갔는지
    x, y, w = 450, dele.MARGIN + 3, 900
    idx = 4 * (y * w + x) + 3
    assert b[idx] < a[idx] and a[idx] == 255, (a[idx], b[idx])


def test_refilter_does_not_touch_the_season_combo_or_the_lists():
    """J7 — 거르기는 경기 목록을 안 바꾼다: _on_loaded·_apply_season·_rebuild_season_combo 를 안 부른다."""
    calls = []
    keep = (WIN._on_loaded, WIN._apply_season, WIN._rebuild_season_combo)
    ids_before = (id(WIN._matches_all), id(WIN._details_all), id(WIN._matches), id(WIN._details))
    WIN._on_loaded = lambda *a, **k: calls.append("on_loaded")
    WIN._apply_season = lambda *a, **k: calls.append("apply_season")
    WIN._rebuild_season_combo = lambda *a, **k: calls.append("rebuild_combo")
    try:
        _mark(MARKED)
        WIN._exclude_on = True
        WIN._refilter()
    finally:
        WIN._on_loaded, WIN._apply_season, WIN._rebuild_season_combo = keep
    assert calls == [], calls
    assert ids_before == (id(WIN._matches_all), id(WIN._details_all), id(WIN._matches), id(WIN._details))
    _reset()


def test_refilter_bumps_the_filter_version_every_time():
    _reset()
    _mark(MARKED)
    WIN._exclude_on = True
    v0 = core.filter_version()
    WIN._refilter()
    v1 = core.filter_version()
    WIN._refilter()
    v2 = core.filter_version()
    assert v1 != v0 and v2 != v1 and v1 != 0, (v0, v1, v2)
    _reset()


def test_switch_value_is_written_to_settings_ini_at_once():
    """J10 소항목 — 쓰는 자리에서 sync. 닫을 때만 쓰면 강제 종료에 값이 사라진다 · 새 창은 그 값으로 켜진다."""
    WIN.chk_exclude.setChecked(True)
    ini = Path(config.SETTINGS_PATH).read_text(encoding="utf-8", errors="replace")
    assert "exclude_shown=true" in ini, ini
    assert WIN._read_exclude_switch() is True
    WIN.chk_exclude.setChecked(False)
    assert WIN._read_exclude_switch() is False
    _reset()


def test_match_detail_xg_is_the_same_with_the_switch_on():
    """R3 — 상세 창의 슛 맵·xG 는 감싸지 않은 core.raw 로 그린다. 표시한 판을 열어도 스위치 끔일 때와 같은 그림이다."""
    target = DETAILS[MATCHES.index(next(m for m in MATCHES if m.match_id == MARKED[0]))]
    _reset()
    dlg_off = WIN._make_match_detail(target, tab="슛")
    off = [dlg_off.tabs.widget(i).grab().toImage() for i in range(dlg_off.tabs.count())]
    _mark(MARKED)
    WIN.chk_exclude.setChecked(True)
    dlg_on = WIN._make_match_detail(target, tab="슛")
    on = [dlg_on.tabs.widget(i).grab().toImage() for i in range(dlg_on.tabs.count())]
    for name, a, b in zip(app_main.MatchDetailDialog.TABS, off, on):
        if name == "상대 전적":      # 이 상대와의 맞대결 승무패 — 경기 결과 집계(F)라 스위치를 따른다
            continue
        assert bytes(a.constBits()) == bytes(b.constBits()), f"상세 창 [{name}] 탭이 스위치에 따라 달라졌다"
    # 대조 — 같은 경기를 감싼 함수로 세면 0 이 된다(보호를 뜯으면 위 그림이 이렇게 된다)
    wrapped = core.shot_map([target], OUID)
    raw = core.raw.shot_map([target], OUID)
    assert raw.total > 0 and wrapped.total == 0, (raw.total, wrapped.total)
    for d in (dlg_off, dlg_on):
        app_main.MainWindow._close_match_detail(d)
    _reset()


def test_sessions_split_at_a_marked_match_by_design_M11():
    """M11 — 세션 끊기는 F 라 표시한 판 자리에서 세션이 갈릴 수 있다(의도). 20분 간격 셋 중 가운데를 빼면 40분이 되어 갈린다."""
    from datetime import datetime, timedelta
    t0 = datetime(2026, 11, 1, 10, 0)
    ms = [models.MatchSummary(match_id=f"x{i}", match_date=t0 + timedelta(minutes=20 * i), match_type=52, my_nickname="me",
                              opponent="o", result="승", my_goals=1, opp_goals=0, possession=50, shoot_total=0,
                              shoot_effective=0, pass_try=0, pass_success=0, rating=0.0, draw_possible=True)
          for i in range(3)]
    core.set_excluded(())
    assert len(core.split_sessions(ms)) == 1
    core.set_excluded({"x1"})
    try:
        assert len(core.split_sessions(ms)) == 2, "가운데 판을 뺐는데 세션이 안 갈렸다"
        assert len(core.raw.split_sessions(ms)) == 1
    finally:
        core.set_excluded(())


def test_opponent_note_button_follows_the_ouid_lookup():
    """J4 — 상대 메모 단추는 그 닉네임의 상대 ouid 를 찾을 때만 켜진다(못 찾으면 흐림). 줄을 고르면 켜진다."""
    _reset()
    WIN._go_page("상대 전적", "전적")
    _app.processEvents()
    assert WIN.opp_list.rowCount() > 0
    assert not WIN.btn_opp_note.isEnabled(), "고른 줄이 없는데 켜져 있다"
    WIN.opp_list.setCurrentIndex(WIN.opp_list._model.index(0, 0))
    _app.processEvents()
    nick = WIN._opp_row_nick()
    assert nick and WIN._opp_ouid_of(nick), nick
    assert WIN.btn_opp_note.isEnabled()
    real = WIN._details_all
    WIN._details_all = []                    # 저장된 경기 상세가 없다 — ouid 를 못 찾는다
    try:
        WIN._refresh_opp_note_button()
        assert not WIN.btn_opp_note.isEnabled(), "ouid 를 못 찾는데 단추가 켜져 있다"
    finally:
        WIN._details_all = real
    # 태그·메모가 줄 글에 붙는다
    oid = WIN._opp_ouid_of(nick)
    WIN._notes = store.Notes({}, {}, {oid: ("자주 만남", ("역습",))})
    WIN._opp_ouid_cache = None
    WIN._invalidate("opponents")
    sub = next(r.sub for r in WIN.opp_list.all_rows() if r.key == nick)
    assert "#역습" in sub and "자주 만남" in sub, sub
    _reset()


# ── 캐시 키 (J6) ────────────────────────────────────────────────────────────
# 캐시 이름 → 그 캐시 키에서 거르기 판본이 든 자리를 꺼내는 함수. 키를 만드는 자리를 새로 더하면 여기에도(아래 소스 검사가 잡는다)
def _fv_slots(win) -> dict:
    out = {
        "_narrate_key": lambda: win._narrate_key[-1],
        "_shot_sum_key": lambda: win._shot_sum_key[-1],
        "_timeline_cache": lambda: win._timeline_cache[0][-1],
        "_history_cache": lambda: win._history_cache[0][0][-1],          # _details_key() 안
        "_records_cache": lambda: win._records_cache[0][1][-1],
        "_arrivals_cache": lambda: win._arrivals_cache[0][0][-1],
        "_fmchange_cache": lambda: win._fmchange_cache[0][0][-1],
        # 랭킹 수집 값이 있어야 그려지는 화면의 캐시 — 안 찼으면 지금 판본으로 쳐서 건너뛴다
        "_reward_cache": lambda: win._reward_cache[0][0][-1] if win._reward_cache is not None else core.filter_version(),
        "_formation_cache": lambda: win._formation_cache[0][-1],
        "_wins_cache_key": lambda: win._wins_cache_key[-1],
    }
    for name in win._fun_cache:                                              # 라이벌·오늘의 선수·내 등급과 포메이션(_saved_calc)
        out[f"_fun_cache[{name}]"] = (lambda n=name: win._fun_cache[n][0][1][-1])
    return out


# 거르기와 무관해 판본을 안 싣는 캐시(이유) — 나머지 키·캐시 속성은 전부 위 표에 있어야 한다
CACHE_EXEMPT = {
    "_dist_key": "구단주 분포 판정 재료(J3 — 분포와 같은 판 묶음이어야 백분위가 맞는다)",
    "_poslevel_key": "자리별 수준(J3 — 같은 이유)",
    "_distcmp_key": "분포 대비(J3 — 같은 이유)",
    "_season_map_key": "시즌 나누기(S — 시즌표와 경기 목록만 본다)",
    "_cal_key_cache": "날짜 → 시즌 지도(S — 시즌 맵이 바뀔 때만 비운다)",
    "_emblem_cache": "엠블럼 지도(S)",
    "_opp_ouid_cache": "닉네임 → 상대 ouid(S — 저장 목록 원본에서 찾는다)",
    "_streak_done": "연승 섞기 결과 — M13 이 판본을 따로 비교한다(HabitsWorker._fv)",
    "_review_chip_keys": "시즌 돌아보기 칩 목록(S)",
    "_div_all_key": "등급 이력(S — 넥슨 기록이라 안 거른다 · _render_all 이 부른다)",
    "_sc_cache": "슈챔 진입 시각(S — _div_all_key 와 한 쌍)",
    "_wins_cache": "값 쪽 이름 — 키(_wins_cache_key)가 판본을 싣는다",
}


def test_every_cache_key_carries_the_current_filter_version():
    """표시한 판을 바꾼 뒤 모든 화면을 다시 그리면, 그 화면 캐시들의 키가 지금 거르기 판본을 품고 있다 — 키에서 판본을 빼면(변이)
    키가 같아져 옛 값이 그대로 쓰이는데, 그림 지문은 같은 화면의 다른 칸이 바뀌어 못 잡는다(슛 요약 칸 · 세션 문장 등)."""
    _reset()
    _fingerprints()
    _mark(MARKED)
    WIN.chk_exclude.setChecked(True)
    _visit()
    want = core.filter_version()
    assert want != 0
    slots = _fv_slots(WIN)
    stale = {}
    for name, get in slots.items():
        try:
            got = get()
        except (TypeError, IndexError, AttributeError, KeyError) as e:
            stale[name] = f"키에서 판본을 못 꺼냈다({type(e).__name__}) — 캐시가 안 찼거나 키 모양이 바뀌었다"
            continue
        if got != want:
            stale[name] = f"키의 판본 {got} != 지금 {want}"
    assert not stale, stale
    # 표가 비어 있지 않은지(안 찬 캐시를 단언하지 않는다)
    assert len(slots) >= 12, sorted(slots)
    _reset()


def test_cache_attributes_in_source_are_all_classified():
    """app_main 이 `self._…_cache` · `…_key` 를 새로 만들면 위 표(판본을 싣는다)나 CACHE_EXEMPT(이유)에 적어야 한다."""
    import re
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    found = set(re.findall(r"self\.(_\w*?(?:_cache|_key)|_streak_done)\s*(?::[^=\n]+)?=(?!=)", src))
    found |= set(re.findall(r'getattr\(self, "(_\w*?(?:_cache|_key))"', src))
    known = set(_fv_slots(WIN)) | set(CACHE_EXEMPT)
    known_names = {n.split("[")[0] for n in known} | {"_fun_cache"}
    missing = sorted(n for n in found if n not in known_names)
    assert not missing, f"분류 안 된 캐시: {missing} — 거르기 판본을 키에 더하거나 CACHE_EXEMPT 에 이유와 적는다"
    assert {"_narrate_key", "_timeline_cache", "_shot_sum_key"} <= found, "소스 검사가 공허하다 — 캐시를 못 찾았다"



# ── 다시 그리는 시간 ─────────────────────────────────────────────────────────


def _real_db() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    p = Path(base) / "피파전적관리" / "fifa.db" if base else None
    return p if p is not None and p.is_file() else None


def test_refilter_redraw_time():
    """스위치를 켜고 _refilter 로 모든 화면을 다시 계산하는 시간(상한 2초 · ROADMAP 109 J6). 이 PC 의 실제 fifa.db 가 있어야 한다 —
    읽기 전용 연결로 sqlite backup 을 떠 스크래치 폴더에 사본을 만들고(실제 파일에는 쓰지 않는다) 그 사본으로 잰다. 없으면 건너뜀."""
    src = _real_db()
    if src is None:
        print("[SKIP] test_refilter_redraw_time: 이 PC 에 fifa.db 가 없다")
        return
    copy = SCRATCH / "real_copy.db"
    ro = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(copy))
        try:
            ro.backup(dst)
        finally:
            dst.close()
    finally:
        ro.close()
    keep_db = config.DB_PATH
    config.DB_PATH = copy
    try:
        conn = store.open_db(copy)
        try:
            mine = store.my_account(conn, config.DEFAULT_MATCH_TYPE)
        finally:
            conn.close()
        if not mine or not mine.get("ouid"):
            print("[SKIP] test_refilter_redraw_time: 사본에 내 계정이 없다")
            return
        t0 = time.perf_counter()
        matches, details = app_main.load_saved(mine["ouid"], config.DEFAULT_MATCH_TYPE)
        load_s = time.perf_counter() - t0
        win = app_main.MainWindow(demodata.DemoAPI(demodata.generate(n=1), cache_dir=SCRATCH / ".cache2"))
        win.LAZY_RENDER = False
        win._on_fetch_team_colors = lambda **k: None
        win._on_loaded(matches, details, mine["ouid"], {"nickname": mine.get("nickname") or "me", "level": 1}, {}, {}, 0,
                       len(matches), None, "-", False, "", {}, {})
        win.show()
        _app.processEvents()
        step = max(1, len(matches) // 40)
        win._notes = store.Notes({m.match_id: "cut" for m in matches[::step]}, {}, {})
        win._exclude_on = False
        win.chk_exclude.blockSignals(True)
        win.chk_exclude.setChecked(False)
        win.chk_exclude.blockSignals(False)
        # ① 실제 앱처럼 열린 화면만 — 스위치를 누르면 모든 화면이 낡음이 되고, 한 화면씩 다시 계산되는 시간을 잰다
        win.LAZY_RENDER = True
        win._go_page("대시보드")
        _app.processEvents()
        t1 = time.perf_counter()
        win.chk_exclude.setChecked(True)
        click_s = time.perf_counter() - t1
        worst = (click_s, "스위치를 누른 화면(대시보드)")
        for key in win._renderers():
            win._dirty.add(key)
            t2 = time.perf_counter()
            win._render_key(key)
            took = time.perf_counter() - t2
            if took > worst[0]:
                worst = (took, key)
        # ② 전 화면을 한꺼번에(ROADMAP 의 "전 화면 다시 계산") — 열린 적 없는 화면까지 전부
        win.LAZY_RENDER = False
        t3 = time.perf_counter()
        win._refilter()
        _app.processEvents()
        redraw_s = time.perf_counter() - t3
        print(f"       실제 사본 경기 {len(matches):,}판 · 표시 {len(win._notes.marks)}판 · 읽기 {load_s:.1f}초 · 스위치를 누른 화면 {click_s:.2f}초 · "
              f"가장 느린 화면 {worst[0]:.2f}초({worst[1]}) · 전 화면 한꺼번에 다시 계산 {redraw_s:.2f}초")
        assert worst[0] <= REDRAW_MAX_S, f"한 화면을 다시 계산하는 데 {worst[0]:.2f}초({worst[1]}) — 상한 {REDRAW_MAX_S}초"
        win.hide()
        win.deleteLater()
    finally:
        config.DB_PATH = keep_db
        core.set_excluded(())


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed, started = 0, time.monotonic()
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과 · {time.monotonic() - started:.0f}초")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
