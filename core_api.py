"""화면 ↔ 분석 경계 — 화면 쪽 파일(app_main·dashboard·check_api)은 계산을 여기서만 가져온다.

나중에 핵심 알고리즘을 비공개 모듈로 옮길 때(docs/ROADMAP.md "배포 보호 준비") 화면에서 고칠 곳이
이 파일 하나가 되게 하려는 것이다. 화면에서 새 분석 함수를 쓰려면 여기 import 와 __all__ 에 한 줄씩.
화면 파일이 analysis·stats·models·predict·squad_timeline·trade_book 을 직접 import 하면 test_parsing 의 경계 테스트가 빨개진다.

⚠ 이름을 복사해 오므로 analysis.narrate 를 바꿔 끼워도 화면에는 안 닿는다 — 테스트는 core_api 쪽을 바꾼다.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
**화면 ↔ 분석 경계**(19단계) — 화면 쪽(`app_main`·`dashboard`·`check_api`)은 analysis·stats·models 를 여기서만 가져온다.
화면에서 새 분석 함수를 쓰려면 import 와 `__all__` 에 한 줄씩. 나중에 핵심을 비공개 모듈로 옮길 때 고칠 곳이 여기 하나가 되게(ROADMAP "배포 보호 준비").
이름을 복사해 오므로 **테스트에서 바꿔 끼울 땐 `core_api` 쪽을**(`app_main.core.narrate`).
`test_ui_reaches_analysis_only_through_core_api` 가 지킨다

**표시한 판 거르기(109단계 J1·J2)** — 이 파일 아래쪽 `_install()` 이 분석 함수를 이름을 내보내기 전에 감싼다(이름으로 import 한 쪽도
감싼 것을 받는다). 어떤 함수를 어떻게 다룰지는 선언 표 `FILTER_PLAN`(이름 → 갈래 · 거를 인자 이름)이 정본이다.
- F: 적힌 인자(경기 목록)에서 `set_excluded` 로 넘긴 경기 번호를 뺀 뒤 부른다. 묶음이 비면 목록을 새로 안 만든다.
- S: 안 건드린다(등급·FC 보상·스쿼드·저장 범위·구단주 분포·예측). MIX: 안 빼고 원래 함수의 `exclude=` 에 묶음을 넣는다
  (호출자가 `exclude` 를 넘겼으면 위치 인자여도 덮지 않는다). PASS: 경기 목록을 골라 돌려주는 함수라 거르지 않는다.
- 상세 창 안 그리기는 감싸지 않은 `core.raw.<이름>` 을 부른다. 거르기 판본은 `filter_version()`(꺼짐 0).
- **새 분석 함수를 더하면 `FILTER_PLAN` 에도 한 줄** — 안 적으면 `test_rules` 가 FAIL 이다. 화면 파일이 `.result` 로 승·무·패를 직접
  세지 않는다(`core.wdl` 등 F 함수로). 테스트 모드(`FIFA_FILTER_TRACE`)는 전부 감싸 불린 이름을 `called_names()` 로 모은다.
"""
from __future__ import annotations

import functools as _functools
import inspect as _inspect
import os as _os
import types as _types

from models import drop_ids as _drop_ids
from analysis import (
    MIN_COND, MIN_OPP, MIN_PLAYER_GAMES, SEC_FLOW, SEC_LOSE, SEC_WIN, SECTIONS, STREAK_MAX, WINDOW,
    Basis, headline, narrate, streak_min_n,
)
from models import (
    MatchSummary, NO_NICK, PeriodStat, current_streak, division_icon_index, is_mixed, is_weak, longest_streaks, mixed_of_details, mixed_of_matches,
    filter_matches, moving_win_rate, opponent_stats, parse_match, period_stats, rated, summarize, team_rating, win_pct, win_rate_trend, window_of,
    decided_shots, error_count, recent_games, wdl,
)
from habits import (GOAL_KIND_NOTE, GOAL_KINDS, MAX_LEAD, Chip, DivisionRun, DivisionStay, DivisionStays, GoalFlow, LeadRow,
                    division_runs, division_stays, goal_flow, half_share_diff, paired_diff, pct, side_totals, strength_chips,
                    AttackRoutes, ROUTES, RouteRow, attack_routes, classify_route,
                    HabitBin, HabitTable, Luck, RequiredRow, StreakBaseline, daily_luck, habit_tables, luck_expected,
                    streak_baseline, streak_key, DistBasis, dist_basis, dist_judged, DistRow, DistTable, dist_table,
                    METRICS as HABIT_METRICS, N19_CHIP_KEYS, PosCell, PosLevel, pos_level,
                    DaySummary, FirstReach, ScoreReport, club_day, day_delta, decisive_rate, first_reach, score_report,
                    CalDay, CalSummary, cal_summary, calendar_days, Run as HistoryRun, history_runs)
