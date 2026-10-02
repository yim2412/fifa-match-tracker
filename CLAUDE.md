# 피파 전적관리 — 프로젝트 규칙

> **공통 규칙(보고·작업 방식·검증·Git·문서·상수 위치·외부 API·Windows/Qt 앱·YAGNI)은
> 전역 `~/.claude/CLAUDE.md` 에 있다.** 여기에는 **이 앱에서 실제로 당한 것**만 적는다.

## 프로젝트 개요

넥슨 오픈API로 EA SPORTS FC 온라인 전적을 조회·집계하는 PyQt6 데스크톱 앱.

| 파일 | 역할 |
|------|------|
| `app_main.py` | PyQt6 UI — 검색 화면 → 왼쪽 메뉴(`NAV` 표) + 위쪽 바 + 메뉴별 페이지(대시보드 포함), 조회 워커 스레드(`MatchLoader`). 메뉴를 늘리려면 `NAV` 에 한 줄 |
| `theme.py` | 테마 — 어두운·밝은 팔레트(`MODE` 로 선택)·QSS·`apply()`(Fusion + 팔레트 + 기본 글꼴). **색은 여기서만** |
| `nexon_api.py` | 넥슨 오픈API 클라이언트(`FCOnlineAPI`). **엔드포인트 경로·에러코드 상수가 전부 여기 상단에** |
| `models.py` | 매치 상세 JSON → `MatchSummary` 파싱, `Stats`·상대 전적·승률 추이 집계 |
| `stats.py` | 여러 경기 집계 — 선수 지표·전술·경기 결과. 역산 상수가 여기 모여 있다 |
| `analysis.py` | 집계 → 문장(`narrate`). **임계값·최소 표본 상수가 전부 여기 상단에.** 표본 미달이면 침묵 |
| `dashboard.py` | 대시보드 — 카드 배치·채우기. **카드마다 눌러서 가는 상세 페이지와 같은 범위**를 센다(파일 머리말 표) |
| `charts.py` | 대시보드 그래프(QPainter). 색은 `theme.CHART_*` — 앱의 GREEN/RED 는 적록 색약에서 구분이 안 돼 그래프엔 안 쓴다 |
| `widgets.py` | 화면 부품 — 랭커 카드, 표(`FitTableWidget`), 축구장 스쿼드 배치(`PitchWidget`), 좁으면 접히는 바(`WrapBar`)·세로 스크롤 틀(`VScrollArea`)·줄어드는 라벨(`FitLabel`) 등 |
| `images.py` | 선수 얼굴·등급 배지·시즌 아이콘 — 넥슨 CDN/메타 기반, 디스크 캐시 |
| `ranker.py` | 넥슨 데이터센터 HTML 스크래핑(감독모드 순위 — 오픈API엔 없음) |
| `config.py` | `.env`에서 API 키 로드·저장(`save_api_key`), 웹 데이터 스위치(`WEB_DATA`)·UA, 매치 종류·조회 개수 기본값 |
| `crashlog.py` | 처리 안 된 예외 → `%LOCALAPPDATA%\피파전적관리\logs\crash.log`. **exe 는 콘솔이 없어 이게 없으면 창이 흔적 없이 사라진다** — PyQt6 는 기본 훅이면 슬롯 예외에서 프로세스를 끝낸다 |
| `check_api.py` | 터미널 연결 점검 — GUI 띄우기 전 키·엔드포인트 확인용 |

```powershell
python check_api.py <닉네임>   # API 점검
python app_main.py             # 앱 실행
python tests/test_ui_smoke.py  # 화면 배선 스모크(offscreen, 네트워크 없음)
# 메뉴별 화면을 PNG 로 떠서 눈으로 볼 때 — 글꼴 폴더를 안 주면 offscreen 이 장식체를 집는다
$env:QT_QPA_FONTDIR="C:/Windows/Fonts"; $env:UI_SHOT="<스크래치 폴더>"; python tests/test_ui_smoke.py
```

필수 패키지: `pip install -r requirements.txt` (PyQt6, requests, python-dotenv)

---

## 넥슨 API — 이 앱의 배선과 함정

> 외부 API 앱의 일반 규칙(캐싱 우선 · `.get()` 방어 · 에러는 사람 말로 · 터미널 스모크
> 유지)과 비밀 취급은 전역 규칙 7·4번. 아래는 **이 앱에만 해당하는 것**이다.

- **키는 `.env` 에만. 코드에서는 `config.API_KEY` 로만 읽는다.** 견본은 `.env.example` 로
  두고 실제 키 대신 안내 문구만 넣는다. `config.save_api_key()` 가 `API_KEY` 를 **재할당**하므로
  `from config import API_KEY` 는 금지(전역 규칙 8번 — 저장 후 새 키가 안 보인다).
- **키는 첫 실행 창(`ApiKeyDialog`)에서 받는다** — 배포받은 사람이 숨김 폴더에 `.env` 를 직접
  만들 수 없어서(2026-10-02). 판정은 `nexon_api.check_key`: 실측상 맞는 키로 없는 닉네임을
  물으면 `OPENAPI00004`, 틀린 키는 `OPENAPI00005` — **"00005 가 아니면 통과"**(네트워크·429·5xx 는
  모르는 상태라 불통). 조회 중 넥슨이 키를 거절하면 `MatchLoader.key_invalid` 로 같은 창을 띄운다.
