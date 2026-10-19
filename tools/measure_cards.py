"""51단계 측정 A~C — 카드 정보로 만들 화면(C1·N28·C2·N29·C3·N21)이 되는지 미리 정한 규칙으로 잰다(이 PC 전용 · 배포판 밖).

판정 규칙은 ROADMAP "## 51단계 › 측정으로 먼저 정할 것" 이 정본이다(기준 커밋 `RULE_COMMIT`). 여기 상수는 그 절을 옮긴 것 —
**결과를 보고 여기 숫자를 바꾸지 않는다**(바꾸려면 그 절을 먼저 고치는 커밋 · 그 해시를 `RULE_COMMIT` 에).

| 명령 | 언제 | 하는 일 |
|---|---|---|
| `--start` | 한 번(D0 전 관문 검토 뒤) | 오늘을 D0 로 `measure/meta.json` 에 — 이게 없으면 `--backfill`·`--b-freeze` 가 안 돈다(D0 를 실수로 잡지 않게) |
| `--backfill [--cap 600]` | D0 ~ D0 + 35 매일 | `cardcollect.run(kind="card_backfill_dev", window=고정 집합)` 을 틱 단위로 상한까지 · 끝에 매일 세기 |
| `--b-freeze` | D0 ~ D0 + 27 매일 | 계정 경기 ↔ 경기 시각에 가장 가까운 스냅숏(24시간 안 · 같은 시즌)을 `measure/b_frozen.jsonl` 에 한 번만 |
| `--count` | 아무 때 | 매일 세기만(A: 22장 다 아는 경기 · B: 상대 11장 팀컬러를 다 아는 경기 절반별) — 모델은 안 맞춘다 |
| `--a` | D0 + 21 · 28 · 35(그날 또는 그 뒤 첫 실행) | `--selftest` 를 그날 실제 n·Δ 로 먼저 → 통과해야 판정 |
| `--b` · `--c` | D0 + 35 한 번 | B 판정(앞 절반 → 뒤 절반) · C 세기 |
| `--selftest` | 아무 때(관문 검토 · 측정일) | 답을 아는 가짜 자료로 A 판정이 미달·통과를 맞게 내는지 — 끝 코드 0 통과 · 1 고정 시험 미달(규칙 버그 — 측정일이면 멈춤) · 2 검정력만 미달(측정일이면 그날 "재료 부족" 으로 기록) |
| `--status` | 아무 때 | D0 · 오늘이 며칠째 · 다음 할 일 · 결과 파일 |

거부하는 것: 앱 토글 "카드 정보 자동 채우기" 가 켜져 있으면(같은 IP 하루 1,200 — `.env` 를 디스크에서 읽어 본다) ·
그날 다른 웹 길이 막혔으면(`cardcollect.run` 이 `store.card_block_kinds()` 로) · 일정 밖의 날.
저장소 밖에만 쓴다: `DATA_DIR/measure/`(닉네임·ouid 는 안 남긴다 — 스냅숏 맞대기에만 쓴다). fifa.db·rank.db 는 읽기만
(`--backfill` 만 fifa.db 의 카드 표·`api_budget` 에 쓴다 — 앱 백필과 같은 함수).

numpy 를 쓴다(이 PC 에만 있으면 된다 — 앱은 결과 상수 `config.C1_LOGIT_*` 만 읽는다). 실효 OVR 은 앱과 같은 `cardstats.effective_ovr`.

    python tools/measure_cards.py --status
    python tools/measure_cards.py --selftest
    python tools/measure_cards.py --backfill        # 작업 스케줄러에 걸 때는 bgrun 으로 감싼다(상태줄)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cardcollect  # noqa: E402
import cardstats  # noqa: E402
import config  # noqa: E402
import rankcollect  # noqa: E402
import seasons as seasons_mod  # noqa: E402
import store  # noqa: E402
import teamcolor  # noqa: E402

# ── 판정 규칙(ROADMAP 51 측정 절 — 기준 커밋 10판) ──────────────────────────────
RULE_COMMIT = "35ec1cf"
MATCH_TYPE = config.DEFAULT_MATCH_TYPE   # 감독모드 52
W_A_DAYS = 60                # A 창 [D0 − 60일, D0)
A_DAYS = (21, 28, 35)        # 측정 A 날(재료 부족이면 다음 날로 — 최대 3번)
FREEZE_LAST = 27             # B 얼리기 D0 ~ D0 + 27
JUDGE_DAY = 35               # B·C 한 번 · 백필 끝
PAIR_H = 24                  # B 짝짓기 — 경기 시각과 스냅숏 시각 차 상한
TRAIN_SHARE = 0.70
SEED = 20261018
BOOT = 1000
REL_GAIN = 0.01              # M1 이 M0 보다 검증 로그손실이 상대 1% 이상 낮아야
ECE_BINS = 5
ECE_SIMS = 1000
CALIB_Q = 0.975             # ③ ECE·평균 차 각각 — 둘 합쳐 맞는 모델 미달 약 5%
RECENT_DAYS = 14             # ④ 검증 마지막 14일
RECENT_FLOOR = -0.01         # ④ 그 구간 상대 차 95% 구간 하한
SELF_REL = 0.02              # selftest 효과 크기 — 검증 상대 개선 2%
SELF_SEEDS = 200
SELF_PASS = 0.90
SELF_N_DEFAULT = 9000        # 그날 자료가 없을 때(관문 검토) 가짜 n — 이 PC W_A 상한(9,289) 근처 · 그날 실제 n 은 --a 가 넘긴다
SELF_REAL_MIN = 100          # 실제 Δ 가 이보다 적으면 가짜 Δ 로(n 은 그날 n)
SELF_FIXED_N = 3000          # 고정 시험 가짜 n — 그날 자료와 무관하게 늘 같다
SELF_CALIB_SEEDS = 200       # ③ 고정 시험 둘의 시드 수
SELF_CALIB_OK = 0.90         # 완벽 보정 → ③ 통과 비율(설계 95% · 오늘 92~94%)
SELF_SHIFT_CATCH = 0.70      # 평균 5%p 밀림 → ③ 미달 비율(오늘 76~80% — 97.5% 문턱의 대가)
A_GRACE = 3                  # 측정일 뒤 이 날수까지만 받는다 — 넘기면 그 회차는 "재료 부족(놓침)"(재는 날을 고르지 못하게)
CAP_MAX = 600                # --cap 상한(규칙의 하루 600)
B_AGREE = 0.90               # 주 팀컬러 이름 일치율
B_MIN_HALF = 300             # 절반마다 경기
C_MIN = 30                   # 칸마다 경기(화면 침묵 규칙과 같은 값)
C_WINDOW_DAYS = 30           # C 의 카드 집합 = 계정의 [D0 − 30, D0) 경기 양쪽 선발
SELF_OK, SELF_FIXED_FAIL, SELF_POWER_FAIL = 0, 1, 2   # selftest 끝 코드(9판 — P6)
C3_OVR_BIN, C3_H_BIN = 2, 5
TC_NO_STEPS = (90000000, 91000000)   # 단일팀·단일국가 — 목록·상세가 없다(조각 ① 실측)

MEASURE_DIR = config.DATA_DIR / "measure"
PROGRESS_DIR = Path.home() / ".claude" / "bg-progress.d"


# ── 파일 · 일정 ──────────────────────────────────────────────────────────────

def _paths(d: Path) -> dict[str, Path]:
    return {"meta": d / "meta.json", "frozen": d / "b_frozen.jsonl", "daily": d / "daily.jsonl",
            "a": d / "a_result.json", "b": d / "b_result.json", "c": d / "c_result.json"}


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def d0_of(meta: dict | None) -> date | None:
    try:
        return date.fromisoformat((meta or {}).get("d0", ""))
    except ValueError:
        return None


def day_no(meta: dict | None, today: date) -> int | None:
    d0 = d0_of(meta)
    return None if d0 is None else (today - d0).days


TOOL_FILES = ("tools/measure_cards.py", "cardstats.py", "cardcollect.py")


def tool_hashes() -> dict[str, str]:
    """측정에 쓰는 코드의 sha256 — D0 에 적고 판정 때 견준다(RULE_COMMIT 상수만으로는 D0 뒤 코드 변경이 안 보인다)."""
    out = {}
    for rel in TOOL_FILES:
        try:
            out[rel] = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        except OSError:
            out[rel] = ""
    return out


def audit_of(fifa, meta: dict, today: date) -> dict:
    """판정 결과에 같이 남길 것 — D0 뒤 도구가 바뀌었나 · 측정 기간에 앱 백필이 돈 날(같은 IP · C 를 두껍게 한다)."""
    d0 = d0_of(meta)
    changed = sorted(k for k, v in tool_hashes().items() if (meta.get("tool_sha256") or {}).get(k) != v)
    days = [r[0] for r in fifa.execute("SELECT day FROM api_budget WHERE kind = ? AND attempts > 0 AND day >= ? AND day <= ?"
                                       " ORDER BY day", (store.BUDGET_CARD, d0.isoformat() if d0 else "", today.isoformat()))]
    return {"tool_changed": changed, "app_backfill_days": days}


def start(d: Path, today: date) -> tuple[bool, str]:
    p = _paths(d)["meta"]
    if load_json(p) is not None:
        return False, f"이미 D0 가 있다({load_json(p).get('d0')}) — 다시 잡으려면 measure 폴더를 손으로 옮긴다"
    save_json(p, {"d0": today.isoformat(), "rule_commit": RULE_COMMIT, "tool_sha256": tool_hashes(),
                  "created_at": datetime.now().isoformat(timespec="seconds")})
    return True, f"D0 = {today} (판정 규칙 기준 커밋 {RULE_COMMIT})"


def read_frozen(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue   # 반쯤 쓴 마지막 줄 — 그 줄만 버린다
    except OSError:
        pass
    return out


# ── 경기 재료 ────────────────────────────────────────────────────────────────

def _ro(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def account_ouids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT ouid FROM accounts")}


def account_matches(conn, lo: str = "", hi: str = "￿", *, ouid: str | None = None):
    """→ [(match_id, match_date, detail)] 계정(또는 그 ouid 하나) 감독모드 경기 · match_date ∈ [lo, hi) · 날짜 순. 깨진 경기는 건너뛴다."""
    if ouid is None:
        q = ("SELECT DISTINCT m.match_id, m.match_date, m.payload FROM matches m JOIN match_players p ON p.match_id = m.match_id"
             " JOIN accounts a ON a.ouid = p.ouid WHERE m.match_type = ? AND m.match_date >= ? AND m.match_date < ?")
        args = (MATCH_TYPE, lo, hi)
    else:
        q = ("SELECT m.match_id, m.match_date, m.payload FROM matches m JOIN match_players p ON p.match_id = m.match_id"
             " WHERE p.ouid = ? AND m.match_type = ? AND m.match_date >= ? AND m.match_date < ?")
        args = (ouid, MATCH_TYPE, lo, hi)
    out = []
    for r in conn.execute(q, args):
        try:
            d = store._loads(r["payload"])
        except Exception:   # noqa: BLE001 — 깨진 한 경기가 나머지를 막지 않는다
            continue
        if isinstance(d, dict):
            out.append((r["match_id"], r["match_date"] or "", d))
    out.sort(key=lambda t: (t[1], t[0]))
    return out


def sides(detail: dict) -> list[dict]:
    """matchInfo 두 쪽 — ouid 순(방향을 정할 때 순서가 저장 순서에 매이지 않게)."""
    infos = [p for p in detail.get("matchInfo") or [] if isinstance(p, dict)]
    return sorted(infos, key=lambda p: str(p.get("ouid") or ""))


def _shootout(side: dict) -> bool:
    v = (side.get("shoot") or {}).get("shootOutScore")
    return isinstance(v, (int, float)) and v > 0


def me_index(match_id: str) -> int:
    """측정 A 방향 — "나" = sha256(match_id) 첫 바이트의 맨 아래 비트 쪽(ouid 순 두 쪽 중)."""
    return hashlib.sha256(match_id.encode()).digest()[0] & 1


def outcome(match_id: str, detail: dict) -> tuple[int | None, str]:
    """→ (나 쪽이 이겼나 1/0, 뺀 이유). 무승부·몰수·승부차기·오류는 뺀다."""
    ss = sides(detail)
    if len(ss) != 2:
        return None, "sides"
    if any(_shootout(s) for s in ss):
        return None, "shootout"
    if any((s.get("matchDetail") or {}).get("matchEndType") not in (0, None) for s in ss):
        return None, "forfeit"
    res = (ss[me_index(match_id)].get("matchDetail") or {}).get("matchResult")
    if res == "승":
        return 1, ""
    if res == "패":
        return 0, ""
    if res == "무":
        return None, "draw"
    return None, "forfeit" if res and "몰수" in res else "result"


def extras_of(conn) -> dict[int, dict]:
    return {r["spid"]: dict(r) for r in conn.execute(
        "SELECT spid, height, body, page_on, pos_ovr, club_tc, rel_tc, sim_on FROM card_extra")}


def format_failed(conn) -> set[int]:
    """시뮬레이터 쪽 형식 실패가 남은 카드 — 측정 A 의 "형식 실패" 이유를 "백필 없음" 과 가른다."""
    out = set()
    for key, fails in conn.execute("SELECT key, fails FROM card_tries WHERE key LIKE 'c:%:sim' AND fails > 0"):
        try:
            out.add(int(key.split(":")[1]))
        except (IndexError, ValueError):
            continue
    return out


def w_a_bounds(d0: date) -> tuple[str, str]:
    return (d0 - timedelta(days=W_A_DAYS)).isoformat(), d0.isoformat()


def window_cards(fifa, d0: date, frozen: list[dict]) -> list[int]:
    """측정 수요(고정 집합) — B 얼린 경기의 상대 선발을 파일 순으로 먼저, 그다음 W_A 경기 양쪽 선발을 등장 순(같으면 spid)."""
    out: dict[int, None] = {}
    for fr in frozen:
        for spid, _g, _po in fr.get("opp") or []:
            out.setdefault(int(spid))
    lo, hi = w_a_bounds(d0)
    seen: Counter = Counter()
    for _mid, _at, d in account_matches(fifa, lo, hi):
        for s in sides(d):
            for p in cardstats.starters(s):
                if isinstance(p.get("spId"), int):
                    seen[p["spId"]] += 1
    for spid, _n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])):
        out.setdefault(spid)
    return list(out)


# ── A 재료 · 매일 세기 ───────────────────────────────────────────────────────

@dataclass
class ARows:
    when: list[str] = field(default_factory=list)       # match_date
    delta: list[float] = field(default_factory=list)    # 나 − 상대
    y: list[int] = field(default_factory=list)
    acc_sign: list[int] = field(default_factory=list)   # +1 나 = 계정 쪽 · −1 상대 = 계정 쪽 · 0 둘 다/모름
    total: int = 0                                      # W_A 경기 수
    dropped: Counter = field(default_factory=Counter)   # 이유 → 경기 수
    dropped_season: Counter = field(default_factory=Counter)   # (이유, 처음 모른 카드의 시즌 번호 spid//1e6)
    lag_days: list[int] = field(default_factory=list)   # sim_on − 경기일(22장 다 아는 경기의 카드마다)


def a_rows(fifa, d0: date) -> ARows:
    lo, hi = w_a_bounds(d0)
    ext = extras_of(fifa)
    bad = format_failed(fifa)
    accs = account_ouids(fifa)
    out = ARows()
    for mid, at, d in account_matches(fifa, lo, hi):
        out.total += 1
        y, why = outcome(mid, d)
        if y is None:
            out.dropped[why] += 1
            continue
        ss = sides(d)
        vals = []
        for s in ss:
            v, why = cardstats.lineup_strength(s, ext)
            if v is None:
                first = next((p.get("spId") for p in cardstats.starters(s)
                              if cardstats.effective_ovr(p.get("spPosition"), p.get("spGrade"), ext.get(p.get("spId")))[0] is None),
                             None)
                if why == "none" and first in bad:
                    why = "format"
                out.dropped[why] += 1
                out.dropped_season[(why, (first or 0) // 1_000_000)] += 1
                break
            vals.append(v)
        else:
            i = me_index(mid)
            out.when.append(at)
            out.delta.append(vals[i] - vals[1 - i])
            out.y.append(y)
            in_acc = [str(s.get("ouid")) in accs for s in ss]
            out.acc_sign.append(0 if in_acc[0] == in_acc[1] else (1 if in_acc[i] else -1))
            try:
                md = date.fromisoformat(at[:10])
                for s in ss:
                    for p in cardstats.starters(s):
                        so = (ext.get(p.get("spId")) or {}).get("sim_on")
                        out.lag_days.append((date.fromisoformat(so) - md).days)
            except (TypeError, ValueError):
                pass
    return out


def b_known(fifa, frozen: list[dict]) -> tuple[int, int, int, int]:
    """→ (앞 절반 아는 · 앞 절반 · 뒤 절반 아는 · 뒤 절반) — 상대 11장 팀컬러(시뮬레이터 쪽)를 다 아는 경기."""
    ext = extras_of(fifa)
    front, back = halves(frozen)

    def known(fr):
        return all((ext.get(int(s)) or {}).get("sim_on") for s, _g, _po in fr.get("opp") or [])
    return sum(map(known, front)), len(front), sum(map(known, back)), len(back)


def halves(frozen: list[dict]) -> tuple[list[dict], list[dict]]:
    """얼린 경기를 match_date 중앙값으로 앞·뒤로(같은 날짜는 match_id 순) — 앞이 n//2."""
    rows = sorted(frozen, key=lambda r: (r.get("match_date") or "", r.get("match_id") or ""))
    k = len(rows) // 2
    return rows[:k], rows[k:]


