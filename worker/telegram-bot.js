// 텔레그램 알림 신청 봇 (Cloudflare Worker, 무료) - START 를 누르면 바로 답한다.
//
// 흐름
//   1) 신청자: START → 받을 알림을 버튼으로 고르고 [신청하기]
//   2) 관리자: "🆕 알림 신청" + [승인] [거절] 버튼
//   3) 승인되면 신청자에게 "내일 아침부터 보내드려요" - 아침 발송(GitHub Actions)이 /export 로 목록을 받아 간다
//   이미 받는 사람이 START 로 알림을 바꾸면 다시 승인을 받는다 (승인 전까지는 예전 알림을 계속 받음).
//   /stop 은 알림 끊기, 관리자 /list 는 받는 사람 목록, 관리자 /test 는 신청 흐름을 혼자 미리 보기.
//   관리자 본인이 START 로 고르면 승인 없이 바로 적용된다.
//
// 설정
//   BOT_TOKEN  (Cloudflare Secret) BotFather 봇 토큰 = GitHub Secrets 의 TELEGRAM_BOT_TOKEN
//   ADMIN_ID   (wrangler.jsonc)    관리자 텔레그램 ID (여러 명이면 쉼표)
//   SUBS       (wrangler.jsonc)    KV 저장공간 - 신청·승인 상태
// 배포 후 https://<worker 주소>/setup 을 한 번 열면 텔레그램과 연결된다.
// 알림 이름(키)은 notify/subscribers.py 의 TOPICS 와 같아야 한다.

const TOPICS = [
  ["food", "식품 (일반식품·건강식품)"],
  ["celeb", "셀럽PGM (어제 운영상품)"],
  ["fashion", "패션 (의류·언더웨어·잡화/주얼리)"],
  ["living", "리빙 (리빙/주방·가전)"],
  ["beauty", "미용"],
];
const TOPIC_SHORT = { food: "식품", celeb: "셀럽PGM", fashion: "패션", living: "리빙", beauty: "미용" };

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
const edit = (env, msg, text, markup) =>
  tg(env, "editMessageText", {
    chat_id: msg.chat.id, message_id: msg.message_id, text, parse_mode: "HTML",
    disable_web_page_preview: true, ...(markup ? { reply_markup: markup } : {}),
  });

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const admins = (env) => String(env.ADMIN_ID || "").split(",").map((s) => s.trim()).filter(Boolean);
const today = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const names = (topics) => (topics || []).map((t) => TOPIC_SHORT[t]).filter(Boolean).join(", ") || "없음";

async function sha(text) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
// webhook 주소로 아무나 가짜 메시지를 못 보내게 하는 비밀값 (봇 토큰에서 만든다)
const webhookSecret = async (env) => (await sha("wh:" + env.BOT_TOKEN)).slice(0, 48);
// /export 열쇠 - notify/subscribers.py 가 같은 방법으로 봇 토큰에서 만든다
const exportKey = (env) => sha("export:" + env.BOT_TOKEN);

// 사용자 기록. mode "u" = 실제, "t" = 관리자 /test (발송 목록에 안 들어감)
// {name, username, status: picking|pending|approved, topics(승인된 것), picks(고르는 중), since}
const key = (mode, id) => (mode === "t" ? "t:" : "u:") + id;
const getUser = async (env, mode, id) => JSON.parse((await env.SUBS.get(key(mode, id))) || "null");
const putUser = (env, mode, id, u) => env.SUBS.put(key(mode, id), JSON.stringify(u));

function who(u, id) {
  return esc(u.name || id) + (u.username ? ` (@${esc(u.username)})` : "");
}

function pickerKeyboard(mode, picks) {
  const set = new Set(picks || []);
  const rows = TOPICS.map(([k, n]) => [{ text: (set.has(k) ? "✅ " : "⬜ ") + n, callback_data: `p:${mode}:${k}` }]);
  rows.push([{ text: "📮 신청하기", callback_data: `p:${mode}:ok` }]);
  return { inline_keyboard: rows };
}
const PICK_TEXT = "📮 받을 알림을 골라주세요\n(누를 때마다 켜고 꺼요, 여러 개 가능)";

async function startPicker(env, mode, chatId, from) {
  const old = await getUser(env, mode, chatId);
  const u = {
    ...(old || {}),
    name: [from.first_name, from.last_name].filter(Boolean).join(" "),
    username: from.username || "",
    picks: (old && (old.picks || old.topics)) || [],
  };
  if (!old || old.status === "picking") u.status = "picking";
  await putUser(env, mode, chatId, u);
  const head = mode === "t" ? "🧪 테스트 - 신청자가 보는 화면\n\n" : "";
  const now = old?.status === "approved" ? `지금 받는 알림: ${names(old.topics)}\n\n` : "";
  await send(env, chatId, head + now + PICK_TEXT, pickerKeyboard(mode, u.picks));
}

