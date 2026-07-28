/**
 * 어댑터(adapters/kakao_bot.js) 회귀 테스트 — node 로 돌린다.
 *
 *   node tests/test_adapter.js
 *
 * 어댑터는 메신저봇R(Rhino)에서 Java 표준 라이브러리로 HTTP 를 부른다.
 * 여기서는 그 java.* 를 가짜로 만들어 스크립트를 통째로 실행한다 — 덕분에
 * 실제 진입점 response() 를 그대로 부르면서 "무엇을 보냈고, 무엇을 답했나"를
 * 네트워크 없이 볼 수 있다. 문법 오류(Rhino 는 ES5 라 let/화살표 금지)도
 * 여기서 걸린다.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "adapters", "kakao_bot.js");

/** 가짜 java.* — 보낸 요청을 기록하고 정해 둔 응답을 돌려준다. */
function makeJava(options) {
    const opts = options || {};
    const log = { url: null, method: null, body: null, headers: {}, timeouts: {} };

    const connection = {
        setRequestMethod(m) { log.method = m; },
        setDoOutput() {},
        setConnectTimeout(t) { log.timeouts.connect = t; },
        setReadTimeout(t) { log.timeouts.read = t; },
        setRequestProperty(k, v) { log.headers[k] = v; },
        getOutputStream() {
            return {
                write(bytes) { log.body = bytes; },
                flush() {},
                close() {},
            };
        },
        getResponseCode() {
            if (opts.throws) throw new Error("연결 실패(가짜)");
            return opts.status || 200;
        },
        getInputStream() { return opts.body; },
        getErrorStream() { return opts.body; },
        disconnect() { log.disconnected = true; },
    };

    const java = {
        net: {
            URL: function (u) {
                log.url = u;
                this.openConnection = () => connection;
            },
        },
        lang: {
            // new java.lang.String(s).getBytes("UTF-8") → 문자열 그대로 기록
            String: function (s) { this.getBytes = () => String(s); },
            StringBuilder: function () {
                let acc = "";
                this.append = (part) => { acc += part; return this; };
                this.toString = () => acc;
            },
        },
        io: {
            InputStreamReader: function (stream) { this.stream = stream; },
            BufferedReader: function (reader) {
                const lines = reader.stream === null || reader.stream === undefined
                    ? [] : String(reader.stream).split("\n");
                let i = 0;
                this.readLine = () => (i < lines.length ? lines[i++] : null);
                this.close = () => {};
            },
        },
    };
    return { java, log };
}

/** 어댑터를 새 컨텍스트에 로드한다 — 테스트마다 전역 상태가 깨끗하다. */
function load(options) {
    const { java, log } = makeJava(options);
    const replies = [];
    const sandbox = { java, module: undefined, console };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

    const replier = { reply: (text) => replies.push(text) };
    return {
        log,
        replies,
        send(room, msg, sender) {
            sandbox.response(room, msg, sender, true, replier, null, "com.kakao.talk");
        },
        ctx: sandbox,
    };
}

// ── 테스트 ──────────────────────────────────────────────────────────────
const tests = {};

tests.명령이_아니면_네트워크를_타지_않는다 = () => {
    const t = load({ body: '{"reply": "안 와야 한다"}' });
    t.send("방", "그냥 잡담", "나");
    t.send("방", "", "나");
    t.send("방", "!", "나");          // 접두사만 있고 명령이 없다
    assert(t.log.url === null, "요청을 보냈다: " + t.log.url);
    assert(t.replies.length === 0, "답장했다: " + t.replies);
};

tests.명령을_서버_계약대로_보낸다 = () => {
    const t = load({ body: '{"reply": "[홍길동] 챔피언스"}' });
    t.send("피파방", "!전적 홍길동", "카톡닉");

    assert(t.log.url === "http://10.0.2.2:8765/message", t.log.url);
    assert(t.log.method === "POST", t.log.method);
    assert(/application\/json/.test(t.log.headers["Content-Type"]),
        JSON.stringify(t.log.headers));

    const sent = JSON.parse(t.log.body);
    assert(sent.room === "피파방", t.log.body);
    assert(sent.sender === "카톡닉", t.log.body);
    assert(sent.text === "!전적 홍길동", t.log.body);
    assert(t.replies.length === 1 && t.replies[0] === "[홍길동] 챔피언스", t.replies);
    assert(t.log.disconnected === true, "커넥션을 닫지 않았다");
};

tests.앞뒤_공백을_털고_보낸다 = () => {
    const t = load({ body: '{"reply": "ok"}' });
    t.send("방", "  !전적 홍길동  ", "나");
    assert(JSON.parse(t.log.body).text === "!전적 홍길동", t.log.body);
};

