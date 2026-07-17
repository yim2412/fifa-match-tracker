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
| `app_main.py` | PyQt6 UI — 검색 바, 요약 패널, 탭 3개(전적/선수 지표/전술·경기 결과), 조회 워커 스레드 |
| `nexon_api.py` | 넥슨 오픈API 클라이언트. **엔드포인트 경로·에러코드가 전부 여기 상수에 모여 있다** |
| `models.py` | 매치 상세 JSON → `MatchSummary` 파싱, `Stats` 집계 |
| `stats.py` | 여러 경기 집계 — 선수 지표·전술·경기 결과. **역산해서 알아낸 상수가 여기 모여 있다** |
| `store.py` | 조회한 경기를 SQLite(`fifa.db`)에 누적 — API 100경기 한계 극복 |
| `config.py` | API 키 로드(.env), DB 경로, 매치 종류·조회 개수 기본값 |
| `check_api.py` | 터미널에서 키·엔드포인트·집계·DB 동작 확인용 |

기본 매치 종류는 **감독모드(52)** 다 — 이 앱은 감독모드 전적을 본다.

## 누적 저장 (`fifa.db`)

**API는 최근 100경기까지만 준다.** 이 계정은 21시간에 100경기가 쌓여서(시간당 약 5경기),
하루만 조회를 걸러도 그 사이 경기는 **영영 못 가져온다** — 과거를 조회할 수단이 API에 없다.

그래서 조회할 때마다 `fifa.db`에 쌓고, 화면은 API가 아니라 DB를 그린다.
검색하는 순간이 곧 수집이라, 자주 보는 계정일수록 데이터가 길어진다.

- **저장 기준은 `ouid`** — 구단주명은 바뀌어도 ouid는 안 바뀐다
- **경기 원본은 한 번만** — 한 경기에 두 명이 나오므로 `matches` / `match_players`로 분리.
  상대를 검색하면 이미 받아 둔 경기가 재활용된다
- **이미 있는 경기는 API를 다시 부르지 않는다** (`store.existing_ids`)
- **등록 계정** — 조회하면 자동 등록. 목록에서 고르면 바로 조회. 등록 해제해도 경기 기록은 남는다

> 한계: **과거는 못 되살린다.** 처음 조회 시점의 최근 100경기가 시작점이고, 그 이전은 없다.
> 조회 간격이 100경기(≈20시간)보다 길면 그 사이가 뚫린다.

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

### 응답에서 역산해 알아낸 것 (공식 문서에 없음)

실제 응답 100경기로 확인한 내용. 상수는 `stats.py` 상단에 있다.

| 항목 | 내용 | 어떻게 확인했나 |
|------|------|-----------------|
| `shoot.goalTotal` 등이 **null** | 상대 탈주 등 기록 없는 경기는 키를 빼지 않고 `null`로 준다. `.get(k, 0)`은 기본값이 안 먹으니 타입까지 확인해야 한다(`models._i`) | 30경기 조회 시 `TypeError`로 재현 |
| `shootDetail[].result` | `3` = 골 | `result==3` 개수가 `goalTotal` 합계와 정확히 일치(126/126) |
| `shootDetail[].goalTime` | 비트 패킹. `>>24` = 구간(0 전반/1 후반/2 연장전반/3 연장후반), `& 0xFFFFFF` = 경과 초 | 구간 0·1은 최대 49.8분(45+추가), 2·3은 19.1분(15+추가)으로 딱 맞음 |
| `shootDetail[].type` | 슛 유형. 1 일반(D) · 2 감아차기(ZD) · 3 헤더 · 6 땅볼(DD) · 7 발리 · 8 프리킥 · 9 페널티킥 | 8·9는 `goalFreekick`/`goalPenaltyKick` 집계와 200개 선수-경기 행에서 경기별 정확히 일치. 나머지는 외부 전적 사이트의 유형별 골 수와 합계 일치 |
| `type` 4 · 10 · 13 · 14 | **미상.** "알 수 없음"으로 표시 | 근거 없음. 참고한 사이트도 못 읽는다 |
| `shoot.shootOutScore` | 승부차기 점수. 승부차기가 없었으면 양쪽 0 | 2:2 무승부인데 승/패가 갈린 경기에서만 0이 아님 |
| `spPosition` | `28` = SUB(교체 명단). 전술은 GK·SUB 제외하고 수비(1-8)-수미(9-11)-미드(12-16)-공미(17-19)-공격(20-27) 인원 | 상대 전술별 승률이 외부 사이트와 일치(4-1-2-3-0 → 51.7%, 31승3무26패) |

> 매치 상세는 **경기별 실제 스쿼드**를 준다(현재 스쿼드를 돌려주는 게 아니다).
> 볼타 66경기에서 서로 다른 스쿼드 조합 10개가 나오는 것으로 확인.

## 앞으로

- [x] 선수별 통계 — `stats.aggregate_players` (선수 이미지는 아직 없음)
- [x] 상대 전술별 승률 — `stats.formation_stats`
- [ ] 공격력·수비력·기대득점률 같은 파생 지표 — **API가 주는 값이 아니라 직접 공식을 만들어야 한다.**
      지금은 실측치만 보여준다
- [ ] 상대 구단주별 상성 분석
- [ ] 기간별 승률 추이 차트
- [x] 조회 결과 로컬 DB(SQLite) 누적 — `store.py`
- [ ] `collect.py` + 작업 스케줄러 — 앱을 안 켜도 6시간마다 수집.
      **조회 간격이 20시간을 넘으면 그 사이가 뚫리므로 필요하다**
- [ ] 선수 이미지 (넥슨 CDN)
- [ ] exe 빌드 (PyInstaller)
