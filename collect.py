"""등록 계정의 새 경기를 수집해 DB에 쌓는다 — GUI 없이 돌아간다.

    python collect.py            # 등록된 계정 전부
    python collect.py GB쭈우 맛술  # 지정한 닉네임만

작업 스케줄러가 이걸 주기적으로 부른다. 앱을 안 켜도 경기가 쌓이게 하려는 것 —
API는 최근 100경기까지만 주고, 이 계정은 20시간이면 100경기가 다 밀려난다.

스케줄러로 돌면 화면이 없으니, 무슨 일이 있었는지 .cache/collect.log 에 남긴다.
"""
from __future__ import annotations

import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):  # 한국어 콘솔(cp949)에서 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
import store
from nexon_api import FCOnlineAPI, NexonAPIError

LOG_PATH = config.CACHE_DIR / "collect.log"


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # 로그 실패가 수집을 막으면 안 된다


def collect_one(api: FCOnlineAPI, conn, ouid: str, nickname: str,
                match_type: int) -> int:
    """한 계정의 새 경기를 저장하고 저장한 수를 돌려준다."""
    ids = api.get_match_ids(ouid, match_type, 0, config.MAX_MATCH_LIMIT)
    have = store.existing_ids(conn, ids)
    todo = [i for i in ids if i not in have]
    if not todo:
        return 0

    fresh = []
    for mid in todo:
        try:
            fresh.append(api.get_match_detail(mid))
        except NexonAPIError as e:
            log(f"  [건너뜀] {mid}: {e.message}")
    saved = store.save_matches(conn, fresh)

    # 창이 밀려 이미 놓친 게 있는지 알린다 — 수집 주기를 조일 근거가 된다.
    if len(todo) >= config.MAX_MATCH_LIMIT:
        log(f"  [경고] {nickname}: 받은 {len(ids)}경기가 전부 새 경기다. "
            f"수집 간격이 길어 그 사이 경기를 놓쳤을 수 있다.")
    return saved


def main(argv: list[str]) -> int:
    if not config.API_KEY:
        log("[실패] .env 에 NEXON_API_KEY 가 없다.")
        return 1

    api = FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)
    conn = store.open_db(config.DB_PATH)
    try:
        if argv:
            targets = []
            for nick in argv:
                try:
                    targets.append((api.get_ouid(nick), nick))
                except NexonAPIError as e:
                    log(f"[실패] '{nick}' 조회: {e.message}")
        else:
            targets = [(a["ouid"], a["nickname"] or a["ouid"][:8])
                       for a in store.list_accounts(conn)]

        if not targets:
            log("등록된 계정이 없다. 앱에서 한 번 조회하면 자동으로 등록된다.")
            return 0

        total = 0
        for ouid, nickname in targets:
            try:
                n = collect_one(api, conn, ouid, nickname,
                                config.DEFAULT_MATCH_TYPE)
                total += n
                cnt = store.match_count(conn, ouid, config.DEFAULT_MATCH_TYPE)
                log(f"{nickname}: 새 경기 {n}건 저장 · 누적 {cnt}경기")
                # 닉네임이 바뀌었을 수 있으니 최신값으로 갱신해 둔다.
                try:
                    basic = api.get_user_basic(ouid)
                    if basic.get("nickname"):
                        store.upsert_account(conn, ouid, basic["nickname"])
                except NexonAPIError:
                    pass
            except NexonAPIError as e:
                # 한 계정이 실패해도 나머지는 계속한다.
                log(f"[실패] {nickname}: {e.message} (code={e.code})")
        log(f"수집 완료 — 계정 {len(targets)}개, 새 경기 {total}건")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
