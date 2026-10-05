# 피파 전적관리 — 프로젝트 규칙

> **공통 규칙(보고·작업 방식·검증·Git·문서·상수 위치·외부 API·Windows/Qt 앱·YAGNI)은
> 전역 `~/.claude/CLAUDE.md` 에 있다.** 여기에는 **이 앱에서 실제로 당한 것**만 적는다.

## 프로젝트 개요

넥슨 오픈API로 EA SPORTS FC 온라인 전적을 조회·집계하는 PyQt6 데스크톱 앱.

| 파일 | 역할 |
|------|------|
| `app_main.py` | PyQt6 UI — 검색 화면 → 왼쪽 메뉴(`NAV` 표) + 위쪽 바 + 메뉴별 페이지(대시보드 포함), 조회 워커 스레드(`MatchLoader`). 메뉴를 늘리려면 `NAV` 에 한 줄. 로더는 계정 확인 뒤 저장된 경기 읽기(`load_saved`)·랭킹·메타를 넥슨 조회와 **나란히** 돌리고, 켤 때 마지막 계정을 미리 읽어 둔다(`SavedPrefetch` — 화면엔 안 그림). 랭킹이 늦으면 `rank_ready` 로 카드만 뒤따른다 |
| `theme.py` | 테마 — 어두운·밝은 팔레트(`MODE` 로 선택)·QSS·`apply()`(Fusion + 팔레트 + 기본 글꼴). **색은 여기서만** |
| `nexon_api.py` | 넥슨 오픈API 클라이언트(`FCOnlineAPI`). **엔드포인트 경로·에러코드 상수가 전부 여기 상단에** |
| `models.py` | 매치 상세 JSON → `MatchSummary` 파싱, `Stats`·상대 전적·승률 추이 집계 |
| `stats.py` | 여러 경기 집계 — 선수 지표·전술·경기 결과. 역산 상수가 여기 모여 있다 |
| `analysis.py` | 집계 → 문장(`narrate`). **임계값·최소 표본 상수가 전부 여기 상단에.** 표본 미달이면 침묵 |
| `dashboard.py` | 대시보드 — 카드 배치·채우기. **카드마다 눌러서 가는 상세 페이지와 같은 범위**를 센다(파일 머리말 표) |
| `charts.py` | 대시보드 그래프(QPainter). 색은 `theme.CHART_*` — 앱의 GREEN/RED 는 적록 색약에서 구분이 안 돼 그래프엔 안 쓴다 |
| `widgets.py` | 화면 부품 — 랭커 카드, 표(`FitTableWidget`), 축구장 스쿼드 배치(`PitchWidget`), 좁으면 접히는 바(`WrapBar`)·세로 스크롤 틀(`VScrollArea`)·줄어드는 라벨(`FitLabel`) 등 |
| `images.py` | 선수 얼굴·등급 배지·시즌 아이콘 — 넥슨 CDN/메타 기반, 디스크 캐시 |
| `ranker.py` | 넥슨 데이터센터 HTML 스크래핑(감독모드 순위·구단가치 — 오픈API엔 없음). 팀컬러 조회는 상대가 적으면 상대마다 검색(`fetch_manager_rank`), 500명보다 많으면 1만 위 목록 500쪽(`fetch_rank_page`) — 목록은 **행 단위로 잘라 읽는다**(팀컬러 빈 행에서 뒤가 밀린다) |
| `rankcollect.py` | 랭킹 1만 명 수집(1.1.1, 기본 꺼짐 `config.RANK_COLLECT`) — `rank.db`(fifa.db 와 **따로** — 지우기가 파일 삭제) 에 스냅숏 원본(14일)·집계(영구)·수집 상태·잠금. `collect()` 한 번 = 한 회차: `.env` 다시 읽기 → 잠금 → 500쪽(작업자 6, 첫 실패에 나머지 취소) → 한 트랜잭션 저장 → `record_result`(실패 대기 1~24h · 차단 3회차면 스스로 끔 D6 · 연결 안 됨·정각 걸침은 안 셈). 예약은 앱 몫 — `is_due`·`pick_start`·`start_still_valid` 만 준다(앱은 `app_main.RankCollectScheduler` — 1시간 확인·예약 하나·늦게 터지면 다시 고름). 팀컬러 목록(`RankListLoader`)과 **목록 읽기 차례**(`acquire_list_read`)를 나눠, 하루 안의 스냅숏이 있으면 팀컬러는 거기서(요청 0) · 겹치면 뒤에 온 쪽이 기다린다 · 팀컬러가 다 읽은 목록은 수집이 켜져 있으면 스냅숏으로(`save_from_pages`). 토글은 `set_enabled_at`(.env 와 rank.db 사본 둘 다). 스레드 우선순위는 ctypes 로 낮추는데 **핸들 형을 안 적으면 64비트에서 조용히 실패**했다(테스트가 잡음) |
| `tray.py` | 트레이 상주(1.1.1) — `AppShell`(트레이 · 수집 예약 · 6시간 업데이트 확인 · 숨긴 30분 뒤 내려놓기)과 **종료 진입점 하나 `quit_app(fast)`** (파일 머리말 표 — 새 종료 경로는 여기를 거친다). 창(`MainWindow`)은 정리를 `shutdown(fast)` 로 내주고, closeEvent 는 `_quitting` 이면 받기만(`app.quit()` 이 다시 부른다). 한 번만 실행(`SingleInstance`)은 **뮤텍스로 판정** — 윈도우에선 같은 이름 `QLocalServer` 의 두 번째 listen 도 성공한다(2026-10-04 실측). 파이프는 "창 앞으로"와 **종료 부탁(`--quit` — 제거기가 부른다. 제거기는 설치기와 달리 떠 있는 앱을 안 닫아 폴더가 통째로 남았다, 1.1.1 실측)**만. `--quit` 은 떠 있는 게 없으면 아무것도 켜지 않는다(`.iss` `QuitArg` 와 같아야 — 테스트가 대조). **트레이 풍선 알림은 없다**(2026-10-05 사용자 — exe 실측 중 뜬 알림을 보고 "알림 자체는 안 보내도록"). 숨긴 중 생긴 일은 창을 열 때 — `test_no_tray_balloon_from_any_path` 가 `showMessage(` 를 소스에서 막는다 |
| `autostart.py` | 자동 실행 — HKCU Run 에 `"<exe>" --tray`. exe 에서만 · 값 이름을 설치판(`VALUE_INSTALLED` — `.iss` 제거기와 같아야)·포터블로 나눈다 · 경로는 없어졌을 때만 고친다(`repair`) |
| `seasons.py` | 데이터센터 랭킹 시즌표 → 경기를 시즌에 나눠 담기(`season_of`·`group_by_season`). 함정은 아래 "시즌" |
| `playerinfo.py` | 선수 카드 상세(모바일 데이터센터)·능력치 시뮬레이터(PC 데이터센터 POST) 스크래핑 |
| `store.py` | SQLite 누적(`fifa.db`) — 경기·계정·최근 검색·팀컬러/시즌 캐시(TTL)·검색한 구단주 ELO(`elo_history` — 메인 검색 로더만 `record_elo=True`, 비교 로더·봇은 안 적는다). **화면은 API 가 아니라 이 DB 를 본다**. 검색 결과는 바탕(화면이 가진 목록 · 미리 읽은 것 · 새로 읽은 것) + DB 에만 있는 경기(`known_ids` 대조 — 시각으로 자르면 이어 받은 옛 경기를 빠뜨린다)를 `merge_details`. 열 때 `PRAGMA optimize` 로 통계를 갱신하는데, **통계가 생기면 SQLite 가 계획을 바꾼다** — `load_details` 는 본문을 통째로 임시 정렬하는 계획을 골라 SQL 이 3배가 됐다(2026-10-04). 그래서 `(종류, 날짜)` 인덱스를 직접 지정한다. 새 쿼리를 붙이면 `EXPLAIN QUERY PLAN` 에 `TEMP B-TREE` 가 없는지 본다. 해석은 `orjson`(없으면 json, 배포판엔 `release.py` 가 확인) |
| `config.py` | `.env`에서 API 키 로드·저장(`save_api_key`), 웹 데이터 스위치(`WEB_DATA`)·랭킹 수집 스위치(`RANK_COLLECT`·`RANK_*` 상수 — `read_env_switches` 가 디스크에서 다시 읽는다)·UA, 매치 종류·조회 개수 기본값 |
| `notice.py` | 이용 안내·개인정보·웹 데이터 고지·오픈소스 목록(`THIRD_PARTY`) — 첫 실행 `NoticeDialog` 와 [정보] `AboutDialog` 가 같은 글을 쓴다. **글을 실질적으로 바꾸면 `config.NOTICE_VERSION` 을 올린다**(이미 동의한 사람에게 다시 보이게). 새 패키지를 묶으면 `THIRD_PARTY` 에 한 줄 — spec 이 라이선스 전문을 `_internal/licenses/` 에 넣고 `release.py` 가 빠진 걸 막는다 |
| `crashlog.py` | 처리 안 된 예외 → `%LOCALAPPDATA%\피파전적관리\logs\crash.log`. **exe 는 콘솔이 없어 이게 없으면 창이 흔적 없이 사라진다** — PyQt6 는 기본 훅이면 슬롯 예외에서 프로세스를 끝낸다. **faulthandler 는 윈도우에서 처리된 네이티브 예외도 "fatal" 로 적는다**(1.1.1 exe 실측 — COM 0x8001010d) → 실행마다 `crash.fault.<PID>` 에 받고 정상 종료(atexit)면 버리고, 다음 실행 때 남은 것(지울 수 있는 것 = 쥔 프로세스가 죽은 것)만 crash.log 로 옮긴다 |
| `updatecheck.py` | 새 버전 확인 + 앱 안 업데이트 — 켤 때 한 번 GitHub 최신 릴리스 태그와 `APP_VERSION` 을 숫자로 비교(`check()` → NEWER·LATEST·UNKNOWN — 실패·릴리스 없음은 UNKNOWN 이라 "최신"이라고 하지 않는다). 창 오른쪽 아래 카드(`widgets.UpdateCard`)의 [업데이트] → 설치 파일을 받아 **`SHA256SUMS.txt` 와 같을 때만** `/AUTOUPDATE=1` 로 실행하고 앱을 닫는다 → `.iss` 의 `IsAutoUpdate` 가 설치 뒤 앱을 다시 켠다. 설치판 판별은 exe 옆 `unins000.exe`(포터블·소스 실행은 페이지만 연다). 첨부 이름(`SETUP_ASSET`)은 `tools/release.py` 와 같아야 한다(테스트가 대조) |
| `check_api.py` | 터미널 연결 점검 — GUI 띄우기 전 키·엔드포인트 확인용 |
| `tools/release.py` · `installer/피파전적관리.iss` | 배포판 — 빌드 → zip · 설치 파일(Inno Setup 6) → 개인정보 검사(대조 문자열이 안 잡히면 멈춤) → `gh release` 명령 **출력만**. `.iss` 는 **UTF-8 BOM**(없으면 한글이 ANSI 로 읽힌다)이고 `AppId` GUID 는 바꾸지 않는다(바뀌면 업데이트가 별개 프로그램으로 깔린다). **릴리스 첨부 이름은 영문**(`ASSET_PREFIX`) — GitHub 가 한글을 지워 v0.2.0 zip 이 `-v0.2.0.zip` 으로 올라갔다(2026-10-02) |
| `bot/` · `adapters/` | 카카오톡 오픈채팅 봇 — 서버(`bot/`, 같은 DB 를 본다)와 카톡에 붙이는 쪽(`adapters/`). 각 폴더 README |
| `tests/` | `test_parsing.py`(파싱·집계·시즌 골든) · `test_analysis.py` · `test_ui_smoke.py`(offscreen 화면 배선 — FHD 가상 화면) · `test_window_size.py`(흉내 낸 화면 5종 `tests/screens/*.json` 에서 실제 창 크기, 화면마다 별도 프로세스) · `test_bot.py` · `test_rankcollect.py`(수집기 — 가짜 목록) · `test_tray.py`(트레이·종료·한 번만 실행·자동 실행 — 창과 엮이는 X 숨김·내려놓기는 `test_ui_smoke`) · `test_release.py`(배포 검사) · `test_adapter.js`. pytest 없이 파일을 직접 실행 |

