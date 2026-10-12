"""43단계 측정 2 — xG 후보 넷(C0·C0r·C2·C1)을 판정 규칙대로 비교한다(이 PC 전용 · exe 밖).

판정 규칙은 ROADMAP "43단계 › 측정 2 — 판정 규칙" (시작 커밋 4f026fb) 이 정본이다. 여기 상수는 그 절을 옮긴 것 —
**결과를 보고 여기 숫자를 바꾸지 않는다**(바꾸려면 그 절을 먼저 고치는 커밋).

    python tools/fit_xg.py              # 실제 DB(읽기 전용)로 판정 보고
    python tools/fit_xg.py --selftest   # 지어낸 슛으로 고르는 규칙이 맞게 고르는지
    python tools/fit_xg.py --write      # 판정한 C1o 표 둘을 xg_grid.py 로(안에서 대조 · 어긋나면 안 씀)

numpy 를 쓴다(이 PC 에만 있으면 된다 — 앱은 결과 표만 읽는다). 원본 슛·닉네임은 아무 파일에도 안 남긴다.
C0 는 앱의 `stats.shot_xg` 가 아니라 **2.9.1 전 식을 얼린 사본**(`c0_frozen`)이다 — 앱이 격자로 바뀐 뒤 다시 돌려도
측정 2~2d 의 판정이 재현되게.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import types
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import xgmodel  # noqa: E402

# ── 판정 규칙(ROADMAP 43 측정 2 — 시작 커밋 4f026fb) ─────────────────────────
MATCH_TYPE = 52
PK_TYPE = 9
TRAIN_SHARE = 0.70
SEED = 20261012
BOOT = 1000
REL_GAIN = 0.01          # 더 복잡한 후보가 이어받으려면 로그손실이 상대 1% 이상 낮아야
ECE_MAX = 0.02
MEAN_DIFF_MAX = 0.01
TYPE_DIFF_MAX = 0.05
TYPE_MIN_TEST = 1000     # 종류별 보정 검사를 하는 최소 검증 슛
RECENT_DAYS = 14
GRID_DIST_M, GRID_WID_M = 5.0, 5.0
GRID_DIST_MAX, GRID_WID_MAX = 60.0, 30.0
GRID_K = 30.0
RARE_TYPE_SHARE = 0.01
FINISH_GAP = 25.0        # analysis.FINISH_GAP 과 같은 값(보고용)
FINISH_MIN_SHOTS = 60    # analysis.MIN_SHOTS 와 같은 값(보고용)
EPS = 1e-6
JUDGED_COMMIT = "3554fa7"   # 측정 2d 결과 — C1o 둘을 싣기로 한 판정(xg_grid 머리말에 적는다)
GRID_OUT = ROOT / "xg_grid.py"


# ── 재료 ──────────────────────────────────────────────────────────────────
def c0_frozen(x: float, y: float, in_penalty: bool = False, type_name: str = "") -> float:
    """2.9.1 전 `stats.shot_xg` 를 그대로 얼린 사본(측정 2 의 C0). 앱 쪽 식이 바뀌어도 여기는 안 바꾼다."""
    if type_name == "페널티킥":
        return 0.76
    dx = max(0.01, (1.0 - x) * 105.0)
    dy = abs(y - 0.5) * 68.0
    dist = math.hypot(dx, dy)
    denom = dx * dx + dy * dy - (7.32 / 2) ** 2
    angle = math.atan2(7.32 * dx, denom)
    if angle < 0:
        angle += math.pi
    z = -0.15 + 0.115 * dist - 3.2 * angle
    xg = 1.0 / (1.0 + math.exp(z))
    if type_name == "헤더":
        xg *= 0.7
    return max(0.0, min(0.99, xg))



def _geom(x: float, y: float) -> tuple[float, float, float, float]:
    """stats.shot_xg 와 같은 거리·각도 — (골라인 거리 m, 중앙 폭 m, 직선 거리, 골대 각)."""
    dx = max(0.01, (1.0 - x) * 105.0)
    dy = abs(y - 0.5) * 68.0
    denom = dx * dx + dy * dy - (7.32 / 2) ** 2
    ang = math.atan2(7.32 * dx, denom)
    if ang < 0:
        ang += math.pi
    return dx, dy, math.hypot(dx, dy), ang


def load_shots(db_path) -> tuple[dict, list]:
    """감독모드 경기를 match_id 마다 한 번 — 슛 열 배열(dict) + 경기 목록[(match_id, date, [ouid 둘])].
    페널티킥은 따로 표시만 하고 남긴다(비교에서 빼는 건 호출 쪽)."""
    conn = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = defaultdict(list)
    matches = []
    for r in conn.execute("SELECT match_id, match_date, payload FROM matches WHERE match_type = ?"
                          " ORDER BY match_date, match_id", (MATCH_TYPE,)):
        try:
            d = store._loads(r["payload"])
        except Exception:
            continue
        infos = d.get("matchInfo") or []
        mi = len(matches)
        matches.append((r["match_id"], r["match_date"], [p.get("ouid") for p in infos]))
        for side, p in enumerate(infos):
            for sd in p.get("shootDetail") or []:
                x, y, res = sd.get("x"), sd.get("y"), sd.get("result")
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    continue
                if res not in stats._SHOT_RESULTS:
                    continue
                t = sd.get("type")
                tname = stats.goal_type_name(t)
                in_pen = bool(sd.get("inPenalty"))
                ax, ay = sd.get("assistX"), sd.get("assistY")
                has_ast = bool(sd.get("assist")) and isinstance(ax, (int, float)) and isinstance(ay, (int, float))
                dx, dy, dist, ang = _geom(float(x), float(y))
                cols["match"].append(mi)
                cols["side"].append(side)
                cols["goal"].append(1.0 if res == stats.SHOT_GOAL else 0.0)
                cols["on_target"].append(res != stats.SHOT_OFF_TARGET)
                cols["type"].append(t if isinstance(t, int) else -1)
                cols["in_pen"].append(1.0 if in_pen else 0.0)
                cols["assist"].append(1.0 if has_ast else 0.0)
                cols["x"].append(float(x))
                cols["y"].append(float(y))
                cols["dx"].append(dx)
                cols["dy"].append(dy)
                cols["dist"].append(dist)
                cols["ang"].append(ang)
                cols["c0"].append(c0_frozen(float(x), float(y), in_pen, tname))
                dv = p.get("division")
                cols["div"].append(int(dv) if isinstance(dv, int) else -1)       # 슈터 등급(측정 2b 보고)
                sp = sd.get("spId")
                cols["spid"].append(int(sp) if isinstance(sp, int) else -1)      # 카드별 xG 차(측정 2b 보고)
    conn.close()
    S = {k: np.asarray(v) for k, v in cols.items()}
    S["date"] = np.asarray([matches[i][1] for i in S["match"]]) if len(S.get("match", [])) else np.asarray([])
    return S, matches


# ── 후보 ──────────────────────────────────────────────────────────────────
def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def fit_logistic(X: np.ndarray, y: np.ndarray, iters: int = 50) -> np.ndarray:
    """뉴턴(IRLS) — 아주 작은 능선(1e-6)만 둬서 특이 행렬을 피한다."""
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ w
        p = 1.0 / (1.0 + np.exp(-z))
        g = X.T @ (y - p)
        W = p * (1 - p)
        H = (X * W[:, None]).T @ X + 1e-6 * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        w += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def _sig(z):
    return 1.0 / (1.0 + np.exp(-z))


class C0:
    name = "C0"

    def fit(self, S, idx):
        return self

    def predict(self, S, idx):
        return np.clip(S["c0"][idx], EPS, 1 - EPS)


class C0r:
    name = "C0r"

    def fit(self, S, idx):
        X = np.column_stack([np.ones(len(idx)), _logit(S["c0"][idx])])
        self.w = fit_logistic(X, S["goal"][idx])
        return self

    def predict(self, S, idx):
        X = np.column_stack([np.ones(len(idx)), _logit(S["c0"][idx])])
        return np.clip(_sig(X @ self.w), EPS, 1 - EPS)


def _type_groups(S, idx) -> list:
    """학습 구간 슛의 1% 이상인 종류만 따로 — 나머지는 '기타'."""
    t = S["type"][idx]
    vals, cnt = np.unique(t, return_counts=True)
    return sorted(int(v) for v, c in zip(vals, cnt) if c / len(idx) >= RARE_TYPE_SHARE)


def _type_code(S, idx, groups) -> np.ndarray:
    t = S["type"][idx]
    out = np.full(len(idx), len(groups))     # 기타
    for i, g in enumerate(groups):
        out[t == g] = i
    return out


class C2:
    name = "C2"

    def _X(self, S, idx):
        code = _type_code(S, idx, self.groups)
        dummies = [(code == i).astype(float) for i in range(1, len(self.groups) + 1)]   # 첫 종류가 기준
        return np.column_stack([np.ones(len(idx)), S["dist"][idx], S["ang"][idx], S["in_pen"][idx],
                                S["assist"][idx], *dummies])

    def fit(self, S, idx):
        self.groups = _type_groups(S, idx)
        X = self._X(S, idx)
        # 거리·각도 크기를 맞춰 뉴턴이 안정적이게
        self.mu = X[:, 1:3].mean(axis=0)
        self.sd = X[:, 1:3].std(axis=0) + 1e-9
        X[:, 1:3] = (X[:, 1:3] - self.mu) / self.sd
        self.w = fit_logistic(X, S["goal"][idx])
        return self

    def predict(self, S, idx):
        X = self._X(S, idx)
        X[:, 1:3] = (X[:, 1:3] - self.mu) / self.sd
        return np.clip(_sig(X @ self.w), EPS, 1 - EPS)


class C1:
    name = "C1"

    def _keys(self, S, idx):
        di = np.minimum(S["dx"][idx] // GRID_DIST_M, GRID_DIST_MAX // GRID_DIST_M).astype(int)
        wi = np.minimum(S["dy"][idx] // GRID_WID_M, GRID_WID_MAX // GRID_WID_M).astype(int)
        ti = _type_code(S, idx, self.groups)
        return di, wi, ti

    def fit(self, S, idx, groups=None):
        """groups 를 안 주면 학습 슛(idx)에서 정한다 — C1o 는 내 계정을 뺀 idx 로 부르므로 그 idx 의 그룹이다."""
        self.groups = _type_groups(S, idx) if groups is None else list(groups)
        di, wi, ti = self._keys(S, idx)
        y = S["goal"][idx]
        self.root = float(y.mean())
        acc = {1: defaultdict(lambda: [0.0, 0]), 2: defaultdict(lambda: [0.0, 0]), 3: defaultdict(lambda: [0.0, 0])}
        for a, b, c, g in zip(di, wi, ti, y):
            for lv, key in ((1, (a,)), (2, (a, b)), (3, (a, b, c))):
                acc[lv][key][0] += g
                acc[lv][key][1] += 1
        self.v1 = {k: (g + GRID_K * self.root) / (n + GRID_K) for k, (g, n) in acc[1].items()}
        self.v2 = {k: (g + GRID_K * self.v1[k[:1]]) / (n + GRID_K) for k, (g, n) in acc[2].items()}
        self.v3 = {k: (g + GRID_K * self.v2[k[:2]]) / (n + GRID_K) for k, (g, n) in acc[3].items()}
        self.cells = len(self.v3)
        self.thin = sum(1 for k, (g, n) in acc[3].items() if n < GRID_K)
        self.n = int(len(idx))
        return self

    def predict(self, S, idx):
        di, wi, ti = self._keys(S, idx)
        out = np.empty(len(idx))
        for i, (a, b, c) in enumerate(zip(di, wi, ti)):
            a, b, c = int(a), int(b), int(c)
            v = self.v3.get((a, b, c))
            if v is None:
                v = self.v2.get((a, b))
                if v is None:
                    v = self.v1.get((a,), self.root)
            out[i] = v
        return np.clip(out, EPS, 1 - EPS)


class C1o(C1):
    """측정 2b — C1 과 같은 절차인데 학습 슛에서 내 계정(accounts 표)이 찬 슛을 뺀다(자기 참조 없애기)."""
    name = "C1o"

    def fit(self, S, idx):
        return super().fit(S, idx[~S["is_mine"][idx]])


# ── 지표 ──────────────────────────────────────────────────────────────────
def logloss_vec(y, p):
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def auc(y, p):
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # 같은 값은 평균 순위로
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = (sums / cnt)[inv]
    npos = y.sum()
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def ece(y, p, bins: int = 10) -> float:
    q = np.quantile(p, np.linspace(0, 1, bins + 1))
    b = np.clip(np.searchsorted(q, p, side="right") - 1, 0, bins - 1)
    tot = 0.0
    for i in range(bins):
        m = b == i
        if m.any():
            tot += m.sum() * abs(p[m].mean() - y[m].mean())
    return float(tot / len(y))


def metrics(S, idx, p) -> dict:
    y = S["goal"][idx]
    out = {"n": int(len(idx)), "logloss": float(logloss_vec(y, p).mean()), "brier": float(((p - y) ** 2).mean()),
           "auc": auc(y, p), "ece": ece(y, p), "mean_diff": float(abs(p.mean() - y.mean())), "types": {}}
    for t in np.unique(S["type"][idx]):
        m = S["type"][idx] == t
        if m.sum() >= TYPE_MIN_TEST:
            out["types"][int(t)] = (int(m.sum()), float(abs(p[m].mean() - y[m].mean())))
    return out


def calib_ok(m) -> tuple[bool, str]:
    why = []
    if m["ece"] > ECE_MAX:
        why.append(f"ECE {m['ece']*100:.2f}%p > {ECE_MAX*100:.0f}")
    if m["mean_diff"] > MEAN_DIFF_MAX:
        why.append(f"평균 차 {m['mean_diff']*100:.2f}%p > {MEAN_DIFF_MAX*100:.0f}")
    for t, (n, d) in m["types"].items():
        if d > TYPE_DIFF_MAX:
            why.append(f"종류 {t}({n}) 차 {d*100:.1f}%p > {TYPE_DIFF_MAX*100:.0f}")
    return (not why), " · ".join(why) or "통과"


def boot_rel_gain(S, idx, p_simple, p_complex, rng) -> tuple[float, float, float]:
    """경기 단위 부트스트랩 — (상대 개선 점추정, 95% 하한, 상한). 개선 = (L_simple − L_complex) / L_simple."""
    y = S["goal"][idx]
    ls, lc = logloss_vec(y, p_simple), logloss_vec(y, p_complex)
    mid = S["match"][idx]
    uniq, inv = np.unique(mid, return_inverse=True)
    sum_s = np.bincount(inv, weights=ls)
    sum_c = np.bincount(inv, weights=lc)
    cnt = np.bincount(inv).astype(float)
    point = (sum_s.sum() - sum_c.sum()) / sum_s.sum()
    k = len(uniq)
    gains = np.empty(BOOT)
    for b in range(BOOT):
        w = np.bincount(rng.integers(0, k, k), minlength=k).astype(float)
        s, c = (w * sum_s).sum(), (w * sum_c).sum()
        gains[b] = (s - c) / s
    lo, hi = np.quantile(gains, [0.025, 0.975])
    return float(point), float(lo), float(hi)


# ── 판정 ──────────────────────────────────────────────────────────────────
def judge(S, train, test, recent, order, start_note="", log=print) -> tuple[str, dict]:
    """규칙 1~3. order = 단순한 순서의 후보 객체. 첫 후보가 출발점(바닥)."""
    rng = np.random.default_rng(SEED)
    fitted = {c.name: c.fit(S, train) for c in order}
    pred = {n: m.predict(S, test) for n, m in fitted.items()}
    met = {n: metrics(S, test, p) for n, p in pred.items()}
    log(f"  {'후보':4} {'로그손실':>8} {'Brier':>7} {'AUC':>6} {'ECE%p':>6} {'평균차%p':>8}  보정")
    for n, m in met.items():
        ok, why = calib_ok(m)
        log(f"  {n:4} {m['logloss']:8.4f} {m['brier']:7.4f} {m['auc']:6.3f} {m['ece']*100:6.2f} {m['mean_diff']*100:8.2f}  {why}")
    chosen = order[0].name
    for c in order[1:]:
        g, lo, hi = boot_rel_gain(S, test, pred[chosen], pred[c.name], rng)
        ok_gain = g >= REL_GAIN and lo > 0
        ok_cal, why = calib_ok(met[c.name])
        verdict = "이어받음" if (ok_gain and ok_cal) else ("보정 문턱 미달 — " + why if ok_gain else "개선 부족")
        log(f"  [규칙1·2] {c.name} 대 {chosen}: 상대 개선 {g*100:+.2f}% [95% {lo*100:+.2f}, {hi*100:+.2f}] → {verdict}")
        if ok_gain and ok_cal:
            chosen = c.name
    # 규칙 3 — 최근 14일에서 C0r 보다 1% 넘게 나쁘면 C0r 로
    if recent is not None and len(recent) and chosen not in ("C0", "C0r") and "C0r" in fitted:
        lr = logloss_vec(S["goal"][recent], fitted[chosen].predict(S, recent)).mean()
        lb = logloss_vec(S["goal"][recent], fitted["C0r"].predict(S, recent)).mean()
        worse = (lr - lb) / lb
        log(f"  [규칙3] 최근 {RECENT_DAYS}일 {len(recent)}슛: {chosen} {lr:.4f} 대 C0r {lb:.4f} ({worse*100:+.2f}%)")
        if worse > REL_GAIN:
            chosen = "C0r"
            log("  → C0r 로 내려감")
    return chosen, {"fitted": fitted, "metrics": met}


def time_split(S, matches):
    k = len(matches)
    cut = int(k * TRAIN_SHARE)
    mi = S["match"]
    train_m = mi < cut
    last = datetime.fromisoformat(matches[-1][1])
    recent_from = (last - timedelta(days=RECENT_DAYS)).isoformat()
    test_m = ~train_m
    recent_m = test_m & (S["date"] >= recent_from)
    return train_m, test_m, recent_m, matches[cut][1] if cut < k else None


def finishing_flips(S, matches, model_name, fitted, accounts) -> str:
    """내 계정마다 결정력 문장 방향(+·−·없음)이 C0 대비 바뀌는가 — 보고만. 페널티킥 포함(문장과 같은 재료)."""
    pk = S["type"] == PK_TYPE
    allidx = np.arange(len(S["goal"]))
    p0 = S["c0"]
    if model_name == "C0":
        return "C0 그대로 — 바뀌는 계정 0"
    pn = np.array(p0, dtype=float)
    nonpk = allidx[~pk]
    pn[nonpk] = fitted[model_name].predict(S, nonpk)
    pk_rate = float(S["goal"][allidx[pk & (S["match"] < int(len(matches) * TRAIN_SHARE))]].mean()) if pk.any() else 0.76
    pn[pk] = pk_rate
    side_ouid = np.array([matches[m][2][s] if s < len(matches[m][2]) else None for m, s in zip(S["match"], S["side"])],
                         dtype=object)
    flips = same = 0
    rows = []
    for ou in accounts:
        m = side_ouid == ou
        n = int(m.sum())
        if n < FINISH_MIN_SHOTS:
            continue
        g = S["goal"][m].sum()

        def state(x):
            gap = (g - x) / x * 100 if x > 0 else 0
            return "+" if gap >= FINISH_GAP else ("-" if gap <= -FINISH_GAP else "·"), gap
        a, ga = state(p0[m].sum())
        b, gb = state(pn[m].sum())
        rows.append(f"{a}{ga:+.0f}%→{b}{gb:+.0f}%")
        if a != b:
            flips += 1
        else:
            same += 1
    return f"슛 {FINISH_MIN_SHOTS}개 넘는 내 계정 {flips + same}개 중 문장이 바뀌는 계정 {flips} — " + " · ".join(rows)


def run(db_path) -> None:
    t0 = datetime.now()
    S, matches = load_shots(db_path)
    print(f"[재료] 감독모드 경기 {len(matches):,} · 슛 {len(S['goal']):,} · 읽기 {(datetime.now()-t0).total_seconds():.0f}초")
    pk = S["type"] == PK_TYPE
    train_m, test_m, recent_m, cut_date = time_split(S, matches)
    print(f"[나누기] 시간순 앞 {TRAIN_SHARE:.0%} 학습 / 뒤 검증 — 검증 시작 {cut_date} · 최근 {RECENT_DAYS}일 = 검증의 끝")
    pk_rate = float(S["goal"][pk & train_m].mean()) if (pk & train_m).any() else float("nan")
    print(f"[페널티킥] 학습 구간 {int((pk & train_m).sum())}개 · 성공 {pk_rate:.1%}(C0 은 0.76 고정) — 비교에서 뺐다")
    idx = np.arange(len(S["goal"]))
    tr, te, rc = idx[train_m & ~pk], idx[test_m & ~pk], idx[recent_m & ~pk]
    print(f"  학습 {len(tr):,} · 검증 {len(te):,} · 최근 {len(rc):,} 슛")

    print("\n[A5 · 전체 슛] 판정 ①(시간)")
    chosen, info = judge(S, tr, te, rc, [C0(), C0r(), C2(), C1()])
    c1 = info["fitted"]["C1"]
    print(f"  격자 칸 {c1.cells} · 학습 슛 {int(GRID_K)}개 미만 칸 {c1.thin} (k 당김)")
    print(f"  ⇒ A5 고른 후보: {chosen}" + ("  → A5 를 뺀다" if chosen == "C0" else ""))

    print("\n[A4 · 유효슛만] 판정 ①(시간) — 출발점 C0r(유효슛에 다시 맞춘 보정)")
    ot = S["on_target"]
    tr2, te2, rc2 = idx[train_m & ~pk & ot], idx[test_m & ~pk & ot], idx[recent_m & ~pk & ot]
    print(f"  학습 {len(tr2):,} · 검증 {len(te2):,} · 최근 {len(rc2):,} 슛")
    chosen_ot, info_ot = judge(S, tr2, te2, rc2, [C0r(), C2(), C1()])
    ok, why = calib_ok(info_ot["metrics"][chosen_ot])
    print(f"  ⇒ 유효슛 고른 후보: {chosen_ot} · 보정 {why}" + ("" if ok else "  → A4 를 뺀다"))

    print("\n[참고 ② 경기 단위 무작위 5겹 — 판정에 안 씀] 평균 로그손실")
    rng = np.random.default_rng(SEED)
    fold_of_match = rng.integers(0, 5, len(matches))
    fold = fold_of_match[S["match"]]
    sums = defaultdict(list)
    for f in range(5):
        a, b = idx[(fold != f) & ~pk], idx[(fold == f) & ~pk]
        for c in (C0(), C0r(), C2(), C1()):
            sums[c.name].append(logloss_vec(S["goal"][b], c.fit(S, a).predict(S, b)).mean())
    print("  " + " · ".join(f"{n} {np.mean(v):.4f}(±{np.std(v):.4f})" for n, v in sums.items()))

    conn = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
    accounts = [r[0] for r in conn.execute("SELECT ouid FROM accounts")]
    conn.close()
    print("\n[보고 · 결정력 문장] " + finishing_flips(S, matches, chosen, info["fitted"], accounts))
    print(f"\n끝 · {(datetime.now()-t0).total_seconds():.0f}초")


def run_2b(db_path) -> None:
    """측정 2b(ROADMAP 43 "측정 2b — 자기 참조 판정 규칙", 3판 시작 커밋 def359d) — C1 대신 C1o 로 같은 규칙 1~3,
    덧붙여 검증 구간의 내 계정이 아닌 쪽 슛만으로도 ECE·평균 차 문턱. 통과하면 C1o, 못 넘으면 C1 을 싣는다."""
    t0 = datetime.now()
    S, matches = load_shots(db_path)
    conn = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
    accounts = {r[0] for r in conn.execute("SELECT ouid FROM accounts")}
    conn.close()
    side_ouid = np.array([matches[m][2][s] if s < len(matches[m][2]) else None for m, s in zip(S["match"], S["side"])],
                         dtype=object)
    S["is_mine"] = np.array([o in accounts for o in side_ouid])
    pk = S["type"] == PK_TYPE
    train_m, test_m, recent_m, cut_date = time_split(S, matches)
    idx = np.arange(len(S["goal"]))
    tr, te, rc = idx[train_m & ~pk], idx[test_m & ~pk], idx[recent_m & ~pk]
    print(f"[재료] 슛 {len(S['goal']):,} · 내 계정이 찬 슛 {int(S['is_mine'].sum()):,} ({S['is_mine'].mean():.1%})")
    print(f"  학습 {len(tr):,}(그중 내 계정 아닌 쪽 {int((~S['is_mine'][tr]).sum()):,} — C1o 가 쓰는 것) · 검증 {len(te):,} · 최근 {len(rc):,}")

    print("\n[2b · 전체 슛] 판정 ①(시간) — C0 → C0r → C2 → C1o")
    chosen, info = judge(S, tr, te, rc, [C0(), C0r(), C2(), C1o()])
    final = "C1"
    if chosen == "C1o":
        te_nm = te[~S["is_mine"][te]]
        m = metrics(S, te_nm, info["fitted"]["C1o"].predict(S, te_nm))
        ok = m["ece"] <= ECE_MAX and m["mean_diff"] <= MEAN_DIFF_MAX
        print(f"  [덧붙인 문턱] 검증의 내 계정 아닌 쪽 {m['n']:,}슛: ECE {m['ece']*100:.2f}%p · 평균 차 {m['mean_diff']*100:.2f}%p → {'통과' if ok else '미달'}")
        if ok:
            final = "C1o"
    print(f"  ⇒ 실을 표: {final}" + ("" if final == "C1o" else "  (C1o 미달 → C1 · '내 계정 슛이 기준의 절반' 을 D2 에)"))

    fitted = dict(info["fitted"])
    if final == "C1":
        fitted["C1"] = C1().fit(S, tr)
    model = fitted[final]

    print("\n[보고 · 결정력 문장] " + finishing_flips(S, matches, final, fitted, list(accounts)))

    # 카드별 xG 차 부호 — 내 계정 슛 · 슛 30개 이상 카드 · 페널티킥은 실측 비율
    allidx = idx
    pn = np.array(S["c0"], dtype=float)
    pn[allidx[~pk]] = model.predict(S, allidx[~pk])
    pk_rate = float(S["goal"][pk & train_m].mean()) if (pk & train_m).any() else 0.76
    pn[pk] = pk_rate
    mine = S["is_mine"]
    cards = defaultdict(lambda: [0, 0.0, 0.0, 0.0])        # 슛, 골, C0 합, 새 합
    for i in allidx[mine]:
        c = cards[int(S["spid"][i])]
        c[0] += 1; c[1] += S["goal"][i]; c[2] += S["c0"][i]; c[3] += pn[i]
    big = {k: v for k, v in cards.items() if v[0] >= 30 and k != -1}
    flip = sum(1 for v in big.values() if np.sign(v[1] - v[2]) != np.sign(v[1] - v[3]))
    print(f"[보고 · 카드 xG 차] 내 계정 카드 중 슛 30개 이상 {len(big)}장 · 골−xG 부호가 C0 대비 바뀐 카드 {flip}장")

    # 슈터 등급별 보정 — 검증 슛 1,000개 넘는 등급
    print("[보고 · 슈터 등급별 보정] 검증 구간 · (실을 표) 예측 평균 − 실제 / (C0) 같은 값")
    over = []
    for dv in np.unique(S["div"][te]):
        g = te[S["div"][te] == dv]
        if len(g) < TYPE_MIN_TEST:
            continue
        y = S["goal"][g].mean()
        dn = model.predict(S, g).mean() - y
        d0 = np.clip(S["c0"][g], EPS, 1 - EPS).mean() - y
        print(f"  division {int(dv):5}  {len(g):6,}슛  실제 {y:.1%} · {final} {dn*100:+.1f}%p · C0 {d0*100:+.1f}%p")
        if abs(dn) > TYPE_DIFF_MAX:
            over.append(int(dv))
    print("  → 5%p 넘는 등급: " + (", ".join(map(str, over)) if over else "없음") +
          ("  (결정력 문장 옆 '기준' 글에 적는다)" if over else ""))
    print(f"\n끝 · {(datetime.now()-t0).total_seconds():.0f}초")


def _mark_mine(S, matches, db_path):
    conn = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
    accounts = [r[0] for r in conn.execute("SELECT ouid FROM accounts")]
    conn.close()
    side_ouid = np.array([matches[m][2][s] if s < len(matches[m][2]) else None for m, s in zip(S["match"], S["side"])],
                         dtype=object)
    S["is_mine"] = np.isin(side_ouid, np.array(accounts, dtype=object))
    S["shooter"] = side_ouid
    return accounts


def _unseen(S, label, tr, te, accounts) -> str:
    """측정 2c 판정 1·2 — 처음 보는 슈터(내 계정 검증 슛)에게 보정이 맞나. 고른 표 이름('C1o'·'C1'·None)을 돌려준다."""
    mine_te = te[S["is_mine"][te]]
    print(f"\n[2c · {label}] 학습 {len(tr):,}(내 계정 아닌 쪽 {int((~S['is_mine'][tr]).sum()):,}) · 판정 무리 = 검증의 내 계정 슛 {len(mine_te):,}")
    c1o = C1o().fit(S, tr)
    print(f"  C1o 격자 칸 {c1o.cells} · 학습 슛 {int(GRID_K)}개 미만 칸 {c1o.thin} (k 당김)")
    m = metrics(S, mine_te, c1o.predict(S, mine_te))
    ok, why = calib_ok(m)
    print(f"  [판정 1 · C1o] 로그손실 {m['logloss']:.4f} · ECE {m['ece']*100:.2f}%p · 평균 차 {m['mean_diff']*100:.2f}%p → {why}")
    per = []
    for a in accounts:
        g = mine_te[S["shooter"][mine_te] == a]
        if len(g):
            per.append((len(g), float(c1o.predict(S, g).mean() - S["goal"][g].mean())))
    per.sort(reverse=True)
    print("  보고 · 계정별(검증 슛 수, 예측−실제 %p): " + " · ".join(f"{n:,} {d*100:+.1f}" for n, d in per))
    if ok:
        return "C1o"
    preds, idxs = [], []
    for a in accounts:
        g = mine_te[S["shooter"][mine_te] == a]
        if not len(g):
            continue
        model = C1().fit(S, tr[S["shooter"][tr] != a])
        preds.append(model.predict(S, g))
        idxs.append(g)
    allg = np.concatenate(idxs)
    m2 = metrics(S, allg, np.concatenate(preds))
    ok2, why2 = calib_ok(m2)
    print(f"  [판정 2 · 계정 하나씩 빼고 C1] {len(allg):,}슛 · 로그손실 {m2['logloss']:.4f} · ECE {m2['ece']*100:.2f}%p · "
          f"평균 차 {m2['mean_diff']*100:.2f}%p → {why2}")
    return "C1" if ok2 else None


def run_2c(db_path) -> None:
    """측정 2c(ROADMAP 43 "측정 2c — 처음 보는 슈터에게 맞나", 시작 커밋 34ca596)."""
    t0 = datetime.now()
    S, matches = load_shots(db_path)
    accounts = _mark_mine(S, matches, db_path)
    pk = S["type"] == PK_TYPE
    train_m, test_m, _recent, _cut = time_split(S, matches)
    idx = np.arange(len(S["goal"]))
    a5 = _unseen(S, "A5 전체 슛", idx[train_m & ~pk], idx[test_m & ~pk], accounts)
    print(f"  ⇒ A5 실을 표: {a5 or '없음 — C0 유지 · A5 를 뺀다'}")
    ot = S["on_target"]
    a4 = _unseen(S, "A4 유효슛", idx[train_m & ~pk & ot], idx[test_m & ~pk & ot], accounts)
    print(f"  ⇒ A4 유효슛 표: {a4 or '없음 — A4 를 뺀다'}")
    print(f"\n끝 · {(datetime.now()-t0).total_seconds():.0f}초")


def _shooter_holdout(S, label, tr, te, folds=5) -> bool:
    """측정 2d — 내 계정이 아닌 슈터를 사람 단위 무작위 5조로. 조마다 '그 조 아닌 사람 · 앞 70%' 로 맞추고 '그 조 · 뒤 30%' 를 예측,
    모아서 보정 문턱. 통과 여부를 돌려준다."""
    others = sorted({o for o in S["shooter"][~S["is_mine"]] if o is not None})
    order = np.random.default_rng(SEED).permutation(len(others))
    fold_of = {others[j]: k % folds for k, j in enumerate(order)}
    fold = np.array([fold_of.get(o, -1) for o in S["shooter"]])
    preds, idxs, rows = [], [], []
    for g in range(folds):
        a = tr[(~S["is_mine"][tr]) & (fold[tr] != g) & (fold[tr] >= 0)]
        b = te[(~S["is_mine"][te]) & (fold[te] == g)]
        if not len(b):
            continue
        p = C1().fit(S, a).predict(S, b)
        preds.append(p); idxs.append(b)
        rows.append((g, len(a), len(b), float(p.mean() - S["goal"][b].mean())))
    allb = np.concatenate(idxs)
    m = metrics(S, allb, np.concatenate(preds))
    ok, why = calib_ok(m)
    print(f"\n[2d · {label}] 내 계정 아닌 슈터 {len(others):,}명 · 5조 · 검증 슛 합 {len(allb):,}")
    print("  조별(학습 슛, 검증 슛, 예측−실제 %p): " + " · ".join(f"{g}: {na:,}/{nb:,} {d*100:+.2f}" for g, na, nb, d in rows))
    print(f"  [판정] 로그손실 {m['logloss']:.4f} · ECE {m['ece']*100:.2f}%p · 평균 차 {m['mean_diff']*100:.2f}%p → {why}")
    mine_te = te[S["is_mine"][te]]
    if len(mine_te):
        c1o = C1o().fit(S, tr)
        d = c1o.predict(S, mine_te).mean() - S["goal"][mine_te].mean()
        print(f"  보고 · 내 계정 검증 슛 {len(mine_te):,}: C1o 예측 − 실제 {d*100:+.2f}%p (이 계정들의 결정력 — 판정에 안 씀)")
    return ok


def run_2d(db_path) -> None:
    """측정 2d(ROADMAP 43 "측정 2d — 사람 단위 무작위로 떼어 낸 슈터", 시작 커밋 13b99bc)."""
    t0 = datetime.now()
    S, matches = load_shots(db_path)
    _mark_mine(S, matches, db_path)
    pk = S["type"] == PK_TYPE
    train_m, test_m, _recent, _cut = time_split(S, matches)
    idx = np.arange(len(S["goal"]))
    a5 = _shooter_holdout(S, "A5 전체 슛", idx[train_m & ~pk], idx[test_m & ~pk])
    print("  ⇒ A5: " + ("C1o 를 싣는다" if a5 else "미달 — A5 를 뺀다"))
    ot = S["on_target"]
    a4 = _shooter_holdout(S, "A4 유효슛", idx[train_m & ~pk & ot], idx[test_m & ~pk & ot])
    print("  ⇒ A4: " + ("유효슛 C1o 를 싣는다" if a4 else "미달 — A4 를 뺀다"))
    print(f"\n끝 · {(datetime.now()-t0).total_seconds():.0f}초")


# ── 싣기(--write) — 판정한 바로 그 C1o 를 상수로 ──────────────────────────────
def _table_dict(m: C1) -> dict:
    """xgmodel.predict 가 읽는 꼴 — 되짚기 순서(v3 → v2 → v1 → root)와 칸 규칙을 표 안에 같이."""
    return {
        "pitch": (105.0, 68.0),                      # _geom 과 같은 값(골라인 거리·중앙 폭 환산)
        "cell": (GRID_DIST_M, GRID_WID_M, int(GRID_DIST_MAX // GRID_DIST_M), int(GRID_WID_MAX // GRID_WID_M)),
        "groups": tuple(int(g) for g in m.groups),
        "eps": EPS,
        "shots": m.n,
        "root": float(m.root),
        "v1": {int(k[0]): float(v) for k, v in sorted(m.v1.items())},
        "v2": {(int(k[0]), int(k[1])): float(v) for k, v in sorted(m.v2.items())},
        "v3": {(int(k[0]), int(k[1]), int(k[2])): float(v) for k, v in sorted(m.v3.items())},
    }


def _render(tables: dict, pk_rate: float, pk_n: int, meta: dict) -> str:
    def block(name, t):
        lines = [f"{name} = {{"]
        for key in ("pitch", "cell", "groups", "eps", "shots", "root"):
            lines.append(f"    {key!r}: {t[key]!r},")
        for key in ("v1", "v2", "v3"):
            lines.append(f"    {key!r}: {{")
            items = list(t[key].items())
            for i in range(0, len(items), 4):
                lines.append("        " + " ".join(f"{k!r}: {v!r}," for k, v in items[i:i + 4]))
            lines.append("    },")
        lines.append("}")
        return "\n".join(lines)
    head = f'''"""xG 격자 표 — **생성물. 손으로 고치지 않는다**(`python tools/fit_xg.py --write` 가 쓴다).