- **넥슨 웹(데이터센터) 요청은 전부 `config.WEB_DATA` 스위치 뒤에** — `ranker`·`playerinfo`·`seasons`
  의 fetch 함수 첫 줄에서 막는다. 새 스크래핑 함수를 만들면 같은 검사를 넣고
  `test_web_data_switch_blocks_every_request` 의 `_web_calls()` 에 한 줄 추가한다(안 넣으면 그 검사가 비어 있다).
  UA 는 `config.WEB_USER_AGENT`(앱 이름) — 2026-10-02 실측으로 브라우저 UA 와 응답이 같았고, **빈 UA 만** playerinfo 가 500.
  CDN 이미지(`images.py`)는 정적 파일이라 스위치 밖.
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

3. **전역 QSS 에 `font-size`/`font-family` 를 넣지 않는다 — QSS 의 글꼴은 `setFont()` 를
   이긴다.** 다크 테마 시절 `QWidget { font-size: 15px }` 하나 때문에 코드의 모든
   `setFont` 크기(검색 제목 30pt · 랭커 타일 30pt · `FitTableWidget` 의 자동 축소)가
   **조용히 15px 로 눌려 있었다**(2026-10-01 밝은 테마 전환 중 발견). 표 자동 축소는
   "글자는 그대로, 열만 좁아지는" 상태였다. 기본 글꼴은 `theme.apply()` 의
   `QApplication.setFont` 로 건다.
   `test_ui_smoke.test_setfont_sizes_survive_stylesheet` 가 지킨다.

4. **`FitTableWidget` 의 열 여백은 상수가 아니라 `_measure_pad()` 로 Qt 에게 묻는다.**
   선수 표는 표 전용 QSS(`QTableWidget::item { padding: … }`)가 셀마다 여백을 얹어서,
   글자 폭만 보고 상수를 줄였더니 "33.3" 이 53px 칸에서 "3…" 로 잘렸다(Qt 계산 필요 폭 60).
   `test_player_table_cells_not_elided` 가 "열 폭 ≥ `sizeHintForColumn`" 으로 지킨다.

5. **창은 `MIN_WINDOW`(1280×720) 밑으로 안 줄고, 그 크기에서 아무것도 안 잘리는 게 기준이다**
   (2026-10-01). 그 전엔 위쪽 바가 한 줄이라 창이 1566×866 밑으로 안 줄었고(1366 노트북에서
   넘침), 그 위에서도 표는 둘째 열부터 균등 분할이라 긴 글자가 "…" 로 잘렸다. 지금 장치:
   - **모든 표는 `_make_table` → `FitTableWidget`**, 폭은 `_fill` 끝에서 `refit()`. `_fill` 을 안
     거치는 표(구단주 비교)는 직접 `refit()` 한다. 정렬 화살표 자리는 **정렬 중인 열에만**
     준다 — 전부에 주면 19열 선수 지표가 최소 글꼴로도 안 들어간다.
   - 위쪽 바는 `WrapBar` — 좁으면 두 줄. 페이지는 `VScrollArea` 로 감싸 **세로만** 스크롤.
     `QScrollArea` 는 기본으로 안쪽 최소 폭을 밖에 안 알려 가로가 조용히 잘리므로 그걸 알린다.
   - 큰 숫자 칸은 `FitLabel`(잘리는 대신 글꼴 축소).
   - **명시적 최소 폭(`setMinimumWidth`)은 Qt 의 힌트를 이긴다** — 시즌 칸의 `150` 이 그래서
     글자를 눌렀다. 줄 수 없는 폭이면 상수 대신 `sizeHint()` 로 준다.
   검사는 offscreen 스모크(`test_window_shrinks_…`·`test_no_table_elides_…` 등)와, 실데이터로
   메뉴마다 위젯 폭을 재는 스크래치 스크립트. ⚠ **가로 스크롤이 보이면 원인부터 재 본다** —
   경기 목록의 가로 막대를 "세로 막대 자리를 안 빼서"로 읽고 장치를 넣었는데, 실화면에서
   그 장치를 빼도 결과가 같았다. 진짜 원인은 화살표 자리를 모든 열에 준 것이었다(장치는 뺐다).

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
| **버전 체계 + changelog** | ✅ 도입됨(2026-10-02, v0.2.0) — 지인·모르는 사람에게 exe 를 주기로 해서 조건 충족. 버전은 `config.APP_VERSION` 한 곳(UA 에도 실린다), 올리면 `CHANGELOG.md` 에 받는 사람이 읽을 말로 한 절. 크래시 로그(`crashlog.py`)도 같은 이유로 같이 들어왔다 |
| **회귀 검증(파싱 골든)** | ✅ 도입됨 — `tests/fixtures/`(익명화한 실응답 4경기) + `test_parsing.py`·`test_analysis.py`. 네트워크 없이 `python tests/test_parsing.py`로 실행 |
| **SQLite 누적 저장** | ✅ 도입됨 — `store.py`. **API가 오래된 경기를 버린다**(2026-09-05 실측: `offset` 페이징으로 3,024경기·약 한 달까지만. 같은 시점 DB 는 7,859경기). 화면은 API가 아니라 이 DB를 본다. ⚠️ 여기 오래 *"최근 100경기만 준다"* 라고 적혀 있었다 — 100은 **한 번에 받는 개수 상한**이고, 그 정정(`d872ef5` · 07-18)이 문서까지 오지 않았다 |
| **PyInstaller exe 빌드** | ✅ 도입됨 — `피파전적관리.spec` → `dist/피파전적관리/`(**onedir**, 2026-10-02 onefile 에서 전환 — 프로세스가 하나라 창 찾기에 부트로더를 거를 필요가 없다), 배포는 README "빌드·배포"의 zip. 기본적으로 매 작업마다 빌드해 실행 확인하되, 게임 중 등 사용자가 스모크를 미루라고 하면 offscreen(`QT_QPA_PLATFORM=offscreen`)으로 위젯 생성·렌더 경로만 확인한다 |
