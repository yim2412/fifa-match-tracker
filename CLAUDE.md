# 피파 전적관리 — 프로젝트 규칙

> **공통 규칙(보고·작업 방식·검증·Git·문서·상수 위치·외부 API·Windows/Qt 앱·YAGNI)은
> 전역 `~/.claude/CLAUDE.md` 에 있다.** 여기에는 **이 앱에서 실제로 당한 것**만 적는다.

## 프로젝트 개요

넥슨 오픈API로 EA SPORTS FC 온라인 전적을 조회·집계하는 PyQt6 데스크톱 앱.

| 파일 | 역할 |
|------|------|
| `app_main.py` | PyQt6 UI — 왼쪽 메뉴(`NAV` 표)·페이지·작업 스레드 로더들. 메뉴를 늘리려면 `NAV` 에 한 줄(그리기 표는 PyQt 7) · 세부는 머리말 |
| `theme.py` | 테마 — 어두운·밝은 팔레트(`MODE` 로 선택)·QSS·`apply()`(Fusion + 팔레트 + 기본 글꼴). **색은 여기서만** |
| `nexon_api.py` | 넥슨 오픈API 클라이언트(`FCOnlineAPI`). **엔드포인트 경로·에러코드 상수가 전부 여기 상단에** |
| `models.py` | 매치 상세 JSON → `MatchSummary` 파싱, `Stats`·상대 전적·승률 추이 집계 |
| `stats.py` | 여러 경기 집계 — 선수 지표·전술·경기 결과·랭커 비교·축구장 배치. 역산 상수가 여기 · 세부는 머리말 |
| `analysis.py` | 집계 → 문장(`narrate`). **임계값·최소 표본 상수가 전부 여기 상단에.** 표본 미달이면 침묵. 조건부 승률 문장은 `Insight.basis`(`Basis` — 화면의 근거 막대)를 싣는다 — **새 규칙 함수는 `BASIS_RULES`/`NO_BASIS_RULES` 둘 중 하나에** 넣는다(test_analysis 가 모듈의 규칙 함수와 대조) |
| `predict.py` | 시즌 말 순위 예측 — 상수는 `config.PREDICT_*` · **골든(`test_predict.GOLDEN_VALUE`)이 바뀌면 의도인지 먼저** · 세부는 머리말 |
| `core_api.py` | **화면 ↔ 분석 경계** — 화면 쪽은 analysis·stats·models 를 여기서만. 새 함수는 import 와 `__all__` 에 한 줄 · 테스트에서 바꿔 끼울 땐 `core_api` 쪽을 |
| `dashboard.py` | 대시보드 — 카드 배치·채우기. **카드마다 눌러서 가는 상세 페이지와 같은 범위**를 센다(파일 머리말 표) |
| `charts.py` | 그래프(QPainter) — 색은 `theme.CHART_*` · 추이는 `AreaTrendChart` 하나, **기본값 그림은 얼린 사본과 픽셀까지 같아야**(`test_area_chart_defaults_unchanged`) · 세부는 머리말 |
| `widgets.py` | 화면 부품 — 표(`FitTableWidget`)·축구장(`PitchWidget`)·`PageTabs`·`WrapBar`·`VScrollArea`·`FitLabel` 등 · 세부는 머리말 |
| `images.py` | 선수 얼굴·등급 배지·시즌 아이콘 — 넥슨 CDN/메타 기반, 디스크 캐시 |
| `ranker.py` | 넥슨 데이터센터 HTML 스크래핑(감독모드 순위·구단가치 — 오픈API엔 없음). 팀컬러 조회는 상대가 적으면 상대마다 검색(`fetch_manager_rank`), 500명보다 많으면 1만 위 목록 500쪽(`fetch_rank_page`) — 목록은 **행 단위로 잘라 읽는다**(팀컬러 빈 행에서 뒤가 밀린다) |
| `teamcolor.py` | 팀컬러 효과표(목록·단계·적용 선수) — `X-Requested-With` 필요 · 이름 같은 팀컬러는 `config.TEAMCOLOR_DUP_NAMES` · 세부는 머리말 |
| `rankcollect.py` | 랭킹 1만 명 수집 · `rank.db`(fifa.db 와 따로) · 기본 꺼짐. **`snapshot_rows`·`snapshots` 의 열·INSERT 는 옛 버전 때문에 안 건드린다(옆 표만)** · **화면 스레드는 rank.db 에 쓰지 않는다** · 세부는 머리말 |
| `rankmeta.py` | 랭커 메타 ② — rank.db **읽기만**(요청 0 · 쓰기 0). rank.db 가 바뀌는 길은 `app_main.RANK_VIEW_KEYS` 를 무효화 · 세부는 머리말 |
| `rankerstats.py` | 랭커 기록 받기 — 하루 캐시(`ranker_stats`)에 없는 쌍만 · 한 요청 상한은 URL 길이 |
| `tradecollect.py` | 거래 기록 받기 — 넥슨 거래 API 는 **ouid 를 무시하고 키 주인 것만** 준다(`trades` 에 ouid 열 없음) · 받는 규칙은 머리말 표를 먼저 |
| `rankerpick.py` | 랭커 픽·추천 — 상위 랭커 마지막 경기를 오픈API 로(하루 계수 `api_budget` · 429 · **화면이 보일 때만**) · 지우기는 `purge_ranker_pick_data` 한 곳 · 세부는 머리말 |
| `squad_timeline.py` · `trade_book.py` | 스쿼드 타임라인 · 이적시장 가계부 — 계산만(화면은 `core_api`) · 거래는 내 계정일 때만(`app_main._timeline_for_screen`) · 세부는 `squad_timeline.py` 머리말 |
| `tray.py` | 트레이 상주 · **종료 진입점 하나 `quit_app(fast)`**(새 종료 경로는 여기를 거친다) · 한 번만 실행은 뮤텍스 · **트레이 풍선 알림 없음** · 세부는 머리말 |
| `autostart.py` | 자동 실행 — HKCU Run 에 `"<exe>" --tray`. exe 에서만 · 값 이름을 설치판(`VALUE_INSTALLED` — `.iss` 제거기와 같아야)·포터블로 나눈다 · 경로는 없어졌을 때만 고친다(`repair`) |
| `trait_codes.py` · `traitcollect.py` | 포지션 특성(32단계) — 특성·코치 이름표(번들에서 옮긴 상수 · 모르는 코드 "코드 N") · 스쿼드메이커 팀 칸 요청·해석·맞는 팀 찾기 · 받기 한 바퀴 `run`(웹 → 오픈API → 웹 · 로더 `app_main.TraitLoader`) · 집계 `trait_usage` → 탭 [메타 분석 › 포지션 특성](키 `TRAIT_KEY` — **`PICK_KEYS` 에 넣지 않는다**, 무효화는 `PICK_DATA_KEYS`) · **오픈API 백그라운드(랭커 픽·특성)를 켜는 자리는 `start_visible_api_loader`, 멈추는 자리는 `stop_api_loaders` 하나**(`test_every_api_loader_start_goes_through_one_function`) · 웹 스위치·`web_get`·`_web_calls()` 규칙은 `teamcolor` 와 같다 · 세부는 `traitcollect.py` 머리말 |
| `seasons.py` | 데이터센터 랭킹 시즌표 → 경기를 시즌에 나눠 담기(`season_of`·`group_by_season`). 함정은 아래 "시즌" |
| `playerinfo.py` | 선수 카드 상세·능력치 시뮬레이터 스크래핑 · 급여·OVR 은 **본 카드 구역에서만** · 세부는 머리말 |
| `store.py` | SQLite 누적(`fifa.db`) — **화면은 API 가 아니라 이 DB 를 본다** · **모든 연결 `secure_delete=ON`** · 새 쿼리는 `EXPLAIN QUERY PLAN` 에 `TEMP B-TREE` 없는지(`test_rules`) · 세부는 머리말 |
| `config.py` | `.env`에서 API 키 로드·저장(`save_api_key`), 웹 데이터 스위치(`WEB_DATA`)·랭킹 수집 스위치(`RANK_COLLECT`·`RANK_*` 상수 — `read_env_switches` 가 디스크에서 다시 읽는다)·UA, 매치 종류·조회 개수 기본값 |
| `notice.py` | 이용 안내·개인정보·오픈소스 목록. **글을 실질적으로 바꾸면 `config.NOTICE_VERSION` 을 올린다**(문구 골든 `test_notice_text_bumps_version` 이 짝을 지킨다 · 바뀐 점은 `CHANGES` 에 버전별 한 덩이) · 새 패키지는 `THIRD_PARTY` 에 한 줄 · 세부는 머리말 |
| `crashlog.py` | 처리 안 된 예외 → `crash.log`. exe 는 콘솔이 없어 이게 없으면 창이 흔적 없이 사라진다 · 세부는 머리말 |
| `updatecheck.py` | 새 버전 확인 + 앱 안 업데이트(SHA256 대조 후 설치) · 첨부 이름 `SETUP_ASSET` 은 `tools/release.py` 와 같아야 · 세부는 머리말 |
| `check_api.py` | 터미널 연결 점검 — GUI 띄우기 전 키·엔드포인트 확인용 |
| `tools/release.py` · `installer/피파전적관리.iss` | 배포판 — 빌드 → zip·설치 파일 → 개인정보 검사 → `gh release` 명령 **출력만**. `.iss` 는 UTF-8 BOM · `AppId` 불변 · 첨부 이름은 영문 · 세부는 `tools/release.py` 머리말 |
| `tests/` | pytest 없이 파일을 직접 실행 · **모든 `main()` 루프는 `watchdog.limit` 안에서**(새 테스트 파일도) · 파일별 범위는 `tests/README.md` |
| `tools/season_notice.py` · `tools/check_predictions.py` | 시즌 종료일 공지(본문 한 줄 바꾼 파일 + `gh release edit` **출력만**) · 시즌 뒤 예측 대조 · 세부는 `tools/season_notice.py` 머리말 |
| `tools/review_kit.py` | 계획 검토 준비(ROADMAP "검토 방법" 11~13) — `bundle`(절이 짚은 코드 조각 · 정의/쓰임만/없음) · `diff`(회차 사이 바뀐 줄 + 진입점 표 행) · `ledger`(주장 장부 빈 표). 검토자에게 파일을 통째로 읽히지 않으려고 |
| `tools/dev_archive.py` | **개발용 상시 수집(이 PC 전용 · 배포판 밖)** — 작업 스케줄러 `FifaDevArchive` · `.env` `DEV_NEXON_API_KEY` · ⚠ 공용 fifa.db 를 연다 — store 스키마를 바꾸는 단계에선 `--only 1,2` · 세부는 머리말 |