from sessions import (PlayTime, Session, SessionLuck, SessionRow, SizeBin, StreakBreak, match_context, period_totals,
                      play_time, session_luck, session_rows, size_bins, split_sessions, streak_break,
                      PaceSide, SeasonPace, SeasonReview, SeasonSpan, StreakNote, season_pace, season_review,
                      season_spans, stored_spans, streak_note, top_scorer,
                      DIVISION_LABELS as BREAK_DIVISION_LABELS, FORMATION_LABELS as BREAK_FORMATION_LABELS)
from ownerdist import OwnerDist, PosDist, StrataDist, load_dist as load_owner_dist, load_pos_dist, load_strata, stratum_of
from predict_check import judge_row as judge_predict_row, results as predict_final_results
from predict import (Prediction, SeasonEnd, describe as describe_prediction, predict_for, reward_brackets,
                     season_end_estimate)
from rankcollect import (EloHist, MetaTrend, RankTrend, data_time, elo_hist_series, elo_marker, meta_status,
                         is_champ, meta_trend, rank_trend_series)
from rankmeta import (
    FORMATION_HOWS, DayWinrate, Peers, RankMoves, TierMeans, ValueBins, ValueScore, Weekly, WinrateElo, blank_label,
    day_winrate, emblems_by_name, find_sn, formation_group, group_meta, latest_pair, my_day_rate, pair_text, rank_moves,
    ranked_nicknames, tier_means, value_bins, value_score, weekly, winrate_peers, winrate_to_elo,
    OppBand, OppRank, opp_rank_rows, opp_rank_table, opp_rank_label,
    OppScore, bin_of_value, opp_scores,
    ClubHead, club_head, dd_band, fc_rank_range, rank_of,
    RewardBand, RewardBoard, TopCard, reward_board, top3,
    MINOR_LABEL, is_minor_color, minor_formations, usage_counts,
    TcBoard, TcBoardRow, team_color_board, TcDetail, TcOwner, tc_detail, tc_top_owners,
    ChampGames, champ_games, rank_cut, last_raw_id,
)
from rankerpick import (
    SRC_OPP, SRC_PICK, SRC_RECOMMEND, PickCard, PickSummary, RecCard, Recommend, Standing, my_team_color,
    ranker_pick_summary, recommend, recommend_candidates,
)
from cardviews import (CardMove, PlayerCard, PriceIndex, SquadOvr, ValueBreakdown, lineup_cards, player_card,
                       price_days, price_index, squad_ovr, value_breakdown, screen_state as card_screen_state)
from enhance import (
    BUDGET_EXPECT_Q as ENHANCE_EXPECT_Q, BUDGET_QS as ENHANCE_BUDGET_QS, GRADES_MAX as ENHANCE_GRADES_MAX,
    PROTECT_FROM as ENHANCE_PROTECT_FROM, PROTECT_RANGE_PCTS as ENHANCE_RANGE_PCTS,
    EnhanceError, Estimate as EnhanceEstimate, cheapest_protect_from as enhance_cheapest_protect, estimate as enhance_estimate,
    parse_money as enhance_parse_money, scan_levels as enhance_levels,
)
from squad_timeline import (
    HELD, NEVER_PLAYED, NOT_RECENT, RECENT, Timeline, TimelineEvent, arrival_report, build_timeline, parse_trades,
    FormationReport, FormationRow, formation_report,
)
from trade_book import Ledger, ledger, price_targets
from records import (Achievement, DayPlayer, RecordRow, Rival, achievements as record_achievements,
                     by_match as records_by_match, day_player, kinds as record_kinds, last_day as last_play_day,
                     latest_day_player, recent as recent_records, rival, summary as record_summary)
