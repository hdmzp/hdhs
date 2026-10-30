// 텔레그램 봇 (Cloudflare Worker) - 버튼을 누르면 바로 답한다.
//
// 텔레그램이 메시지를 이 Worker 로 바로 보내고(webhook), 구독 설정은 Cloudflare KV 에
// 저장한다. 아침 발송(GitHub Actions)은 /export 로 구독자 목록을 받아 간다.
//
// 설정 (Cloudflare 대시보드 → 이 Worker → Settings)
//   Bindings  KV namespace  변수 이름 SUBS
//   Secret    BOT_TOKEN     BotFather 봇 토큰
//   Secret    EXPORT_KEY    아무 긴 문자열 (GitHub Secrets 의 SUBS_KEY 와 같게)
//   Variable  ADMIN_IDS     관리자 텔레그램 ID, 쉼표 구분 (새 구독자 알림·/list)
// 코드를 배포한 뒤 브라우저로 https://<worker 주소>/setup?key=<EXPORT_KEY> 를 한 번 열면
// 텔레그램 webhook 과 명령어 메뉴가 연결된다.
//
// 명령: /start (알림·카테고리 고르기) /brand 브랜드명 /unbrand 브랜드명 /status /stop /list(관리자)
// 카테고리·메시지 키는 notify/subscribers.py 와 같아야 한다.

const CATEGORIES = [
  ["living", "리빙/주방·가전"],
  ["fashion", "의류·언더웨어·잡화/주얼리"],
  ["food", "일반식품·건강식품"],
  ["beauty", "미용"],
];
const CATEGORY_NAMES = Object.fromEntries(CATEGORIES);
const MESSAGE_NAMES = { celeb: "셀럽PGM 알림", category: "카테고리 알림", both: "셀럽PGM + 카테고리 알림" };
const BRAND_MAX = 20;

const HELP =
  "📮 홈쇼핑 모니터링 알림봇\n\n" +
  "/start  알림 신청·변경\n" +
  "/brand 브랜드명  특정 브랜드 방송 알림 (예: /brand 설화수)\n" +
  "/unbrand 브랜드명  브랜드 알림 빼기\n" +
  "/status  내 설정 보기\n" +
  "/stop  알림 그만 받기";

const COMMANDS = [
  { command: "start", description: "알림 신청·변경" },
  { command: "status", description: "내 설정 보기" },
  { command: "brand", description: "브랜드 방송 알림 추가 (/brand 설화수)" },
  { command: "unbrand", description: "브랜드 알림 빼기" },
  { command: "stop", description: "알림 그만 받기" },
];

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const nowIso = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 19) + "+09:00";

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) console.log(method, r.status, await r.text());
  return r;
}

const send = (env, chatId, text, markup) =>
  tg(env, "sendMessage", {
    chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true,
    ...(markup ? { reply_markup: markup } : {}),
  });

async function webhookSecret(env) {
  const data = new TextEncoder().encode("wh:" + env.EXPORT_KEY);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 48);
}

