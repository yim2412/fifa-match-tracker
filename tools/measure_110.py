"""110단계 조각 ④ 관문 측정. 랭커 팀 전술·개인 전술을 사람별로 보여 줄 수 있는지 잽니다(이 PC 전용 · 배포판 밖).

판정 규칙은 ROADMAP "조각 ④ 관문 측정 — 규칙" 이 정본입니다(기준 커밋 `RULE_COMMIT`). 여기 상수는 그 절을 옮긴 것이고
**결과를 보고 여기 숫자를 바꾸지 않습니다.**

| 명령 | 하는 일 | 요청 |
|---|---|---|
| `--targets` | 표본 75명과 최근 선발을 `targets.json` 에 얼립니다(다시 하면 거부) | 0 |
| `--fetch [N]` | 남은 사람(앞에서 N명)의 칸을 받아 `raw/<번호>.json.gz` 에. 사람마다 한 파일이라 끊겨도 이어집니다 | 홈페이지 |
| `--judge` | `raw/` 만 읽고 판정해 `result.json` 에(닉네임·번호 없음) | 0 |
| `--known 번들.js` | 스쿼드메이커 번들의 자리 × 개인 전술 표와 대조해 조건 ④ 재료 `known.json` 을 만듭니다(`--judge` 앞뒤로) | 0 |
| `--app` | 앱 코드(`tactics` · `tactic_codes`)로 받아 둔 사람을 다시 판정해 `result.json` 과 사람마다 대조하고, 이름표를 번들과 대조합니다 | 0 |
| `--status` | 받은 사람 수·오늘 요청 수 | 0 |

막힘(403·429·확인 페이지) 한 번이면 `blocked` 를 남기고 끝냅니다. 그 뒤 `--fetch` 는 거부합니다. 게임 중이면 안 합니다.

    python tools/measure_110.py --targets
    node ~/.claude/tools/bgrun.js --name "110 관문" -- python tools/measure_110.py --fetch
    python tools/measure_110.py --judge
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import sqlite3
import struct
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import gameguard  # noqa: E402
import tactic_codes  # noqa: E402
import tactics  # noqa: E402
import traitcollect  # noqa: E402
from demodata import POSITIONS  # noqa: E402

RULE_COMMIT = "842d8ce"
OUT = config.DATA_DIR / "measure_110"
RAW = OUT / "raw"

TOP_RANK, TOP_MOD, TOP_REM = 200, 4, 2     # (가) 판정용
WIDE_STEP, WIDE_MAX = 400, 10_000          # (나) 참고용
RECENT_GAMES = 20
MAX_AGE_DAYS = 14
DISTINCT_LINEUPS = 5
GAP_S = 0.25
DAILY_REQ = 2_000
TEAMS = config.TRAIT_TEAM_ORDER
SEQS = tuple(range(10))
SUB = 28

PASS_SEEN = 0.70
PASS_FEW, FEW_MAX = 0.60, 3
PASS_MEAN_REQ, PASS_P90_S = 25, 20.0
PASS_KNOWN = 0.99


# ── 순수 함수(테스트 tests/test_measure_110.py) ──────────────────────────────

def unpack_lineup(body: bytes) -> list[tuple[int, int]]:
    """dev_matches 선발 묶음 → (spid, 자리) 목록."""
    if body[:1] == b"\x01" and len(body) % 6:
        return [(a, b) for a, b, _ in struct.iter_unpack("<qhh", body[1:])]
    return [(a, b) for a, b, _ in struct.iter_unpack("<IBB", body)]


def pairs_of(lineup) -> frozenset:
    return frozenset((int(s), POSITIONS[p].lower()) for s, p in lineup)


def distinct_lineups(lineups: list, limit: int = DISTINCT_LINEUPS) -> list[frozenset]:
    """새것부터 온 선발들 → 서로 다른 (카드, 자리) 묶음 limit 개(새것부터)."""
    out: list[frozenset] = []
    for x in lineups:
        f = pairs_of(x)
        if f not in out:
            out.append(f)
        if len(out) >= limit:
            break
    return out


def squad(resp: dict) -> dict | None:
    """응답 → Squadinfo · 저장 안 된 칸(ResultCode -1)은 None. 구조 이상은 TraitError("format")."""
    if traitcollect.parse_team(resp) is None:
        return None
    return resp["ResultData"]["Squadinfo"]


def cell_pairs(sq: dict) -> frozenset:
    return frozenset((p["spid"], str(p.get("role")).lower()) for p in sq["players"] if p["state"] == 1)


def cell_cards(sq: dict) -> set:
    return {p["spid"] for p in sq["players"]}


def content(sq: dict) -> tuple:
    """보여 줄 내용 — 팀 전술 값 + 선발의 (자리, 개인 전술, 참여도). 개인 전술 순서는 안 봅니다."""
    who = []
    for p in sq["players"]:
        if p["state"] != 1:
            continue
        strat = tuple(sorted((str(a), str(b)) for a, b in (p.get("strategy") or []) if str(b) != "0"))   # 0 = 기본값(번들 vR) = 키 없음
        who.append((str(p.get("role")).lower(), strat, tuple(p.get("participation") or (2, 2))))
    return tuple(sorted((k, str(v)) for k, v in sq["teamSt"].items())), tuple(sorted(who))


def teams_for(first: dict, lineup: frozenset) -> list[str]:
    """팀마다 전술 1 칸(first: {"타입-팀": Squadinfo|None})에서 선발 카드가 다 있는 팀."""
    cards = {s for s, _ in lineup}
    return [k for k, sq in first.items() if sq is not None and cards and cards <= cell_cards(sq)]


def verdict(lineups: list[frozenset], cells: dict[str, dict | None], last_only: bool = False) -> dict:
    """새것부터 보며 처음 맞는 선발 → 맞은 칸·서로 다른 내용 수. cells 는 {"타입-팀-전술": Squadinfo|None}."""
    for i, lu in enumerate(lineups[:1] if last_only else lineups):
        hit = sorted(k for k, sq in cells.items() if sq is not None and cell_pairs(sq) == lu)
        if hit:
            return {"back": i, "cells": hit, "distinct": len({content(cells[k]) for k in hit})}
    return {"back": None, "cells": [], "distinct": 0}


def pctile(xs: list[float], q: float) -> float:
    """가장 가까운 순위(올림) — 표본 n 의 q 분위."""
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, -(-int(q * 1000) * len(s) // 1000) - 1))]


TEAM_ST_MAX = {"defensivestyle": 4, "offensive_style_buildup": 3, "offensive_style_chancecreation": 3, "teammentality": 6,
               "defwidth": 9, "defdepth": 9, "offwidth": 9, "offplayerinbox": 9, "offcornerkick": 4, "offfreekick": 4}   # 응답은 0부터(화면은 +1)


def bundle_table(js: str) -> dict[str, dict[str, int]]:
    """번들 글자 → {자리: {코드: 선택지 수}}. 표는 `=pR({gk:[],sw:[[{tit:`AS`,…},…],…],…})` 꼴입니다."""
    start = js.index("=pR({gk:") + 4
    end = js.index("})", start)
    out: dict[str, dict[str, int]] = {}
    for role, body in re.findall(r"([a-z]+):\[((?:\[[^\]]*\],?)*)\]", js[start:end + 1]):
        out[role] = {}
        for group in re.findall(r"\[([^\]]*)\]", body):
            codes = re.findall(r"tit:`([^`]*)`", group)
            if codes:
                out[role][codes[0]] = len(codes)
    return out


def bundle_names(js: str) -> dict[str, dict[str, tuple[str, ...]]]:
    """번들 글자 → {자리: {코드: (선택지 이름, …)}} — 앱 이름표(`tactic_codes.PLAYER_NAMES`)와 같은 꼴."""
    start = js.index("=pR({gk:") + 4
    end = js.index("})", start)
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for role, body in re.findall(r"([a-z]+):\[((?:\[[^\]]*\],?)*)\]", js[start:end + 1]):
        out[role] = {}
        for group in re.findall(r"\[([^\]]*)\]", body):
            items = re.findall(r"tit:`([^`]*)`,desc:`([^`]*)`", group)
            out[role][items[0][0]] = tuple(name for _c, name in items)
    return out


def team_names_in(js: str, names: tuple[str, ...]) -> bool:
    """이름 목록이 번들에 그 차례로 있나 — `[{value:0,label:`가`},…]` 또는 [`가`,`나`] 꼴."""
    labeled = ",".join(f"{{value:{i},label:`{n}`}}" for i, n in enumerate(names))
    return f"[{labeled}]" in js or "[" + ",".join(f"`{n}`" for n in names) + "]" in js


def readable(key: str, table: dict) -> bool:
    """판정이 모은 값 이름(`st|…` · `pl|…` · `pa|…`) 하나가 번들 표로 읽히나."""
    kind, *rest = key.split("|")
    if kind == "st":
        return rest[0] in TEAM_ST_MAX and rest[1].isdigit() and int(rest[1]) <= TEAM_ST_MAX[rest[0]]
    if kind == "pl":
        role, code, level = rest
        return level.isdigit() and int(level) < table.get(role, {}).get(re.sub(r"\d+$", "", code), 0)
    return rest[0] in {str((a, b)) for a in (1, 2, 3) for b in (1, 2, 3)}


def known(path: str) -> int:
    table = bundle_table(Path(path).read_text(encoding="utf-8"))
    values = json.loads((OUT / "result.json").read_text(encoding="utf-8"))["values"]
    bad = {k: v for k, v in values.items() if not readable(k, table)}
    total = sum(values.values())
    _save("known.json", {"known": total - sum(bad.values()), "all": total, "unknown": bad, "roles": len(table)})
    print(f"자리 {len(table)} · 값 {total} · 모르는 값 {sum(bad.values())} {bad}")
    return 0


# ── 표본 ─────────────────────────────────────────────────────────────────────

def _ro(name: str) -> sqlite3.Connection:
    return sqlite3.connect((config.DATA_DIR / name).as_uri() + "?mode=ro", uri=True)


def make_targets(now: float) -> list[dict]:
    rank, dev = _ro("rank.db"), _ro("dev_matches.db")
    try:
        sid = rank.execute("SELECT MAX(id) FROM snapshots").fetchone()[0]
        rows = rank.execute("SELECT rank, profile_sn FROM snapshot_rows WHERE snapshot_id=? ORDER BY rank", (sid,)).fetchall()
        out = []
        for r, sn in rows:
            a = r <= TOP_RANK and r % TOP_MOD == TOP_REM
            b = r % WIDE_STEP == 0 and r <= WIDE_MAX
            if not (a or b):
                continue
            own = dev.execute("SELECT id FROM owners WHERE profile_sn=?", (sn,)).fetchall()
            games = []
            for (o,) in own:
                for me, l in (("o1", "l1"), ("o2", "l2")):
                    games += dev.execute(
                        f"SELECT g.at, x.body FROM games g LEFT JOIN lineups x ON x.id=g.{l} WHERE g.{me}=? "
                        "ORDER BY g.at DESC LIMIT ?", (o, RECENT_GAMES)).fetchall()
            games.sort(key=lambda g: -g[0])
            lineups, first_at = [], None
            for at, body in games[:RECENT_GAMES]:
                if body is None or now - at > MAX_AGE_DAYS * 86400:
                    continue
                lu = [x for x in unpack_lineup(body) if x[1] != SUB]
                if len(lu) != 11:
                    continue
                first_at = first_at or at
                lineups.append(lu)
            out.append({"no": len(out), "group": "a" if a else "b", "sn": sn, "lineups": lineups,
                        "age_h": None if first_at is None else round((now - first_at) / 3600, 1)})
        return out
    finally:
        rank.close()
        dev.close()


# ── 받기 ─────────────────────────────────────────────────────────────────────

class Stop(Exception):
    pass


def _meter() -> dict:
    p = OUT / "meter.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"days": {}, "blocked": None}


def _save(name: str, obj) -> None:
    tmp = OUT / (name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT / name)


class Fetcher:
    def __init__(self):
        self.session = traitcollect.new_session()
        self.meter = _meter()
        self.day = date.today().isoformat()
        self.log: list[float] = []

    def cell(self, sn: int, team: tuple[int, int], seq: int) -> dict:
        if self.meter["blocked"]:
            raise Stop(f"막힌 기록이 있습니다: {self.meter['blocked']}")
        if gameguard.game_running():
            raise Stop("게임 중입니다")
        used = self.meter["days"].get(self.day, 0)
        if used >= DAILY_REQ:
            raise Stop("하루 상한")
        self.meter["days"][self.day] = used + 1
        _save("meter.json", self.meter)
        time.sleep(GAP_S)
        t = time.monotonic()
        try:
            resp = traitcollect._post(self.session, {
                "strMethod": traitcollect.M_TEAM, "n1TeamType": team[0], "n1TeamPart": team[1],
                "n4TeamSeq": seq, "n8TargetNexonSN": sn})
        except traitcollect.TraitError as e:
            if e.kind == "rate":
                self.meter["blocked"] = f"{self.day} {e}"
                _save("meter.json", self.meter)
            raise
        finally:
            self.log.append(time.monotonic() - t)
        if isinstance(resp.get("ResultData"), dict):
            resp["ResultData"]["strCharacterName"] = ""   # 닉네임은 남기지 않는다
        return resp


def fetch_person(f: Fetcher, t: dict) -> dict:
    """한 사람 — 규칙 절 "받기" 그대로. 결과: 칸 원응답·요청 시각·거르기로 고른 팀."""
    f.log = []
    t0 = time.monotonic()
    raw: dict[str, dict] = {}
    for team in TEAMS:
        raw[f"{team[0]}-{team[1]}-0"] = f.cell(t["sn"], team, 0)
    first = {f"{a}-{b}": squad(raw[f"{a}-{b}-0"]) for a, b in TEAMS}
    teams: list[str] = []
    for lu in distinct_lineups(t["lineups"]):
        teams = teams_for(first, lu)
        if teams:
            break
    for k in teams:
        a, b = map(int, k.split("-"))
        for s in SEQS[1:]:
            raw[f"{k}-{s}"] = f.cell(t["sn"], (a, b), s)
    main = {"req": len(f.log), "sec": round(time.monotonic() - t0, 2), "net": [round(x, 3) for x in f.log]}
    extra: dict[str, dict] = {}
    if not teams:
        for a, b in TEAMS:
            for s in SEQS[1:]:
                extra[f"{a}-{b}-{s}"] = f.cell(t["sn"], (a, b), s)
    return {"no": t["no"], "main": main, "teams": teams, "raw": raw, "extra": extra}


def fetch(limit: int | None) -> int:
    targets = json.loads((OUT / "targets.json").read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    todo = [t for t in targets if t["lineups"] and not (RAW / f"{t['no']:03d}.json.gz").exists()]
    if limit is not None:
        todo = todo[:limit]
    f = Fetcher()
    for i, t in enumerate(todo, 1):
        try:
            res = fetch_person(f, t)
        except Stop as e:
            print(f"[멈춤] {e}", flush=True)
            return 1
        except traitcollect.TraitError as e:
            print(f"[멈춤] {e.kind}: {e}", flush=True)
            return 1
        p = RAW / f"{t['no']:03d}.json.gz"
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False)
        tmp.replace(p)
        print(f"{i}/{len(todo)}명 · {t['group']}{t['no']} · 요청 {res['main']['req']}+{len(res['extra'])} · "
              f"{res['main']['sec']}초 · 팀 {len(res['teams'])}", flush=True)
    print(f"[끝] 오늘 요청 {f.meter['days'].get(f.day, 0)}", flush=True)
    return 0


# ── 판정 ─────────────────────────────────────────────────────────────────────

def person_result(t: dict, d: dict) -> dict:
    cells = {k: squad(v) for k, v in d["raw"].items()}
    lus = distinct_lineups(t["lineups"])
    v = verdict(lus, cells)
    last = verdict(lus, cells, last_only=True)
    allc = dict(cells, **{k: squad(x) for k, x in d["extra"].items()})
    full = verdict(lus, allc)
    plain = any(not content(allc[k])[1] or all(not s and p == (2, 2) for _, s, p in content(allc[k])[1]) for k in v["cells"])
    values = collections.Counter()
    for k in v["cells"]:
        st, who = content(cells[k])
        values.update(f"st|{a}|{b}" for a, b in st)
        for role, strat, part in who:
            values.update(f"pl|{role}|{a}|{b}" for a, b in strat)
            values[f"pa|{part}"] += 1
    return {"no": t["no"], "group": t["group"], "age_h": t["age_h"], "seen": bool(v["cells"]), "distinct": v["distinct"],
            "n_cells": len(v["cells"]), "back": v["back"], "seen_last": bool(last["cells"]),
            "distinct_last": last["distinct"], "filter_missed": bool(full["cells"]) and not v["cells"],
            "req": d["main"]["req"], "sec": d["main"]["sec"], "extra": len(d["extra"]), "plain": plain,
            "blank": sum(x is None for x in cells.values()), "values": dict(values)}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    return {"n": n, "seen": sum(r["seen"] for r in rows), "seen_last": sum(r["seen_last"] for r in rows),
            "few": sum(1 <= r["distinct"] <= FEW_MAX for r in rows), "one": sum(r["distinct"] == 1 for r in rows),
            "distinct": dict(sorted(collections.Counter(r["distinct"] for r in rows).items())),
            "filter_missed": sum(r["filter_missed"] for r in rows), "plain": sum(r["plain"] for r in rows),
            "req_mean": round(sum(r["req"] for r in rows) / n, 1), "req_max": max(r["req"] for r in rows),
            "sec_p50": pctile([r["sec"] for r in rows], 0.5), "sec_p90": pctile([r["sec"] for r in rows], 0.9),
            "age_h_p50": pctile([r["age_h"] for r in rows], 0.5)}


def gate(s: dict, blocked, known: float | None) -> dict:
    """통과 조건 넷(가 표본 요약) → 조건마다 참·거짓(known 을 아직 모르면 None)."""
    n = s["n"]
    return {"1_seen": s["seen"] / n >= PASS_SEEN, "2_few": s["few"] / n >= PASS_FEW,
            "3_cost": s["req_mean"] <= PASS_MEAN_REQ and s["sec_p90"] <= PASS_P90_S and not blocked,
            "4_known": None if known is None else known >= PASS_KNOWN}


def judge() -> int:
    targets = json.loads((OUT / "targets.json").read_text(encoding="utf-8"))
    rows, missing, none = [], 0, {"a": 0, "b": 0}
    keys: collections.Counter = collections.Counter()
    for t in targets:
        if not t["lineups"]:
            none[t["group"]] += 1
            continue
        p = RAW / f"{t['no']:03d}.json.gz"
        if not p.exists():
            missing += 1
            continue
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        rows.append(person_result(t, d))
        for v in d["raw"].values():
            sq = squad(v)
            if sq:
                keys.update(f"sq:{k}" for k in sq)
                keys.update(f"rd:{k}" for k in v["ResultData"])
                keys.update(f"pl:{k}" for x in sq["players"] for k in x)
    values: collections.Counter = collections.Counter()
    for r in rows:
        values.update(r.pop("values"))
    known_p = OUT / "known.json"     # 번들 표와 대조한 뒤 손으로 만든다: {"known": 읽힌 수, "all": 전체 수, "unknown": [...]}
    k = json.loads(known_p.read_text(encoding="utf-8")) if known_p.exists() else None
    a = summarize([r for r in rows if r["group"] == "a"])
    b = summarize([r for r in rows if r["group"] == "b"])
    res = {"rule": RULE_COMMIT, "missing": missing, "no_games": none, "a": a, "b": b, "meter": _meter(),
           "gate": gate(a, _meter()["blocked"], k and k["known"] / k["all"]) if a["n"] else None,
           "keys": sorted(keys), "values": dict(sorted(values.items())), "rows": rows}
    _save("result.json", res)
    print(json.dumps({x: res[x] for x in ("missing", "no_games", "a", "b", "gate", "meter")}, ensure_ascii=False, indent=1))
    return 0


def app_person(t: dict, d: dict) -> int:
    """앱 길(tactics)로 본 한 사람 — 마지막 경기 선발과 맞는 서로 다른 전술 수. 받아 둔 칸만 읽습니다."""
    raw = {tuple(map(int, k.split("-"))): v for k, v in d["raw"].items()}
    lineup = tactics.lineup_of([{"spId": s, "spPosition": p} for s, p in t["lineups"][0]])
    first = {team: tactics.parse_cell(raw[(*team, 0)]) for team in TEAMS}
    cells = {(*team, s): tactics.parse_cell(raw[(*team, s)]) for team in tactics.candidate_teams(first, lineup) for s in SEQS}
    return len(tactics.match(lineup, cells))


def app() -> int:
    targets = {t["no"]: t for t in json.loads((OUT / "targets.json").read_text(encoding="utf-8"))}
    rows = json.loads((OUT / "result.json").read_text(encoding="utf-8"))["rows"]
    bad, cells, blank, seen = [], 0, 0, collections.Counter()
    for r in rows:
        with gzip.open(RAW / f"{r['no']:03d}.json.gz", "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        n = app_person(targets[r["no"]], d)
        if n != r["distinct_last"]:
            bad.append((r["no"], n, r["distinct_last"]))
        for v in [*d["raw"].values(), *d["extra"].values()]:
            c = tactics.parse_cell(v)
            cells, blank = cells + 1, blank + (c is None)
            if c is not None:
                for k, x in c.team:
                    seen[k, tactic_codes.team_value(k, x)] += 1
                for p in c.players:
                    for code, lv in p.strategy:
                        seen[p.role, tactic_codes.player_value(p.role, code, lv)] += 1
    unknown = {k: n for k, n in seen.items() if tactic_codes.is_unknown(k[1])}
    print(f"{'[FAIL]' if bad else '[OK]  '} 사람 {len(rows)} · 다른 사람 {bad}")
    print(f"{'[FAIL]' if unknown else '[OK]  '} 칸 {cells}(빈칸 {blank}) · 값 {sum(seen.values())} · 모르는 값 {unknown}")
    diff = []
    bundles = sorted(OUT.glob("bundle_*.js"))
    for path in bundles:
        js = path.read_text(encoding="utf-8")
        want = bundle_names(js)
        diff += [(r, want.get(r), tactic_codes.PLAYER_NAMES.get(r)) for r in {*want, *tactic_codes.PLAYER_NAMES}
                 if want.get(r) != tactic_codes.PLAYER_NAMES.get(r)]
        diff += [names for names in tactic_codes.TEAM_NAMES.values() if not team_names_in(js, names)]
    print(f"{'[FAIL]' if diff or not bundles else '[OK]  '} 번들 {len(bundles)}개 · 자리 {len(tactic_codes.PLAYER_NAMES)} · 다른 것 {diff}")
    return 1 if bad or unknown or diff or not bundles else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--targets", action="store_true")
    g.add_argument("--fetch", nargs="?", type=int, const=-1, default=None)
    g.add_argument("--judge", action="store_true")
    g.add_argument("--known")
    g.add_argument("--app", action="store_true")
    g.add_argument("--status", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.targets:
        if (OUT / "targets.json").exists():
            print("[FAIL] 표본을 이미 얼렸습니다")
            return 1
        ts = make_targets(time.time())
        _save("targets.json", ts)
        for g_ in "ab":
            x = [t for t in ts if t["group"] == g_]
            print(g_, len(x), "경기 없음", sum(not t["lineups"] for t in x))
        return 0
    if a.fetch is not None:
        return fetch(None if a.fetch < 0 else a.fetch)
    if a.judge:
        return judge()
    if a.app:
        return app()
    if a.known:
        return known(a.known)
    print(len(list(RAW.glob("*.json.gz"))) if RAW.exists() else 0, "명 받음", _meter())
    return 0


if __name__ == "__main__":
    sys.exit(main())
