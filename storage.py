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

지우기 + VACUUM 대신 바꿔 끼우기(2회차 [중]2): fifa.db 는 WAL 이라 secure_delete 로 600MB 를 지우면 WAL 이 DB 만큼 자라고,
VACUUM 뒤 체크포인트 없이는 파일이 안 준다. 남길 행(`store.RESET_KEEP`)만 새 파일로 옮겨 바꿔 끼우면 빠르고 옛 바이트가 안 남는다.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import store

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