def count_line(fifa, d0: date, frozen: list[dict], today: date) -> dict:
    a = a_rows(fifa, d0)
    bf, bn, bb, bm = b_known(fifa, frozen)
    return {"day": today.isoformat(), "day_no": (today - d0).days, "a_known": len(a.y), "a_total": a.total,
            "a_dropped": dict(a.dropped), "b_front": [bf, bn], "b_back": [bb, bm]}


def show_count(c: dict) -> str:
    dr = " · ".join(f"{k} {v}" for k, v in sorted(c["a_dropped"].items()))
    return (f"D0+{c['day_no']} · A 22장 다 아는 경기 {c['a_known']:,}/{c['a_total']:,}(뺀 것: {dr or '없음'})"
            f" · B 팀컬러 다 아는 경기 앞 {c['b_front'][0]:,}/{c['b_front'][1]:,} · 뒤 {c['b_back'][0]:,}/{c['b_back'][1]:,}")


# ── A 판정 ──────────────────────────────────────────────────────────────────

def _logloss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def fit_logit(x: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float]:
    """logit p = a + b·x — 뉴턴법(아주 약한 L2 로 완전 분리에서도 멈춘다)."""
    a = b = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(a + b * x)))
        w = p * (1 - p)
        g = np.array([np.sum(p - y), np.sum((p - y) * x) + 1e-6 * b])
        h = np.array([[np.sum(w) + 1e-9, np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x) + 1e-6]])
        step = np.linalg.solve(h, g)
        a, b = a - step[0], b - step[1]
        if np.max(np.abs(step)) < 1e-10:
            break
    return float(a), float(b)


