# 피파 전적관리 — 프로젝트 규칙

> **공통 규칙(보고·작업 방식·검증·Git·문서·상수 위치·외부 API·Windows/Qt 앱·YAGNI)은
> 전역 `~/.claude/CLAUDE.md` 에 있다.** 여기에는 **이 앱에서 실제로 당한 것**만 적는다.

## 프로젝트 개요

넥슨 오픈API로 EA SPORTS FC 온라인 전적을 조회·집계하는 PyQt6 데스크톱 앱.

| 파일 | 역할 |
|------|------|
| `app_main.py` | PyQt6 UI — 검색 바, 요약 패널, 전적 표, 조회 워커 스레드(`MatchLoader`) |
| `nexon_api.py` | 넥슨 오픈API 클라이언트(`FCOnlineAPI`). **엔드포인트 경로·에러코드 상수가 전부 여기 상단에** |
| `models.py` | 매치 상세 JSON → `MatchSummary` 파싱, `Stats`·상대 전적·승률 추이 집계 |
| `stats.py` | 여러 경기 집계 — 선수 지표·전술·경기 결과. 역산 상수가 여기 모여 있다 |
| `analysis.py` | 집계 → 문장(`narrate`). **임계값·최소 표본 상수가 전부 여기 상단에.** 표본 미달이면 침묵 |
| `widgets.py` | 화면 부품 — 랭커 카드, 표, 축구장 스쿼드 배치(`PitchWidget`) 등 |
| `images.py` | 선수 얼굴·등급 배지·시즌 아이콘 — 넥슨 CDN/메타 기반, 디스크 캐시 |
| `ranker.py` | 넥슨 데이터센터 HTML 스크래핑(감독모드 순위 — 오픈API엔 없음) |
| `config.py` | `.env`에서 API 키 로드, 매치 종류·조회 개수 기본값 |
| `check_api.py` | 터미널 연결 점검 — GUI 띄우기 전 키·엔드포인트 확인용 |

```powershell
python check_api.py <닉네임>   # API 점검
python app_main.py             # 앱 실행
```

필수 패키지: `pip install -r requirements.txt` (PyQt6, requests, python-dotenv)

---

## 넥슨 API — 이 앱의 배선과 함정

> 외부 API 앱의 일반 규칙(캐싱 우선 · `.get()` 방어 · 에러는 사람 말로 · 터미널 스모크
> 유지)과 비밀 취급은 전역 규칙 7·4번. 아래는 **이 앱에만 해당하는 것**이다.

- **키는 `.env` 에만. 코드에서는 `config.API_KEY` 로만 읽는다.** 견본은 `.env.example` 로
  두고 실제 키 대신 안내 문구만 넣는다.
- **엔드포인트 경로 상수는 `nexon_api.py` 상단에.** 여기서 특히 중요한 이유는
  **공식 문서가 JS 렌더링이라 자동 대조가 안 되기 때문**이다 — 경로가 틀렸을 때
  찾아 고칠 곳이 하나여야 한다. 실제로 자주 겪는다.
- **끝난 경기 상세는 내용이 안 변한다 → `.cache/` 디스크 캐시**(`FCOnlineAPI.get_match_detail`).
  호출량 초과는 **`OPENAPI00007`(429)** 로 돌아온다.
- **방어적 읽기의 이 앱 패턴은 `MatchLoader._safe_detail`.** 한 경기 파싱 실패가
  나머지 조회를 막지 않는다.
- **에러 메시지 표는 `nexon_api.ERROR_MESSAGES`.** 새 코드가 생기면 여기에 넣는다.
- **터미널 스모크는 `python check_api.py <닉네임>`.** 새 엔드포인트를 붙이면 여기에도
  한 줄 추가한다.

---

## PyQt6 규칙 (실수 방지 — 실제로 당한 것들)

> 일반 Qt 함정(`NoScrollComboBox` · 위젯 import 확인 · 네트워크는 UI 스레드 밖 ·
> 닫을 때 워커 정리 · 재할당 전역은 모듈 경유)은 전역 규칙 8번.
> 이 앱의 해당 지점: 워커는 `MatchLoader`, 정리는 `closeEvent` 의 `cancel()` → `wait()`.
> 아래 둘은 **표(`QTableWidget`) 고유의 함정**이라 여기 남긴다.

1. **`QTableWidget.setSortingEnabled(True)`를 다시 부르면, 헤더에 이미 정렬
   상태(이전에 `sortByColumn`을 부른 적 있음)가 남아 있을 때 Qt가 그 자리에서
   즉시 재정렬한다(문서화된 동작).** `_fill()`로 표를 다시 채운 직후 이 재정렬이
   일어나면, "방금 채운 순서 = 원본 리스트 순서"라고 믿고 인덱스로 색을 칠하거나
   데이터를 붙이는 후처리 코드가 엉뚱한 행을 건드린다 — 실제로 선수 지표 표의
   공격력/수비력 색이 틀린 값에 칠해지는 버그로 나타났다(재검색 등 **두 번째
   렌더부터**만 터져서 처음엔 안 보였다). 후처리가 있는 표는 `_fill(..., enable_sort=False)`로
   채우고, 후처리를 다 끝낸 뒤에만 `setSortingEnabled(True)`를 직접 부를 것.

