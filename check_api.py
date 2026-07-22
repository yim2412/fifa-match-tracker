"""API 연결 점검 — GUI 띄우기 전에 키·엔드포인트가 맞는지 터미널에서 확인.

    python check_api.py 닉네임
"""
from __future__ import annotations

import sys

# 한국어 Windows 콘솔은 기본 cp949라 한글·기호(—) 출력에서 죽는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
import images
import stats
import store
from models import parse_match, summarize
from nexon_api import FCOnlineAPI, NexonAPIError


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python check_api.py <닉네임>")
        return 1
    nickname = sys.argv[1]

    if not config.API_KEY:
        print("[FAIL] .env 에 NEXON_API_KEY가 없습니다.")
        return 1
    print(f"[OK]   API 키 로드 ({config.API_KEY[:8]}…)")

    api = FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)

    try:
        ouid = api.get_ouid(nickname)
        print(f"[OK]   닉네임 → ouid: {ouid}")
    except NexonAPIError as e:
        print(f"[FAIL] 닉네임 조회: {e.message}  (code={e.code}, status={e.status})")
        return 1

    try:
        basic = api.get_user_basic(ouid)
        print(f"[OK]   계정: {basic.get('nickname')} / Lv.{basic.get('level')}")
    except NexonAPIError as e:
        print(f"[FAIL] 계정 정보: {e.message}")

    try:
        ids = api.get_match_ids(ouid, config.DEFAULT_MATCH_TYPE, 0, 5)
        print(f"[OK]   최근 매치 {len(ids)}건: {ids[:2]}")
    except NexonAPIError as e:
        print(f"[FAIL] 매치 목록: {e.message}")
        return 1

    if not ids:
        print("[WARN] 공식경기 기록이 없어 상세 조회는 건너뜁니다.")
        return 0

    try:
        matches = []
        details = []
        for mid in ids:
            d = api.get_match_detail(mid)
            details.append(d)
            m = parse_match(d, ouid)
            if m:
                matches.append(m)
        print(f"[OK]   매치 상세 파싱 {len(matches)}건")
        for m in matches:
            print(f"       {m.date_text}  {m.result}  {m.score}  vs {m.opponent}"
                  f"  (점유 {m.possession}%, 평점 {m.rating:.2f})")
        s = summarize(matches)
        print(f"\n요약: {s.win}승 {s.draw}무 {s.lose}패 · 승률 {s.win_rate:.1f}%"
              f" · 평균 {s.avg_goals_for:.2f}득 {s.avg_goals_against:.2f}실")
    except NexonAPIError as e:
        print(f"[FAIL] 매치 상세: {e.message}")
        return 1

    # 탭 화면이 쓰는 집계 — GUI 없이 여기서 먼저 깨지는지 본다
    try:
        names = {m["id"]: m["name"] for m in api.get_meta("spid")}
        positions = {m["spposition"]: m["desc"] for m in api.get_meta("spposition")}
        print(f"[OK]   메타: 선수 {len(names)}명 / 포지션 {len(positions)}종")
    except NexonAPIError as e:
        print(f"[FAIL] 메타데이터: {e.message}")
        return 1

    try:
        players = stats.aggregate_players(
            details, ouid, name_of=lambda i: names.get(i, str(i)),
            pos_name=lambda p: positions.get(p, str(p)))
        print(f"[OK]   선수 지표 {len(players)}명")
        for p in players[:3]:
            print(f"       {p.position:>4} {p.name}  출전{p.games} 골{p.goal}"
                  f" 어시{p.assist} 패스{p.pass_rate:.0f}% 평점{p.rating:.2f}")

        mine = stats.formation_stats(details, ouid, of_opponent=False)
        print(f"[OK]   내 전술: {', '.join(f'{f.formation}({f.games})' for f in mine)}")
        print("[OK]   상대 전술별 승률:")
        for f in stats.formation_stats(details, ouid):
            print(f"       {f.formation}  {f.win_rate:5.1f}%  "
                  f"({f.win}승 {f.draw}무 {f.lose}패)")

        rb = stats.result_breakdown(details, ouid)
        print("[OK]   경기 결과: " + " · ".join(
            f"{k} {v[0]}승{v[1]}무{v[2]}패" for k, v in
            [("전후반", rb.normal), ("연장", rb.extra),
             ("승부차기", rb.shootout), ("몰수", rb.forfeit)]))
        print(f"[OK]   득점 유형: {dict(rb.goal_types.most_common(3))}")
    except Exception as e:
        print(f"[FAIL] 집계: {type(e).__name__}: {e}")
        return 1

    try:
        first_sp = players[0].sp_id if players else None
        if first_sp is not None:
            path = images.fetch(first_sp, config.CACHE_DIR / "player_images")
            print(f"[OK]   선수 이미지 CDN: {'받음' if path else '실패(404 등 — 그림 없이 표시됨)'}"
                  f" (spId={first_sp})")
        else:
            print("[WARN] 선수 이미지 CDN: 확인할 선수가 없음")
    except Exception as e:
        print(f"[WARN] 선수 이미지 CDN 확인 실패(전적엔 영향 없음): {e}")

    try:
        seasons = {m["seasonId"]: m for m in api.get_meta("seasonid")
                  if "seasonId" in m}
        if first_sp is not None:
            season_id = stats.season_id_of(first_sp)
            info = seasons.get(season_id)
            print(f"[OK]   시즌 메타: {len(seasons)}종 · spId={first_sp} → "
                  f"{info['className'] if info else '(매칭 안 됨: ' + str(season_id) + ')'}")
        else:
            print(f"[OK]   시즌 메타: {len(seasons)}종")
    except Exception as e:
        print(f"[WARN] 시즌 메타 확인 실패(전적엔 영향 없음): {e}")

    try:
        import ranker
        r = ranker.fetch_manager_rank(nickname)
        if r.ranked:
            print(f"[OK]   데이터센터 랭킹: {r.rank:,}위 · 구단가치 {r.team_value_text}"
                  f" · ELO {r.elo} · 통산 {r.record_text}")
        else:
            print("[OK]   데이터센터 랭킹: 감독모드 1만 위 밖(순위 없음)")
    except Exception as e:
        print(f"[WARN] 데이터센터 랭킹 조회 실패(전적엔 영향 없음): {e}")

    try:
        import playerinfo
        if first_sp is not None:
            pinfo = playerinfo.fetch_player_info(first_sp)
            print(f"[OK]   선수 카드 상세: {pinfo.name} {pinfo.position} OVR {pinfo.ovr}"
                  f" · 능력치 {len(pinfo.abilities)}개 · 특성 {len(pinfo.traits)}개"
                  f" · 시세 {len(pinfo.prices)}단계")
        else:
            print("[WARN] 선수 카드 상세: 확인할 선수가 없음")
    except Exception as e:
        print(f"[WARN] 선수 카드 상세 조회 실패(전적엔 영향 없음): {e}")

    try:
        import playerinfo
        if first_sp is not None:
            sim = playerinfo.fetch_player_ability(first_sp)
            print(f"[OK]   능력치 시뮬레이터: OVR {sim.ovr}"
                  f" · 능력치 {len(sim.abilities)}개 · 팀컬러 선택지"
                  f" 소속 {len(sim.club_options)}/강화 {len(sim.enhance_options)}"
                  f"/관계 {len(sim.feature_options)}개")
        else:
            print("[WARN] 능력치 시뮬레이터: 확인할 선수가 없음")
    except Exception as e:
        print(f"[WARN] 능력치 시뮬레이터 조회 실패(전적엔 영향 없음): {e}")

    try:
        names = {m["divisionId"]: m["divisionName"] for m in api.get_meta("division")}
        me = next((p for p in details[0]["matchInfo"] if p["ouid"] == ouid), None)
        div_id = me.get("division") if me else None
        grade = names.get(div_id, str(div_id))
        champ = stats.is_champion_or_above(div_id)
        print(f"[OK]   현재 등급(최근 경기 기준): {grade}"
              f" — 챔피언스 이상: {'예' if champ else '아니오'}(랭커 카드 표시 여부)")
    except Exception as e:
        print(f"[WARN] 등급 판단 실패: {e}")

    try:
        conn = store.open_db(config.DB_PATH)
        try:
            new = store.save_matches(conn, details)
            total = store.match_count(conn, ouid, config.DEFAULT_MATCH_TYPE)
            a, b = store.date_range(conn, ouid, config.DEFAULT_MATCH_TYPE)
            print(f"[OK]   DB({config.DB_PATH.name}): 이번에 {new}건 저장 · "
                  f"누적 {total}경기 ({a} ~ {b})")
        finally:
            conn.close()
    except Exception as e:
        print(f"[FAIL] DB: {type(e).__name__}: {e}")
        return 1

    print("\n전부 통과 — python app_main.py 로 앱을 띄우세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