def predict(a: float, b: float, x) -> np.ndarray:
    return 1 / (1 + np.exp(-(a + b * np.asarray(x, dtype=float))))


def _rel_ci(l0: np.ndarray, l1: np.ndarray, rng, boot: int) -> tuple[float, float, float]:
    """검증 로그손실 상대 차(1 − ΣL1/ΣL0)와 경기 단위 부트스트랩 95% 구간(모델은 고정 — 검증 쪽만 다시 뽑는다)."""
    n = len(l0)
    rel = 1 - l1.sum() / l0.sum()
    idx = rng.integers(0, n, (boot, n))
    rb = 1 - l1[idx].sum(1) / l0[idx].sum(1)
    lo, hi = np.percentile(rb, [2.5, 97.5])
    return float(rel), float(lo), float(hi)


def ece_of(p: np.ndarray, y: np.ndarray, order: np.ndarray, bins: int = ECE_BINS) -> np.ndarray:
    """5분위 ECE — order 는 p 를 정렬한 순서(같은 p 에서 모의 y 여러 벌을 한 번에: y 는 (k, n) 도 된다)."""
    y = np.atleast_2d(y)
    n = p.shape[0]
    out = np.zeros(y.shape[0])
    for part in np.array_split(order, bins):
        if len(part):
            out += len(part) / n * np.abs(y[:, part].mean(1) - p[part].mean())
    return out


def fisher_cov(a: float, b: float, x: np.ndarray) -> np.ndarray:
    """학습 쪽 (a, b) 의 근사 공분산 — 피셔 정보의 역행렬."""
    p = predict(a, b, x)
    w = p * (1 - p)
    h = np.array([[w.sum(), (w * x).sum()], [(w * x).sum(), (w * x * x).sum()]])
    return np.linalg.inv(h + np.eye(2) * 1e-9)


def calib(p: np.ndarray, y: np.ndarray, rng, *, x=None, theta=None, cov=None) -> dict:
    """③ — 모델이 맞다면 나올 결과를 ECE_SIMS 번 만들어 ECE·|평균 차| 를 각자 CALIB_Q 분위와 견준다(예측값 p 는 고정).

    cov 를 주면 결과를 만들 때 계수를 N(theta, cov) 에서 뽑는다(9판 — P2: 학습 쪽 추정 오차까지 품는다 · 없으면 8판처럼
    맞는 모델도 14~17% 미달). cov 가 없으면 계수를 아는 것으로 보고 p 에서 바로 뽑는다(selftest 고정 시험)."""
    order = np.argsort(p, kind="stable")
    ece = float(ece_of(p, y, order)[0])
    if cov is None:
        ps = np.broadcast_to(p, (ECE_SIMS, len(p)))
    else:
        th = rng.multivariate_normal(theta, cov, ECE_SIMS)
        ps = 1 / (1 + np.exp(-(th[:, :1] + th[:, 1:] * np.asarray(x, dtype=float)[None, :])))
    sims = (rng.random(ps.shape) < ps).astype(float)
    ece_q = float(np.quantile(ece_of(p, sims, order), CALIB_Q))
    mean_diff = float(abs(y.mean() - p.mean()))
    md_q = float(np.quantile(np.abs(sims.mean(1) - p.mean()), CALIB_Q))
    return {"ece": ece, "ece_q": ece_q, "mean_diff": mean_diff, "mean_diff_q": md_q,
            "pass": ece <= ece_q and mean_diff <= md_q}


