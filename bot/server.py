"""봇 서버 — 어댑터가 바라보는 단 하나의 엔드포인트.

Flask 를 쓰지 않는다. 이 프로젝트의 의존성은 지금 3개(PyQt6·requests·
python-dotenv)뿐이고, 받는 요청이 초당 몇 건도 안 되는 개인용 봇에
웹 프레임워크를 더할 이유가 없다 — stdlib 로 충분하다.

    POST /message   {"room": "...", "sender": "...", "text": "!전적 홍길동"}
                 →  {"reply": "..."}   또는 {"reply": null} (봇이 반응 안 함)
    GET  /health →  {"ok": true}

인증은 두지 않기로 했다(2026-07-29 합의). 기본 바인딩이 0.0.0.0 이라
같은 공유기의 다른 기기도 부를 수 있다는 뜻이다 — 신뢰하는 망에서만 켤 것.

실행:  python -m bot.server            (기본 0.0.0.0:8765)
       python -m bot.server --port 9000 --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

from .commands import CommandRouter

DEFAULT_HOST = "0.0.0.0"   # 에뮬레이터(10.0.2.2)·폰(PC 내부 IP) 양쪽에서 붙는다
DEFAULT_PORT = 8765
MAX_BODY = 64 * 1024       # 채팅 한 줄에 이보다 클 이유가 없다


class _Handler(BaseHTTPRequestHandler):
    router: CommandRouter  # 서버가 주입한다 (모든 요청 스레드가 공유)

    protocol_version = "HTTP/1.1"  # 어댑터가 커넥션을 재사용할 수 있게

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/health", ""):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/message":
            self._json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"error": "bad body length"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._json(400, {"error": "invalid json"})
            return

        room = str(payload.get("room") or "")
        sender = str(payload.get("sender") or "")
        text = str(payload.get("text") or "")

        try:
            reply = self.router.handle(room, sender, text)
        except Exception as e:
            # 라우터가 이미 대부분을 잡지만, 여기서 죽으면 어댑터가 끊긴다.
            print(f"[bot] 처리 실패: {e}")
            reply = "봇 내부 오류가 발생했습니다."

        if reply:
            print(f"[bot] {room or '-'} / {sender or '-'}: {text}")
        self._json(200, {"reply": reply})

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        pass  # 요청 한 줄 로그는 우리가 직접 찍는다(위 do_POST)


def build_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 router: CommandRouter | None = None) -> ThreadingHTTPServer:
    handler = type("BotHandler", (_Handler,),
                   {"router": router or CommandRouter()})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    ap = argparse.ArgumentParser(description="피파 전적관리 카카오톡 봇 서버")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    if not config.API_KEY:
        print("[bot] NEXON_API_KEY 가 없습니다."
              f" {config.DATA_DIR / '.env'} 를 확인하세요.")
        return

    server = build_server(args.host, args.port)
    print(f"[bot] http://{args.host}:{args.port} 대기 중 (Ctrl+C 로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bot] 종료합니다.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
