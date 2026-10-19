"""넥슨 요청 한 곳 — 서비스 키는 이 세션에만. 클라이언트 헤더는 넘기지 않는다(쿼리 인자도 허용 목록을 거친 것만 온다)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

import nexon_api

from . import settings


@dataclass
class Reply:
    status: int
    body: bytes
    sent: int  # 실제로 넥슨에 보낸 요청 수(재시도 포함)


class BudgetExhausted(Exception):
    """하루 몫(전체 또는 IP)에 닿았다 — 넥슨에 보내지 않았다."""


class Upstream:
    def __init__(self, key: str, *, session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        if not key:
            raise ValueError(f"{settings.KEY_VAR} 가 비어 있다")
        self._session = session or requests.Session()
        self._session.headers.update({"x-nxopen-api-key": key})
        self._session.mount("https://", HTTPAdapter(pool_maxsize=settings.UPSTREAM_CONCURRENCY + 4))
        self._slots = threading.BoundedSemaphore(settings.UPSTREAM_CONCURRENCY)
        self._sleep = sleep
        self.sent = 0  # 켠 뒤 넥슨에 보낸 수 — 상태줄·실측용
        self._count_lock = threading.Lock()

    def get(self, url: str, params: dict, allow: Callable[[], bool]) -> Reply:
        """allow() 가 False 면 그 시도를 보내지 않는다(하루 몫). 429·500·503 은 settings.UPSTREAM_ATTEMPTS 까지 다시.
        네트워크 실패는 마지막 시도 뒤 requests 예외를 그대로 올린다."""
        last = settings.UPSTREAM_ATTEMPTS - 1
        sent = 0
        last_res: requests.Response | None = None
        last_exc: requests.RequestException | None = None
        for attempt in range(settings.UPSTREAM_ATTEMPTS):
            if not allow():
                if last_res is not None:   # 다시 해 볼 몫이 없다 — 앞 시도의 응답을 그대로
                    return Reply(last_res.status_code, last_res.content, sent)
                if last_exc is not None:
                    raise last_exc
                raise BudgetExhausted()
            with self._slots:
                try:
                    res = self._session.get(url, params=params, timeout=settings.UPSTREAM_TIMEOUT_S)
                except requests.RequestException as e:
                    sent += 1
                    self._count(1)
                    last_exc = e
                    if attempt == last:
                        raise
                    self._sleep(1.0 * (attempt + 1))
                    continue
            sent += 1
            self._count(1)
            last_res = res
            if res.status_code in (429, 500, 503) and attempt < last:
                self._sleep(1.5 * (attempt + 1))
                continue
            return Reply(res.status_code, res.content, sent)
        raise AssertionError("unreachable")

    def _count(self, n: int) -> None:
        with self._count_lock:
            self.sent += n


def api_url(path: str) -> str:
    return f"{nexon_api.BASE_URL}{path}"
