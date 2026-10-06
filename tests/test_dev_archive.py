"""개발용 상시 수집(tools/dev_archive.py) 테스트 — 가짜 rank.db·fifa.db 로, 네트워크 없이.

pytest 없이 `python tests/test_dev_archive.py`. 닉네임·프로필 번호·경기 id 는 전부 지어낸 값이다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import config
import dev_archive as D
import rankcollect as rc
import ranker
import store

T0 = datetime(2026, 10, 7, 9, 0, 0)
HTML = '<p class="rank_advice">※ 2026-10-07 06:00:00 기준 데이터로 현재와 다를 수 있으며</p>'


def _rows(n=5, shift=0, emblem_at=None):
    return [ranker.RankRow(rank=r, profile_sn=900000 + r + shift, nickname=f"n{r + shift}", elo=5000.0 - r, win=10 + r,
                           team_color="리버풀", formation="4-2-3-1", grade=1,
                           team_color_emblem="E1" if r == emblem_at else "")
            for r in range(1, n + 1)]


class Tmp:
    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="devarc_"))
        self.rank = self.dir / "rank.db"
        self.arc = self.dir / "arc.db"
        self._saved = (config.RANK_DB_PATH, D.LOG_PATH, D.PROGRESS_DIR)
        config.RANK_DB_PATH = self.rank
        D.LOG_PATH = self.dir / "log.txt"
        D.PROGRESS_DIR = self.dir / "prog"
        return self

    def __exit__(self, *a):
        config.RANK_DB_PATH, D.LOG_PATH, D.PROGRESS_DIR = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)


def test_archive_keeps_rows_after_source_prune():
    with Tmp() as t:
        r = rc.open_rank_db(t.rank)
        rc.save_snapshot(r, _rows(5, emblem_at=2), T0)
        rc.save_snapshot(r, _rows(5), T0 + timedelta(days=1))
        arc = D.open_archive(t.arc)
        assert D.archive_snapshots(r, arc, T0) == (2, 10)
        assert D.archive_snapshots(r, arc, T0) == (0, 0), "두 번째는 옮길 게 없다(멱등)"
        # 원본 정리 — 앱이 14일 뒤 지운다
        rc.prune_raw(r, T0 + timedelta(days=config.RANK_RAW_KEEP_DAYS + 5))
        assert r.execute("SELECT COUNT(*) FROM snapshot_rows").fetchone()[0] == 0, "전제: 원본이 지워졌다"
        assert arc.execute("SELECT COUNT(*) FROM snap_rows").fetchone()[0] == 10, "보관은 남는다"
        emb = arc.execute("SELECT emblem FROM snap_rows WHERE profile_sn = 900002 ORDER BY snap_id LIMIT 1").fetchone()[0]
        assert emb == "E1", f"엠블럼도 같이 옮긴다: {emb}"
        rc.save_snapshot(r, _rows(5), T0 + timedelta(days=20))
        assert D.archive_snapshots(r, arc, T0) == (1, 5), "새 스냅숏만"
        r.close(), arc.close()


def test_rank_db_reset_does_not_hide_new_snapshot():
    """rank.db 를 지우면 snapshots.id 가 1부터 다시 — id 로 대조하면 새 회차를 '이미 있음'으로 건너뛴다."""
    with Tmp() as t:
        r = rc.open_rank_db(t.rank)
        rc.save_snapshot(r, _rows(3), T0)
        arc = D.open_archive(t.arc)
        D.archive_snapshots(r, arc, T0)
        r.close()
        t.rank.unlink()
        r = rc.open_rank_db(t.rank)
        sid = rc.save_snapshot(r, _rows(3, shift=50), T0 + timedelta(days=2))
        assert sid == 1, "전제: 다시 1번"
        assert D.archive_snapshots(r, arc, T0) == (1, 3)
        r.close(), arc.close()


def test_parse_ref_time():
    assert D.parse_ref_time(HTML) == "2026-10-07 06:00:00"
    assert D.parse_ref_time("<p>점검 중</p>") is None
    with Tmp() as t:
        arc = D.open_archive(t.arc)
        assert D.record_ref_time(arc, T0, fetch=lambda: "<p>바뀐 페이지</p>") is None
        assert arc.execute("SELECT ref_time FROM ref_times").fetchall()[0][0] is None, "못 찾음도 적는다(구조 변경 신호)"
        arc.close()


def test_pick_day_records_starters_without_subs():
    with Tmp() as t:
        conn = store.open_db(t.dir / "fifa.db")
        players = [{"spId": 100 + i, "spPosition": po, "spGrade": 5}
                   for i, po in enumerate([0, 3, 5, 7, 10, 13, 15, 23, 25, 27, 9])]
        players.append({"spId": 999, "spPosition": 28, "spGrade": 1})  # 교체 명단 — 선발 아님
        store.save_matches(conn, [{"matchId": "m1", "matchType": config.DEFAULT_MATCH_TYPE,
                                   "matchDate": "2026-10-06T10:00:00",
                                   "matchInfo": [{"ouid": "o1", "nickname": "n1", "player": players}]}])
        store.save_ranker_squad(conn, 900001, nickname="n1", ouid="o1", rank=1, match_id="m1", match_day="2026-10-06",
                                fetched_at="2026-10-06T09:00:00", fail=None, source=store.PICK)
        store.save_ranker_squad(conn, 900002, nickname="n2", ouid=None, rank=2, match_id=None, match_day=None,
                                fetched_at="2026-10-06T09:00:00", fail="최근 감독모드 경기 없음", source=store.PICK)
        arc = D.open_archive(t.arc)
        targets = [{"rank": 1, "profile_sn": 900001}, {"rank": 2, "profile_sn": 900002}, {"rank": 3, "profile_sn": 900003}]
        assert D.snapshot_pick_day(conn, arc, targets, "2026-10-07") == 2, "받은 적 없는 랭커(3)는 줄이 없다"
        st = json.loads(arc.execute("SELECT starters FROM pick_days WHERE profile_sn = 900001").fetchone()[0])
        assert len(st) == 11 and 999 not in [s[0] for s in st], st
        assert arc.execute("SELECT fail, starters FROM pick_days WHERE profile_sn = 900002").fetchone()[1] is None
        assert D.snapshot_pick_day(conn, arc, targets, "2026-10-07") == 2
        assert arc.execute("SELECT COUNT(*) FROM pick_days").fetchone()[0] == 2, "같은 날 다시 돌면 덮어쓴다"
        D.snapshot_pick_day(conn, arc, targets, "2026-10-08")
        assert arc.execute("SELECT COUNT(DISTINCT day) FROM pick_days").fetchone()[0] == 2, "날짜별로 쌓인다"
        conn.close(), arc.close()


def test_run_once_a_day_and_one_failure_does_not_block_others():
    with Tmp() as t:
        r = rc.open_rank_db(t.rank)
        rc.save_snapshot(r, _rows(3), T0)
        r.close()
        saved = D.record_ref_time

        def boom(arc, now, fetch=None):
            raise RuntimeError("연결 안 됨")
        D.record_ref_time = boom
        try:
            assert D.run({1, 2}, archive_path=t.arc) == 1, "② 실패 → 종료 코드 1"
        finally:
            D.record_ref_time = saved
        arc = D.open_archive(t.arc)
        assert arc.execute("SELECT COUNT(*) FROM snap_rows").fetchone()[0] == 3, "① 은 그래도 했다"
        day = datetime.now().date().isoformat()
        assert not D.ran_ok_today(arc, day), "실패한 날은 다음 로그온에 다시"
        arc.close()
        D.record_ref_time = lambda arc, now, fetch=None: "2026-10-07 06:00:00"
        try:
            assert D.run({1, 2}, archive_path=t.arc) == 0
            arc = D.open_archive(t.arc)
            assert D.ran_ok_today(arc, day)
            arc.close()
            calls = []
            D.record_ref_time = lambda arc, now, fetch=None: calls.append(1)
            assert D.run({1, 2}, archive_path=t.arc) == 0 and calls == [], "성공한 날은 다시 안 돈다"
            assert D.run({1, 2}, force=True, archive_path=t.arc) == 0 and calls == [1], "--force 는 돈다"
        finally:
            D.record_ref_time = saved
        assert not list((t.dir / "prog").glob("*.txt")), "상태줄 파일은 끝나면 지운다"
        # 첫 단계가 실패해도 뒤 단계는 돈다(마지막 단계 실패로는 '실패 뒤 멈춤'이 안 보인다)
        saved_arch, got = D._arch, []

        def arch_boom(*a):
            raise RuntimeError("rank.db 잠김")
        D._arch = arch_boom
        D.record_ref_time = lambda arc, now, fetch=None: got.append(1)
        try:
            assert D.run({1, 2}, force=True, archive_path=t.arc) == 1
            assert got == [1], "① 실패 뒤에도 ② 가 돌아야 한다"
        finally:
            D._arch, D.record_ref_time = saved_arch, saved
        assert "[FAIL] ② 기준 시각" in t.dir.joinpath("log.txt").read_text(encoding="utf-8")


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
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
