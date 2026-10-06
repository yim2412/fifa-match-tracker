"""테스트 하나마다 시간 한도 — 넘기면 그 테스트 이름과 모든 스레드의 호출 스택을 찍고 끝낸다(종료 코드 3).

2026-10-06 16단계 회귀에서 화면 스모크가 실패 대신 **18분 동안 CPU 를 쓰며 멈췄고**, 사람이 상태줄을 보고서야 알았다
(같은 날 손 변이 #19 도 테스트가 멈춰 변이 스크립트가 10분 시간 초과로 죽었다). 멈춤은 빨간 줄이 안 나와 어디서 멈췄는지도
안 보인다 — 그래서 한도를 넘기면 실패로 바꾸고 멈춘 자리를 남긴다.

    with watchdog.limit(t.__name__):
        t()

한도는 TEST_LIMIT_S(환경 변수)로 바꾼다. 기본 300초 — 지금 가장 긴 테스트(규칙 검사의 SQL 모으기)도 그 안이다.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
from contextlib import contextmanager

DEFAULT_LIMIT_S = 300
EXIT_HUNG = 3
_BACKSTOP_S = 15   # 파이썬 스레드조차 못 도는 멈춤(C 코드가 GIL 을 쥔 채) — faulthandler 의 C 스레드가 이만큼 뒤에 끊는다


def limit_s() -> float:
    try:
        return float(os.environ.get("TEST_LIMIT_S", DEFAULT_LIMIT_S))
    except ValueError:
        return DEFAULT_LIMIT_S


@contextmanager
def limit(name: str, seconds: float | None = None):
    secs = limit_s() if seconds is None else seconds
    started = time.monotonic()

    def fire():
        sys.stderr.write(f"\n[멈춤] {name} — {secs:.0f}초를 넘겼다(실패로 끝낸다). 그때의 호출 스택:\n")
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stdout.write(f"[멈춤] {name}: {time.monotonic() - started:.0f}초 — 위 호출 스택(stderr)\n")
        sys.stdout.flush()
        os._exit(EXIT_HUNG)

    timer = threading.Timer(secs, fire)
    timer.daemon = True
    timer.start()
    faulthandler.dump_traceback_later(secs + _BACKSTOP_S, exit=True)
    try:
        yield
    finally:
        timer.cancel()
        faulthandler.cancel_dump_traceback_later()