const admins = (env) => (env.ADMIN_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
const getUser = async (env, id) => JSON.parse((await env.SUBS.get("u:" + id)) || "null");
const putUser = (env, id, u) => env.SUBS.put("u:" + id, JSON.stringify(u));

async function allUsers(env) {
  const users = {};
  let cursor;
  do {
    const page = await env.SUBS.list({ prefix: "u:", cursor });
    for (const k of page.keys) {
      const u = await env.SUBS.get(k.name);
      if (u) users[k.name.slice(2)] = JSON.parse(u);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return users;
}

function summary(u) {
  const lines = ["받는 알림: " + (MESSAGE_NAMES[u.messages] || "아직 안 고름")];
  if (u.messages === "category" || u.messages === "both") {
    const cats = CATEGORIES.filter(([k]) => (u.cats || []).includes(k)).map(([, n]) => n);
    lines.push("카테고리: " + (cats.length ? cats.join(", ") : "없음"));
  }
  if ((u.brands || []).length) lines.push("브랜드: " + u.brands.join(", "));
  return lines.join("\n");
}

const messagesKeyboard = () => ({
  inline_keyboard: [
    [{ text: "셀럽PGM 알림", callback_data: "m:celeb" }],
    [{ text: "카테고리 알림", callback_data: "m:category" }],
    [{ text: "둘 다", callback_data: "m:both" }],
  ],
});

function catsKeyboard(u) {
  const picked = new Set(u.cats || []);
  const rows = CATEGORIES.map(([k, n]) => [{ text: (picked.has(k) ? "✅ " : "⬜ ") + n, callback_data: "c:" + k }]);
  rows.push([{ text: "완료", callback_data: "done" }]);
  return { inline_keyboard: rows };
}

function who(u, id) {
  return (u.name || id) + (u.username ? ` (@${u.username})` : "");
}

async function finish(env, chatId, u) {
  await send(env, chatId, "✅ 설정 완료! 내일 아침부터 보내드려요\n\n" + esc(summary(u)) + "\n\n바꾸려면 /start, 그만 받으려면 /stop");
  if (!u.announced) {
    u.announced = true;
    for (const a of admins(env)) {
      if (a !== chatId) await send(env, a, "🆕 새 구독자: " + esc(who(u, chatId)) + "\n" + esc(summary(u)));
    }
  }
}

function newUser(from) {
  return {
    joined: nowIso(), cats: [], brands: [],
    name: [from.first_name, from.last_name].filter(Boolean).join(" "),
    username: from.username || "",
  };
}

async function onMessage(env, msg) {
  if (msg.chat?.type !== "private") return;
  const chatId = String(msg.chat.id);
  const text = (msg.text || "").trim();
  const sp = text.indexOf(" ");
  const cmd = (sp < 0 ? text : text.slice(0, sp)).split("@")[0].toLowerCase();
  const arg = sp < 0 ? "" : text.slice(sp + 1).trim();
  let u = await getUser(env, chatId);

  if (cmd === "/start") {
    const fresh = newUser(msg.from || {});
    u = u ? { ...u, name: fresh.name, username: fresh.username } : fresh;
    u.updated = nowIso();
    await putUser(env, chatId, u);
    await send(env, chatId, "📮 받을 알림을 골라주세요", messagesKeyboard());
  } else if (cmd === "/stop") {
    await env.SUBS.delete("u:" + chatId);
    await send(env, chatId, "알림을 그만 보낼게요. 다시 받으려면 /start");
  } else if (cmd === "/status") {
    await send(env, chatId, u ? esc(summary(u)) : "아직 신청 전이에요. /start 를 눌러주세요.");
  } else if (cmd === "/brand") {
    u = u || newUser(msg.from || {});
    if (!arg) {
      await send(env, chatId, "알림 받는 브랜드: " + (u.brands.length ? esc(u.brands.join(", ")) : "없음") + "\n추가는 /brand 브랜드명");
      return;
    }
    const brand = arg.slice(0, 30);
    if (!u.brands.includes(brand)) {
      if (u.brands.length >= BRAND_MAX) {
        await send(env, chatId, `브랜드는 ${BRAND_MAX}개까지 등록할 수 있어요.`);
        return;
      }
      u.brands.push(brand);
    }
    u.updated = nowIso();
    await putUser(env, chatId, u);
    await send(env, chatId, `🔔 '${esc(brand)}' 방송이 있는 날 아침에 알려드릴게요.\n지금 등록: ${esc(u.brands.join(", "))}`);
  } else if (cmd === "/unbrand") {
    if (u && (u.brands || []).includes(arg)) {
      u.brands = u.brands.filter((b) => b !== arg);
      u.updated = nowIso();
      await putUser(env, chatId, u);
      await send(env, chatId, `'${esc(arg)}' 브랜드 알림을 뺐어요.`);
    } else {
      await send(env, chatId, "등록된 브랜드가 아니에요. /brand 로 목록을 볼 수 있어요.");
    }
  } else if (cmd === "/list" && admins(env).includes(chatId)) {
    const users = await allUsers(env);
    const ids = Object.keys(users).sort((a, b) => (users[a].joined || "").localeCompare(users[b].joined || ""));
    const lines = [`👥 구독자 ${ids.length}명`, ""];
    for (const id of ids) {
      lines.push(`<b>${esc(who(users[id], id))}</b> · ${(users[id].joined || "").slice(0, 10)}`, esc(summary(users[id])), "");
    }
    await send(env, chatId, ids.length ? lines.join("\n").trim().slice(0, 4000) : "구독자가 없어요.");
  } else {
    await send(env, chatId, HELP);
  }
}

async function onCallback(env, cq) {
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
  const chatId = String(cq.message?.chat?.id || "");
  if (!chatId) return;
  const u = await getUser(env, chatId);
  if (!u) {
    await send(env, chatId, "먼저 /start 를 눌러주세요.");
    return;
  }
  const action = cq.data || "";
  u.updated = nowIso();
  if (action.startsWith("m:")) {
    u.messages = action.slice(2);
    if (u.messages === "celeb") await finish(env, chatId, u);
    else await send(env, chatId, "📂 받을 카테고리를 골라주세요 (여러 개 가능, 누를 때마다 켜고 꺼요)", catsKeyboard(u));
  } else if (action.startsWith("c:")) {
    const key = action.slice(2);
    if (CATEGORY_NAMES[key]) {
      u.cats = u.cats || [];
      u.cats = u.cats.includes(key) ? u.cats.filter((c) => c !== key) : [...u.cats, key];
      await tg(env, "editMessageReplyMarkup", { chat_id: chatId, message_id: cq.message.message_id, reply_markup: catsKeyboard(u) });
    }
  } else if (action === "done") {
    if ((u.messages === "category" || u.messages === "both") && !(u.cats || []).length) {
      await send(env, chatId, "카테고리를 하나 이상 골라주세요.", catsKeyboard(u));
    } else {
      await finish(env, chatId, u);
    }
  }
  await putUser(env, chatId, u);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const json = (obj, status = 200) =>
      new Response(JSON.stringify(obj, null, 1), { status, headers: { "content-type": "application/json; charset=utf-8" } });

    if (url.pathname === "/webhook" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== (await webhookSecret(env))) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      try {
        if (update.message) await onMessage(env, update.message);
        else if (update.callback_query) await onCallback(env, update.callback_query);
      } catch (e) {
        console.log("처리 실패", e && e.stack);
      }
      return new Response("ok"); // 실패해도 200 - 텔레그램이 같은 걸 계속 재전송하지 않게
    }

    if (url.pathname === "/export") {
      if (!env.EXPORT_KEY || request.headers.get("Authorization") !== "Bearer " + env.EXPORT_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      return json({ users: await allUsers(env) });
    }

    if (url.pathname === "/setup") {
      if (!env.EXPORT_KEY || url.searchParams.get("key") !== env.EXPORT_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      const hook = await tg(env, "setWebhook", {
        url: `${url.origin}/webhook`,
        secret_token: await webhookSecret(env),
        allowed_updates: ["message", "callback_query"],
      });
      await tg(env, "setMyCommands", { commands: COMMANDS });
      const result = await hook.json();
      return new Response(result.ok ? "✅ 텔레그램 webhook 연결 완료. 이제 봇에서 /start 를 눌러보세요."
                                    : "❌ 연결 실패: " + JSON.stringify(result),
                          { headers: { "content-type": "text/plain; charset=utf-8" } });
    }

    return new Response("hdhs telegram bot", { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
