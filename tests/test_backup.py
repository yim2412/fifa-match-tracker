"""내 기록 백업·복원(109 G1 · J9 · storage.backup / inspect_backup / restore / sweep_tmp) — 임시 폴더·지어낸 기록, 네트워크 없음.

pytest 없이 `python tests/test_backup.py`. 화면 배선(버튼 → 파일 고르기 → 걸쇠 → 결과 글)은 test_ui_smoke 의 test_storage_backup_*.
이 파일은 로직을 잰다: 왕복 · zip 검사 거부 · 임시 파일 · 취소 · 복원 전 백업 실패 · 빈 공간 · 설정 키 거르기.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import watchdog  # noqa: E402 — config 보다 먼저(FIFA_NO_CRED)
import config
import storage
import store
import test_storage as ts   # _fill · _detail 을 같이 쓴다

SECRET = "SUPER-SECRET-API-KEY-9f2c"   # 백업 zip 에 들어가면 안 되는 글자(.env · rank.db 쪽에만 심는다)


def _ini(path: Path, values: dict) -> None:
    from PySide6.QtCore import QSettings
    s = QSettings(str(path), QSettings.Format.IniFormat)
    for k, v in values.items():
        s.setValue(k, v)
    s.sync()


def _ini_value(path: Path, key: str):
    from PySide6.QtCore import QSettings
    return QSettings(str(path), QSettings.Format.IniFormat).value(key)


def _env(d: Path):
    """(데이터 폴더, db, ini, 백업을 둘 폴더) — 기록 2경기 + 설정 + 키·rank.db 를 심은 폴더."""
    data = d / "data"
    data.mkdir(parents=True)
    out = d / "out"
    out.mkdir(parents=True)
    db = data / "fifa.db"
    ts._fill(db)
    ini = data / "settings.ini"
    _ini(ini, {"view/page": "players", "view/exclude_shown": "true", "window/geometry": "cur-geo",
               "app/seen_version": "3.10.1"})
    (data / ".env").write_text(f"NEXON_API_KEY={SECRET}\n", encoding="utf-8")
    rk = sqlite3.connect(str(data / "rank.db"))
    rk.execute("CREATE TABLE snapshots (k TEXT)")
    rk.execute("INSERT INTO snapshots VALUES (?)", (SECRET,))
    rk.commit()
    rk.close()
    return data, db, ini, out


def _matches(db: Path) -> int:
    conn = store.open_db(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    finally:
        conn.close()


def _empty_matches(db: Path) -> None:
    conn = store.open_db(db)
    try:
        conn.execute("DELETE FROM matches")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _strays(data: Path) -> list[str]:
    """데이터 폴더에 남은 임시 파일·보조 파일 — restore-/backup- 무늬와, 임시 사본의 -wal·-shm."""
    return sorted(p.name for p in data.iterdir()
                  if p.name.startswith(tuple(config.BACKUP_TMP_PREFIXES))
                  or (p.name.endswith(("-wal", "-shm")) and not p.name.startswith(("fifa.db", "rank.db"))))


def _zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _good_zip(d: Path, name="ok.zip") -> Path:
    data, db, ini, out = _env(d / "g")
    r = storage.backup(db, ini, out / name)
    assert r.state == "ok", r
    return out / name


# ── 왕복 ──────────────────────────────────────────────────────────────

def test_roundtrip_restores_records_and_chosen_settings_only():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        z = out / "b.zip"
        r = storage.backup(db, ini, z)
        assert r.state == "ok" and r.zip_bytes == z.stat().st_size > 0, r
        with zipfile.ZipFile(z) as zf:
            assert sorted(zf.namelist()) == ["fifa.db", "settings.ini"], zf.namelist()
            blob = zf.read("fifa.db") + zf.read("settings.ini")
        assert SECRET.encode() not in blob, "API 키가 백업에 들어갔다"
        assert not (out / "b.zip.part").exists() and _strays(data) == []
        # 그 뒤 지금 기록이 달라진다 — 경기를 비우고 설정을 바꾼다
        _empty_matches(db)
        _ini(ini, {"view/page": "dash", "view/exclude_shown": "false", "window/geometry": "new-geo", "app/seen_version": "3.11.0"})
        info = storage.inspect_backup(z, db)
        assert info.state == "ok", (info.state, info.reason)
        assert info.backup.matches == 2 and info.current.matches == 0 and info.backup.accounts >= 1, (info.backup, info.current)
        assert info.backup.last_match.startswith("2026-10-20"), info.backup
        res = storage.restore(db, ini, info)
        assert res.state == "ok" and res.detail == "", res
        assert _matches(db) == 2, "기록이 안 돌아왔다"
        assert str(_ini_value(ini, "view/page")) == "players" and str(_ini_value(ini, "view/exclude_shown")) == "true"
        assert str(_ini_value(ini, "window/geometry")) == "new-geo", "창 위치를 백업 값으로 되돌렸다(M5)"
        assert str(_ini_value(ini, "app/seen_version")) == "3.11.0", "'바뀐 점 본 버전' 을 되돌렸다(M5)"
        assert _strays(data) == [] and (data / ".env").read_text(encoding="utf-8").count(SECRET) == 1
        rk = sqlite3.connect(str(data / "rank.db"))
        try:
            assert rk.execute("SELECT k FROM snapshots").fetchone()[0] == SECRET, "rank.db 를 건드렸다"
        finally:
            rk.close()


def test_restore_skip_list_is_wired_not_just_default():
    """건너뛸 키 목록이 실제로 쓰인다 — 목록에 다른 키를 더하면 그 키가 안 돌아와야 한다(배선 · 기본값과 다른 값으로 잰다)."""
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        z = out / "b.zip"
        assert storage.backup(db, ini, z).state == "ok"
        _ini(ini, {"view/page": "dash"})
        keep = storage.SETTINGS_RESTORE_SKIP
        try:
            storage.SETTINGS_RESTORE_SKIP = keep + ("view/page",)
            info = storage.inspect_backup(z, db)
            assert storage.restore(db, ini, info).state == "ok"
            assert str(_ini_value(ini, "view/page")) == "dash", "건너뛸 키 목록을 안 읽는다"
        finally:
            storage.SETTINGS_RESTORE_SKIP = keep
        info = storage.inspect_backup(z, db)
        assert storage.restore(db, ini, info).state == "ok" and str(_ini_value(ini, "view/page")) == "players", \
            "목록 밖 키는 돌아와야 한다 — 위 단언이 비었다"


def test_missing_ini_still_backs_up_two_entries():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        ini.unlink()
        r = storage.backup(db, ini, out / "b.zip")
        assert r.state == "ok", r
        with zipfile.ZipFile(out / "b.zip") as zf:
            assert sorted(zf.namelist()) == ["fifa.db", "settings.ini"]


# ── zip 검사 ────────────────────────────────────────────────────────────

def _plain_db(path: Path, ddl: str, version: int | None = None) -> bytes:
    c = sqlite3.connect(str(path))
    c.executescript(ddl)
    if version is not None:
        c.execute(f"PRAGMA user_version={version}")
    c.commit()
    c.close()
    return path.read_bytes()


def _assert_rejected(tmp: Path, z: Path, want: str, why: str):
    data = tmp / f"cur_{abs(hash(why))}"   # 사례마다 새 폴더(_fill 이 같은 DB 에 두 번 못 채운다)
    data.mkdir()
    db = data / "fifa.db"
    ts._fill(db)
    before = db.read_bytes()
    info = storage.inspect_backup(z, db)
    assert info.state == want, (why, info.state, info.reason)
    assert info.reason, (why, "까닭 글이 비었다")
    assert _strays(data) == [], (why, "거부했는데 임시 파일이 남았다", _strays(data))
    assert db.read_bytes() == before, (why, "거부했는데 지금 파일이 바뀌었다")


def test_inspect_rejects_wrong_names_and_paths():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        good = _good_zip(t)
        with zipfile.ZipFile(good) as zf:
            dbb, inib = zf.read("fifa.db"), zf.read("settings.ini")
        _assert_rejected(t, _zip(t / "a.zip", {"fifa.db": dbb, "settings.ini": inib, "extra.txt": b"x"}), "bad", "셋 이상")
        _assert_rejected(t, _zip(t / "b.zip", {"../fifa.db": dbb, "settings.ini": inib}), "bad", "경로 탈출 ../")
        _assert_rejected(t, _zip(t / "b2.zip", {"sub/fifa.db": dbb, "settings.ini": inib}), "bad", "하위 폴더")
        _assert_rejected(t, _zip(t / "c.zip", {"fifa.db": dbb}), "bad", "settings.ini 빠짐")
        _assert_rejected(t, _zip(t / "c2.zip", {"settings.ini": inib}), "bad", "fifa.db 빠짐")
        (t / "d.zip").write_bytes(b"this is not a zip" * 20)
        _assert_rejected(t, t / "d.zip", "bad", "zip 이 아님")
        # 위 거부 사례가 전부 거부 때문이지 검사가 비어서가 아니다 — 같은 재료로 정상 zip 은 받는다
        data = t / "cur2"
        data.mkdir()
        db = data / "fifa.db"
        ts._fill(db)
        okinfo = storage.inspect_backup(good, db)
        assert okinfo.state == "ok", okinfo
        storage.discard(okinfo)


def test_inspect_rejects_bad_databases():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        good = _good_zip(t)
        with zipfile.ZipFile(good) as zf:
            inib = zf.read("settings.ini")
            dbb = zf.read("fifa.db")
        _assert_rejected(t, _zip(t / "a.zip", {"fifa.db": b"\x00not a database\xff" * 500, "settings.ini": inib}), "bad", "DB 가 아님")
        no_tables = _plain_db(t / "nt.db", "CREATE TABLE x (a); INSERT INTO x VALUES (1);")
        _assert_rejected(t, _zip(t / "b.zip", {"fifa.db": no_tables, "settings.ini": inib}), "bad", "필수 표 없음")
        only_one = _plain_db(t / "one.db", "CREATE TABLE matches (match_id TEXT, match_date TEXT); CREATE TABLE accounts (a);")
        _assert_rejected(t, _zip(t / "b2.zip", {"fifa.db": only_one, "settings.ini": inib}), "bad", "match_players 없음")
        # quick_check 만 잡는 손상 — 표 읽기·집계는 멀쩡하고 보조 색인 쪽만 깨진다
        broken = bytearray(dbb)
        conn = sqlite3.connect(str(t / "cur_peek.db"))
        conn.close()
        probe = t / "probe.db"
        probe.write_bytes(dbb)
        pc = sqlite3.connect(str(probe))
        root = pc.execute("SELECT rootpage FROM sqlite_master WHERE name='idx_players_ouid'").fetchone()[0]
        psize = pc.execute("PRAGMA page_size").fetchone()[0]
        pc.close()
        off = (root - 1) * psize
        broken[off + 8:off + psize] = b"\xff" * (psize - 8)
        _assert_rejected(t, _zip(t / "c.zip", {"fifa.db": bytes(broken), "settings.ini": inib}), "bad", "quick_check 실패")


def test_inspect_schema_version_direction():
    """높은 판은 거부 · 옛 백업(판 번호 0)과 같은 판은 받는다."""
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        good = _good_zip(t)
        with zipfile.ZipFile(good) as zf:
            inib, dbb = zf.read("settings.ini"), zf.read("fifa.db")

        def with_version(v: int) -> bytes:
            p = t / f"v{v}.db"
            p.write_bytes(dbb)
            c = sqlite3.connect(str(p))
            c.execute(f"PRAGMA user_version={v}")
            c.commit()
            c.close()
            return p.read_bytes()

        _assert_rejected(t, _zip(t / "new.zip", {"fifa.db": with_version(store.SCHEMA_VERSION + 1), "settings.ini": inib}),
                         "newer", "이 앱보다 새 판")
        data = t / "cur_ok"
        data.mkdir()
        db = data / "fifa.db"
        ts._fill(db)
        for v in (0, store.SCHEMA_VERSION):
            info = storage.inspect_backup(_zip(t / f"ok{v}.zip", {"fifa.db": with_version(v), "settings.ini": inib}), db)
            assert info.state == "ok" and info.version == v, (v, info.state, info.reason)
            storage.discard(info)
        # 새 파일을 평소대로 열면 판 번호가 오른다(옛 백업 = 0 → 지금 판)
        old = t / "old.db"
        old.write_bytes(with_version(0))
        c = store.open_db(old)
        assert c.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
        c.close()


def test_inspect_leaves_no_wal_shm_and_discard_cleans():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        good = _good_zip(t)
        data = t / "cur"
        data.mkdir()
        db = data / "fifa.db"
        ts._fill(db)
        info = storage.inspect_backup(good, db)
        assert info.state == "ok"
        names = sorted(p.name for p in data.iterdir() if p.name.startswith("restore-"))
        assert len(names) == 2 and names[0].endswith(".tmp") and names[1].endswith(".tmp.ini"), \
            ("검사 때 -wal·-shm 이 생겼다(immutable 이 아니다)", names)
        storage.discard(info)
        storage.discard(info)   # 두 번 불러도 된다
        assert _strays(data) == []


def test_inspect_wal_header_backup_leaves_no_wal_shm():
    """바깥에서 만든 zip 의 DB 가 WAL 모드 머리말이어도 검사가 -wal·-shm 을 만들지 않는다(immutable · M46) — 우리 백업은 DELETE 모드라
    이 경우를 따로 만들어 잰다."""
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        p = t / "wal.db"
        c = sqlite3.connect(str(p))
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("CREATE TABLE matches (match_id TEXT, match_date TEXT); CREATE TABLE match_players (a);"
                        " CREATE TABLE accounts (a); INSERT INTO matches VALUES ('m', '2026-10-20T10:00:00');")
        c.commit()
        c.close()
        assert p.read_bytes()[18] == 2, "WAL 모드 머리말이 아니다 - 아래 단언이 비었다"
        data = t / "cur"
        data.mkdir()
        db = data / "fifa.db"
        ts._fill(db)
        info = storage.inspect_backup(_zip(t / "w.zip", {"fifa.db": p.read_bytes(), "settings.ini": b""}), db)
        assert info.state == "ok" and info.backup.matches == 1, (info.state, info.reason)
        stray = [x.name for x in data.iterdir() if x.name.endswith(("-wal", "-shm")) and x.name.startswith("restore-")]
        assert stray == [], ("검사가 보조 파일을 만들었다", stray)
        storage.discard(info)
        assert _strays(data) == []


def test_inspect_does_not_touch_current_db_bytes():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        good = _good_zip(t)
        data = t / "cur"
        data.mkdir()
        db = data / "fifa.db"
        ts._fill(db)
        before = db.read_bytes()
        info = storage.inspect_backup(good, db)
        assert info.state == "ok"
        storage.discard(info)
        # 집계가 지금 기록을 평소대로 연다 — 본 파일은 같지만(WAL 이 아직 있을 수 있다) 경기 수는 그대로
        assert _matches(db) == 2 and db.read_bytes()[:16] == before[:16]


# ── 임시 파일 · 취소 · 빈 공간 ─────────────────────────────────────────────

def test_sweep_tmp_removes_leftovers_only():
    with tempfile.TemporaryDirectory() as t:
        data = Path(t)
        mine = ["restore-abc.tmp", "restore-abc.tmp.ini", "restore-abc.tmp-wal", "backup-xyz.tmp", "backup-xyz.tmp-journal"]
        keep = ["fifa.db", "settings.ini", "other.tmp", "restore.txt", ".env"]
        for n in mine + keep:
            (data / n).write_bytes(b"x")
        n = storage.sweep_tmp(data)
        assert n == len(mine), n
        assert sorted(p.name for p in data.iterdir()) == sorted(keep)


def test_backup_cancel_at_each_stage_leaves_nothing():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        dest = out / "b.zip"
        # 막지 않으면 만들어진다 — 아래 취소 단언이 비지 않게
        assert storage.backup(db, ini, out / "free.zip").state == "ok" and (out / "free.zip").exists()
        keep = config.BACKUP_COPY_CHUNK
        config.BACKUP_COPY_CHUNK = 4096   # 덩어리를 작게 - zip 쓰는 도중에도 취소를 여러 번 본다
        try:
            for n in (1, 2, 3, 10):
                calls = {"n": 0}

                def cancel(n=n, calls=calls):
                    calls["n"] += 1
                    return calls["n"] >= n

                r = storage.backup(db, ini, dest, cancel=cancel)
                assert r.state == "cancelled", (n, r)
                assert not dest.exists() and not (out / "b.zip.part").exists(), (n, "반쯤 쓴 zip 이 남았다")
                assert _strays(data) == [], (n, "임시 사본이 남았다", _strays(data))
        finally:
            config.BACKUP_COPY_CHUNK = keep
        assert _matches(db) == 2


def test_backup_finishes_while_another_connection_writes():
    """②-2 리뷰 — 나눠 복사하면 걸음 사이에 다른 연결이 쓸 때마다 SQLite 가 처음부터 다시 복사한다(수집 예약이 쓰는 동안 끝나지 않음).
    기록을 한 걸음(2048쪽 · 8MB)보다 크게 만들고, 걸음마다 다른 연결이 한 줄 쓴다. 상한까지 끝나지 않으면 취소 → 'ok' 가 아니다."""
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS pad (b BLOB)")
        conn.executemany("INSERT INTO pad VALUES (?)", [(os.urandom(4000),) for _ in range(3000)])   # 약 12MB
        conn.commit()
        calls = {"n": 0}
        writer = sqlite3.connect(str(db))
        try:
            def cancel():
                calls["n"] += 1
                writer.execute("INSERT INTO pad VALUES (x'00')")
                writer.commit()
                return calls["n"] > 50
            r = storage.backup(db, ini, out / "busy.zip", cancel=cancel)
        finally:
            writer.close()
            conn.close()
        assert r.state == "ok", ("다른 연결이 쓰는 동안 백업이 끝나지 않았다", r, calls)
        assert _strays(data) == []


def test_backup_error_cleans_part_and_tmp():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        r = storage.backup(db, ini, out / "no_such_dir" / "b.zip")
        assert r.state in ("error", "nospace"), r
        assert _strays(data) == []
        r = storage.backup(data / "missing.db", ini, out / "b.zip")
        assert r.state == "error" and not (data / "missing.db").exists(), ("없는 DB 를 빈 파일로 만들었다", r)
        assert not (out / "b.zip").exists()


def test_backup_nospace_creates_nothing():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        z = _good_zip(Path(t) / "x")
        keep = storage._free
        try:
            storage._free = lambda p: 0
            r = storage.backup(db, ini, out / "b.zip")
            assert r.state == "nospace" and r.need > 0, r
            assert not (out / "b.zip").exists() and _strays(data) == []
            info = storage.inspect_backup(z, db)
            assert info.state == "nospace" and info.need > 0 and _strays(data) == [], (info.state, info.need)
        finally:
            storage._free = keep
        assert storage.backup(db, ini, out / "b.zip").state == "ok", "여유가 있으면 만든다 — 위 단언이 비었다"


def test_nospace_on_a_different_volume_is_checked_per_folder():
    """사본(데이터 폴더)과 zip(고른 폴더)이 다른 드라이브면 각각 잰다 - 한쪽만 모자라도 거부."""
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        keep = (storage._same_volume, storage._free)
        try:
            storage._same_volume = lambda a, b: False
            storage._free = lambda p: 0 if Path(p) == out else 1 << 40
            r = storage.backup(db, ini, out / "b.zip")
            assert r.state == "nospace" and not (out / "b.zip").exists(), r
            storage._free = lambda p: 1 << 40
            assert storage.backup(db, ini, out / "b.zip").state == "ok", "여유가 있으면 만든다 - 위 단언이 비었다"
        finally:
            storage._same_volume, storage._free = keep


def test_snapshot_connection_has_secure_delete():
    """사본 연결에도 secure_delete(M6) — 연결에서 실행한 명령을 기록해 본다."""
    log: list[str] = []

    class Rec(sqlite3.Connection):
        def execute(self, sql, *a):
            log.append(sql)
            return super().execute(sql, *a)

    real = sqlite3.connect
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        sqlite3.connect = lambda *a, **k: real(*a, factory=Rec, **k)
        try:
            assert storage.backup(db, ini, out / "b.zip").state == "ok"
        finally:
            sqlite3.connect = real
    assert any("secure_delete=ON" in s for s in log), log
    assert any("journal_mode=DELETE" in s for s in log), "사본이 -wal 을 남긴다 — 한 파일이어야"


# ── 복원: 복원 전 백업 · 취소 · 바꿔 끼우기 ─────────────────────────────────

def _backup_then_change(t: Path):
    data, db, ini, out = _env(t)
    z = out / "b.zip"
    assert storage.backup(db, ini, z).state == "ok"
    _empty_matches(db)
    return data, db, ini, out, z


def test_pre_backup_failure_blocks_restore_and_keeps_current_file():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out, z = _backup_then_change(Path(t))
        before = db.read_bytes()
        # 막지 않았으면 일어났을 일 — 같은 조건에서 복원 전 백업이 되면 복원된다
        info = storage.inspect_backup(z, db)
        ok = storage.restore(db, ini, info, pre_backup=out / "pre.zip")
        assert ok.state == "ok" and (out / "pre.zip").exists() and ok.pre_bytes > 0 and _matches(db) == 2, ok
        # 이제 복원 전 백업이 안 되는 곳(없는 폴더)을 준다 → 복원하지 않는다
        _empty_matches(db)
        before = db.read_bytes()
        info = storage.inspect_backup(z, db)
        assert info.state == "ok"
        res = storage.restore(db, ini, info, pre_backup=out / "no_such" / "pre.zip")
        assert res.state == "prebackup", res
        assert db.read_bytes() == before and _matches(db) == 0, "복원 전 백업이 실패했는데 복원했다"
        assert _strays(data) == [], "복원하지 않았는데 임시 파일이 남았다"


def test_pre_backup_holds_the_zip_of_the_old_records():
    """복원 전 zip 이 지금(바꾸기 전) 기록을 담는다."""
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out, z = _backup_then_change(Path(t))
        info = storage.inspect_backup(z, db)
        assert storage.restore(db, ini, info, pre_backup=out / "pre.zip").state == "ok"
        info2 = storage.inspect_backup(out / "pre.zip", db)
        assert info2.state == "ok" and info2.backup.matches == 0, "복원 전 zip 에 지금 기록이 아니라 복원본이 들었다"
        storage.discard(info2)


def test_restore_cancel_before_swap_keeps_old_file():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out, z = _backup_then_change(Path(t))
        before = db.read_bytes()
        info = storage.inspect_backup(z, db)
        assert storage.restore(db, ini, info, cancel=lambda: True).state == "cancelled"
        assert db.read_bytes() == before and _matches(db) == 0 and _strays(data) == []
        info = storage.inspect_backup(z, db)
        assert storage.restore(db, ini, info).state == "ok" and _matches(db) == 2, "취소 없이는 복원된다 — 위 단언이 비었다"


def test_restore_busy_keeps_old_file_and_removes_tmp():
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out, z = _backup_then_change(Path(t))
        info = storage.inspect_backup(z, db)
        hold = sqlite3.connect(str(db))
        hold.execute("BEGIN")
        hold.execute("SELECT COUNT(*) FROM matches").fetchone()
        try:
            res = storage.restore(db, ini, info)
        finally:
            hold.rollback()
            hold.close()
        assert res.state == "busy", res
        assert _matches(db) == 0 and _strays(data) == []


def test_restore_replaces_old_wal_not_overlays_it():
    """옛 -wal 이 새 파일에 덧씌워지지 않는다 — 열려 있던 연결이 쓴 WAL 이 남은 상태에서 복원."""
    with tempfile.TemporaryDirectory() as t:
        data, db, ini, out = _env(Path(t))
        z = out / "b.zip"
        assert storage.backup(db, ini, z).state == "ok"
        conn = store.open_db(db)
        conn.execute("DELETE FROM matches")
        conn.commit()   # WAL 에 남아 있다(연결을 열어 둔 채)
        assert (data / "fifa.db-wal").exists()
        conn.close()
        info = storage.inspect_backup(z, db)
        res = storage.restore(db, ini, info)
        assert res.state == "ok", res
        assert _matches(db) == 2, "옛 WAL 이 복원본 위에 덮였다"


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