async function submit(env, mode, chatId, msg, u) {
  if (!u.picks?.length) {
    await tg(env, "sendMessage", { chat_id: chatId, text: "알림을 하나 이상 골라주세요." });
    return;
  }
  const isAdmin = mode === "u" && admins(env).includes(chatId);
  if (isAdmin) {
    // 관리자 본인은 승인 없이 바로
    Object.assign(u, { status: "approved", topics: u.picks, since: u.since || today() });
    delete u.picks;
    await putUser(env, mode, chatId, u);
    await edit(env, msg, `✅ 바로 적용했어요 (관리자)\n받는 알림: ${names(u.topics)}`);
    return;
  }
  const change = u.status === "approved";
  if (!change) u.status = "pending";
  u.requested = today();
  await putUser(env, mode, chatId, u);
  await edit(env, msg, `✅ ${change ? "변경" : "신청"}이 접수됐어요: ${names(u.picks)}\n관리자가 승인하면 알려드릴게요.`
    + (change ? `\n(승인 전까지는 지금 알림(${names(u.topics)})을 계속 받아요)` : ""));

  const lines = [
    mode === "t" ? "🧪 테스트 - 관리자가 받는 알림\n" : "",
    change ? "🔄 알림 변경 신청" : "🆕 알림 신청",
    "이름: " + who(u, chatId),
    `ID: <code>${chatId}</code>`,
    change ? `지금: ${names(u.topics)}\n바꿀 것: ${names(u.picks)}` : "고른 알림: " + names(u.picks),
  ].filter(Boolean);
  const markup = { inline_keyboard: [[
    { text: "✅ 승인", callback_data: `a:${mode}:${chatId}` },
    { text: "❌ 거절", callback_data: `r:${mode}:${chatId}` },
  ]] };
  const targets = mode === "t" ? [chatId] : admins(env);
  for (const a of targets) await send(env, a, lines.join("\n"), markup);
}

async function decide(env, cq, approve, mode, id) {
  const msg = cq.message;
  const u = await getUser(env, mode, id);
  const base = (msg.text || "").split("\n").filter((l) => !l.startsWith("👉")).join("\n");
  if (!u || !u.picks?.length) {
    await edit(env, msg, esc(base) + "\n\n👉 이미 처리했거나 취소된 신청이에요.");
    return;
  }
  const by = cq.from?.first_name || "관리자";
  if (approve) {
    Object.assign(u, { status: "approved", topics: u.picks, since: u.since || today() });
    delete u.picks;
    await putUser(env, mode, id, u);
    await send(env, id, (mode === "t" ? "🧪 테스트 - 승인되면 신청자가 받는 메시지\n\n" : "")
      + `🎉 알림 신청이 승인됐어요!\n내일 아침부터 보내드려요: ${names(u.topics)}\n\n바꾸려면 /start, 그만 받으려면 /stop`);
    await edit(env, msg, esc(base) + `\n\n👉 ✅ ${esc(by)} 승인 (${today()})`);
  } else {
    if (u.status === "approved") {
      delete u.picks; // 변경만 거절 - 예전 알림은 유지
      await putUser(env, mode, id, u);
    } else {
      await env.SUBS.delete(key(mode, id));
    }
    await send(env, id, (mode === "t" ? "🧪 테스트 - 거절되면 신청자가 받는 메시지\n\n" : "")
      + "신청하신 알림이 승인되지 않았어요. 궁금한 점은 관리자에게 문의해 주세요.");
    await edit(env, msg, esc(base) + `\n\n👉 ❌ ${esc(by)} 거절 (${today()})`);
  }
}

