"""이용 안내·개인정보·오픈소스 고지 — 첫 실행 안내 창과 [정보] 창이 같은 글을 보여 준다.

글을 실질적으로 바꾸면(책임 범위·외부 연결 목록이 달라지면) config.NOTICE_VERSION 을 올린다 —
그래야 이미 동의한 사람에게도 다시 보인다. 맞춤법·표현만 고친 거면 올리지 않는다.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
이용 안내·개인정보·웹 데이터 고지·오픈소스 목록(`THIRD_PARTY`) — 첫 실행 `NoticeDialog` 와 [정보] `AboutDialog` 가 같은 글을 쓴다.
**글을 실질적으로 바꾸면 `config.NOTICE_VERSION` 을 올린다**(이미 동의한 사람에게 다시 보이게).
`NOTICE_BASE_VERSION` 이상 동의자는 막지 않고 창이 보일 때 다시 묻기만(지금 체크 값 그대로) — 올릴 때 "옛 동의로 계속 써도 되나"로 BASE 를 판단하고, 새 기록만 따로 막는다(`track_allowed`).
새 패키지를 묶으면 `THIRD_PARTY` 에 한 줄 — spec 이 라이선스 전문을 `_internal/licenses/` 에 넣고 `release.py` 가 빠진 걸 막는다
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
<li>넥슨 오픈API (open.api.nexon.com) — 전적·거래 기록 조회. 거래 기록은 넥슨이 <b>API 키 주인 계정</b> 것만 줍니다</li>
<li>넥슨 이미지 서버 (fco.dn.nexoncdn.co.kr · ssl.nexon.com) — 선수 얼굴·등급 배지</li>
<li>넥슨 홈페이지 (fconline.nexon.com · m.fconline.nexon.com) — 아래 '넥슨 홈페이지 데이터'를 켰을 때만</li>
<li>GitHub (api.github.com · github.com) — 새 버전 확인(켤 때, 트레이에 남아 있는 동안 6시간마다)·내려받기
· 시즌 종료일 공지(같은 응답에 실려 온다)</li>
</ul>
<p>키·전적 기록·거래 기록·ELO 기록·랭킹 수집 기록·오류 기록은 이 PC 의 <code>%LOCALAPPDATA%\\{config.DATA_DIR_NAME}</code> 에만 있습니다.
오류 기록(crash.log)에는 PC 의 폴더 경로(사용자 이름 포함)가 들어갈 수 있으니, 누구에게 보내기 전에
열어서 확인하세요.</p>
"""

WEB_DATA_HTML = f"""
<h3>넥슨 홈페이지 데이터 (선택)</h3>
<p>오픈API 에 없는 정보 — 감독모드 랭킹·구단가치, 팀컬러, 시즌 구분, 선수 능력치 — 는
넥슨 FC 온라인 홈페이지(데이터센터)를 프로그램이 <b>자동으로 읽어</b> 가져옵니다.</p>
<p>이건 넥슨이 공식으로 허락한 방법(오픈API)이 <b>아닙니다.</b> 넥슨이 언제든 막거나 문제 삼을 수
있으며, 켜는 것은 <b>사용자의 선택이고 그 책임도 사용자에게</b> 있습니다.
끄면 랭커 카드·팀컬러·시즌 구분·선수 능력치가 빈칸으로 나옵니다. 나중에 [정보] 에서 바꿀 수 있습니다.</p>
<p>켜 두면 이적시장 가계부의 평가 손익을 내려고, 보유 중인 카드의 시세를 <b>하루 최대 {config.PRICE_FETCH_MAX}장</b>
자동으로 읽습니다.</p>
<p>켜 두면 스쿼드 축구장(스쿼드 창·구단주 비교)을 열 때 그 칸에 놓인 카드의 시세·급여·OVR 을 <b>하루 최대
{config.CHIP_FETCH_MAX}장</b> 자동으로 읽어 이 PC 에 둡니다(시세는 하루, 급여·OVR 은 {config.CARD_INFO_TTL_DAYS}일 동안 다시 안 읽음).
선수 카드를 직접 열 때는 지금처럼 상한 없이 읽습니다.</p>
"""

