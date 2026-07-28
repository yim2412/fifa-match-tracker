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

# 같은 사람이 같은 명령을 연타하면 넥슨 호출량(OPENAPI00007)이 금방 찬다.
# 쿨다운은 **방이 아니라 사람 단위**다 — 방 단위로 잡았더니 한 명이 치는
# 동안 그 방의 다른 사람이 아무것도 못 했다. 호출량 자체는 아래 전역
# 상한이 막는다. 쿨다운에 걸린 요청은 답을 안 한다(채팅방에서 "잠시 후"
# 안내가 도배되는 게 더 시끄럽다). 전역 상한은 사람이 알아야 하므로 알린다.
USER_COOLDOWN_SEC = 10
GLOBAL_PER_MINUTE = 20

# 등록·해제는 쿨다운을 걸지 않는다. 오타를 냈을 때 바로 다시 쳐야 하는데
# 10초를 못 기다려 무응답이면 봇이 죽은 줄 안다(실사용 스모크에서 겪었다).
# 조회가 아니라 API 를 많아야 한 번 쓰는 명령이고, 연타는 전역 상한이 막는다.
NO_COOLDOWN = ("등록", "해제", "도움")

RECENT_DEFAULT = 5
RECENT_MAX = 15


class CommandRouter:
    """스레드 안전 — HTTP 서버가 요청마다 스레드를 쓴다."""

    def __init__(self, service: BotService | None = None):
        self._svc = service or BotService()
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str, str], float] = {}  # (방,사람,명령) → 시각
        self._recent_calls: list[float] = []                # 전역 분당 상한용
        self._last_nick: dict[tuple[str, str], str] = {}    # (방,사람) → 최근 조회

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

        if cmd not in NO_COOLDOWN and not self._allow(room, sender, cmd):
            return None
        over = self._check_global()
        if over:
            return over

        try:
            return _HANDLERS[cmd](self, room, sender, args)
        except _NeedNickname as e:
            return str(e)
        except NexonAPIError as e:
            return f"조회 실패: {e.message}"   # 이미 사람이 읽는 한글 메시지
        except ranker.RankerError as e:
            return f"랭킹 조회 실패: {e}"
        except Exception as e:
            return f"예기치 못한 오류: {e}"

    # ── 쿨다운 ────────────────────────────────────────────────────────
    def _allow(self, room: str, sender: str, cmd: str) -> bool:
        now = time.time()
        key = (room, sender, cmd)
        with self._lock:
            last = self._last.get(key, 0.0)
            if now - last < USER_COOLDOWN_SEC:
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
    def _nickname(self, room: str, sender: str, args: list[str]) -> str | None:
        """구단주명을 정하는 순서: 직접 쓴 것 → 등록한 계정 → 방금 조회한 계정.

        마지막 항목도 (방, 사람) 단위다. 방 단위로 두면 옆 사람이 조회한 순간
        내 "!선수" 가 남의 계정을 보게 된다.
        """
        if args:
            return args[0]
        saved = self._svc.registered(room, sender)
        if saved:
            return saved
        with self._lock:
            return self._last_nick.get((room, sender))

    def _lookup(self, room: str, sender: str, args: list[str],
                limit: int | None = None):
        nick = self._nickname(room, sender, args)
        if not nick:
            raise _NeedNickname()
        result = self._svc.lookup(nick, limit=limit)
        with self._lock:
            self._last_nick[(room, sender)] = result.nickname
        return result

    # ── 명령 구현 ─────────────────────────────────────────────────────
    def _cmd_register(self, room: str, sender: str, args: list[str]) -> str:
        if not args:
            saved = self._svc.registered(room, sender)
            if saved:
                return f"등록된 계정: {saved}\n바꾸려면 !등록 <구단주명>"
            return "등록할 구단주명을 적어주세요. 예) !등록 홍길동"
        nickname = args[0]
        self._svc.register(room, sender, nickname)   # 없는 계정이면 여기서 막힌다
        return f"'{nickname}' 계정을 등록했습니다.\n이제 !전적 처럼 짧게 치면 됩니다."

    def _cmd_unregister(self, room: str, sender: str, args: list[str]) -> str:
        if self._svc.unregister(room, sender):
            return "등록을 해제했습니다."
        return "등록된 계정이 없습니다."

    def _cmd_record(self, room: str, sender: str, args: list[str]) -> str:
        lookup = self._lookup(room, sender, args)
        return fmt.summary(lookup, self._svc.division_names())

    def _cmd_today(self, room: str, sender: str, args: list[str]) -> str:
        return fmt.today(self._lookup(room, sender, args))

    def _cmd_recent(self, room: str, sender: str, args: list[str]) -> str:
        n = RECENT_DEFAULT
        if args and args[-1].isdigit():       # "!최근 닉 10" · "!최근 10"
            n = max(1, min(RECENT_MAX, int(args[-1])))
            args = args[:-1]
        return fmt.recent(self._lookup(room, sender, args), n)

    def _cmd_opponent(self, room: str, sender: str, args: list[str]) -> str:
        # 인자 두 개면 (내 구단주명, 상대 구단주명). 하나면 내 구단주명이다 —
        # 등록 여부에 따라 뜻이 달라지면 헷갈리므로 규칙을 고정한다.
        target = args[1] if len(args) > 1 else ""
        lookup = self._lookup(room, sender, args)
        return fmt.opponents(lookup, target=target)

    def _cmd_players(self, room: str, sender: str, args: list[str]) -> str:
        lookup = self._lookup(room, sender, args)
        return fmt.players(lookup, self._svc.player_names(),
                           self._svc.position_names())

    def _cmd_ranking(self, room: str, sender: str, args: list[str]) -> str:
        nick = self._nickname(room, sender, args)
        if not nick:
            raise _NeedNickname()
        info = ranker.fetch_manager_rank(nick)
        with self._lock:
            self._last_nick[(room, sender)] = info.nickname or nick
        return fmt.ranking(info)


class _NeedNickname(Exception):
    """구단주명을 못 정한 경우 — handle 의 except 가 안내 문구로 바꾼다."""

    def __str__(self) -> str:
        return ("구단주명을 적거나 !등록 을 먼저 해주세요."
                " 예) !등록 홍길동 · !전적 홍길동")


_HANDLERS = {
    "등록": CommandRouter._cmd_register,
    "해제": CommandRouter._cmd_unregister,
    "전적": CommandRouter._cmd_record,
    "오늘": CommandRouter._cmd_today,
    "최근": CommandRouter._cmd_recent,
    "상대": CommandRouter._cmd_opponent,
    "선수": CommandRouter._cmd_players,
    "랭킹": CommandRouter._cmd_ranking,
    "도움": None,  # handle 에서 먼저 처리 — 쿨다운·API 없이 바로 답한다
}
