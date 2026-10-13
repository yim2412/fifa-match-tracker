"""예측 되짚기(50단계 B8) — 끝난 시즌마다 내 계정의 최종 순위를 넥슨 홈페이지에서 한 번 확인해 남겨 둔 예측과 같이 둔다.

| 무엇 | 규칙 |
|---|---|
| 대상 | **내 계정 하나**(`trade_state.my_ouid` + 키 지문 일치 — N23 과 같은 정의). 검색한 남의 계정은 묻지 않는다 |
| 정본 | `trade_state` 키 `predict_final:<ouid>:<season_start>` = JSON `{rank, state, first, last}` (시즌 단위 하나). `predictions.final_*` 열은 화면용 사본 |
| 상태 | `found`(순위) · `not_found`(1만 위 밖과 닉네임 바꿈을 구분하지 않는다) · 꺼짐·오류는 줄을 안 쓴다 |
| 다시 묻기 | **처음 물은 날부터** `PREDICT_CHECK_RETRY_DAYS` 동안 `not_found` 도 하루 한 번 — 그 뒤 굳힌다(시즌표가 늦게 따라와도 첫 not_found 가 바로 굳지 않게) |
| 관문 | `config.predict_check_allowed()` — 디스크 `.env` 로 다시(트레이에서 새 안내를 안 본 사람에게 웹 요청이 안 나가게) |
| 부르는 자리 | `rankcollect.process_meta` 끝 — 수집 작업 스레드. 시즌 넘김(_roll) 안이 아니라 **매 회차 "끝난 시즌에 결과 없는 내 줄이 있나"** 로 판정 |

시즌 번호는 fifa.db `seasons`(`check_predictions` 와 같은 출처)에서 `predictions.season_start` 로 찾고, 없으면 그 회차는 묻지 않는다.
요청은 `ranker.fetch_manager_rank`(웹 스위치·`web_get` 상한을 이미 지난다) 하나만 쓴다 — 새 웹 함수가 없다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

import config
import ranker
import store


def _key(ouid: str, season_start: str) -> str:
    return f"{store.PREDICT_FINAL_PREFIX}{ouid}:{season_start}"


def _load(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _settled(rec: dict | None, today: date) -> bool:
    """더 묻지 않아도 되나 — found, 또는 not_found 인데 처음 물은 날부터 RETRY_DAYS 가 지났다."""
    if not rec:
        return False
    if rec.get("state") == "found":
        return True
    try:
        first = date.fromisoformat(rec.get("first") or "")
    except ValueError:
        return False
    return (today - first).days >= config.PREDICT_CHECK_RETRY_DAYS


def _asked_today(rec: dict | None, today: date) -> bool:
    return bool(rec) and rec.get("last") == today.isoformat()


def run(*, fifa_path: Path | str | None = None, today: date | None = None, fetch=None,
        cancel: threading.Event | None = None) -> dict:
    """한 회차 — 끝난 시즌 중 결과가 안 정해진 시즌마다 최대 한 번 묻는다. → {"asked": n, "found": n, "skipped": 이유}.

    fetch(nickname, season_no) → ranker.RankerInfo (테스트가 바꿔 끼운다). 오류는 그 시즌만 건너뛰고 다음 회차가 다시."""
    if not config.predict_check_allowed():
        return {"skipped": "gate"}
    today = today or date.today()
    fetch = fetch or (lambda nick, no: ranker.fetch_manager_rank(nick, season_no=no))
    out = {"asked": 0, "found": 0}
    try:
        fconn = store.open_db(fifa_path or config.DB_PATH)
    except sqlite3.Error:
        return {"skipped": "db"}
    try:
        st = store.trade_state(fconn)
        mine = st.get("my_ouid")
        if not mine or st.get("key_fp") != store.key_fingerprint(config.API_KEY):
            return {"skipped": "no_account"}
        rows = store.predictions(fconn, mine)
        seasons = store.load_seasons(fconn)
        nick = next((a.get("nickname") for a in store.list_accounts(fconn) if a["ouid"] == mine), None)
        by_start = {s.start.isoformat(): s for s in seasons if s.end <= today}
        groups: dict[str, list[dict]] = {}
        for r in rows:
            if r.get("season_start") in by_start:
                groups.setdefault(r["season_start"], []).append(r)
        stamp = datetime.now().isoformat(timespec="seconds")
        for start, items in sorted(groups.items()):
            if cancel is not None and cancel.is_set():
                break
            key = _key(mine, start)
            rec = _load(st.get(key))
            if rec is None or not _settled(rec, today):
                if not nick or _asked_today(rec, today):
                    continue
                sn = next((r["profile_sn"] for r in reversed(items) if r["profile_sn"]), None)
                if sn is None:      # 대조할 프로필 번호가 없으면 결과가 not_found 로 정해져 있다 — 헛요청을 안 보낸다
                    continue
                try:
                    info = fetch(nick, by_start[start].no)
                except Exception:       # 오류·점검·꺼짐은 줄을 안 쓴다 — 다음 회차가 다시 (전역 규칙 7)
                    continue
                out["asked"] += 1
                found = sn is not None and info.profile_sn == sn and bool(info.rank)
                rec = {"rank": int(info.rank) if found else None, "state": "found" if found else "not_found",
                       "first": (rec or {}).get("first") or today.isoformat(), "last": today.isoformat()}
                out["found"] += int(found)
                if cancel is not None and cancel.is_set():      # [수집 기록 지우기] 뒤에 되살아나지 않게
                    break
                store.set_trade_state(fconn, **{key: json.dumps(rec)})
            if _settled(rec, today):
                store.set_prediction_final(fconn, mine, start, rank=rec.get("rank"), state=rec["state"],
                                           checked_at=stamp)
        return out
    except sqlite3.Error:
        return {"skipped": "db"}
    finally:
        fconn.close()


def judge_row(row: dict, final_rank: int | None) -> tuple:
    """하루 예측 한 줄 × 최종 순위 → (200위 안 맞음?, 80% 범위 안?) — 순위를 모르면 (None, None).
    final_rank None 이 not_found 라는 뜻이면 부르는 쪽이 건너뛴다 — 1만 위 밖과 닉네임 바꿈을 가를 수 없다."""
    if final_rank is None:
        return None, None
    in_top = final_rank <= config.PREDICT_TARGETS[0]
    lo, hi = row.get("lo"), row.get("hi")
    return in_top, (None if lo is None or hi is None else lo <= final_rank <= hi)


def results(conn: sqlite3.Connection, ouid: str, today: date | None = None) -> list[dict]:
    """화면용 — 시즌마다 {season_start, state, rank, pending, days: 그 시즌 예측 줄들}. 결과 없는 시즌은 state None.
    pending = not_found 인데 다시 묻는 기간이 안 끝났다(아직 굳지 않음 — "확인 중")."""
    today = today or date.today()
    st = store.trade_state(conn)
    groups: dict[str, list[dict]] = {}
    for r in store.predictions(conn, ouid):
        groups.setdefault(r.get("season_start") or "", []).append(r)
    out = []
    for start, items in sorted(groups.items(), reverse=True):
        rec = _load(st.get(_key(ouid, start))) or {}
        out.append({"season_start": start, "state": rec.get("state"), "rank": rec.get("rank"),
                    "pending": rec.get("state") == "not_found" and not _settled(rec, today), "days": items})
    return out
