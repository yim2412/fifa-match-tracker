"""내 메모·표시(109 U2·L2·F1) — notes 표의 저장·읽기·정리와 지우기. `python tests/test_notes.py` 로 실행(Qt·네트워크 없음).

화면·스레드 쪽은 `test_filter_screens.py`(화면 · QThread 막음)와 `test_notes_thread.py`(실제 QThread)가 잰다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

import config  # noqa: E402
import storage  # noqa: E402
import store  # noqa: E402


def _db():
    d = tempfile.mkdtemp()
    return d, store.open_db(Path(d) / "f.db")


def test_roundtrip_marks_memos_and_opp_notes():
    _d, c = _db()
    try:
        store.save_note(c, store.NOTE_MATCH, "m1", mark="cut", memo="  랙이   심함\n")
        store.save_note(c, store.NOTE_MATCH, "m2", mark="practice")
        store.save_note(c, store.NOTE_MATCH, "m3", memo="표시 없이 메모만")
        store.save_note(c, store.NOTE_OPP, "o1", memo="자주 만남", tags=["패스 위주", "  ", "패스 위주", "역습"])
        n = store.load_notes(c)
        assert n.marks == {"m1": "cut", "m2": "practice"}, n.marks
        assert n.memos == {"m1": "랙이 심함", "m3": "표시 없이 메모만"}, n.memos
        assert n.opp == {"o1": ("자주 만남", ("패스 위주", "역습"))}, n.opp
        assert n.shown == frozenset({"m1", "m2"}), "표시한 판 = 표시 코드가 있는 경기뿐(메모만 있는 경기는 안 빠진다)"
    finally:
        c.close()


def test_empty_row_is_deleted_and_unknown_mark_is_ignored():
    _d, c = _db()
    try:
        store.save_note(c, store.NOTE_MATCH, "m1", mark="cut", memo="x")
        store.save_note(c, store.NOTE_MATCH, "m1")           # 표시·메모 다 비움 → 줄이 사라진다
        assert c.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        store.save_note(c, store.NOTE_MATCH, "m2", mark="바보")   # 모르는 표시 코드는 저장하지 않는다
        assert c.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        store.save_note(c, store.NOTE_OPP, "o1", mark="cut")      # 상대 줄에는 표시가 없다
        assert c.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        try:
            store.save_note(c, "엉뚱", "k", memo="x")
            raise AssertionError("모르는 종류를 받았다")
        except ValueError:
            pass
        # 읽을 때도 거른다 — 옛 버전·손으로 고친 파일의 모르는 표시 코드·깨진 태그 JSON
        c.execute("INSERT INTO notes (kind, key, mark, memo, tags, updated_at) VALUES ('match', 'bad', 'zzz', NULL, NULL, 't')")
        c.execute("INSERT INTO notes (kind, key, mark, memo, tags, updated_at) VALUES ('opp', 'o9', NULL, '메모', '{깨짐', 't')")
        c.commit()
        n = store.load_notes(c)
        assert "bad" not in n.marks and n.opp == {"o9": ("메모", ())}, (n.marks, n.opp)
    finally:
        c.close()


def test_limits_come_from_config():
    long_memo = "가" * (config.NOTE_MEMO_MAX + 30)
    assert len(store.clean_memo(long_memo)) == config.NOTE_MEMO_MAX
    tags = store.clean_tags([f"태그{i}" + "나" * 40 for i in range(config.NOTE_TAGS_MAX + 4)])
    assert len(tags) == config.NOTE_TAGS_MAX and all(len(t) <= config.NOTE_TAG_MAX for t in tags), tags


def test_notes_with_matches_what_the_db_would_hold():
    """화면이 쥔 값(notes_with)과 DB 에서 읽은 값(load_notes)이 같은 규칙으로 간다 — 쓰기 끝난 뒤 화면 값이 DB 와 갈리지 않게."""
    _d, c = _db()
    try:
        mem = store.EMPTY_NOTES
        steps = [
            (store.NOTE_MATCH, "m1", dict(mark="cut", memo="a")),
            (store.NOTE_MATCH, "m2", dict(mark="practice")),
            (store.NOTE_MATCH, "m1", dict(mark=None, memo="a")),     # 표시만 뗌 — 메모는 남는다
            (store.NOTE_OPP, "o1", dict(memo="u", tags=["x", "y"])),
            (store.NOTE_MATCH, "m2", dict()),                         # 다 비움
            (store.NOTE_OPP, "o1", dict(memo="", tags=[])),
        ]
        for kind, key, kw in steps:
            store.save_note(c, kind, key, **kw)
            mem = store.notes_with(mem, kind, key, **kw)
            assert mem == store.load_notes(c), (kind, key, kw, mem, store.load_notes(c))
        assert mem.marks == {} and mem.memos == {"m1": "a"} and mem.opp == {}, mem
    finally:
        c.close()


def test_notes_table_is_wiped_on_reset_and_every_connection_is_secure_delete():
    """U3 — [내 기록 전부 지우기] 는 notes 도 지운다(RESET_WIPE) · 지운 메모 글자가 파일 바이트에 안 남는다(secure_delete)."""
    assert "notes" in store.RESET_WIPE and "notes" not in store.RESET_KEEP
    d, c = _db()
    path = Path(d) / "f.db"
    secret = "지울메모-고유글자"
    try:
        assert c.execute("PRAGMA secure_delete").fetchone()[0] == 1
        store.save_note(c, store.NOTE_MATCH, "m1", mark="cut", memo=secret)
        store.save_note(c, store.NOTE_OPP, "o1", memo=secret, tags=[secret])
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()
    assert secret.encode("utf-8") in path.read_bytes(), "시험이 공허하다 — 쓴 글자가 파일에 없다"
    assert storage.reset_db(path) == "ok"
    c = store.open_db(path)
    try:
        assert c.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()
    blob = b"".join(p.read_bytes() for p in storage.db_files(path) if p.exists())
    assert secret.encode("utf-8") not in blob, "전부 지우기 뒤에도 메모 글자가 파일에 남았다"


def test_load_notes_query_plan_has_no_temp_btree():
    _d, c = _db()
    try:
        plan = " ".join(str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN SELECT kind, key, mark, memo, tags FROM notes"))
        assert "TEMP B-TREE" not in plan.upper(), plan
    finally:
        c.close()


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