from traitcollect import TraitSlot, TraitUsage, trait_usage
from stats import (
    END_KINDS, MIN_BUCKET_SHOTS, PITCH_ROWS, PITCH_X, PITCH_Y, PITCH_Y_MAX, SquadValue, card_ovr, pitch_rows, squad_value, KeyPlayers, key_players, PASS_KINDS, PASS_MIN_TRIES, PERIODS, PLAYER_RATING_MIN_GAMES, PLAYER_TREND_MIN_SHOTS, RANKER_METRICS,
    SHOT_GOAL, SHOT_OFF_TARGET, SHOT_ON_TARGET, SUB_POSITION, SUPER_CHAMPION_DIVISION_ID, CHAMPION_DIVISION_ID,
    Discipline, PositionOpponent, StreakAfter, after_streak_rates, aggregate_players, clutch_summary,
    discipline_stats, division_entries, division_stats, division_trend, finishing_ranking,
    formation_of, formation_stats, goal_minute_buckets, is_champion_or_above, match_formations,
    FormationUsage, OppCard, formation_label, opponent_cards, opponent_formations, opponent_position_players, opponent_squad, own_squad, pass_style, player_finishing_trend,
    position_line, possession_stats, ranker_compare, ranker_targets, ranker_values, rating_trend, recent_ratings, result_breakdown, season_divisions, season_id_of, shot_distance_breakdown, shot_map,
    shot_type_breakdown, team_color_baseline, team_color_mixed, team_color_stats, team_profile, time_of_day_rates,
    time_weekday_rates, VERSUS_ROWS, VersusLine, VersusProfile, versus_profile,
    MATCH_VERSUS_ROWS, MatchGoal, TimedShot, latest_match_with, match_goals, match_mvp, match_shots, match_sides, match_versus, went_to_extra,
    ShootoutKeeper, shootout_keepers, shot_type_counts,
    Scorer, ShotSide, ShotSummary, shot_summary,
    trade_hint, TIME_BANDS, TRADE_HINT_GAMES, WEEKDAYS,
    WinDivision, WinRewards, reward_for, win_divisions, win_rewards,
    COMPARE_LINES, COMPARE_MIN_GAMES, CompareDiff, LineCard, LinePair, compare_summary, line_pairs,
)

