# 당한 것들 — CLAUDE.md 에서 옮긴 이야기

> CLAUDE.md 에는 규칙 한 줄만 남기고, **언제 어떻게 당했나**는 여기에 둔다(2026-10-07 — 매 세션 자동으로 읽히는 양을 줄이려고).
> 이 문서는 갱신하지 않는다 — 새로 당한 것은 아래에 절을 더한다.

## PyQt6 규칙 — 원문 (실수 방지 — 실제로 당한 것들)

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

7. **화면은 보이는 자리(메뉴, 탭)만 그린다 — 새 메뉴·탭은 `VIEW_OF_KEY`·`_renderers()` 에 넣는다**(2026-10-02 · 2.1.1 탭 단위).
   예전엔 `_render_all` 이 18개 메뉴를 매번 다 그려 시즌 "전체"(1만 경기) 전환에 3.2~3.5초 창이 굳었다
   → 지금 0.55초. `_render_all` 은 전부 낡음 표시 + 보이는 것만, 나머지는 `_on_nav_changed`·`_on_tab_changed` 에서 그린다.
   - **`_go_page(메뉴, 탭)` 은 없는 이름이면 아무것도 안 한다**(예외 없음 — 예전엔 묶음 제목 줄로 갔다). 탭이 None 이면 첫 탭.
     2.1.1 에서 메뉴 이름을 옮기자 테스트 넷이 옛 이름으로 `_go_page` 를 불러 **엉뚱한 화면을 잰 채 통과**하던 것이 하나 있었다 —
     테스트 안의 `_win._go_page("…")` 이름은 `test_go_page_literals_in_tests_exist` 가 NAV 와 대조한다.
   - 표에 안 넣은 자리는 **조용히 안 그려진다**(예외 없음). 일부러 뺀 자리는 `VIEW_EXEMPT` 에 이유와 함께 —
     `test_every_nav_page_has_a_renderer` 가 `NAV` 와 두 표·`_renderers()` 를 대조한다(2026-10-06 추가, 변이 3건 FAIL 확인). 데이터가 바뀌어 한 메뉴만 다시 그려야 하면
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
   **새 대화상자는 부모(메인 창)를 주고 `fit_to_screen(dlg, w, h)`** 로 연다 — 크기뿐 아니라 **메인 창 위 가운데에 직접 놓는다**
   (`place_over_parent` · 최소화 중이면 돌아올 자리 기준), 메인 창이 움직이면 `moveEvent → _follow_dialogs` 가 열린 대화상자를 끌고 간다
   (2026-10-06 사용자: 안내 창이 프로그램과 다른 모니터에 — 메인 창만 옮겨져 주 모니터에 홀로 남았다. 다시 묻는 안내는 최소화 중엔 미룬다). 그리고 내용 최소 높이가 657(150% 노트북 창 안쪽)을 넘으면 세로 스크롤
   안에 넣는다(스쿼드 창이 그랬다). `tests/test_window_size.py` 가 흉내 낸 화면 5종에서 실제 창으로 잰다.

---

## 알려진 버그 — 해결 이력(2026-07-18) — 원문

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

