/**
 * 카카오톡 오픈채팅 봇 어댑터 — 메신저봇R 스크립트.
 *
 * 방에서 오간 메시지를 bot/server.py 로 넘기고, 돌아온 텍스트를 방에 답한다.
 * 전적 조회·집계·쿨다운은 전부 서버가 한다 — 이 파일은 배달만 맡는다.
 *
 * PC 안드로이드 에뮬레이터와 실제 폰 양쪽에서 같은 파일을 쓴다. 다른 건
 * 아래 SERVER 주소 한 줄뿐이다.
 *
 * 설치·연결 방법은 adapters/README.md 참고.
 *
 * 주의: 메신저봇R 은 Rhino 엔진(ES5)이다. let/const, 화살표 함수,
 * 템플릿 문자열을 쓰면 컴파일되지 않는다 — var 와 function 만 쓴다.
 */

// ── 설정 ────────────────────────────────────────────────────────────────
// 에뮬레이터 종류에 따라 PC 를 가리키는 주소가 다르다(README 의 표 참고).
//   안드로이드 스튜디오 AVD : http://10.0.2.2:8765
//   LDPlayer·녹스·MEmu·실제 폰 : http://<PC 내부 IP>:8765   예) 192.168.0.5
var SERVER = "http://10.0.2.2:8765";

var PREFIX = "!";              // 서버(bot/commands.py)의 PREFIX 와 같아야 한다
var CONNECT_TIMEOUT_MS = 5000;
// 쌓인 경기가 많은 계정의 첫 조회가 몇 초 걸린다(실측 3,772경기에 2.8초).
// 넉넉히 잡지 않으면 정상 응답을 타임아웃으로 버리게 된다.
var READ_TIMEOUT_MS = 60000;

// 서버가 꺼져 있을 때 명령마다 오류를 답하면 방이 도배된다. 한 번 알리고
// 이 시간 동안은 조용히 넘긴다.
var ERROR_NOTICE_INTERVAL_MS = 300000;  // 5분
var ERROR_TEXT = "봇 서버에 연결하지 못했습니다. PC에서 서버가 켜져 있는지 확인해 주세요.";

var _lastErrorNotice = 0;


// ── 순수 로직 (Java 에 기대지 않는다 — tests/test_adapter.js 가 이걸 돌린다) ──

/** 서버로 넘길 메시지인지. 명령이 아니면 네트워크를 아예 타지 않는다. */
function shouldHandle(text) {
    return typeof text === "string"
        && text.length > PREFIX.length
        && text.substring(0, PREFIX.length) === PREFIX;
}

/** 앞뒤 공백 제거 — 카톡에서 " !전적" 처럼 쳐도 먹게. (ES5 라 trim 자작) */
function clean(value) {
    if (value === null || value === undefined) {
        return "";
    }
    return String(value).replace(/^\s+/, "").replace(/\s+$/, "");
}

/**
 * 서버 응답 → 답장할 텍스트. 답하지 않을 경우 null.
 * 서버는 봇이 반응하지 않을 메시지에 {"reply": null} 을 준다.
 */
function parseReply(raw) {
    if (!raw) {
        return null;
    }
    var data;
    try {
        data = JSON.parse(raw);
    } catch (e) {
        return null;  // 서버가 아닌 무언가(프록시 오류 페이지 등)가 답한 경우
    }
    if (!data || typeof data.reply !== "string" || data.reply.length === 0) {
        return null;
    }
    return data.reply;
}

/** 오류 안내를 지금 해도 되는지 — 도배 방지. now 는 테스트가 넣어 준다. */
function shouldNotifyError(now) {
    if (now - _lastErrorNotice < ERROR_NOTICE_INTERVAL_MS) {
        return false;
    }
    _lastErrorNotice = now;
    return true;
}


// ── HTTP (Java 표준 라이브러리만 — Jsoup 등 외부 의존 없음) ──────────────
function postJson(url, payload) {
    var conn = new java.net.URL(url).openConnection();
    try {
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
        conn.setReadTimeout(READ_TIMEOUT_MS);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");

        var out = conn.getOutputStream();
        out.write(new java.lang.String(JSON.stringify(payload)).getBytes("UTF-8"));
        out.flush();
        out.close();

        var status = conn.getResponseCode();
        var stream = status < 400 ? conn.getInputStream() : conn.getErrorStream();
        var reader = new java.io.BufferedReader(
            new java.io.InputStreamReader(stream, "UTF-8"));
        var buffer = new java.lang.StringBuilder();
        var line = reader.readLine();
        while (line != null) {
            buffer.append(line);
            line = reader.readLine();
        }
        reader.close();
        return String(buffer.toString());
    } finally {
        conn.disconnect();
    }
}


// ── 메신저봇R 진입점 ────────────────────────────────────────────────────
function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
    var text = clean(msg);
    if (!shouldHandle(text)) {
        return;
    }

    var raw;
    try {
        raw = postJson(SERVER + "/message", {
            room: clean(room),
            sender: clean(sender),
            text: text
        });
    } catch (e) {
        if (shouldNotifyError(Date.now())) {
            replier.reply(ERROR_TEXT);
        }
        return;
    }

    var reply = parseReply(raw);
    if (reply !== null) {
        replier.reply(reply);
    }
}


// 메신저봇R(Rhino)에는 module 이 없다 — node 로 돌리는 테스트에서만 잡힌다.
if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        shouldHandle: shouldHandle,
        clean: clean,
        parseReply: parseReply,
        SERVER: SERVER,
        PREFIX: PREFIX
    };
}