__all__ = [
    # analysis — 집계 → 문장
    "MIN_COND", "MIN_OPP", "MIN_PLAYER_GAMES", "SEC_FLOW", "SEC_LOSE", "SEC_WIN", "SECTIONS", "STREAK_MAX", "WINDOW",
    "Basis", "headline", "narrate", "streak_min_n",
    # models — 경기 파싱 · 한 계정 요약
    "MatchSummary", "NO_NICK", "PeriodStat", "current_streak", "filter_matches", "longest_streaks", "moving_win_rate", "opponent_stats", "parse_match",
    "period_stats", "summarize", "team_rating", "win_rate_trend", "window_of", "division_icon_index",
    # models — 승률 식(2.6.1 A6): 섞인 묶음은 승÷(승+패) · 한 집단은 승÷전체
    "is_mixed", "is_weak", "mixed_of_details", "mixed_of_matches", "rated", "win_pct",
    # models — 화면이 직접 세던 줄의 자리(109 J2 ⓐ): 승·무·패 · 오류 수 · 슛 합 · 결과 점 재료
    "decided_shots", "error_count", "recent_games", "wdl",
    # habits — 내 경기 습관·기준선(2.6.1) · N2 등급 체류
    "DivisionRun", "DivisionStay", "DivisionStays", "division_runs", "division_stays",
    # habits — N19 칩 · N17 골 흐름(2.7.1)
    "GOAL_KIND_NOTE", "GOAL_KINDS", "MAX_LEAD", "Chip", "GoalFlow", "LeadRow", "goal_flow", "half_share_diff",
    "paired_diff", "pct", "side_totals", "strength_chips",
    # habits·ownerdist — 구단주 분포 판정(49단계 조각 ②) · 받기(ownerdist.run_round)는 로더가 부른다
    "DistBasis", "dist_basis", "dist_judged", "HABIT_METRICS", "N19_CHIP_KEYS", "OwnerDist", "load_owner_dist",
    "DistRow", "DistTable", "StrataDist", "dist_table", "load_strata", "stratum_of",   # 97 X3 분포 대비
    "PosCell", "PosDist", "PosLevel", "load_pos_dist", "pos_level",   # C4 자리별 수준(50단계 ③)
    # habits — N16 운 범위 · A3 연승·연패 우연 기준 · N20 경기 습관(2.7.1)
    "HabitBin", "HabitTable", "Luck", "RequiredRow", "StreakBaseline", "daily_luck", "habit_tables", "luck_expected",
    "streak_baseline", "streak_key",
    # habits·rankmeta — 55 구단 머리 줄·점수 리포트(H1~H6 · R1~R3)
    "DaySummary", "FirstReach", "ScoreReport", "HistoryRun", "history_runs", "club_day", "day_delta", "decisive_rate", "first_reach", "score_report",
    "ClubHead", "club_head", "dd_band", "fc_rank_range", "rank_of",
    "RewardBand", "RewardBoard", "TopCard", "reward_board", "top3",
    "MINOR_LABEL", "is_minor_color", "minor_formations", "usage_counts",
    # habits — 55 D1 일별 기록 달력
    "CalDay", "CalSummary", "cal_summary", "calendar_days",
    # habits·sessions — 97 Y3 세션 · Y2 경기를 돌린 시간 · N1 연패 끊는 패턴
    "PlayTime", "Session", "SessionLuck", "SessionRow", "SizeBin", "StreakBreak", "match_context", "period_totals",
    "play_time", "session_luck", "session_rows", "size_bins", "split_sessions", "streak_break",
    "PaceSide", "SeasonPace", "SeasonReview", "SeasonSpan", "StreakNote", "season_pace", "season_review",
    "season_spans", "stored_spans", "streak_note", "top_scorer",
    "BREAK_DIVISION_LABELS", "BREAK_FORMATION_LABELS",
    # habits — N18 공격 루트(2.7.1)
    "AttackRoutes", "ROUTES", "RouteRow", "attack_routes", "classify_route",
    # predict — 시즌 말 순위 예측(46단계(옛 38)에서 비공개로 옮길 1순위)
    "Prediction", "describe_prediction", "predict_for", "reward_brackets", "SeasonEnd", "season_end_estimate",
    "judge_predict_row", "predict_final_results",   # B8 예측 되짚기(50단계)
    # rankerpick — 랭커 픽 집계(2.1.1 · 6). 받기(rankerpick.collect)는 화면이 아니라 로더가 부른다
    "PickCard", "PickSummary", "ranker_pick_summary",
    # traitcollect — 포지션 특성 집계(32단계). 받기(traitcollect.run)는 로더가 부른다
    "TraitSlot", "TraitUsage", "trait_usage",
    # rankerpick — 11-lite 추천(17단계) · rankcollect — 랭킹 추이(P2, rank.db 영구 집계)
    "SRC_OPP", "SRC_PICK", "SRC_RECOMMEND", "RecCard", "Recommend", "Standing", "my_team_color", "recommend",
    "recommend_candidates", "RankTrend", "rank_trend_series",
    "MetaTrend", "meta_trend", "EloHist", "elo_hist_series", "elo_marker", "data_time", "meta_status", "is_champ",
    # rankmeta — 랭커 메타 ② 스냅숏 읽기(2.4.1)
    "FORMATION_HOWS", "DayWinrate", "Peers", "RankMoves", "TierMeans", "ValueBins", "ValueScore", "Weekly",
    "WinrateElo", "blank_label", "day_winrate", "emblems_by_name", "find_sn", "formation_group", "group_meta", "latest_pair",
    "my_day_rate", "pair_text", "rank_moves", "ranked_nicknames", "tier_means", "value_bins", "value_score", "weekly",
    "winrate_peers", "winrate_to_elo",
    "TcBoard", "TcBoardRow", "team_color_board",   # 67 R1 팀컬러 목록
    "TcDetail", "TcOwner", "tc_detail", "tc_top_owners",   # 67 R2 팀컬러 상세 창
    "ChampGames", "champ_games", "rank_cut", "last_raw_id",   # 67 R5 달성 판수 · R4·R8 20위 컷
    # rankmeta — N23 상대 순위 구간(2.9.1)
    "OppBand", "OppRank", "opp_rank_rows", "opp_rank_table", "opp_rank_label",
    # rankmeta — 57 K3 상대 점수(저장 안 함) · K1 구단가치 칸
    "OppScore", "bin_of_value", "opp_scores",
    # cardviews — 카드 정보 화면 재료(68)
    "CardMove", "PlayerCard", "PriceIndex", "SquadOvr", "ValueBreakdown", "card_screen_state", "lineup_cards",
    "player_card", "price_days", "price_index", "squad_ovr", "value_breakdown",
    # enhance — 강화 비용(83). 재료 받기(boostcollect)는 로더가 부른다
    "ENHANCE_BUDGET_QS", "ENHANCE_EXPECT_Q", "ENHANCE_GRADES_MAX", "ENHANCE_PROTECT_FROM", "ENHANCE_RANGE_PCTS",
    "EnhanceError", "EnhanceEstimate", "enhance_cheapest_protect", "enhance_estimate", "enhance_levels", "enhance_parse_money",
    # squad_timeline · trade_book — 스쿼드 타임라인 · 이적시장 가계부(1.4.1)
    "HELD", "NEVER_PLAYED", "NOT_RECENT", "RECENT", "Timeline", "TimelineEvent", "arrival_report", "build_timeline", "parse_trades",
    "FormationReport", "FormationRow", "formation_report",   # 97 X2 포메이션 바꾼 전후
    "Ledger", "ledger", "price_targets",
    # records — 88 C1·U1 업적(저장 전체 · 요청 0)
    "Achievement", "RecordRow", "record_achievements", "records_by_match", "record_kinds", "recent_records", "record_summary",
    "Rival", "rival", "DayPlayer", "day_player", "last_play_day", "latest_day_player",   # 88 F4·Q2
    # stats — 여러 경기 집계
    "END_KINDS", "MIN_BUCKET_SHOTS", "PITCH_ROWS", "PITCH_X", "PITCH_Y", "PITCH_Y_MAX", "SquadValue", "card_ovr", "pitch_rows", "squad_value", "KeyPlayers", "key_players", "PASS_KINDS", "PASS_MIN_TRIES", "PERIODS", "PLAYER_RATING_MIN_GAMES", "PLAYER_TREND_MIN_SHOTS",
    "RANKER_METRICS", "SHOT_GOAL", "SHOT_OFF_TARGET",
    "SHOT_ON_TARGET", "SUB_POSITION", "SUPER_CHAMPION_DIVISION_ID", "CHAMPION_DIVISION_ID", "Discipline", "PositionOpponent", "StreakAfter",
    "after_streak_rates", "aggregate_players", "clutch_summary", "discipline_stats",
    "division_entries", "division_stats", "division_trend", "finishing_ranking", "formation_of", "formation_stats",
    "goal_minute_buckets", "is_champion_or_above", "match_formations",
    "FormationUsage", "OppCard", "formation_label", "opponent_cards", "opponent_formations", "opponent_position_players", "opponent_squad", "own_squad", "pass_style", "player_finishing_trend",
    "position_line", "possession_stats", "ranker_compare", "ranker_targets", "ranker_values", "rating_trend", "recent_ratings",
    "result_breakdown", "season_divisions", "season_id_of",
    "shot_distance_breakdown", "shot_map",
    "shot_type_breakdown", "team_color_baseline", "team_color_mixed", "team_color_stats", "team_profile", "time_of_day_rates",
    "time_weekday_rates", "trade_hint", "TIME_BANDS", "TRADE_HINT_GAMES", "WEEKDAYS",
    "VERSUS_ROWS", "VersusLine", "VersusProfile", "versus_profile",   # 65 M1 대시보드 나와 상대
    "MATCH_VERSUS_ROWS", "MatchGoal", "TimedShot", "latest_match_with", "match_goals", "match_mvp", "match_shots", "match_sides", "match_versus", "went_to_extra",   # 65 M3 경기 상세 창
    "ShootoutKeeper", "shootout_keepers", "shot_type_counts",   # 65 M6 승부차기 상대 GK · 슛 타입 분포
    "Scorer", "ShotSide", "ShotSummary", "shot_summary",   # 65 M8 슛 맵 오른쪽 패널
    # stats — A2 FC 보상(43단계)
    "WinDivision", "WinRewards", "reward_for", "win_divisions", "win_rewards",
    # stats — D1 구단주 비교 확장(50단계)
    "COMPARE_LINES", "COMPARE_MIN_GAMES", "CompareDiff", "LineCard", "LinePair", "compare_summary", "line_pairs",
]