def judge_a(when: list[str], delta, y, *, seed: int = SEED, boot: int = BOOT) -> dict:
    """측정 A ①~④. when 은 match_date(ISO) — 그 순으로 앞 70% 학습 · 뒤 30% 검증."""
    rng = np.random.default_rng(seed)
    order = sorted(range(len(y)), key=lambda i: when[i])
    x = np.asarray(delta, dtype=float)[order]
    yy = np.asarray(y, dtype=float)[order]
    ww = [when[i] for i in order]
    n = len(yy)
    k = int(n * TRAIN_SHARE)
    out: dict = {"n": n, "n_train": k, "n_test": n - k}
    if k < 2 or n - k < 2 or yy[:k].min() == yy[:k].max():
        out.update(verdict="재료 부족", why="경기가 너무 적다")
        return out
    p0 = float(np.clip(yy[:k].mean(), 1e-6, 1 - 1e-6))
    a, b = fit_logit(x[:k], yy[:k])
    xt, yt = x[k:], yy[k:]
    p1 = predict(a, b, xt)
    l0, l1 = _logloss(np.full(len(yt), p0), yt), _logloss(p1, yt)
    rel, lo, hi = _rel_ci(l0, l1, rng, boot)
    out.update(a=a, b=b, p0=p0, rel=rel, ci=[lo, hi])
    out["decidable"] = lo > 0 or hi < 0   # 9판 — P1: 1% 는 ② 의 점추정에만
    if not out["decidable"]:
        out.update(verdict="재료 부족", why=f"상대 차 95% 구간 [{lo:+.2%}, {hi:+.2%}] 이 0 을 품는다")
        return out
    out["pass2"] = rel >= REL_GAIN and lo > 0
    out["calib"] = calib(p1, yt, rng, x=xt, theta=[a, b], cov=fisher_cov(a, b, x[:k]))
    last = datetime.fromisoformat(ww[-1][:19]) - timedelta(days=RECENT_DAYS)
    rec = np.array([datetime.fromisoformat(w[:19]) >= last for w in ww[k:]])
    if rec.sum() >= 2:
        r_rel, r_lo, r_hi = _rel_ci(l0[rec], l1[rec], rng, boot)
        out["recent"] = {"n": int(rec.sum()), "rel": r_rel, "ci": [r_lo, r_hi]}
        out["pass4"] = r_lo > RECENT_FLOOR
    else:
        out["recent"] = {"n": int(rec.sum())}
        out["pass4"] = False
    ok = out["pass2"] and out["calib"]["pass"] and out["pass4"]
    out["verdict"] = "통과" if ok else "미달"
    return out


def deciles(delta, y) -> list[tuple[float, float, int, float]]:
    """보고만 — Δ 10분위별 (Δ 하한, 상한, 경기 수, 실제 승률)."""
    x, yy = np.asarray(delta, dtype=float), np.asarray(y, dtype=float)
    order = np.argsort(x, kind="stable")
    out = []
    for part in np.array_split(order, 10):
        if len(part):
            out.append((float(x[part].min()), float(x[part].max()), len(part), float(yy[part].mean())))
    return out


# ── selftest ─────────────────────────────────────────────────────────────────

def _fake_when(n: int) -> list[str]:
    """60일에 고르게 퍼진 가짜 경기 시각(순서만 쓰인다 — ④ 의 14일 구간이 생기게)."""
    t0 = datetime(2026, 1, 1)
    return [(t0 + timedelta(seconds=int(i * 60 * 86400 / max(n, 1)))).isoformat() for i in range(n)]


def b_for_gain(x: np.ndarray, gain: float) -> float:
    """a = 0 일 때 기대 상대 개선(1 − 평균 H(p_i) / H(평균 p))이 gain 이 되는 b — 이분법."""
    def h(p):
        p = np.clip(p, 1e-12, 1 - 1e-12)
        return -(p * np.log(p) + (1 - p) * np.log(1 - p))

    def g(b):
        p = predict(0.0, b, x)
        return 1 - h(p).mean() / h(p.mean())
    lo, hi = 0.0, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if g(mid) < gain else (lo, mid)
    return (lo + hi) / 2


def selftest(delta=None, *, n: int | None = None, seeds: int = SELF_SEEDS, log=print) -> int:
    """답을 아는 자료로 A 판정을 잰다.

    고정 시험 넷은 그날 자료와 떼어 늘 같은 가짜 자료(SELF_FIXED_N · 시드 7)로 — 결과가 결정적이라 코드가 그대로면 측정일에도
    D0 때(`--start`)와 같다(관문 검토 [상]1: 그날 n 에 따라 표본이 바뀌어 측정일마다 약 28% 가 "규칙 버그" 로 멈췄다).
    검정력만 그날 Δ·n 으로(delta 가 SELF_REAL_MIN 보다 적으면 가짜 Δ 를 그날 n 만큼 · 아예 없으면 SELF_N_DEFAULT)."""
    rng = np.random.default_rng(7)
    xf = rng.normal(0, 4, SELF_FIXED_N)
    real = delta is not None and len(delta) >= SELF_REAL_MIN
    if real:
        x = np.asarray(delta, dtype=float)
    else:
        x = np.random.default_rng(8).normal(0, 4, n or (len(delta) if delta is not None and len(delta) else SELF_N_DEFAULT))
    n = len(x)
    log(f"검정력 자료: {'그날 실제 Δ' if real else '가짜 Δ(정규 · 표준편차 4)'} n = {n:,} · 고정 시험 가짜 n = {SELF_FIXED_N:,}")
    bad = 0

    def check(ok: bool, text: str):
        nonlocal bad
        bad += not ok
        log(f"{'[OK]  ' if ok else '[FAIL]'} {text}")

    wf = _fake_when(SELF_FIXED_N)
    y = (rng.random(SELF_FIXED_N) < 0.5).astype(int)
    r = judge_a(wf, xf, y)
    check(not r.get("pass2", False) and r["verdict"] != "통과", f"Δ 와 승 무관 → ② 미달 (판정 {r['verdict']} · 상대 차 {r.get('rel', 0):+.2%})")
    y = (rng.random(SELF_FIXED_N) < predict(0.0, 0.5, xf)).astype(int)
    r = judge_a(wf, xf, y)
    check(r["verdict"] == "통과", f"강하게 관련(b = 0.5) → 통과 (판정 {r['verdict']} · 상대 차 {r.get('rel', 0):+.2%})")
    # ③ 단독 — 완벽 보정 n = 900 · 평균 5%p 밀림. 한 번 뽑기는 원래 확률적이라(5%p 밀림을 n 900 에서 약 80% 만 잡는다)
    # SELF_CALIB_SEEDS 시드 비율로 본다 — 시드가 고정이라 결과는 늘 같다(관문 검토 [상]1)
    xs = rng.normal(0, 4, 900)
    p = predict(0.0, 0.15, xs)
    rate = np.mean([calib(p, (np.random.default_rng(s).random(900) < p).astype(float), np.random.default_rng(s + 1))["pass"]
                    for s in range(SELF_CALIB_SEEDS)])
    check(rate >= SELF_CALIB_OK, f"완벽 보정 n = 900 → ③ 통과 {rate:.0%} ≥ {SELF_CALIB_OK:.0%}({SELF_CALIB_SEEDS}시드)")
    pb = np.clip(p + 0.05, 1e-6, 1 - 1e-6)
    rate = np.mean([not calib(pb, (np.random.default_rng(s).random(900) < p).astype(float), np.random.default_rng(s + 1))["pass"]
                    for s in range(SELF_CALIB_SEEDS)])
    check(rate >= SELF_SHIFT_CATCH, f"평균 5%p 밀림 n = 900 → ③ 미달 {rate:.0%} ≥ {SELF_SHIFT_CATCH:.0%}({SELF_CALIB_SEEDS}시드)")
    # 검정력 — 효과 있음(검증 상대 개선 2%) · 완벽 보정 · 그날 n · 시드 200 → ①~④ 를 모두 통과하는 비율
    b = b_for_gain(x, SELF_REL)
    p = predict(0.0, b, x)
    when = _fake_when(n)
    t0 = time.monotonic()
    passed, verdicts = 0, Counter()
    for s in range(seeds):
        ys = (np.random.default_rng(1000 + s).random(n) < p).astype(int)
        r = judge_a(when, x, ys, seed=SEED + s)
        verdicts[r["verdict"]] += 1
        passed += r["verdict"] == "통과"
    share = passed / seeds
    vs = " · ".join(f"{k} {v}" for k, v in sorted(verdicts.items()))
    fixed_bad = bad
    check(share >= SELF_PASS, f"검정력 — 효과 2%(b = {b:.3f}) · 완벽 보정 · n = {n:,} · {seeds}시드 → 통과 {share:.0%} ≥ {SELF_PASS:.0%} ({vs} · {time.monotonic() - t0:.0f}초)")
    if fixed_bad:
        log("→ 고정 시험 미달 = 규칙 버그: 측정 전에 측정 절을 고치고 새 커밋(RULE_COMMIT)")
        return SELF_FIXED_FAIL
    if bad:
        log("→ 검정력만 미달 = 이 n 이 모자란다: 측정일이면 그날은 \"재료 부족(검정력)\"")
        return SELF_POWER_FAIL
    return SELF_OK


