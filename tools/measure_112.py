"""112단계 관문 측정 — N24 포메이션 맞대결 · A7 승격 회차를 미리 정한 규칙으로 잰다(이 PC 전용 · 배포판 밖).

판정 규칙은 ROADMAP "## 112단계 › 관문 측정" 이 정본이다(기준 커밋 `RULE_COMMIT`). 여기 상수는 그 절을 옮긴 것이고
**결과를 보고 여기 숫자를 바꾸지 않는다**(바꾸려면 그 절을 먼저 고치는 커밋 · 그 해시를 `RULE_COMMIT` 에).

| 명령 | 하는 일 |
|---|---|
| `--counts` | 판정 전 세기만(N24 칸 · 판수 구간별 n · A7 회차 · `missing` 줄 · 10판째 승격 비율) — 결과를 보고 규칙을 안 바꾼다 |
| `--selftest` | 손으로 만든 작은 DB 로 A7 2번(경기 전 · 뒤 · 엇갈림) — 실제 자료를 안 읽는다 |
| `--fake` | 실제 자료 위의 가짜 0 · 가짜 혼동 · 가짜 2%p · 가짜 A7 여섯 판 — 단언 결과를 `fake.json` 에 |
| `--judge` | 실제 판정(N24 · A7) — `fake.json` 이 같은 도구 해시로 통과했을 때만 |
| `--status` | 자료 고정 파일 · 결과 파일 |

읽기만 한다: `dev_matches.db`·`fifa.db`·`rank.db` 는 읽기 전용 URI. 첫 실행이 `[SEASON_START, T0)` 를 숫자 배열로
`DATA_DIR/measure_112/data.npz` 에 한 번 얼리고(단 4 거슬러 받기가 T0 앞 경기를 나중에 더 넣어도 답이 같게) 그 뒤로는 그것만 읽는다.
얼린 파일에는 닉네임 · ouid · 경기 id 가 없다(구단주는 DB 안 번호 · 절반 나누기용 해시 한 비트만). 결과는 끝에서 한 번에 쓴다.
게임 중이면 안 돈다(`gameguard`) · 한 판 30분 · 메모리 4GB 를 넘으면 멈추고 그 사실을 결과에 남긴다.

numpy 를 쓴다(이 PC 에만 · 테스트 `tests/test_measure_112.py` 도 이 PC 에서만).

    python tools/measure_112.py --counts
    python tools/measure_112.py --selftest
    python tools/measure_112.py --fake       # bgrun 으로 감싸 띄운다(상태줄)
    python tools/measure_112.py --judge
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import config  # noqa: E402
import gameguard  # noqa: E402
import matchtime  # noqa: E402
import models  # noqa: E402
import stats  # noqa: E402

# ── 판정 규칙(ROADMAP 112 관문 측정 절 · 8판 · 계획 검토 끝) ──────────────────────
RULE_COMMIT = "2b21234"
T0_DATE = date(2026, 11, 22)       # 규칙 커밋 날 KST 0시 — at < T0 만
MIN_SEASON_DAYS = 14               # T0 − SEASON_START 가 이보다 짧으면 판정 안 함
MATCH_TYPE = 52                    # 감독모드
CUTOFF_H = config.SEASON_CUTOFF_H  # 시즌 경계 8시

N24_MIN_OWNERS = 50                # 칸 하한: 양쪽 합친 서로 다른 구단주 > 50
N24_BH_Q = 0.05
N24_WITHIN_MIN_OWNERS = 30         # 5번 쪽마다 사람 > 30
N24_WITHIN_MIN_GAMES = 300         # 5번 쪽마다 짝 판 합 > 300(승+패)
N24_WITHIN_Z = 1.28                # 한쪽 10%
N24_SKIP_AFTER_CHANGE = 10         # 포메이션을 바꾼 뒤 첫 10판 뺌
N24_MIN_PASS = 5                   # (쌍 × 칸) 통과가 이보다 적으면 N24 뺌
CHAMP_BAND = (800, 900)            # 칸 0 = 슈퍼챔피언스·챔피언스 · 칸 1 = 그 밖

FAKE_SEED = 112_2026               # 가짜 자료 난수(고정 · 결과를 보고 안 바꾼다)
SMALL_DB_SEED = 112_0040           # 작은 DB 난수(FAIL 이어도 바꾸지 않는다)
FAKE_SHRINK = 5                    # 실력 값 수축 5판
FAKE0_MAX_PASS = 1                 # 가짜 0 통과 0~1
CONF_PAIRS = 10                    # 가짜 혼동 쌍 수
CONF_RENAME_P = 0.7
CONF_MIN_14 = 5                    # 1~4 만: 10쌍 중 5쌍 넘게(> 5)
CONF_MAX_15 = 1                    # 1~5: 0~1쌍
LIFT = 0.02                        # 가짜 2%p
LIFT_BINS = ((1_000, 10_000), (10_000, 100_000), (100_000, None))
LIFT_PER_BIN = 4
LIFT_TOP_SHARE = 0.75              # 10만 넘음: ⌈0.75 · n⌉
LIFT_LOW_SHARE = 0.5               # 아래 두 구간: ⌈0.5 · n⌉

A7_GRADES = (1000, 1100, 1200, 1300) + tuple(range(1700, 3200, 100))   # 등급 코드(작을수록 높다) · 800·900 은 등수 등급이라 뺌
SET_LEN = 10
PTS = {1: 3, 0: 1, -1: 0}
A7_PREPOST_DIFF = 0.10             # 2번: (새 등급과 같음 − 옛 등급과 같음) ≥ 0.10 경기 전 · ≤ −0.10 경기 뒤
A7_W_DIFF = 0.10                   # 2번 보조: w − w0
A7_SET_SHIFT = 0.01                # 3번 가정 확인: 11~16판째 비율 > 1~6판째 비율 + 1%p 면 뺌
A7_MISSING_SHARE = 0.005           # 4번: missing 줄 > 셀 회차 × 0.5% 면 멈춤
A7_EARLY_K = 3                     # 5번: 끝난 판째 ≤ 3 인 바뀜
A7_LOW_PROMO_PTS = 2               # 5번: 회차 끝 0~2점 승격
A7_UNEXPLAINED_SHARE = 0.01        # 5번: 칸 바뀜 중 1% 넘으면 그 칸만 뺌
A7_ACC = 0.99                      # 7번: 전체 · 경계 띠 둘 다 > 99%
A7_BAND_PTS = (11, 15)             # 경계 띠
A7_GRID = range(0, 31)             # 문턱 후보(승점 0~30)

RUN_LIMIT_S = 30 * 60
MEM_LIMIT = 4 * 1024 ** 3

DEV_DB = config.DATA_DIR / "dev_matches.db"
RANK_DB = config.DATA_DIR / "rank.db"
OUT_DIR = config.DATA_DIR / "measure_112"
PROGRESS_DIR = Path.home() / ".claude" / "bg-progress.d"
TOOL_FILES = ("tools/measure_112.py", "stats.py", "tools/dev_matches.py")
KST = timezone(timedelta(hours=9))
DAY_S = 86_400
NONE = -9                          # 결과 · 등급 · 선발이 없음


class Stop(Exception):
    """판정을 하지 않고 멈춤(규칙이 정한 멈춤 · 자원 상한)."""


# ── 공용 ─────────────────────────────────────────────────────────────────────

def tool_hashes() -> dict[str, str]:
    return {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in TOOL_FILES}


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=_jsonable), encoding="utf-8")
    os.replace(tmp, path)


def _jsonable(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def kst_day(at):
    return (np.asarray(at, dtype=np.int64) + 9 * 3600) // DAY_S


def peak_mem() -> int | None:
    """이 프로세스의 최대 작업 집합(바이트) — Windows 만 · 못 읽으면 None."""
    if os.name != "nt":
        return None

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        h = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return int(pmc.PeakWorkingSetSize)
    except (OSError, AttributeError):
        pass
    return None


class Guard:
    """한 판 30분 · 메모리 4GB · 게임 중 — 넘으면 Stop."""

    def __init__(self, game_check=gameguard.game_running, limit_s: float = RUN_LIMIT_S, progress=None):
        self.game_check, self.limit_s, self.progress = game_check, limit_s, progress
        self.t0 = time.monotonic()
        self.label = ""

    def start(self, label: str) -> None:
        self.t0, self.label = time.monotonic(), label
        self.check(label)

    def check(self, where: str = "") -> None:
        if self.progress:
            self.progress.set(f"{self.label} {where}".strip())
        if time.monotonic() - self.t0 > self.limit_s:
            raise Stop(f"한 판 {self.limit_s / 60:.0f}분 넘음({self.label} {where})")
        m = peak_mem()
        if m is not None and m > MEM_LIMIT:
            raise Stop(f"메모리 {m / 1024 ** 3:.1f}GB 넘음({self.label} {where})")
        if self.game_check():
            raise Stop("게임이 켜짐")


class Progress:
    def __init__(self):
        self.path = PROGRESS_DIR / f"measure-112-{os.getpid()}.txt"
        self.t0 = time.monotonic()

    def set(self, msg: str) -> None:
        try:
            PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"112 관문 측정 · {msg} · 경과 {int((time.monotonic() - self.t0) // 60)}분\n", encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ── 선발 풀기 ────────────────────────────────────────────────────────────────

def lineup_players(body: bytes) -> list[tuple[int, int, int]]:
    """`dev_matches.lineup_body` 의 반대 — (spId, spPosition, spGrade) 목록. 넓은 꼴은 맨 앞 0x01 + `<qhh`(길이가 홀수)."""
    if not body:
        return []
    if len(body) % 2 == 1:
        if body[0] != 1 or (len(body) - 1) % 12:
            raise ValueError("넓은 꼴 길이가 맞지 않음")
        return [tuple(x) for x in struct.iter_unpack("<qhh", body[1:])]
    if len(body) % 6:
        raise ValueError("좁은 꼴 길이가 맞지 않음")
    return [tuple(x) for x in struct.iter_unpack("<IBB", body)]


def lineup_formation(body: bytes) -> tuple[str, int]:
    """선발 → (앱의 `stats.formation_of` 5칸 키, 선수 수)."""
    ps = lineup_players(body)
    return stats.formation_of([{"spPosition": p[1]} for p in ps]), len(ps)


# ── 시즌 · 자료 고정 ──────────────────────────────────────────────────────────

def t0_epoch() -> int:
    return int(datetime.combine(T0_DATE, datetime.min.time(), KST).timestamp())


def season_start(fifa_db: Path, rank_db: Path) -> int:
    """지금 시즌 시작 — `dev_matches.season_start_at`(0시 값)을 KST 날짜로 내리고 그날 8시."""
    import dev_matches
    rk = _ro(rank_db) if Path(rank_db).exists() else sqlite3.connect(":memory:")
    try:
        at = dev_matches.season_start_at(fifa_db, rk)
    finally:
        rk.close()
    if at is None:
        raise Stop("시즌 시작을 모름(시즌표 · rank.db 둘 다)")
    d = datetime.fromtimestamp(at, KST).date()
    return int(datetime.combine(d, datetime.min.time(), KST).timestamp()) + CUTOFF_H * 3600


def season_id_of(fifa_db: Path, start: int, t0: int) -> int:
    """`[start, t0)` 안 내 경기(fifa.db · 감독모드)의 시즌 번호 — 하나로 모이지 않으면 멈춤."""
    lo = matchtime.raw_bound(datetime.fromtimestamp(start, KST).replace(tzinfo=None))
    hi = matchtime.raw_bound(datetime.fromtimestamp(t0, KST).replace(tzinfo=None))
    c = _ro(fifa_db)
    try:
        rows = c.execute("SELECT payload FROM matches WHERE match_type = ? AND match_date >= ? AND match_date < ?",
                         (MATCH_TYPE, lo, hi)).fetchall()
    finally:
        c.close()
    ids = set()
    for (payload,) in rows:
        try:
            d = json.loads(payload)
        except ValueError:
            continue
        for i in d.get("matchInfo") or []:
            if isinstance(i, dict):
                sid = models._season_id(i.get("matchDetail") or {})
                if sid is not None:
                    ids.add(sid)
    if len(ids) != 1:
        raise Stop(f"SEASON_ID 가 하나로 모이지 않음({sorted(ids)} · 경기 {len(rows)})")
    return ids.pop()


def _owner_half(ouid: str) -> int:
    return hashlib.sha256(ouid.encode()).digest()[0] & 1


def freeze(dev_db: Path, start: int, t0: int, log=print) -> dict:
    """`[start, t0)` 경기를 숫자 배열로. 경기는 (at, match_id) 순(WITHOUT ROWID 표의 열쇠 순 + 안정 정렬)."""
    c = _ro(dev_db)
    try:
        n = c.execute("SELECT COUNT(*) FROM games WHERE at >= ? AND at < ?", (start, t0)).fetchone()[0]
        cols = {k: np.empty(n, dt) for k, dt in (("at", np.int64), ("et", np.int8), ("o1", np.int32), ("d1", np.int16),
                                                   ("r1", np.int8), ("l1", np.int32), ("o2", np.int32), ("d2", np.int16),
                                                   ("r2", np.int8), ("l2", np.int32))}
        cur = c.execute("SELECT at, end_type, o1, d1, r1, l1, o2, d2, r2, l2 FROM games WHERE at >= ? AND at < ?", (start, t0))
        i = 0
        names = list(cols)
        while True:
            rows = cur.fetchmany(200_000)
            if not rows:
                break
            arr = np.array([[NONE if v is None else v for v in r] for r in rows], dtype=np.int64)
            for j, k in enumerate(names):
                cols[k][i:i + len(rows)] = arr[:, j]
            i += len(rows)
            log(f"  경기 {i:,}/{n:,}")
        if i != n:
            raise Stop("경기 수가 읽는 도중 바뀜")
        cols["o2"][cols["o2"] < 0] = 0      # 상대가 빈 줄 — 0 번은 없는 구단주(대상 아님)
        order = np.argsort(cols["at"], kind="stable")
        for k in names:
            cols[k] = cols[k][order]
        # 선발 → 포메이션 번호(키 문자열 순) · 선수 수
        keys, nplay = {}, {}
        for lid, body in c.execute("SELECT id, body FROM lineups"):
            try:
                k, m = lineup_formation(body)
            except (ValueError, struct.error):
                k, m = None, 0
            keys[lid], nplay[lid] = k, m
        fkeys = sorted({k for k in keys.values() if k})
        fidx = {k: j for j, k in enumerate(fkeys)}
        maxl = max(keys) + 1 if keys else 1
        f_of = np.full(maxl, -1, np.int32)
        n_of = np.zeros(maxl, np.int16)
        for lid, k in keys.items():
            if k:
                f_of[lid] = fidx[k]
            n_of[lid] = nplay[lid]
        for s in ("1", "2"):
            l = cols["l" + s]
            ok = (l >= 0) & (l < maxl)
            cols["f" + s] = np.where(ok, f_of[np.clip(l, 0, maxl - 1)], -1).astype(np.int32)
            cols["n" + s] = np.where(ok, n_of[np.clip(l, 0, maxl - 1)], 0).astype(np.int16)
            del cols["l" + s]
        # 구단주
        maxo = c.execute("SELECT MAX(id) FROM owners").fetchone()[0] or 0
        tgt = np.zeros(maxo + 1, bool)
        half = np.zeros(maxo + 1, np.int8)
        gap = np.full(maxo + 1, NONE, np.int64)
        for oid, ouid, sn, gap_at in c.execute("SELECT id, ouid, profile_sn, gap_at FROM owners"):
            if sn is not None:
                tgt[oid] = True
                half[oid] = _owner_half(ouid)
            if gap_at is not None:
                gap[oid] = gap_at
        lo = np.full(maxo + 1, NONE, np.int64)
        hi = np.full(maxo + 1, NONE, np.int64)
        for oid, base_at, top_at, back_done in c.execute("SELECT owner, base_at, top_at, back_done FROM cursor"):
            if top_at is None:
                continue
            lo[oid] = -1 if back_done else (base_at if base_at is not None else top_at)
            hi[oid] = top_at
        miss_owner = np.zeros(maxo + 1, bool)
        miss_rows = 0
        for (oid,) in c.execute("SELECT owner FROM missing"):
            miss_rows += 1
            if oid is not None and 0 <= oid <= maxo:
                miss_owner[oid] = True
    finally:
        c.close()
    return {**cols, "fkeys": np.array(fkeys), "tgt": tgt, "half": half, "gap": gap, "lo": lo, "hi": hi,
            "miss_owner": miss_owner, "miss_rows": np.array(miss_rows)}


def load_frozen(path: Path, dev_db: Path, start: int, t0: int, log=print) -> dict:
    """얼린 자료 — 있으면 그대로(같은 시즌 · T0 일 때만) · 없으면 한 번 얼린다."""
    if path.exists():
        z = np.load(path, allow_pickle=False)
        G = {k: z[k] for k in z.files}
        if int(G["start"]) == start and int(G["t0"]) == t0:
            return G
        raise Stop(f"얼린 자료의 시즌 · T0 가 다름({path}) — 지우고 다시")
    G = freeze(dev_db, start, t0, log=log)
    G["start"], G["t0"] = np.array(start), np.array(t0)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez(tmp, **G)
    os.replace(tmp, path)
    return G


# ── N24 ──────────────────────────────────────────────────────────────────────

def norm_p2(z):
    z = np.abs(np.asarray(z, float))
    return np.array([math.erfc(v / math.sqrt(2)) if np.isfinite(v) else 0.0 for v in z.ravel()]).reshape(z.shape)


def bh(p, q=N24_BH_Q):
    """Benjamini–Hochberg — 통과 여부(같은 순서)."""
    p = np.asarray(p, float)
    m = len(p)
    if m == 0:
        return np.zeros(0, bool)
    order = np.argsort(p, kind="stable")
    ok = p[order] <= q * (np.arange(1, m + 1) / m)
    k = np.nonzero(ok)[0]
    out = np.zeros(m, bool)
    if len(k):
        out[order[:k[-1] + 1]] = True
    return out


def base_static(G) -> np.ndarray:
    """포메이션과 무관한 N24 거르기: 정상 종료 · 양쪽 대상 · 양쪽 11명 · 같은 등급 코드 · 결과가 서로 맞음."""
    r1, r2 = G["r1"], G["r2"]
    consistent = ((r1 == 1) & (r2 == -1)) | ((r1 == -1) & (r2 == 1)) | ((r1 == 0) & (r2 == 0))
    return ((G["et"] == 0) & G["tgt"][G["o1"]] & G["tgt"][G["o2"]] & (G["n1"] == 11) & (G["n2"] == 11)
            & (G["d1"] == G["d2"]) & (G["d1"] >= 0) & consistent)


def change_excl(G, F1, F2) -> tuple[np.ndarray, np.ndarray]:
    """사람마다 시즌 경기 순서에서 포메이션을 바꾼 뒤 첫 10판(바뀐 판 포함) — 시즌 첫 포메이션은 바꿈이 아니다."""
    n = len(F1)
    person = np.concatenate([G["o1"], G["o2"]])
    form = np.concatenate([F1, F2])
    gi = np.concatenate([np.arange(n), np.arange(n)])
    ok = form >= 0
    idx = np.nonzero(ok)[0]
    order = idx[np.lexsort((gi[idx], person[idx]))]
    p, f = person[order], form[order]
    newp = np.r_[True, p[1:] != p[:-1]]
    chg = np.r_[False, (f[1:] != f[:-1]) & ~newp[1:]]
    seg_start = newp | chg
    pos = np.arange(len(order))
    last_chg = np.maximum.accumulate(np.where(seg_start, pos, 0))
    cum = np.cumsum(chg)
    seen_chg = cum > np.maximum.accumulate(np.where(newp, cum, 0))   # 사람의 첫 구간(바꿈 없음)은 빼지 않는다
    ex = seen_chg & ((pos - last_chg) < N24_SKIP_AFTER_CHANGE)
    out = np.zeros(2 * n, bool)
    out[order] = ex
    return out[:n], out[n:]


def segments(G, F1, F2):
    """사람마다 시즌 경기 순서의 같은 포메이션 덩어리 — (위치 배열(2n 공간), 덩어리 번호, 덩어리 사람, 덩어리 포메이션)."""
    n = len(F1)
    person = np.concatenate([G["o1"], G["o2"]])
    form = np.concatenate([F1, F2])
    gi = np.concatenate([np.arange(n), np.arange(n)])
    idx = np.nonzero(form >= 0)[0]
    order = idx[np.lexsort((gi[idx], person[idx]))]
    p, f = person[order], form[order]
    start = np.r_[True, (p[1:] != p[:-1]) | (f[1:] != f[:-1])]
    seg = np.cumsum(start) - 1
    return order, seg, p[start], f[start]


def frame(G, F1, F2, R1, static) -> dict:
    """N24 판 하나에 한 줄 — 정렬 쌍(A < B) 기준. R1 은 1쪽 결과(1 · 0 · −1)."""
    base = static & (F1 >= 0) & (F2 >= 0) & (F1 != F2)
    idx = np.nonzero(base)[0]
    a1 = F1[idx] < F2[idx]
    fa = np.where(a1, F1[idx], F2[idx]).astype(np.int64)
    fb = np.where(a1, F2[idx], F1[idx]).astype(np.int64)
    r = R1[idx].astype(np.int8)
    ya = np.where(a1, r, -r)
    band = np.where(np.isin(G["d1"][idx], CHAMP_BAND), 0, 1).astype(np.int64)
    K = len(G["fkeys"])
    cell = (band * K + fa) * K + fb
    return {"idx": idx, "cell": cell, "ya": ya, "own_a": np.where(a1, G["o1"][idx], G["o2"][idx]),
            "own_b": np.where(a1, G["o2"][idx], G["o1"][idx]), "day": kst_day(G["at"][idx]), "K": K}


def _cluster_var(cinv, owner, resid, ncell, n_dec):
    key = cinv.astype(np.int64) * (1 << 22) + owner.astype(np.int64)
    uk, kinv = np.unique(key, return_inverse=True)
    s = np.bincount(kinv, weights=resid)
    return np.bincount(uk >> 22, weights=s * s, minlength=ncell) / np.maximum(n_dec, 1) ** 2


def cell_stats(fr) -> dict:
    uc, cinv = np.unique(fr["cell"], return_inverse=True)
    nc = len(uc)
    ya = fr["ya"]
    dec = ya != 0
    win = ya == 1
    n_games = np.bincount(cinv, minlength=nc)
    n_dec = np.bincount(cinv, weights=dec, minlength=nc)
    wins = np.bincount(cinv, weights=win, minlength=nc)
    with np.errstate(invalid="ignore", divide="ignore"):
        v = wins / n_dec
        resid = np.where(dec, win - v[cinv], 0.0)
        va = _cluster_var(cinv, fr["own_a"], resid, nc, n_dec)
        vb = _cluster_var(cinv, fr["own_b"], resid, nc, n_dec)
        se = np.sqrt(va + vb)
        z = (v - 0.5) / se
    okey = np.unique(np.concatenate([cinv.astype(np.int64) * (1 << 22) + fr["own_a"],
                                     cinv.astype(np.int64) * (1 << 22) + fr["own_b"]]))
    owners = np.bincount(okey >> 22, minlength=nc)
    odd = (fr["day"] % 2) == 1
    with np.errstate(invalid="ignore", divide="ignore"):
        v_odd = np.bincount(cinv, weights=win & odd, minlength=nc) / np.bincount(cinv, weights=dec & odd, minlength=nc)
        v_even = np.bincount(cinv, weights=win & ~odd, minlength=nc) / np.bincount(cinv, weights=dec & ~odd, minlength=nc)
    K = fr["K"]
    return {"cell": uc, "band": uc // (K * K), "fa": (uc // K) % K, "fb": uc % K, "n_games": n_games, "n_dec": n_dec,
            "v": v, "se": se, "z": z, "p": norm_p2(z), "owners": owners, "v_odd": v_odd, "v_even": v_even}


def steps_1_4(cs) -> dict:
    """칸 하한 · BH · 두 반 — 칸마다 통과 여부. BH 의 m 은 칸 하한을 넘은 칸 수."""
    elig = cs["owners"] > N24_MIN_OWNERS
    ok_v = np.isfinite(cs["v"]) & np.isfinite(cs["z"])
    p = np.where(ok_v, cs["p"], 1.0)
    bh_ok = np.zeros(len(p), bool)
    e = np.nonzero(elig)[0]
    bh_ok[e] = bh(p[e])
    s_odd, s_even = np.sign(cs["v_odd"] - 0.5), np.sign(cs["v_even"] - 0.5)
    halves = np.isfinite(cs["v_odd"]) & np.isfinite(cs["v_even"]) & (s_odd == s_even) & (s_odd != 0)
    return {"elig": elig, "bh": bh_ok & elig, "halves": halves, "pass14": elig & bh_ok & halves, "m": int(elig.sum())}


def within_records(G, F1, F2, R1, static, ex1, ex2) -> dict:
    """사람 안 비교 재료 — 판마다 양쪽을 "사람" 으로 한 줄씩(빼는 판 · 무승부 뺌) · (상대 포메이션, 칸) 순으로 정렬."""
    base = static & (F1 >= 0) & (F2 >= 0) & (F1 != F2)
    idx = np.nonzero(base)[0]
    band = np.where(np.isin(G["d1"][idx], CHAMP_BAND), 0, 1)
    day = kst_day(G["at"][idx])
    r = R1[idx].astype(np.int8)
    parts = []
    for own, opp, o, y, ex in ((F1, F2, G["o1"], r, ex1), (F2, F1, G["o2"], -r, ex2)):
        keep = (y != 0) & ~ex[idx]
        parts.append((o[idx][keep], own[idx][keep], opp[idx][keep], band[keep], day[keep], (y[keep] == 1)))
    person, own, opp, bnd, dy, win = (np.concatenate([a[i] for a in parts]) for i in range(6))
    key = opp.astype(np.int64) * 2 + bnd
    order = np.argsort(key, kind="stable")
    return {"key": key[order], "person": person[order], "own": own[order], "day": dy[order] - dy.min() if len(dy) else dy,
            "win": win[order]}


def within_side(rec, X, Y, band) -> dict:
    """상대가 Y 인 판(칸 band)에서 같은 KST 날에 X 로도 X 가 아닌 것으로도 붙은 날을 가진 사람만 — d = X 승률 − X 아닌 승률."""
    k = int(Y) * 2 + int(band)
    lo, hi = np.searchsorted(rec["key"], [k, k + 1])
    person, own, day, win = rec["person"][lo:hi], rec["own"][lo:hi], rec["day"][lo:hi], rec["win"][lo:hi]
    isx = own == X
    pd_key = person.astype(np.int64) * 8192 + day
    uk, inv = np.unique(pd_key, return_inverse=True)
    both = (np.bincount(inv, weights=isx, minlength=len(uk)) > 0) & (np.bincount(inv, weights=~isx, minlength=len(uk)) > 0)
    m = both[inv]
    up, pinv = np.unique(person[m], return_inverse=True)
    nx = np.bincount(pinv, weights=isx[m], minlength=len(up))
    nn = np.bincount(pinv, weights=~isx[m], minlength=len(up))
    wx = np.bincount(pinv, weights=isx[m] & win[m], minlength=len(up))
    wn = np.bincount(pinv, weights=~isx[m] & win[m], minlength=len(up))
    keep = (nx > 0) & (nn > 0)
    nx, nn, wx, wn = nx[keep], nn[keep], wx[keep], wn[keep]
    owners, games = int(keep.sum()), int((nx + nn).sum())
    if owners == 0:
        return {"owners": 0, "games": 0, "d": float("nan"), "z": float("nan")}
    d = wx / nx - wn / nn
    w = np.minimum(nx, nn)
    D = float((w * d).sum() / w.sum())
    se = float(math.sqrt(((w * (d - D)) ** 2).sum()) / w.sum())
    z = D / se if se > 0 else (0.0 if D == 0 else math.copysign(float("inf"), D))
    return {"owners": owners, "games": games, "d": D, "z": z}


def step_5(rec, cs, i) -> dict:
    """칸 i 의 5번 — 하한 · s · z_A ≥ 1.28 · −s · z_B ≥ 1.28."""
    A, B, band = int(cs["fa"][i]), int(cs["fb"][i]), int(cs["band"][i])
    s = float(np.sign(cs["v"][i] - 0.5))
    a = within_side(rec, A, B, band)
    b = within_side(rec, B, A, band)
    floor = all(x["owners"] > N24_WITHIN_MIN_OWNERS and x["games"] > N24_WITHIN_MIN_GAMES for x in (a, b))
    ok = floor and s != 0 and s * a["z"] >= N24_WITHIN_Z and -s * b["z"] >= N24_WITHIN_Z
    return {"floor": floor, "pass": bool(ok), "s": s, "a": a, "b": b}


def n24_run(G, F1, F2, R1, static, ex1, ex2, *, only=None, guard=None) -> dict:
    """1~5 전부 — `only` 가 있으면 그 칸(번호 목록)만 5번을 잰다(나머지는 1~4 를 통과한 칸만)."""
    fr = frame(G, F1, F2, R1, static)
    cs = cell_stats(fr)
    st = steps_1_4(cs)
    if guard:
        guard.check("1~4")
    rec = within_records(G, F1, F2, R1, static, ex1, ex2)
    want = set(np.nonzero(st["pass14"])[0].tolist()) | set(only or [])
    s5 = {i: step_5(rec, cs, i) for i in sorted(want)}
    if guard:
        guard.check("5")
    pass15 = np.zeros(len(cs["cell"]), bool)
    for i, r in s5.items():
        pass15[i] = st["pass14"][i] and r["pass"]
    return {"cs": cs, "st": st, "s5": s5, "pass15": pass15}


def cell_index(cs, key) -> int | None:
    j = np.searchsorted(cs["cell"], key)
    return int(j) if j < len(cs["cell"]) and cs["cell"][j] == key else None


def cell_name(G, cs, i) -> str:
    fk = G["fkeys"]
    return f"{fk[cs['fa'][i]]} 대 {fk[cs['fb'][i]]} · 칸 {'800·900' if cs['band'][i] == 0 else '그 밖'}"


# ── 가짜 자료(N24) ────────────────────────────────────────────────────────────

def skill_pi(G) -> np.ndarray:
    """판마다 1쪽이 이길 확률 — 두 사람 그날 실력 값(로짓) 차의 로지스틱. 실력은 (승+5)/(승+패+10) · 날 값은 시즌 쪽으로 5판 수축."""
    r1, r2 = G["r1"], G["r2"]
    dec = (G["et"] == 0) & (((r1 == 1) & (r2 == -1)) | ((r1 == -1) & (r2 == 1)))
    day = kst_day(G["at"])
    day = day - day.min()
    person = np.concatenate([G["o1"], G["o2"]]).astype(np.int64)
    win = np.concatenate([r1 == 1, r2 == 1])
    d2 = np.concatenate([dec, dec])
    dd = np.concatenate([day, day])
    no = len(G["tgt"])
    w = np.bincount(person, weights=win & d2, minlength=no)
    nn = np.bincount(person, weights=d2, minlength=no)
    P = (w + FAKE_SHRINK) / (nn + 2 * FAKE_SHRINK)
    pk = person * 8192 + dd
    uk, inv = np.unique(pk, return_inverse=True)
    wd = np.bincount(inv, weights=win & d2)
    nd = np.bincount(inv, weights=d2)
    pd = (wd + FAKE_SHRINK * P[uk // 8192]) / (nd + FAKE_SHRINK)
    s = np.log(pd / (1 - pd))[inv]
    n = len(r1)
    return 1.0 / (1.0 + np.exp(-(s[:n] - s[n:])))


def fake_results(G, pi1, u) -> np.ndarray:
    """승·패만 다시 뽑는다(무승부 판은 실제 무승부 그대로)."""
    r1 = G["r1"].copy()
    dec = np.isin(r1, (1, -1))
    r1[dec] = np.where(u[dec] < pi1[dec], 1, -1)
    return r1


def person_skill(G) -> np.ndarray:
    r1, r2 = G["r1"], G["r2"]
    dec = (G["et"] == 0) & (((r1 == 1) & (r2 == -1)) | ((r1 == -1) & (r2 == 1)))
    person = np.concatenate([G["o1"], G["o2"]]).astype(np.int64)
    win = np.concatenate([r1 == 1, r2 == 1])
    d2 = np.concatenate([dec, dec])
    no = len(G["tgt"])
    w = np.bincount(person, weights=win & d2, minlength=no)
    nn = np.bincount(person, weights=d2, minlength=no)
    return (w + FAKE_SHRINK) / (nn + 2 * FAKE_SHRINK)


def rename(G, F1, F2, X, Y, top, rng, seg_info) -> tuple[np.ndarray, np.ndarray]:
    """가짜 혼동 — 실력 상위 절반의 Y 덩어리는 0.7 로 X, 하위 절반의 X 덩어리는 0.7 로 Y(바꿈 시점은 그대로)."""
    order, seg, sp, sf = seg_info
    flip = rng.random(len(sp)) < CONF_RENAME_P
    to = np.full(len(sp), -1, np.int64)
    to[(sf == Y) & top[sp] & flip] = X
    to[(sf == X) & ~top[sp] & flip] = Y
    n = len(F1)
    form = np.concatenate([F1, F2]).copy()
    t = to[seg]
    hit = t >= 0
    form[order[hit]] = t[hit]
    return form[:n].astype(np.int32), form[n:].astype(np.int32)


def run_fakes(G, guard, log=print) -> dict:
    """가짜 0 · 혼동 · 2%p — 단언과 갈래를 그대로 적는다."""
    rng = np.random.default_rng(FAKE_SEED)
    out: dict = {}
    static = base_static(G)
    F1, F2 = G["f1"], G["f2"]
    ex1, ex2 = change_excl(G, F1, F2)
    guard.start("가짜 0")
    pi1 = skill_pi(G)
    u = rng.random(len(pi1))
    R0 = fake_results(G, pi1, u)
    r0 = n24_run(G, F1, F2, R0, static, ex1, ex2, guard=guard)
    n0 = int(r0["pass15"].sum())
    out["fake0"] = {"pass15": n0, "pass14": int(r0["st"]["pass14"].sum()), "m": r0["st"]["m"],
                    "ok": n0 <= FAKE0_MAX_PASS}
    log(f"가짜 0: 1~5 통과 {n0} · 1~4 통과 {out['fake0']['pass14']} · m {r0['st']['m']} → {'OK' if out['fake0']['ok'] else 'FAIL'}")
    cs0 = r0["cs"]
    elig = np.nonzero(r0["st"]["elig"])[0]

    # 가짜 혼동 — 판이 많은 칸 10개(X < Y) · 칸마다 따로 이름 바꾸기
    guard.start("가짜 혼동")
    pick = elig[np.argsort(-cs0["n_games"][elig], kind="stable")[:CONF_PAIRS]]
    top_p = person_skill(G)
    tg = G["tgt"]
    med = np.median(top_p[tg]) if tg.any() else 0.5
    top = top_p > med
    seg_info = segments(G, F1, F2)
    conf = []
    for i in pick:
        key = int(cs0["cell"][i])
        X, Y = int(cs0["fa"][i]), int(cs0["fb"][i])
        C1, C2 = rename(G, F1, F2, X, Y, top, rng, seg_info)
        rc = n24_run(G, C1, C2, R0, static, ex1, ex2, guard=guard, only=None)
        j = cell_index(rc["cs"], key)
        if j is None:
            conf.append({"cell": cell_name(G, cs0, i), "gone": True, "floor": False, "pass14": False, "pass15": False})
            continue
        rec = within_records(G, C1, C2, R0, static, ex1, ex2)
        s5 = rc["s5"].get(j) or step_5(rec, rc["cs"], j)
        conf.append({"cell": cell_name(G, cs0, i), "n_games": int(rc["cs"]["n_games"][j]), "v": float(rc["cs"]["v"][j]),
                     "floor": s5["floor"], "pass14": bool(rc["st"]["pass14"][j]),
                     "pass15": bool(rc["st"]["pass14"][j] and s5["pass"]), "za": s5["a"]["z"], "zb": s5["b"]["z"],
                     "a_owners": s5["a"]["owners"], "a_games": s5["a"]["games"], "b_owners": s5["b"]["owners"], "b_games": s5["b"]["games"]})
        log(f"  혼동 {conf[-1]['cell']}: 하한 {s5['floor']} · 1~4 {conf[-1]['pass14']} · 1~5 {conf[-1]['pass15']}")
    a1 = len(conf) == CONF_PAIRS and all(c["floor"] for c in conf)
    a2 = sum(c["pass14"] for c in conf) > CONF_MIN_14
    a3 = sum(c["pass15"] for c in conf) <= CONF_MAX_15
    out["confound"] = {"cells": conf, "a1_floor": a1, "a2_14": a2, "a3_15": a3,
                       "verdict": "N24 뺌(사람 안 비교 판이 얇음)" if not a1 else ("멈춤(원인 찾기)" if not (a2 and a3) else "OK")}
    log(f"가짜 혼동: ① {a1} ② {a2} ③ {a3} → {out['confound']['verdict']}")

    # 가짜 2%p — 판수 구간마다 넷 · A 쪽 확률 +0.02
    guard.start("가짜 2%p")
    chosen = {}
    for lo, hi in LIFT_BINS:
        inb = elig[(cs0["n_games"][elig] >= lo) & ((cs0["n_games"][elig] < hi) if hi else True)]
        chosen[(lo, hi)] = inb[np.argsort(-cs0["n_games"][inb], kind="stable")[:LIFT_PER_BIN]].tolist()
    lift_cells = {int(cs0["cell"][i]) for v in chosen.values() for i in v}
    fr0 = frame(G, F1, F2, R0, static)
    sign1 = np.zeros(len(pi1))
    inlift = np.isin(fr0["cell"], list(lift_cells))
    a_is_1 = F1[fr0["idx"]] < F2[fr0["idx"]]
    sign1[fr0["idx"][inlift]] = np.where(a_is_1[inlift], 1.0, -1.0)
    R2 = fake_results(G, np.clip(pi1 + LIFT * sign1, 0, 1), u)
    rl = n24_run(G, F1, F2, R2, static, ex1, ex2, guard=guard, only=None)
    rec = within_records(G, F1, F2, R2, static, ex1, ex2)
    bins = []
    for (lo, hi), idxs in chosen.items():
        rows = []
        for i in idxs:
            j = cell_index(rl["cs"], int(cs0["cell"][i]))
            s5 = rl["s5"].get(j) or step_5(rec, rl["cs"], j)
            rows.append({"cell": cell_name(G, cs0, i), "n_games": int(rl["cs"]["n_games"][j]), "v": float(rl["cs"]["v"][j]),
                         "s5": s5["pass"], "s15": bool(rl["st"]["pass14"][j] and s5["pass"]), "za": s5["a"]["z"], "zb": s5["b"]["z"],
                         "floor": s5["floor"]})
        n = len(rows)
        bins.append({"lo": lo, "hi": hi, "n": n, "rows": rows, "s5": sum(r["s5"] for r in rows), "s15": sum(r["s15"] for r in rows)})
    top_bin = bins[-1]
    nt = top_bin["n"]
    need_top = math.ceil(LIFT_TOP_SHARE * nt)
    lift = {"bins": bins}
    if nt == 0:
        lift["assert"], lift["verdict"] = False, "N24 뺌(10만 넘음 칸 없음)"
    elif top_bin["s5"] >= need_top:
        lift["assert"] = True
        lift["verdict"] = "OK" if top_bin["s15"] >= need_top else "N24 뺌(가장 큰 칸도 못 가름)"
    else:
        bad = [r for r in top_bin["rows"] if not r["s5"]]
        flipped = any((r["za"] < 0) or (r["zb"] > 0) for r in bad)
        lift["assert"] = False
        lift["verdict"] = "멈춤(버그 — 5번 부호)" if flipped else "N24 뺌(검정력 부족)"
    floor_games = None
    if lift["verdict"] == "OK":
        floor_games = top_bin["lo"]
        for b in reversed(bins[:-1]):
            if b["n"] == 0 or b["s15"] < math.ceil(LIFT_LOW_SHARE * b["n"]):
                break
            floor_games = b["lo"]
    lift["conclusion_min_games"] = floor_games
    out["lift"] = lift
    log(f"가짜 2%p: 10만 넘음 n {nt} · 5번만 {top_bin['s5']} · 1~5 {top_bin['s15']} → {lift['verdict']} · 결론 하한 {floor_games}")
    return out


def judge_n24(G, guard, conclusion_min_games, log=print) -> dict:
    guard.start("N24 실제")
    static = base_static(G)
    ex1, ex2 = change_excl(G, G["f1"], G["f2"])
    r = n24_run(G, G["f1"], G["f2"], G["r1"], static, ex1, ex2, guard=guard)
    cs, st = r["cs"], r["st"]
    n15 = int(r["pass15"].sum())
    cells = []
    for i in np.nonzero(st["elig"])[0]:
        s5 = r["s5"].get(int(i))
        cells.append({"a": str(G["fkeys"][cs["fa"][i]]), "b": str(G["fkeys"][cs["fb"][i]]), "band": int(cs["band"][i]),
                      "n_games": int(cs["n_games"][i]), "n_dec": int(cs["n_dec"][i]), "owners": int(cs["owners"][i]),
                      "v": float(cs["v"][i]), "se": float(cs["se"][i]), "p": float(cs["p"][i]),
                      "pass14": bool(st["pass14"][i]), "pass15": bool(r["pass15"][i]),
                      "conclusive": bool(conclusion_min_games is not None and cs["n_games"][i] >= conclusion_min_games),
                      "za": None if s5 is None else s5["a"]["z"], "zb": None if s5 is None else s5["b"]["z"]})
    verdict = "N24 살림" if n15 >= N24_MIN_PASS else f"N24 뺌(통과 {n15} < {N24_MIN_PASS})"
    log(f"N24 실제: m {st['m']} · 1~4 {int(st['pass14'].sum())} · 1~5 {n15} → {verdict}")
    return {"m": st["m"], "pass14": int(st["pass14"].sum()), "pass15": n15, "verdict": verdict, "cells": cells}


# ── A7 ───────────────────────────────────────────────────────────────────────

def _is_a7(d) -> bool:
    return d in _A7_SET


_A7_SET = frozenset(A7_GRADES)


def sequences(G) -> dict[int, np.ndarray]:
    """대상마다 커서가 덮는 구간 안의 자기 판(경기 번호 · 쪽) — 시각 순."""
    n = len(G["at"])
    person = np.concatenate([G["o1"], G["o2"]]).astype(np.int64)
    gi = np.concatenate([np.arange(n), np.arange(n)])
    side = np.concatenate([np.zeros(n, np.int8), np.ones(n, np.int8)])
    at = G["at"][gi]
    lo, hi = G["lo"][person], G["hi"][person]
    ok = G["tgt"][person] & (hi != NONE) & (at >= np.where(lo == -1, np.iinfo(np.int64).min, lo)) & (at <= hi)
    idx = np.nonzero(ok)[0]
    order = idx[np.lexsort((gi[idx], person[idx]))]
    p = person[order]
    cuts = np.nonzero(np.r_[True, p[1:] != p[:-1]])[0]
    out = {}
    for a, b in zip(cuts, np.r_[cuts[1:], len(order)]):
        out[int(p[a])] = np.stack([gi[order[a:b]], side[order[a:b]]], axis=1)
    return out


def seq_arrays(G, seq, D1=None, D2=None):
    """한 사람 판의 (자기 등급, 상대 등급, 자기 결과, 종료 꼴, 시각)."""
    gi, sd = seq[:, 0], seq[:, 1]
    d1 = G["d1"] if D1 is None else D1
    d2 = G["d2"] if D2 is None else D2
    own = np.where(sd == 0, d1[gi], d2[gi])
    opp = np.where(sd == 0, d2[gi], d1[gi])
    r = np.where(sd == 0, G["r1"][gi], G["r2"][gi])
    return own, opp, r, G["et"][gi], G["at"][gi]


def step2(G, seqs, D1=None, D2=None) -> dict:
    """`division` 이 경기 전 값인지 뒤 값인지 — 상대 등급 코드와 승격 판 승·무 비율(w − w0)."""
    new_eq = old_eq = n = 0
    w_hit = w_n = 0
    promo_people = {}
    w_games: dict[int, set] = {}
    for p, seq in seqs.items():
        own, opp, r, _, _ = seq_arrays(G, seq, D1, D2)
        for i in range(1, len(own)):
            a, b = int(own[i - 1]), int(own[i])
            if a == b or not (_is_a7(a) and _is_a7(b)):
                continue
            n += 1
            new_eq += int(opp[i]) == b
            old_eq += int(opp[i]) == a
            if b < a:                    # 승격(코드가 작아짐)
                promo_people[p] = seq
                if int(r[i]) in (1, 0, -1):
                    w_n += 1
                    w_hit += int(r[i]) >= 0
                    w_games.setdefault(p, set()).add(i)
    w0_hit = w0_n = 0
    for p, seq in promo_people.items():
        _, _, r, _, _ = seq_arrays(G, seq, D1, D2)
        skip = w_games.get(p, set())
        for i, x in enumerate(r.tolist()):
            if i in skip or x not in (1, 0, -1):
                continue
            w0_n += 1
            w0_hit += x >= 0
    diff = (new_eq - old_eq) / n if n else float("nan")
    w = w_hit / w_n if w_n else float("nan")
    w0 = w0_hit / w0_n if w0_n else float("nan")
    if not n or not w_n or not w0_n:
        conv, why = None, "바뀜 없음"
    elif diff >= A7_PREPOST_DIFF:
        conv, why = "pre", ""
    elif diff <= -A7_PREPOST_DIFF:
        conv, why = "post", ""
    else:
        conv, why = None, "상대 코드 차이가 ±0.10 안"
    if conv == "post" and w - w0 < A7_W_DIFF:
        conv, why = None, "엇갈림(경기 뒤인데 w − w0 < 0.10)"
    elif conv == "pre" and w - w0 >= A7_W_DIFF:
        conv, why = None, "엇갈림(경기 전인데 w − w0 ≥ 0.10)"
    return {"n": n, "diff": diff, "w": w, "w0": w0, "w_n": w_n, "w0_n": w0_n, "promo_people": len(promo_people),
            "conv": conv, "why": why}


def build_sets(G, seqs, conv, D1=None, D2=None) -> dict:
    """회차 — 바뀜 직후 첫 판부터 10판(안에서 바뀌면 거기서 끝) · 유지면 10판 간격으로 이어 셈(사슬).
    사람마다 시즌 첫 회차는 앞에 바뀜이 없어 만들어지지 않는다(4번 "시즌 시작 뒤 첫 회차" 뺌)."""
    rows = []          # (사람, 등급, 방향, 끝난 판째, 승점, 결과, 뺌 사유, 누적 승점 10칸, 절반)
    first_pos = []     # 첫 회차 시작에서 다음 바뀜까지 몇 판째(16판까지)
    gap = G["gap"]
    for p, seq in seqs.items():
        own, _, r, et, at = seq_arrays(G, seq, D1, D2)
        own, r, et, at = own.tolist(), r.tolist(), et.tolist(), at.tolist()
        L = len(own)
        if conv == "pre":
            gpre = own
            gpost = own[1:] + [None]
        else:
            gpre = [None] + own[:-1]
            gpost = own
        bad_person = bool(G["miss_owner"][p])
        gap_at = int(gap[p])
        # 바뀜: 판 c 가 끝나고 등급이 바뀜(gpost[c] != gpre[c])
        i = 0
        state = None   # (시작 판, 등급, 방향, 사슬 기원 시각)
        while i < L:
            if state is None:
                a, b = gpre[i], gpost[i]
                if a is not None and b is not None and a != b and _is_a7(b) and a != NONE and b != NONE:
                    came = "down" if b > a else "up"    # 코드가 커짐 = 위에서 내려옴
                    state = (i + 1, b, came, at[i])
                    # 첫 회차 위치(가정 확인) — 끝을 모르면 세지 않는다
                    for k in range(1, 17):
                        j = i + k
                        if j >= L or gpost[j] is None or gpost[j] == NONE:
                            break
                        if gpost[j] != b:
                            first_pos.append(k)
                            break
                    else:
                        first_pos.append(0)   # 16판 동안 안 바뀜
                i += 1
                continue
            start, grade, came, origin = state
            pts, cum, why, end_k, outcome = 0, [], "", None, None
            j = start
            for k in range(1, SET_LEN + 1):
                if j >= L or gpost[j] is None or gpost[j] == NONE:
                    why = "끝 모름"
                    break
                if gpre[j] != grade:
                    why = "등급 어긋남"
                    break
                x = r[j]
                if x not in PTS:
                    why = why or "결과 없음"
                    x = -1
                if et[j] != 0:
                    why = why or "몰수·탈주"
                pts += PTS[x]
                cum.append(pts)
                if gpost[j] != grade:
                    end_k, outcome = k, ("promo" if gpost[j] < grade else "demote")
                    break
                j += 1
            else:
                end_k, outcome = SET_LEN, "stay"
                j -= 1
            if end_k is None:
                state = None
                i = max(j, i + 1)
                continue
            if bad_person:
                why = why or "missing"
            if gap_at != NONE and origin <= gap_at:
                why = why or "gap_at 앞"
            rows.append((p, grade, came, end_k, pts, outcome, why, cum + [cum[-1]] * (SET_LEN - len(cum)), int(G["half"][p])))
            if outcome == "stay":
                state = (j + 1, grade, "stay", origin)
                i = j + 1
            else:
                state = None
                i = j          # 끝난 판에서 다음 바뀜을 다시 본다
    first = np.array(first_pos) if first_pos else np.zeros(0, int)
    return {"rows": rows, "first_pos": first}


def assume_10(first) -> dict:
    n = len(first)
    a = float(np.isin(first, range(1, 7)).sum() / n) if n else float("nan")
    b = float(np.isin(first, range(11, 17)).sum() / n) if n else float("nan")
    return {"n": n, "p1_6": a, "p11_16": b, "ok": bool(n and not (b > a + A7_SET_SHIFT))}


def clean_sets(rows) -> dict:
    """4 · 5번 거르기 — 칸(등급, 방향)마다 설명 안 되는 바뀜이 1% 넘으면 그 칸만 뺀다 · 1000 승격은 뺀다."""
    keep = [x for x in rows if not x[6]]
    by_cell: dict = {}
    for x in keep:
        by_cell.setdefault((x[1], x[2]), []).append(x)
    out, dropped, unexplained = [], [], {}
    for cell, xs in by_cell.items():
        chg = [x for x in xs if x[5] != "stay"]
        bad = [x for x in chg if x[3] <= A7_EARLY_K or (x[5] == "promo" and x[4] <= A7_LOW_PROMO_PTS)]
        unexplained[f"{cell[0]}·{cell[1]}"] = [len(bad), len(chg)]
        if chg and len(bad) > A7_UNEXPLAINED_SHARE * len(chg):
            dropped.append(f"{cell[0]}·{cell[1]}")
            continue
        badset = set(map(id, bad))
        out.extend(x for x in xs if id(x) not in badset and not (x[1] == 1000 and x[5] == "promo"))
    return {"sets": out, "dropped": sorted(dropped), "unexplained": unexplained, "removed_why": _why_counts(rows)}


def _why_counts(rows) -> dict:
    c: dict = {}
    for x in rows:
        c[x[6] or "씀"] = c.get(x[6] or "씀", 0) + 1
    return c


DIRS = ("up", "down", "stay")


def _hits(cum, end_k):
    """문턱마다 처음 닿는 판째(1~10 · 0 = 안 닿음) — 승격: 누적 ≥ Tp · 강등: 누적 + 3(10 − i) ≤ Td. 끝난 판째 뒤는 모른다."""
    N = len(cum)
    kk = np.arange(1, SET_LEN + 1)
    known = kk[None, :] <= end_k[:, None]
    q = cum + 3 * (SET_LEN - kk)[None, :]
    hp = np.zeros((len(A7_GRID), N), np.int8)
    hd = np.zeros((len(A7_GRID), N), np.int8)
    for t in A7_GRID:
        a = (cum >= t) & known
        hp[t] = np.where(a.any(1), a.argmax(1) + 1, 0)
        b = (q <= t) & known
        hd[t] = np.where(b.any(1), b.argmax(1) + 1, 0)
    return hp, hd


def _correct(rule, tp, td, cum, end_k, out_code, hp, hd):
    """규칙(tp · td 문턱)의 예측이 (끝난 판째, 결과)와 같은가 — out_code 1 승격 · 0 유지 · −1 강등."""
    p10 = cum[:, -1]
    if rule in ("R1", "R2"):
        pred = np.where(p10 >= tp, 1, np.where(p10 <= td, -1, 0))
        return (end_k == SET_LEN) & (pred == out_code)
    a, b = hp[tp].astype(np.int16), hd[td].astype(np.int16)
    a = np.where(a == 0, 99, a)
    b = np.where(b == 0, 99, b)
    first = np.minimum(a, b)
    pk = np.where(first == 99, SET_LEN, first)
    pc = np.where(first == 99, np.where(p10 >= tp, 1, np.where(p10 <= td, -1, 0)), np.where(a <= b, 1, -1))
    # 10판째에 닿는 것은 위 식과 같다(q_10 = 누적)
    return (pk == end_k) & (pc == out_code)


def fit_rules(sets) -> dict:
    """R1 · R2 · R3 — 구단주 절반(해시)으로 맞추고 나머지 절반에서 전체 · 경계 띠 맞힘률. 99% 를 넘은 것 중 단순한 쪽."""
    if not sets:
        return {"pick": None, "rules": {}}
    cum = np.array([x[7] for x in sets], dtype=np.int16)
    end_k = np.array([x[3] for x in sets], dtype=np.int16)
    out_code = np.array([{"promo": 1, "stay": 0, "demote": -1}[x[5]] for x in sets], dtype=np.int8)
    dirs = np.array([x[2] for x in sets])
    half = np.array([x[8] for x in sets], dtype=np.int8)
    pts = np.array([x[4] for x in sets], dtype=np.int16)
    band = (pts >= A7_BAND_PTS[0]) & (pts <= A7_BAND_PTS[1])
    hp, hd = _hits(cum, end_k)
    grid = [(tp, td) for tp in A7_GRID for td in A7_GRID if td < tp]
    res = {}
    for rule in ("R1", "R2", "R3"):
        groups = [np.ones(len(sets), bool)] if rule == "R1" else [dirs == d for d in DIRS]
        th, ok_all = {}, np.zeros(len(sets), bool)
        mode = "R3" if rule == "R3" else "R1"
        for gname, g in zip(["all"] if rule == "R1" else list(DIRS), groups):
            fit = g & (half == 0)
            best, best_acc = None, -1.0
            if fit.any():
                cf, ef, of, hpf, hdf = cum[fit], end_k[fit], out_code[fit], hp[:, fit], hd[:, fit]
            for tp, td in grid:
                if not fit.any():
                    break
                acc = _correct(mode, tp, td, cf, ef, of, hpf, hdf).mean()
                if acc > best_acc:
                    best, best_acc = (tp, td), acc
            if best is None:
                continue
            th[gname] = best
            ok_all[g] = _correct(mode, best[0], best[1], cum[g], end_k[g], out_code[g], hp[:, g], hd[:, g])
        ev = half == 1
        acc = float(ok_all[ev].mean()) if ev.any() else float("nan")
        accb = float(ok_all[ev & band].mean()) if (ev & band).any() else float("nan")
        res[rule] = {"th": th, "acc": acc, "acc_band": accb, "ok": bool(acc > A7_ACC and accb > A7_ACC)}
    pick = next((k for k in ("R1", "R2", "R3") if res[k]["ok"]), None)
    return {"pick": pick, "rules": res, "n": len(sets)}


def prob_table(sets, min_owners=N24_MIN_OWNERS) -> list:
    """9번 — (등급, 방향, 끝난 판째, 회차 끝 승점) → 승격 · 유지 · 강등 수 · 칸 하한(서로 다른 구단주 > 50)."""
    cells: dict = {}
    for x in sets:
        k = (x[1], x[2], x[3], x[4])
        c = cells.setdefault(k, {"promo": 0, "stay": 0, "demote": 0, "owners": set()})
        c[x[5]] += 1
        c["owners"].add(x[0])
    out = []
    for (g, d, k, p), c in sorted(cells.items()):
        if len(c["owners"]) > min_owners:
            out.append({"grade": g, "came": d, "end_k": k, "pts": p, "promo": c["promo"], "stay": c["stay"],
                        "demote": c["demote"], "owners": len(c["owners"])})
    return out


def a7_pipeline(G, seqs, conv, D1=None, D2=None, sets_count_for_missing=True) -> dict:
    bs = build_sets(G, seqs, conv, D1, D2)
    a10 = assume_10(bs["first_pos"])
    cl = clean_sets(bs["rows"])
    miss_rows = int(G["miss_rows"])
    n_sets = len(bs["rows"])
    out = {"conv": conv, "assume_10": a10, "sets_raw": n_sets, "sets": len(cl["sets"]), "dropped_cells": cl["dropped"],
           "unexplained": cl["unexplained"], "removed_why": cl["removed_why"], "missing_rows": miss_rows}
    if sets_count_for_missing and n_sets and miss_rows > A7_MISSING_SHARE * n_sets:
        out["verdict"] = f"멈춤(missing {miss_rows} > 회차 {n_sets} × 0.5%)"
        return out
    if not a10["ok"]:
        out["verdict"] = "A7 뺌(회차 10판 가정이 틀림)"
        return out
    if not cl["sets"]:
        out["verdict"] = "A7 · N22 뺌(남은 칸 0)"
        return out
    fit = fit_rules(cl["sets"])
    out["fit"] = fit
    out["verdict"] = f"규칙표 {fit['pick']}" if fit["pick"] else "확률 표"
    out["_sets"] = cl["sets"]
    return out


# ── 가짜 A7 ──────────────────────────────────────────────────────────────────

LADDER = (900,) + A7_GRADES        # 승격 = 왼쪽 한 칸

FAKE_RULES = {   # 심는 규칙 — (승격 문턱, 강등 문턱) 방향마다 · R3 은 조기 확정
    "R1": {"up": (15, 8), "down": (15, 8), "stay": (15, 8)},
    "R2": {"up": (15, 10), "down": (12, 8), "stay": (15, 8)},   # 아래에서(up) 15 · 위에서(down) 12 · 강등 10 · 8
    "R3": {"up": (15, 10), "down": (12, 8), "stay": (15, 8)},
}


def _step(grade, move):
    i = LADDER.index(grade)
    if move > 0:
        return LADDER[max(i - 1, 0)]
    return LADDER[min(i + 1, len(LADDER) - 1)]


def plant_divisions(G, seqs, rule, conv) -> tuple[np.ndarray, np.ndarray]:
    """실제 판 순서 · 결과는 그대로 · 대상의 자기 쪽 `division` 만 아는 규칙으로 다시 만든다(경기 전 또는 뒤 값)."""
    D1, D2 = G["d1"].copy(), G["d2"].copy()
    early = rule == "R3"
    th = FAKE_RULES[rule]
    for p, seq in seqs.items():
        own, _, r, _, _ = seq_arrays(G, seq)
        start = next((int(x) for x in own.tolist() if _is_a7(int(x))), None)
        if start is None:
            continue
        grade, came, k, pts = start, "stay", 0, 0
        vals = []
        for x in r.tolist():
            before = grade
            k += 1
            pts += PTS.get(x, 0)
            tp, td = th[came]
            move = 0
            if grade != 900:
                if early:
                    if pts >= tp:
                        move = 1
                    elif pts + 3 * (SET_LEN - k) <= td:
                        move = -1
                elif k == SET_LEN:
                    move = 1 if pts >= tp else (-1 if pts <= td else 0)
            if move and _step(grade, move) == grade:   # 맨 아래에서 강등 — 움직일 곳이 없어 10판째 유지로
                move = 0
            if move:
                grade = _step(grade, move)
                came = "up" if move > 0 else "down"
                k, pts = 0, 0
            elif k == SET_LEN:
                came, k, pts = "stay", 0, 0
            vals.append(before if conv == "pre" else grade)
        gi, sd = seq[:, 0], seq[:, 1]
        v = np.array(vals, dtype=np.int16)
        D1[gi[sd == 0]] = v[sd == 0]
        D2[gi[sd == 1]] = v[sd == 1]
    return D1, D2


def run_fake_a7(G, guard, log=print) -> dict:
    seqs = sequences(G)
    out = {}
    for rule in ("R1", "R2", "R3"):
        for conv in ("pre", "post"):
            guard.start(f"가짜 A7 {rule} {conv}")
            D1, D2 = plant_divisions(G, seqs, rule, conv)
            r = a7_pipeline(G, seqs, conv, D1, D2, sets_count_for_missing=False)
            fit = r.get("fit") or {}
            pr = (fit.get("rules") or {}).get(rule) or {}
            ok = fit.get("pick") == rule and pr.get("acc", 0) > A7_ACC and pr.get("acc_band", 0) > A7_ACC
            r.pop("_sets", None)
            out[f"{rule}-{conv}"] = {**r, "ok": bool(ok)}
            log(f"가짜 A7 {rule} {conv}: 고름 {fit.get('pick')} · 맞힘 {pr.get('acc')} · 띠 {pr.get('acc_band')} → {'OK' if ok else 'FAIL'}")
    out["ok"] = all(v["ok"] for k, v in out.items() if k != "ok")
    return out


# ── 작은 DB(A7 2번) ──────────────────────────────────────────────────────────

def make_small_db(path: Path, mode: str, seed: int = SMALL_DB_SEED) -> int:
    """사람 40명 · 사람마다 승격 바뀜 10번 넘게 — mode: "pre" · "post" · "cross"(상대 코드는 경기 전 · 결과는 경기 뒤).
    상대 코드는 그 판 시점 등급과 0.8 로 같다. 결과: 승격을 끝낸 판(경기 뒤 꼴의 "처음 보인 판")은 승·무 0.9 · 그 밖 0.6.
    강등 바뀜에는 0.9 를 심지 않는다(7회차 [중] 2). 돌려주는 값 = 심은 승격 바뀜 수."""
    import dev_matches
    rng = np.random.default_rng(seed)
    if path.exists():
        path.unlink()
    c = sqlite3.connect(path)
    c.executescript(dev_matches.SCHEMA)
    grades = list(A7_GRADES[1:])          # 1000 은 승격이 900 으로 가 빼고 1100~
    mid = 0
    n_promo = 0
    t = 1_793_300_000
    for person in range(1, 41):
        oid = person
        c.execute("INSERT INTO owners(id, ouid, profile_sn) VALUES (?, ?, ?)", (oid, f"p{person:03d}", person))
        gi = int(rng.integers(len(grades) // 2, len(grades)))
        segs = []                          # (등급, 길이, 끝 바뀜 +1 승격 · −1 강등)
        promos = 0
        while promos < 12:
            move = 1 if (rng.random() < 0.7 and gi > 0) else (-1 if gi < len(grades) - 1 else 1)
            segs.append((grades[gi], int(rng.integers(5, 16)), move))
            gi += -1 if move > 0 else 1
            promos += move > 0
        segs.append((grades[gi], 8, 0))
        first_at = t
        games = []                         # (true grade, 이 판이 바뀜을 끝냄, 다음 등급)
        for s, (g, L, move) in enumerate(segs):
            for k in range(L):
                last = k == L - 1 and move != 0
                games.append((g, last, segs[s + 1][0] if last else g, move if last else 0))
        prev_end = None
        for j, (g, last, nxt, move) in enumerate(games):
            mid += 1
            t += 600
            # 기록된 division
            if mode == "post":
                rec = nxt if last else g
            else:
                rec = g
            opp = g if rng.random() < 0.8 else grades[int(rng.integers(len(grades)))]
            if mode == "post":
                hot = last and move > 0
            elif mode == "cross":
                hot = prev_end is not None and prev_end > 0       # 승격 직후 첫 판(경기 전 꼴의 처음 보인 판)
            else:
                hot = False
            p_ok = 0.9 if hot else 0.6
            u = rng.random()
            res = 1 if u < p_ok * 0.8 else (0 if u < p_ok else -1)
            c.execute("INSERT INTO games(match_id, at, end_type, o1, d1, r1, l1, o2, d2, r2, l2) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (f"m{mid:07d}", t, 0, oid, rec, res, None, 1000 + oid, opp, -res, None))
            prev_end = move if last else None
            n_promo += last and move > 0
        c.execute("INSERT INTO cursor(owner, top_id, top_at, back_done, base_at) VALUES (?, ?, ?, 0, ?)", (oid, "x", t, first_at))
    for o in range(1001, 1041):
        c.execute("INSERT INTO owners(id, ouid) VALUES (?, ?)", (o, f"q{o}"))
    c.commit()
    c.close()
    return n_promo


def selftest(tmp: Path, log=print) -> dict:
    out = {}
    tmp.mkdir(parents=True, exist_ok=True)
    want = {"pre": "pre", "post": "post", "cross": None}
    for mode, exp in want.items():
        p = tmp / f"small_{mode}.db"
        n = make_small_db(p, mode)
        G = freeze(p, 0, 1 << 40, log=lambda *_: None)
        s = step2(G, sequences(G))
        ok = s["conv"] == exp and n >= 400
        out[mode] = {**s, "planted": n, "ok": ok}
        log(f"작은 DB {mode}: 판정 {s['conv']} (기대 {exp}) · 차이 {s['diff']:.3f} · w−w0 {s['w'] - s['w0']:.3f} · 승격 {n} → {'OK' if ok else 'FAIL'}")
        try:
            p.unlink()
        except OSError:
            pass
    out["ok"] = all(v["ok"] for v in out.values())
    return out


# ── 세기 ─────────────────────────────────────────────────────────────────────

def counts(G, log=print) -> dict:
    static = base_static(G)
    fr = frame(G, G["f1"], G["f2"], G["r1"], static)
    cs = cell_stats(fr)
    st = steps_1_4(cs)
    e = st["elig"]
    out = {"games": len(G["at"]), "n24_games": len(fr["idx"]), "cells": len(cs["cell"]), "elig": int(e.sum()),
           "elig_band": {"800·900": int((e & (cs["band"] == 0)).sum()), "그 밖": int((e & (cs["band"] == 1)).sum())},
           "bins": {}}
    for lo, hi in LIFT_BINS:
        m = e & (cs["n_games"] >= lo) & ((cs["n_games"] < hi) if hi else True)
        out["bins"][f"{lo}~{hi or ''}"] = {"cells": int(m.sum()), "n": min(int(m.sum()), LIFT_PER_BIN)}
    seqs = sequences(G)
    out["a7_people"] = len(seqs)       # 2번 판정은 세기에 넣지 않는다(판정 몫)
    for conv in ("pre", "post"):
        bs = build_sets(G, seqs, conv)
        rows = bs["rows"]
        promo10 = [x for x in rows if x[5] == "promo" and not x[6]]
        out[f"a7_{conv}"] = {"sets": len(rows), "why": _why_counts(rows), "assume_10": assume_10(bs["first_pos"]),
                             "promo": len(promo10), "promo_at_10": sum(1 for x in promo10 if x[3] == SET_LEN)}
    out["missing_rows"] = int(G["miss_rows"])
    log(json.dumps(out, ensure_ascii=False, indent=1, default=_jsonable))
    return out


# ── 진입점 ───────────────────────────────────────────────────────────────────

def _prepare(log):
    start, t0 = season_start(config.DB_PATH, RANK_DB), t0_epoch()
    if t0 - start < MIN_SEASON_DAYS * DAY_S:
        raise Stop(f"T0 − SEASON_START 가 {MIN_SEASON_DAYS}일 미만")
    sid = season_id_of(config.DB_PATH, start, t0)
    G = load_frozen(OUT_DIR / "data.npz", DEV_DB, start, t0, log=log)
    head = {"rule_commit": RULE_COMMIT, "tool_sha256": tool_hashes(), "season_start": start, "season_id": sid,
            "t0": t0, "games": len(G["at"])}
    return G, head


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="112단계 관문 측정")
    g = ap.add_mutually_exclusive_group(required=True)
    for f in ("--counts", "--selftest", "--fake", "--judge", "--status"):
        g.add_argument(f, action="store_true")
    a = ap.parse_args(argv)
    log = lambda m: print(m, flush=True)  # noqa: E731
    if a.status:
        for f in ("data.npz", "counts.json", "selftest.json", "fake.json", "judge.json"):
            p = OUT_DIR / f
            log(f"{f}: {'있음 ' + datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='minutes') if p.exists() else '없음'}")
        return 0
    prog = Progress()
    guard = Guard(progress=prog)
    try:
        guard.start("준비")
        if a.selftest:
            r = selftest(OUT_DIR / "tmp")
            save_json(OUT_DIR / "selftest.json", {"rule_commit": RULE_COMMIT, "tool_sha256": tool_hashes(), **r})
            return 0 if r["ok"] else 1
        G, head = _prepare(log)
        if a.counts:
            save_json(OUT_DIR / "counts.json", {**head, **counts(G, log)})
            return 0
        if a.fake:
            r = run_fakes(G, guard, log)
            r["a7"] = run_fake_a7(G, guard, log)
            r["peak_mem"] = peak_mem()
            save_json(OUT_DIR / "fake.json", {**head, **r})
            return 0
        if a.judge:
            fk = load_json(OUT_DIR / "fake.json")
            st = load_json(OUT_DIR / "selftest.json")
            if not fk or fk.get("tool_sha256") != head["tool_sha256"] or not st or st.get("tool_sha256") != head["tool_sha256"]:
                raise Stop("fake.json · selftest.json 이 없거나 도구 해시가 다름 — --selftest · --fake 먼저")
            out = {**head}
            n24_stop = not fk["fake0"]["ok"] or "멈춤" in fk["confound"]["verdict"] or "멈춤" in fk["lift"]["verdict"]
            n24_drop = [v for v in (fk["confound"]["verdict"], fk["lift"]["verdict"]) if v.startswith("N24 뺌")]
            if n24_stop:
                out["n24"] = {"verdict": "판정 안 함(가짜 단언 FAIL — 원인 찾기)"}
            elif n24_drop:
                out["n24"] = {"verdict": n24_drop[0]}
            else:
                out["n24"] = judge_n24(G, guard, fk["lift"]["conclusion_min_games"], log)
            guard.start("A7 실제")
            seqs = sequences(G)
            s2 = step2(G, seqs)
            out["a7_step2"] = s2
            if not st.get("ok") or not fk["a7"]["ok"]:
                out["a7"] = {"verdict": "판정 안 함(작은 DB 또는 가짜 A7 FAIL — 원인 찾기)"}
            elif s2["conv"] is None:
                out["a7"] = {"verdict": f"A7 · N22 뺌(2번: {s2['why']})"}
            else:
                r = a7_pipeline(G, seqs, s2["conv"])
                sets = r.pop("_sets", None)
                if sets is not None and r["verdict"] == "확률 표":
                    r["table"] = prob_table(sets)
                out["a7"] = r
            log(f"A7 실제: {out['a7']['verdict']}")
            out["peak_mem"] = peak_mem()
            save_json(OUT_DIR / "judge.json", out)
            return 0
    except Stop as e:
        log(f"[멈춤] {e}")
        save_json(OUT_DIR / "stopped.json", {"rule_commit": RULE_COMMIT, "why": str(e), "at": datetime.now().isoformat(timespec="seconds")})
        return 2
    finally:
        prog.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