# ── 109 J1·J2 표시한 판 거르기 — 분석 함수의 입구 하나 ─────────────────────────────────────────
# 위 이름 중 클래스가 아닌 호출 가능한 것(=분석 함수)은 전부 아래 FILTER_PLAN 에 갈래를 적는다(정적 검사 test_rules).
#   F    경기 결과·내용에서 나온 숫자 — 적힌 인자(경기 목록)에서 표시한 경기 번호를 뺀 뒤 부른다
#   S    넥슨이 매긴 상태·저장 범위·경기와 무관한 것 — 안 건드린다
#   MIX  한 함수가 둘 다 낸다 — 안 빼고, 호출자가 exclude= 를 안 넘겼으면 표시 묶음을 넣어 그 함수가 승·무·패 쪽만 뺀다
#   PASS 경기 목록을 골라 돌려주는 함수 — 거르면 목록에서 표시한 판이 사라져 상세 열기가 깨진다(M31). 세는 쪽이 F 여야 한다
# 인자 이름은 "경기 목록이 들어오는 자리"다(MIX 는 거르지 않지만 정적 검사가 시그니처와 맞춰 본다).
# 판정: 승·무·패·득실·승률·연승·세션·달력 칸의 승·업적의 승 기록·라이벌·오늘의 선수 = F. 등급·FC 보상·스쿼드·카드·포메이션 이력·
# 저장 범위·구단주 분포·예측·시세 = S(J3). 단일 경기 상세를 받는 함수(match_*)는 상세 창이 부르므로 S.
# 새 분석 함수를 core_api 에 더하면 여기에도 한 줄 — 안 적으면 test_rules 가 FAIL.
_F_MATCHES = ("current_streak", "longest_streaks", "opponent_stats", "period_stats", "summarize", "win_rate_trend",
              "moving_win_rate", "mixed_of_matches", "habit_tables", "streak_baseline", "streak_key", "split_sessions",
              "streak_break", "streak_note", "rival", "after_streak_rates", "team_color_baseline", "team_color_mixed",
              "team_color_stats", "time_of_day_rates", "time_weekday_rates", "my_day_rate",
              "wdl", "error_count", "decided_shots", "recent_games")
