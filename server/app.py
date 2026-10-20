"""중앙 서버 본체 — 넥슨 오픈API 경로를 같은 모양으로 받아 대신 묻고 저장한다(docs/CENTRAL_SERVER.md 11절).

파이썬 표준 http.server 로 만든다(설계는 FastAPI 였다 — 이 PC 에 없고, 프로세스 하나라 경기 id 잠금이 그대로 맞고,
테스트에 설치할 것이 없다. Oracle 에서 성능이 모자라면 그때 바꾼다 · 2026-10-20).

- 허용 목록(settings.PARAMS · 메타 이름) 밖은 404 · user/trade 는 일부러 없다
- 넥슨이 요청에 준 오류는 그대로, 서버 사정은 SERVER_BUSY / SERVER_UNAVAILABLE(503)
- 같은 키를 동시에 물으면 넥슨에는 한 번만(single flight)
- 접근 로그 없음 — 쿼리에 닉네임이 있다. 자체 로그는 경로·상태·넥슨 요청 수만
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

import nexon_api

from . import settings
from .cache import Cache, dumps
from .upstream import BudgetExhausted, Upstream, api_url

META_PREFIX = "/static/fconline/meta/"
_BAD = object()


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


def _error(status: int, code: str, headers: dict | None = None) -> Response:
    msg = nexon_api.ERROR_MESSAGES.get(code, code)
    return Response(status, dumps({"error": {"name": code, "message": msg}}), headers or {})


def _busy() -> Response:
    return _error(503, nexon_api.SERVER_BUSY_CODE, {"Retry-After": str(settings.RETRY_AFTER_S)})


def _unavailable() -> Response:
    return _error(503, nexon_api.SERVER_UNAVAILABLE_CODE)


def next_meta_boundary(now: float) -> float:
    """다음 금요일 0시(KST) — 앱의 메타 주간 갱신과 같은 규칙(nexon_api.META_REFRESH_WEEKDAY)."""
    t = datetime.fromtimestamp(now, settings.KST)
    days = (nexon_api.META_REFRESH_WEEKDAY - t.weekday()) % 7
    b = datetime.combine((t + timedelta(days=days)).date(), datetime.min.time(), tzinfo=settings.KST)
    if b.timestamp() <= now:
        b += timedelta(days=7)
    return b.timestamp()


class _Flight:
    """같은 키의 동시 요청은 첫 요청 결과를 같이 받는다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: dict[str, tuple[threading.Event, list]] = {}

    def do(self, key: str, fn: Callable[[], Response]) -> Response:
        with self._lock:
            hit = self._calls.get(key)
            if hit is None:
                ev, box = threading.Event(), [None, None]
                self._calls[key] = (ev, box)
        if hit is not None:
            ev, box = hit
            ev.wait()
            if box[1] is not None:
                raise box[1]
            return box[0]
        try:
            box[0] = fn()
            return box[0]
        except BaseException as e:
            box[1] = e
            raise
        finally:
            with self._lock:
                self._calls.pop(key, None)
            ev.set()


