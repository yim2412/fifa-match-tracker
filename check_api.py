"""API 연결 점검 — GUI 띄우기 전에 키·엔드포인트가 맞는지 터미널에서 확인.

    python check_api.py 닉네임

점검은 공유 문맥(CheckCtx)을 이어 쓰는 함수 목록이다(96 W3) — 터미널은 CHECKS 전부, 앱 [정보 › 연결 점검]은
사용자용 묶음 USER_CHECKS 만 같은 문맥 꼴로 돈다. 터미널 출력 바이트·종료 코드는 떼기 전과 같다(tests/test_check_api 골든).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import config
import core_api as core
import images
import matchtime
import store
from core_api import parse_match, summarize
import nexon_api
from nexon_api import FCOnlineAPI, NexonAPIError

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"


@dataclass
class CheckCtx:
    """점검들이 이어 쓰는 상태 — 앞 점검이 채운 값을 뒤 점검이 읽는다(ouid → ids → details → players → first_sp)."""
    nickname: str = ""
    api: Any = None
    ouid: str = ""
    ids: list = field(default_factory=list)
    details: list = field(default_factory=list)
    matches: list = field(default_factory=list)
    players: list = field(default_factory=list)
    first_sp: Any = None
    page: Any = None            # 랭킹 목록 1쪽(팀컬러 엠블럼 점검이 쓴다)
    items: list = field(default_factory=list)   # 팀컬러 목록(그림 점검이 쓴다)
    rank_seasons: list = field(default_factory=list)


@dataclass
class Result:
    """점검 하나의 결과 — lines 는 터미널에 찍는 줄 그대로(앱은 status 와 text 를 쓴다).
    stop 이면 뒤 점검을 안 돌고 code 로 끝낸다."""
    status: str
    lines: list
    stop: bool = False
    code: int = 0

    @property
    def text(self) -> str:
        """앱 표 칸 — 첫 줄 앞의 [OK]/[FAIL] 표지를 뗀 글."""
        out = []
        for ln in self.lines:
            s = ln.strip()
            if s.startswith("[") and "]" in s[:8]:
                s = s[s.index("]") + 1:].strip()
            out.append(s)
        return "\n".join(x for x in out if x)


def _worst(lines: list) -> str:
    """여러 줄을 찍는 점검의 상태 — 가장 나쁜 표지."""
    if any(ln.startswith("[FAIL") for ln in lines):
        return FAIL
    if any(ln.startswith("[WARN") for ln in lines):
        return WARN
    return OK


def _res(lines: list, stop: bool = False, code: int = 0) -> Result:
    return Result(_worst(lines), lines, stop, code)


# ── 터미널 점검(차례대로) ──────────────────────────────────────────────

def check_key_loaded(ctx: CheckCtx) -> Result:
    if config.KEY_STATE == "fail":
        return _res(["[FAIL] API 키를 읽지 못했습니다(Windows 자격 증명 관리자 또는 .env). 키가 없는 것과는 다릅니다."], stop=True, code=1)
    if not config.API_KEY:
        return _res(["[FAIL] API 키가 없습니다(앱을 한 번 켜 키를 넣거나 .env 에 NEXON_API_KEY 를 적습니다)."], stop=True, code=1)
    if ctx.api is None:
        ctx.api = FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)
    return _res([f"[OK]   API 키 로드 ({config.API_KEY[:8]}…)"])


def check_ouid(ctx: CheckCtx) -> Result:
    try:
        ctx.ouid = ctx.api.get_ouid(ctx.nickname)
        return _res([f"[OK]   닉네임 → ouid: {ctx.ouid}"])
    except NexonAPIError as e:
        return _res([f"[FAIL] 닉네임 조회: {e.message}  (code={e.code}, status={e.status})"], stop=True, code=1)


def check_server(ctx: CheckCtx) -> Result:
    # 중앙 서버(1단계 ⓐ · .env FIFA_SERVER_URL 이 있을 때만) — 살아 있나 · 서버를 거친 ouid 가 직접과 같은가
    lines: list = []
    if config.SERVER_URL:
        import requests
        try:
            ok = requests.get(f"{config.SERVER_URL.rstrip('/')}/health", timeout=5).status_code == 200
            lines.append(f"[{'OK' if ok else 'FAIL'}]   중앙 서버 /health ({config.SERVER_URL})")
        except requests.RequestException as e:
            lines.append(f"[FAIL] 중앙 서버 /health: {e}")
        srv = FCOnlineAPI(config.API_KEY, cache_dir=None, server=True, server_url=config.SERVER_URL,
                          install_id=config.install_id())
        try:
            same = srv._get_server(nexon_api.EP_ID, 1, {"nickname": ctx.nickname}).get("ouid") == ctx.ouid
            lines.append(f"[{'OK' if same else 'FAIL'}]   서버 경유 ouid {'== 직접' if same else '!= 직접'}")
        except NexonAPIError as e:
            lines.append(f"[FAIL] 서버 경유 닉네임 조회: {e.message}  (code={e.code}, status={e.status})")
    return _res(lines)


def check_basic(ctx: CheckCtx) -> Result:
    try:
        basic = ctx.api.get_user_basic(ctx.ouid)
        return _res([f"[OK]   계정: {basic.get('nickname')} / Lv.{basic.get('level')}"])
    except NexonAPIError as e:
        return _res([f"[FAIL] 계정 정보: {e.message}"])


def check_max_division(ctx: CheckCtx) -> Result:
    api = ctx.api
    try:
        rows = api.get_max_division(ctx.ouid)
        row = next((r for r in rows if r.get("matchType") == config.DEFAULT_MATCH_TYPE), None)
        if row:
            names = {d.get("divisionId"): d.get("divisionName") for d in api.get_meta("division")}
            name = names.get(row.get("division"), row.get("division"))
            return _res([f"[OK]   최고티어(감독모드): {name} · 최근 달성일 {matchtime.day(row.get('achievementDate')) or '-'}"])
        return _res(["[WARN] 역대 최고(감독모드): 기록 없음"])
    except NexonAPIError as e:
        return _res([f"[WARN] 역대 최고 등급: {e.message}"])


def check_trades(ctx: CheckCtx) -> Result:
    # 1.4.1 거래 기록 — ouid 없이(넥슨이 무시하고 키 주인 것만 준다, docs/DONE.md 1.4.1 R1). 카드·금액은 찍지 않는다.
    # 마지막 거래 날짜로 반영 지연(R6)을, 넥슨이 ouid 를 다시 존중하게 바뀌면 여기서 400·빈 목록으로 보인다
    lines = []
    for kind in ("buy", "sell"):
        try:
            rows = ctx.api.get_trades(kind)
            last = matchtime.label(rows[0].get("tradeDate")) if rows else "-"
            lines.append(f"[OK]   거래 기록({kind}, 키 주인 것): 첫 쪽 {len(rows)}건 · 마지막 거래 {last}")
        except NexonAPIError as e:
            lines.append(f"[WARN] 거래 기록({kind}): {e.message}  (code={e.code}, status={e.status})")
    return _res(lines)


def check_match_ids(ctx: CheckCtx) -> Result:
    try:
        ctx.ids = ctx.api.get_match_ids(ctx.ouid, config.DEFAULT_MATCH_TYPE, 0, 5)
    except NexonAPIError as e:
        return _res([f"[FAIL] 매치 목록: {e.message}"], stop=True, code=1)
    lines = [f"[OK]   최근 매치 {len(ctx.ids)}건: {ctx.ids[:2]}"]
    if not ctx.ids:
        lines.append("[WARN] 공식경기 기록이 없어 상세 조회는 건너뜁니다.")
        return _res(lines, stop=True, code=0)
    return _res(lines)


def check_details(ctx: CheckCtx) -> Result:
    lines = []
    try:
        for mid in ctx.ids:
            d = ctx.api.get_match_detail(mid)
            ctx.details.append(d)
            m = parse_match(d, ctx.ouid)
            if m:
                ctx.matches.append(m)
        lines.append(f"[OK]   매치 상세 파싱 {len(ctx.matches)}건")
        for m in ctx.matches:
            lines.append(f"       {m.date_text}  {m.result}  {m.score}  vs {m.opponent}"
                         f"  (점유 {m.possession}%, 평점 {m.rating:.2f})")
        s = summarize(ctx.matches)
        lines.append(f"\n요약: {s.win}승 {s.draw}무 {s.lose}패 · 승률 {s.win_rate:.1f}%"
                     f" ({'승÷(승+패) — 무승부 나는 경기와 안 나는 경기가 섞임' if s.mixed else '승÷전체'})"
                     f" · 평균 {s.avg_goals_for:.2f}득 {s.avg_goals_against:.2f}실")
    except NexonAPIError as e:
        lines.append(f"[FAIL] 매치 상세: {e.message}")
        return _res(lines, stop=True, code=1)
    return _res(lines)


def check_meta_and_aggregates(ctx: CheckCtx) -> Result:
    # 탭 화면이 쓰는 집계 — GUI 없이 여기서 먼저 깨지는지 본다
    api, details, ouid = ctx.api, ctx.details, ctx.ouid
    try:
        names = {m["id"]: m["name"] for m in api.get_meta("spid")}
        positions = {m["spposition"]: m["desc"] for m in api.get_meta("spposition")}
        lines = [f"[OK]   메타: 선수 {len(names)}명 / 포지션 {len(positions)}종"]
    except NexonAPIError as e:
        return _res([f"[FAIL] 메타데이터: {e.message}"], stop=True, code=1)
    try:
        players = core.aggregate_players(
            details, ouid, name_of=lambda i: names.get(i, str(i)),
            pos_name=lambda p: positions.get(p, str(p)))
        ctx.players = players
        lines.append(f"[OK]   선수 지표 {len(players)}명")
        for p in players[:3]:
            lines.append(f"       {p.position:>4} {p.name}  출전{p.games} 골{p.goal}"
                         f" 어시{p.assist} 패스{p.pass_rate:.0f}% 평점{p.rating:.2f}")

        mine = core.formation_stats(details, ouid, of_opponent=False)
        lines.append(f"[OK]   내 전술: {', '.join(f'{f.formation}({f.games})' for f in mine)}")
        lines.append("[OK]   상대 전술별 승률:")
        for f in core.formation_stats(details, ouid):
            lines.append(f"       {f.formation}  {f.win_rate:5.1f}%  "
                         f"({f.win}승 {f.draw}무 {f.lose}패)")

        rb = core.result_breakdown(details, ouid)
        lines.append("[OK]   경기 결과: " + " · ".join(
            f"{k} {v[0]}승{v[1]}무{v[2]}패" for k, v in
            [("전후반", rb.normal), ("연장", rb.extra),
             ("승부차기", rb.shootout), ("몰수", rb.forfeit)]))
        lines.append(f"[OK]   득점 유형: {dict(rb.goal_types.most_common(3))}")
    except Exception as e:
        lines.append(f"[FAIL] 집계: {type(e).__name__}: {e}")
        return _res(lines, stop=True, code=1)
    return _res(lines)


def check_player_image(ctx: CheckCtx) -> Result:
    try:
        ctx.first_sp = ctx.players[0].sp_id if ctx.players else None
        if ctx.first_sp is not None:
            path = images.fetch(ctx.first_sp, config.CACHE_DIR / "player_images")
            return _res([f"[OK]   선수 이미지 CDN: {'받음' if path else '실패(404 등 — 그림 없이 표시됨)'}"
                         f" (spId={ctx.first_sp})"])
        return _res(["[WARN] 선수 이미지 CDN: 확인할 선수가 없음"])
    except Exception as e:
        return _res([f"[WARN] 선수 이미지 CDN 확인 실패(전적엔 영향 없음): {e}"])


def check_ranker_stats(ctx: CheckCtx) -> Result:
    # 1.4.1 랭커 기록 — 가장 많이 뛴 카드 × 포지션 28개를 요청 하나로(선수 카드 [랭커 기록]과 같은 꼴). 값이 경기당
    # 평균인지도 눈으로 — 넥슨이 합계로 바꾸면 슛이 matchCount 배쯤 커진다
    first_sp = ctx.first_sp
    try:
        if first_sp is not None:
            rows = ctx.api.get_ranker_stats(config.DEFAULT_MATCH_TYPE, [(first_sp, po) for po in range(28)])
            if rows:
                r = max(rows, key=lambda x: (x.get("status") or {}).get("matchCount") or 0)
                st = r.get("status") or {}
                return _res([f"[OK]   랭커 기록: 포지션 {len(rows)}/28곳 · 표본 {st.get('matchCount')}경기"
                             f" · 경기당 슛 {st.get('shoot')} · 기준일 {matchtime.day(r.get('createDate')) or '-'}"])
            return _res(["[OK]   랭커 기록: 그 카드는 랭커 기록 없음(요청은 성공)"])
    except NexonAPIError as e:
        return _res([f"[WARN] 랭커 기록 조회 실패(랭커와 비교만 영향): {e.message}"])
    return _res([])


def check_season_meta(ctx: CheckCtx) -> Result:
    first_sp = ctx.first_sp
    try:
        seasons = {m["seasonId"]: m for m in ctx.api.get_meta("seasonid")
                   if "seasonId" in m}
        if first_sp is not None:
            season_id = core.season_id_of(first_sp)
            info = seasons.get(season_id)
            return _res([f"[OK]   시즌 메타: {len(seasons)}종 · spId={first_sp} → "
                         f"{info['className'] if info else '(매칭 안 됨: ' + str(season_id) + ')'}"])
        return _res([f"[OK]   시즌 메타: {len(seasons)}종"])
    except Exception as e:
        return _res([f"[WARN] 시즌 메타 확인 실패(전적엔 영향 없음): {e}"])


def check_manager_rank(ctx: CheckCtx) -> Result:
    try:
        import ranker
        r = ranker.fetch_manager_rank(ctx.nickname)
        if r.ranked:
            return _res([f"[OK]   데이터센터 랭킹: {r.rank:,}위 · 구단가치 {r.team_value_text}"
                         f" · ELO {r.elo} · 통산 {r.record_text}"])
        return _res(["[OK]   데이터센터 랭킹: 감독모드 1만 위 밖(순위 없음)"])
    except Exception as e:
        return _res([f"[WARN] 데이터센터 랭킹 조회 실패(전적엔 영향 없음): {e}"])


def check_rank_page(ctx: CheckCtx) -> Result:
    lines = []
    try:
        import ranker
        page = ranker.fetch_rank_rows(1)
        ctx.page = page
        verdict = ranker.judge_page(1, page.rows, None)  # 구조가 바뀌었으면 여기서 예외
        lines.append(f"[OK]   랭킹 목록 1쪽(수집용): {len(page.rows)}행 · 판정 {verdict} · Date {page.date}")
        # 2.3.1 N14 — 넥슨 데이터 기준 시각. 못 찾으면 갱신 걸침 판정이 응답 Date 의 시(時)로 돌아간다(R3)
        if page.ref_time is None:
            lines.append("[FAIL] 랭킹 기준 시각: 1쪽에서 'rank_advice' 문구를 못 찾음(구조가 바뀌었을 수 있음)")
        else:
            from datetime import datetime
            lag_h = (datetime.now() - datetime.fromisoformat(page.ref_time)).total_seconds() / 3600
            tag = "[WARN]" if lag_h > 2 else "[OK]  "
            lines.append(f"{tag} 랭킹 기준 시각: {page.ref_time} · 지금과 {lag_h:.1f}시간 차(R2: 보통 1시간 안팎)")
    except Exception as e:
        lines.append(f"[WARN] 랭킹 목록 수집 읽기 실패(수집만 영향): {type(e).__name__}: {e}")
        ctx.page = None
    return _res(lines)


def check_teamcolor(ctx: CheckCtx) -> Result:
    # 팀컬러(2.2.1) — 엠블럼 앵커 · 겹치는 이름 상수 · 효과 상세·선수(X-Requested-With) · 선수 JSON 단위
    lines = []
    try:
        import playerinfo
        import teamcolor
        page = ctx.page
        if page is not None:
            empty = [r.nickname for r in page.rows if r.team_color and not r.team_color_emblem]
            lines.append(f"{'[FAIL]' if empty else '[OK]  '} 랭킹 행 팀컬러 엠블럼: "
                         f"{sum(1 for r in page.rows if r.team_color_emblem)}행 · 엠블럼 없는 팀컬러 행 {len(empty)}")
        items = ctx.items = teamcolor.fetch_list()
        names: dict = {}
        for m in items:
            names[m.name] = names.get(m.name, 0) + 1
        dups = {n for n, k in names.items() if k > 1}
        same = dups == set(config.TEAMCOLOR_DUP_NAMES)
        lines.append(f"{'[OK]  ' if same else '[FAIL]'} 팀컬러 목록 {len(items)}개 · 겹치는 이름 {len(dups)}쌍"
                     + ("" if same else f" — 상수와 다름: 새 {sorted(dups - config.TEAMCOLOR_DUP_NAMES)}"
                                        f" · 사라짐 {sorted(config.TEAMCOLOR_DUP_NAMES - dups)} (config.TEAMCOLOR_DUP_NAMES 고칠 것)"))
        big = max(items, key=lambda m: (m.members or 0, m.max_step or 0))
        _desc, steps = teamcolor.fetch_detail(big.id)
        lines.append(f"[OK]   팀컬러 상세({big.name}): {len(steps)}단계 · "
                     + " / ".join(f"{s.step}단계 {s.members}명" for s in steps))
        players = teamcolor.fetch_players(big.id)
        lines.append(f"[OK]   팀컬러 선수: {len(players)}명(한 번 상한 {config.TEAMCOLOR_PLAYERS_PAGE}) · 최저 OVR "
                     f"{min((p.ovr for p in players if p.ovr is not None), default='-')}")
        # 카드 캐시(B)에 넣는 값이 선수 페이지와 같은 단위인가 — 급여·1강 시세
        p0 = next((p for p in players if p.prices.get(1)), None)
        if p0 is not None:
            info = playerinfo.fetch_player_info(p0.spid)
            page_price = playerinfo.prices_as_int(info).get(1)
            ok = info.salary == p0.pay and page_price == p0.prices[1]
            lines.append(f"{'[OK]  ' if ok else '[FAIL]'} 팀컬러 선수 JSON 단위({p0.name}): 급여 {p0.pay}/{info.salary}"
                         f" · 1강 시세 {p0.prices[1]}/{page_price}(JSON/선수 페이지)")
    except Exception as e:
        lines.append(f"[WARN] 팀컬러 효과 확인 실패(효과 창만 영향): {type(e).__name__}: {e}")
    return _res(lines)


def check_icons(ctx: CheckCtx) -> Result:
    # 표 칸 그림(40단계) — 종류마다 한 장씩 받는다. 앱과 같은 받기 함수(images.fetch_icon — Referer 포함)로 받아야
    # 앱의 403 이 여기서도 보인다. 사용자 캐시를 건드리지 않게 임시 폴더로 · 정적 CDN 이라 웹 데이터 스위치와 무관
    lines = []
    try:
        import tempfile
        from pathlib import Path
        samples = [("특성", "trait", 55), ("훈련 특성", "train", 0), ("등급 배지", "division", 3)]
        for kind in images.EMBLEM_PATHS:   # 구단 · 국기 · 강화 팀컬러 — 실제 팀컬러 목록의 키로(없으면 건너뜀)
            key = next((m.emblem for m in ctx.items if m.emblem.startswith(kind + "/")), "")
            if key:
                samples.append((f"엠블럼({kind})", "emblem", key))
            else:
                lines.append(f"[WARN] 엠블럼({kind}): 팀컬러 목록에 이 종류가 없어 건너뜀")
        with tempfile.TemporaryDirectory() as tmp:
            for label, kind, key in samples:
                url = images.icon_url(kind, key)
                path = images.fetch_icon(url, Path(tmp), kind)
                size = path.stat().st_size if path else 0
                lines.append(f"{'[OK]  ' if size else '[FAIL]'} 그림 {label}: {size:,}바이트 · {url.rsplit('/', 1)[-1]}")
    except Exception as e:
        lines.append(f"[WARN] 표 칸 그림 확인 실패(그림만 영향): {type(e).__name__}: {e}")
    return _res(lines)


def check_traits(ctx: CheckCtx) -> Result:
    # 포지션 특성(32단계) — 스쿼드메이커 구단주 번호 · 팀 칸 구조(선수 18명 · spid·state·traits) · 이름표가 코드를 다 아는지. 구조만 본다
    try:
        import trait_codes
        import traitcollect
        sess = traitcollect.new_session()
        sn = traitcollect.fetch_owner_sn(sess, ctx.nickname)
        if sn is None:
            return _res(["[WARN] 특성 칸: 이 닉네임의 스쿼드메이커 구단주를 못 찾았습니다(특성 받기만 영향)"])
        cards = None
        for tt, part in config.TRAIT_TEAM_ORDER:
            cards = traitcollect.fetch_team(sess, sn, tt, part)
            if cards:
                break
        if not cards:
            return _res(["[WARN] 특성 칸: 저장된 팀 칸이 없습니다 — 구조는 확인 못 함"])
        names = [trait_codes.name_of(k, c) for card in cards
                 for k, c in zip(("normal", "new", "train"), card.traits) if trait_codes.is_open_slot(c)]
        names += [trait_codes.name_of("coach", c) for card in cards for c in (card.trainer or ())
                  if trait_codes.is_open_slot(c)]
        miss = [n for n in names if n.startswith("코드 ")]
        return _res([f"{'[FAIL]' if len(cards) < 11 else '[OK]  '} 특성 칸(구단주 번호 {sn}): 선수 {len(cards)}명 · "
                     f"선발 {sum(c.state == 1 for c in cards)} · 이름 나옴 {len(names) - len(miss)}/{len(names)}"
                     f"{' · 이름표에 없는 코드 ' + ', '.join(sorted(set(miss))) if miss else ''}"])
    except Exception as e:
        return _res([f"[WARN] 특성 칸 확인 실패(특성 받기만 영향): {type(e).__name__}: {e}"])


def check_player_card(ctx: CheckCtx) -> Result:
    first_sp = ctx.first_sp
    lines = []
    try:
        import playerinfo
        if first_sp is not None:
            pinfo = playerinfo.fetch_player_info(first_sp)
            lines.append(f"[OK]   선수 카드 상세: {pinfo.name} {pinfo.position} OVR {pinfo.ovr}"
                         f" · 능력치 {len(pinfo.abilities)}개 · 특성 {len(pinfo.traits)}개"
                         f" · 시세 {len(pinfo.prices)}단계")
            # 축구장 칩(2.1.1) — 급여는 본 카드 구역에서만 읽는다. 못 찾으면 칩·띠의 급여가 빈다
            tag = "[OK]  " if isinstance(pinfo.salary, int) else "[FAIL]"
            lines.append(f"{tag} 카드 급여(본 카드 구역): {pinfo.salary}")
            # 강화별 OVR 가산표(config.GRADE_OVR_BONUS)가 넥슨 계산과 같은가 — 시뮬레이터 4번
            sims = {g: playerinfo.fetch_player_ability(first_sp, strong=g).ovr for g in (1, 5, 8, 13)}
            want = {g: sims[1] + config.GRADE_OVR_BONUS[g] for g in sims} if sims[1] is not None else {}
            ok = bool(want) and sims == want and pinfo.ovr == sims[1]
            lines.append(f"{'[OK]  ' if ok else '[FAIL]'} 강화별 OVR 가산표: 넥슨 {sims} · 표 {want} · 선수 페이지 1강 {pinfo.ovr}")
        else:
            lines.append("[WARN] 선수 카드 상세: 확인할 선수가 없음")
    except Exception as e:
        lines.append(f"[WARN] 선수 카드 상세 조회 실패(전적엔 영향 없음): {e}")
    return _res(lines)


def check_simulator(ctx: CheckCtx) -> Result:
    try:
        import playerinfo
        if ctx.first_sp is not None:
            sim = playerinfo.fetch_player_ability(ctx.first_sp)
            return _res([f"[OK]   능력치 시뮬레이터: OVR {sim.ovr}"
                         f" · 능력치 {len(sim.abilities)}개 · 팀컬러 선택지"
                         f" 소속 {len(sim.club_options)}/강화 {len(sim.enhance_options)}"
                         f"/관계 {len(sim.feature_options)}개"])
        return _res(["[WARN] 능력치 시뮬레이터: 확인할 선수가 없음"])
    except Exception as e:
        return _res([f"[WARN] 능력치 시뮬레이터 조회 실패(전적엔 영향 없음): {e}"])


def check_boost(ctx: CheckCtx) -> Result:
    # 강화 비용(83단계) — 재료 목록 형식 · 게이지 규칙(enhance.gauge_of)이 넥슨 값과 맞는지 한 조합으로. 저장 안 함
    first_sp = ctx.first_sp
    try:
        import enhance
        import playerinfo
        if not config.WEB_DATA:
            return _res(["[WARN] 강화 재료 목록: 웹 데이터 꺼짐 — 건너뜀"])
        if first_sp is not None:
            base = playerinfo.fetch_player_info(first_sp).ovr
            level = base + enhance.CUM_OVR[5] + enhance.GAUGE_ANCHOR - 2     # 게이지 48.9 자리
            rows = playerinfo.fetch_boost_rows(first_sp, level)
            res = playerinfo.web_boost_cal(first_sp, 5, [(rows[0][0], rows[0][1])]) if rows else None
            want = enhance.gauge_of(base, 5, rows[0][2]) if rows else None
            ok = res is not None and abs(res - want) <= 1.0
            return _res([f"{'[OK]  ' if ok else '[FAIL]'} 강화 재료 목록: {len(rows)}줄 · 게이지 넥슨 {res}% · 규칙 {want}%"])
        return _res(["[WARN] 강화 재료 목록: 확인할 선수가 없음"])
    except Exception as e:
        return _res([f"[WARN] 강화 재료 목록 조회 실패(전적엔 영향 없음): {e}"])


def check_card_backfill(ctx: CheckCtx) -> Result:
    # 카드 정보 자동 채우기(51단계) — 백필이 새로 기대는 칸(키·체형 · 포지션 16칸 · 소속 팀컬러)이 깨지면 여기서 먼저 보이게. 저장 안 함
    first_sp = ctx.first_sp
    try:
        import playerinfo
        if not config.WEB_DATA:
            return _res(["[WARN] 카드 백필 1장: 웹 데이터 꺼짐 — 건너뜀"])
        if first_sp is not None:
            info = playerinfo.fetch_player_info(first_sp)
            sim = playerinfo.fetch_player_ability(first_sp, 1)
            ok = playerinfo.page_ok(info) and info.body_type != "-" and playerinfo.sim_ok(sim)
            return _res([f"{'[OK]  ' if ok else '[FAIL]'} 카드 백필 1장: 키 {playerinfo.parse_cm(info.height)}cm · 체형 {info.body_type}"
                         f" · 포지션 {len(sim.position_ovrs)}칸(기대 {len(set(config.SIM_POS_OF.values()))})"
                         f" · 소속 팀컬러 {len(sim.club_options)}개 · 관계 {len(sim.feature_options)}개"])
    except Exception as e:
        return _res([f"[WARN] 카드 백필 1장 확인 실패(카드 정보 자동 채우기만 영향): {e}"])
    return _res([])


def check_grade(ctx: CheckCtx) -> Result:
    try:
        names = {m["divisionId"]: m["divisionName"] for m in ctx.api.get_meta("division")}
        me = next((p for p in ctx.details[0]["matchInfo"] if p["ouid"] == ctx.ouid), None)
        div_id = me.get("division") if me else None
        grade = names.get(div_id, str(div_id))
        champ = core.is_champion_or_above(div_id)
        return _res([f"[OK]   현재 등급(최근 경기 기준): {grade}"
                     f" — 챔피언스 이상: {'예' if champ else '아니오'}(랭커 카드 표시 여부)"])
    except Exception as e:
        return _res([f"[WARN] 등급 판단 실패: {e}"])


def check_db(ctx: CheckCtx) -> Result:
    ouid = ctx.ouid
    try:
        conn = store.open_db(config.DB_PATH)
        try:
            new = store.save_matches(conn, ctx.details)
            total = store.match_count(conn, ouid, config.DEFAULT_MATCH_TYPE)
            a, b = store.date_range(conn, ouid, config.DEFAULT_MATCH_TYPE)
            lines = [f"[OK]   DB({config.DB_PATH.name}): 이번에 {new}건 저장 · "
                     f"누적 {total}경기 ({matchtime.label(a)} ~ {matchtime.label(b)} KST)"]
            elo = store.elo_history(conn, ouid)
            tracked = any(t["ouid"] == ouid for t in store.track_list(conn))
            lines.append(f"[OK]   ELO 기록: 검색 {sum(r['source'] == 'search' for r in elo)}점 · "
                         f"스냅숏 {sum(r['source'] == 'snapshot' for r in elo)}점"
                         f" · 따라가기 {'함' if tracked else '안 함'}")
        finally:
            conn.close()
    except Exception as e:
        return _res([f"[FAIL] DB: {type(e).__name__}: {e}"], stop=True, code=1)
    return _res(lines)


def check_owner_dist(ctx: CheckCtx) -> Result:
    # 49단계 구단주 분포 — 상태 한 줄(받는 중·최근 바퀴·쓰는 분포) + 로더와 같은 요청 꼴 탐침: 목록 한 쪽(OWNER_DIST_PAGE ·
    # 재시도 없음)과 캐시 없는 상세 1건. 남의 닉네임 대신 이 계정 ouid 로 — 상세가 .cache 에 안 쓰이는지도 본다(14.6만 파일 방지)
    api = ctx.api
    lines = []
    try:
        from datetime import datetime as _dt
        import ownerdist
        conn = store.open_db(config.DB_PATH)
        try:
            status = ownerdist.status_text(conn, _dt.now())
        finally:
            conn.close()
        lines.append(f"[OK]   구단주 분포: {'켜짐' if config.owner_dist_allowed() else '꺼짐'} · {status}")
        page = api.get_match_ids(ctx.ouid, matchtype=config.DEFAULT_MATCH_TYPE, offset=0, limit=config.OWNER_DIST_PAGE,
                                 attempts=1)
        fresh = next((m for m in page if not api._cache_path(m).exists()), None)
        if fresh is None:
            lines.append(f"[WARN] 구단주 분포 탐침: 목록 {len(page)}건 — 캐시에 없는 경기가 없어 nocache 는 확인 못 함")
        else:
            d = api.get_match_detail_nocache(fresh)
            wrote = api._cache_path(fresh).exists()
            lines.append(f"{'[FAIL]' if wrote else '[OK]  '} 구단주 분포 탐침: 목록 {len(page)}건 · 캐시 없는 상세 "
                         f"{len(d.get('matchInfo') or [])}명분 · .cache 에 {'썼음!' if wrote else '안 씀'}")
    except NexonAPIError as e:
        lines.append(f"[WARN] 구단주 분포 탐침 실패(분포 받기만 영향): {e.message}  (code={e.code}, status={e.status})")
    except Exception as e:
        lines.append(f"[WARN] 구단주 분포 확인 실패(분포 받기만 영향): {type(e).__name__}: {e}")
    return _res(lines)


def check_rank_seasons(ctx: CheckCtx) -> Result:
    lines = []
    try:
        import seasons as sn
        rank_seasons = sn.fetch_seasons()
        latest = rank_seasons[0]
        lines.append(f"[OK]   랭킹 시즌표: {len(rank_seasons)}개 · 최신 {latest.label}"
                     f" ({latest.span_text})")
        groups = sn.group_by_season(rank_seasons, ctx.matches,
                                    key=lambda m: m.match_date)
        for season, group in groups[:3]:
            label = "진행 중" if season is None else season.label
            lines.append(f"       {label}: {len(group)}경기")
        ctx.rank_seasons = rank_seasons
    except Exception as e:
        lines.append(f"[WARN] 랭킹 시즌표 조회 실패(시즌별 보기만 영향): {e}")
        ctx.rank_seasons = []
    return _res(lines)


def check_prediction(ctx: CheckCtx) -> Result:
    # 1.3.1 시즌 말 예측 — 지난 시즌 최종 컷(실호출 1건) · 종료일 공지 · 예측
    from datetime import date, datetime
    rank_seasons, ouid = ctx.rank_seasons, ctx.ouid
    lines = []
    ended = [s for s in rank_seasons if s.end <= date.today()]
    start = max((s.end for s in ended), default=None)
    try:
        import ranker
        last = max(ended, key=lambda s: s.end) if ended else None
        if last is not None:
            lines.append(f"[OK]   지난 시즌 최종 200위 컷: {last.no}시즌 {ranker.fetch_season_cut(last.no, 200):.0f}")
    except Exception as e:
        lines.append(f"[WARN] 지난 시즌 컷 조회 실패(예측만 영향): {e}")
    try:
        import updatecheck
        n = updatecheck.check_full().season_notice
        if n is None:
            lines.append("[OK]   시즌 종료 공지: 없음 — 앱은 최근 시즌 길이로 추정")
        else:
            same = "지금 시즌과 같음" if n[1] == start else f"지금 시즌 시작 {start} 과 다름!"
            lines.append(f"[OK]   시즌 종료 공지: {n[0]:%m/%d} (시작 {n[1]:%m/%d} — {same})")
    except Exception as e:
        lines.append(f"[WARN] 시즌 종료 공지 확인 실패: {e}")
    try:
        import rankcollect
        conn = store.open_db(config.DB_PATH)
        try:
            elo = store.elo_history(conn, ouid)
            dates = store.match_dates(conn, ouid, "2000-01-01")
            logged = len(store.predictions(conn, ouid))
        finally:
            conn.close()
        r = rankcollect.open_rank_db_ro()
        try:
            pr = core.predict_for(elo_rows=elo, match_dates=dates, rank_conn=r, seasons=rank_seasons,
                                  notice=None, now=datetime.now())
        finally:
            if r is not None:
                r.close()
        lines.append(f"[OK]   시즌 말 예측: {core.describe_prediction(pr).splitlines()[0]} · 남긴 기록 {logged}일")
    except Exception as e:
        lines.append(f"[WARN] 시즌 말 예측 실패: {type(e).__name__}: {e}")
    return _res(lines)


CHECKS: list[tuple[str, Callable[[CheckCtx], Result]]] = [
    ("API 키", check_key_loaded), ("닉네임", check_ouid), ("중앙 서버", check_server), ("계정", check_basic),
    ("최고 등급", check_max_division), ("거래", check_trades), ("경기 목록", check_match_ids),
    ("경기 상세", check_details), ("메타·집계", check_meta_and_aggregates), ("선수 그림", check_player_image),
    ("랭커 기록", check_ranker_stats), ("시즌 메타", check_season_meta), ("데이터센터 랭킹", check_manager_rank),
    ("랭킹 목록", check_rank_page), ("팀컬러", check_teamcolor), ("표 칸 그림", check_icons),
    ("포지션 특성", check_traits), ("선수 카드", check_player_card), ("시뮬레이터", check_simulator),
    ("강화 재료", check_boost), ("카드 백필", check_card_backfill), ("현재 등급", check_grade),
    ("DB", check_db), ("구단주 분포", check_owner_dist), ("시즌표", check_rank_seasons), ("예측", check_prediction),
]


# ── 앱 [연결 점검] — 사용자용 묶음(96 W3) ───────────────────────────────
# 개발용 줄(랭킹 목록 판정·팀컬러 JSON 단위·특성 칸·구단주 분포·예측 — DB_PATH 를 연다)은 터미널에만.
# 키는 앞 8자도 안 쓴다 · 경기 상세는 캐시를 안 거친다(캐시에 있으면 요청 없이 OK 가 된다) · 아무것도 저장하지 않는다.
# 화면 글이라 docs/STYLE.md 를 따른다(합쇼체).

def user_key(ctx: CheckCtx) -> Result:
    if config.KEY_STATE == "fail":
        return Result(FAIL, ["API 키를 읽지 못했습니다. 키가 없는 것과는 다릅니다. 앱을 다시 켜 주세요."], stop=True)
    if not config.API_KEY:
        return Result(FAIL, ["API 키가 없습니다. [정보] 에서 키를 넣어 주세요."], stop=True)
    return Result(OK, ["API 키가 들어 있습니다."])


def user_route(ctx: CheckCtx) -> Result:
    server = bool(getattr(ctx.api, "server_mode", False))
    return Result(OK, ["중앙 서버를 거쳐 묻습니다." if server else "넥슨에 직접 묻습니다."])


def user_account(ctx: CheckCtx) -> Result:
    if not ctx.nickname:
        return Result(SKIP, ["닉네임을 넣으면 확인합니다."], stop=True)
    try:
        ctx.ouid = ctx.api.get_ouid(ctx.nickname)
        basic = ctx.api.get_user_basic(ctx.ouid)
    except NexonAPIError as e:
        return Result(FAIL, [e.message], stop=True)
    return Result(OK, [f"{basic.get('nickname') or ctx.nickname} 계정을 찾았습니다."])


def user_match_ids(ctx: CheckCtx) -> Result:
    try:
        ctx.ids = ctx.api.get_match_ids(ctx.ouid, config.DEFAULT_MATCH_TYPE, 0, 5)
    except NexonAPIError as e:
        return Result(FAIL, [e.message], stop=True)
    if not ctx.ids:
        return Result(WARN, ["감독모드 경기 기록이 없습니다."], stop=True)
    return Result(OK, [f"최근 경기 {len(ctx.ids)}건을 받았습니다."])


def user_match_detail(ctx: CheckCtx) -> Result:
    try:
        d = ctx.api.get_match_detail_nocache(ctx.ids[0])
    except NexonAPIError as e:
        return Result(FAIL, [e.message])
    except ValueError:
        return Result(FAIL, ["넥슨 응답의 형식이 맞지 않습니다."])
    if parse_match(d, ctx.ouid) is None:
        return Result(WARN, ["경기 상세를 받았지만 내 경기로 읽지 못했습니다."])
    return Result(OK, ["경기 상세 1건을 받아 읽었습니다."])


def user_meta(ctx: CheckCtx) -> Result:
    try:
        n = len(ctx.api.get_meta("spid"))
    except NexonAPIError as e:
        return Result(FAIL, [e.message])
    if not n:
        return Result(WARN, ["선수 이름표가 비어 있습니다. 선수 이름 대신 번호가 보일 수 있습니다."])
    return Result(OK, [f"선수 이름표 {n:,}명을 받았습니다."])


def user_cdn(ctx: CheckCtx) -> Result:
    import tempfile
    from pathlib import Path
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = images.fetch_icon(images.icon_url("division", 3), Path(tmp), "division")
            size = path.stat().st_size if path else 0
    except Exception:
        size = 0
    if not size:
        return Result(WARN, ["그림을 받지 못했습니다. 그림 없이 표시됩니다."])
    return Result(OK, ["그림 서버에서 그림을 받았습니다."])


def user_datacenter(ctx: CheckCtx) -> Result:
    if not config.WEB_DATA:
        return Result(SKIP, ["웹 데이터가 꺼져 있어 확인하지 않습니다."])
    try:
        import ranker
        r = ranker.fetch_manager_rank(ctx.nickname)
    except Exception:
        return Result(WARN, ["넥슨 데이터센터에 닿지 못했습니다. 순위·구단가치만 비어 보입니다."])
    return Result(OK, [f"감독모드 {r.rank:,}위입니다." if r.ranked else "감독모드 1만 위 밖입니다."])


USER_CHECKS: list[tuple[str, Callable[[CheckCtx], Result]]] = [
    ("API 키", user_key), ("경로", user_route), ("닉네임 → 계정", user_account), ("경기 목록", user_match_ids),
    ("경기 상세", user_match_detail), ("선수 이름표", user_meta), ("그림 서버", user_cdn),
    ("넥슨 데이터센터", user_datacenter),
]


def run_checks(checks, ctx: CheckCtx, on_result: Callable[[str, Result], None],
               stop: Callable[[], bool] = lambda: False) -> int:
    """차례대로 돌린다 — stop 인 결과에서 그 code 로 끝낸다(남은 점검은 안 돈다). 끝까지 가면 0."""
    for name, fn in checks:
        if stop():
            return 0
        r = fn(ctx)
        on_result(name, r)
        if r.stop:
            return r.code
    return 0


def main() -> int:
    # 한국어 Windows 콘솔은 기본 cp949라 한글·기호(—) 출력에서 죽는다. import 때가 아니라 여기서 — 앱이 이 모듈을
    # import 해도(96 W3 [연결 점검]) 앱의 stdout 은 안 건드린다
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("사용법: python check_api.py <닉네임>")
        return 1
    ctx = CheckCtx(nickname=sys.argv[1])
    stopped = []

    def show(_name: str, r: Result) -> None:
        for ln in r.lines:
            print(ln)
        if r.stop:
            stopped.append(r)

    code = run_checks(CHECKS, ctx, show)
    if stopped:
        return code
    print("\n전부 통과 — python app_main.py 로 앱을 띄우세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
