# 피파 전적관리

넥슨 오픈API로 EA SPORTS FC 온라인 전적을 조회·집계하는 PyQt6 데스크톱 앱.

## 시작하기

```powershell
cd C:\Users\준\Desktop\피파전적관리

# 1) 패키지 설치
pip install -r requirements.txt

# 2) API 키 설정 — .env.example 을 복사해 .env 로 만들고 키를 넣는다
copy .env.example .env
notepad .env

# 3) API 연결 점검 (GUI 전에 먼저)
python check_api.py 내닉네임

# 4) 앱 실행
python app_main.py
```

## 파일 구조

| 파일 | 역할 |
|------|------|
| `app_main.py` | PyQt6 UI — 검색 바, 요약 패널, 전적 표, 조회 워커 스레드 |
| `nexon_api.py` | 넥슨 오픈API 클라이언트. **엔드포인트 경로·에러코드가 전부 여기 상수에 모여 있다** |
| `models.py` | 매치 상세 JSON → `MatchSummary` 파싱, `Stats` 집계 |
| `config.py` | API 키 로드(.env), 매치 종류·조회 개수 기본값 |
| `check_api.py` | 터미널에서 키·엔드포인트 동작 확인용 |

## API 메모

- 호스트: `https://open.api.nexon.com`
- 인증: 요청 헤더 `x-nxopen-api-key`
- 키 발급: [NEXON Open API](https://openapi.nexon.com/) 로그인 → 마이페이지

사용 중인 엔드포인트 (`nexon_api.py` 상단 상수):

| 상수 | 경로 | 용도 |
|------|------|------|
| `EP_ID` | `/fconline/v1/id` | 닉네임 → ouid |
| `EP_USER_BASIC` | `/fconline/v1/user/basic` | 계정 기본 정보 |
| `EP_MAX_DIVISION` | `/fconline/v1/user/maxdivision` | 역대 최고 등급 |
| `EP_USER_MATCH` | `/fconline/v1/user/match` | 매치 id 목록 |
| `EP_MATCH_DETAIL` | `/fconline/v1/match-detail` | 매치 상세 |

메타데이터(매치 종류·선수·시즌·등급)는 인증 없이 `https://open.api.nexon.com/static/fconline/meta/{name}.json`.

> 공식 문서가 자바스크립트로 렌더링돼 경로를 자동 대조하지 못했다.
> `check_api.py`가 FAIL이면 [공식 문서](https://openapi.nexon.com/ko/game/fconline/)와 대조해
> `nexon_api.py` 상단 상수만 고치면 된다.

## 앞으로

- [ ] 선수별 통계 (spid 메타 + 선수 이미지)
- [ ] 상대별 상성 분석
- [ ] 기간별 승률 추이 차트
- [ ] 조회 결과 로컬 DB(SQLite) 누적 — API 상한(최근 100경기) 너머 장기 추적
- [ ] exe 빌드 (PyInstaller)