_F_DETAILS = ("mixed_of_details", "goal_flow", "half_share_diff", "side_totals", "attack_routes", "top_scorer",
              "day_player", "last_play_day", "latest_day_player", "opp_rank_table", "key_players", "aggregate_players",
              "clutch_summary", "discipline_stats", "division_stats", "finishing_ranking", "formation_stats",
              "goal_minute_buckets", "opponent_cards", "opponent_formations", "opponent_position_players", "pass_style",
              "player_finishing_trend", "possession_stats", "ranker_compare", "rating_trend", "recent_ratings",
              "result_breakdown", "shot_map", "versus_profile", "shootout_keepers", "shot_type_counts", "shot_summary",
              "team_profile",
              # M39: 영입 시점·포메이션 바뀐 구간 경계를 표시한 판을 뺀 기록에서 다시 잡는다(표시한 판이 처음부터 없었던 것처럼).
              # 섞임으로 두면 squad_timeline 의 구간 계산을 둘로 갈라야 하고, 영입이 표시한 판 자리면 전후 구간에 그 판이 끼어
              # 숫자에 남는다. 경계가 한 판 밀릴 수 있어도 화면의 모든 승·무·패는 표시한 판을 안 센다.
              "arrival_report", "formation_report",
              # 사건 앞뒤 창(Window)의 승·무·패를 낸다 — arrival_report 와 같은 이유로 F(사건 경계도 표시한 판을 뺀 기록에서)
              "build_timeline")

