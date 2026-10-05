"""화면 ↔ 분석 경계 — 화면 쪽 파일(app_main·dashboard·check_api)은 계산을 여기서만 가져온다.

나중에 핵심 알고리즘을 비공개 모듈로 옮길 때(docs/ROADMAP.md "배포 보호 준비") 화면에서 고칠 곳이
이 파일 하나가 되게 하려는 것이다. 화면에서 새 분석 함수를 쓰려면 여기 import 와 __all__ 에 한 줄씩.
화면 파일이 analysis·stats·models 를 직접 import 하면 test_parsing 의 경계 테스트가 빨개진다.

⚠ 이름을 복사해 오므로 analysis.narrate 를 바꿔 끼워도 화면에는 안 닿는다 — 테스트는 core_api 쪽을 바꾼다.
"""
from __future__ import annotations

from analysis import (
    MIN_COND, MIN_OPP, MIN_PLAYER_GAMES, SEC_FLOW, SEC_LOSE, SEC_WIN, SECTIONS, WINDOW, narrate,
)
from models import (
    MatchSummary, current_streak, longest_streaks, opponent_stats, parse_match, period_stats,
    summarize, win_rate_trend,
)
from stats import (
    MIN_BUCKET_SHOTS, PERIODS, PLAYER_TREND_MIN_SHOTS, SHOT_GOAL, SHOT_OFF_TARGET, SHOT_ON_TARGET,
    PositionOpponent, aggregate_players, clutch_summary, division_stats, division_trend,
    finishing_ranking, formation_of, formation_stats, goal_minute_buckets, is_champion_or_above,
    opponent_position_players, opponent_squad, own_squad, player_finishing_trend,
    possession_stats, result_breakdown, season_id_of, shot_distance_breakdown, shot_map,
    shot_type_breakdown, team_color_stats, team_profile, time_of_day_rates,
)

__all__ = [
    # analysis — 집계 → 문장
    "MIN_COND", "MIN_OPP", "MIN_PLAYER_GAMES", "SEC_FLOW", "SEC_LOSE", "SEC_WIN", "SECTIONS", "WINDOW", "narrate",
    # models — 경기 파싱 · 한 계정 요약
    "MatchSummary", "current_streak", "longest_streaks", "opponent_stats", "parse_match", "period_stats",
    "summarize", "win_rate_trend",
    # stats — 여러 경기 집계
    "MIN_BUCKET_SHOTS", "PERIODS", "PLAYER_TREND_MIN_SHOTS", "SHOT_GOAL", "SHOT_OFF_TARGET", "SHOT_ON_TARGET",
    "PositionOpponent", "aggregate_players", "clutch_summary", "division_stats", "division_trend",
    "finishing_ranking", "formation_of", "formation_stats", "goal_minute_buckets", "is_champion_or_above",
    "opponent_position_players", "opponent_squad", "own_squad", "player_finishing_trend",
    "possession_stats", "result_breakdown", "season_id_of", "shot_distance_breakdown", "shot_map",
    "shot_type_breakdown", "team_color_stats", "team_profile", "time_of_day_rates",
]
