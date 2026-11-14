"""[정보 › 저장 공간](57 K9) — 종류별 크기 재기와 지우기. 화면 스레드에서 부르지 않는다(크기·지우기 모두 작업 스레드 —
fifa.db 600MB 안팎 · 그림 수천 장). 무엇을 언제 멈추고 지우는지(걸쇠·쓰는 쪽 멈추기)는 app_main.MainWindow.clear_storage.

| 종류 | 무엇 | 지우기 |
|---|---|---|
| rank | rank.db(+-wal·-shm) | `app_main.clear_rank_records` 그대로(수집을 끈다 · 화면 스레드) |
| cards | fifa.db `store.CARD_TABLES` | `clear_cards` — 표를 비운다 |
| images | `.cache/` 아래 폴더 전부(얼굴·배지·엠블럼·표 칸 그림 …) | `clear_images` — 메타·상세 파일은 남는다 |
| details | `.cache/` 최상위 `*.json` 중 `meta_` 아닌 것(DB 에 넣기 전 경기 상세) | `clear_details` |
| dist | 구단주 분포·랭커 픽·포지션 특성 표 | 기존 지우기(`ownerdist.purge` · `MainWindow.purge_ranker_pick_data`) |
| all | 위 전부 + fifa.db 의 사용자 자료 표 전부 | `reset_db`(남길 행만 새 파일로 → 바꿔 끼우기) + `clear_cache` + rank |

내 기록 백업·복원(109 J9 — 맨 아래 절): `backup`(SQLite backup API 사본 + settings.ini → zip 하나) · `inspect_backup`(풀기·검사 — 임시 파일) ·
`restore`(복원 전 백업 → `_swap_in` 바꿔 끼우기 → 되돌릴 설정 키) · `sweep_tmp`(켤 때 쓸기). API 키·rank.db 는 zip 에 안 넣는다(U4).

지우기 + VACUUM 대신 바꿔 끼우기(2회차 [중]2): fifa.db 는 WAL 이라 secure_delete 로 600MB 를 지우면 WAL 이 DB 만큼 자라고,
VACUUM 뒤 체크포인트 없이는 파일이 안 준다. 남길 행(`store.RESET_KEEP`)만 새 파일로 옮겨 바꿔 끼우면 빠르고 옛 바이트가 안 남는다.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config
import matchtime
import store
import updatecheck

KINDS = ("rank", "cards", "images", "details", "dist", "all")
META_PREFIX = "meta_"   # .cache 최상위의 메타(오프라인 등급·시즌 이름 — nexon_api) — 상세 캐시가 아니다


def _files_bytes(paths) -> int:
    n = 0
    for p in paths:
        try:
            n += p.stat().st_size
        except OSError:
            pass
    return n


def db_files(path: Path) -> list[Path]:
    """DB 본 파일과 -wal·-shm."""
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


def image_dirs(cache_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in cache_dir.iterdir() if p.is_dir())
    except OSError:
        return []


def detail_files(cache_dir: Path) -> list[Path]:
    try:
        return [p for p in cache_dir.iterdir()
                if p.is_file() and p.suffix == ".json" and not p.name.startswith(META_PREFIX)]
    except OSError:
        return []


def dir_bytes(path: Path) -> int:
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return n


@dataclass
class Sizes:
    """종류 → 바이트(근사 · 모르면 None). pending = 상세 캐시 중 DB 에 아직 없는 경기(지우면 그 이어 받기분도 사라진다)."""
    bytes: dict = field(default_factory=dict)
    pending: int = 0
    details: int = 0


def measure(db_path: Path, rank_path: Path, cache_dir: Path, stop: Callable[[], bool] = lambda: False) -> Sizes:
    """종류별 크기 — 작업 스레드에서. fifa.db 안은 표 값 바이트 근사(store.table_bytes)."""
    out = Sizes()
    out.bytes["rank"] = _files_bytes(db_files(rank_path))
    imgs = image_dirs(cache_dir)
    out.bytes["images"] = sum(dir_bytes(d) for d in imgs)
    if stop():
        return out
    files = detail_files(cache_dir)
    out.details = len(files)
    out.bytes["details"] = _files_bytes(files)
    out.bytes["cards"] = out.bytes["dist"] = None
    try:
        conn = store.open_db(db_path)
        try:
            out.bytes["cards"] = store.table_bytes(conn, store.CARD_TABLES)
            out.bytes["dist"] = store.table_bytes(conn, store.DIST_TABLES)
            if files:
                have = store.all_match_ids(conn)
                out.pending = sum(1 for p in files if p.stem not in have)
        finally:
            conn.close()
    except sqlite3.Error:
        pass   # 잠김 — 그 칸은 "?"
    out.bytes["all"] = (_files_bytes(db_files(db_path)) + out.bytes["rank"] + dir_bytes(cache_dir))
    return out


def _remove(paths) -> tuple[int, int]:
    """→ (지운 수, 못 지운 수). 폴더는 통째로."""
    ok = bad = 0
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
            ok += 1
        except OSError:
            bad += 1
    return ok, bad


def clear_images(cache_dir: Path) -> tuple[int, int]:
    return _remove(image_dirs(cache_dir))


def clear_details(cache_dir: Path) -> tuple[int, int]:
    return _remove(detail_files(cache_dir))


def clear_cache(cache_dir: Path) -> tuple[int, int]:
    """.cache 안 전부(내 기록 전부 — 메타·로그 포함). 폴더 자체는 남긴다(쓰는 쪽이 만들지 않고 바로 쓰는 자리가 있다)."""
    try:
        return _remove(list(cache_dir.iterdir()))
    except OSError:
        return 0, 0


def clear_cards(db_path: Path) -> int:
    conn = store.open_db(db_path)
    try:
        return store.clear_tables(conn, store.CARD_TABLES)
    finally:
        conn.close()


def reset_db(db_path: Path, cancel: Callable[[], bool] = lambda: False) -> str:
    """fifa.db 를 남길 행만 담은 새 파일로 바꿔 끼운다 → "ok" · "busy"(다른 연결이 잡고 있음 — 옛 파일 그대로) ·
    "cancelled"(바꿔 끼우기 전에 멈춤) · "error"(디스크 모자람 등 — 옛 파일 그대로). 이 프로세스의 다른 연결은 부르는 쪽이 먼저 닫는다."""
    new = db_path.with_name(db_path.name + ".new")
    for f in db_files(new):
        f.unlink(missing_ok=True)
    try:
        store.copy_kept(db_path, new)
    except (sqlite3.Error, OSError):
        _remove(db_files(new))
        return "error"
    return _swap_in(db_path, new, cancel)


def _swap_in(db_path: Path, new: Path, cancel: Callable[[], bool]) -> str:
    """다 만든 새 파일 `new` 를 fifa.db 자리에 바꿔 끼운다(reset_db·복원 공용) → "ok" · "cancelled" · "busy". ok 가 아니면 `new` 를 지운다.
    순서: 취소 확인 → 옛 파일 체크포인트(TRUNCATE) → 옛 -wal·-shm 지우기 → os.replace 한 번."""
    if cancel():
        _remove(db_files(new))
        return "cancelled"
    try:
        old = sqlite3.connect(str(db_path), timeout=store.OPEN_TIMEOUT_S)
        try:
            busy = old.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        finally:
            old.close()
    except sqlite3.Error:
        busy = 1
    if busy:
        _remove(db_files(new))
        return "busy"
    # 옛 -wal·-shm 부터 — 남아 있으면 새 본 파일에 옛 WAL 이 덧씌워진다. 다른 실행본이 열고 있으면 윈도우가 막는다 → 옛 파일 그대로
    for f in db_files(db_path)[1:]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            _remove(db_files(new))
            return "busy"
    try:
        os.replace(new, db_path)
    except OSError:
        _remove(db_files(new))
        return "busy"
    return "ok"


# ── 내 기록 백업·복원(109 G1 · J9) ────────────────────────────────────────────────
# zip 안에는 이름이 정확히 두 개: fifa.db(SQLite backup API 사본) · settings.ini. API 키(자격 증명·.env)와 rank.db 는 안 넣는다(U4).
# 화면 스레드에서 부르지 않는다(800MB 안팎). 걸쇠(CLEARING)·쓰는 로더 멈추기는 app_main.MainWindow.start_restore 가 한다.
DB_ENTRY, INI_ENTRY = "fifa.db", "settings.ini"
BACKUP_ENTRIES = (DB_ENTRY, INI_ENTRY)
REQUIRED_TABLES = ("matches", "match_players")
# 복원이 되돌리지 않는 settings.ini 키 — 창 위치(다른 화면에서 만든 백업이면 화면 밖일 수 있다) · "바뀐 점" 을 본 버전(M5)
SETTINGS_RESTORE_SKIP = ("window/geometry", updatecheck.SEEN_VERSION_KEY)


class _Cancelled(Exception):
    pass


@dataclass
class BackupResult:
    state: str            # "ok" · "nospace" · "cancelled" · "error"
    zip_bytes: int = 0
    db_bytes: int = 0
    need: int = 0         # nospace 일 때 필요한 바이트(여유 포함)
    detail: str = ""


def _free(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _same_volume(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _space_short(dir_a: Path, need_a: int, dir_b: Path, need_b: int) -> int:
    """두 폴더에 각각 need 바이트가 들어가나 → 0(충분) · 모자라면 필요한 바이트. 같은 드라이브면 합쳐서 본다."""
    margin = config.BACKUP_SPACE_MARGIN
    if _same_volume(dir_a, dir_b):
        total = need_a + need_b + margin
        return 0 if _free(dir_a) >= total else total
    for d, n in ((dir_a, need_a), (dir_b, need_b)):
        if _free(d) < n + margin:
            return n + margin
    return 0


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""   # 설정을 한 번도 안 저장했다 - 빈 settings.ini 로 쓴다(zip 의 이름은 늘 둘)


def _snapshot_db(src_path: Path, dst_path: Path, cancel: Callable[[], bool]) -> None:
    """SQLite backup API 로 일관된 사본(WAL 까지 합친 파일 하나). 걸음 크기는 config.BACKUP_PAGES_PER_STEP(한 번에 전부 — 까닭은 거기)."""
    if cancel():
        raise _Cancelled()
    if not src_path.exists():
        raise FileNotFoundError(str(src_path))   # connect 가 빈 파일을 만들지 않게
    src = sqlite3.connect(str(src_path), timeout=store.OPEN_TIMEOUT_S)
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            dst.execute("PRAGMA secure_delete=ON")   # 모든 연결 secure_delete(store 머리말) - 사본에서 지울 일은 없지만 규칙은 한 곳으로

            def progress(_status, _remaining, _total):
                if cancel():
                    raise _Cancelled()

            src.backup(dst, pages=config.BACKUP_PAGES_PER_STEP, progress=progress)
            dst.execute("PRAGMA journal_mode=DELETE")   # 사본은 한 파일이어야(-wal 없이)
        finally:
            dst.close()
    finally:
        src.close()


def _write_zip(part: Path, db_tmp: Path, ini: bytes, cancel: Callable[[], bool]) -> None:
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=config.BACKUP_ZIP_LEVEL, allowZip64=True) as zf:
        with zf.open(DB_ENTRY, "w", force_zip64=True) as out, open(db_tmp, "rb") as f:
            while True:
                chunk = f.read(config.BACKUP_COPY_CHUNK)
                if not chunk:
                    break
                if cancel():
                    raise _Cancelled()
                out.write(chunk)
        zf.writestr(INI_ENTRY, ini)


def backup(db_path: Path, ini_path: Path, dest: Path, cancel: Callable[[], bool] = lambda: False) -> BackupResult:
    """fifa.db 사본 + settings.ini 를 zip 하나로 `dest` 에. 임시 이름(`dest.part`)으로 다 쓴 뒤 이름 바꾸기 - 실패·취소면 반쯤 쓴 것을
    지운다. 시작 전에 사본(데이터 폴더)과 zip(dest 폴더)이 들어갈 빈 공간을 잰다(압축 전 크기를 상한으로)."""
    dest = Path(dest)
    part = dest.with_name(dest.name + config.BACKUP_PART_SUFFIX)
    tmp = db_path.parent / f"{config.BACKUP_TMP_PREFIXES[1]}{uuid.uuid4().hex}.tmp"
    try:
        size = _files_bytes(db_files(db_path))
        short = _space_short(db_path.parent, size, dest.parent, size)
        if short:
            return BackupResult("nospace", need=short)
        _snapshot_db(db_path, tmp, cancel)
        db_bytes = tmp.stat().st_size
        _write_zip(part, tmp, _read_bytes(ini_path), cancel)
        os.replace(part, dest)
        return BackupResult("ok", zip_bytes=dest.stat().st_size, db_bytes=db_bytes)
    except _Cancelled:
        return BackupResult("cancelled")
    except (sqlite3.Error, OSError, zipfile.BadZipFile) as e:
        return BackupResult("error", detail=f"{type(e).__name__}: {e}")
    finally:
        _remove(db_files(tmp))
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)


@dataclass
class DbSummary:
    exists: bool = False
    matches: int = 0
    last_match: str = ""   # 마지막 경기 시각(KST 글자) - 없으면 ""
    accounts: int = 0


def summarize_conn(conn: sqlite3.Connection) -> DbSummary:
    n, last = conn.execute("SELECT COUNT(*), MAX(match_date) FROM matches").fetchone()
    acc = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    return DbSummary(True, int(n or 0), matchtime.label(last) if last else "", int(acc or 0))


def summarize_current(db_path: Path) -> DbSummary:
    """지금 fifa.db 의 확인 창용 집계 - 없거나 못 열면 exists=False."""
    if not db_path.exists():
        return DbSummary()
    try:
        conn = store.open_db(db_path)
        try:
            return summarize_conn(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return DbSummary()


@dataclass
class BackupInfo:
    state: str                      # "ok" · "bad"(zip·DB 가 아님/깨짐/필수 표 없음) · "newer"(이 앱보다 새 판) · "nospace" · "cancelled" · "error"
    reason: str = ""                # 사람이 읽는 까닭(화면 글)
    db_tmp: Path | None = None      # ok 일 때 풀어 둔 임시 파일(복원이 바꿔 끼운다 · 아니면 지운다)
    ini_tmp: Path | None = None
    backup: DbSummary = field(default_factory=DbSummary)
    current: DbSummary = field(default_factory=DbSummary)
    need: int = 0
    version: int = 0

    def files(self) -> list[Path]:
        out = list(db_files(self.db_tmp)) if self.db_tmp else []
        if self.ini_tmp:
            out.append(self.ini_tmp)
        return out


def _extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dst: Path, cancel: Callable[[], bool]) -> None:
    """한 항목을 우리가 고른 이름으로 푼다(zip 안 경로를 파일 이름으로 쓰지 않는다). 읽는 양은 zipfile 이 머리말 크기로 제한한다."""
    with zf.open(info) as src, open(dst, "wb") as out:
        while True:
            chunk = src.read(config.BACKUP_COPY_CHUNK)
            if not chunk:
                break
            if cancel():
                raise _Cancelled()
            out.write(chunk)


def _verify(path: Path) -> tuple[str, str, int, DbSummary]:
    """풀어 둔 DB 를 읽기 전용·immutable 로 열어 검사 -> (상태, 까닭, 스키마 판, 집계). immutable 이라 -wal·-shm 이 안 생긴다."""
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        qc = conn.execute("PRAGMA quick_check").fetchall()
        if not (len(qc) == 1 and qc[0][0] == "ok"):
            return "bad", "기록 파일이 깨져 있습니다.", 0, DbSummary()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(REQUIRED_TABLES) <= tables:
            return "bad", "경기 기록이 들어 있는 백업이 아닙니다.", 0, DbSummary()
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        if ver > store.SCHEMA_VERSION:
            return "newer", "이 프로그램보다 새 버전이 만든 백업입니다. 프로그램을 업데이트한 뒤 다시 시도해 주세요.", ver, DbSummary()
        return "ok", "", ver, summarize_conn(conn)
    finally:
        conn.close()


def inspect_backup(zip_path: Path, db_path: Path, cancel: Callable[[], bool] = lambda: False) -> BackupInfo:
    """백업 zip 을 검사한다(복원의 앞 절반 · 작업 스레드) - 이름 확인 -> 빈 공간 -> 데이터 폴더 안 임시 파일로 풀기 -> 검사 -> 양쪽 집계.
    ok 가 아니면 임시 파일을 지운다. ok 면 호출한 쪽이 `restore` 에 넘기거나 `discard` 로 지운다."""
    data_dir = db_path.parent
    token = uuid.uuid4().hex
    pre = config.BACKUP_TMP_PREFIXES[0]
    info = BackupInfo("error", db_tmp=data_dir / f"{pre}{token}.tmp", ini_tmp=data_dir / f"{pre}{token}.tmp.ini")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            items = zf.infolist()
            if sorted(i.filename for i in items) != sorted(BACKUP_ENTRIES):
                info.state, info.reason = "bad", "이 프로그램이 만든 백업 파일이 아닙니다."
                return info
            by = {i.filename: i for i in items}
            info.need = _space_short(data_dir, by[DB_ENTRY].file_size + by[INI_ENTRY].file_size, data_dir, 0)
            if info.need:
                info.state, info.reason = "nospace", "빈 공간이 모자랍니다."
                return info
            _extract(zf, by[DB_ENTRY], info.db_tmp, cancel)
            _extract(zf, by[INI_ENTRY], info.ini_tmp, cancel)
        info.state, info.reason, info.version, info.backup = _verify(info.db_tmp)
        if info.state == "ok":
            info.current = summarize_current(db_path)
        return info
    except _Cancelled:
        info.state, info.reason = "cancelled", ""
        return info
    except (zipfile.BadZipFile, EOFError, ValueError, RuntimeError, NotImplementedError, sqlite3.Error):
        info.state, info.reason = "bad", "백업 파일을 읽지 못했습니다. 깨졌거나 이 프로그램이 만든 파일이 아닙니다."
        return info
    except OSError as e:
        info.state, info.reason = "error", f"파일을 읽거나 쓰지 못했습니다: {e}"
        return info
    finally:
        if info.state != "ok":
            discard(info)


def discard(info: BackupInfo) -> None:
    """풀어 둔 임시 파일을 지운다(여러 번 불러도 된다)."""
    _remove(info.files())


@dataclass
class RestoreResult:
    state: str            # "ok" · "busy" · "cancelled" · "error" · "prebackup"(복원 전 백업이 안 돼 복원하지 않음)
    detail: str = ""      # "settings" = 기록은 되돌렸으나 설정 일부를 못 되돌림 · prebackup 의 까닭 상태
    pre_bytes: int = 0    # 복원 전 백업 zip 크기(만들었을 때)
    need: int = 0


def merge_settings(src_ini: Path, dst_ini: Path) -> int:
    """백업의 settings.ini 에서 SETTINGS_RESTORE_SKIP 밖의 키를 지금 settings.ini 에 쓴다 -> 쓴 키 수(M5). Qt 를 이 안에서만 부른다."""
    from PySide6.QtCore import QSettings
    src = QSettings(str(src_ini), QSettings.Format.IniFormat)
    dst = QSettings(str(dst_ini), QSettings.Format.IniFormat)
    n = 0
    for k in src.allKeys():
        if k in SETTINGS_RESTORE_SKIP:
            continue
        dst.setValue(k, src.value(k))
        n += 1
    dst.sync()
    if dst.status() != QSettings.Status.NoError:
        raise OSError("settings.ini 를 쓰지 못했습니다")
    return n


def restore(db_path: Path, ini_path: Path, info: BackupInfo, pre_backup: Path | None = None,
            cancel: Callable[[], bool] = lambda: False) -> RestoreResult:
    """검사를 마친 백업(`inspect_backup` ok)으로 fifa.db 를 바꿔 끼운다 - 걸쇠를 잡고 쓰는 로더를 멈춘 **뒤에** 작업 스레드에서.
    순서: (pre_backup 이 있으면) 지금 기록 zip 먼저 - 실패하면 복원하지 않는다 -> `_swap_in`(체크포인트 · -wal·-shm 지우기 · os.replace 한 번)
    -> 되돌릴 설정 키. 어느 쪽이든 임시 파일은 끝에 지운다. 바꿔 끼우기 전에 멈추면 지금 파일 그대로."""
    try:
        res = RestoreResult("ok")
        if pre_backup is not None:
            b = backup(db_path, ini_path, pre_backup, cancel)
            if b.state == "cancelled":
                return RestoreResult("cancelled")
            if b.state != "ok":
                return RestoreResult("prebackup", detail=b.state, need=b.need)
            res.pre_bytes = b.zip_bytes
        state = _swap_in(db_path, info.db_tmp, cancel)
        if state != "ok":
            return RestoreResult(state)
        try:
            merge_settings(info.ini_tmp, ini_path)
        except Exception:   # noqa: BLE001 - 기록은 이미 바뀌었다. 설정만 못 되돌렸다고 알린다
            res.detail = "settings"
        return res
    except OSError:
        return RestoreResult("error")
    finally:
        discard(info)


def sweep_tmp(data_dir: Path) -> int:
    """켤 때 - 복원·백업 도중 죽어 남은 임시 파일(옛 기록 전부가 담겨 있다)을 지운다 -> 지운 수. 백업 zip 의 `.part` 는 사용자가 고른 폴더라 못 쓴다."""
    n = 0
    for prefix in config.BACKUP_TMP_PREFIXES:
        try:
            found = list(data_dir.glob(f"{prefix}*.tmp*"))
        except OSError:
            continue
        for p in found:
            if p.is_file():
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
    return n