FILTER_PLAN: dict[str, tuple[str, tuple[str, ...]]] = {
    **{n: ("F", ("matches",)) for n in _F_MATCHES},
    **{n: ("F", ("details",)) for n in _F_DETAILS},
    "narrate": ("F", ("matches", "details")),
    "compare_summary": ("F", ("my_details", "opp_details")),   # U9: 상대 쪽 상세도 같은 판이 빠진다
    "line_pairs": ("F", ("my_details", "opp_details")),
    # 섞임 — 승·무·패(F)와 FC·등급·점수(S)를 한 함수가 같이 낸다. 원래 함수의 exclude= 가 승·무·패 쪽만 뺀다
    "club_day": ("MIX", ("rows",)),
    "calendar_days": ("MIX", ("rows",)),
    "cal_summary": ("MIX", ("rows",)),
    "first_reach": ("MIX", ("rows",)),          # 도달 판수는 S 라 exclude 를 받기만 한다(habits.first_reach 주석)
    "season_pace": ("MIX", ("matches", "wins")),
    "season_review": ("MIX", ("matches", "details")),
    "record_achievements": ("MIX", ("details",)),
    # 통과
    "window_of": ("PASS", ()),
    "filter_matches": ("PASS", ()),
    "latest_match_with": ("PASS", ()),          # M31
    # S — 넥슨 기록·저장 범위·경기와 무관(J3: 예측·구단주 분포·decisive_rate 는 같은 판 묶음이어야 견줘진다)
    **{n: ("S", ()) for n in (
        "headline", "streak_min_n", "parse_match", "team_rating", "division_icon_index", "is_mixed", "is_weak",
        "rated", "win_pct", "division_runs", "division_stays", "paired_diff", "pct", "strength_chips", "dist_basis",
        "dist_judged", "load_owner_dist", "dist_table", "load_strata", "stratum_of", "load_pos_dist", "pos_level",
        "daily_luck", "luck_expected", "history_runs", "day_delta", "decisive_rate", "score_report", "club_head",
        "dd_band", "fc_rank_range", "rank_of", "reward_board", "top3", "is_minor_color", "minor_formations",
        "usage_counts", "match_context", "period_totals", "play_time", "session_luck", "session_rows", "size_bins",
        "season_spans", "stored_spans", "classify_route", "describe_prediction", "predict_for", "reward_brackets",
        "season_end_estimate", "judge_predict_row", "predict_final_results", "ranker_pick_summary", "trait_usage",
        "my_team_color", "recommend", "recommend_candidates", "rank_trend_series", "meta_trend", "elo_hist_series",
        "elo_marker", "data_time", "meta_status", "is_champ", "blank_label", "day_winrate", "emblems_by_name",
        "find_sn", "formation_group", "group_meta", "latest_pair", "pair_text", "rank_moves", "ranked_nicknames",
        "tier_means", "value_bins", "value_score", "weekly", "winrate_peers", "winrate_to_elo", "team_color_board",
        "tc_detail", "tc_top_owners", "champ_games", "rank_cut", "last_raw_id", "opp_rank_rows", "opp_rank_label",
        "bin_of_value", "opp_scores", "card_screen_state", "lineup_cards", "player_card", "price_days", "price_index",
        "squad_ovr", "value_breakdown", "enhance_cheapest_protect", "enhance_estimate", "enhance_levels",
        "enhance_parse_money", "parse_trades", "ledger", "price_targets", "records_by_match",
        "record_kinds", "recent_records", "record_summary", "card_ovr", "pitch_rows", "squad_value",
        "division_entries", "division_trend", "formation_of", "is_champion_or_above", "match_formations",
        "formation_label", "opponent_squad", "own_squad", "position_line", "ranker_targets", "ranker_values",
        "season_divisions", "season_id_of", "shot_distance_breakdown", "shot_type_breakdown",
        "trade_hint", "match_goals", "match_mvp", "match_shots", "match_sides", "match_versus", "went_to_extra",
        "reward_for", "win_divisions", "win_rewards")},
}

TRACE_ENV = "FIFA_FILTER_TRACE"   # 값이 있으면(테스트) 표 밖 이름까지 전부 감싸 불린 이름을 모은다(M43)
CONTROL_NAMES = frozenset({"set_excluded", "excluded", "filter_version", "bump_filter_version", "called_names",
                           "reset_called"})   # 거르기를 조종하는 함수 — 분석 함수가 아니라 FILTER_PLAN 밖

