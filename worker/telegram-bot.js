// 텔레그램 알림 신청 봇 (Cloudflare Worker, 무료) - START 를 누르면 바로 답한다.
//
// 하는 일은 하나: 누가 봇에 메시지를 보내면
//   - 그 사람에게 "알림 신청 접수됐어요" 답장
//   - 관리자에게 이름·@아이디·채팅 ID + recipients.txt 에 붙여넣을 한 줄
// 받을 알림은 관리자가 notify/recipients.txt 에 ID 별로 적는다 (아침 발송은 GitHub Actions).
//
// 설정 (Cloudflare → 이 Worker → Settings → Variables and Secrets)
//   BOT_TOKEN  (Secret) BotFather 봇 토큰 = GitHub Secrets 의 TELEGRAM_BOT_TOKEN
//   ADMIN_ID   (Text)   관리자 텔레그램 ID (여러 명이면 쉼표)
// 배포 후 브라우저로 https://<worker 주소>/setup 을 한 번 열면 텔레그램과 연결된다.

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) console.log(method, r.status, await r.text());
  return r;
}

const send = (env, chatId, text) =>
  tg(env, "sendMessage", { chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true });

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const admins = (env) => String(env.ADMIN_ID || "").split(",").map((s) => s.trim()).filter(Boolean);

// webhook 주소로 아무나 가짜 메시지를 못 보내게 하는 비밀값 (봇 토큰에서 만든다)
async function webhookSecret(env) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("wh:" + env.BOT_TOKEN));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 48);
}

async function onMessage(env, msg) {
  if (msg.chat?.type !== "private") return;
  const chatId = String(msg.chat.id);
  const from = msg.from || {};
  const name = [from.first_name, from.last_name].filter(Boolean).join(" ") || chatId;
  const who = name + (from.username ? ` (@${from.username})` : "");
  const text = (msg.text || "").trim();

  if (admins(env).includes(chatId)) {
    await send(env, chatId, "👋 관리자 계정이에요. 누가 알림을 신청하면 여기로 알려드려요.");
    return;
  }

  await send(env, chatId, "✅ 알림 신청이 접수됐어요.\n관리자가 확인하고 받을 알림을 설정해 드릴게요.");

  const lines = ["🆕 알림 신청", "이름: " + esc(who), `ID: <code>${chatId}</code>`];
  if (text && text.split("@")[0].toLowerCase() !== "/start") lines.push("보낸 말: " + esc(text.slice(0, 300)));
  lines.push(
    "",
    "notify/recipients.txt 에 한 줄 추가 (받을 알림만 남기기):",
    `<code>${chatId} food celeb fashion living beauty #${esc(name.replace(/#/g, ""))}</code>`,
  );
  for (const a of admins(env)) await send(env, a, lines.join("\n"));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const text = (s) => new Response(s, { headers: { "content-type": "text/plain; charset=utf-8" } });

    if (url.pathname === "/webhook" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== (await webhookSecret(env))) {
        return new Response("forbidden", { status: 403 });
      }
      try {
        const update = await request.json();
        if (update.message) await onMessage(env, update.message);
      } catch (e) {
        console.log("처리 실패", e && e.stack);
      }
      return new Response("ok"); // 실패해도 200 - 텔레그램이 같은 걸 계속 다시 보내지 않게
    }

    if (url.pathname === "/setup") {
      if (!env.BOT_TOKEN) return text("❌ BOT_TOKEN 설정이 없어요. Settings → Variables and Secrets 를 확인하세요.");
      if (!admins(env).length) return text("❌ ADMIN_ID 설정이 없어요. Settings → Variables and Secrets 를 확인하세요.");
      const r = await tg(env, "setWebhook", {
        url: `${url.origin}/webhook`,
        secret_token: await webhookSecret(env),
        allowed_updates: ["message"],
      });
      await tg(env, "deleteMyCommands", {}); // 예전 명령어 메뉴 정리
      const result = await r.json();
      return text(result.ok ? "✅ 연결 완료! 이제 다른 계정으로 봇에서 START 를 눌러보세요."
                            : "❌ 연결 실패: " + JSON.stringify(result) + "\n(BOT_TOKEN 이 맞는지 확인하세요)");
    }

    return text("hdhs 알림 신청 봇");
  },
};
