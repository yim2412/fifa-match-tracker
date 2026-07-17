"""앱 설정 — API 키 로드와 상수."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "피파 전적관리"
APP_VERSION = "v0.1.0"


def _root() -> Path:
    """개발 중에는 소스 폴더, exe로 묶이면 exe 옆 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _root()
CACHE_DIR = ROOT / ".cache"

load_dotenv(ROOT / ".env")
API_KEY = os.getenv("NEXON_API_KEY", "").strip()

# 매치 종류. 정식 목록은 메타데이터 matchtype.json 으로 받아오고, 이건 폴백·기본값용.
DEFAULT_MATCH_TYPE = 52  # 감독모드 — 이 앱은 감독모드 전적만 집계한다
FALLBACK_MATCH_TYPES = [
    (50, "공식경기"),
    (52, "감독모드"),
    (40, "볼타"),
    (60, "친선경기"),
]

DEFAULT_MATCH_LIMIT = 20  # 한 번 조회할 최근 경기 수
MAX_MATCH_LIMIT = 100     # 넥슨 API가 한 번에 주는 상한
