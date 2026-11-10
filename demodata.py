"""둘러보기(96 O2) 자료 — 지어낸 계정 하나의 감독모드 경기 약 300판과, 그 자료만 돌려주는 가짜 API.

데모 프로세스(`--demo` · `demo.py` 가 데이터 폴더를 임시 폴더로 바꾼 뒤)에서만 쓴다. 넥슨에 요청하지 않는다:
`DemoAPI` 는 `FCOnlineAPI` 를 물려받되 `_get`·`get_meta` 를 메모리 자료로 바꾸고, 세션도 요청하면 예외를 내는 것으로 갈아 끼운다.

| 무엇 | 어떻게 |
|---|---|
| 경기 | 고정 seed(`config.DEMO_SEED`)로 만든 상세 JSON — `store.save_matches`·`models.parse_match` 를 그대로 거친다 |
| 시각 | 넥슨 원문처럼 **UTC** 문자열(`matchtime` 이 KST 로 읽는다) — 오늘에서 거꾸로 `DEMO_DAYS` 일 |
| 이름 | 구단주·선수 이름 전부 지어낸 것(실제 닉네임·선수 이름 없음) · 시즌 번호도 넥슨에 없는 번호 |
| 메타 | 등급·포지션·경기 종류는 넥슨 공개 메타와 같은 꼴 · 선수 이름표는 지어낸 이름만 |
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import requests

import config
import matchtime
import nexon_api
import seasons as sn
import store

NICK = "둘러보기구단주"
OUID = "d" * 31 + "1"
MATCH_TYPE = config.DEFAULT_MATCH_TYPE

# 넥슨 공개 메타와 같은 꼴(등급 배지 번호 = 목록 순서) — 사람과 무관한 게임 정보라 그대로 둔다
DIVISIONS = [(800, "슈퍼 챔피언스"), (900, "챔피언스"), (1000, "슈퍼 챌린지"), (1100, "챌린저1"), (1200, "챌린저2"),
             (1300, "챌린저3"), (1700, "마스터1"), (1800, "마스터2"), (1900, "마스터3"), (2000, "월드클래스1"),
             (2100, "월드클래스2"), (2200, "월드클래스3"), (2300, "프로1"), (2400, "프로2"), (2500, "프로3"),
             (2600, "세미프로1"), (2700, "세미프로2"), (2800, "세미프로3"), (2900, "유망주1"), (3000, "유망주2"),
             (3100, "유망주3")]
POSITIONS = ["GK", "SW", "RWB", "RB", "RCB", "CB", "LCB", "LB", "LWB", "RDM", "CDM", "LDM", "RM", "RCM", "CM", "LCM",
             "LM", "RAM", "CAM", "LAM", "RF", "CF", "LF", "RW", "RS", "ST", "LS", "LW", "SUB"]
MATCH_TYPES = [(30, "리그 친선"), (40, "클래식 1on1"), (50, "공식경기"), (52, "감독모드"), (60, "공식 친선")]
# 카드 시즌 — 넥슨에 없는 번호(950~)라 실제 카드와 겹치지 않는다
CARD_SEASONS = {951: "가상 A (Demo A)", 952: "가상 B (Demo B)", 953: "가상 C (Demo C)"}
SUB = 28
# 4-2-3-1 — 자리 번호는 위 POSITIONS 순서
FORMATION = (0, 3, 4, 6, 7, 9, 11, 17, 18, 19, 25)

_FAMILY = "강고권김남도류마문박배서석송신오유윤이임장전정조주지진차최한허홍황"
_GIVEN = ["가온", "나래", "다온", "라온", "마루", "바다", "새벽", "아라", "온유", "자람", "찬솔", "하람", "한결",
          "해솔", "도담", "보람", "시온", "여울", "윤슬", "이든", "지안", "태오", "하늘", "누리"]
_NICK_A = ["푸른", "붉은", "조용한", "빠른", "느긋한", "단단한", "새벽", "한낮", "별빛", "바람", "번개", "작은"]
_NICK_B = ["여우", "늑대", "고래", "참새", "호랑이", "수달", "매", "곰", "사자", "두더지", "다람쥐", "펭귄"]


class _NoNetwork(requests.Session):
    """둘러보기 API 의 세션 — 무엇이든 보내려 하면 예외(가짜가 덮지 못한 길이 남았으면 여기서 드러난다)."""

    def request(self, *a, **k):
        raise requests.ConnectionError("둘러보기에서는 넥슨에 요청하지 않습니다")


class DemoAPI(nexon_api.FCOnlineAPI):
    """지어낸 자료만 돌려주는 API — 화면은 진짜 검색·불러오기 길을 그대로 탄다."""

    def __init__(self, data: "DemoData", cache_dir: Path | None = None):
        super().__init__("demo", cache_dir=cache_dir)
        self._session = _NoNetwork()
        self._data = data

    def set_key(self, api_key: str) -> None:
        pass

    def _get_direct(self, path, attempts, params):   # _get 을 덮었으니 안 불린다 — 남은 길이 있어도 요청 0
        raise nexon_api.NexonAPIError("둘러보기에서는 넥슨에 요청하지 않습니다", code=nexon_api.NETWORK_CODE)

    _get_server = _get_direct

    def _get(self, path: str, attempts: int = 3, **params):
        d = self._data
        if path == nexon_api.EP_ID:
            if (params.get("nickname") or "").strip() == NICK:
                return {"ouid": OUID}
            raise nexon_api.NexonAPIError("둘러보기에서는 지어낸 구단주 한 명만 볼 수 있습니다.",
                                          code="OPENAPI00004", status=400)
        if path == nexon_api.EP_USER_BASIC:
            return {"ouid": OUID, "nickname": NICK, "level": 120}
        if path == nexon_api.EP_MAX_DIVISION:
            return [{"matchType": MATCH_TYPE, "division": 800, "achievementDate": d.best_at}]
        if path == nexon_api.EP_USER_MATCH:
            if params.get("ouid") != OUID or int(params.get("matchtype") or 0) != MATCH_TYPE:
                return []
            off, lim = int(params.get("offset") or 0), int(params.get("limit") or 20)
            return d.ids[off:off + lim]
        if path == nexon_api.EP_MATCH_DETAIL:
            det = d.details.get(params.get("matchid"))
            if det is None:
                raise nexon_api.NexonAPIError("경기를 찾지 못했습니다.", code="OPENAPI00004", status=400)
            return json.loads(json.dumps(det))   # 받는 쪽이 고쳐 써도 원본이 안 바뀌게
        if path in (nexon_api.EP_USER_TRADE, nexon_api.EP_RANKER_STATS):
            return []
        raise nexon_api.NexonAPIError("둘러보기에서는 쓰지 않는 기능입니다.", code="OPENAPI00004", status=400)

    def get_match_detail(self, match_id: str, attempts: int = 3) -> dict:
        return self._get(nexon_api.EP_MATCH_DETAIL, matchid=match_id)   # 디스크 캐시를 거치지 않는다(DB 에 이미 있다)

    def get_match_detail_nocache(self, match_id: str, attempts: int = 1) -> dict:
        return self.get_match_detail(match_id)

    def get_meta(self, name: str) -> list[dict]:
        meta = self._data.meta.get(name)
        if meta is None:
            raise nexon_api.NexonAPIError(f"메타데이터({name}) 없음")
        return meta


class DemoData:
    """지어낸 자료 한 벌 — ids 는 최신순(넥슨 목록과 같은 순서)."""

    def __init__(self, details: list[dict], meta: dict, best_at: str, seasons: list):
        self.details = {d["matchId"]: d for d in details}
        self.ids = [d["matchId"] for d in sorted(details, key=lambda d: d["matchDate"], reverse=True)]
        self.meta = meta
        self.best_at = best_at
        self.seasons = seasons


def _name(rng: random.Random) -> str:
    return rng.choice(_FAMILY) + rng.choice(_GIVEN)


def _squad(rng: random.Random, start_pid: int) -> list[tuple[int, int]]:
    """(spId, 강화) 18명 — 앞 11명이 선발(FORMATION 순서), 뒤 7명이 교체."""
    out = []
    for i in range(18):
        season = rng.choice(list(CARD_SEASONS))
        out.append((season * 1_000_000 + start_pid + i, rng.randint(5, 9) if i < 11 else rng.randint(1, 6)))
    return out


def _status(rng, pos: int, goals: int, assists: int, shots: int, on_target: int) -> dict:
    pt = rng.randint(15, 60) if pos else rng.randint(10, 25)
    ps = int(pt * rng.uniform(0.7, 0.93))
    tt = rng.randint(0, 8) if pos else 0
    rating = round(min(10.0, max(4.0, rng.gauss(6.6, 0.7) + 0.6 * goals + 0.4 * assists)), 1)
    return {"shoot": shots, "effectiveShoot": on_target, "assist": assists, "goal": goals,
            "dribble": rng.randint(0, 80), "intercept": rng.randint(0, 4), "defending": rng.randint(0, 5),
            "passTry": pt, "passSuccess": ps, "dribbleTry": rng.randint(0, 6), "dribbleSuccess": rng.randint(0, 3),
            "ballPossesionTry": rng.randint(0, 6), "ballPossesionSuccess": rng.randint(0, 3),
            "aerialTry": rng.randint(0, 5), "aerialSuccess": rng.randint(0, 2), "blockTry": rng.randint(0, 2),
            "block": 0, "tackleTry": tt, "tackle": int(tt * rng.uniform(0.3, 0.8)),
            "yellowCards": 1 if rng.random() < 0.05 else 0, "redCards": 0, "spRating": rating}


def _shots(rng, squad, goals: int) -> list[dict]:
    """슛 기록 — 골 수만큼 result 3, 나머지는 막힘 1 · 빗나감 2. 좌표는 상대 골문 쪽(x 0.7~0.97)."""
    attackers = [sp for sp, _g in squad[7:11]] + [squad[5][0], squad[6][0]]
    grades = dict(squad)
    n = goals + rng.randint(3, 9)
    out = []
    for i in range(n):
        result = 3 if i < goals else (1 if rng.random() < 0.45 else 2)
        period = 0 if rng.random() < 0.48 else 1
        sp = rng.choice(attackers)
        x, y = rng.uniform(0.70, 0.97), rng.uniform(0.25, 0.75)
        assist = result == 3 and rng.random() < 0.7
        asp = rng.choice([a for a in attackers if a != sp]) if assist else 0
        out.append({"goalTime": (period << 24) + rng.randint(30, 2900), "x": x, "y": y,
                    "type": rng.choice((1, 1, 1, 2, 2, 3, 6, 7)), "result": result, "spId": sp,
                    "spGrade": grades[sp], "spLevel": 5, "spIdType": True, "assist": assist,
                    "assistSpId": asp, "assistX": max(0.4, x - rng.uniform(0.05, 0.25)) if assist else 0.0,
                    "assistY": rng.uniform(0.2, 0.8) if assist else 0.0, "hitPost": result == 2 and rng.random() < 0.08,
                    "inPenalty": x > 0.84 and 0.3 < y < 0.7})
    rng.shuffle(out)
    return out


def _side(rng, ouid: str, nick: str, division: int, squad, goals: int, result: str, possession: int) -> dict:
    shots = _shots(rng, squad, goals)
    eff = sum(1 for s in shots if s["result"] in (1, 3))
    per_player: dict[int, list[int]] = {}
    for s in shots:
        row = per_player.setdefault(s["spId"], [0, 0, 0, 0])
        row[2] += 1
        row[3] += s["result"] in (1, 3)
        row[0] += s["result"] == 3
        if s["assist"]:
            per_player.setdefault(s["assistSpId"], [0, 0, 0, 0])[1] += 1
    players = []
    for i, (sp, grade) in enumerate(squad):
        pos = FORMATION[i] if i < 11 else SUB
        g, a, sh, on = per_player.get(sp, [0, 0, 0, 0]) if i < 11 else (0, 0, 0, 0)
        st = _status(rng, pos, g, a, sh, on)
        if i >= 11:
            st = {k: 0 for k in st} | {"spRating": 0.0}
        players.append({"spId": sp, "spPosition": pos, "spGrade": grade, "status": st})
    pass_try = sum(p["status"]["passTry"] for p in players)
    pass_ok = sum(p["status"]["passSuccess"] for p in players)
    in_box = sum(1 for s in shots if s["inPenalty"])
    head = sum(1 for s in shots if s["type"] == 3)
    return {
        "ouid": ouid, "nickname": nick, "division": division,
        "matchDetail": {"seasonId": 202611, "matchResult": result, "matchEndType": 0, "systemPause": 0,
                        "foul": rng.randint(0, 6), "injury": 0, "redCards": 0, "yellowCards": rng.randint(0, 2),
                        "dribble": rng.randint(30, 90), "cornerKick": rng.randint(0, 6), "possession": possession,
                        "offsideCount": rng.randint(0, 3), "averageRating": 0.0,
                        "controller": rng.choice(("keyboard", "keyboard", "gamepad"))},
        "shoot": {"shootTotal": len(shots), "effectiveShootTotal": eff, "shootOutScore": 0, "goalTotal": goals,
                  "goalTotalDisplay": goals, "ownGoal": 0, "shootHeading": head,
                  "goalHeading": sum(1 for s in shots if s["type"] == 3 and s["result"] == 3),
                  "shootFreekick": 0, "goalFreekick": 0, "shootInPenalty": in_box,
                  "goalInPenalty": sum(1 for s in shots if s["inPenalty"] and s["result"] == 3),
                  "shootOutPenalty": len(shots) - in_box,
                  "goalOutPenalty": sum(1 for s in shots if not s["inPenalty"] and s["result"] == 3),
                  "shootPenaltyKick": 0, "goalPenaltyKick": 0},
        "shootDetail": shots,
        "pass": {"passTry": pass_try, "passSuccess": pass_ok, "shortPassTry": pass_try // 2,
                 "shortPassSuccess": pass_ok // 2, "longPassTry": 0, "longPassSuccess": 0,
                 "bouncingLobPassTry": 0, "bouncingLobPassSuccess": 0, "drivenGroundPassTry": 0,
                 "drivenGroundPassSuccess": 0, "throughPassTry": 0, "throughPassSuccess": 0,
                 "lobbedThroughPassTry": 0, "lobbedThroughPassSuccess": 0},
        "defence": {"blockTry": rng.randint(0, 5), "blockSuccess": rng.randint(0, 2),
                    "tackleTry": rng.randint(3, 15), "tackleSuccess": rng.randint(1, 8)},
        "player": players,
    }


def _goals(rng, edge: float) -> int:
    """골 수 — 평균 1.6 근처의 작은 분포(edge 가 크면 조금 더)."""
    lam = max(0.3, 1.6 + edge)
    n, p = 0, rng.random()
    acc = term = pow(2.718281828, -lam)
    while p > acc and n < 7:
        n += 1
        term *= lam / n
        acc += term
    return n


def generate(today: date | None = None, n: int | None = None, seed: int | None = None) -> DemoData:
    """지어낸 자료 한 벌 — 같은 seed·today 면 바이트까지 같다."""
    today = today or date.today()
    n = config.DEMO_MATCHES if n is None else n
    rng = random.Random(config.DEMO_SEED if seed is None else seed)
    mine = _squad(rng, 900_001)
    names = {sp: _name(rng) for sp, _g in mine}
    opps = []
    for k in range(config.DEMO_OPPONENTS):
        nick = f"{rng.choice(_NICK_A)}{rng.choice(_NICK_B)}{rng.randint(1, 99)}"
        squad = _squad(rng, 910_001 + k * 20)
        names.update((sp, _name(rng)) for sp, _g in squad)
        opps.append((f"{k + 1:032x}".replace("0", "e", 8), nick, rng.choice((800, 900, 900, 1000, 1100)), squad))
    start = datetime.combine(today - timedelta(days=config.DEMO_DAYS), dtime(0))
    span = (datetime.combine(today, dtime(0)) - start).total_seconds()
    stamps = sorted(start + timedelta(seconds=rng.uniform(0, span)) for _ in range(n))
    details = []
    for i, kst in enumerate(stamps):
        o_ouid, o_nick, o_div, o_squad = rng.choice(opps)
        edge = rng.gauss(0.15, 0.5)
        mg, og = _goals(rng, edge), _goals(rng, -edge)
        res_me = "승" if mg > og else ("패" if mg < og else "무")
        res_opp = {"승": "패", "패": "승", "무": "무"}[res_me]
        pos_me = max(30, min(70, int(rng.gauss(52, 7))))
        my_div = 900 if i < n // 2 else 800
        squad = list(mine)
        if rng.random() < 0.15:   # 가끔 선발 한 자리를 교체 명단 선수로 — 스쿼드 타임라인에 변화가 보이게
            j, k = rng.randint(1, 10), rng.randint(11, 17)
            squad[j], squad[k] = squad[k], squad[j]
        utc = kst - timedelta(hours=config.MATCH_DATE_OFFSET_H)
        details.append({
            "matchId": f"demo{config.DEMO_SEED:04d}{i:016d}",
            "matchDate": utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "matchType": MATCH_TYPE,
            "matchInfo": [_side(rng, OUID, NICK, my_div, squad, mg, res_me, pos_me),
                          _side(rng, o_ouid, o_nick, o_div, o_squad, og, res_opp, 100 - pos_me)],
        })
    mid = today - timedelta(days=config.DEMO_DAYS // 2)
    seasons = [sn.Season(no=990, name="시즌 1", start=today - timedelta(days=config.DEMO_DAYS * 2), end=mid)]
    meta = {
        "division": [{"divisionId": i, "divisionName": nm} for i, nm in DIVISIONS],
        "spposition": [{"spposition": i, "desc": d} for i, d in enumerate(POSITIONS)],
        "matchtype": [{"matchtype": i, "desc": d} for i, d in MATCH_TYPES],
        "seasonid": [{"seasonId": k, "className": v, "seasonImg": ""} for k, v in CARD_SEASONS.items()],
        "spid": [{"id": sp, "name": nm} for sp, nm in sorted(names.items())],
    }
    best_at = (stamps[n // 2] - timedelta(hours=config.MATCH_DATE_OFFSET_H)).strftime("%Y-%m-%dT%H:%M:%S") if n else ""
    return DemoData(details, meta, best_at, seasons)


def install(data: DemoData, db_path: Path, cache_dir: Path) -> None:
    """자료를 데모 폴더의 fifa.db·메타 캐시에 넣는다 — 화면이 켤 때 디스크에서 읽는 것(등급 배지 번호·시즌표·최근 검색)까지."""
    conn = store.open_db(db_path)
    try:
        store.save_matches(conn, list(data.details.values()))
        store.save_seasons(conn, data.seasons)
        store.upsert_account(conn, OUID, NICK)
    finally:
        conn.close()
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in data.meta.items():
        (cache_dir / f"meta_{name}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def latest_kst(data: DemoData) -> datetime | None:
    """가장 늦은 경기 시각(KST) — 테스트용."""
    return matchtime.parse(max(d["matchDate"] for d in data.details.values())) if data.details else None