tests.reply가_null이면_침묵한다 = () => {
    const t = load({ body: '{"reply": null}' });
    t.send("방", "!없는명령", "나");
    assert(t.log.url !== null, "요청은 보내야 한다");
    assert(t.replies.length === 0, "답장했다: " + t.replies);
};

tests.깨진_응답에도_죽지_않는다 = () => {
    const t = load({ body: "<html>프록시 오류</html>" });
    t.send("방", "!전적 홍길동", "나");
    assert(t.replies.length === 0, "답장했다: " + t.replies);
};

tests.서버가_꺼져_있으면_한_번만_알린다 = () => {
    const t = load({ throws: true });
    t.send("방", "!전적 홍길동", "나");
    assert(t.replies.length === 1, "첫 오류를 안 알렸다: " + t.replies);
    assert(/연결하지 못했습니다/.test(t.replies[0]), t.replies[0]);

    // 서버가 꺼진 동안 명령마다 오류를 답하면 방이 도배된다
    t.send("방", "!전적 홍길동", "나");
    t.send("다른방", "!최근 홍길동", "나");
    assert(t.replies.length === 1, "오류를 반복해 알렸다: " + t.replies);
};

tests.타임아웃이_서버_응답시간을_감당한다 = () => {
    const t = load({ body: '{"reply": "ok"}' });
    t.send("방", "!전적 홍길동", "나");
    // 쌓인 경기가 많은 계정의 첫 조회가 몇 초 걸린다(실측 2.8초).
    assert(t.log.timeouts.read >= 60000, "읽기 타임아웃이 짧다: " + t.log.timeouts.read);
};

tests.서버와_접두사가_같다 = () => {
    const t = load({ body: '{"reply": "ok"}' });
    // 서버(bot/commands.py)의 PREFIX 와 어긋나면 명령이 조용히 무시된다
    assert(t.ctx.PREFIX === "!", t.ctx.PREFIX);
};

tests.Rhino가_읽을_수_있는_ES5다 = () => {
    // 메신저봇R 은 Rhino 엔진이라 ES6 문법에서 컴파일이 깨진다. node 는
    // 최신 문법을 다 파싱해 주므로 위 테스트들이 통과해도 못 잡는다 —
    // 실제로 폰에 올린 뒤에야 알게 되는 종류의 사고라 여기서 막는다.
    const source = fs.readFileSync(SCRIPT, "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")   // 블록 주석 제외
        .replace(/^\s*\/\/.*$/gm, "");      // 한 줄 주석 제외
    const banned = [
        [/\blet\s+\w/, "let"],
        [/\bconst\s+\w/, "const"],
        [/=>/, "화살표 함수"],
        [/`/, "템플릿 문자열"],
        [/\bclass\s+\w/, "class"],
        [/\.\.\./, "spread"],
    ];
    for (const [pattern, label] of banned) {
        assert(!pattern.test(source), "Rhino 가 못 읽는 문법: " + label);
    }
};

tests.실제_서버와_주고받는다 = () => {
    // 서버가 안 떠 있으면 건너뛴다 — 오프라인에서도 이 파일 전체가 돌아야 한다.
    const health = curl("http://127.0.0.1:8765/health");
    if (health === null) {
        console.log("       (서버가 꺼져 있어 건너뜀 — python -m bot.server 로 켜면 확인)");
        return;
    }
    assert(JSON.parse(health).ok === true, health);

    // 어댑터가 실제로 만든 요청 바디를 그대로 진짜 서버에 보낸다
    const t = load({ body: '{"reply": null}' });
    t.send("통합테스트방", "!도움", "나");
    const raw = curl("http://127.0.0.1:8765/message", t.log.body);
    assert(raw !== null, "서버가 응답하지 않았다");
    const reply = JSON.parse(raw).reply;
    assert(reply && reply.indexOf("!전적") >= 0, raw);
};

/** 동기 HTTP — 테스트 흐름을 단순하게 두려고 curl 을 쓴다(윈도우 10+ 기본 탑재). */
function curl(url, body) {
    const args = ["-s", "--max-time", "90", url];
    if (body !== undefined) {
        args.push("-X", "POST", "-H", "Content-Type: application/json",
            "--data-binary", "@-");
    }
    const res = require("child_process").spawnSync("curl", args, {
        input: body, encoding: "utf8",
    });
    if (res.status !== 0 || !res.stdout) {
        return null;
    }
    return res.stdout;
}

// ── 실행기 ──────────────────────────────────────────────────────────────
function assert(ok, message) {
    if (!ok) throw new Error(message || "assert 실패");
}

let failed = 0;
for (const name of Object.keys(tests)) {
    try {
        tests[name]();
        console.log("[OK]   " + name);
    } catch (e) {
        failed += 1;
        console.log("[FAIL] " + name + ": " + e.message);
    }
}
const total = Object.keys(tests).length;
console.log("\n" + (total - failed) + "/" + total + " 통과");
process.exit(failed ? 1 : 0);