async function listUsers(env) {
  const out = [];
  let cursor;
  do {
    const page = await env.SUBS.list({ prefix: "u:", cursor });
    for (const k of page.keys) {
      const u = JSON.parse((await env.SUBS.get(k.name)) || "null");
      if (u) out.push([k.name.slice(2), u]);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

async function onMessage(env, msg) {
  if (msg.chat?.type !== "private") return;
  const chatId = String(msg.chat.id);
  const from = msg.from || {};
  const cmd = (msg.text || "").trim().split(/\s/)[0].split("@")[0].toLowerCase();
  const isAdmin = admins(env).includes(chatId);

  if (cmd === "/stop") {
    const u = await getUser(env, "u", chatId);
    await env.SUBS.delete(key("u", chatId));
    await send(env, chatId, "알림을 그만 보낼게요. 다시 받으려면 /start");
    if (u?.status === "approved" && !isAdmin) {
      for (const a of admins(env)) await send(env, a, `🚪 알림 해지: ${who(u, chatId)}\nID: <code>${chatId}</code>`);
    }
  } else if (cmd === "/test" && isAdmin) {
    await env.SUBS.delete(key("t", chatId));
    await startPicker(env, "t", chatId, from);
  } else if (cmd === "/list" && isAdmin) {
    const users = await listUsers(env);
    const ok = users.filter(([, u]) => u.status === "approved");
    const wait = users.filter(([, u]) => u.picks?.length && u.status !== "picking");
    const lines = [`👥 받는 사람 ${ok.length}명`];
    for (const [id, u] of ok) lines.push(`• ${who(u, id)} <code>${id}</code>\n  ${names(u.topics)}`);
    if (wait.length) {
      lines.push("", `⏳ 승인 대기 ${wait.length}건`);
      for (const [id, u] of wait) lines.push(`• ${who(u, id)} <code>${id}</code> → ${names(u.picks)}`);
    }
    lines.push("", "※ notify/recipients.txt 에 적힌 사람은 따로 받아요");
    await send(env, chatId, lines.join("\n").slice(0, 4000));
  } else if (cmd === "/status") {
    const u = await getUser(env, "u", chatId);
    const text = !u ? "아직 신청 전이에요. /start 를 눌러주세요."
      : u.status === "approved" ? `받는 알림: ${names(u.topics)}` + (u.picks?.length ? `\n변경 승인 대기: ${names(u.picks)}` : "")
      : u.status === "pending" ? `승인 대기 중: ${names(u.picks)}` : "알림을 고르는 중이에요. /start";
    await send(env, chatId, text);
  } else {
    // /start 나 아무 말 → 고르기 화면
    await startPicker(env, "u", chatId, from);
  }
}

async function onCallback(env, cq) {
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
  const msg = cq.message;
  if (!msg) return;
  const chatId = String(msg.chat.id);
  const [kind, mode, arg] = (cq.data || "").split(":");
  if (mode !== "u" && mode !== "t") return;

  if (kind === "p") {
    const u = await getUser(env, mode, chatId);
    if (!u) {
      await send(env, chatId, "다시 /start 를 눌러주세요.");
      return;
    }
    if (arg === "ok") return submit(env, mode, chatId, msg, u);
    if (!TOPIC_SHORT[arg]) return;
    const set = new Set(u.picks || []);
    set.has(arg) ? set.delete(arg) : set.add(arg);
    u.picks = TOPICS.map(([k]) => k).filter((k) => set.has(k));
    await putUser(env, mode, chatId, u);
    await tg(env, "editMessageReplyMarkup", {
      chat_id: chatId, message_id: msg.message_id, reply_markup: pickerKeyboard(mode, u.picks),
    });
  } else if ((kind === "a" || kind === "r") && admins(env).includes(chatId) && arg) {
    await decide(env, cq, kind === "a", mode, arg);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const text = (s, status = 200) =>
      new Response(s, { status, headers: { "content-type": "text/plain; charset=utf-8" } });

    if (url.pathname === "/webhook" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== (await webhookSecret(env))) {
        return text("forbidden", 403);
      }
      try {
        const update = await request.json();
        if (update.message) await onMessage(env, update.message);
        else if (update.callback_query) await onCallback(env, update.callback_query);
      } catch (e) {
        console.log("처리 실패", e && e.stack);
      }
      return text("ok"); // 실패해도 200 - 텔레그램이 같은 걸 계속 다시 보내지 않게
    }

    if (url.pathname === "/export") {
      // 승인된 사람만: {users: {ID: {name, username, topics, since}}}
      if (!env.BOT_TOKEN || request.headers.get("Authorization") !== "Bearer " + (await exportKey(env))) {
        return text("forbidden", 403);
      }
      const users = {};
      for (const [id, u] of await listUsers(env)) {
        if (u.status === "approved") users[id] = { name: u.name, username: u.username, topics: u.topics, since: u.since };
      }
      return new Response(JSON.stringify({ users }), { headers: { "content-type": "application/json; charset=utf-8" } });
    }

    if (url.pathname === "/setup") {
      if (!env.BOT_TOKEN) return text("❌ BOT_TOKEN 설정이 없어요. Settings → Variables and Secrets 를 확인하세요.");
      if (!admins(env).length) return text("❌ ADMIN_ID 설정이 없어요. wrangler.jsonc 를 확인하세요.");
      if (!env.SUBS) return text("❌ KV 저장공간(SUBS)이 연결되지 않았어요. wrangler.jsonc 를 확인하세요.");
      const r = await tg(env, "setWebhook", {
        url: `${url.origin}/webhook`,
        secret_token: await webhookSecret(env),
        allowed_updates: ["message", "callback_query"],
      });
      await tg(env, "setMyCommands", { commands: [
        { command: "start", description: "알림 신청·변경" },
        { command: "status", description: "내 알림 보기" },
        { command: "stop", description: "알림 그만 받기" },
      ] });
      const result = await r.json();
      return text(result.ok ? "✅ 연결 완료! 이제 봇에서 START 를 눌러보세요."
                            : "❌ 연결 실패: " + JSON.stringify(result) + "\n(BOT_TOKEN 이 맞는지 확인하세요)");
    }

    return text("hdhs 알림 신청 봇");
  },
};
