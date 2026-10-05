"""화면 ↔ 분석 경계 — 화면 쪽 파일(app_main·dashboard·check_api)은 계산을 여기서만 가져온다.

나중에 핵심 알고리즘을 비공개 모듈로 옮길 때(docs/ROADMAP.md "배포 보호 준비") 화면에서 고칠 곳이
이 파일 하나가 되게 하려는 것이다. 화면에서 새 분석 함수를 쓰려면 여기 import 와 __all__ 에 한 줄씩.
화면 파일이 analysis·stats·models·predict 를 직접 import 하면 test_parsing 의 경계 테스트가 빨개진다.

⚠ 이름을 복사해 오므로 analysis.narrate 를 바꿔 끼워도 화면에는 안 닿는다 — 테스트는 core_api 쪽을 바꾼다.
"""
from __future__ import annotations

from analysis import (
    MIN_COND, MIN_OPP, MIN_PLAYER_GAMES, SEC_FLOW, SEC_LOSE, SEC_WIN, SECTIONS, STREAK_MAX, WINDOW,
    Basis, narrate, streak_min_n,
)
from models import (
    MatchSummary, current_streak, longest_streaks, moving_win_rate, opponent_stats, parse_match, period_stats,
    summarize, win_rate_trend,
)
from predict import Prediction, describe as describe_prediction, predict_for
from stats import (
    END_KINDS, MIN_BUCKET_SHOTS, PERIODS, PLAYER_TREND_MIN_SHOTS, SHOT_GOAL, SHOT_OFF_TARGET, SHOT_ON_TARGET,
    SUPER_CHAMPION_DIVISION_ID,
    Discipline, PositionOpponent, StreakAfter, after_streak_rates, aggregate_players, clutch_summary,
    daily_division, discipline_stats, division_entries, division_stats, division_trend, finishing_ranking,
    formation_of, formation_stats, goal_minute_buckets, is_champion_or_above,
    opponent_position_players, opponent_squad, own_squad, player_finishing_trend,
    position_line, possession_stats, result_breakdown, season_divisions, season_id_of, shot_distance_breakdown, shot_map,
    shot_type_breakdown, team_color_stats, team_profile, time_of_day_rates, time_weekday_rates,
    TIME_BANDS, WEEKDAYS,
)

__all__ = [
    # analysis — 집계 → 문장
    "MIN_COND", "MIN_OPP", "MIN_PLAYER_GAMES", "SEC_FLOW", "SEC_LOSE", "SEC_WIN", "SECTIONS", "STREAK_MAX", "WINDOW",
    "Basis", "narrate", "streak_min_n",
    # models — 경기 파싱 · 한 계정 요약
    "MatchSummary", "current_streak", "longest_streaks", "moving_win_rate", "opponent_stats", "parse_match",
    "period_stats", "summarize", "win_rate_trend",
    # predict — 시즌 말 순위 예측(21단계에서 비공개로 옮길 1순위)
    "Prediction", "describe_prediction", "predict_for",
    # stats — 여러 경기 집계
    "END_KINDS", "MIN_BUCKET_SHOTS", "PERIODS", "PLAYER_TREND_MIN_SHOTS", "SHOT_GOAL", "SHOT_OFF_TARGET",
    "SHOT_ON_TARGET", "SUPER_CHAMPION_DIVISION_ID", "Discipline", "PositionOpponent", "StreakAfter",
    "after_streak_rates", "aggregate_players", "clutch_summary", "daily_division", "discipline_stats",
    "division_entries", "division_stats", "division_trend", "finishing_ranking", "formation_of", "formation_stats",
    "goal_minute_buckets", "is_champion_or_above",
    "opponent_position_players", "opponent_squad", "own_squad", "player_finishing_trend",
    "position_line", "possession_stats", "result_breakdown", "season_divisions", "season_id_of",
    "shot_distance_breakdown", "shot_map",
    "shot_type_breakdown", "team_color_stats", "team_profile", "time_of_day_rates",
    "time_weekday_rates", "TIME_BANDS", "WEEKDAYS",
]
