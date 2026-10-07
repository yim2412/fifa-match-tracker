# 테스트 파일 — 무엇을 재나

(CLAUDE.md 파일 표에서 옮김 — 2026-10-07)

`test_parsing.py`(파싱·집계·시즌 골든) · `test_analysis.py` · `test_ui_smoke.py`(offscreen 화면 배선 — FHD 가상 화면) ·
`test_window_size.py`(흉내 낸 화면 5종 `tests/screens/*.json` 에서 실제 창 크기, 화면마다 별도 프로세스 — 자식은 `FIFA_DATA_DIR` 임시 폴더로, 안 주면 실제 fifa.db 를 열었다) ·
`test_rankcollect.py`(수집기 — 가짜 목록) · `test_trades.py`(거래 받기·시세 캐시·랭커 기록 캐시 — 가짜 거래·랭커 목록) ·
`test_traits.py`(포지션 특성 단계 1 — 이름표 · 팀 칸 응답 해석·구조 검사 · 맞는 팀 찾기(멈춤·stale·강화 다른 카드) · 요청 오류 분류 — 실응답 한 칸 픽스처 `squadmaker_team.json.gz` · 가짜 세션) ·
`test_timeline.py`(타임라인·가계부 — 지어낸 경기·거래 · 1만 경기 예산) · `test_rankerpick.py`(선수 색인·백필 · 랭커 픽 받기 규칙 · 지우기 — 가짜 API ·
2만 경기 예산) · `test_predict.py`(예측 — 가짜 스냅숏) · `test_rankmeta.py`(랭커 메타 ② — 지어낸 rank.db · 1만 행 × 14스냅숏 CPU 예산) ·
`test_tray.py`(트레이·종료·한 번만 실행·자동 실행 — 창과 엮이는 X 숨김·내려놓기는 `test_ui_smoke`) · `test_release.py`(배포 검사) ·
`test_rules.py`(규칙 검사 — 아래 "규칙은 테스트로") · `test_review_kit.py`. pytest 없이 파일을 직접 실행.
**모든 `main()` 루프는 `watchdog.limit`** 안에서 테스트를 돈다 — 하나가 300초(`TEST_LIMIT_S`)를 넘기면 이름·호출 스택을 찍고 종료 코드 3(2026-10-06 스모크가 실패 대신 18분 멈췄다 ·
재현 못 함). 새 테스트 파일도 같은 루프로