```powershell
python check_api.py <닉네임>   # API 점검
python rankcollect.py --pages 3   # 랭킹 수집 실제 3쪽(저장 안 함 — 웹 데이터가 켜져 있어야)
python app_main.py             # 앱 실행
python tests/test_ui_smoke.py  # 화면 배선 스모크(offscreen, 네트워크 없음 — 글꼴 폴더는 테스트가 기본으로 준다)
python tests/test_tray.py      # 트레이·종료 진입점·한 번만 실행·자동 실행(가짜 레지스트리)
python tests/test_window_size.py  # 창 크기 — FHD 100·125·150%·1366·175% 를 흉내 내 실제 창을 띄워 잰다
# 메뉴별 화면을 PNG 로 떠서 눈으로 볼 때 — 최근 검색 칩에 실제 닉네임이 찍히니 확인 후 지운다
$env:UI_SHOT="<스크래치 폴더>"; python tests/test_ui_smoke.py
python -m PyInstaller --noconfirm 피파전적관리.spec   # exe → dist\피파전적관리\ (확인용)
python tests/test_release.py   # 배포 검사 로직(빌드 없이)
python tools/release.py        # 배포판 — 커밋된 상태에서만 돈다. 공개 명령은 출력만 (README "빌드·배포")
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
- **화면 이름은 `config.APP_NAME`("감독모드 전적 분석"), 내부 이름은 예전 그대로**(데이터 폴더·exe·AppId·
  릴리스 첨부·저장소) — 2026-10-02 상표 때문에 화면에서 FIFA·FC ONLINE 을 뺐다. 내부 이름을 바꾸면 기존
  데이터·자동 업데이트가 끊긴다. `.iss` 의 `AppTitle` 은 `APP_NAME` 과 같아야 한다(테스트가 대조).
  화면에 상표가 다시 들어오면 `test_no_trademark_in_window_names` 가 빨개진다.
- **웹 데이터는 기본 꺼짐 + 첫 실행 동의(`NoticeDialog`) 때 사용자가 고른다**(2026-10-02 결정, 체크 기본 해제).
  `config.WEB_DATA`·`NOTICE_ACCEPTED` 는 재할당 전역(`set_web_data`·`accept_notice`) — 모듈 경유로 읽는다.
- **넥슨 웹(데이터센터) 요청은 전부 `config.WEB_DATA` 스위치 뒤에** — `ranker`·`playerinfo`·`seasons`
  의 fetch 함수 첫 줄에서 막는다. 새 스크래핑 함수를 만들면 같은 검사를 넣고
  `test_web_data_switch_blocks_every_request` 의 `_web_calls()` 에 한 줄 추가한다(안 넣으면 그 검사가 비어 있다).
  **요청은 세션을 직접 부르지 말고 `ranker.web_get(session, url, …)`** — 한 프로세스 동시 요청 상한
  (`config.RANK_MAX_CONCURRENT` = 8)이 거기 있다. 수집(1.1.1)·팀컬러·검색이 겹쳐도 넥슨에 8개를 넘겨 보내지 않게.
  `_web_calls()` 에 넣으면 `test_every_web_request_goes_through_the_concurrency_cap` 이 우회를 잡는다.
  UA 는 `config.WEB_USER_AGENT`(앱 이름) — 2026-10-02 실측으로 브라우저 UA 와 응답이 같았고, **빈 UA 만** playerinfo 가 500.
  CDN 이미지(`images.py`)는 정적 파일이라 스위치 밖.
- **엔드포인트 경로 상수는 `nexon_api.py` 상단에.** 여기서 특히 중요한 이유는
  **공식 문서가 JS 렌더링이라 자동 대조가 안 되기 때문**이다 — 경로가 틀렸을 때
  찾아 고칠 곳이 하나여야 한다. 실제로 자주 겪는다.
- **끝난 경기 상세는 내용이 안 변한다 → `.cache/` 디스크 캐시**(`FCOnlineAPI.get_match_detail`).
  호출량 초과는 **`OPENAPI00007`(429)** 로 돌아온다.
  **캐시는 DB 에 넣기 전까지만 산다** — 저장하면 `forget_details`, 켤 때 `CachePruneWorker` 가 DB 에 있는
  것을 정리한다(v1.0.1 전엔 두 벌로 쌓여 2만 개·407MB). 남는 건 한도에 걸려 아직 못 넣은 경기 — 이어 받기용.
- **방어적 읽기의 이 앱 패턴은 `MatchLoader._safe_detail`.** 한 경기 파싱 실패가
  나머지 조회를 막지 않는다.
- **에러 메시지 표는 `nexon_api.ERROR_MESSAGES`.** 새 코드가 생기면 여기에 넣는다.
- **터미널 스모크는 `python check_api.py <닉네임>`.** 새 엔드포인트를 붙이면 여기에도
  한 줄 추가한다.

### 시즌 (`seasons.py`) — 넥슨 시즌표의 함정

오픈API 는 경기에 시즌을 안 달아 준다. 데이터센터 시즌표의 기간으로 나누는데, 셋 다
조용히 틀어지는 종류라 `test_parsing.py` 의 시즌 테스트가 지킨다(2026-10-02 추가 — 그전엔 0개였다).
- **`rt` 에 따라 시즌 번호가 다르다** — 공식경기 91 = 감독모드 89(기간은 같다). 항상 `rt=manager`.
- **앞 시즌 종료일 == 다음 시즌 시작일.** `[시작, 종료)` 반개구간 — 닫힌 구간이면 경계일 경기가 두 시즌에 들어간다.
- **진행 중인 시즌은 목록에 없다.** 마지막 종료일 이후 경기는 `None`(진행 중) 그룹으로 맨 앞.
- 이름이 해마다 반복된다("시즌 3") → 화면에는 `Season.label`("2026 시즌 3").

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

6. **offscreen 테스트에서 모달은 안 닫힌다 — 회귀가 "실패"가 아니라 "멈춤"으로 나타난다.**
   2026-10-02 하루에 세 번(못 찾음 안내 창 · 키 창 `exec()` · `app.exec()`), 매번 그 자리만
   가로채다가 장치로 바꿨다: `test_ui_smoke.py` 머리에서 `QMessageBox.warning/information/
   critical/question` · `QDialog.exec` · `QApplication.exec` 를 **부르면 즉시 `ModalCalled`**
   로 막는다. 창이 떠야 정상인 테스트는 그 함수를 직접 가로챈다(지금처럼). 새 종류의 모달
   (`QFileDialog`·`QInputDialog`·`QMenu.exec` 등)을 쓰기 시작하면 거기에도 한 줄 더한다.
   검증: 오늘 멈췄던 두 경우가 장치로 4초 만에 FAIL, 장치를 빼면 다시 60초 멈춤.

7. **화면은 보이는 메뉴만 그린다 — 새 메뉴는 `PAGE_RENDER_KEYS`·`_renderers()` 에 넣는다**(2026-10-02).
   예전엔 `_render_all` 이 18개 메뉴를 매번 다 그려 시즌 "전체"(1만 경기) 전환에 3.2~3.5초 창이 굳었다
   → 지금 0.55초. `_render_all` 은 전부 낡음 표시 + 보이는 것만, 나머지는 `_on_nav_changed` 에서 그린다.
   - 표에 안 넣은 메뉴는 **조용히 안 그려진다**(예외 없음). 데이터가 바뀌어 한 메뉴만 다시 그려야 하면
     직접 `_render_x()` 대신 `_invalidate("키")` — 보이면 바로, 아니면 열 때.
   - 대시보드와 흐름 분석 메뉴는 `_narrate_scope()` 결과를 같이 쓴다(따로 계산하던 게 그리기의 55%).
     캐시 키는 목록 `id`·길이·맨 앞 경기 — 목록을 제자리에서 고치면(같은 객체·같은 길이) 안 잡힌다.
   - 테스트는 `_win.LAZY_RENDER = False` 로 다 그린 상태를 본다. 지연 동작은 `test_lazy_*` 가 켜서 잰다.

8. **스레드 → 화면 신호에 큰 데이터를 실을 땐 `pyqtSignal(object)` — `list`·`dict` 로 선언하지 않는다**(2026-10-02).
   `pyqtSignal(list, …)` 은 PyQt 가 중첩 dict 를 Qt 형식으로 통째로 깊은 복사했다 되돌린다. 1만 경기 기록이
   직접 읽으면 786MB 인데 앱에선 최대 4.5GB, 로더 끝 → 화면까지 6초였다. 몇 달 동안 안 보인 이유: 메모리를
   늘 화면 함수를 **직접** 불러 쟀다(신호를 안 거침) — 실제 경로(exe 를 켜서 프로세스 메모리)로 재야 보인다.
   `test_loader_signals_pass_objects_without_copying` 이 "받은 게 같은 객체(is)"로 지킨다.

9. **창·대화상자 크기는 고정값으로 열지 않는다 — 화면(배율 반영 · 작업 표시줄 뺀 것)에 맞춘다**(1.0.3, 2026-10-04).
   1600×900 고정이 FHD 150%(논리 1280×720)·1366×768 에서 넘쳤고, 최소 1280×720 조차 안 들어갔다. 지금 장치:
   `initial_window`(규칙) · `_apply_plan`(최소 크기 → 저장 크기 복원 순서 — 복원보다 최소를 늦게 걸면 Qt 의 '화면에 맞춰
   줄임'이 무시된다) · 띄운 뒤 `_check_fits` · 모니터/배율 변경 때 `_refit`(한 바퀴 뒤 — 바로 하면 Qt 가 덮어쓴다).
   **새 대화상자는 `fit_to_screen(dlg, w, h)`** 로 열고, 내용 최소 높이가 657(150% 노트북 창 안쪽)을 넘으면 세로 스크롤
   안에 넣는다(스쿼드 창이 그랬다). `tests/test_window_size.py` 가 흉내 낸 화면 5종에서 실제 창으로 잰다.

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
| **버전 체계 + changelog** | ✅ 도입됨(2026-10-02, v0.2.0) — 지인·모르는 사람에게 exe 를 주기로 해서 조건 충족. 버전은 `config.APP_VERSION` 한 곳(UA 에도 실린다), 올리면 `CHANGELOG.md` 에 받는 사람이 읽을 말로 한 절. 크래시 로그(`crashlog.py`)도 같은 이유로 같이 들어왔다. **번호 규칙(2026-10-04 사용자)**: `a.b.c` — a 큰 패치(구조가 바뀌어 새로 익혀야 함) · b 중간(새 기능·화면) · c 작은 수정(버그·문구). 올린 자리 아래는 **1로** 돌린다(1.4.2 → 2.1.1). 1.0.x 만 예외로 1.0.3 까지, 다음 기능 패치는 1.1.1. 앞으로의 버전별 계획은 `docs/ROADMAP.md` |
| **회귀 검증(파싱 골든)** | ✅ 도입됨 — `tests/fixtures/`(익명화한 실응답 4경기) + `test_parsing.py`·`test_analysis.py`. 네트워크 없이 `python tests/test_parsing.py`로 실행 |
| **SQLite 누적 저장** | ✅ 도입됨 — `store.py`. **API가 오래된 경기를 버린다**(2026-09-05 실측: `offset` 페이징으로 3,024경기·약 한 달까지만. 같은 시점 DB 는 7,859경기). 화면은 API가 아니라 이 DB를 본다. ⚠️ 여기 오래 *"최근 100경기만 준다"* 라고 적혀 있었다 — 100은 **한 번에 받는 개수 상한**이고, 그 정정(`d872ef5` · 07-18)이 문서까지 오지 않았다 |
| **PyInstaller exe 빌드** | ✅ 도입됨 — `피파전적관리.spec` → `dist/피파전적관리/`(**onedir**, 2026-10-02 onefile 에서 전환 — 프로세스가 하나라 창 찾기에 부트로더를 거를 필요가 없다), 배포는 README "빌드·배포"의 zip. 기본적으로 매 작업마다 빌드해 실행 확인하되, 게임 중 등 사용자가 스모크를 미루라고 하면 offscreen(`QT_QPA_PLATFORM=offscreen`)으로 위젯 생성·렌더 경로만 확인한다 |