# ── B 얼리기 · 판정 ──────────────────────────────────────────────────────────

def _season_key(ss, when: datetime):
    s = seasons_mod.season_of(ss, when)
    return None if s is None else (s.start, s.end)


def snapshot_list(rank) -> list[tuple[int, datetime]]:
    out = []
    for r in rank.execute("SELECT s.id, s.taken_at, m.ref_time FROM snapshots s LEFT JOIN snapshot_meta m ON m.snapshot_id = s.id"
                          " WHERE EXISTS (SELECT 1 FROM snapshot_rows w WHERE w.snapshot_id = s.id)"):
        try:
            out.append((r["id"], datetime.fromisoformat(rankcollect.data_time(r)[:19])))
        except (TypeError, ValueError):
            continue
    return out


def b_freeze(fifa, rank, frozen_path: Path, now: datetime) -> dict:
    """계정 경기마다 경기 시각에 가장 가까운 스냅숏(±PAIR_H · 같은 시즌)의 상대 팀컬러 표시를 얼린다.

    한 경기는 한 번만(이미 얼린 match_id 는 건너뛴다) · 경기 뒤 PAIR_H 가 지나야 얼린다(그 뒤에 올 더 가까운 스냅숏을 놓치지 않게).
    상대 = accounts 에 없는 쪽(두 쪽 다 계정이면 안 얼린다) · 닉네임은 맞대기에만 쓰고 안 남긴다. → 센 것."""
    # 한 번 본 경기는 얼리지 못했어도 다시 보지 않는다(관문 검토 [중]8: 다음 날 다시 보면 14일 보관으로 가장 가까운
    # 스냅숏이 지워져 둘째로 가까운 것과 짝지을 수 있다) — 경기 뒤 PAIR_H 가 지난 것만 보므로 판단이 바뀔 것이 없다
    skipped_path = frozen_path.with_name("b_skipped.jsonl")
    done = {fr.get("match_id") for fr in read_frozen(frozen_path)} | {fr.get("match_id") for fr in read_frozen(skipped_path)}
    skips: list[dict] = []
    snaps = snapshot_list(rank)
    stats: Counter = Counter()
    if not snaps:
        stats["no_snapshot"] += 1
        return dict(stats)
    ss = store.load_seasons(fifa)
    accs = account_ouids(fifa)
    lo = (min(t for _i, t in snaps) - timedelta(hours=PAIR_H)).isoformat()
    hi = (now - timedelta(hours=PAIR_H)).isoformat()
    people: dict[int, dict] = {}
    lines, dts = [], []

    def skip(mid, why):
        stats[why] += 1
        skips.append({"match_id": mid, "why": why})
        done.add(mid)
    for mid, at, d in account_matches(fifa, lo, hi):
        if mid in done:
            continue
        try:
            when = datetime.fromisoformat(at[:19])
        except ValueError:
            continue
        sid, t = min(snaps, key=lambda s: (abs((s[1] - when).total_seconds()), s[0]))
        dt_h = (t - when).total_seconds() / 3600
        if abs(dt_h) > PAIR_H:
            skip(mid, "far")
            continue
        if _season_key(ss, when) != _season_key(ss, t):
            skip(mid, "season")
            continue
        opp = [s for s in sides(d) if str(s.get("ouid")) not in accs]
        if len(opp) != 1:
            skip(mid, "both_accounts")
            continue
        lineup = cardstats.starters(opp[0])
        if len(lineup) != 11:
            skip(mid, "lineup")
            continue
        if sid not in people:
            people[sid] = {r["nickname"]: (r["team_color"] or "", r["color_count"]) for r in rank.execute(
                "SELECT nickname, team_color, color_count FROM snapshot_rows WHERE snapshot_id = ?", (sid,))}
        hit = people[sid].get(opp[0].get("nickname"))
        if hit is None:
            skip(mid, "not_in_rank")
            continue
        lines.append({"match_id": mid, "match_date": at, "snap_at": t.isoformat(), "dt_h": round(dt_h, 2),
                      "team_color": hit[0], "color_count": hit[1],
                      "opp": [[p.get("spId"), p.get("spGrade"), p.get("spPosition")] for p in lineup]})
        dts.append(abs(dt_h))
        done.add(mid)
    if lines:
        frozen_path.parent.mkdir(parents=True, exist_ok=True)
        with open(frozen_path, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    if skips:
        skipped_path.parent.mkdir(parents=True, exist_ok=True)
        with open(skipped_path, "a", encoding="utf-8") as f:
            for ln in skips:
                f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    stats["frozen"] = len(lines)
    if dts:
        stats["dt_h_p50"], stats["dt_h_p90"] = float(np.percentile(dts, 50)), float(np.percentile(dts, 90))
    return dict(stats)


def _tc_lists(ex: dict | None) -> tuple[list[int], list[int]] | None:
    """card_extra 줄 → (소속 팀컬러 id, 관계 팀컬러 id). 시뮬레이터를 안 읽었으면 None."""
    if not ex or not ex.get("sim_on"):
        return None
    out = []
    for col in ("club_tc", "rel_tc"):
        try:
            items = json.loads(ex.get(col) or "[]")
        except ValueError:
            return None
        out.append([it[0] for it in items if isinstance(it, list) and it and isinstance(it[0], int)])
    return out[0], out[1]


def _greedy(pool: dict[int, set[int]], need: dict[int, list[int]]) -> list[tuple[int, int, int]]:
    """카드 하나는 팀컬러 하나에만 — 적용 인원 많은 것부터(같으면 단계 높은 것 · 같으면 id 작은 것) 차례로 카드를 가져간다.
    pool: 카드 → 그 카드가 걸 수 있는 팀컬러 id · need: id → 단계별 최소 인원(오름차순). → [(id, 인원, 단계)]."""
    left = {c: set(ts) for c, ts in pool.items()}
    out = []
    while True:
        cnt: Counter = Counter()
        for ts in left.values():
            cnt.update(ts)
        best = None
        for tid, m in cnt.items():
            steps = need.get(tid)
            if not steps:
                continue
            step = sum(1 for s in steps if m >= s)
            if step and (best is None or (m, step, -tid) > (best[1], best[2], -best[0])):
                best = (tid, m, step)
        if best is None:
            return out
        out.append(best)
        left = {c: ts for c, ts in left.items() if best[0] not in ts}


def active_r1(cards: list[tuple[list[int], list[int]]], need) -> list[tuple[int, int, int]]:
    """R1 — 카드 하나는 (종류와 무관하게) 팀컬러 하나에만. 강화 팀컬러는 넣지 않는다(9판 — P4)."""
    return _greedy({i: set(c) | set(r) for i, (c, r) in enumerate(cards)}, need)


# 9판 — P3: 8판의 R2(종류마다 하나)는 뺐다 — 탐욕 배정의 첫 선택이 R1 과 늘 같아 주 팀컬러만 보는 B 로는 못 가른다.
# 앞 절반 미달 뒤 고친 규칙은 여기에 넣고 커밋한 뒤 --b-retry <이름>.
RULES = {"R1": active_r1}


def main_tc(active: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    return max(active, key=lambda t: (t[1], t[2], -t[0])) if active else None


def b_material(fifa) -> tuple[dict[int, dict], dict[int, list[int]], dict[int, str]]:
    ext = extras_of(fifa)
    need: dict[int, list[int]] = {}
    for tid, members in fifa.execute("SELECT id, members FROM teamcolor_steps ORDER BY id, step"):
        if isinstance(members, int):
            need.setdefault(tid, []).append(members)
    names = {r[0]: r[1] for r in fifa.execute("SELECT id, name FROM teamcolor_meta")}
    return ext, {k: sorted(v) for k, v in need.items()}, names


def b_eval(frozen: list[dict], ext, need, names, rule: str) -> dict:
    """한 묶음(절반)에서 규칙 하나 — 주 팀컬러 이름 일치율 · 뺀 이유 · 인원 차(보고만)."""
    st: Counter = Counter()
    agree = n = ties = 0
    count_diff = []
    fn = RULES[rule]
    for fr in frozen:
        cards = [_tc_lists(ext.get(int(s))) for s, _g, _po in fr.get("opp") or []]
        if len(cards) != 11 or any(c is None for c in cards):
            st["tc_unknown"] += 1
            continue
        seen = Counter(t for c, r in cards for t in set(c) | set(r))
        if any(t not in need and t in names and k >= 3 for t, k in seen.items()):
            st["steps_unknown"] += 1   # 단계를 모르는 팀컬러가 3장 이상 — 주 팀컬러가 그것일 수 있다
            continue
        snap = teamcolor.name_of(fr.get("team_color") or "")
        if not snap:
            st["snap_empty"] += 1
            continue
        act = fn(cards, need)
        top = main_tc(act)
        n += 1
        if top is None:
            st["none_active"] += 1
            continue
        if sum(1 for t in act if (t[1], t[2]) == (top[1], top[2])) > 1:
            ties += 1
        agree += names.get(top[0], "") == snap
        if isinstance(fr.get("color_count"), int):
            count_diff.append(top[1] - fr["color_count"])
    return {"rule": rule, "n": n, "agree": agree, "rate": agree / n if n else None, "ties": ties,
            "dropped": dict(st), "count_diff_mean": float(np.mean(count_diff)) if count_diff else None}


def judge_b(frozen: list[dict], ext, need, names, *, retry_rule: str | None = None) -> dict:
    front, back = halves(frozen)
    out: dict = {"n_frozen": len(frozen)}
    if retry_rule is not None:   # 앞 절반에서 둘 다 미달 → 규칙을 한 번 고친 뒤 뒤 절반에서 한 번
        rb = b_eval(back, ext, need, names, retry_rule)
        out["back"] = rb
        ok_n = rb["n"] >= B_MIN_HALF
        out["verdict"] = "재료 부족" if not ok_n else ("통과" if rb["rate"] >= B_AGREE else "미달")
        return out
    pick = "R1"
    fr = {pick: b_eval(front, ext, need, names, pick)}
    out["front"] = fr
    out["pick"] = pick
    if fr[pick]["n"] < B_MIN_HALF:
        out["verdict"] = "재료 부족"
        return out
    if (fr[pick]["rate"] or 0) < B_AGREE:
        out["verdict"] = "앞 절반 미달"   # 규칙을 고치고 --b-retry <규칙> 로 뒤 절반 한 번
        return out
    rb = b_eval(back, ext, need, names, pick)
    out["back"] = rb
    out["verdict"] = "재료 부족" if rb["n"] < B_MIN_HALF else ("통과" if rb["rate"] >= B_AGREE else "미달")
    return out


# ── C 세기 ──────────────────────────────────────────────────────────────────

def count_c(fifa, d0: date) -> list[dict]:
    """계정마다 — 그 계정의 [D0 − 30, D0) 경기 양쪽 선발에 든 카드만 아는 것으로 보고 N21·C3 칸을 센다(이름 없이 경기 수 순).

    N21(9판 — P5): 승부차기 경기의 상대 GK 값(키 · 실효 OVR · 급여)을 그 계정 경기들의 중앙값으로 둘로 · 키의 두 칸 모두
    C_MIN 이면 통과. 내 GK 는 안 본다."""
    ext = extras_of(fifa)
    salary = {r[0]: r[1] for r in fifa.execute("SELECT spid, salary FROM card_info WHERE salary IS NOT NULL")}
    lo, hi = (d0 - timedelta(days=C_WINDOW_DAYS)).isoformat(), d0.isoformat()
    out = []
    for ouid in account_ouids(fifa):
        known: set[int] = set()
        for _m, _a, d in account_matches(fifa, lo, hi, ouid=ouid):
            for s in sides(d):
                known.update(p.get("spId") for p in cardstats.starters(s))
        allm = account_matches(fifa, ouid=ouid)
        opp_gk: dict[str, list[int]] = {"height": [], "ovr": [], "salary": []}
        cells: Counter = Counter()
        for _mid, _at, d in allm:
            ss = sides(d)
            me = next((s for s in ss if s.get("ouid") == ouid), None)
            op = next((s for s in ss if s.get("ouid") != ouid), None)
            if me is None or op is None:
                continue
            if any(_shootout(s) for s in ss):
                g = next((p for p in cardstats.starters(op) if p.get("spPosition") == 0), None)
                sp = g.get("spId") if g else None
                if sp in known:
                    ex = ext.get(sp)
                    if ex and isinstance(ex.get("height"), int):
                        opp_gk["height"].append(ex["height"])
                    v, _w = cardstats.effective_ovr(0, g.get("spGrade"), ex)
                    if v is not None:
                        opp_gk["ovr"].append(v)
                    if isinstance(salary.get(sp), int):
                        opp_gk["salary"].append(salary[sp])
            here = set()
            for p in cardstats.starters(me):
                ex = ext.get(p.get("spId")) if p.get("spId") in known else None
                if not ex or not isinstance(ex.get("height"), int):
                    continue
                v, _w = cardstats.effective_ovr(p.get("spPosition"), p.get("spGrade"), ex)
                if v is not None:
                    here.add((v // C3_OVR_BIN, ex["height"] // C3_H_BIN))
            cells.update(here)
        by_ovr: dict[int, list[int]] = {}
        for (ob, _hb), k in cells.items():
            if k >= C_MIN:
                by_ovr.setdefault(ob, []).append(k)
        c3_bins = sorted(ob * C3_OVR_BIN for ob, ks in by_ovr.items() if len(ks) >= 2)
        split = {k: _median_split(v) for k, v in opp_gk.items()}
        out.append({"matches": len(allm), "known_cards": len(known), "n21": split, "n21_pass": split["height"]["pass"],
                    "c3_ovr_bins": c3_bins, "c3_pass": bool(c3_bins)})
    out.sort(key=lambda r: -r["matches"])
    return out


def _median_split(vals: list[int]) -> dict:
    """상대 GK 값을 중앙값으로 둘로 → {median, bins: [이하, 초과] 경기 수, pass: 두 칸 모두 C_MIN}."""
    if not vals:
        return {"median": None, "bins": [0, 0], "pass": False}
    m = float(np.median(vals))
    bins = [sum(v <= m for v in vals), sum(v > m for v in vals)]
    return {"median": m, "bins": bins, "pass": min(bins) >= C_MIN}


# ── 백필 ────────────────────────────────────────────────────────────────────

class _Progress:
    """상태줄 한 줄(전역 규칙 — bg-progress.d/<이름>-<PID>.txt). 끝나면 지운다."""

    def __init__(self):
        self.path = PROGRESS_DIR / f"measure-cards-{os.getpid()}.txt"
        self.t0 = time.monotonic()

    def set(self, msg: str) -> None:
        try:
            PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"카드 측정 백필 · {msg} · 경과 {int((time.monotonic() - self.t0) // 60)}분\n", encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def app_toggle_on() -> bool:
    """앱 토글이 켜졌나(디스크 .env) — 못 읽으면 켜진 것으로 본다(같은 IP 1,200 을 만들지 않는 쪽)."""
    if config.read_card_backfill() is None:
        return True
    return bool(config.CARD_BACKFILL_ON)


def backfill(fifa_path, d: Path, today: date, cap: int, *, run=cardcollect.run, log=print, progress=None) -> int:
    p = _paths(d)
    meta = load_json(p["meta"])
    k = day_no(meta, today)
    if k is None:
        log("[FAIL] D0 가 없다 — D0 전 관문 검토가 끝난 뒤 --start")
        return 2
    if not 0 <= k <= JUDGE_DAY:
        log(f"[FAIL] 오늘은 D0+{k} — 측정 백필은 D0 ~ D0+{JUDGE_DAY}")
        return 2
    config.read_env_switches()
    if app_toggle_on():
        log("[FAIL] 앱의 '카드 정보 자동 채우기' 가 켜져 있다 — 측정 35일 동안은 끈다(같은 IP 하루 1,200)")
        return 2
    if not config.WEB_DATA:
        log("[FAIL] 웹 데이터가 꺼져 있다")
        return 2
    d0 = d0_of(meta)
    fconn = _ro(fifa_path)
    try:
        window = window_cards(fconn, d0, read_frozen(p["frozen"]))
    finally:
        fconn.close()
    log(f"측정 수요 {len(window):,}장(B 얼린 경기 상대 먼저 → W_A 등장 순)")
    total, why = 0, "다 함"
    while True:
        r = run(fifa_path, today, cap, kind=store.BUDGET_CARD_DEV, window=window)
        total += r.requests
        if progress:
            progress.set(f"오늘 {total}/{cap} 요청")
        if r.blocked:
            why = "막힘(오늘 다른 웹 길 포함)"
        elif r.format_err:
            why = "형식 바뀜 — 오늘 멈춤"
        elif r.down:
            why = "점검·연결 실패"
        elif r.error:
            why = f"DB {r.error}"
        elif r.requests and r.skipped_cap:
            continue   # 틱 상한 — 다음 틱
        elif r.skipped_cap:
            why = "하루 상한"
        break
    log(f"요청 {total} · 끝: {why}")
    fconn = _ro(fifa_path)
    try:
        c = count_line(fconn, d0, read_frozen(p["frozen"]), today)
    finally:
        fconn.close()
    c.update(requests=total, end=why)
    with open(p["daily"], "a", encoding="utf-8") as f:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
    log(show_count(c))
    return 0


# ── 명령 ────────────────────────────────────────────────────────────────────

def _pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def cmd_a(fifa_path, d: Path, today: date, log=print) -> int:
    p = _paths(d)
    meta = load_json(p["meta"])
    k = day_no(meta, today)
    res = load_json(p["a"]) or {"attempts": []}
    if res.get("final"):
        log(f"이미 끝났다: {res['final']}")
        return 0
    if k is None:
        log("[FAIL] D0 가 없다")
        return 2
    # 놓친 회차(측정일 + A_GRACE 를 넘김)는 "재료 부족(놓침)" 으로 채운다 — 재는 날을 고르지 못하게(관문 검토 [중]2)
    missed = 0
    while len(res["attempts"]) < len(A_DAYS) and k > A_DAYS[len(res["attempts"])] + A_GRACE:
        res["attempts"].append({"verdict": "재료 부족", "why": "놓침", "day_no": A_DAYS[len(res["attempts"])], "date": None})
        missed += 1
    i = len(res["attempts"])
    if i >= len(A_DAYS):
        res["final"] = "미달(재료 부족 세 번)"
        save_json(p["a"], res)
        log(f"[FAIL] 측정일을 다 놓쳤다 — {res['final']}")
        return 2
    if missed:
        save_json(p["a"], res)
    if k < A_DAYS[i]:
        log(f"[FAIL] 측정 A {i + 1}번째는 D0+{A_DAYS[i]} ~ D0+{A_DAYS[i] + A_GRACE}(오늘 D0+{k})")
        return 2
    fconn = _ro(fifa_path)
    try:
        rows = a_rows(fconn, d0_of(meta))
        audit = audit_of(fconn, meta, today)
    finally:
        fconn.close()
    log(f"W_A 경기 {rows.total:,} · 22장 다 아는 경기 {len(rows.y):,} · 뺀 것 {dict(rows.dropped)}")
    log("── selftest(고정 시험 + 그날 n·Δ 검정력) ──")
    st = selftest(rows.delta, log=log)
    if st == SELF_FIXED_FAIL:   # 고정 시험은 결정적 — 미달이면 코드가 D0 뒤 바뀐 것. 회차는 쓴 것으로 친다(다시 돌려 고르지 못하게)
        r = {"verdict": "재료 부족", "why": "selftest 고정 시험 미달(규칙 버그 — 판정 없음)", "n": len(rows.y)}
    elif st == SELF_POWER_FAIL:   # 9판 — P6: 기록하고 다음 측정일로(기록 없이 멈추면 다음 날 다시 돌 수 있다)
        r = {"verdict": "재료 부족", "why": "selftest 검정력 미달", "n": len(rows.y)}
    else:
        r = judge_a(rows.when, rows.delta, rows.y)
    r.update(audit)
    r.update(day_no=k, date=today.isoformat(), rule_commit=RULE_COMMIT,
             dropped=dict(rows.dropped), dropped_season={f"{a}:{b}": v for (a, b), v in rows.dropped_season.items()},
             lag_days=[float(x) for x in np.percentile(rows.lag_days, [0, 50, 90, 100])] if rows.lag_days else None,
             deciles=deciles(rows.delta, rows.y) if rows.y else None)
    acc = [(s * dl, y if s > 0 else 1 - y) for dl, y, s in zip(rows.delta, rows.y, rows.acc_sign) if s]
    if acc and "a" in r:
        pa = predict(r["a"], r["b"], [t[0] for t in acc])
        ya = np.array([t[1] for t in acc], dtype=float)
        r["acc_ece"] = float(ece_of(pa, ya, np.argsort(pa, kind="stable"))[0])
    res["attempts"].append(r)
    if r["verdict"] != "재료 부족" or i + 1 == len(A_DAYS):
        res["final"] = r["verdict"] if r["verdict"] != "재료 부족" else "미달(재료 부족 세 번)"
    save_json(p["a"], res)
    log(f"판정: {r['verdict']}" + (f" · 상대 차 {r['rel']:+.2%} [{r['ci'][0]:+.2%}, {r['ci'][1]:+.2%}]" if "rel" in r else ""))
    if res.get("final") == "통과":
        log(f"config: C1_LOGIT_A = {r['a']:.6f} · C1_LOGIT_B = {r['b']:.6f}  # 기준 커밋 {RULE_COMMIT} · W_A · 경기 {r['n']:,}")
    return 1 if st == SELF_FIXED_FAIL else 0


def cmd_b(fifa_path, d: Path, today: date, retry: str | None = None, log=print) -> int:
    p = _paths(d)
    meta = load_json(p["meta"])
    k = day_no(meta, today)
    if k is None or k < JUDGE_DAY:
        log(f"[FAIL] 측정 B 는 D0+{JUDGE_DAY}(오늘 D0+{k})")
        return 2
    old = load_json(p["b"])
    if retry is None and old is not None:
        log(f"이미 쟀다: {old.get('verdict')}")
        return 0
    if retry is None and k > JUDGE_DAY + A_GRACE:
        save_json(p["b"], {"verdict": "재료 부족", "why": "놓침", "day_no": k, "date": today.isoformat(), "rule_commit": RULE_COMMIT})
        log(f"[FAIL] 측정 B 는 D0+{JUDGE_DAY} ~ D0+{JUDGE_DAY + A_GRACE} — 놓쳐서 재료 부족으로 기록")
        return 2
    if retry is not None and (old is None or old.get("verdict") != "앞 절반 미달" or old.get("retry")):
        log("[FAIL] --b-retry 는 앞 절반 미달 뒤 한 번만")
        return 2
    if retry is not None and retry not in RULES:
        log(f"[FAIL] 모르는 규칙 {retry} — RULES 에 먼저 넣고 커밋")
        return 2
    fconn = _ro(fifa_path)
    try:
        r = judge_b(read_frozen(p["frozen"]), *b_material(fconn), retry_rule=retry)
        r.update(audit_of(fconn, meta, today))
    finally:
        fconn.close()
    r.update(day_no=k, date=today.isoformat(), rule_commit=RULE_COMMIT)
    if retry is not None:
        old["retry"] = r
        old["verdict_final"] = r["verdict"]
        save_json(p["b"], old)
    else:
        save_json(p["b"], r)
    for half, evals in (("front", list((r.get("front") or {}).values())), ("back", [r["back"]] if r.get("back") else [])):
        for e in evals:
            log(f"{half} {e['rule']}: 일치 {e['agree']}/{e['n']} ({_pct(e['rate'])}) · 동률 {e['ties']} · 뺀 것 {e['dropped']}")
    log(f"판정: {r['verdict']}")
    return 0


def cmd_c(fifa_path, d: Path, today: date, log=print) -> int:
    p = _paths(d)
    meta = load_json(p["meta"])
    k = day_no(meta, today)
    if k is None or k < JUDGE_DAY:
        log(f"[FAIL] 측정 C 는 D0+{JUDGE_DAY}(오늘 D0+{k})")
        return 2
    if load_json(p["c"]) is not None:
        log("이미 셌다")
        return 0
    if k > JUDGE_DAY + A_GRACE:   # 그 뒤엔 앱 백필이 카드를 더 채울 수 있다 — 세는 날을 고르지 못하게
        save_json(p["c"], {"day_no": k, "date": today.isoformat(), "rule_commit": RULE_COMMIT, "why": "놓침",
                           "n21": False, "c3": False})
        log(f"[FAIL] 측정 C 는 D0+{JUDGE_DAY} ~ D0+{JUDGE_DAY + A_GRACE} — 놓쳐서 N21·C3 은 뺀 것으로 기록")
        return 2
    fconn = _ro(fifa_path)
    try:
        rows = count_c(fconn, d0_of(meta))
        audit = audit_of(fconn, meta, today)
    finally:
        fconn.close()
    save_json(p["c"], {"day_no": k, "date": today.isoformat(), "rule_commit": RULE_COMMIT, "accounts": rows, **audit,
                       "n21": any(r["n21_pass"] for r in rows),
                       "n21_lines": {v: any(r["n21"][v]["pass"] for r in rows) for v in ("height", "ovr", "salary")},
                       "c3": any(r["c3_pass"] for r in rows)})
    for i, r in enumerate(rows, 1):
        sp = " · ".join(f"{v} 중앙 {s['median']} {s['bins']}{' ✔' if s['pass'] else ''}" for v, s in r["n21"].items())
        log(f"계정 {i}: 경기 {r['matches']:,} · N21 상대 GK({sp}) · C3 OVR 칸 {r['c3_ovr_bins'] or '없음'}")
    return 0


def cmd_status(d: Path, today: date, log=print) -> int:
    p = _paths(d)
    meta = load_json(p["meta"])
    if meta is None:
        log(f"D0 없음 — 관문 검토 뒤 --start · 판정 규칙 기준 커밋 {RULE_COMMIT}")
        return 0
    k = day_no(meta, today)
    log(f"D0 = {meta['d0']} · 오늘 D0+{k} · 규칙 {meta.get('rule_commit')}")
    todo = []
    if 0 <= k <= JUDGE_DAY:
        todo.append("--backfill")
    if 0 <= k <= FREEZE_LAST:
        todo.append("--b-freeze")
    a = load_json(p["a"]) or {"attempts": []}
    if not a.get("final") and len(a["attempts"]) < len(A_DAYS) and k >= A_DAYS[len(a["attempts"])]:
        todo.append("--a")
    if k >= JUDGE_DAY:
        todo += [c for c, f in (("--b", "b"), ("--c", "c")) if load_json(p[f]) is None]
    log(f"오늘 할 일: {' '.join(todo) or '없음'} · 얼린 경기 {len(read_frozen(p['frozen'])):,}")
    for f in ("a", "b", "c"):
        v = load_json(p[f])
        if v is not None:
            log(f"{f.upper()}: {v.get('final') or v.get('verdict_final') or v.get('verdict') or ''}")
    try:
        last = p["daily"].read_text(encoding="utf-8").strip().splitlines()[-1]
        log("마지막 세기: " + show_count(json.loads(last)))
    except (OSError, IndexError, ValueError):
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="51단계 측정 A~C(이 PC 전용)")
    g = ap.add_mutually_exclusive_group(required=True)
    for flag, text in (("--start", "오늘을 D0 로(한 번)"), ("--backfill", "측정 백필 하루치"), ("--b-freeze", "B 얼리기"),
                       ("--count", "매일 세기만"), ("--a", "측정 A"), ("--b", "측정 B"), ("--c", "측정 C"),
                       ("--selftest", "A 판정 규칙 자체 시험"), ("--status", "현황")):
        g.add_argument(flag, action="store_true", help=text)
    g.add_argument("--b-retry", metavar="규칙", help="앞 절반 미달 뒤 고친 규칙으로 뒤 절반 한 번")
    ap.add_argument("--cap", type=int, default=config.CARD_BACKFILL_DAILY_REQ, help="--backfill 하루 상한(기본 600)")
    a = ap.parse_args(argv)
    today, d = date.today(), MEASURE_DIR
    if a.selftest:
        meta = load_json(_paths(d)["meta"])
        delta = None
        if d0_of(meta) is not None:
            fconn = _ro(config.DB_PATH)
            try:
                delta = a_rows(fconn, d0_of(meta)).delta
            finally:
                fconn.close()
        return selftest(delta)
    if a.status:
        return cmd_status(d, today)
    if a.cap > CAP_MAX:
        print(f"[FAIL] --cap 은 {CAP_MAX} 까지(규칙의 하루 상한)")
        return 2
    if a.start:
        if load_json(_paths(d)["meta"]) is None and selftest() == SELF_FIXED_FAIL:
            print("[FAIL] selftest 고정 시험 미달 — D0 를 잡지 않는다")
            return 2
        ok, text = start(d, today)
        print(("" if ok else "[FAIL] ") + text)
        return 0 if ok else 2
    if a.backfill:
        pr = _Progress()
        try:
            return backfill(config.DB_PATH, d, today, a.cap, progress=pr)
        finally:
            pr.clear()
    meta = load_json(_paths(d)["meta"])
    if d0_of(meta) is None:
        print("[FAIL] D0 가 없다 — --start 먼저")
        return 2
    if a.count:
        fconn = _ro(config.DB_PATH)
        try:
            print(show_count(count_line(fconn, d0_of(meta), read_frozen(_paths(d)["frozen"]), today)))
        finally:
            fconn.close()
        return 0
    if a.b_freeze:
        k = day_no(meta, today)
        if not 0 <= k <= FREEZE_LAST:
            print(f"[FAIL] 얼리기는 D0 ~ D0+{FREEZE_LAST}(오늘 D0+{k})")
            return 2
        fconn, rconn = _ro(config.DB_PATH), _ro(config.RANK_DB_PATH)
        try:
            print(b_freeze(fconn, rconn, _paths(d)["frozen"], datetime.now()))
        finally:
            fconn.close()
            rconn.close()
        return 0
    if a.a:
        return cmd_a(config.DB_PATH, d, today)
    if a.b or a.b_retry:
        return cmd_b(config.DB_PATH, d, today, retry=a.b_retry)
    return cmd_c(config.DB_PATH, d, today)


if __name__ == "__main__":
    raise SystemExit(main())
