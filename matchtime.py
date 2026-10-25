"""넥슨 오픈API 시각 읽기 — 한 곳(63단계).

넥슨 오픈API 가 주는 시각(경기 `matchDate` · 슈챔 달성 `achievementDate` · 거래 `tradeDate` · 랭커 기록 `createDate`)은
**UTC** 다(2026-10-25 실측 — `config.MATCH_DATE_OFFSET_H`). 예전엔 읽는 곳마다 따로 파싱해 KST 로 읽었고, 모든 시각이
9시간 일렀다(새벽 경기가 전날로 · 달력·시즌 경계·rank.db 스냅숏 짝까지). 이제 원문은 여기서만 읽는다
(`tests/test_rules` 가 밖의 직접 파싱을 잡는다). 넥슨 웹(데이터센터)·rank.db 시각은 이미 KST 라 여기 넣지 않는다.

| 함수 | 돌려주는 것 |
|---|---|
| `parse(raw)` | KST 벽시계(naive) — 앱 전체가 `datetime.now()`·rank.db 스냅숏(KST naive)과 비교하니 aware 로 안 바꾼다 |
| `day(raw)` | KST 날짜 — `raw[:10]` 을 대신(UTC 15시 뒤 경기는 다음 날) |
| `label(raw)` | 화면 글자 `YYYY-MM-DD HH:MM`(`MatchSummary.date_text` 와 같은 꼴) · 깨졌으면 "-" |
| `raw_bound(kst)` | KST 경계 → 원문과 같은 꼴의 UTC 문자열 — SQL 의 `match_date >= ?` 에 넣는 값. 열을 SQL 안에서 고치면 색인을 못 쓴다 |
| `epoch(raw)` | 진짜 epoch 초 |

저장 원본(`matches.match_date` 열 · payload)은 안 바꾼다 — 옛 버전이 같은 DB 를 열어도 그대로 읽고, 지난 경기도 읽는 쪽에서 같이 맞는다.
**날짜만(`YYYY-MM-DD`) 원문은 None** — 시각 없이는 KST 날을 모른다(실데이터 3만 경기엔 없다).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import config

RAW_FORMAT = "%Y-%m-%dT%H:%M:%S"   # 넥슨 원문 꼴 — raw_bound 는 소수초 없이 이 꼴로만 낸다(같은 초에서 문자열 비교가 어긋나지 않게)


def _offset() -> timedelta:
    return timedelta(hours=config.MATCH_DATE_OFFSET_H)


def _utc(raw) -> datetime | None:
    s = str(raw or "").strip()
    if len(s) <= 10:                    # 비었거나 날짜만
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def parse(raw) -> datetime | None:
    t = _utc(raw)
    return None if t is None else t + _offset()


def day(raw) -> date | None:
    t = parse(raw)
    return None if t is None else t.date()


def label(raw) -> str:
    t = parse(raw)
    return t.strftime("%Y-%m-%d %H:%M") if t else "-"


def raw_bound(kst) -> str:
    """KST 경계(datetime · date · `YYYY-MM-DD` · ISO 문자열 — 소수초 있어도) → UTC 원문 꼴 문자열."""
    if isinstance(kst, datetime):
        t = kst
    elif isinstance(kst, date):
        t = datetime.combine(kst, datetime.min.time())
    else:
        t = datetime.fromisoformat(str(kst).strip())
    if t.tzinfo is not None:            # 이미 aware 면 그 시각 그대로 UTC 로
        return t.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0).strftime(RAW_FORMAT)
    return (t.replace(microsecond=0) - _offset()).strftime(RAW_FORMAT)


def epoch(raw) -> int | None:
    t = _utc(raw)
    return None if t is None else int(t.replace(tzinfo=timezone.utc).timestamp())
