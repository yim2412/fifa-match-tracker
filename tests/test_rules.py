"""규칙 검사 — CLAUDE.md 에 "이렇게 한다"로만 적혀 있어 검토자가 매번 눈으로 대조하던 것을 기계로.

검토 방법 13(ROADMAP): 여기 있는 규칙은 계획 검토에서 다시 보지 않는다. 규칙마다 두 단언 —
① 지금 코드에 위반이 0 ② **위반을 심은 가짜 소스를 실제로 잡는다**(못 잡으면 ①이 비어 있어도 통과한다).

    python tests/test_rules.py
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import re
import sqlite3
import sys
import tempfile
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_FILES = sorted(p for p in ROOT.glob("*.py"))
ALL_PY = APP_FILES + sorted(ROOT.glob("tools/*.py")) + sorted(ROOT.glob("tests/*.py"))

# 색은 theme.py 에서만 — 예외는 넥슨 데이터센터 원본 색(능력치 등급표: 화면 테마가 아니라 넥슨이 정한 값)
COLOR_ALLOW = {("playerinfo.py", "STAT_COLOR_BUCKETS"),
               ("playerinfo.py", "_SKILLMOVE_ON")}          # 스크래핑 표식(HTML 안의 색 글자)
# 임시 정렬(TEMP B-TREE)을 봐줄 표 — 행이 수십 개라 통째 정렬이 공짜다(계정·시즌·수집 상태).
# 1만 행대(matches · match_players · snapshot_rows · elo_history)는 여기 넣지 않는다
SQL_SMALL_TABLES = {"accounts", "seasons", "team_colors", "collect_state", "collect_lock", "snapshots",
                    "cut_elo", "elo_track"}   # elo_track — 최대 config.ELO_TRACK_MAX(5)줄
# 그 밖에 봐줄 문장 — 앱이 안 타는 경로만
SQL_TEMP_ALLOW = {
    # 종류 없는 load_details 는 테스트만 쓴다 — 앱은 항상 종류를 준다(app_main.load_saved)
    "store.py:load_details": re.compile(r"FROM matches m JOIN"),
    # 옛 DB 의 겹친 ELO 줄 정리 — 고유 인덱스를 만들기 전에 DB 마다 한 번만 돈다(인덱스가 생기면 다시 안 돈다)
    "store.py:_ensure_elo_unique": re.compile(r"DELETE FROM elo_history WHERE rowid NOT IN"),
}
# 승률 표본 판정(2.6.1 A6) — 문턱과의 비교식은 models.is_weak(rated, 문턱) 안에서만. 아래는 승률 표본이 아닌 비교
# (파일, 함수, 문턱 이름) → 이유. 줄이 아니라 비교식 단위 — 한 줄에 대상·제외가 같이 있을 수 있다(팀컬러 상대 수)
WEAK_FILES = ("models.py", "stats.py", "analysis.py", "widgets.py", "dashboard.py", "app_main.py",
              "squad_timeline.py", "check_api.py")
WEAK_ALLOW = {
    ("models.py", "is_weak", "min_n"): "판정 자신",
    ("app_main.py", "_fit_match_extra", "MATCH_EXTRA_MIN_PX"): "글꼴 크기(px) — 표본이 아니다(57 K3 상대 정보 열 접기)",
    ("analysis.py", "_contrast", "MIN_BASE"): "선제골 *비율* 표본(경기 수) — 승률 아님",
    ("analysis.py", "_contrast", "MIN_FLOW"): "선제골 *비율* 표본(경기 수) — 승률 아님",
    ("analysis.py", "_contrast", "MIN_COND"): "선제골 *비율* 표본(경기 수) — 승률 아님",
    ("analysis.py", "_minute_rules", "MIN_GOALS"): "골 수",
    ("analysis.py", "_finishing_rules", "MIN_SHOTS"): "슛 수",
    ("analysis.py", "_goal_type_rules", "MIN_GOALS"): "골 수",
    ("analysis.py", "_creation_rules", "MIN_SHOTS"): "유효슛 수(N19)",
    ("analysis.py", "_creation_rules", "MIN_BASE"): "경기 수 — 승률이 아니라 경기당 유효슛 표본(N19)",
    ("analysis.py", "_creation_rules", "N19_CREATE_MIN_REL"): "상대 대비 크기 문턱(%) — 표본이 아니다",
    ("analysis.py", "_half_rules", "MIN_GOALS"): "골 수(N19)",
    ("stats.py", "enough", "MIN_BUCKET_SHOTS"): "슛 수",
    ("stats.py", "key_players", "min_games"): "선수 출전 수",
    ("stats.py", "compare_summary", "COMPARE_Z_MIN"): "z 값 문턱 — 표본이 아니다(D1)",
    ("stats.py", "compare_summary", "COMPARE_MIN_GAMES"): "경기별 값의 분산을 낼 최소 경기 수 — 승률 표본 아님(D1)",
    ("app_main.py", "_render_compare_extra", "COMPARE_MIN_GAMES"): "위와 같은 경기 수 — 요약을 못 내는 이유 문구(D1)",
    ("stats.py", "weak", "PLAYER_RATING_MIN_GAMES"): "선수 평점 출전 수",
    ("dashboard.py", "_render_clutch", "MIN_GAUGE_GAMES"): "셋째 칸 지킨 비율 = (승+무)÷선제골 경기 — 승률 아님",
    ("widgets.py", "_refit", "_min_pt"): "글자 크기",
    ("widgets.py", "_fit", "MIN_FONT_PX"): "글자 크기",
    ("widgets.py", "shows_price_line", "PRICE_MIN_H"): "위젯 높이",
    ("widgets.py", "shows_face", "FACE_MIN_H"): "위젯 높이",
    ("widgets.py", "_paint_assists", "X_MIN"): "좌표",
    ("app_main.py", "eta_text", "ETA_MIN_DONE"): "남은 시간",
    ("app_main.py", "initial_window", "MIN_WINDOW"): "창 크기",
    ("app_main.py", "_render_attack_route", "N18_MIN_ROUTE"): "루트 슛 수 — 결정력(골÷슛) 차이 표본, 승률 아님(N18)",
    ("app_main.py", "_render_ranker_pick", "RANKER_PICK_MIN_RANKERS"): "랭커 픽 인원",
    ("app_main.py", "_render_recommend", "RANKER_PICK_MIN_RANKERS"): "랭커 픽 인원",
    ("app_main.py", "_rec_sources_text", "RANKER_PICK_MIN_RANKERS"): "랭커 픽 인원",
    ("app_main.py", "_render_rank_trend", "TREND_MIN_SHARE"): "메타 점유",
    ("app_main.py", "_fill_trait_forms", "TRAIT_MIN_PEOPLE"): "특성 인원",
    ("app_main.py", "_render_ranker_compare", "few_me"): "선수 출전 수",
    ("app_main.py", "_render_ranker_compare", "few_r"): "랭커 표본(matchCount)",
    ("app_main.py", "_teamcolor_weak", "TEAMCOLOR_MIN_OPPONENTS"): "상대 수",
    ("app_main.py", "_fill_scout", "few"): "선수 출전 수",
    ("app_main.py", "_build_my_record", "PLAYER_TREND_MIN_SHOTS"): "슛 수",
    ("app_main.py", "_render_players", "few"): "선수 출전 수 ⚠ — 선수 지표 전체의 표본 표시",
    ("app_main.py", "_render_pass_style", "min_tries"): "패스 시도 수",
    ("app_main.py", "rate", "min_tries"): "패스 시도 수",
    ("app_main.py", "_render_pass_style", "few"): "패스 스타일 경기 수 — 승률 아님",
}
MODAL_STATIC = {"QFileDialog", "QInputDialog", "QColorDialog", "QFontDialog", "QMessageBox"}
MODAL_GUARDED_BASE = {"QDialog.exec", "QApplication.exec"}   # 하위 클래스의 exec() 도 이걸로 막힌다


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):     # 'mod.attr' — 받는 쪽이 이름이 아니면 '.attr'
        return (f.value.id if isinstance(f.value, ast.Name) else "") + "." + f.attr
    return ""


def _kw(node: ast.Call, name: str):
    return next((k.value for k in node.keywords if k.arg == name), None)


def _const(v) -> object:
    return v.value if isinstance(v, ast.Constant) else None


# ── 규칙 ─────────────────────────────────────────────────────────────────────

def check_combo(srcs: dict[str, str]) -> list[str]:
    """QComboBox 를 직접 만들지 않는다 — 휠이 지나가면 값이 바뀐다(전역 규칙 8 · NoScrollComboBox)."""
    out = []
    for rel, src in srcs.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and _call_name(node) in ("QComboBox", "QtWidgets.QComboBox"):
                out.append(f"{rel}:{node.lineno} QComboBox(...) — NoScrollComboBox 를 쓴다")
    return out


def check_dialog_fit(srcs: dict[str, str]) -> list[str]:
    """새 대화상자는 fit_to_screen 으로 연다 — 고정 크기는 150% 노트북에서 넘친다(CLAUDE.md PyQt 9)."""
    out = []
    for rel, src in srcs.items():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    (isinstance(b, ast.Name) and b.id == "QDialog") for b in node.bases):
                if "fit_to_screen(" not in ast.get_source_segment(src, node):
                    out.append(f"{rel}:{node.lineno} class {node.name}(QDialog) — fit_to_screen 이 없다")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(src, node)
                makes = [c for c in ast.walk(node) if isinstance(c, ast.Call) and _call_name(c) == "QDialog"]
                if makes and "fit_to_screen(" not in body:
                    out.append(f"{rel}:{makes[0].lineno} {node.name}() 의 QDialog(...) — fit_to_screen 이 없다")
    return out


def check_encoding(srcs: dict[str, str]) -> list[str]:
    """글자 파일·자식 프로세스 출력은 인코딩을 적는다 — 이 PC 는 UTF-8, 남의 PC 는 CP949(전역 인코딩 1·3)."""
    out = []
    for rel, src in srcs.items():
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            has_enc = _kw(node, "encoding") is not None
            if name == "open":
                mode = _const(node.args[1]) if len(node.args) > 1 else _const(_kw(node, "mode"))
                if not (isinstance(mode, str) and "b" in mode) and not has_enc:
                    out.append(f"{rel}:{node.lineno} open(...) — encoding= 이 없다")
            elif name.endswith((".read_text", ".write_text")) and not has_enc:
                out.append(f"{rel}:{node.lineno} {name.lstrip('.')}(...) — encoding= 이 없다")
            elif name in ("subprocess.run", "subprocess.Popen", "subprocess.check_output"):
                text = _const(_kw(node, "text")) or _const(_kw(node, "universal_newlines"))
                if text and not has_enc:
                    out.append(f"{rel}:{node.lineno} {name}(text=True) — encoding= 이 없다(실패 원인이 None 으로 사라진다)")
    return out


def check_signals(srcs: dict[str, str]) -> list[str]:
    """스레드 → 화면 신호에 list·dict 형을 쓰지 않는다 — PyQt 가 통째로 깊은 복사한다(CLAUDE.md PyQt 8)."""
    out = []
    for rel, src in srcs.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and _call_name(node) == "pyqtSignal":
                for a in node.args:
                    if isinstance(a, ast.Name) and a.id in ("list", "dict"):
                        out.append(f"{rel}:{node.lineno} pyqtSignal({a.id}, …) — object 로")
    return out


def guarded_modals(smoke_src: str) -> set[str]:
    """test_ui_smoke 머리의 모달 차단이 막는 이름들 — 'QMessageBox.warning' · 'QDialog.exec' 꼴."""
    got = set(re.findall(r"^(\w+\.\w+) = _no_modal", smoke_src, re.M))
    for names, cls in re.findall(r"for _n in \(([^)]*)\):\s*\n\s*setattr\((\w+)", smoke_src):
        got |= {f"{cls}.{n}" for n in re.findall(r"\"(\w+)\"", names)}
    return got


def check_modals(srcs: dict[str, str], guarded: set[str]) -> list[str]:
    """앱이 쓰는 모달은 화면 스모크가 막고 있어야 한다 — 안 막으면 회귀가 '실패' 대신 '멈춤'(CLAUDE.md PyQt 6)."""
    out = []
    for rel, src in srcs.items():
        tree = ast.parse(src)
        menus: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and _call_name(node.value) == "QMenu":
                menus |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            cls, _, attr = name.partition(".")
            if cls in MODAL_STATIC and attr and name not in guarded:
                if attr.startswith("get") or attr in ("warning", "information", "critical", "question",
                                                      "about", "aboutQt"):
                    out.append(f"{rel}:{node.lineno} {name}(...) — test_ui_smoke 모달 차단에 없다")
            if attr in ("exec", "exec_") and cls in menus and "QMenu.exec" not in guarded:
                out.append(f"{rel}:{node.lineno} {cls}.exec() (QMenu) — test_ui_smoke 모달 차단에 없다")
    return out


HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def check_colors(srcs: dict[str, str]) -> list[str]:
    """색은 theme.py 에서만 — 테마를 바꿀 때 고칠 곳이 하나여야 한다(CLAUDE.md 파일 표)."""
    out = []
    for rel, src in srcs.items():
        if rel == "theme.py":
            continue
        tree = ast.parse(src)
        allowed: list[tuple[int, int]] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and (rel, t.id) in COLOR_ALLOW:
                        allowed.append((node.lineno, node.end_lineno))
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if any(a <= line <= b for a, b in allowed):
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and HEX.fullmatch(node.value.strip()):
                out.append(f"{rel}:{line} 색 \"{node.value}\" — theme.py 에 둔다")
            if isinstance(node, ast.Call) and _call_name(node) == "QColor" and len(node.args) >= 3 \
                    and all(isinstance(a, ast.Constant) for a in node.args[:3]):
                out.append(f"{rel}:{line} QColor(r, g, b) — theme.py 에 둔다")
    return out


def _threshold(name: str) -> bool:
    """문턱 이름 — 밑줄로 자른 조각에 min · need · few* (대소문자 무시). minute 같은 낱말은 안 걸린다."""
    toks = [t.lower() for t in name.split("_") if t]
    return any(t == "min" or t == "need" or t.startswith("few") for t in toks)


def _operand(node) -> str | None:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None


def _names_in(side) -> list[str]:
    """비교식 한쪽 안의 이름 전부 — `MIN_WINDOW[0] + fw` 처럼 첨자·덧셈 안의 문턱도(처음엔 맨 이름만 봐 창 크기 비교를
    놓쳤다). 불리는 함수 이름(`min(a, b)`)은 문턱이 아니라 뺀다."""
    funcs = {id(c.func) for c in ast.walk(side) if isinstance(c, ast.Call)}
    return [n for x in ast.walk(side) if id(x) not in funcs and (n := _operand(x))]


def check_weak(srcs: dict[str, str], allow: dict = WEAK_ALLOW) -> list[str]:
    """문턱과의 대소 비교는 is_weak(rated, 문턱) 로 — 승률 분모(rated)와 다른 수로 흐리면 흐림과 숫자가 어긋난다(A6).
    같은 함수 안에서 문턱을 대입받은 이름(별칭 `few = core.MIN_COND`)도 문턱으로 본다. 예외는 allow 표로만."""
    out = []
    for rel, src in srcs.items():
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            alias: dict[str, str] = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    src_name = _operand(node.value)
                    if src_name and (_threshold(src_name) or src_name in alias):
                        alias[node.targets[0].id] = alias.get(src_name, src_name)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Compare) or not any(
                        isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in node.ops):
                    continue
                for n in {n for side in (node.left, *node.comparators) for n in _names_in(side)}:
                    if not (_threshold(n) or n in alias):
                        continue
                    if (rel, fn.name, n) in allow or (rel, fn.name, alias.get(n, "")) in allow:
                        continue
                    out.append(f"{rel}:{node.lineno} {fn.name} — `{ast.unparse(node)}` 는 is_weak(rated, {n}) 로")
    return out


RATE_FILES = ("models.py", "stats.py", "squad_timeline.py", "rankmeta.py")


def rate_classes(srcs: dict[str, str]) -> set[str]:
    """승률을 내는 집계 클래스 — RateMixin 을 잇거나 몸통에서 win_pct 를 부른다(필드 이름으로 모으면
    승무패가 list 인 Discipline·ClutchSummary 가 빠지고 win: PassGroup 인 PassStyle 이 잘못 걸린다 — 검토 3회차)."""
    out = set()
    for src in srcs.values():
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.ClassDef) or node.name == "RateMixin":
                continue
            bases = {_operand(b) for b in node.bases}
            calls = {_call_name(c).split(".")[-1] for c in ast.walk(node) if isinstance(c, ast.Call)}
            if "RateMixin" in bases or "win_pct" in calls:
                out.add(node.name)
    return out


# ── 넥슨 시각 원문은 matchtime 에서만(63단계) ──────────────────────────────────
# 넥슨 오픈API 시각은 UTC 라 직접 파싱하면 9시간 이르다(KST 로 읽던 27곳이 그랬다). 함수 단위로 ⓐ 원문을 읽고(키·SQL 열 문자열)
# ⓑ 같은 함수에서 — 또는 그 함수가 부르는 같은 모듈 함수 한 단계 안에서 — fromisoformat·strptime·`[:10]` 꼴 자르기를 하면 걸린다.
# 원문을 받는 줄과 파싱하는 줄이 갈라진 자리(변수에 받고 다음 줄 · 도우미 함수)가 줄 단위 검사로는 빠지기 때문이다.
# `MatchSummary.match_date` 속성은 이미 KST 라 ⓐ 가 아니다(속성 이름 · 문자열 아님).
RAW_TIME_FILES = ("*.py", "tools/*.py", "server/*.py")
RAW_TIME_KEYS = {"matchDate", "achievementDate", "tradeDate", "createDate", "match_date", "trade_date"}
RAW_TIME_SQL = re.compile(r"\b(match_date|trade_date)\b")
RAW_TIME_SLICES = {10, 16, 19}
RAW_TIME_ALLOW = {   # (파일, 함수, 따라간 도우미 — "" 는 그 함수 안 직접) → 이유. 조각·판정이 끝나면 지운다
    ("rankcollect.py", "fill_opp_rank", "season_raw_snaps"): "도우미의 [:10] 은 rank.db 상태(KST) 자르기 · 원문은 raw_bound/parse",
    ("tradecollect.py", "_top", "_overlap_floor"): "받기 쪽 — tradeDate 원문끼리 비교(경계도 원문 꼴) · 시각으로 안 읽는다",
    ("tools/check_t0.py", "reference", ""): "63 골든 — 앱 코드(matchtime)와 독립인 기준 계산이라 일부러 원문을 직접 읽는다",
    ("check_api.py", "main", ""): "점검 수십 개가 한 함수 — 직접 파싱은 웹 랭킹 기준 시각(KST · ref_time)뿐, 넥슨 시각은 matchtime",
    ("tools/measure_cards.py", "b_freeze", ""): "51 측정 U1 — 11-26 판정까지 도구를 얼린다(:330·:471·:472 도 그때 같이 · 규칙이 못 보는 꼴)",
    ("tools/measure_cards.py", "b_freeze", "snapshot_list"): "같음(U1) — 도우미는 rank.db 스냅숏(KST) 읽기",
}


OLD_COLOR_NAMES = ("GREEN", "GREEN_SOFT", "GREEN_HOVER", "RED", "YELLOW", "ON_ACCENT")
_OLD_COLOR_RE = re.compile(r"\b(" + "|".join(OLD_COLOR_NAMES) + r")\b")
_STRING_TOKENS = {tokenize.STRING} | {getattr(tokenize, n) for n in ("FSTRING_MIDDLE", "TSTRING_MIDDLE")
                                      if hasattr(tokenize, n)}


def check_old_color_names(srcs: dict[str, str]) -> list[str]:
    """옛 색 이름(3.1.1 에 지움 — 강조 ACCENT · 좋음/나쁨/주의 GOOD·BAD·WARN 으로 갈랐다)이 코드·글에 없다.

    속성은 그 줄이 실행될 때 읽히므로 스모크가 안 지나는 분기에 남은 `T.GREEN` 은 exe 에서 처음 터진다 —
    그래서 소스로 잰다. 주석은 빼고 문자열(f-string 의 `{GREEN}` · docstring)은 센다."""
    out = []
    for rel, src in srcs.items():
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.NAME and tok.string in OLD_COLOR_NAMES:
                out.append(f"{rel}:{tok.start[0]} 옛 색 이름 {tok.string}")
            elif tok.type in _STRING_TOKENS:
                out += [f"{rel}:{tok.start[0]} 글 속 옛 색 이름 {m.group(1)}" for m in _OLD_COLOR_RE.finditer(tok.string)]
    return out


GRADE_FILES = ("app_main.py", "dashboard.py")
_GRADE_NAME = re.compile(r"grade(?!s)")


def _mentions_grade(node) -> bool:
    return any(isinstance(n, ast.Name) and _GRADE_NAME.search(n.id)
               or isinstance(n, ast.Attribute) and _GRADE_NAME.search(n.attr) for n in ast.walk(node))


def _grade_text(node) -> bool:
    """표 칸 (글자, 정렬 키) 의 글자가 강화 하나뿐인가 — f"{g}" · f"+{g}" · str(g …) · 그 셋의 `… if … else "-"`.
    "3 → 5" 처럼 값이 둘인 글이나 "5강 12 · 3강 4" 같은 목록은 배지가 아니다(글자 그대로)."""
    if isinstance(node, ast.IfExp):
        return _grade_text(node.body)
    if isinstance(node, ast.JoinedStr):
        vals = [v for v in node.values if isinstance(v, ast.FormattedValue)]
        lits = [v.value for v in node.values if isinstance(v, ast.Constant)]
        return len(vals) == 1 and all(t in ("", "+") for t in lits) and _mentions_grade(vals[0].value)
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str"
            and len(node.args) == 1 and _mentions_grade(node.args[0]))


def grade_cells(srcs: dict[str, str]) -> dict[str, int]:
    """함수 이름 → 강화 하나뿐인 표 칸 튜플 수(64 P8 진입점 정의 — 첫 실행이 곧 진입점 표)."""
    out: dict[str, int] = {}

    def walk(node, fn):
        for ch in ast.iter_child_nodes(node):
            here = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(ch, ast.Tuple) and len(ch.elts) == 2 and fn and _grade_text(ch.elts[0]):
                out[fn] = out.get(fn, 0) + 1
            walk(ch, here)
    for src in srcs.values():
        walk(ast.parse(src), None)
    return out


def check_grade_badges(srcs: dict[str, str], table: dict) -> list[str]:
    """강화 숫자 칸을 만드는 함수 ↔ app_main.GRADE_BADGE_COLUMNS(배지 대리자를 거는 열) — 함수도 칸 수도 같아야."""
    found = grade_cells(srcs)
    want = {fn: len(cols) for fn, (_t, cols) in table.items()}
    return [f"{fn}: 강화 칸 {found.get(fn, 0)}개 · 배지 열 {want.get(fn, 0)}개"
            for fn in sorted(set(found) | set(want)) if found.get(fn, 0) != want.get(fn, 0)]


def _docstring_nodes(tree) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _raw_time_read(fn, docs: set[int]) -> int | None:
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            if n.value in RAW_TIME_KEYS or RAW_TIME_SQL.search(n.value):
                return n.lineno
    return None


def _time_parse(fn) -> int | None:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("fromisoformat", "strptime"):
            return n.lineno
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
            s = n.slice
            if s.lower is None and s.step is None and _const(s.upper) in RAW_TIME_SLICES:
                return n.lineno
    return None


def _local_calls(fn) -> set[str]:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("self", "cls"):
                out.add(f.attr)
    return out


def check_raw_time(srcs: dict[str, str], allow: dict = RAW_TIME_ALLOW) -> list[str]:
    out = []
    for rel, src in srcs.items():
        if rel == "matchtime.py":
            continue
        tree = ast.parse(src)
        docs = _docstring_nodes(tree)
        fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        parse_at = {f.name: _time_parse(f) for f in fns}
        for f in fns:
            a = _raw_time_read(f, docs)
            if a is None:
                continue
            hits = [("", _time_parse(f))] + [(c, parse_at[c]) for c in sorted(_local_calls(f)) if parse_at.get(c)]
            for via, b in hits:
                if b is not None and (rel, f.name, via) not in allow:
                    where = f":{b} ({via}() 안)" if via else f":{b}"
                    out.append(f"{rel}:{a} {f.name}() — 넥슨 시각 원문을 {where} 에서 직접 파싱 · matchtime 으로")
                    break
    return out


# ── SQL 실행 계획 ────────────────────────────────────────────────────────────

SQL_FILES = ("store.py", "rankcollect.py", "rankerpick.py", "rankmeta.py", "cardcollect.py")


def collect_sql() -> dict[str, set[str]]:
    """파싱·수집 테스트를 그대로 돌리며 store·rankcollect 가 낸 SQL 을 모은다 — {문장: {'파일:함수'}}.
    값이 박힌 문장(trace 는 ? 를 값으로 바꿔 준다)이라 그대로 EXPLAIN 할 수 있다."""
    seen: dict[str, set[str]] = {}
    real = sqlite3.connect

    def connect(*a, **k):
        conn = real(*a, **k)

        def trace(sql):
            f = sys._getframe(1)
            while f:
                fn = Path(f.f_code.co_filename).name
                if fn in SQL_FILES:
                    seen.setdefault(" ".join(sql.split()), set()).add(f"{fn}:{f.f_code.co_name}")
                    return
                f = f.f_back
        conn.set_trace_callback(trace)
        return conn

    sqlite3.connect = connect
    try:
        for name in ("test_parsing", "test_rankcollect", "test_trades", "test_rankmeta"):
            spec = importlib.util.spec_from_file_location(f"_rules_{name}", ROOT / "tests" / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            with contextlib.redirect_stdout(io.StringIO()):
                spec.loader.exec_module(mod)
                for k, v in sorted(vars(mod).items()):
                    if k.startswith("test_") and "budget" not in k and callable(v):   # 예산 테스트는 14만 행 — trace 로 느려 뺀다
                        try:
                            v()
                        except Exception:  # noqa: BLE001 — 그 테스트의 성패는 그 파일이 본다
                            pass
        _drive_uncovered()
    finally:
        sqlite3.connect = real
    return seen


def _drive_cardcollect(path: Path) -> None:
    """카드 백필 한 바퀴(51단계) — 내 계정 · 상대 · 팀컬러 목록을 심고 가짜 넥슨으로 run 을 두 날(① ② ③ ④ ⑤ 의 SELECT 가 다 돈다)."""
    from datetime import date, timedelta
    import cardcollect
    import config
    import playerinfo
    import store
    import teamcolor
    conn = store.open_db(path)
    store.upsert_account(conn, "me", "me")
    store.set_trade_state(conn, my_ouid="me")
    day = date(2026, 10, 18)
    m = conn.execute("INSERT INTO squad_match (match_id) VALUES ('m1')").lastrowid
    for ouid, spid in (("me", 1), ("opp", 2)):
        o = conn.execute("INSERT INTO squad_owner (ouid, nickname, last_day) VALUES (?, ?, ?) RETURNING id",
                         (ouid, ouid, day.toordinal())).fetchone()[0]
        conn.execute("INSERT INTO match_squads (spid, day, match, owner, po, grade) VALUES (?, ?, ?, ?, 25, 3)",
                     (spid, day.toordinal(), m, o))
    conn.commit()
    store.save_teamcolor_meta(conn, [teamcolor.TeamColorMeta(7, "팀", "teamcolor/a.png", "", 1, 3, [])], day.isoformat())
    conn.close()
    page = lambda s: playerinfo.PlayerInfo(sp_id=s, name="가", ovr=1, height="180cm", body_type="보통",  # noqa: E731
                                           prices={3: "1,000 BP"})
    sim = lambda s: playerinfo.AbilitySim(ovr=1, groups={}, abilities={}, club_options=[(7, "팀")],  # noqa: E731
                                          position_ovrs={c: 1 for c in config.SIM_POS_OF.values()})
    keep = config.WEB_DATA
    config.WEB_DATA = True
    try:
        for d in (day, day + timedelta(days=40)):
            cardcollect.run(path, d, 600, fetch_page=page, fetch_sim=sim, sleep=lambda s: None,
                            fetch_detail=lambda t: ("", [teamcolor.Step(1, 3, [])]), fetch_list=lambda: [])
        conn = store.open_db(path)
        cardcollect.status(conn, day)
        conn.close()
    finally:
        config.WEB_DATA = keep


def _drive_uncovered() -> None:
    """파싱·수집 테스트가 안 부르는 읽기 함수 — 화면 스모크만 부른다(Qt 를 띄우기엔 무겁다)."""
    import store
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            store.all_match_ids(conn)
            store.list_accounts(conn)
            store.load_details_by_ids(conn, ["a", "b"])
            store.load_seasons(conn)
            store.load_team_colors(conn, ["가", "나"])
            store.recent_searches(conn)
            store.match_dates(conn, "a", "2026-01-01")          # 예측(1.3.1) — test_predict·화면 스모크가 부른다
            store.fix_squad_days(conn)                          # 63 옛 줄 날 다시 세기 — test_rankerpick·test_storage 가 부른다
            store.predictions(conn)
            store.predictions(conn, "a")
            store.account_nickname(conn, "a")                   # 거래 화면 띠(1.4.1) — 화면 스모크가 부른다
            store.trait_squads(conn)                            # 포지션 특성(32단계) — test_traits 가 부른다
            store.prune_trait_squads(conn, on_at=None, keep={1})
            store.trait_block_streak(conn)                      # 특성 막힘 날짜(32단계 단계 3) — test_traits 가 부른다
            store.card_block_streak(conn, "card_backfill", "2026-10-01")   # 카드 백필(51단계) — test_cardcollect 가 부른다
            _drive_cardcollect(Path(d) / "c.db")
            store.owner_round_running(conn)                     # 구단주 분포(49단계) — test_ownerdist 가 부른다
            store.owner_round_last_end(conn, ("done",))
            store.owner_done_hashes(conn, 1)
            store.owner_dist_rows(conn, 1)
            store.owner_req_since(conn, "2026-10-01T00:00:00")
            store.owner_round_results(conn, "2026-10-01")       # 구단주 분포 예약(49단계 ③a) — test_ownerdist 가 부른다
            store.owner_dist_has_data(conn)
            # 57 K12 내 특성 · K9 저장 공간 — test_storage 가 부른다
            store.save_my_traits(conn, "a", sn=1, match_id="m", team="1:0", state="ok", body=[], fetched_at="2026-10-20")
            store.my_traits(conn, "a")
            store.prune_my_traits(conn, "b")
            store.prune_my_traits(conn, None)
            store.table_bytes(conn, store.CARD_TABLES + store.DIST_TABLES)
            store.copy_kept(Path(d) / "f.db", Path(d) / "kept.db")
            # 줄 섞기(50단계 V1) — 옛 done 바퀴 하나를 심어 안쪽 SELECT 까지 돌린다(test_ownerdist 가 부른다)
            conn.execute("INSERT INTO owner_dist_state (seed, started_at, ended_at, result) VALUES (1, 'a', 'b', 'done')")
            conn.execute("INSERT INTO owner_dist (round_id, stratum, n_games) VALUES (1, 1, 1)")
            conn.commit()
            store.owner_dist_mix_pending(conn)
        finally:
            conn.close()
        import rankcollect
        r = rankcollect.open_rank_db(Path(d) / "r.db")
        try:
            rankcollect.raw_snapshots(r)
            rankcollect.snapshot_games(r, 1)
            rankcollect.season_cuts(r)
            import rankerpick                                   # 랭커 픽 대상 · 12 찾기의 팀컬러·순위(2.1.1)
            r.execute("INSERT INTO snapshots (id, taken_at, row_count, dup_count, season_seq, new_season)"
                      " VALUES (1, '2026-10-06T10:00:00', 1, 0, 1, 0)")
            r.execute("INSERT INTO snapshot_rows (snapshot_id, rank, profile_sn, nickname) VALUES (1, 1, 5, 'n')")
            rankerpick.top_rankers(r)
            rankerpick.top_rankers(r, snapshot_id=1)             # 구단주 분포 이어 받기(49단계) — 바퀴를 시작한 스냅숏
            rankerpick.latest_raw_snapshot(r)
            rankerpick.snapshot_index(r)
        finally:
            r.close()
    # 선수 색인 · 랭커 픽 받기·집계·지우기(2.1.1) — 화면 없이 도는 테스트를 그대로
    sys.path.insert(0, str(ROOT / "tests"))
    import rankmeta                                             # 이름 → 엠블럼(40단계) — 시즌 스냅숏이 있어야 JOIN 이 돈다
    import test_rankmeta as trm
    db = trm.DB()
    try:
        db.snap(trm.T0, [{"sn": 1, "rank": 1, "color": "A"}])
        rankmeta.emblems_by_name(db.ro())
    finally:
        db.close()
    import test_rankerpick as trp
    for t in (trp.test_save_indexes_squads, trp.test_backfill_resumes_and_skips_indexed, trp.test_owner_lookup_window_and_grade,
              trp.test_player_scope_owners_merge_cards_and_count_once,   # 선수 단위 조회(50단계 N30) — IN (카드들)

              trp.test_ranker_pick_collects_and_saves, trp.test_ranker_pick_second_429_stops_for_the_day,
              trp.test_ranker_pick_summary, trp.test_clear_removes_ranker_pick_data,
              trp.test_recommend_candidates_sources,    # 11-lite 추천 후보(17단계 — 색인·내 상대 마지막 경기)
              trp.test_pick_days_add_once_by_match_day_only_pick_top):   # 랭커 픽 날짜별(2.3.1 N15)
        t()


def sql_functions() -> set[str]:
    """SELECT 를 품은 함수 — 이게 전부 collect_sql 에 나와야 검사가 빈 게 아니다."""
    out = set()
    for fn in SQL_FILES:
        src = _src(ROOT / fn)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and re.search(r"\bSELECT\b", ast.get_source_segment(src, node) or ""):
                out.add(f"{fn}:{node.name}")
    return out


def temp_sorts(seen: dict[str, set[str]]) -> tuple[list[str], int]:
    import rankcollect
    import store
    dbs = []
    for schema in (store.SCHEMA, rankcollect.SCHEMA):
        c = sqlite3.connect(":memory:")
        c.executescript(schema)
        dbs.append(c)
    bad, n = [], 0
    for sql, who in seen.items():
        if not re.match(r"(SELECT|UPDATE|DELETE|INSERT|WITH)\b", sql, re.I):
            continue
        plan = None
        for c in dbs:
            try:
                plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql))
                break
            except sqlite3.Error:
                continue
        if plan is None:
            continue                      # 테스트가 만든 임시 표 등 — 두 스키마 어디에도 없다
        n += 1
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+(\w+)", sql, re.I))
        if tables and tables <= SQL_SMALL_TABLES:
            continue
        if "TEMP B-TREE" in plan and not any(
                w in SQL_TEMP_ALLOW and SQL_TEMP_ALLOW[w].search(sql) for w in who):
            bad.append(f"{sorted(who)} {sql[:120]} => {plan}")
    return bad, n


# ── 테스트 ───────────────────────────────────────────────────────────────────

def _all(files) -> dict[str, str]:
    return {_rel(p): _src(p) for p in files}


def test_no_direct_combobox():
    assert check_combo({"x.py": "from PyQt6.QtWidgets import QComboBox\nc = QComboBox()\n"}), "심은 위반을 못 잡는다"
    assert not check_combo({"x.py": "c = NoScrollComboBox()\n"})
    assert check_combo(_all(APP_FILES)) == []


def test_dialogs_fit_to_screen():
    bad = "class D(QDialog):\n    def __init__(self):\n        self.resize(600, 400)\n"
    bad_fn = "def f(self):\n    dlg = QDialog(self)\n    dlg.resize(1, 1)\n"
    ok = "def f(self):\n    dlg = QDialog(self)\n    fit_to_screen(dlg, 560, 480)\n"
    assert len(check_dialog_fit({"x.py": bad, "y.py": bad_fn})) == 2, "심은 위반을 못 잡는다"
    assert not check_dialog_fit({"x.py": ok})
    assert check_dialog_fit(_all(APP_FILES)) == []


def test_text_io_names_encoding():
    bad = ("open('a.txt')\nopen('a.txt', 'w')\nPath('a').read_text()\np.write_text('x')\n"
           "subprocess.run(['git'], capture_output=True, text=True)\n")
    ok = ("open('a.bin', 'rb')\nopen('a', encoding='utf-8')\nPath('a').read_text(encoding='utf-8')\n"
          "subprocess.run(['git'], capture_output=True)\n"
          "subprocess.run(['git'], text=True, encoding='utf-8')\n")
    assert len(check_encoding({"x.py": bad})) == 5, check_encoding({"x.py": bad})
    assert check_encoding({"x.py": ok}) == []
    assert check_encoding(_all(ALL_PY)) == []


def test_signals_carry_objects():
    assert check_signals({"x.py": "s = pyqtSignal(list, int)\nt = pyqtSignal(dict)\n"}) and \
        len(check_signals({"x.py": "s = pyqtSignal(list, int)\nt = pyqtSignal(dict)\n"})) == 2
    assert not check_signals({"x.py": "s = pyqtSignal(object, int)\n"})
    assert check_signals(_all(APP_FILES)) == []


def test_every_modal_the_app_uses_is_blocked_in_smoke():
    guarded = guarded_modals(_src(ROOT / "tests" / "test_ui_smoke.py"))
    # 차단 목록을 못 읽으면 아래 검사가 전부 위반이 되거나(엄격) — 반대로 읽기가 넓으면 비어 있어도 통과한다
    assert {"QMessageBox.warning", "QMessageBox.question", "QDialog.exec", "QApplication.exec"} <= guarded, guarded
    bad = ("QFileDialog.getOpenFileName(None)\nQMessageBox.about(None, 'a', 'b')\n"
           "def f():\n    m = QMenu()\n    m.exec(pos)\n")
    assert len(check_modals({"x.py": bad}, guarded)) == 3, check_modals({"x.py": bad}, guarded)
    assert not check_modals({"x.py": "QMessageBox.warning(None, 'a', 'b')\n"}, guarded)
    assert check_modals(_all(APP_FILES), guarded) == []


def test_colors_only_in_theme():
    bad = "p.setPen(QColor('#ffffff'))\nc = QColor(10, 20, 30)\n"
    assert len(check_colors({"x.py": bad})) == 2, "심은 위반을 못 잡는다"
    assert not check_colors({"theme.py": "A = '#ffffff'\n"})
    # 예외 표는 그 이름의 할당 안에서만 — 같은 파일 다른 자리는 잡힌다
    assert check_colors({"playerinfo.py": "STAT_COLOR_BUCKETS = ['#ffffff']\nX = '#000000'\n"}) == \
        ["playerinfo.py:2 색 \"#000000\" — theme.py 에 둔다"]
    assert check_colors(_all(APP_FILES)) == []


def test_old_color_names_are_gone():
    bad = ('c = T.GREEN\nQSS = f"a {{ color: {GREEN}; }}"\ndef f():\n    """ON_ACCENT 위 글자"""\n'
           "x = theme.RED  # 주석의 YELLOW 는 안 센다\n")
    found = check_old_color_names({"x.py": bad})
    assert [f.split(":")[1].split()[0] for f in found] == ["1", "2", "4", "5"], found
    assert not check_old_color_names({"x.py": "c = T.GOOD  # 옛 GREEN\nd = T.ACCENT_SOFT\ne = 'GREENISH'\n"})
    # test_rules 자신은 위의 심은 위반을 품고 있어 뺀다
    assert check_old_color_names(_all(p for p in ALL_PY if p.name != "test_rules.py")) == []


def test_grade_cells_are_badged():
    # 64 P8 — 강화 숫자 하나만 든 표 칸은 배지로. 심은 소스: 잡혀야 할 넷(f"{}"·f"+{}"·str()·if-else) · 아닌 셋(변화·목록·라벨)
    planted = ("def a(r, e, t):\n"
               "    r.append([(f'{e.grade}', 1), (f'+{t.cur_grade}' if t else '-', 0)])\n"
               "def b(e, now_grade):\n    return [(str(now_grade or '-'), 0), (str(e.grade_to) if e else '-', 0)]\n"
               "def c(e, c2):\n    x = (f'{e.grade_from} → {e.grade_to}', 0)\n"
               "    y = (' · '.join(f'{g}강' for g in c2.grades), 0)\n    z = QLabel(f'{e.grade}강')\n")
    assert grade_cells({"x.py": planted}) == {"a": 2, "b": 2}, grade_cells({"x.py": planted})
    assert check_grade_badges({"x.py": planted}, {"a": ("t", (1, 2)), "b": ("t", (3,))}) == ["b: 강화 칸 2개 · 배지 열 1개"]
    import app_main
    srcs = {f: _src(ROOT / f) for f in GRADE_FILES}
    bad = check_grade_badges(srcs, app_main.MainWindow.GRADE_BADGE_COLUMNS)
    assert bad == [], bad


def test_weak_samples_go_through_is_weak():
    # 심은 위반 다섯(DONE.md 2.6.1 A6) — 직접 비교 · 별칭 · 경기 수 · 지역 문턱 · config 문턱
    bad = ("def a(b):\n    if b.games < MIN_COND: pass\n"
           "def c(p):\n    few = core.MIN_COND\n    if p.games < few: pass\n"
           "def d(s):\n    if s.total < MIN_BASE: pass\n"
           "def e(games, min_n):\n    weak = games < min_n\n"
           "def f(c):\n    if c.met < config.OPP_CARD_MIN: pass\n")
    assert len(check_weak({"x.py": bad})) == 5, check_weak({"x.py": bad})
    ok = ("def a(b):\n    if core.is_weak(b.rated, MIN_COND): pass\n"
          "def g(minute):\n    if minute < 45: pass\n"
          "def h(a, b, x):\n    if x < min(a, b): pass\n")
    assert check_weak({"x.py": ok}) == []
    # 첨자·덧셈 안의 문턱도(맨 이름만 보던 첫 판은 창 크기 비교 app_main.initial_window 를 놓쳤다)
    assert len(check_weak({"x.py": "def w(a, f):\n    return a >= LIM.MIN_N[0] + f\n"})) == 1
    # 예외는 (파일, 함수, 이름) — 같은 파일 다른 함수는 잡힌다
    assert check_weak({"x.py": "def z(t):\n    return t < X_MIN\n"}, {("x.py", "y", "X_MIN"): ""})
    srcs = {f: _src(ROOT / f) for f in WEAK_FILES}
    assert check_weak(srcs) == [], "\n".join(check_weak(srcs))
    # 표에 쓰이지 않는 줄이 남지 않게 — 지운 코드의 예외가 남으면 다음 위반을 봐준다
    used = set()
    for key in WEAK_ALLOW:
        if check_weak({key[0]: srcs[key[0]]}, {k: v for k, v in WEAK_ALLOW.items() if k != key}):
            used.add(key)
    assert used == set(WEAK_ALLOW), sorted(set(WEAK_ALLOW) - used)


def test_rate_classes_table_is_complete():
    # 표(models.RATE_CLASSES)가 빠지면 test_parsing 의 "mixed 가 bool" 검사가 그 클래스를 안 본다
    import models
    planted = {"x.py": "class A(RateMixin):\n    pass\n"
                       "class B:\n    def rate(self, w):\n        return win_pct(*w, self.mixed)\n"
                       "class C:\n    win: int = 0\n"}
    assert rate_classes(planted) == {"A", "B"}, rate_classes(planted)
    found = rate_classes({f: _src(ROOT / f) for f in RATE_FILES})
    assert found == set(models.RATE_CLASSES), (sorted(found - set(models.RATE_CLASSES)),
                                               sorted(set(models.RATE_CLASSES) - found))


def test_every_query_avoids_temp_sort():
    seen = collect_sql()
    need, ran = sql_functions(), {w for ws in seen.values() for w in ws}
    # 막지 않았으면: 함수가 안 돌면 그 쿼리는 검사 밖이다 — "TEMP 0" 이 빈 검사일 수 있다
    assert need <= ran, f"SQL 을 한 번도 안 낸 함수(검사 밖): {sorted(need - ran)}"
    bad, n = temp_sorts(seen)
    assert n >= 100, f"검사한 문장 {n}개 — 수집이 비었다"
    assert bad == [], "\n".join(bad)


def test_temp_sort_check_catches_unindexed_order():
    # 인덱스 없는 열로 정렬하는 문장을 심으면 잡혀야 한다(예외 표가 넓어 다 통과시키지 않는지도)
    bad, n = temp_sorts({"SELECT payload FROM matches ORDER BY payload": {"store.py:load_details"},
                         "SELECT match_id FROM matches WHERE match_id = 'a'": {"store.py:x"},
                         "SELECT nickname FROM accounts ORDER BY nickname": {"store.py:y"},
                         "SELECT m.payload FROM matches m JOIN accounts a ON a.ouid = m.match_id "
                         "ORDER BY m.payload": {"store.py:z"}})
    # 작은 표만 읽는 정렬은 봐주되, 큰 표가 하나라도 끼면 잡는다
    assert n == 4 and len(bad) == 2, (n, bad)

def test_nexon_times_are_parsed_only_in_matchtime():
    planted = {
        "a.py": "def f(d):\n    return datetime.fromisoformat(d.get('matchDate'))\n",                 # 한 줄
        "b.py": "def f(d):\n    raw = d['matchDate']\n    when = datetime.fromisoformat(raw)\n",        # 두 줄 꼴
        "c.py": "def _p(s):\n    return datetime.strptime(s, '%Y')\n"
                "def f(d):\n    return _p(d.get('matchDate'))\n",                                     # 도우미 함수 꼴
        "d.py": "class L:\n    def _p(self, s):\n        return s[:10]\n"
                "    def f(self, r):\n        return self._p(r['achievementDate'])\n",                 # 메서드 도우미
        "e.py": "def f(c):\n    for r in c.execute('SELECT match_date FROM matches'):\n        yield r[0][:19]\n",  # SQL 열
        "f.py": "def f(r):\n    return r.get('trade_date')[:10]\n",                                    # M4 Row 키
        "g.py": "def f(r):\n    return str(r.get('createDate') or '')[:10]\n",
    }
    found = check_raw_time(planted, allow={})
    assert len(found) == len(planted), found
    ok = {"x.py": "def f(d):\n    '''matchDate 를 [:10] 으로 자르지 않는다'''\n    return matchtime.day(d.get('matchDate'))\n",
          "y.py": "def f(ms):\n    return [m.match_date.isoformat()[:10] for m in ms]\n",     # KST 속성 — 원문 아님
          "matchtime.py": "def parse(raw):\n    return datetime.fromisoformat(raw['matchDate'])\n"}
    assert check_raw_time(ok, allow={}) == [], check_raw_time(ok, allow={})
    files = [p for g in RAW_TIME_FILES for p in sorted(ROOT.glob(g))]
    srcs = _all(files)
    assert check_raw_time(srcs) == [], "\n".join(check_raw_time(srcs))
    # 예외는 (함수, 도우미) 한 쌍만 가린다 — 같은 함수에 직접 파싱을 더 심으면 다시 걸린다(함수 통째로 가리면 fill_opp_rank 안
    # 직접 파싱이 숨는다)
    planted_fill = {"rankcollect.py": srcs["rankcollect.py"].replace(
        "when = matchtime.parse(r[\"match_date\"])", "when = datetime.fromisoformat(r[\"match_date\"])")}
    assert planted_fill["rankcollect.py"] != srcs["rankcollect.py"], "심을 자리가 바뀌었다"
    assert any("fill_opp_rank()" in s for s in check_raw_time(planted_fill)), "예외가 함수 통째를 가린다"
    # 예외 표가 실제로 걸리는 자리만 — 고쳐진 자리를 표에 남기면 다시 생겨도 못 잡는다
    for k in RAW_TIME_ALLOW:
        rest = {kk: v for kk, v in RAW_TIME_ALLOW.items() if kk != k}
        assert any(f" {k[1]}()" in s for s in check_raw_time({k[0]: srcs.get(k[0], "")}, allow=rest)), \
            f"예외 표에 더는 안 걸리는 자리: {k}"


def test_watchdog_kills_hung_test():
    """테스트 감시(tests/watchdog.py)가 정말 끊는다 — 끝나지 않는 테스트를 1초 한도로 돌리면 종료 코드 3 · 이름 · 멈춘 줄.
    감시가 빠지면 이 자식이 30초 시간 초과로 걸려 빨개진다(2026-10-06 스모크가 18분 멈췄던 일의 장치)."""
    import subprocess
    code = ("import sys; sys.path.insert(0, r'%s'); import watchdog\n"
            "def spin_forever():\n    while True: pass\n"
            "with watchdog.limit('test_spin', 1):\n    spin_forever()\n") % (ROOT / "tests")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr[-500:])
    assert "[멈춤] test_spin" in r.stdout, r.stdout
    assert "spin_forever" in r.stderr, "멈춘 자리(호출 스택)가 안 남았다"


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
