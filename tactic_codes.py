"""팀 전술·개인 전술 이름표(110 ④) — 스쿼드메이커 응답의 숫자·코드 → 게임 화면의 이름. 요청 없음.

| 함수 | 돌려주는 것 |
|---|---|
| `team_value(key, v)` | 팀 전술 값 하나의 글자 — 이름(수비 스타일·빌드업·기회 만들기·성향) 또는 게임 화면의 숫자(폭·깊이·박스·코너킥·프리킥) |
| `team_bar(key, v)` | 막대로 그리는 값의 (화면 값, 최대) — 이름 값이거나 범위 밖이면 None |
| `player_value(role, code, level)` | 개인 전술 하나의 이름 |
| `code_of(raw)` | 응답의 코드 글자 → 앞 글자만("AR2" → "AR") |
| `POSITION_ROLE` | 경기의 `spPosition` → 스쿼드메이커 `role` |

모르는 값은 예외 없이 "코드 AR 5" 꼴입니다(`is_unknown`). `check_api` 가 그 수를 셉니다.

**출처(2026-11-24 스쿼드메이커 번들 `app.QCQYFrF3.js` — 이름이 해시라 실행 때 읽지 않고 옮겼습니다)**: 수비 스타일 `aR` · 빌드업 `oR` ·
기회 만들기 `sR` · 성향 `cR` · 자리마다 개인 전술 `mR`. 번들은 `DATA_DIR/measure_110/` 에 남겼고(이 PC), `tools/measure_110.py --app` 가
이 표와 대조합니다. 넥슨이 값을 더하면 번들을 다시 받아 옮깁니다.

⚠ **응답의 폭·깊이·박스·코너킥·프리킥은 0부터이고 화면은 1부터입니다**(번들 `yV` 가 남의 스쿼드를 불러올 때 여섯 키에 1을 더합니다).
수비 스타일·빌드업·기회 만들기·성향은 그대로 씁니다. 개인 전술의 단계 0 은 기본값이라 키가 없는 것과 같습니다.
"""
from __future__ import annotations

import re

DEFENSE_STYLES = ("후퇴", "밸런스", "볼 터치 실수 시 압박", "공 뺏긴 직후 압박", "지속적인 압박")
BUILDUP_STYLES = ("짧은 패스", "밸런스", "긴 패스", "빠른 빌드업")
MENTALITIES = ("전원수비", "매우 수비적", "수비적", "보통", "공격적", "매우 공격적", "전원공격")

TEAM_NAMES = {"defensivestyle": DEFENSE_STYLES, "offensive_style_buildup": BUILDUP_STYLES,
              "offensive_style_chancecreation": BUILDUP_STYLES, "teammentality": MENTALITIES}
TEAM_BARS = {"defwidth": 10, "defdepth": 10, "offwidth": 10, "offplayerinbox": 10, "offcornerkick": 5, "offfreekick": 5}
TEAM_KEYS = ("defensivestyle", "defwidth", "defdepth", "offensive_style_buildup", "offensive_style_chancecreation",
             "offwidth", "offplayerinbox", "offcornerkick", "offfreekick", "teammentality")

_INTERCEPT = ("일반적인 차단", "적극적 차단", "소극적 차단")
_CROSS = ("균형잡힌 크로스 지원", "페널티 박스 안으로 침투", "측면 대기")
_SUPPORT = ("기본 수비 지원", "항상 수비 지원", "전방에 대기")
_HOLD = ("균형잡힌 공격", "공격 시 후방 대기", "공격 가담", "수비수 사이에 위치")
_COVER = ("사이드 커버", "센터 커버")
_CENTER_BACK = {"AS": ("공격 시 후방 대기", "공격 가담", "공격수로 플레이"), "IN": _INTERCEPT}
_FULL_BACK = {"AR": ("균형잡힌 공격", "공격 가담", "공격 시 후방 대기"), "IN": _INTERCEPT,
              "SR": ("균형잡힌 침투 지원", "중앙 지원", "오버랩")}