_EXCLUDED: frozenset = frozenset()      # 재할당 전역 — 화면은 core.excluded() 로만 읽는다(모듈 경유 · 전역 규칙 8)
_FILTER_VERSION = 0                     # 표시를 고칠 때마다 하나 오르는 수(J6)
_CALLED: set | None = set() if _os.environ.get(TRACE_ENV) else None


def set_excluded(ids) -> None:
    """표시한 경기 번호 묶음을 통째로 바꿔 끼운다 — frozenset 이라 작업 스레드가 반쯤 바뀐 값을 읽지 않는다."""
    global _EXCLUDED
    _EXCLUDED = frozenset(ids or ())


def excluded() -> frozenset:
    return _EXCLUDED


def bump_filter_version() -> int:
    """표시를 고쳤을 때 부른다(쓰는 곳은 ①-2 `_refilter`). 오른 번호를 돌려준다."""
    global _FILTER_VERSION
    _FILTER_VERSION += 1
    return _FILTER_VERSION


def filter_version() -> int:
    """화면 캐시 키에 더할 거르기 판본(J6) — 거를 판이 없으면 0, 있으면 표시 판본 번호."""
    return _FILTER_VERSION if _EXCLUDED else 0


def called_names() -> set:
    """TRACE_ENV 를 켜고 돌렸을 때 지금까지 불린 분석 함수 이름(켜지 않았으면 빈 묶음)."""
    return set(_CALLED or ())


def reset_called() -> None:
    if _CALLED is not None:
        _CALLED.clear()


def _wrap(name: str, fn, kind: str, args: tuple):
    """분석 함수 하나를 갈래에 맞게 감싼다. 시그니처는 여기서 한 번만 계산(M43)."""
    sig = _inspect.signature(fn)

    if kind == "F":
        @_functools.wraps(fn)
        def wrapper(*a, **k):
            if _CALLED is not None:
                _CALLED.add(name)
            ex = _EXCLUDED                  # 한 호출에서 한 번만 읽는다 — 목록 둘이 같은 판본으로 걸러진다
            if not ex:
                return fn(*a, **k)          # 묶음이 비면 목록을 새로 안 만든다
            b = sig.bind(*a, **k)
            for n in args:
                if b.arguments.get(n) is not None:
                    b.arguments[n] = _drop_ids(b.arguments[n], ex)
            return fn(*b.args, **b.kwargs)
    elif kind == "MIX":
        @_functools.wraps(fn)
        def wrapper(*a, **k):
            if _CALLED is not None:
                _CALLED.add(name)
            ex = _EXCLUDED
            if not ex:
                return fn(*a, **k)
            b = sig.bind(*a, **k)
            if "exclude" not in b.arguments:    # apply_defaults 앞에서 본다 — 호출자가 넘겼으면(위치 인자 포함) 덮지도 합치지도 않는다
                b.arguments["exclude"] = ex
            return fn(*b.args, **b.kwargs)
    else:
        @_functools.wraps(fn)
        def wrapper(*a, **k):
            if _CALLED is not None:
                _CALLED.add(name)
            return fn(*a, **k)
    return wrapper


def _install() -> None:
    """이름을 내보내기 전에 감싼다 — dashboard.py 처럼 이름으로 import 하는 쪽이 감싼 것을 받는다.
    배포판은 F·MIX 만 감싸고(parse_match·win_pct 처럼 많이 불리는 S 는 그대로), 테스트(TRACE_ENV)는 전부 감싸 기록한다."""
    ns = globals()
    originals = {}
    for name in __all__:
        obj = ns[name]
        if _inspect.isclass(obj) or not callable(obj):
            continue
        originals[name] = obj
    ns["raw"] = _types.SimpleNamespace(**originals)   # 감싸지 않은 원래 함수 — 상세 창 안 그리기가 쓴다(J1 · R3)
    for name, fn in originals.items():
        kind, args = FILTER_PLAN.get(name, ("S", ()))
        if kind in ("F", "MIX") or _CALLED is not None:
            ns[name] = _wrap(name, fn, kind, args)


_install()
__all__ += ["FILTER_PLAN", "TRACE_ENV", "CONTROL_NAMES", "raw", "set_excluded", "excluded", "filter_version",
            "bump_filter_version", "called_names", "reset_called"]
