"""이용 안내·개인정보·오픈소스 고지 — 첫 실행 안내 창과 [정보] 창이 같은 글을 보여 준다.

글을 실질적으로 바꾸면(책임 범위·외부 연결 목록이 달라지면) config.NOTICE_VERSION 을 올린다 —
그래야 이미 동의한 사람에게도 다시 보인다. 맞춤법·표현만 고친 거면 올리지 않는다.
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import config

# 상표 고지 — 화면 이름에서 FIFA·FC ONLINE 을 뺀 것과 짝이다(2026-10-02 결정).
UNOFFICIAL = ("넥슨(NEXON)·EA 와 관련 없는 비공식 프로그램입니다. "
              "FC 온라인·EA SPORTS FC 는 각 사의 상표입니다.")

TERMS_HTML = f"""
<h3>이용 안내</h3>
<ul>
<li>개인이 만든 {UNOFFICIAL} 넥슨·EA 가 보증하거나 지원하지 않습니다.</li>
<li><b>무료</b>이며 <b>있는 그대로</b> 제공됩니다. 화면의 수치(공식에 없는 값을 계산한 지표 포함)는
틀릴 수 있고, 이 프로그램을 써서 생긴 손해에 대해 만든 사람은 책임지지 않습니다.</li>
<li>전적은 <b>넥슨 오픈API</b> 로 받습니다. 키는 본인이 넥슨에서 발급받아 넣고, 키 관리와
넥슨 오픈API 이용약관을 지키는 것은 <b>키를 발급받은 본인</b>의 몫입니다. 키를 남에게 주지 마세요.</li>
<li>조회한 결과를 다른 곳에 올리거나 남에게 넘길 때도 넥슨 오픈API 이용약관을 따라야 합니다.</li>
</ul>
"""

PRIVACY_HTML = f"""
<h3>개인정보</h3>
<p>만든 사람은 <b>아무것도 수집하지 않습니다.</b> 개발자 서버도, 사용 통계도 없습니다.
이 PC 에서 나가는 연결은 아래뿐입니다.</p>
<ul>
<li>넥슨 오픈API (open.api.nexon.com) — 전적 조회</li>
<li>넥슨 이미지 서버 (fco.dn.nexoncdn.co.kr · ssl.nexon.com) — 선수 얼굴·등급 배지</li>
<li>넥슨 홈페이지 (fconline.nexon.com · m.fconline.nexon.com) — 아래 '넥슨 홈페이지 데이터'를 켰을 때만</li>
<li>GitHub (api.github.com · github.com) — 새 버전 확인·내려받기</li>
</ul>
<p>키·전적 기록·오류 기록은 이 PC 의 <code>%LOCALAPPDATA%\\{config.DATA_DIR_NAME}</code> 에만 있습니다.
오류 기록(crash.log)에는 PC 의 폴더 경로(사용자 이름 포함)가 들어갈 수 있으니, 누구에게 보내기 전에
열어서 확인하세요.</p>
"""

WEB_DATA_HTML = """
<h3>넥슨 홈페이지 데이터 (선택)</h3>
<p>오픈API 에 없는 정보 — 감독모드 랭킹·구단가치, 팀컬러, 시즌 구분, 선수 능력치 — 는
넥슨 FC 온라인 홈페이지(데이터센터)를 프로그램이 <b>자동으로 읽어</b> 가져옵니다.</p>
<p>이건 넥슨이 공식으로 허락한 방법(오픈API)이 <b>아닙니다.</b> 넥슨이 언제든 막거나 문제 삼을 수
있으며, 켜는 것은 <b>사용자의 선택이고 그 책임도 사용자에게</b> 있습니다.
끄면 랭커 카드·팀컬러·시즌 구분·선수 능력치가 빈칸으로 나옵니다. 나중에 [정보] 에서 바꿀 수 있습니다.</p>
"""

WEB_DATA_CHECK = "넥슨 홈페이지 데이터 읽기 켜기 (선택)"
AGREE_CHECK = "위 내용을 읽었고 동의합니다"

# ── 오픈소스 고지 ─────────────────────────────────────────────────────
# 배포물에 실제로 들어가는 것. (이름, 라이선스, 배포 이름 — 라이선스 파일을 찾을 때 쓴다)
# 새 패키지를 쓰기 시작하면 여기 한 줄 — tests/test_release.py 가 설치된 라이선스 파일과 대조한다.
THIRD_PARTY = [
    ("PyQt6", "GPL v3", "PyQt6"),
    ("Qt 6", "LGPL v3", "PyQt6-Qt6"),
    ("PyQt6-sip", "BSD-2-Clause", "PyQt6_sip"),
    ("requests", "Apache-2.0", "requests"),
    ("urllib3", "MIT", "urllib3"),
    ("idna", "BSD-3-Clause", "idna"),
    ("charset-normalizer", "MIT", "charset-normalizer"),
    ("certifi", "MPL-2.0", "certifi"),
    ("python-dotenv", "BSD-3-Clause", "python-dotenv"),
    ("setuptools", "MIT", "setuptools"),  # PyInstaller 가 같이 묶는다
    ("orjson", "MPL-2.0 AND (Apache-2.0 OR MIT)", "orjson"),  # 경기 기록 해석(store._loads)
    ("Python", "PSF License", None),       # OpenSSL·SQLite·libffi 등 동봉 라이브러리 고지가 이 파일 안에 있다
]
LICENSE_DIR = "licenses"  # 배포물 안(_internal\licenses\<이름>\…)


def license_files() -> list[tuple[str, Path]]:
    """(고지 이름, 라이선스 파일) — spec 이 배포물에 넣고, 테스트가 빠진 게 없는지 본다."""
    out = []
    for name, _lic, dist in THIRD_PARTY:
        if dist is None:
            p = Path(sys.base_prefix) / "LICENSE.txt"
            if p.is_file():
                out.append((name, p))
            continue
        try:
            d = metadata.distribution(dist)
        except metadata.PackageNotFoundError:
            continue
        for f in d.files or []:
            up = f.name.upper()
            if up.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                out.append((name, Path(d.locate_file(f))))
    return out


def licenses_html() -> str:
    rows = "".join(f"<li>{n} — {lic}</li>" for n, lic, _ in THIRD_PARTY)
    return f"""
<h3>오픈소스 라이선스</h3>
<p>이 프로그램의 소스 코드는 <b>MIT</b> 라이선스입니다
(<a href="{config.REPO_URL}">{config.REPO_URL}</a>).<br>
배포하는 실행 파일은 <b>GPL v3</b> 인 PyQt6 를 포함하므로, <b>실행 파일 전체는 GPL v3 조건</b>으로
배포됩니다 — 소스는 위 주소에서 누구나 받을 수 있습니다.</p>
<p>포함된 오픈소스:</p>
<ul>{rows}</ul>
<p>라이선스 전문은 프로그램 폴더의 <code>_internal\\{LICENSE_DIR}</code> 에 있습니다.</p>
"""
