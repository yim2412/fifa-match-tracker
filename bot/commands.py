"""명령어 파싱·쿨다운·에러 처리 — 채팅 한 줄을 답장 한 줄로 바꾼다.

어댑터(메신저봇R / PC 자동화)는 여기 로직을 하나도 모른다. 방 이름과
보낸 사람, 원문만 넘기면 된다.
"""
from __future__ import annotations

import threading
import time

import ranker
from nexon_api import NexonAPIError

from . import formatters as fmt
from .service import BotService

PREFIX = "!"

# 같은 방에서 같은 명령을 연타하면 넥슨 호출량(OPENAPI00007)이 금방 찬다.
# 쿨다운에 걸린 요청은 답을 안 한다 — 채팅방에서 "잠시 후" 안내가 도배되는
# 게 더 시끄럽기 때문. 전역 상한은 사람이 알아야 하므로 안내한다.
ROOM_COOLDOWN_SEC = 10
GLOBAL_PER_MINUTE = 20

RECENT_DEFAULT = 5
RECENT_MAX = 15


class CommandRouter:
    """스레드 안전 — HTTP 서버가 요청마다 스레드를 쓴다."""

    def __init__(self, service: BotService | None = None):
        self._svc = service or BotService()
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], float] = {}   # (방, 명령) → 마지막 시각
        self._recent_calls: list[float] = []             # 전역 분당 상한용
        self._last_nick: dict[str, str] = {}             # 방 → 마지막 조회 닉네임

    # ── 진입점 ────────────────────────────────────────────────────────
    def handle(self, room: str, sender: str, text: str) -> str | None:
        """답장할 텍스트. 봇이 반응하지 않을 메시지면 None."""
        text = (text or "").strip()
        if not text.startswith(PREFIX):
            return None

        parts = text[len(PREFIX):].split()
        if not parts:
            return None
        cmd, args = parts[0], parts[1:]
        if cmd not in _HANDLERS:
            return None  # 다른 봇의 명령일 수 있으니 조용히 넘긴다

        if cmd == "도움":
            return fmt.HELP

        if not self._allow(room, cmd):
            return None
        over = self._check_global()
        if over:
            return over

        try:
            return _HANDLERS[cmd](self, room, args)
        except _NeedNickname as e:
            return str(e)
        except NexonAPIError as e:
            return f"조회 실패: {e.message}"   # 이미 사람이 읽는 한글 메시지
        except ranker.RankerError as e:
            return f"랭킹 조회 실패: {e}"
        except Exception as e:
            return f"예기치 못한 오류: {e}"

    # ── 쿨다운 ────────────────────────────────────────────────────────
    def _allow(self, room: str, cmd: str) -> bool:
        now = time.time()
        key = (room, cmd)
        with self._lock:
            last = self._last.get(key, 0.0)
            if now - last < ROOM_COOLDOWN_SEC:
                return False
            self._last[key] = now
        return True

    def _check_global(self) -> str | None:
        now = time.time()
        with self._lock:
            self._recent_calls = [t for t in self._recent_calls if now - t < 60]
            if len(self._recent_calls) >= GLOBAL_PER_MINUTE:
                return "요청이 몰려 잠시 쉬는 중입니다. 1분 뒤에 다시 시도해 주세요."
            self._recent_calls.append(now)
        return None

    # ── 닉네임 ────────────────────────────────────────────────────────
    def _nickname(self, room: str, args: list[str]) -> str | None:
        """인자가 없으면 그 방에서 마지막으로 조회한 계정을 쓴다."""
        if args:
            return args[0]
        with self._lock:
            return self._last_nick.get(room)

    def _lookup(self, room: str, args: list[str], limit: int | None = None):
        nick = self._nickname(room, args)
        if not nick:
            raise _NeedNickname()
        result = self._svc.lookup(nick, limit=limit)
        with self._lock:
            self._last_nick[room] = result.nickname
        return result

    # ── 명령 구현 ─────────────────────────────────────────────────────
    def _cmd_record(self, room: str, args: list[str]) -> str:
        lookup = self._lookup(room, args)
        return fmt.summary(lookup, self._svc.division_names())

    def _cmd_recent(self, room: str, args: list[str]) -> str:
        n = RECENT_DEFAULT
        if args and args[-1].isdigit():       # "!최근 닉 10" · "!최근 10"
            n = max(1, min(RECENT_MAX, int(args[-1])))
            args = args[:-1]
        lookup = self._lookup(room, args)
        return fmt.recent(lookup, n)

    def _cmd_opponent(self, room: str, args: list[str]) -> str:
        target = args[1] if len(args) > 1 else ""
        lookup = self._lookup(room, args)
        return fmt.opponents(lookup, target=target)

    def _cmd_players(self, room: str, args: list[str]) -> str:
        lookup = self._lookup(room, args)
        return fmt.players(lookup, self._svc.player_names(),
                           self._svc.position_names())

    def _cmd_ranking(self, room: str, args: list[str]) -> str:
        nick = self._nickname(room, args)
        if not nick:
            raise _NeedNickname()
        info = ranker.fetch_manager_rank(nick)
        with self._lock:
            self._last_nick[room] = info.nickname or nick
        return fmt.ranking(info)


class _NeedNickname(Exception):
    """닉네임을 못 정한 경우 — handle 의 except 가 안내 문구로 바꾼다."""

    def __str__(self) -> str:
        return "구단주명을 함께 적어주세요. 예) !전적 홍길동"


_HANDLERS = {
    "전적": CommandRouter._cmd_record,
    "최근": CommandRouter._cmd_recent,
    "상대": CommandRouter._cmd_opponent,
    "선수": CommandRouter._cmd_players,
    "랭킹": CommandRouter._cmd_ranking,
    "도움": None,  # handle 에서 먼저 처리 — 쿨다운·API 없이 바로 답한다
}