RANK_COLLECT_HTML = f"""
<h3>랭킹 수집 (선택 · 기본 꺼짐)</h3>
<p>[정보] 에서 켜면, 앱이 켜져 있는 동안 <b>하루 한 번</b> 넥슨 홈페이지의 감독모드 랭킹 목록 500쪽(1만 명)을
<b>자동으로 읽어</b> 이 PC 에 쌓습니다 — 랭킹 추이·순위 컷 점수 같은 기능에 씁니다. 위 '넥슨 홈페이지 데이터'와
같은 방식이라 넥슨이 허락한 방법이 아니고, 켜는 것은 사용자의 선택입니다. 홈페이지 데이터를 끄면 같이 꺼지고,
다시 켜도 수집은 따로 다시 켜야 합니다. 넥슨이 요청을 계속 막으면 스스로 끕니다.</p>
<ul>
<li>다른 구단주의 닉네임·프로필 번호·순위·점수 원본은 <b>14일</b>만 두고, 순위 구간별 집계(포메이션·팀컬러 비율,
구단가치, 컷 점수)는 계속 둡니다.</li>
<li>수집을 켜지 않아도, <b>검색한 구단주의 ELO(랭킹 점수)·순위</b>를 검색할 때마다 이 PC 에 기록합니다
(홈페이지 데이터가 켜져 있을 때).</li>
<li>수집이 켜져 있으면, 승률 그래프 메뉴에서 <b>직접 고른 구단주(최대 5명)</b>의 ELO·순위를 수집 목록에서 찾아
<b>하루 한 번 이어서</b> 기록합니다 — 지울 때까지 남습니다. 기본은 아무도 고르지 않은 상태입니다.</li>
<li>수집이 켜져 있으면 지난 시즌들의 최종 순위 컷 점수(순위와 점수만)를 받아 두고, 시즌 말 순위 예측에 씁니다.
예측 결과는 나중에 맞았는지 확인할 수 있게 계정마다 하루 한 줄 이 PC 에 남깁니다.</li>
<li>수집이 켜져 있으면 <b>랭커 픽</b> 메뉴나 아래 [포지션 특성] 탭을 열어 둔 동안에만, 랭킹 상위 {config.RANKER_PICK_TOP}명의 최근 경기 하나씩을
<b>넥슨 오픈API(내 API 키)</b>로 받아 그들이 쓰는 선발 카드를 셉니다. [추천] 탭을 열었는데 내 팀컬러 랭커가
{config.RECOMMEND_MIN_RANKERS}명보다 적으면, 내 팀컬러인 {config.RANKER_RECOMMEND_TOP:,}위 안 랭커를 최대
{config.RECOMMEND_FETCH_MAX}명 더 받습니다. 둘을 합쳐 하루 최대 {config.RANKER_PICK_DAILY_REQ}번 요청
(키 한도를 다 쓰지 않게), 넥슨이 호출 한도를 알리면 그날은 멈춥니다. 받은 경기(상대 구단주 정보 포함)는
<b>14일</b>만 두고, 수집을 끄거나 아래 지우기를 하면 함께 지웁니다. 메뉴를 열지 않으면 요청하지 않습니다.</li>
<li>수집이 켜져 있으면 [메타 분석 › <b>포지션 특성</b>] 탭을 열어 둔 동안에만, 랭킹 상위 {config.TRAIT_TOP}명의 최근 경기
하나씩(상대 구단주 정보 포함)을 <b>넥슨 오픈API(내 API 키)</b>로 받습니다 — 위 랭커 픽과 <b>합쳐 하루 최대
{config.RANKER_PICK_DAILY_REQ}번</b> 그대로입니다. 그리고 그들이 카드에 넣은 포지션 특성·코치를 넥슨 홈페이지(스쿼드메이커)에서
<b>하루 최대 {config.TRAIT_WEB_DAILY_REQ:,}번</b> 읽습니다. 받은 것은 이 PC 에 <b>{config.RANK_RAW_KEEP_DAYS}일</b>만 두고, 화면에는
포메이션·포지션별 인원과 비율만(이름 없이) 보입니다. 수집을 끄거나 아래 지우기를 하면 함께 지웁니다. 넥슨이 요청을 서로 다른 날
{config.TRAIT_BLOCK_DAYS}번 이어서 막으면 특성 받기만 스스로 끕니다. 탭을 열지 않으면 요청하지 않습니다.</li>
<li>슈퍼챔피언스 연속 기간·시즌 최고점·처음 챔피언스에 닿은 판수처럼 <b>사람을 이어서 봐야 하는 값</b>은,
닉네임 없이 <b>프로필 번호만</b>으로 <b>그 시즌 동안만</b> 둡니다. 시즌이 끝나면 번호를 지우고 숫자(기간·점수·판수)만
남깁니다. 수집을 끄면 이 사람별 기록은 바로 지웁니다. 랭커 픽은 날마다 카드·팀컬러별 인원(사람 구분 없는 숫자)만 남깁니다.</li>
<li>[정보] 의 <b>수집 기록 지우기</b>로 둘 다 지울 수 있습니다. 이 기록을 밖으로 보내는 기능은 없습니다.
프로그램을 제거해도 이 PC 의 기록은 남습니다 — 지우려면 제거하기 전에 <b>수집 기록 지우기</b>를 누르세요.</li>
</ul>
<p><b>선수로 구단주 찾기</b> — 이 PC 에 저장된 경기 기록(검색한 계정과 그 상대 · 위 랭커 픽)으로 "카드 → 그 카드를 쓴
구단주" 색인을 만듭니다. 새로 요청하지 않고, 밖으로 보내지 않으며, 경기 기록과 같이 지워집니다.</p>
"""

