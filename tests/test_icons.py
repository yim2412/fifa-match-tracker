"""표 칸 그림(40단계) — 주소 틀 · 받기(Referer·부정 캐시·반쯤 쓴 파일) · 열 폭 · 키가 행을 따라가는지 · 로더 정리.

네트워크 없이 돈다: 받기는 가짜 세션·가짜 fetch_icon, QThread.start 는 그 자리에서 run(또는 막는다).
`python tests/test_icons.py` — 화면 배선은 test_ui_smoke 가, 이 파일은 그림 부품 하나하나가 **조용히** 틀어지지 않는지만 잰다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QThread, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import config  # noqa: E402
import images  # noqa: E402
import models  # noqa: E402
import nexon_api  # noqa: E402
import rankmeta  # noqa: E402
from widgets import ICON_KEY_ROLE, FitTableWidget, IconCell, measure_icon_gap  # noqa: E402

import test_rankmeta as tr  # noqa: E402 — 지어낸 rank.db

MW = app_main.MainWindow
_REAL_START = QThread.start


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="icons_"))


# ── I1 주소 ──────────────────────────────────────────────────────────────────

def test_icon_url_table():
    cdn = images.ICON_CDN
    assert images.icon_url("trait", 55) == f"{cdn}traits/trait_icon_55.png"
    assert images.icon_url("train", 0) == f"{cdn}trainingTrait/training_trait_icon_000.png", "훈련 특성은 세 자리"
    assert images.icon_url("train", 12) == f"{cdn}trainingTrait/training_trait_icon_012.png"
    # 엠블럼은 종류마다 칸 틀이 다르다
    assert images.icon_url("emblem", "crests/l11.png") == f"{cdn}crests/light/medium/l11.png"
    assert images.icon_url("emblem", "countries/f_5.png") == f"{cdn}countries/largeflags/f_5.png"
    assert images.icon_url("emblem", "teamcolorboost/4_l9.png") == f"{cdn}teamcolorboost/icon/medium/4_l9.png"
    assert images.icon_url("emblem", "teamcolor/x.png") == "", "표 밖 종류(rank.db 의 ssl 키 13행)는 그림 없음"
    assert images.icon_url("emblem", "crests/") == "" and images.icon_url("emblem", "crests") == ""
    assert images.icon_url("division", 3) == images.division_icon_url(3)
    seasons = {101: {"seasonImg": "https://x/s101.png"}}
    assert images.icon_url("season", 101, seasons) == "https://x/s101.png"
    assert images.icon_url("season", 999, seasons) == "" and images.icon_url("season", 101) == ""
    for kind in ("trait", "emblem", "division", "season", "train"):
        assert images.icon_url(kind, None) == "" and images.icon_url(kind, "") == "", kind
    assert images.icon_url("nope", 1) == ""


# ── I2 받기 ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, content=b"PNG"):
        self.status_code, self.content = status, content


class _Sess:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.resps.pop(0)


def _with_session(sess):
    old = images._session
    images._session = sess
    images._missing.clear()
    return old


def test_fetch_icon_sends_referer_and_caches():
    d, sess = _tmp(), _Sess(_Resp(200, b"PNGDATA"))
    old = _with_session(sess)
    try:
        p = images.fetch_icon("https://x/a.png", d, "emblem")
        assert p is not None and p.read_bytes() == b"PNGDATA" and p.parent == d / "emblem"
        assert sess.calls[0][1]["headers"]["Referer"] == "https://fconline.nexon.com/", "Referer 없이 받으면 403 인 주소가 있다"
        assert images.fetch_icon("https://x/a.png", d, "emblem") == p and len(sess.calls) == 1, "캐시에 있으면 다시 안 받는다"
        assert not list(d.rglob("*.tmp")), "임시 파일이 남았다"
    finally:
        images._session = old
        shutil.rmtree(d, ignore_errors=True)


def test_fetch_url_sends_referer_too():
    """카드 창 사진·국기·효과 창 엠블럼이 쓰는 fetch_url 도 Referer 를 붙인다 — 한 곳(_REFERER)에서."""
    d, sess = _tmp(), _Sess(_Resp(200, b"PNG"))
    old = _with_session(sess)
    try:
        assert images.fetch_url("https://x/photo.jpg", d) is not None
        assert sess.calls[0][1]["headers"]["Referer"] == images._REFERER == "https://fconline.nexon.com/"
    finally:
        images._session = old
        shutil.rmtree(d, ignore_errors=True)


def test_fetch_icon_negative_cache_only_for_403_404():
    d = _tmp()
    sess = _Sess(_Resp(404), _Resp(500), _Resp(200, b"ok"), _Resp(403))
    old = _with_session(sess)
    try:
        assert images.fetch_icon("https://x/404.png", d, "k") is None
        assert images.fetch_icon("https://x/404.png", d, "k") is None
        assert len(sess.calls) == 1, "404 는 세션 안에서 다시 묻지 않는다"
        assert images.fetch_icon("https://x/500.png", d, "k") is None
        assert images.fetch_icon("https://x/500.png", d, "k") is not None, "500 은 일시 실패 — 다시 시도한다"
        assert len(sess.calls) == 3
        assert images.fetch_icon("https://x/403.png", d, "k") is None
        assert images.fetch_icon("https://x/403.png", d, "k") is None and len(sess.calls) == 4
        assert images.fetch_icon("", d, "k") is None
    finally:
        images._session = old
        images._missing.clear()
        shutil.rmtree(d, ignore_errors=True)


def test_partial_file_never_seen():
    """받은 내용은 임시 파일에 쓰고 os.replace — 최종 경로에는 다 쓴 파일만 나타난다(동시에 받는 두 로더)."""
    d, sess = _tmp(), _Sess(_Resp(200, b"X" * 64))
    old = _with_session(sess)
    seen = []
    real = os.replace

    def spy(src, dst):
        seen.append((Path(src).exists(), Path(dst).exists(), Path(src).read_bytes()))
        return real(src, dst)

    os.replace = spy
    try:
        p = images.fetch_icon("https://x/p.png", d, "k")
    finally:
        os.replace = real
        images._session = old
        shutil.rmtree(d, ignore_errors=True)
    assert p is not None and seen == [(True, False, b"X" * 64)], seen


def test_prune_keeps_icons():
    d = _tmp()
    try:
        (d / "icons" / "emblem").mkdir(parents=True)
        (d / "icons" / "emblem" / "a.png").write_bytes(b"x")
        (d / "match1.json").write_text("{}", encoding="utf-8")
        (d / "meta_division.json").write_text("[]", encoding="utf-8")
        api = nexon_api.FCOnlineAPI("k", cache_dir=d)
        n, _ = api.prune_detail_cache({"match1"})
        assert n == 1 and not (d / "match1.json").exists(), "DB 에 있는 경기 캐시는 지운다(이게 안 되면 아래 단언이 비어 있다)"
        assert (d / "icons" / "emblem" / "a.png").exists() and (d / "meta_division.json").exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── I6 등급 번호 · 검색 전 재료 ─────────────────────────────────────────────

def test_division_icon_index():
    meta = [{"divisionId": 800}, {"divisionId": 900}, {"divisionId": 1000}]
    assert models.division_icon_index(900, meta) == 1, "번호 = 원본 목록의 순서"
    assert models.division_icon_index(1000, meta) == 2
    assert models.division_icon_index(777, meta) is None and models.division_icon_index(None, meta) is None
    assert models.division_icon_index(900, None) is None and models.division_icon_index(900, []) is None


def test_badges_before_first_search():
    """파일 있음 · 없음 · 지난주 파일 — 지난주 파일도 읽는다(get_meta 의 _meta_read 는 None 이라 매주 첫 검색 전 배지가 사라졌다)."""
    d = _tmp()
    try:
        assert nexon_api.read_meta_on_disk(d, "division") is None, "새 설치(파일 없음) → None = 글자만"
        p = d / "meta_division.json"
        p.write_text(json.dumps([{"divisionId": 800}, {"divisionId": 900}]), encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(p, (old, old))
        got = nexon_api.read_meta_on_disk(d, "division")
        assert got and models.division_icon_index(900, got) == 1, "지난주(40일 전) 파일도 읽는다"
        p.write_text("{깨진", encoding="utf-8")
        assert nexon_api.read_meta_on_disk(d, "division") is None, "깨진 파일은 None"
        p.write_text('{"a": 1}', encoding="utf-8")
        assert nexon_api.read_meta_on_disk(d, "division") is None, "목록이 아니면 None"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── I7 이름 → 엠블럼 ─────────────────────────────────────────────────────────

def test_emblems_by_name():
    db = tr.DB()
    try:
        dup = sorted(config.TEAMCOLOR_DUP_NAMES)[0]
        db.snap(tr.T0, [
            {"sn": 1, "rank": 1, "color": "Alpha"}, {"sn": 2, "rank": 2, "color": "Alpha"},
            {"sn": 3, "rank": 3, "color": "Beta"}, {"sn": 4, "rank": 4, "color": "Beta"},
            {"sn": 5, "rank": 5, "color": dup}, {"sn": 6, "rank": 6, "color": "Gamma"}])
        with db.conn:
            db.conn.executemany("INSERT INTO snapshot_emblems VALUES (?,?,?)", [
                (1, 1, "crests/l1.png"), (1, 2, "crests/l1.png"),      # Alpha: 같은 엠블럼 둘 → 넣는다
                (1, 3, "crests/l2.png"), (1, 4, "crests/l3.png"),      # Beta: 서로 다른 엠블럼 → 어느 그림인지 모른다 → 뺀다
                (1, 5, "crests/l4.png")])                              # 이름 겹침 쌍 → 뺀다 · Gamma 는 엠블럼 없음
        got = rankmeta.emblems_by_name(db.ro())
        assert got == {"Alpha": "crests/l1.png"}, got
        db2 = tr.DB()
        try:
            assert rankmeta.emblems_by_name(db2.ro()) == {}, "스냅숏이 없으면 빈 dict"
        finally:
            db2.close()
    finally:
        db.close()


# ── I3 표 칸 ─────────────────────────────────────────────────────────────────

def _table(icons=None, cols=3):
    t = FitTableWidget(0, cols)
    t.setHorizontalHeaderLabels([f"열{i}" for i in range(cols)])
    t.set_base_font_px(14, 13)
    if icons:
        t.set_icon_columns(icons)
    return t


class _Stub:
    """MainWindow 가 아니라 _fill·_start_icons 가 읽는 것만 가진 껍데기 — 창 전체를 안 띄운다."""
    _cell = staticmethod(MW._cell)
    _apply_icon = staticmethod(MW._apply_icon)

    def __init__(self, icon_dir=None):
        self._seasons = {1: {"seasonImg": "https://x/s1.png"}, 2: {"seasonImg": "https://x/s2.png"}}
        self._icon_dir = icon_dir or _tmp()
        self._icon_loaders = []
        self.started = []

    def _start_icons(self, table):
        self.started.append(table)

    def fill(self, table, rows, enable_sort=True):
        MW._fill(self, table, rows, enable_sort)


def _rows(keys, texts=None):
    return [[IconCell(texts[i] if texts else f"선수{k}", None, k), (str(i), i), "-"] for i, k in enumerate(keys)]


def test_pad_ignores_icon_columns():
    """그림이 sizeHintForColumn 을 키워도 그 여백이 모든 열에 얹히지 않는다 — **같은 표**의 그림 붙기 전·후 _pad 가 같다.
    (다른 표와 비교하면 재는 열 집합이 달라 글꼴에 따라 1px 갈린다 — 첫 판이 그렇게 비교했다가 CI 에서 (12, 11) 로 FAIL 했다. 같은 표로
    바꿔도 둘째 판이 똑같이 FAIL 했다 — 첫 측정의 1px 차이를 그림 탓으로 읽은 것이라 예열을 넣었다)"""
    from PyQt6.QtGui import QIcon
    st = _Stub()
    with_icon = _table({0: "season"})
    st.fill(with_icon, _rows([1, 2, 1]), False)
    with_icon.refit()                       # 예열 — 첫 측정만 _pad 가 1px 다르다(그림 없는 표도 9 → 10 · CI 에선 11 → 12). 그림과 무관한 기존 현상
    with_icon.refit()
    before = with_icon._pad                 # 그림이 아직 없을 때 — 같은 열 집합
    pm = QPixmap(30, 20)
    pm.fill(QColor("red"))
    for r in range(3):                      # 그림이 먼저 붙은 뒤 refit — 받은 뒤에 다시 재는 길
        with_icon.item(r, 0).setIcon(QIcon(pm))
    with_icon.refit()
    assert with_icon._pad == before, (with_icon._pad, before)
    assert with_icon._extra[0] >= with_icon.icon_px_for("season")[0], "등록 열은 그림 폭 이상의 여백을 따로 갖는다"


def test_gap_measured_from_qt():
    t = _table({0: "season"})
    w, h = t.icon_px_for("season")
    probe = FitTableWidget(1, 1)
    probe.setIconSize(t.iconSize())
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QTableWidgetItem
    probe.setItem(0, 0, QTableWidgetItem("가"))
    bare = probe.sizeHintForColumn(0)
    pm = QPixmap(w, h)
    pm.fill(QColor("blue"))
    probe.item(0, 0).setIcon(QIcon(pm))
    assert t._icon_extra[0] == probe.sizeHintForColumn(0) - bare == measure_icon_gap(t, w, h), "간격은 Qt 에 물은 값"
    assert t._icon_extra[0] > w, "그림 폭보다 간격만큼 더 넓어야 한다(0 이면 재지 못한 것)"


def test_extra_merges_existing():
    """set_content_widths 로 준 열 여백(범위 막대·배지)은 등록과 합쳐 쓴다 — 갈아 끼우지 않는다."""
    st = _Stub()
    t = _table({0: "season"})
    gap = t._icon_extra[0]
    t.set_content_widths({0: 40, 1: 7})
    assert t._extra == {0: 40 + gap, 1: 7}, t._extra
    for _ in range(3):
        st.fill(t, _rows([1, 2]), False)    # _fill 끝 refit() — 여러 번 해도 여백이 자라지 않는다
    assert t._extra == {0: 40 + gap, 1: 7}, t._extra
    assert t._user_extra == {0: 40, 1: 7}


def test_icon_width_stable_across_renders():
    st = _Stub()
    t = _table({0: "season"})
    st.fill(t, _rows([1, 2, 1]), False)   # 예열 — 첫 렌더만 폭이 몇 px 다르다(그림 없는 표도 같다: 표 자체의 초기 배치)
    st.fill(t, _rows([1, 2, 1]), False)
    first = [t.columnWidth(c) for c in range(3)]
    st.fill(t, _rows([2, 1]), False)
    st.fill(t, _rows([1, 2, 1]), False)
    assert [t.columnWidth(c) for c in range(3)] == first


def test_icon_keys_follow_rows_after_resort():
    """정렬 상태를 남긴 채 두 번째 렌더 — 키는 칸과 같이 들어가 있어서 행마다 키 = 그 행 글자의 키."""
    st = _Stub()
    t = _table({0: "season"})
    key_of = {"가": 1, "나": 2, "다": 1, "라": 2}
    st.fill(t, [[IconCell(n, n, key_of[n]), (str(i), i), "-"] for i, n in enumerate("다가라나")])
    t.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    st.fill(t, [[IconCell(n, n, key_of[n]), (str(i), i), "-"] for i, n in enumerate("나라가다")])
    seen = {t.item(r, 0).text(): t.item(r, 0).data(ICON_KEY_ROLE) for r in range(t.rowCount())}
    assert seen == key_of, seen
    assert [t.item(r, 0).text() for r in range(4)] == sorted("가나다라", reverse=True), "정렬이 실제로 걸려 있어야 이 검사가 의미 있다"


def test_icon_cell_leaves_user_role_alone():
    """UserRole 은 선수 칸 spId(더블클릭이 읽는다) — 그림 키는 거기 안 쓴다."""
    st = _Stub()
    t = _table({0: "season"})
    st.fill(t, _rows([1]), False)
    assert ICON_KEY_ROLE not in (Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1)
    assert t.item(0, 0).data(Qt.ItemDataRole.UserRole) is None and t.item(0, 0).data(ICON_KEY_ROLE) == 1
    assert t.item(0, 1).data(ICON_KEY_ROLE) is None, "일반 2-튜플 칸은 그림 키가 없다"


# ── 로더(M3 · M6 · M7) ───────────────────────────────────────────────────────

def _png(path: Path, w=10, h=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = QPixmap(w, h)
    pm.fill(QColor("green"))
    pm.save(str(path), "PNG")


def test_loader_gets_plain_list():
    """로더는 표 칸이 아니라 (종류, 키, 주소) 평범한 목록만 받고, 같은 그림은 한 번만 받는다."""
    d = _tmp()
    calls, built = [], []
    real_fetch, real_start, real_loader = images.fetch_icon, QThread.start, app_main.IconLoader

    def fake_fetch(url, cache_dir, kind="", timeout=6):
        calls.append(url)
        p = Path(cache_dir) / kind / (url.rsplit("/", 1)[1])
        _png(p)
        return p

    class Spy(real_loader):
        def __init__(self, entries, cache_dir):
            built.append(entries)
            super().__init__(entries, cache_dir)

    images.fetch_icon, app_main.IconLoader = fake_fetch, Spy
    QThread.start = lambda self, *a, **k: self.run()
    try:
        st = _Stub(d)
        st._start_icons = lambda table: MW._start_icons(st, table)
        t = _table({0: "season"})
        st.fill(t, _rows([1, 2, 1, 1]), False)
        assert len(built) == 1 and all(isinstance(e, tuple) and len(e) == 3 for e in built[0]), built
        assert sorted(k for _kind, k, _u in built[0]) == [1, 2], "키마다 한 번"
        assert sorted(calls) == ["https://x/s1.png", "https://x/s2.png"], calls
        has = [not t.item(r, 0).icon().isNull() for r in range(4)]
        assert has == [True] * 4, "받은 그림이 같은 키의 모든 칸에 붙는다"
        assert t.iconSize().width() == t.icon_px_for("season")[0]
    finally:
        images.fetch_icon, QThread.start, app_main.IconLoader = real_fetch, real_start, real_loader
        shutil.rmtree(d, ignore_errors=True)


def test_loader_skips_unknown_keys():
    """주소를 못 만드는 키(시즌 meta 에 없음)는 요청하지 않는다 — 그 칸은 글자만."""
    d = _tmp()
    built = []
    real_loader, real_start = app_main.IconLoader, QThread.start

    class Spy(real_loader):
        def __init__(self, entries, cache_dir):
            built.append(entries)
            super().__init__(entries, cache_dir)

    app_main.IconLoader = Spy
    QThread.start = lambda self, *a, **k: None
    try:
        st = _Stub(d)
        st._start_icons = lambda table: MW._start_icons(st, table)
        t = _table({0: "season"})
        st.fill(t, _rows([999, 999]), False)
        assert built == [], built
        plain = _table()
        st.fill(plain, [["a", "b", "c"]], False)
        assert built == [], "등록 안 된 표는 로더를 안 띄운다"
    finally:
        app_main.IconLoader, QThread.start = real_loader, real_start
        shutil.rmtree(d, ignore_errors=True)


def test_icon_set_under_sort():
    """정렬이 켜진 채 아이콘 열로 정렬된 표에 붙여도 정렬이 유지되고, 그림은 제 행에 남는다(재정렬해도 칸에 붙어 따라간다)."""
    st = _Stub()
    t = _table({0: "season"})
    st.fill(t, [[IconCell(n, n, k), (str(i), i), "-"] for i, (n, k) in enumerate([("다", 1), ("가", 2), ("나", 1), ("라", 2)])])
    t.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    d = _tmp()
    try:
        _png(d / "a.png")
        calls = []
        real = t.setSortingEnabled
        t.setSortingEnabled = lambda on: (calls.append(on), real(on))   # 붙이는 동안 꺼졌다 켜지는지 — 결과만 봐서는 안 보인다
        MW._apply_icon(t, "season", 1, str(d / "a.png"))
        assert calls == [False, True], f"정렬 열에 붙이는 동안 정렬을 껐다 켠다(실측 1만 행 1.4초): {calls}"
        assert t.isSortingEnabled(), "붙인 뒤 정렬을 다시 켠다"
        by_text = {t.item(r, 0).text(): not t.item(r, 0).icon().isNull() for r in range(4)}
        assert by_text == {"다": True, "가": False, "나": True, "라": False}, by_text
        assert [t.item(r, 0).text() for r in range(4)] == ["가", "나", "다", "라"]
        bad = d / "bad.png"
        bad.write_bytes(b"not a png")
        MW._apply_icon(t, "season", 2, str(bad))
        assert not bad.exists(), "깨진 캐시는 지운다"
        assert all(t.item(r, 0).icon().isNull() for r in range(4) if t.item(r, 0).text() in "가라")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_retired_loader_awaited_on_quit():
    """다시 그리면 도는 옛 로더를 cancel 하되 목록에는 남긴다 — 종료 표가 물러난 로더도 기다린다."""
    created = []
    real_loader, real_start = app_main.IconLoader, QThread.start

    class _Sig:
        def connect(self, f):
            self.f = f

    class Fake:
        def __init__(self, entries, cache_dir):
            self.entries, self.cancelled, self.loaded, self.finished = entries, False, _Sig(), _Sig()
            created.append(self)

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

        def start(self):
            pass

    app_main.IconLoader = Fake
    try:
        st = _Stub()
        st._start_icons = lambda table: MW._start_icons(st, table)
        t = _table({0: "season"})
        st.fill(t, _rows([1]), False)
        st.fill(t, _rows([2]), False)
        a, b = created
        assert a.cancelled and not b.cancelled, "옛 로더만 멈춘다"
        assert st._icon_loaders == [a, b], "물러난 로더도 종료 때 기다릴 목록에 남는다"
        a.finished.f()
        assert st._icon_loaders == [b], "끝나면 빠진다"
    finally:
        app_main.IconLoader = real_loader


# ── 스쿼드 화면 시즌 배지(옛 SeasonIconLoader 를 IconLoader 로 합친 길) ─────────────

class _FakePitch:
    def __init__(self, sp_ids, dead=False):
        self._sp, self.dead, self.got = sp_ids, dead, []

    def sp_ids(self):
        return self._sp

    def set_face(self, *a):
        pass

    def set_season_icon(self, sp_id, path):
        if self.dead:
            raise RuntimeError("wrapped C/C++ object of type PitchWidget has been deleted")
        self.got.append((sp_id, Path(path).name))


def _pitch_loaders(pitch, fetched):
    d = _tmp()
    real_fetch, real_start, real_img = images.fetch_icon, QThread.start, app_main.ImageLoader

    def fake_fetch(url, cache_dir, kind="", timeout=6):
        fetched.append(url)
        return Path(url.rsplit("/", 1)[1])

    class _NoFace:                       # 얼굴 로더는 이 테스트 밖 — 받지 않는다
        def __init__(self, *a):
            self.loaded = type("S", (), {"connect": lambda s, f: None})()

        def start(self):
            pass

    images.fetch_icon, app_main.ImageLoader = fake_fetch, _NoFace
    QThread.start = lambda self, *a, **k: None
    try:
        st = _Stub(d)
        st._img_cache_dir = d
        out = MW._start_pitch_loaders(st, pitch, card_info=False)
        season = [ld for ld in out if isinstance(ld, app_main.IconLoader)]
        assert len(season) == 1, out
        season[0].run()
    finally:
        images.fetch_icon, QThread.start, app_main.ImageLoader = real_fetch, real_start, real_img
        shutil.rmtree(d, ignore_errors=True)


def test_pitch_season_icons_reach_every_player_of_the_season():
    # spId 앞 3자리 = seasonId(core.season_id_of) — 101 시즌 선수 둘, 202 시즌 선수 하나
    a, b, c = 101000001, 101000002, 202000003
    fetched = []
    pitch = _FakePitch([a, b, c])
    st_seasons = {101: {"seasonImg": "https://x/s101.png"}, 202: {"seasonImg": "https://x/s202.png"}}
    orig_init = _Stub.__init__

    def init(self, icon_dir=None):
        orig_init(self, icon_dir)
        self._seasons = st_seasons
    _Stub.__init__ = init
    try:
        _pitch_loaders(pitch, fetched)
    finally:
        _Stub.__init__ = orig_init
    assert sorted(fetched) == ["https://x/s101.png", "https://x/s202.png"], f"시즌당 한 번: {fetched}"
    assert sorted(pitch.got) == [(a, "s101.png"), (b, "s101.png"), (c, "s202.png")], pitch.got


def test_pitch_season_icon_survives_deleted_pitch():
    """람다 연결은 위젯이 지워져도 안 끊긴다 — 지워진 축구장에 신호가 와도 죽지 않는다(PyQt6 는 슬롯 예외에서 abort)."""
    orig_init = _Stub.__init__

    def init(self, icon_dir=None):
        orig_init(self, icon_dir)
        self._seasons = {101: {"seasonImg": "https://x/s101.png"}}
    _Stub.__init__ = init
    try:
        _pitch_loaders(_FakePitch([101000001], dead=True), [])
    finally:
        _Stub.__init__ = orig_init


# ── 상대 등급(경기 목록 · 상대 전적) ─────────────────────────────────────────

def _fixture_matches():
    man = json.load(open(Path(__file__).parent / "fixtures" / "manifest.json", encoding="utf-8"))
    details = [json.load(open(Path(__file__).parent / "fixtures" / f"{m}.json", encoding="utf-8")) for m in man["match_ids"]]
    return man["ouid"], details


def test_opp_division_parsed_from_opponent_side():
    """상대 등급은 상대 쪽 matchInfo 의 division — 내 쪽 값을 읽으면 둘이 같은 픽스처에서도 안 보이니 값을 바꿔 가른다."""
    ouid, details = _fixture_matches()
    d = json.loads(json.dumps(details[0]))
    for info in d["matchInfo"]:
        info["division"] = 800 if info["ouid"] == ouid else 1100
    m = models.parse_match(d, ouid)
    assert m.opp_division == 1100, m.opp_division
    for info in d["matchInfo"]:
        info.pop("division", None)
    assert models.parse_match(d, ouid).opp_division is None, "값이 없으면 None(모름 → 글자만)"
    for info in d["matchInfo"]:
        info["division"] = "x"
    assert models.parse_match(d, ouid).opp_division is None, "정수가 아니면 None"


def test_opponent_stats_uses_latest_division():
    """한 상대와 여러 번 — 가장 최근(맨 앞) 경기 때 등급. 오래된 경기 값으로 덮이면 안 된다."""
    ouid, details = _fixture_matches()
    ms = []
    for i, div in enumerate((1000, 1100, 1200)):   # 최신순으로 온다
        d = json.loads(json.dumps(details[0]))
        for info in d["matchInfo"]:
            info["division"] = 800 if info["ouid"] == ouid else div
        ms.append(models.parse_match(d, ouid))
    for m in ms:
        m.opponent = "같은상대"
    s = models.opponent_stats(ms)
    assert len(s) == 1 and s[0].games == 3 and s[0].opp_division == 1000, s


# ── 엠블럼 지도(I7 · M4) ─────────────────────────────────────────────────────

def _emblem_stub(meta, known, snap):
    """_emblem_map 이 읽는 것만 가진 껍데기 — fifa.db 는 임시 파일(meta = teamcolor_meta 줄), 스냅숏은 가짜."""
    import store
    import teamcolor
    d = _tmp()
    conn = store.open_db(d / "fifa.db")
    try:
        store.save_teamcolor_meta(conn, [teamcolor.TeamColorMeta(i, n, e, "", None, None) for i, (n, e) in enumerate(meta, 1)],
                                  "2026-10-11")
    finally:
        conn.close()
    st = _Stub()
    st._emblem_label, st._emblem_cache = dict(known), None
    st._rank_read = lambda fn: snap
    st._note_emblem = lambda label, emblem: MW._note_emblem(st, label, emblem)
    st._emblem_map = st.map = lambda: MW._emblem_map(st)
    st.cell = lambda *a, **k: MW._emblem_cell(st, *a, **k)
    return st, d


def test_emblem_map_source_order():
    """① 팀컬러 목록 캐시 → ② team_colors 에서 읽은 것 → ③ 스냅숏. 앞 출처가 이긴다 · 이름이 겹치면 라벨(꼬리표)로."""
    real = config.DB_PATH
    st, d = _emblem_stub(
        meta=[("Alpha", "crests/l1.png"), ("Spartan", "teamcolorboost/4_l5.png")],
        known={"Alpha": "crests/l9.png", "Beta": "crests/l2.png"},
        snap={"Alpha": "crests/l7.png", "Beta": "crests/l8.png", "Gamma": "crests/l3.png"})
    try:
        config.DB_PATH = d / "fifa.db"
        m = st.map()
        assert m["Alpha"] == "crests/l1.png", "① 목록 캐시가 ② ③ 보다 앞"
        assert m["Beta"] == "crests/l2.png", "② 가 ③ 보다 앞"
        assert m["Gamma"] == "crests/l3.png", "③ 스냅숏은 앞 출처에 없는 이름만"
        assert m["Spartan · 강화"] == "teamcolorboost/4_l5.png", "겹치는 이름은 꼬리표가 붙은 라벨이 키"
        assert "Spartan" not in m
    finally:
        config.DB_PATH = real
        shutil.rmtree(d, ignore_errors=True)


def test_emblem_cell_cache_and_invalidation():
    real = config.DB_PATH
    st, d = _emblem_stub(meta=[], known={}, snap={})
    try:
        config.DB_PATH = d / "fifa.db"
        c = st.cell("Alpha")
        assert c == "Alpha", "엠블럼을 모르면 글자만(IconCell 이 아니다)"
        assert st.cell("Alpha", 5) == ("Alpha", 5)
        st._note_emblem("Alpha", "crests/l1.png")
        assert st._emblem_cache is None, "새로 알게 되면 합친 지도를 비운다"
        c = st.cell("Alpha", 5)
        assert c == IconCell("Alpha", 5, "crests/l1.png"), c
        before = st._emblem_cache
        st._note_emblem("Alpha", "crests/l1.png")
        assert st._emblem_cache is before, "같은 값이면 지도를 안 비운다(렌더마다 DB 를 다시 읽지 않게)"
        assert st.cell("Zed", emblem="crests/l77.png").icon_key == "crests/l77.png", "지도에 없어도 행이 키를 알면 그걸 쓴다"
        assert st.cell("Alpha", emblem="crests/l77.png").icon_key == "crests/l77.png", "지도에 있어도 행이 아는 키가 이긴다(구단주 표 스냅숏 줄)"
        st._invalidate = lambda key: None
        st.RANK_VIEW_KEYS = MW.RANK_VIEW_KEYS
        MW._invalidate_rank_views(st)
        assert st._emblem_cache is None, "rank.db 가 바뀌는 길은 합친 지도를 비운다(스냅숏 출처가 낡지 않게)"
        assert st.cell("", ) == "" and st._note_emblem("", "x") is None
    finally:
        config.DB_PATH = real
        shutil.rmtree(d, ignore_errors=True)


def test_owner_emblem_without_snapshot():
    """M4 — 구단주가 스냅숏에 없으면(수집 끈 사용자 전부) 행의 엠블럼 키가 비어 팀컬러 표와 같은 출처(지도)로 찾는다.
    행이 준 키가 주소를 못 만드는 종류(teamcolor/)여도 지도로 넘어간다."""
    real = config.DB_PATH
    st, d = _emblem_stub(meta=[("Alpha", "crests/l1.png")], known={}, snap={})
    try:
        config.DB_PATH = d / "fifa.db"
        assert st.cell("Alpha", emblem="").icon_key == "crests/l1.png", "스냅숏 키 없음 → 지도"
        assert st.cell("Alpha", emblem="teamcolor/x.png").icon_key == "crests/l1.png", "그림을 못 만드는 키 → 지도"
        assert st.cell("Alpha", emblem="crests/l9.png").icon_key == "crests/l9.png", "쓸 수 있는 행 키는 지도보다 앞"
        assert st.cell("Nobody", emblem="teamcolor/x.png") == "Nobody", "둘 다 모르면 글자만"
    finally:
        config.DB_PATH = real
        shutil.rmtree(d, ignore_errors=True)


def test_emblem_map_not_cached_when_first_source_unreadable():
    """fifa.db 를 잠깐 못 읽었을 때(잠김) 그 출처 없이 지도를 확정하면 다음 무효화까지 계속 빈다 — 이번만 쓰고 확정하지 않는다."""
    real = config.DB_PATH
    st, d = _emblem_stub(meta=[("Alpha", "crests/l1.png")], known={"Beta": "crests/l2.png"}, snap={})
    try:
        config.DB_PATH = d / "no_such_dir" / "x" / "fifa.db"      # 열 수 없는 경로
        import store
        real_open = store.open_db
        store.open_db = lambda p: (_ for _ in ()).throw(OSError("잠김"))
        try:
            m = st.map()
        finally:
            store.open_db = real_open
        assert m == {"Beta": "crests/l2.png"}, "읽은 출처만으로는 그린다"
        assert st._emblem_cache is None, "일시 실패는 확정하지 않는다"
        config.DB_PATH = d / "fifa.db"
        assert st.map().get("Alpha") == "crests/l1.png", "다음엔 다시 읽는다"
    finally:
        config.DB_PATH = real
        shutil.rmtree(d, ignore_errors=True)


def test_effect_dialog_refreshes_emblem_map():
    """효과 창이 팀컬러 목록을 새로 저장하면 지도를 다시 만들고, 달라졌을 때만 팀컬러 표를 다시 그린다."""
    import store
    import teamcolor
    real = config.DB_PATH
    st, d = _emblem_stub(meta=[], known={}, snap={})
    st._refresh_emblems = lambda: MW._refresh_emblems(st)
    drawn = []
    st._invalidate = lambda key: drawn.append(key)
    try:
        config.DB_PATH = d / "fifa.db"
        assert st.map() == {}
        st._refresh_emblems()
        assert drawn == [], "지도가 그대로면 표를 다시 안 그린다('더 보기'로 여러 번 불려도)"
        conn = store.open_db(d / "fifa.db")
        try:
            store.save_teamcolor_meta(conn, [teamcolor.TeamColorMeta(1, "Alpha", "crests/l1.png", "", None, None)], "2026-10-11")
        finally:
            conn.close()
        assert st.map() == {}, "저장만으로는 낡은 지도가 남는다 — 이게 리뷰가 잡은 틈"
        st._refresh_emblems()
        assert st.map() == {"Alpha": "crests/l1.png"} and drawn == ["teamcolor"], (st.map(), drawn)
    finally:
        config.DB_PATH = real
        shutil.rmtree(d, ignore_errors=True)


def test_effect_dialog_on_loaded_calls_refresh():
    """효과 창의 _on_loaded 가 목록이 온 결과(meta 있음)에서 지도를 새로 고친다 — 호출이 빠지면 위 갱신 테스트는 계속 통과한다."""
    from types import SimpleNamespace
    calls = []
    fake = SimpleNamespace(_req=7, btn_more=SimpleNamespace(setEnabled=lambda on: None),
                           main=SimpleNamespace(_refresh_emblems=lambda: calls.append("refresh")),
                           _show_pick=lambda view: calls.append("pick"))
    app_main.TeamColorDialog._on_loaded(fake, SimpleNamespace(req=7, meta={"name": "A"}, candidates=[1]))
    assert calls == ["refresh", "pick"], calls
    calls.clear()
    app_main.TeamColorDialog._on_loaded(fake, SimpleNamespace(req=6, meta={"name": "A"}, candidates=[1]))
    assert calls == [], "갈아 끼운 뒤 늦게 온 옛 결과는 건드리지 않는다"


def test_icon_column_extra_is_per_column_width():
    """한 표에 폭이 다른 종류(국기 5:3 · 정사각 배지)가 같이 있어도 열 여백은 그 열 그림 폭 + 간격 — 아이콘 사각형은 가장 넓은 쪽."""
    t = _table({0: "emblem", 1: "division"})
    w_emblem, w_div = t.icon_px_for("emblem")[0], t.icon_px_for("division")[0]
    assert w_emblem > w_div, "이 검사는 두 종류의 폭이 달라야 의미가 있다"
    assert t.iconSize().width() == w_emblem, "아이콘 사각형은 표 하나 — 가장 넓은 종류"
    assert t._icon_extra[0] - t._icon_extra[1] == w_emblem - w_div, t._icon_extra
    assert t._icon_extra[1] > w_div, "정사각 열도 간격은 받는다"


def test_fetch_icon_replace_refused_uses_existing_file():
    """Windows 에서 다른 로더가 같은 그림을 읽는 중이면 os.replace 가 거절된다 — 이미 그 파일이 있으면 그걸 쓰고 임시 파일을 남기지 않는다."""
    d, sess = _tmp(), _Sess(_Resp(200, b"NEW"))
    old = _with_session(sess)
    real = os.replace

    def refuse(src, dst):
        Path(dst).write_bytes(b"OTHER")           # 다른 로더가 먼저 놓은 파일
        raise PermissionError("읽는 중")

    os.replace = refuse
    try:
        p = images.fetch_icon("https://x/r.png", d, "k")
        assert p is not None and p.read_bytes() == b"OTHER", p
        assert not list(d.rglob("*.tmp")), "임시 파일이 남았다"
        os.replace = lambda s, t: (_ for _ in ()).throw(PermissionError("끝까지 거절"))
        sess.resps.append(_Resp(200, b"NEW"))
        assert images.fetch_icon("https://x/never.png", d, "k") is None, "파일이 없으면 실패(글자만)"
    finally:
        os.replace = real
        images._session = old
        shutil.rmtree(d, ignore_errors=True)


# ── 40단계 조각 ② — I4 특성 칸 · I5 작은 축구장 ────────────────────────────────────

def test_formation_forms_agree():
    """내 경기(0 줄 포함 고정 5줄)와 랭커(줄 수가 바뀜)가 같은 모양이면 같은 키 · 모양이 아닌 글자는 그림 없음."""
    from widgets import formation_key, formation_pixmap
    assert formation_key("4-1-2-3-0") == formation_key("4-1-2-3") == "4-1-2-3"
    assert formation_key("4-2-2-1-1") == "4-2-2-1-1"
    for bad in ("", "-", "모름", "기타·모름", "4백", "앞줄 3명", "4", "4-2-x", None):
        assert formation_key(bad) == "", bad
        assert formation_pixmap(bad).isNull(), bad
    a, b = formation_pixmap("4-1-2-3-0", dpr=1.0), formation_pixmap("4-1-2-3", dpr=1.0)
    assert a.toImage() == b.toImage(), "같은 모양인데 그림이 다르다 — 캐시 키가 갈린다"


def test_formation_pixmap_draws_dots():
    """줄마다 점 — 포메이션이 다르면 그림이 달라야 하고, 점 색이 실제로 찍혀야 한다(점을 빼면 모든 포메이션이 같은 빈 판)."""
    from widgets import formation_pixmap
    px = config.FORMATION_PX
    imgs = {f: formation_pixmap(f, dpr=1.0).toImage() for f in ("4-4-2", "4-3-3", "3-5-2", "5-4-1")}
    for i in imgs.values():
        assert i.width() == round(px * config.ICON_ASPECT["formation"]) and i.height() == px, (i.width(), i.height())
    uniq = {bytes(i.constBits().asarray(i.sizeInBytes())) for i in imgs.values()}
    assert len(uniq) == 4, "포메이션이 달라도 그림이 같다"
    white = QColor(app_main.T.FORMATION_DOT).rgb()
    for f, i in imgs.items():
        n = sum(1 for y in range(i.height()) for x in range(i.width()) if i.pixelColor(x, y).rgb() == white)
        assert n >= sum(int(x) for x in f.split("-")), (f, n)   # 점마다 흰 픽셀이 최소 한 개씩은 있다
    pitch = QColor(app_main.T.FORMATION_PITCH).rgb()
    assert imgs["4-4-2"].pixelColor(1, px // 2).rgb() == pitch, "판이 pitch 색으로 안 칠해졌다"


def test_formation_cells_need_no_loader():
    """포메이션 칸은 받지 않고 그 자리에서 그린다 — 로더가 안 뜨고 아이콘이 붙는다 · 그릴 수 없는 글자는 글자만."""
    cell = MW._formation_cell
    assert isinstance(cell("4-1-2-3-0"), IconCell) and cell("4-1-2-3-0").icon_key == "4-1-2-3"
    assert cell("모름") == "모름" and cell("4백") == "4백" and cell("-") == "-"
    assert cell("4-4-2", 7) == IconCell("4-4-2", 7, "4-4-2") and cell("모름", 3) == ("모름", 3)

    class St(_Stub):
        _put_icon = staticmethod(MW._put_icon)

        def _start_icons(self, table):
            MW._start_icons(self, table)

    st = St()
    t = _table({0: "formation"})
    st.fill(t, [[cell("4-4-2"), "-", "-"], [cell("모름"), "-", "-"], [cell("4-1-2-3-0"), "-", "-"]], False)
    assert st._icon_loaders == [], "포메이션은 네트워크 없이 그리는데 로더가 떴다"
    assert not t.item(0, 0).icon().isNull() and not t.item(2, 0).icon().isNull()
    assert t.item(1, 0).icon().isNull(), "그릴 수 없는 글자('모름')에 그림이 붙었다"


def test_trait_code_of_roundtrip():
    """이름 → 코드(그림 주소용) — 모든 표에서 되돌아오고, 모르는 이름("코드 N")은 None."""
    import trait_codes as tc
    assert tc.code_of("normal", "승부욕") == 28 and tc.code_of("new", "2개의 심장") == 55
    assert tc.code_of("train", "근거리 슛 향상") == 0 and tc.code_of("coach", "근거리 슛 향상") == 0, "코치는 훈련 표를 같이 쓴다"
    assert tc.code_of("normal", "코드 99") is None and tc.code_of("nope", "승부욕") is None
    assert tc.code_of("new", "승부욕") is None, "일반 이름을 신규 표에서 찾으면 안 된다"
    for kind in tc.KINDS:
        table = tc._TABLES[kind]
        assert len(set(table.values())) == len(table), f"{kind} 표 안에 이름 중복 — 역표가 모호하다"
        for code, name in table.items():
            assert tc.code_of(kind, name) == code, (kind, code, name)


def test_trait_cell_is_percent_only():
    """칸 글자는 'NN%' 만(이름이 들어가면 _measure_text 가 긴 이름을 재 열이 넓어진다) · 이름은 칸 이름 역할·툴팁."""
    from widgets import NAME_ROLE
    c = MW._trait_cell("normal", "승부욕", 3, 0.456)
    assert c == IconCell("46%", 0.456, 28, "승부욕"), c
    assert MW._trait_cell("train", "코드 77", 1, 0.5).icon_key is None, "모르는 코드는 그림 없음(이름으로 대신)"
    st = _Stub()
    t = _table({0: "trait"})
    st.fill(t, [[c, "-", "-"]], False)
    it = t.item(0, 0)
    assert it.text() == "46%" and it.data(NAME_ROLE) == "승부욕" and it.toolTip() == "승부욕" and it.data(ICON_KEY_ROLE) == 28


def _trait_table():
    t = _table({0: "trait", 1: "train"}, cols=3)
    st = _Stub()
    st.fill(t, [[MW._trait_cell("normal", "승부욕", 3, 0.5), MW._trait_cell("train", "근거리 슛 향상", 1, 0.25), "-"],
                ["", "", "-"]], False)
    return t


def test_trait_columns_stacked_not_beside():
    """특성 칸은 글자 옆이 아니라 위에 그린다 — 열 여백 0(그래도 _extra 에는 있어 _pad 를 안 부풀린다) · 행 높이는 그림 + 글자 한 줄."""
    from widgets import TraitIconDelegate
    t = _trait_table()
    assert t._icon_extra[0] == 0 and t._icon_extra[1] == 0 and 0 in t._extra and 1 in t._extra
    assert isinstance(t.itemDelegateForColumn(0), TraitIconDelegate) and t.itemDelegateForColumn(0).kind == "trait"
    assert t.itemDelegateForColumn(1).kind == "train"
    assert not isinstance(t.itemDelegateForColumn(2), TraitIconDelegate)
    need = config.TRAIT_ICON_PX + t.fontMetrics().height()
    assert t.verticalHeader().defaultSectionSize() >= need, (t.verticalHeader().defaultSectionSize(), need)
    assert t.iconSize().height() == config.TRAIT_ICON_PX
    # 같은 글자의 일반 표 — 열 폭이 그림 때문에 부풀지 않는다
    plain = _table(None, cols=3)
    _Stub().fill(plain, [["50%", "25%", "-"], ["", "", "-"]], False)
    plain.refit()
    plain.refit()
    t.refit()
    t.refit()
    assert t._pad == plain._pad, (t._pad, plain._pad)


def _paint_cell(t, row, col, icon=None):
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QStyleOptionViewItem
    if icon is not None:
        t.item(row, col).setIcon(icon)
    idx = t.model().index(row, col)
    t.resize(400, 200)
    t.show()
    t.refit()
    opt = QStyleOptionViewItem()
    opt.initFrom(t)
    opt.rect = t.visualRect(idx)
    opt.font = t.font()
    img = QImage(t.viewport().size(), QImage.Format.Format_ARGB32)
    img.fill(QColor("black"))
    p = QPainter(img)
    t.itemDelegateForColumn(col).paint(p, opt, idx)
    p.end()
    return img, opt


def test_trait_cell_geometry_no_overlap():
    """판(육각 상자)과 % 글자 상자가 안 겹치고 둘 다 칸 안에 있다."""
    t = _trait_table()
    _img, opt = _paint_cell(t, 0, 0)
    d = t.itemDelegateForColumn(0)
    pad, txt = d.pad_rect(opt), d.text_rect(opt)
    cell = opt.rect
    assert pad.bottom() <= txt.top(), (pad, txt)
    assert pad.top() >= cell.top() and txt.bottom() <= cell.bottom() + 1, (pad, txt, cell)
    assert pad.left() >= cell.left() and pad.right() <= cell.right() + 1, (pad, cell)


def test_trait_cell_paints_pad_and_icon():
    """그림이 있으면 판 위에 그림 · 없으면 판 없이 이름만 — 판 색은 종류(일반 금 · 훈련 회)를 따른다.
    그림이 없을 때 판을 그리면 이름이 판 밖으로 번져 겹쳐 보였다(2026-10-11 실화면 · 일반 특성 8개는 넥슨에 그림이 없다)."""
    import theme as T
    from PyQt6.QtGui import QIcon
    t = _trait_table()
    pm = QPixmap(config.TRAIT_ICON_PX, config.TRAIT_ICON_PX)
    pm.fill(QColor("#ff0000"))
    bare, opt = _paint_cell(t, 0, 0)
    drawn, _ = _paint_cell(t, 0, 0, QIcon(pm))
    d = t.itemDelegateForColumn(0)
    box = d.pad_rect(opt)
    c = box.center().toPoint()
    assert drawn.pixelColor(c).rgb() == QColor("#ff0000").rgb(), "아이콘이 판 가운데에 안 찍혔다"
    assert bare.pixelColor(c).rgb() != QColor("#ff0000").rgb()
    assert bare != drawn, "그림이 있든 없든 똑같다 — 아이콘을 안 그린다"
    # 육각 판 위쪽 꼭짓점 바로 아래(그림 상자 가장자리 밖) — 판 색
    top_inside = drawn.pixelColor(int(box.center().x()), int(box.top()) + 1)
    assert top_inside.rgb() == QColor(T.TRAIT_PAD_GOLD).rgb(), "일반 특성 판이 금색이 아니다"
    # 그림이 없으면 판이 없다 — 같은 자리가 배경(검정)이어야 한다(이름 글자는 그 아래 가운데에 있어 꼭짓점은 비어 있다)
    assert bare.pixelColor(int(box.center().x()), int(box.top()) + 1).rgb() == QColor("black").rgb(), \
        "그림이 없는데 판을 그렸다 — 이름이 판 위에 겹친다"
    img_t, opt_t = _paint_cell(t, 0, 1, QIcon(pm))
    d1 = t.itemDelegateForColumn(1)
    box1 = d1.pad_rect(opt_t)
    edge_t = img_t.pixelColor(int(box1.center().x()), int(box1.top()) + 1)
    assert edge_t.rgb() == QColor(T.TRAIT_PAD_GRAY).rgb(), "훈련 특성 판이 회색이 아니다"
    assert d.failed == 0 and d1.failed == 0, "그리다 예외가 났다"


def test_trait_blank_cell_draws_nothing():
    """줄 수가 모자라 비어 있는 칸은 판도 안 그린다."""
    t = _trait_table()
    img, opt = _paint_cell(t, 1, 0)
    black = QColor("black").rgb()
    r = opt.rect
    assert all(img.pixelColor(x, y).rgb() == black for x in range(r.left(), r.right() + 1)
               for y in range(r.top(), r.bottom() + 1)), "빈 칸에 뭔가 그려졌다"


def test_trait_delegate_leaves_default_drawing_to_background():
    """기본 그리기는 배경·선택만 — 글자와 그림을 비워야 우리가 그린 글자와 겹쳐 두 번 찍히지 않는다."""
    from PyQt6.QtWidgets import QStyleOptionViewItem
    t = _trait_table()
    t.item(0, 0).setIcon(app_main.QIcon(QPixmap(8, 8)))
    d = t.itemDelegateForColumn(0)
    opt = QStyleOptionViewItem()
    d.initStyleOption(opt, t.model().index(0, 0))
    assert opt.text == "" and opt.icon.isNull(), (opt.text, opt.icon.isNull())


def test_trait_row_height_follows_font():
    """창이 좁아 FitTableWidget 이 글꼴을 줄이면 행 높이도 그 글꼴의 글자 줄에 맞춰 다시 잡는다(처음 글꼴로 굳으면 글자가 잘린다)."""
    from widgets import TraitIconDelegate
    t = _trait_table()
    t.setColumnWidth(0, 40)
    t.resize(70, 200)        # 열 셋이 70px 에 안 들어간다 → 글꼴이 줄어든다
    t.show()
    t.refit()
    assert t.font().pixelSize() < 14, t.font().pixelSize()
    want = TraitIconDelegate.row_height(t.fontMetrics())
    assert t.verticalHeader().defaultSectionSize() == want, (t.verticalHeader().defaultSectionSize(), want)


FIXTURES = Path(__file__).parent / "fixtures" / "icons"


def test_real_ratio_icons_fit_cells():
    """진짜 비율(가로로 긴 국기 · 정사각 엠블럼·등급 · 투명 여백 큰 특성)의 지어낸 그림을 붙인 뒤에도 칸이 안 잘리고,
    아이콘 높이는 행 높이 안에 든다(그림이 붙은 **뒤** 잰다 — 붙기 전만 재면 그림 폭을 놓친다)."""
    from PyQt6.QtGui import QIcon
    for fname, kind in (("flag_130x78.png", "emblem"), ("emblem_128.png", "emblem"), ("division_64.png", "division")):
        pm = QPixmap(str(FIXTURES / fname))
        assert not pm.isNull(), fname
        st = _Stub()
        t = _table({0: kind})
        st.fill(t, [[IconCell("구단 이름 하나", None, "k"), "-", "-"], [IconCell("가", None, "k"), "-", "-"]], False)
        t.resize(500, 200)
        t.show()
        t.refit()
        before = t.columnWidth(0)
        for r in range(2):
            t.item(r, 0).setIcon(QIcon(pm))
        t.refit()
        assert t.columnWidth(0) >= t.sizeHintForColumn(0), (fname, t.columnWidth(0), t.sizeHintForColumn(0))
        assert t.columnWidth(0) == before or abs(t.columnWidth(0) - before) <= 1, \
            f"{fname}: 그림이 붙으며 열 폭이 흔들렸다 {before} → {t.columnWidth(0)}"
        assert t.iconSize().height() <= t.rowHeight(0), (fname, t.iconSize().height(), t.rowHeight(0))


def test_trait_fixture_paints_inside_pad():
    """투명 여백이 큰 특성 그림(90×90)을 판 안에 줄여 그려도 판 상자 밖으로 안 번진다."""
    from PyQt6.QtGui import QIcon
    t = _trait_table()
    pm = QPixmap(str(FIXTURES / "trait_90_pad.png"))
    assert not pm.isNull() and pm.width() == 90
    bare, opt = _paint_cell(t, 0, 0)
    drawn, _ = _paint_cell(t, 0, 0, QIcon(pm))
    d = t.itemDelegateForColumn(0)
    box = d.pad_rect(opt)
    assert bare != drawn
    # 판 상자 바깥(칸 위쪽 가장자리 · 판 왼쪽)은 그림을 넣어도 달라지지 않는다
    for x, y in ((int(box.left()) - 3, int(box.center().y())), (int(box.right()) + 3, int(box.center().y())),
                 (int(box.center().x()), 0)):
        assert bare.pixelColor(x, y) == drawn.pixelColor(x, y), (x, y)


def test_hbarlist_icons_make_room_and_draw():
    """상대 포메이션 막대(전술) — 이름 앞 작은 축구장: 그림이 있으면 최소 폭이 그만큼 늘고, 그려지며, 없으면 예전과 같다."""
    import charts
    from PyQt6.QtGui import QImage
    from widgets import formation_pixmap
    rows = [("4-4-2", 55.0, "55%", "t"), ("모름", 40.0, "40%", "t")]
    bars = charts.HBarList()
    bars.set_data(rows)
    plain_w = bars.minimumSizeHint().width()
    pms = [formation_pixmap("4-4-2", dpr=1.0), formation_pixmap("모름", dpr=1.0)]   # 둘째는 null — 그림 없는 줄
    bars.set_data(rows, pms)
    assert bars.minimumSizeHint().width() == plain_w + pms[0].width() + 6, (bars.minimumSizeHint().width(), plain_w)
    bars.resize(400, bars.minimumSizeHint().height())
    img = QImage(bars.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor("black"))
    bars.render(img)
    pitch = QColor(app_main.T.FORMATION_PITCH).rgb()
    mid = int(bars.ROW / 2)
    assert any(img.pixelColor(x, mid).rgb() == pitch for x in range(pms[0].width())), "첫 줄에 작은 축구장이 안 그려졌다"
    bars.set_data(rows)
    assert bars.minimumSizeHint().width() == plain_w, "그림을 빼면 예전 폭으로 돌아와야 한다"


def test_ranker_card_head_icons():
    """대시보드 머리(구단주 카드) — 포메이션·엠블럼이 있으면 줄이 보이고 서로·등급 배지와 안 겹치며, 둘 다 없으면 줄이 숨는다."""
    from widgets import RankerCard, formation_pixmap
    fx = Path(__file__).parent / "fixtures" / "icons"
    card = RankerCard()
    card.set_name("닉네임 Lv.1")
    card.resize(560, 700)
    card.show()
    _app.processEvents()
    assert card._head_icons.isHidden(), "아무것도 없으면 줄이 숨어야 한다"
    card.set_formation(formation_pixmap("4-2-3-1", config.FORMATION_HEAD_PX, 1.0), "tip")
    assert not card._head_icons.isHidden() and not card._head_pitch.isHidden() and card._head_emblem.isHidden()
    card.set_emblem(str(fx / "emblem_128.png"), "엠블럼")
    _app.processEvents()
    em, pt = card._head_emblem, card._head_pitch
    assert not em.isHidden() and em.pixmap().height() == config.EMBLEM_HEAD_PX, em.pixmap().height()
    assert em.toolTip() == "엠블럼" and pt.toolTip() == "tip"
    r_em, r_pt = em.geometry(), pt.geometry()
    assert not r_em.intersects(r_pt), (r_em, r_pt)
    head_rect = card._head.rect()
    for r in (r_em, r_pt):
        assert head_rect.contains(r), (head_rect, r)
    assert card._head_icons.geometry().top() >= card._head_badge.geometry().bottom(), "아이콘 줄이 등급 배지를 가린다"
    # 깨진 파일·None 은 숨김(빈 네모 없음) · 포메이션을 빼면 엠블럼만 남는다
    card.set_emblem(str(fx / "no_such.png"), "x")
    assert card._head_emblem.isHidden() and card._head_emblem.toolTip() == ""
    card.set_formation(None)
    assert card._head_icons.isHidden(), "둘 다 없으면 줄이 숨어야 한다"
    card.close()


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