판정: ROADMAP 43단계 측정 2d(커밋 {meta["commit"]}) — C1o(내 계정 슛을 뺀 격자) 둘을 싣는다.
생성 {meta["generated"]} · 감독모드 {meta["matches"]:,}경기({meta["first"]} ~ {meta["last"]}) · 뺀 계정 {meta["excluded"]}개
학습 슛: 전체 {tables["SHOT"]["shots"]:,} · 유효슛 {tables["ON_TARGET"]["shots"]:,} (페널티킥 제외 · 내 계정 제외)
페널티킥은 칸 없이 상수 PK_RATE(같은 학습분 {pk_n:,}개의 성공 비율).
읽는 쪽은 `xgmodel.predict(표, x, y, 종류 코드)` 하나뿐이다.
"""

GENERATED = {meta["generated"]!r}
JUDGED_COMMIT = {meta["commit"]!r}
PK_TYPE = {PK_TYPE!r}
PK_RATE = {pk_rate!r}
'''
    return head + "\n" + block("SHOT", tables["SHOT"]) + "\n\n" + block("ON_TARGET", tables["ON_TARGET"]) + "\n"


def write_grid(db_path, out: Path = GRID_OUT) -> int:
    """측정 2d 가 통과시킨 C1o 를 전체 기간(내 계정 슛 제외)으로 맞춰 xg_grid.py 로. 쓰기 전에 대조한다:
    ① 생성한 글을 모듈로 읽어 xgmodel.predict 가 C1o.predict 와 같은 값을 내나(모든 비PK 슛 · 6자리)
    ② C1o 표 ≠ 내 계정을 안 뺀 C1 표(빼는 줄이 안 돌면 둘이 같다). 하나라도 어긋나면 파일을 안 쓰고 1."""
    S, matches = load_shots(db_path)
    accounts = _mark_mine(S, matches, db_path)
    pk = S["type"] == PK_TYPE
    idx = np.arange(len(S["goal"]))
    tables, fitted, bad = {}, {}, 0
    for name, sel in (("SHOT", ~pk), ("ON_TARGET", ~pk & S["on_target"])):
        m = C1o().fit(S, idx[sel])
        full = C1().fit(S, idx[sel])
        if m.v3 == full.v3:
            print(f"[FAIL] {name}: C1o 표가 C1 표와 같다 — 내 계정 슛을 빼는 줄이 안 돌았다")
            bad += 1
        tables[name], fitted[name] = _table_dict(m), (m, idx[sel])
        print(f"  {name}: 학습 슛 {m.n:,}(전체 {int(sel.sum()):,} 중 내 계정 제외) · 칸 {m.cells} · "
              f"{int(GRID_K)}개 미만 칸 {m.thin} · 종류 그룹 {m.groups}")
    pk_mask = pk & ~S["is_mine"]
    pk_rate = float(S["goal"][pk_mask].mean())
    print(f"  페널티킥: 내 계정 제외 {int(pk_mask.sum()):,}개 성공 {pk_rate:.4f}"
          f" (내 계정 포함이면 {float(S['goal'][pk].mean()):.4f})")
    meta = {"commit": JUDGED_COMMIT, "generated": datetime.now().strftime("%Y-%m-%d"),
            "matches": len(matches), "first": matches[0][1][:10], "last": matches[-1][1][:10],
            "excluded": len(accounts)}
    text = _render(tables, pk_rate, int(pk_mask.sum()), meta)
    mod = types.ModuleType("xg_grid_new")
    exec(compile(text, "xg_grid_new", "exec"), mod.__dict__)
    for name, (m, ix) in fitted.items():
        want = m.predict(S, ix)
        table = getattr(mod, name)
        worst = 0.0
        for i, w in zip(ix, want):
            t = int(S["type"][i])
            got = xgmodel.predict(table, float(S["x"][i]), float(S["y"][i]), None if t == -1 else t)
            worst = max(worst, abs(got - float(w)))
        ok = worst < 5e-7
        bad += not ok
        print(f"{'[OK]  ' if ok else '[FAIL]'} {name}: 생성한 표로 xgmodel.predict 대 C1o.predict — "
              f"{len(ix):,}슛 최대 차 {worst:.2e}")
    if bad:
        print(f"[FAIL] 대조 {bad}건 어긋남 — {out.name} 를 쓰지 않았다")
        return 1
    out.write_text(text, encoding="utf-8")
    print(f"[OK]   {out.name} 씀 · {len(text.encode('utf-8')):,}바이트")
    return 0


# ── 자기 점검 ──────────────────────────────────────────────────────────────
def _fake(n_match: int, truth: str, rng) -> tuple[dict, list]:
    """지어낸 슛 — truth='c0' 면 골이 C0 확률대로, 'grid' 면 C0 와 다른 칸별 확률대로(헤더·먼 거리에서 크게 어긋나게)."""
    cols = defaultdict(list)
    matches = []
    t0 = datetime(2026, 6, 1)
    for mi in range(n_match):
        matches.append((f"m{mi}", (t0 + timedelta(minutes=30 * mi)).isoformat(), ["a", "b"]))
        for _ in range(9):
            x = rng.uniform(0.55, 0.99)
            y = rng.uniform(0.15, 0.85)
            t = int(rng.choice([1, 2, 3, 6, 7], p=[0.5, 0.15, 0.15, 0.1, 0.1]))
            tname = stats.goal_type_name(t)
            in_pen = (1 - x) * 105 < 16.5 and abs(y - 0.5) * 68 < 20.16
            c0 = c0_frozen(x, y, in_pen, tname)
            dx, dy, dist, ang = _geom(x, y)
            if truth == "c0":
                p = c0
            else:
                p = 0.85 if dist < 11 else (0.55 if dist < 20 else 0.08)
                if t == 3:
                    p = 0.15
            goal = 1.0 if rng.random() < p else 0.0
            for k, v in (("match", mi), ("side", 0), ("goal", goal), ("on_target", True), ("type", t),
                         ("in_pen", float(in_pen)), ("assist", 0.0), ("dx", dx), ("dy", dy), ("dist", dist),
                         ("ang", ang), ("c0", c0)):
                cols[k].append(v)
    S = {k: np.asarray(v) for k, v in cols.items()}
    S["date"] = np.asarray([matches[i][1] for i in S["match"]])
    return S, matches


def selftest() -> int:
    """고르는 규칙이 '정답'을 고르나 — 진짜가 C0 면 C0(또는 1% 못 넘는 C0r 은 못 이어받음), 진짜가 칸 모양이면 C0 를 벗어나야."""
    rng = np.random.default_rng(1)
    bad = 0
    for truth, want in (("c0", {"C0"}), ("grid", {"C2", "C1"})):
        S, matches = _fake(6000, truth, rng)
        tr_m, te_m, rc_m, _ = time_split(S, matches)
        idx = np.arange(len(S["goal"]))
        got, _ = judge(S, idx[tr_m], idx[te_m], idx[rc_m], [C0(), C0r(), C2(), C1()], log=lambda *_: None)
        ok = got in want
        bad += not ok
        print(f"{'[OK]  ' if ok else '[FAIL]'} 진짜={truth} → 고른 후보 {got} (기대 {sorted(want)})")
    # 보정 문턱이 실제로 막는가 — 평균이 10%p 어긋난 예측
    S, matches = _fake(2000, "c0", rng)
    idx = np.arange(len(S["goal"]))
    m = metrics(S, idx, np.clip(S["c0"] + 0.10, EPS, 1 - EPS))
    ok, why = calib_ok(m)
    print(f"{'[OK]  ' if not ok else '[FAIL]'} 평균 +10%p 예측은 보정 문턱에서 막힌다 ({why})")
    bad += ok
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--2b", dest="m2b", action="store_true", help="측정 2b — 내 계정 슛을 뺀 격자(C1o)")
    ap.add_argument("--2c", dest="m2c", action="store_true", help="측정 2c — 처음 보는 슈터(내 계정 슛)로 보정 판정")
    ap.add_argument("--2d", dest="m2d", action="store_true", help="측정 2d — 사람 단위 무작위 5조로 떼어 낸 슈터")
    ap.add_argument("--write", action="store_true", help="C1o 표 둘을 xg_grid.py 로(안에서 대조 · 어긋나면 안 씀)")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.write:
        sys.exit(write_grid(config.DB_PATH))
    if a.m2d:
        run_2d(config.DB_PATH)
    elif a.m2c:
        run_2c(config.DB_PATH)
    elif a.m2b:
        run_2b(config.DB_PATH)
    else:
        run(config.DB_PATH)