class Service:
    def __init__(self, cache: Cache, upstream: Upstream, *, now: Callable[[], float] = time.time,
                 log_path: Path | None = None):
        self.cache = cache
        self.upstream = upstream
        self.now = now
        self._flight = _Flight()
        self._log_path = log_path
        self._log_lock = threading.Lock()
        self.requests = 0
        self.hits = 0

    # ── 한 요청 ───────────────────────────────────────────────────
    def handle(self, path: str, query: list[tuple[str, str]], ip: str) -> Response:
        started = time.monotonic()
        sent_before = self.upstream.thread_sent()
        res = self._handle(path, query, ip)
        self._log(path, res.status, self.upstream.thread_sent() - sent_before, time.monotonic() - started)
        return res

    def _handle(self, path: str, query: list[tuple[str, str]], ip: str) -> Response:
        if path == "/health":
            return Response(200, dumps({"ok": True}))
        self.requests += 1
        if path.startswith(META_PREFIX):
            name = path[len(META_PREFIX):]
            if not name.endswith(".json") or name[:-5] not in settings.META_NAMES:
                return _error(404, "NOT_FOUND")
            return self._cached(f"meta:{name}", api_url(path), {}, ip, meta=True)
        rules = settings.PARAMS.get(path)
        if rules is None:
            return _error(404, "NOT_FOUND")
        params: dict[str, str] = {}
        for k, v in query:
            if k in rules and k not in params:  # 허용 밖 인자는 버린다 · 같은 이름이 둘이면 앞의 것
                params[k] = v
        if any(k not in params for k in settings.REQUIRED[path]) or not all(rules[k](v) for k, v in params.items()):
            return _error(400, "OPENAPI00004")
        key = f"{path}?{urlencode(sorted(params.items()))}"
        if path == nexon_api.EP_MATCH_DETAIL:
            raw = self.cache.get_detail(params["matchid"])
            if raw is not None:
                self.hits += 1
                return Response(200, raw)
        return self._cached(key, api_url(path), params, ip, path=path)

    def _cached(self, key: str, url: str, params: dict, ip: str, *, meta: bool = False, path: str = "") -> Response:
        hit = self.cache.get_short(key, self.now())
        if hit is not None:
            self.hits += 1
            return Response(hit[0], hit[1])
        return self._flight.do(key, lambda: self._fetch(key, url, params, ip, meta, path))

    def _fetch(self, key: str, url: str, params: dict, ip: str, meta: bool, path: str) -> Response:
        hit = self.cache.get_short(key, self.now())  # 줄 서는 사이 앞 요청이 채웠을 수 있다
        if hit is not None:
            self.hits += 1
            return Response(hit[0], hit[1])
        day = datetime.fromtimestamp(self.now(), settings.KST).date().isoformat()
        allow = lambda: self.cache.take_budget(  # noqa: E731
            day, [("all", settings.DAILY_BUDGET), (f"ip:{ip}", settings.IP_DAILY)])
        try:
            reply = self.upstream.get(url, params, allow)
        except BudgetExhausted:
            return _busy()
        except requests.RequestException:
            return _unavailable()
        status, raw = reply.status, reply.body
        try:
            data = json.loads(raw)
        except ValueError:
            data = _BAD
        now = self.now()
        if status == 200 and data is not _BAD:
            if path == nexon_api.EP_MATCH_DETAIL:
                mid = params["matchid"]
                if not isinstance(data, dict) or data.get("matchId") != mid:
                    return _unavailable()  # 빈·다른 본문 — 영구 저장도 중계도 안 한다
                self.cache.put_detail(mid, raw, data)  # 지운 ouid 가 끼었으면 저장만 안 한다
            else:
                expires = next_meta_boundary(now) if meta else now + settings.TTL_S.get(path, 0)
                self.cache.put_short(key, 200, raw, expires)
            return Response(200, raw)
        err = data.get("error") if isinstance(data, dict) else None
        code = err.get("name", "") if isinstance(err, dict) else ""
        if code == nexon_api.KEY_INVALID_CODE:
            return _unavailable()  # 서버 키 거절 — 넘기면 전원에게 "키가 틀렸다" 창이 뜬다
        if status == 429 or code == nexon_api.QUOTA_CODE:
            return _busy()
        if code in settings.NEG_CODES:
            ttl = settings.NEG_TTL_DETAIL_S if path == nexon_api.EP_MATCH_DETAIL else settings.NEG_TTL_S
            self.cache.put_short(key, status, raw, now + ttl)
            return Response(status, raw)
        if data is _BAD or (status >= 500 and not code.startswith("OPENAPI")):
            return _unavailable()
        return Response(status, raw)  # 넥슨이 요청에 준 그 밖의 오류 — 그대로(저장 안 함)

    def _log(self, path: str, status: int, sent: int, secs: float) -> None:
        if self._log_path is None:
            return
        line = f"{datetime.now(settings.KST).isoformat(timespec='seconds')}\t{path}\t{status}\tnexon={sent}\t{secs * 1000:.0f}ms\n"
        with self._log_lock:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass  # 로그 한 줄 때문에 응답이 죽으면 안 된다


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # 앱의 연결 풀이 연결을 다시 쓰게

        def do_GET(self):  # noqa: N802
            parts = urlsplit(self.path)
            try:
                res = service.handle(parts.path, parse_qsl(parts.query, keep_blank_values=True), self._ip())
            except Exception:
                res = _unavailable()
            self.send_response(res.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res.body)))
            for k, v in res.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(res.body)

        def _ip(self) -> str:
            # ⓐ 는 접속 상대 주소만 — 터널·프록시 뒤(ⓑ)에서만 CF-Connecting-IP 를 믿는다(X-Forwarded-For 는 위조 가능)
            return self.client_address[0]

        def log_message(self, format, *args):  # noqa: A002 — 기본 접근 로그는 쿼리의 닉네임을 그대로 찍는다
            pass

    return Handler


def make_server(service: Service, host: str = settings.HOST, port: int = settings.PORT) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(service))
    srv.daemon_threads = True
    return srv