2. **`QTableWidgetItem.setBackground()`에 반투명(alpha) 색을 쓰면 alternating
   row 색(짝/홀 행이 다름) 위에 섞여서, 값이 같아도 행 위치에 따라 진하기가
   달라 보인다.** 값 크기에 비례해 배경을 칠하는 강조(공격력/수비력 등)는
   알파 대신 고정 배경색(`T.PANEL`) 기준으로 직접 섞은 **불투명** 색을 써야
   행마다 일관되게 보인다.

---

## 작업 방식 (이 앱 고유분)

> 난이도별 작업 방식·TaskList·Git·문서 규칙은 전역 `~/.claude/CLAUDE.md`.

- **커밋 후 push 까지 자동으로 진행한다** (원격이 붙어 있으면). 이 프로젝트만의 관습이다.
- `.gitignore` 대상 중 이 앱 고유: **`.env`**(API 키) · **`.cache/`**(넥슨 응답 캐시).
- **CSV 로 내보내는 기능을 추가하면 `encoding="utf-8-sig"`** 를 쓴다 — Excel 이 BOM 없는
  UTF-8 을 못 알아본다. 닉네임·구단명·선수명이 전부 한글이라 **첫 사용에서 바로 깨진
  화면을 보게 된다.** (2026-08-15 전수 점검: 이 프로젝트 인코딩 위험 지점 0건 — 유지한다.)

---

## 알려진 버그 — 해결 이력

2026-07-18 팀컬러 기능 세션에서 코드 리뷰로 찾은 항목들. 같은 날 전부 수정 완료.

- `_on_fetch_team_colors` — 조회 중 범위가 넓어져 재호출되면 `_teamcolor_retry_pending`
  플래그를 세워 `_on_teamcolor_finished`에서 자동 재시도하도록 고침.
- `_on_loaded` — `ouid`가 실제로 바뀔 때만 `_trend_reset_pending`을 세우도록 고쳐서,
  같은 계정 재검색/새 경기 확인 시 승률 그래프 "최근 N일" 설정이 유지되게 함.
- `_on_teamcolor_finished` — 상태 메시지를 세션 누적(`self._team_colors`) 대신 이번
  라운드(`self._teamcolor_pending`/`fetched`) 기준으로 바꿈.
- `TeamColorLoader.run()`, `MatchLoader.run()` — `RuntimeError`뿐 아니라
  `concurrent.futures.CancelledError`도 잡도록 고쳐서, 조회 중 창을 닫아도 트레이스백
  없이 종료되게 함.
- 죽은 코드였던 `self._img_loader` 필드·`closeEvent`의 관련 체크 제거.
- 아웃라인 버튼 스타일시트를 `theme.OUTLINE_BUTTON_QSS` 상수로 통합(3곳 복붙 제거).
- "포지션별 최다 상대" 정렬고정+색칠 시퀀스는 확인 결과 이미 `_position_opp_rows`/
  `_tint_position_rows` 공용 메서드로 분리돼 있어 추가 조치 불필요.
- `widgets.py` `FitTableWidget._fit()` — 기준 폰트 크기의 텍스트 폭을
  `set_content_widths()`(데이터 변경 시)에서만 캐시하고, 리사이즈 중엔 그 캐시로
  후보 폰트 크기를 산술 추정 + 폰트 크기별 결과 캐시(`_fit_cache`)로 재사용하도록 바꿔
  드래그 중 반복 전체 스캔을 없앰.

`_on_teamcolor_loaded`의 10개 단위 재계산은 그대로 둠 — 표시 구간이 수십~백 건
규모라 체감 성능 이슈가 없고, 지금 손대면 과한 최적화(YAGNI).

---

## 나중에 (필요가 생기면 도입 — 지금은 과함)

개발 프로세스·인프라는 일부러 가볍게 뒀다(YAGNI). 아래는 **그 필요가 실제로
생겼을 때** 도입한다. ⚠️ 이건 프로세스 이야기지 **런타임 의존성 규칙이 아니다** —
라이브러리 추가는 그때그때 이득 대비 비용(exe 용량·빌드 실패 위험·기존 코드와의
스타일 충돌)으로 따로 판단한다.

| 항목 | 상태 / 도입 시점 |
|------|-----------------|
| **버전 체계 + changelog** | ❌ 아직. exe로 **남에게 줄 때** 도입. 혼자 쓰는 동안은 git log로 충분 |
| **회귀 검증(파싱 골든)** | ✅ 도입됨 — `tests/fixtures/`(익명화한 실응답 4경기) + `test_parsing.py`·`test_analysis.py`. 네트워크 없이 `python tests/test_parsing.py`로 실행 |
| **SQLite 누적 저장** | ✅ 도입됨 — `store.py`. API가 최근 100경기만 주는데 이 계정은 21시간에 100경기가 쌓여서, 화면은 API가 아니라 이 DB를 본다 |
| **PyInstaller exe 빌드** | ✅ 도입됨 — `피파전적관리.spec` → `dist/`. 기본적으로 매 작업마다 빌드해 실행 확인하되, 게임 중 등 사용자가 스모크를 미루라고 하면 offscreen(`QT_QPA_PLATFORM=offscreen`)으로 위젯 생성·렌더 경로만 확인한다 |