```powershell
python check_api.py <닉네임>   # API 점검
python rankcollect.py --pages 3   # 랭킹 수집 실제 3쪽(저장 안 함 — 웹 데이터가 켜져 있어야)
python app_main.py             # 앱 실행
python tests/test_ui_smoke.py  # 화면 배선 스모크(offscreen, 네트워크 없음 — 글꼴 폴더는 테스트가 기본으로 준다)
python tests/test_tray.py      # 트레이·종료 진입점·한 번만 실행·자동 실행(가짜 레지스트리)
python tests/test_trades.py    # 거래 받기(끊김·겹침·429·키 바뀜) · 시세 캐시 하루 상한 · 랭커 기록 캐시 — 가짜 목록(네트워크 없음)
python tests/test_traits.py    # 포지션 특성 — 이름표·팀 칸 해석·팀 찾기·요청 오류 분류 · trait_squads 저장·지우기(가짜 응답·가짜 세션, 네트워크 없음)
python tests/test_timeline.py  # 스쿼드 타임라인 · 가계부(짝·보유 상태·경계·앞뒤 창) — 지어낸 경기·거래(네트워크 없음)
python tests/test_predict.py   # 시즌 말 예측 — 가짜 스냅숏(네트워크 없음)
python tests/test_rankmeta.py  # 랭커 메타 ② 스냅숏 읽기(하루 승률·가성비·구단가치·구간 평균·승률→점수·주간 요약·순위 변동) — 지어낸 rank.db(네트워크 없음)
# 팀컬러 효과(2.2.1) 파싱·가르기는 test_parsing(실목록 픽스처 teamcolor_list.html.gz) · 창은 test_ui_smoke · 실제 넥슨은 check_api 의 팀컬러 줄
python tests/test_rankerpick.py   # 선수 색인 · 랭커 픽 받기(상한·429·닉네임 바뀜) · 지우기(파일 바이트까지) — 가짜 API(네트워크 없음)
python tools/season_notice.py 2026-11-12   # 종료일 공지 — 바꾼 본문 파일 + gh 명령 출력(공개는 사람이)
python tests/test_window_size.py  # 창 크기 — FHD 100·125·150%·1366·175% 를 흉내 내 실제 창을 띄워 잰다
# 메뉴별 화면을 PNG 로 떠서 눈으로 볼 때 — 최근 검색 칩에 실제 닉네임이 찍히니 확인 후 지운다
$env:UI_SHOT="<스크래치 폴더>"; python tests/test_ui_smoke.py
python -m PyInstaller --noconfirm 피파전적관리.spec   # exe → dist\피파전적관리\ (확인용)
python tests/test_release.py   # 배포 검사 로직(빌드 없이)
python tests/test_rules.py     # 규칙 검사(정적 + SQL 실행 계획 — 파싱·수집 테스트를 같이 돌려 쿼리를 모은다)
python tools/review_kit.py bundle "## 2.1.1"   # 계획 검토 근거 묶음 (diff <커밋> · ledger <절> 도)
python tools/dev_archive.py --status   # 개발용 상시 수집 현황(이 PC 전용 — 작업 스케줄러 FifaDevArchive 가 로그온·09:00 에 돈다)
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

> 일반 Qt 함정(`NoScrollComboBox` · 위젯 import 확인 · 네트워크는 UI 스레드 밖 · 닫을 때 워커 정리 · 재할당 전역은 모듈 경유)은
> 전역 규칙 8번. 아래 번호는 코드·테스트가 "PyQt N" 으로 가리키니 **바꾸지 않는다**. 언제 어떻게 당했나는 `docs/LESSONS.md`.

1. **후처리가 있는 표는 `_fill(..., enable_sort=False)` 로 채우고 후처리 뒤에만 `setSortingEnabled(True)`** — 다시 켜는 순간 Qt 가
   이전 정렬로 즉시 재정렬해, "채운 순서 = 원본 순서"로 칠한 색이 엉뚱한 행에 간다(두 번째 렌더부터만 터진다).
2. **값 비례 배경색은 알파 대신 `T.PANEL` 과 섞은 불투명 색** — 알파는 줄무늬 행 색과 섞여 같은 값이 행마다 다르게 보인다.
3. **전역 QSS 에 `font-size`/`font-family` 금지** — QSS 글꼴이 `setFont()` 를 이긴다. 기본 글꼴은 `theme.apply()` 의
   `QApplication.setFont`. `test_setfont_sizes_survive_stylesheet`.
4. **`FitTableWidget` 열 여백은 `_measure_pad()` 로 Qt 에 묻는다**(상수 금지 — 표 QSS 의 셀 padding). `test_player_table_cells_not_elided`.
5. **창은 `MIN_WINDOW`(1280×720) 밑으로 안 줄고 그 크기에서 아무것도 안 잘린다** — 모든 표는 `_make_table` → `FitTableWidget`
   (`_fill` 끝 `refit()` · 안 거치는 표는 직접) · 정렬 화살표 자리는 정렬 중인 열에만 · 위쪽 바 `WrapBar` · 페이지 `VScrollArea`(세로만) ·
   큰 숫자 `FitLabel` · 명시적 `setMinimumWidth` 대신 `sizeHint()`. ⚠ 가로 스크롤이 보이면 원인부터 재 본다.
6. **offscreen 테스트에서 모달은 안 닫힌다(회귀가 "멈춤"으로 나온다)** — `test_ui_smoke.py` 머리가 `QMessageBox.*`·`QDialog.exec`·
   `QApplication.exec` 를 `ModalCalled` 로 막는다. 새 종류의 모달(`QFileDialog`·`QInputDialog`·`QMenu.exec` 등)을 쓰면 거기 한 줄.
7. **화면은 보이는 자리(메뉴·탭)만 그린다 — 새 메뉴·탭은 `VIEW_OF_KEY`·`_renderers()` 에**(뺄 자리는 `VIEW_EXEMPT` 에 이유와).
   표에 없으면 예외 없이 조용히 안 그려진다(`test_every_nav_page_has_a_renderer`). 다시 그리기는 직접 `_render_x()` 대신 `_invalidate("키")`.
   `_go_page` 는 없는 이름이면 아무것도 안 한다 — 테스트 안 이름은 `test_go_page_literals_in_tests_exist` 가 대조.
   대시보드·흐름 분석은 `_narrate_scope()` 캐시를 같이 쓴다(키: 목록 id·길이·맨 앞 — 제자리 수정은 안 잡힌다). 테스트는 `LAZY_RENDER = False`.
8. **스레드 → 화면 신호에 큰 데이터는 `pyqtSignal(object)`** — `list`·`dict` 선언은 통째로 깊은 복사(1만 경기 4.5GB · 6초).
   메모리는 실제 경로(exe 프로세스)로 잰다. `test_loader_signals_pass_objects_without_copying`.
9. **창·대화상자 크기는 화면(배율·작업 표시줄 반영)에 맞춘다** — 새 대화상자는 부모를 주고 `fit_to_screen(dlg, w, h)`
   (메인 창 위 가운데 · 메인 창이 움직이면 따라감 · 내용 최소 높이가 657 넘으면 세로 스크롤 안에). 창은 `initial_window`·`_apply_plan`
   (최소 크기 → 저장 크기 순)·`_check_fits`·`_refit`. `tests/test_window_size.py` 가 흉내 낸 화면 5종에서 잰다.

## 작업 방식 (이 앱 고유분)

> 난이도별 작업 방식·TaskList·Git·문서 규칙은 전역 `~/.claude/CLAUDE.md`.

- **커밋 후 push 까지 자동으로 진행한다** (원격이 붙어 있으면). 이 프로젝트만의 관습이다.
- `.gitignore` 대상 중 이 앱 고유: **`.env`**(API 키) · **`.cache/`**(넥슨 응답 캐시).
- **CSV 로 내보내는 기능을 추가하면 `encoding="utf-8-sig"`** 를 쓴다 — Excel 이 BOM 없는
  UTF-8 을 못 알아본다. 닉네임·구단명·선수명이 전부 한글이라 **첫 사용에서 바로 깨진
  화면을 보게 된다.** (2026-08-15 전수 점검: 이 프로젝트 인코딩 위험 지점 0건 — 유지한다.)

---

## 규칙은 테스트로 (`tests/test_rules.py` — 2026-10-05)

계획 검토 때 검토자가 매번 눈으로 대조하던 규칙을 기계로 옮겼다 — 여기 있는 것은 **검토에서 다시 보지 않는다**.
`QComboBox` 직접 생성 · 대화상자 `fit_to_screen` · 글자 파일·`subprocess(text=True)` 의 `encoding=` · `pyqtSignal(list/dict)` ·
앱이 쓰는 모달이 화면 스모크 차단에 있는지 · **색은 `theme.py` 에서만** · **모든 SQL 의 실행 계획에 큰 표 `TEMP B-TREE` 없음**.
- 규칙마다 **위반을 심은 가짜 소스를 잡는지** 같이 단언한다(빈 검사 방지). 새 규칙도 그 꼴로.
- SQL 은 파싱·수집 테스트를 그대로 돌리며 trace 로 모은다 — **SELECT 를 품은 함수가 한 번도 안 돌면 FAIL**(검사 밖이 생기지 않게).
  새 읽기 함수를 그 두 테스트가 안 부르면 `_drive_uncovered()` 에 한 줄.
- 예외는 표로만(`COLOR_ALLOW` · `SQL_SMALL_TABLES` · `SQL_TEMP_ALLOW`) — 줄마다 이유. 첫 실행에서 grep 이 놓친 색 13곳이
  나왔다(강화 등급 배지·축구장 선 → `theme.GRADE_BADGES`·`PITCH_LINE`, 값 그대로).

## 알려진 버그 — 해결 이력

2026-07-18 팀컬러 세션의 코드 리뷰 결과(전부 수정)는 `docs/LESSONS.md` 로 옮겼다.

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
