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
"""
from __future__ import annotations

from analysis import (
    MIN_COND, MIN_OPP, MIN_PLAYER_GAMES, SEC_FLOW, SEC_LOSE, SEC_WIN, SECTIONS, STREAK_MAX, WINDOW,
    Basis, headline, narrate, streak_min_n,
)
from models import (
    MatchSummary, current_streak, division_icon_index, is_mixed, is_weak, longest_streaks, mixed_of_details, mixed_of_matches,
    filter_matches, moving_win_rate, opponent_stats, parse_match, period_stats, rated, summarize, win_pct, win_rate_trend,
)
from habits import (GOAL_KIND_NOTE, GOAL_KINDS, MAX_LEAD, Chip, DivisionStay, DivisionStays, GoalFlow, LeadRow,
                    division_stays, goal_flow, half_share_diff, paired_diff, pct, side_totals, strength_chips,
                    AttackRoutes, ROUTES, RouteRow, attack_routes, classify_route,
                    DayLuck, HabitBin, HabitTable, Luck, RequiredRow, StreakBaseline, daily_luck, habit_tables,
                    streak_baseline, streak_key, DistBasis, dist_basis, dist_judged,
                    METRICS as HABIT_METRICS, N19_CHIP_KEYS, PosCell, PosLevel, pos_level,
                    DaySummary, FirstReach, ScoreReport, club_day, day_delta, decisive_rate, first_reach, score_report,
                    CalDay, CalSummary, cal_summary, calendar_days, Run as HistoryRun, history_runs)
from ownerdist import OwnerDist, PosDist, load_dist as load_owner_dist, load_pos_dist
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
)
from rankerpick import (
    SRC_OPP, SRC_PICK, SRC_RECOMMEND, PickCard, PickSummary, RecCard, Recommend, Standing, my_team_color,
    ranker_pick_summary, recommend, recommend_candidates,
)
from squad_timeline import (
    HELD, NEVER_PLAYED, NOT_RECENT, RECENT, Timeline, TimelineEvent, build_timeline, parse_trades,
)
from trade_book import Ledger, ledger, price_targets
from traitcollect import TraitSlot, TraitUsage, trait_usage
from stats import (
    END_KINDS, MIN_BUCKET_SHOTS, PITCH_ROWS, PITCH_X, PITCH_Y, PITCH_Y_MAX, SquadValue, card_ovr, pitch_rows, squad_value, KeyPlayers, key_players, PASS_KINDS, PASS_MIN_TRIES, PERIODS, PLAYER_RATING_MIN_GAMES, PLAYER_TREND_MIN_SHOTS, RANKER_METRICS,
    SHOT_GOAL, SHOT_OFF_TARGET, SHOT_ON_TARGET, SUB_POSITION, SUPER_CHAMPION_DIVISION_ID, CHAMPION_DIVISION_ID,
    Discipline, PositionOpponent, StreakAfter, after_streak_rates, aggregate_players, clutch_summary,
    daily_division, discipline_stats, division_entries, division_stats, division_trend, finishing_ranking,
    formation_of, formation_stats, goal_minute_buckets, is_champion_or_above, match_formations,
    FormationUsage, OppCard, formation_label, opponent_cards, opponent_formations, opponent_position_players, opponent_squad, own_squad, pass_style, player_finishing_trend,
    position_line, possession_stats, ranker_compare, ranker_targets, ranker_values, rating_trend, result_breakdown, season_divisions, season_id_of, shot_distance_breakdown, shot_map,
    shot_type_breakdown, team_color_baseline, team_color_mixed, team_color_stats, team_profile, time_of_day_rates,
    time_weekday_rates,
    trade_hint, TIME_BANDS, TRADE_HINT_GAMES, WEEKDAYS,
    WinDivision, WinRewards, reward_for, win_divisions, win_rewards,
    COMPARE_LINES, COMPARE_MIN_GAMES, CompareDiff, LineCard, LinePair, compare_summary, line_pairs,
)

__all__ = [
    # analysis — 집계 → 문장
    "MIN_COND", "MIN_OPP", "MIN_PLAYER_GAMES", "SEC_FLOW", "SEC_LOSE", "SEC_WIN", "SECTIONS", "STREAK_MAX", "WINDOW",
    "Basis", "headline", "narrate", "streak_min_n",
    # models — 경기 파싱 · 한 계정 요약
    "MatchSummary", "current_streak", "filter_matches", "longest_streaks", "moving_win_rate", "opponent_stats", "parse_match",
    "period_stats", "summarize", "win_rate_trend", "division_icon_index",
    # models — 승률 식(2.6.1 A6): 섞인 묶음은 승÷(승+패) · 한 집단은 승÷전체
    "is_mixed", "is_weak", "mixed_of_details", "mixed_of_matches", "rated", "win_pct",
    # habits — 내 경기 습관·기준선(2.6.1) · N2 등급 체류
    "DivisionStay", "DivisionStays", "division_stays",
    # habits — N19 칩 · N17 골 흐름(2.7.1)
    "GOAL_KIND_NOTE", "GOAL_KINDS", "MAX_LEAD", "Chip", "GoalFlow", "LeadRow", "goal_flow", "half_share_diff",
    "paired_diff", "pct", "side_totals", "strength_chips",
    # habits·ownerdist — 구단주 분포 판정(49단계 조각 ②) · 받기(ownerdist.run_round)는 로더가 부른다
    "DistBasis", "dist_basis", "dist_judged", "HABIT_METRICS", "N19_CHIP_KEYS", "OwnerDist", "load_owner_dist",
    "PosCell", "PosDist", "PosLevel", "load_pos_dist", "pos_level",   # C4 자리별 수준(50단계 ③)
    # habits — N16 운 범위 · A3 연승·연패 우연 기준 · N20 경기 습관(2.7.1)
    "DayLuck", "HabitBin", "HabitTable", "Luck", "RequiredRow", "StreakBaseline", "daily_luck", "habit_tables",
    "streak_baseline", "streak_key",
    # habits·rankmeta — 55 구단 머리 줄·점수 리포트(H1~H6 · R1~R3)
    "DaySummary", "FirstReach", "ScoreReport", "HistoryRun", "history_runs", "club_day", "day_delta", "decisive_rate", "first_reach", "score_report",
    "ClubHead", "club_head", "dd_band", "fc_rank_range", "rank_of",
    "RewardBand", "RewardBoard", "TopCard", "reward_board", "top3",
    "MINOR_LABEL", "is_minor_color", "minor_formations", "usage_counts",
    # habits — 55 D1 일별 기록 달력
    "CalDay", "CalSummary", "cal_summary", "calendar_days",
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
    # rankmeta — N23 상대 순위 구간(2.9.1)
    "OppBand", "OppRank", "opp_rank_rows", "opp_rank_table", "opp_rank_label",
    # rankmeta — 57 K3 상대 점수(저장 안 함) · K1 구단가치 칸
    "OppScore", "bin_of_value", "opp_scores",
    # squad_timeline · trade_book — 스쿼드 타임라인 · 이적시장 가계부(1.4.1)
    "HELD", "NEVER_PLAYED", "NOT_RECENT", "RECENT", "Timeline", "TimelineEvent", "build_timeline", "parse_trades",
    "Ledger", "ledger", "price_targets",
    # stats — 여러 경기 집계
    "END_KINDS", "MIN_BUCKET_SHOTS", "PITCH_ROWS", "PITCH_X", "PITCH_Y", "PITCH_Y_MAX", "SquadValue", "card_ovr", "pitch_rows", "squad_value", "KeyPlayers", "key_players", "PASS_KINDS", "PASS_MIN_TRIES", "PERIODS", "PLAYER_RATING_MIN_GAMES", "PLAYER_TREND_MIN_SHOTS",
    "RANKER_METRICS", "SHOT_GOAL", "SHOT_OFF_TARGET",
    "SHOT_ON_TARGET", "SUB_POSITION", "SUPER_CHAMPION_DIVISION_ID", "CHAMPION_DIVISION_ID", "Discipline", "PositionOpponent", "StreakAfter",
    "after_streak_rates", "aggregate_players", "clutch_summary", "daily_division", "discipline_stats",
    "division_entries", "division_stats", "division_trend", "finishing_ranking", "formation_of", "formation_stats",
    "goal_minute_buckets", "is_champion_or_above", "match_formations",
    "FormationUsage", "OppCard", "formation_label", "opponent_cards", "opponent_formations", "opponent_position_players", "opponent_squad", "own_squad", "pass_style", "player_finishing_trend",
    "position_line", "possession_stats", "ranker_compare", "ranker_targets", "ranker_values", "rating_trend",
    "result_breakdown", "season_divisions", "season_id_of",
    "shot_distance_breakdown", "shot_map",
    "shot_type_breakdown", "team_color_baseline", "team_color_mixed", "team_color_stats", "team_profile", "time_of_day_rates",
    "time_weekday_rates", "trade_hint", "TIME_BANDS", "TRADE_HINT_GAMES", "WEEKDAYS",
    # stats — A2 FC 보상(43단계)
    "WinDivision", "WinRewards", "reward_for", "win_divisions", "win_rewards",
    # stats — D1 구단주 비교 확장(50단계)
    "COMPARE_LINES", "COMPARE_MIN_GAMES", "CompareDiff", "LineCard", "LinePair", "compare_summary", "line_pairs",
]