_DEF_MID = {"AS": _HOLD, "DP": _COVER, "DB": ("균형잡힌 수비", "패스 차단", "대인 수비"), "IN": _INTERCEPT}
_MID = {"AS": _HOLD, "DP": _COVER, "IN": _INTERCEPT, "PF": ("포지션 위치 유지", "자유 역할"), "SC": _CROSS}
_SIDE_MID = {"CC": ("균형잡힌 찬스 만들기", "수비수 돌파", "측면에 위치"), "DS": _SUPPORT, "IN": _INTERCEPT,
             "PF": ("포지션 위치 유지", "자유 역할"), "SC": _CROSS, "SR": ("균형잡힌 침투 지원", "뒤에서 침투", "동료 가까이")}
_ATT_MID = {"DS": _SUPPORT, "IN": _INTERCEPT, "PF": ("포지션 위치 유지", "자유 역할", "넓은 지역으로"), "SC": _CROSS}
_FORWARD = {"AR": ("균형잡힌 공격", "뒤에서 침투", "타겟맨", "가짜 공격수"), "DP": _SUPPORT, "IN": _INTERCEPT,
            "SR": ("균형잡힌 침투 지원", "넓은 지역으로", "중앙에 위치")}
_WINGER = {"CC": ("균형잡힌 찬스 만들기", "수비수 돌파", "측면에 위치", "자유 역할"), "DS": _SUPPORT, "IN": _INTERCEPT,
           "SC": _CROSS, "SR": ("균형잡힌 침투 지원", "뒤에서 침투", "동료 가까이", "타겟맨")}

PLAYER_NAMES: dict[str, dict[str, tuple[str, ...]]] = {"gk": {}}
for _roles, _table in ((("sw", "rcb", "cb", "lcb"), _CENTER_BACK), (("rwb", "rb", "lb", "lwb"), _FULL_BACK),
                       (("rdm", "cdm", "ldm"), _DEF_MID), (("rcm", "cm", "lcm"), _MID), (("rm", "lm"), _SIDE_MID),
                       (("ram", "cam", "lam"), _ATT_MID), (("rf", "cf", "lf", "rs", "st", "ls"), _FORWARD),
                       (("rw", "lw"), _WINGER)):
    for _r in _roles:
        PLAYER_NAMES[_r] = _table

POSITION_ROLE = {0: "gk", 1: "sw", 2: "rwb", 3: "rb", 4: "rcb", 5: "cb", 6: "lcb", 7: "lb", 8: "lwb", 9: "rdm", 10: "cdm",
                 11: "ldm", 12: "rm", 13: "rcm", 14: "cm", 15: "lcm", 16: "lm", 17: "ram", 18: "cam", 19: "lam", 20: "rf",
                 21: "cf", 22: "lf", 23: "rw", 24: "rs", 25: "st", 26: "ls", 27: "lw"}

_UNKNOWN = "코드 "


def is_unknown(text: str) -> bool:
    return text.startswith(_UNKNOWN)


def code_of(raw) -> str:
    """"AR2" → "AR". 글자로 시작하지 않으면 온 그대로(번들 `gR` 은 빈 글자 — 우리는 모르는 값으로 보입니다)."""
    s = str(raw).strip().upper()
    m = re.match(r"[A-Z]+", s)
    return m.group() if m else s


def team_bar(key: str, v: int) -> tuple[int, int] | None:
    top = TEAM_BARS.get(key)
    return (v + 1, top) if top is not None and 0 <= v < top else None


def team_value(key: str, v: int) -> str:
    bar = team_bar(key, v)
    if bar is not None:
        return str(bar[0])
    names = TEAM_NAMES.get(key, ())
    return names[v] if 0 <= v < len(names) else f"{_UNKNOWN}{v}"


def player_value(role: str, code: str, level: int) -> str:
    c = code_of(code)
    names = PLAYER_NAMES.get(role, {}).get(c, ())
    return names[level] if 0 <= level < len(names) else f"{_UNKNOWN}{c} {level}"
