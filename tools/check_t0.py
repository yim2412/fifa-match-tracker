"""63단계 골든 — 내 계정 10-01~10-23 날마다 승·무·패·FC 를 앱 계산(stats.win_divisions → habits.club_day)으로 다시 세어
기준 화면 값과 맞춘다(이 PC 전용 · 배포판 밖 · fifa.db 는 읽기 전용).

    python tools/check_t0.py            # 대조 — 날마다 [OK]/[FAIL] · 하나라도 틀리면 종료 코드 1
    python tools/check_t0.py --write    # 골든 다시 적기(아래 "골든의 출처")

골든 파일은 내 계정 실데이터라 **저장소에 안 넣는다** — `DATA_DIR/t0_golden.json`(51 측정 폴더 `measure/` 옆).

골든의 출처: 2026-10-25 기준 화면과 대조할 때 "넥슨 원문 +9시간으로 하루를 나누면 23일 전부 오차 0" 이었다(그날 화면에서 읽은
값은 파일로 안 남겼다 — ROADMAP 63 "재지 않은 것"). `--write` 는 그 계산을 **앱 코드를 안 거치고** 여기서 따로 한다(원문 문자열 +
9시간 · 결과 글자 · 등급별 승리 FC 15/20). 그래서 이 대조는 "앱의 하루 나누기가 화면과 맞던 계산과 같은가"를 잰다 — 앱이 다시
원문을 KST 로 읽으면 새벽 경기가 전날로 가 FAIL 한다(63 전 코드로 FAIL 확인).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import habits  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402

FIRST, LAST = date(2026, 10, 1), date(2026, 10, 23)
GOLDEN = config.DATA_DIR / "t0_golden.json"
REF_OFFSET_H = 9                  # 기준 화면과 맞던 계산 — 앱 상수(config.MATCH_DATE_OFFSET_H)를 일부러 안 쓴다
REF_FC = {900: 15, 800: 20}       # 같은 이유로 손으로 — 사용자 확인값(2026-10-12 · config.FC_REWARDS 와 같아야)


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _mine(conn) -> str:
    ouid = store.trade_state(conn).get("my_ouid")
    if not ouid:
        raise SystemExit("[FAIL] 내 계정(trade_state my_ouid)이 없다 — 앱에서 거래 받기를 한 번 돌린 뒤")
    return ouid


def reference(details: list[dict], ouid: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for d in details:
        if d.get("matchType") not in (None, config.DEFAULT_MATCH_TYPE):
            continue
        me = next((p for p in d.get("matchInfo") or [] if p.get("ouid") == ouid), None)
        raw = str(d.get("matchDate") or "")
        if me is None or len(raw) < 19:
            continue
        day = (datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S") + timedelta(hours=REF_OFFSET_H)).date()
        if not FIRST <= day <= LAST:
            continue
        res = (me.get("matchDetail") or {}).get("matchResult") or ""
        k = 0 if "승" in res else 1 if "무" in res else 2 if "패" in res else None
        if k is None:
            continue
        row = out.setdefault(day.isoformat(), [0, 0, 0, 0])
        row[k] += 1
        if k == 0:
            row[3] += REF_FC.get(me.get("division"), 0)
    return out


def app_days(details: list[dict], ouid: str) -> dict[str, list[int]]:
    rows = stats.win_divisions(details, ouid)
    out = {}
    d = FIRST
    while d <= LAST:
        s = habits.club_day(rows, d)
        if s.win or s.draw or s.lose:
            out[d.isoformat()] = [s.win, s.draw, s.lose, s.fc]
        d += timedelta(days=1)
    return out


def main(argv: list[str]) -> int:
    conn = _open()
    try:
        ouid = _mine(conn)
        details = store.load_details(conn, ouid, config.DEFAULT_MATCH_TYPE)
    finally:
        conn.close()
    if "--write" in argv:
        ref = reference(details, ouid)
        GOLDEN.write_text(json.dumps({"made": datetime.now().isoformat(timespec="seconds"), "first": FIRST.isoformat(),
                                      "last": LAST.isoformat(), "days": ref}, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"[OK]   골든 {len(ref)}일 → {GOLDEN}")
        return 0
    try:
        gold = json.loads(GOLDEN.read_text(encoding="utf-8"))["days"]
    except (OSError, ValueError, KeyError):
        print(f"[FAIL] 골든이 없다 — python tools/check_t0.py --write  ({GOLDEN})")
        return 1
    got = app_days(details, ouid)
    bad = 0
    for day in sorted(set(gold) | set(got)):
        g, a = gold.get(day), got.get(day)
        ok = g == a
        bad += not ok
        print(f"{'[OK]  ' if ok else '[FAIL]'} {day}  기준 승무패FC {g}  앱 {a}")
    print(f"\n{len(set(gold) | set(got)) - bad}/{len(set(gold) | set(got))}일 일치")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
