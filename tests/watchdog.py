"""테스트 하나마다 시간 한도 — 넘기면 그 테스트 이름과 모든 스레드의 호출 스택을 찍고 끝낸다(종료 코드 3).

2026-10-06 16단계 회귀에서 화면 스모크가 실패 대신 **18분 동안 CPU 를 쓰며 멈췄고**, 사람이 상태줄을 보고서야 알았다
(같은 날 손 변이 #19 도 테스트가 멈춰 변이 스크립트가 10분 시간 초과로 죽었다). 멈춤은 빨간 줄이 안 나와 어디서 멈췄는지도
안 보인다 — 그래서 한도를 넘기면 실패로 바꾸고 멈춘 자리를 남긴다.

    with watchdog.limit(t.__name__):
        t()

한도는 TEST_LIMIT_S(환경 변수)로 바꾼다. 기본 300초 — 지금 가장 긴 테스트(규칙 검사의 SQL 모으기)도 그 안이다.

**슬롯 예외도 여기서 잡는다(73단계 F9 · `slot_errors`).** PySide6 는 슬롯·타이머·`QThread.run` 안 예외를
`sys.excepthook` 으로 넘기고 **기본 훅이면 찍고 계속 돈다**(PyQt6 는 끝냈다). 예외가 테스트 함수까지 안 올라오니,
클릭으로 연 슬롯 안의 `ModalCalled` 나 단언 실패가 "통과" 로 읽힌다. 그래서 테스트 하나가 도는 동안 훅을
"모아 두기" 로 바꾸고, 끝에 모인 것이 있으면 실패로 바꾼다. 훅을 스스로 바꿔 끼우는 테스트(crashlog)는 안에서
저장·복원하면 된다 — 이 훅이 그 "저장된 것" 이 된다.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager

# 테스트 공통 머리(109 J8) — config 를 import 하는 테스트는 config 보다 먼저 이 모듈을 거친다(test_rules 가 대조).
# Windows 자격 증명 관리자는 사용자마다 하나라 임시 폴더·가짜 키를 쓰는 테스트가 개발 PC 의 진짜 키를 덮거나 읽는다.
# 그래서 이 프로세스(와 물려받는 자식)는 자격 증명을 읽지도 쓰지도 않는다. 자격 증명 자체를 재는 테스트는
# 가짜 함수를 바꿔 끼우고 config.NO_CRED_VAR 를 그 안에서만 풀며 진짜 관리자는 부르지 않는다.
os.environ["FIFA_NO_CRED"] = "1"

DEFAULT_LIMIT_S = 300
EXIT_HUNG = 3
_BACKSTOP_S = 15   # 파이썬 스레드조차 못 도는 멈춤(C 코드가 GIL 을 쥔 채) — faulthandler 의 C 스레드가 이만큼 뒤에 끊는다


def limit_s() -> float:
    try:
        return float(os.environ.get("TEST_LIMIT_S", DEFAULT_LIMIT_S))
    except ValueError:
        return DEFAULT_LIMIT_S


class SlotError(AssertionError):
    """테스트 도중 sys.excepthook·threading.excepthook 으로 간 예외 — 슬롯 안에서 나 테스트까지 안 올라온 것."""


@contextmanager
def slot_errors():
    """안에서 훅으로 간 예외를 모았다가, 블록이 정상으로 끝나면 `SlotError` 로 올린다(블록이 이미 실패 중이면 그쪽을 둔다)."""
    caught: list[str] = []
    lock = threading.Lock()

    def on_exc(exc_type, exc, tb):
        with lock:
            caught.append("".join(traceback.format_exception_only(exc_type, exc)).strip())

    def on_thread(args):
        if args.exc_type is SystemExit:
            return
        on_exc(args.exc_type, args.exc_value, args.exc_traceback)

    saved = (sys.excepthook, threading.excepthook)
    sys.excepthook, threading.excepthook = on_exc, on_thread
    try:
        yield caught
    finally:
        sys.excepthook, threading.excepthook = saved
    if caught:
        raise SlotError(f"훅으로 간 예외 {len(caught)}건(슬롯·스레드 안 — 테스트까지 안 올라왔다): " + " | ".join(caught[:3]))


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
        with slot_errors():
            yield
    finally:
        timer.cancel()
        faulthandler.cancel_dump_traceback_later()
