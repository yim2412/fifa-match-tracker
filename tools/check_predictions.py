"""시즌이 끝난 뒤 — 남겨 둔 예측(fifa.db predictions)을 실제 최종 순위와 대조한다(1.3.1 · 수동).

    python tools/check_predictions.py

예측은 원본(14일)이 지워져 나중에 다시 만들 수 없으므로 하루 한 줄씩 남겨 둔다. 끝난 시즌마다 그 계정의 최종 순위를
`n4seasonno` 랭킹 검색으로 받아 **저장된 프로필 번호**로 확인한다 — 시즌 중 닉네임을 바꾸면 닉네임으로는 못 찾거나
남을 찾는다(검토 B 3-8). 못 찾으면 "확인 불가".

한계: 한 계정·한 시즌으로는 "N% 라고 한 것의 실제 비율"을 못 낸다 — 여러 계정·시즌이 쌓여야 한다.
웹 요청을 하므로 넥슨 홈페이지 데이터가 켜져 있어야 한다.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import ranker  # noqa: E402
import store  # noqa: E402


def judge(row: dict, final_rank: int | None) -> str:
    """하루 기록 한 줄 → 대조 글. final_rank None = 1만 위 밖."""
    inside = {t: final_rank is not None and final_rank <= t for t in config.PREDICT_TARGETS}
    probs = " · ".join(f"{t}위 {row[f'p{t}'] * 100:.0f}%→{'안' if inside[t] else '밖'}" for t in config.PREDICT_TARGETS)
    lo, hi = row["lo"], row["hi"]
    hit = final_rank is not None and lo is not None and hi is not None and lo <= final_rank <= hi
    return f"{row['day']} {probs} · 범위 {lo}~{hi} {'맞음' if hit else '벗어남'}"


def main() -> int:
    if not config.WEB_DATA:
        print(f"[FAIL] {config.WEB_DATA_OFF_MSG}")
        return 1
    conn = store.open_db(config.DB_PATH)
    try:
        rows = store.predictions(conn)
        seasons = store.load_seasons(conn)
        names = {a["ouid"]: a.get("nickname") for a in store.list_accounts(conn)}
    finally:
        conn.close()
    if not rows:
        print("[OK] 남겨 둔 예측이 없습니다")
        return 0
    by_start = {s.start.isoformat(): s for s in seasons if s.end <= date.today()}
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["ouid"], r["season_start"]), []).append(r)
    for (ouid, start), items in groups.items():
        season = by_start.get(start or "")
        nick = names.get(ouid) or ouid[:8]
        if season is None:
            print(f"[--] {nick} · 시즌 시작 {start} — 아직 안 끝났거나 시즌표에 없음({len(items)}일)")
            continue
        sn = items[-1]["profile_sn"]
        try:
            info = ranker.fetch_manager_rank(nick, season_no=season.no)
        except ranker.RankerError as e:
            print(f"[FAIL] {nick} · {season.label}: {e}")
            continue
        if sn is None or info.profile_sn != sn:
            print(f"[--] {nick} · {season.label}: 확인 불가(프로필 번호가 다르거나 없음 — 닉네임을 바꿨을 수 있음)")
            continue
        print(f"[OK] {nick} · {season.label} 최종 {info.rank if info.rank else '1만 위 밖'}")
        for r in items:
            print("     " + judge(r, info.rank))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