TRAY_HTML = """
<h3>트레이 상주 · 자동 실행</h3>
<ul>
<li>랭킹 수집이 켜져 있거나 자동 실행을 켜 두면, 창을 닫아도(X) 끝나지 않고 작업 표시줄 오른쪽 <b>트레이</b>에 남아
수집을 이어 갑니다. 완전히 끄려면 트레이 아이콘을 오른쪽 클릭해 <b>[종료]</b>. 둘 다 꺼져 있으면 X 가 그대로 끕니다.</li>
<li>창을 닫고 30분이 지나면 경기 기록을 메모리에서 내려놓습니다 — 다시 열면 저장된 기록을 다시 읽습니다.</li>
<li><b>자동 실행</b>(선택 · 기본 꺼짐) — [정보] 에서 켜면 윈도우 시작 프로그램에 등록돼, 윈도우를 켤 때 창 없이
트레이로 시작합니다. 끄거나 프로그램을 제거하면 등록을 지웁니다.</li>
<li>트레이에 있는 동안 윈도우 알림은 띄우지 않습니다 — 새 버전·수집 상태·오류는 창을 열면 보입니다.</li>
</ul>
"""

# 이미 동의한 사람에게 다시 물을 때 맨 위에 — 무엇이 바뀌었는지만(전문은 아래 그대로). 버전마다 한 덩이 —
# 동의한 버전 뒤의 것만 보인다(5 이하 동의자는 6·7 둘 다). 안내를 올리면 여기 한 덩이를 더한다.
CHANGES = {
    6: """
<p>랭킹 수집이 켜져 있으면, 슈퍼챔피언스 연속 기간·시즌 최고점·처음 챔피언스에 닿은 판수를 재려고 <b>다른 구단주의
프로필 번호(닉네임 없이)와 그 값</b>을 <b>그 시즌 동안</b> 이 PC 에 둡니다. 지금까지는 14일 지나면 지웠습니다.
시즌이 끝나면 번호를 지우고 숫자만 남기며, 수집을 끄면 바로 지웁니다. 동의하기 전에는 이 기록을 쓰지 않습니다
(아래 '랭킹 수집'). 새로 요청하는 것은 없습니다.</p>
<p>랭커 픽은 날마다 카드·팀컬러별 인원(사람 구분 없는 숫자)을 남깁니다. 프로그램을 제거해도 이 PC 의 기록은 남으니,
지우려면 제거하기 전에 [정보] 의 <b>수집 기록 지우기</b>를 누르세요.</p>
""",
    7: f"""
<p><b>포지션 특성</b> — 랭킹 수집이 켜져 있으면, [메타 분석 › 포지션 특성] 탭을 열어 둔 동안에만 랭킹 상위
{config.TRAIT_TOP}명이 카드에 넣은 포지션 특성·코치를 넥슨 홈페이지(스쿼드메이커)에서 하루 최대 {config.TRAIT_WEB_DAILY_REQ:,}번 읽고,
그들의 최근 경기를 오픈API 로 받습니다(랭커 픽과 합쳐 하루 {config.RANKER_PICK_DAILY_REQ}번 그대로). 이 PC 에
{config.RANK_RAW_KEEP_DAYS}일만 두고 화면에는 인원·비율만 보입니다. 동의하기 전에는 받지 않습니다(아래 '랭킹 수집').</p>
""",
}


def changes_html(accepted: int) -> str:
    """동의한 버전(accepted) 뒤에 바뀐 점만 — 오래된 것부터."""
    parts = [CHANGES[v] for v in sorted(CHANGES) if v > accepted]
    if not parts:
        return ""
    return ("<h3>이번에 바뀐 점</h3>" + "".join(parts)
            + "<p>홈페이지 데이터 선택은 지금 값 그대로 두었습니다.</p>")


WEB_DATA_CHECK = "넥슨 홈페이지 데이터 읽기 켜기 (선택)"
WEB_DATA_ON_NOW = "지금 켜져 있음 — 계속 쓰려면 체크"   # 안내 글이 바뀌어 다시 동의받을 때(D5)
RANK_COLLECT_CHECK = "랭킹 수집 켜기 — 하루 한 번 랭킹 1만 명 (선택)"
AUTOSTART_CHECK = "윈도우를 켤 때 트레이로 자동 실행 (선택)"
AGREE_CHECK ="위 내용을 읽었고 동의합니다"

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
