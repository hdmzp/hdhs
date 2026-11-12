// 텔레그램 알림 신청 봇 (Cloudflare Worker, 무료) - START 를 누르면 바로 답한다.
//
// 흐름
//   1) 신청자: START → [셀럽PGM] [카테고리 편성] [둘 다] 중 하나
//      셀럽PGM 은 누르면 바로 신청, 카테고리는 다음 화면에서 패션/미용/식품/리빙 을 골라 [신청하기]
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

// 카테고리 편성 (세부 화면에서 고르는 것) - 버튼 순서
const CATS = [
  ["fashion", "패션 (의류·언더웨어·잡화/주얼리)"],
  ["beauty", "미용"],
  ["food", "식품 (일반식품·건강식품)"],
  ["living", "리빙 (리빙/주방·가전)"],
];
const TOPIC_SHORT = { celeb: "셀럽PGM", fashion: "패션", beauty: "미용", food: "식품", living: "리빙", brand: "브랜드" };
const ORDER = ["celeb", ...CATS.map(([k]) => k), "brand"];

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
const names = (topics) => ORDER.filter((t) => (topics || []).includes(t)).map((t) => TOPIC_SHORT[t]).join(", ") || "없음";

async function sha(text) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
// webhook 주소로 아무나 가짜 메시지를 못 보내게 하는 비밀값 (봇 토큰에서 만든다)
const webhookSecret = async (env) => (await sha("wh:" + env.BOT_TOKEN)).slice(0, 48);
// /export 열쇠 - notify/subscribers.py 가 같은 방법으로 봇 토큰에서 만든다
const exportKey = (env) => sha("export:" + env.BOT_TOKEN);

// 사용자 기록. mode "u" = 실제, "t" = 관리자 /test (발송 목록에 안 들어감)
// {name, username, status: picking|pending|approved, topics(승인된 것), picks(화면에서 고르는 중),
//  req([신청하기]로 보낸 것 - 승인 대기), since, opened(마지막으로 고르기 화면을 연 날)}
// 고르는 중(picks)과 신청한 것(req)을 나눠서, 신청 뒤 START 를 다시 눌러도 대기 중인 신청이 지워지지 않는다.
const reqOf = (u) => u.req || (u.status === "pending" ? u.picks : null); // 예전 기록(req 없음)도 읽는다
const key = (mode, id) => (mode === "t" ? "t:" : "u:") + id;
const getUser = async (env, mode, id) => JSON.parse((await env.SUBS.get(key(mode, id))) || "null");
const putUser = (env, mode, id, u) => env.SUBS.put(key(mode, id), JSON.stringify(u));

function who(u, id) {
  return esc(u.name || id) + (u.username ? ` (@${esc(u.username)})` : "");
}

// 첫 화면: 셀럽PGM / 카테고리 편성 / 둘 다
const PICK_TEXT = "📮 어떤 알림을 받고 싶으세요?";
const menuKeyboard = (mode) => ({ inline_keyboard: [
  [{ text: "⭐ 셀럽PGM", callback_data: `m:${mode}:celeb` }],
  [{ text: "📂 카테고리 편성", callback_data: `m:${mode}:cat` }],
  [{ text: "⭐📂 둘 다", callback_data: `m:${mode}:both` }],
  [{ text: "🔔 브랜드 알림", callback_data: `m:${mode}:brand` }],
] });

// 둘째 화면: 카테고리 고르기
const CAT_TEXT = (withCeleb) =>
  `📂 받을 카테고리를 골라주세요${withCeleb ? " (셀럽PGM 포함)" : ""}\n누를 때마다 켜고 꺼요, 여러 개 가능`;
function catKeyboard(mode, picks) {
  const set = new Set(picks || []);
  const rows = CATS.map(([k, n]) => [{ text: (set.has(k) ? "✅ " : "⬜ ") + n, callback_data: `p:${mode}:${k}` }]);
  rows.push([{ text: "◀ 뒤로", callback_data: `m:${mode}:back` }, { text: "📮 신청하기", callback_data: `p:${mode}:ok` }]);
  return { inline_keyboard: rows };
}

async function startPicker(env, mode, chatId, from) {
  const old = await getUser(env, mode, chatId);
  const u = {
    ...(old || {}),
    name: [from.first_name, from.last_name].filter(Boolean).join(" "),
    username: from.username || "",
    picks: [], // 고르는 화면은 항상 전체 해제 상태에서 시작 (2026-11 사용자 요청)
    opened: today(),
  };
  delete u.await; // 브랜드 입력 기다리던 것 취소
  if (old && !old.req && reqOf(old)) u.req = reqOf(old); // 예전 형식의 대기 신청 보존
  if (!old || old.status === "picking") u.status = "picking";
  await putUser(env, mode, chatId, u);
  const head = mode === "t" ? "🧪 테스트 - 신청자가 보는 화면\n\n" : "";
  const now = (old?.status === "approved" ? `지금 받는 알림: ${names(old.topics)}\n` : "")
    + (u.req ? `⏳ 승인 대기 중: ${names(u.req)} (새로 고르면 그 내용으로 다시 신청돼요)\n` : "")
    + (old?.status === "approved" || u.req ? "\n" : "");
  await send(env, chatId, head + now + PICK_TEXT, menuKeyboard(mode));
}

async function submit(env, mode, chatId, msg, u) {
  const reply = (text) => (msg ? edit(env, msg, text) : send(env, chatId, text));
  if (!u.picks?.length) {
    await tg(env, "sendMessage", { chat_id: chatId, text: "알림을 하나 이상 골라주세요." });
    return;
  }
  const isAdmin = mode === "u" && admins(env).includes(chatId);
  if (isAdmin) {
    // 관리자 본인은 승인 없이 바로
    Object.assign(u, { status: "approved", topics: u.picks, since: u.since || today() });
    delete u.picks;
    delete u.req;
    await putUser(env, mode, chatId, u);
    await reply(`✅ 바로 적용했어요 (관리자)\n받는 알림: ${names(u.topics)}`);
    return;
  }
  const change = u.status === "approved";
  if (!change) u.status = "pending";
  u.req = u.picks;
  u.requested = today();
  await putUser(env, mode, chatId, u);
  await reply(`✅ 관리자가 확인 후 승인 예정이에요. 승인 후 다음날부터 오전 중에 알림을 보내드릴게요.\n`
    + `🔔 ${change ? "바꿀" : "받는"} 알림: ${names(u.picks)}`
    + (change ? `\n(승인 전까지는 지금 알림(${names(u.topics)})을 계속 받아요)` : ""));

  const lines = [
    mode === "t" ? "🧪 테스트 - 관리자가 받는 알림\n" : "",
    change ? "🔄 알림 변경 신청" : "🆕 알림 신청",
    "이름: " + who(u, chatId),
    `ID: <code>${chatId}</code>`,
    change ? `지금: ${names(u.topics)}\n바꿀 것: ${names(u.picks)}` : "고른 알림: " + names(u.picks),
    u.picks.includes("brand") && u.brands?.length ? "브랜드: " + esc(brandNames(u.brands)) : "",
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
  const want = u && reqOf(u);
  if (!want?.length) {
    await edit(env, msg, esc(base) + "\n\n👉 이미 처리했거나 취소된 신청이에요.");
    return;
  }
  const by = cq.from?.first_name || "관리자";
  if (approve) {
    Object.assign(u, { status: "approved", topics: want, since: u.since || today() });
    delete u.picks;
    delete u.req;
    await putUser(env, mode, id, u);
    await send(env, id, (mode === "t" ? "🧪 테스트 - 승인되면 신청자가 받는 메시지\n\n" : "")
      + `🎉 알림 신청이 승인됐어요! 내일부터 오전 중에 알림을 보내드릴게요.\n🔔 받는 알림: ${names(u.topics)}\n\n바꾸려면 /start, 그만 받으려면 /stop`);
    await edit(env, msg, esc(base) + `\n\n👉 ✅ ${esc(by)} 승인 (${today()})`);
  } else {
    if (u.status === "approved") {
      delete u.picks; // 변경만 거절 - 예전 알림은 유지
      delete u.req;
      await putUser(env, mode, id, u);
    } else {
      await env.SUBS.delete(key(mode, id));
    }
    await send(env, id, (mode === "t" ? "🧪 테스트 - 거절되면 신청자가 받는 메시지\n\n" : "")
      + "신청하신 알림이 승인되지 않았어요. 궁금한 점은 관리자에게 문의해 주세요.");
    await edit(env, msg, esc(base) + `\n\n👉 ❌ ${esc(by)} 거절 (${today()})`);
  }
}

// ---------------- 🔔 브랜드 알림 ----------------
// 전체 홈쇼핑 11개 채널 대상. 사용자가 짧게 입력 → 실제 편성에 나온 브랜드 후보를 버튼으로 → 눌러서 등록 (여러 개, 다시 누르면 취소).
// 같은 상품이 회사마다 다른 브랜드명으로 찍히기도 해서 (HD 에이스바이옴 = GS·CJ·LT 비에날씬) 여러 개 고를 수 있게 했다.
// 후보는 homeshopping/brand_index.json (tools/build_brand_index.py, 편성 수집 때마다 갱신)에서 찾는다.
// 저장: u.brands = [{t: "b", v: 브랜드명} | {t: "k", v: 단어}]  - k 는 '그 단어가 들어간 방송 모두'
// 아침 발송(notify/send_brand_alert.py)이 /export 의 brands 로 오늘 편성과 맞춰 본다.
// 처음 쓰는 사람(승인 전)은 첫 등록 때 관리자 승인 요청이 한 번 간다. 이미 승인된 사람은 승인 없이 바로.
const BRAND_INDEX_URL = "https://raw.githubusercontent.com/hdmzp/hdhs/main/homeshopping/brand_index.json";
const BRAND_MAX = 20;
const AWAIT_MS = 30 * 60e3; // 입력 기다리는 시간 (지나면 아무 말 = /start)
let brandIndexCache = null;

async function brandIndex() {
  if (brandIndexCache && Date.now() - brandIndexCache.at < 10 * 60e3) return brandIndexCache.data;
  try {
    const r = await fetch(BRAND_INDEX_URL, { cf: { cacheTtl: 600 } });
    if (r.ok) brandIndexCache = { at: Date.now(), data: await r.json() };
  } catch (e) {
    console.log("브랜드 색인 못 받음", e && e.message);
  }
  return brandIndexCache?.data || { brands: [] };
}

// 비교용: 소문자, 글자·숫자만 (띄어쓰기·& 등 무시) - notify/send_brand_alert.py 의 norm 과 같게
const normB = (s) => String(s || "").toLowerCase().replace(/[^0-9a-z가-힣]/g, "");
const bigrams = (s) => { const o = new Set(); for (let i = 0; i < s.length - 1; i++) o.add(s.slice(i, i + 2)); return o; };
function dice(a, b) {
  if (a.length < 2 || b.length < 2) return 0;
  const A = bigrams(a), B = bigrams(b);
  let n = 0;
  for (const x of A) if (B.has(x)) n++;
  return (2 * n) / (A.size + B.size);
}

// 점수: 이름(별칭 포함) 같음 100 > 이름에 포함 80 > 비슷한 이름(오타) 64~80 > 상품명에 포함 60. 같으면 방송 많은 순.
function searchBrands(index, term, limit = 5) {
  const q = normB(term);
  if (q.length < 2) return [];
  const out = [];
  for (const [name, n, cos, cat, prods, alias] of index.brands || []) {
    const keys = [name, ...(alias || [])].map(normB).filter(Boolean);
    let s = 0;
    if (keys.includes(q)) s = 100;
    else if (keys.some((x) => x.length >= 2 && (x.includes(q) || q.includes(x)))) s = 80;
    else {
      const d = Math.max(...keys.map((x) => dice(q, x)));
      if (d >= 0.6) s = 40 + 40 * d;
    }
    if (s < 60 && (prods || []).some((p) => normB(p).includes(q))) s = 60;
    if (s) out.push({ name, n, cos, cat, s });
  }
  out.sort((a, b) => b.s - a.s || b.n - a.n);
  return out.slice(0, limit);
}

const brandLabel = (e) => (e.t === "k" ? `'${e.v}' 들어간 방송` : e.v);
const brandNames = (list) => (list || []).map(brandLabel).join(", ");
const sameBrand = (a, b) => a.t === b.t && normB(a.v) === normB(b.v);
const COS_KO = { HD: "HD", GS: "GS", CJ: "CJ", LT: "LT", NS: "NS", HNS: "홈앤", PUBLIC: "공영", KTALPHA: "KT알파",
  SHINSEGAE: "신세계", SHOPPINGNT: "쇼핑엔티", SKSTOA: "SK스토아" };
// 후보 버튼에 채널 이름 - 많으면 앞 3개 + '등 N개 채널'
const cosText = (cos) => (cos.length > 4 ? cos.slice(0, 3).map((c) => COS_KO[c] || c).join("·") + ` 등 ${cos.length}개 채널`
  : cos.map((c) => COS_KO[c] || c).join("·"));

const BRAND_GUIDE = "🔔 <b>알림 받을 브랜드를 짧게 보내주세요.</b>\n"
  + "브랜드명, 상품명 어느 쪽이든 괜찮아요.\n"
  + "예) <code>세포랩</code> · <code>테팔</code> · <code>콘드로이친</code>\n\n"
  + "여러 개는 쉼표로 나눠 보내도 돼요.\n"
  + "보내주시면 실제 편성에 나온 이름 중에서 골라드릴게요.";

function brandListText(u) {
  const list = u.brands || [];
  const lines = ["🔔 <b>브랜드 알림</b>",
    "등록한 브랜드가 그날 전체 홈쇼핑(11개 채널) 편성에 있으면 아침에 알려드려요.", ""];
  if (!list.length) lines.push("아직 등록한 브랜드가 없어요.");
  else list.forEach((e, i) => lines.push(`${i + 1}. ${esc(brandLabel(e))}`));
  lines.push("", `(최대 ${BRAND_MAX}개 · 🗑 를 누르면 지워요)`);
  return lines.join("\n");
}
function brandListKeyboard(mode, u) {
  const rows = (u.brands || []).map((e, i) => [{ text: "🗑 " + brandLabel(e), callback_data: `b:${mode}:d${i}` }]);
  rows.push([{ text: "➕ 브랜드 추가", callback_data: `b:${mode}:add` }]);
  rows.push([{ text: "◀ 처음 화면", callback_data: `m:${mode}:back` }]);
  return { inline_keyboard: rows };
}

// 목록 화면 (msg 가 있으면 그 메시지를 바꾸고, 없으면 새로 보낸다). 등록한 게 없으면 바로 입력 안내.
async function showBrands(env, mode, chatId, msg, u) {
  if (!(u.brands || []).length) return askBrand(env, mode, chatId, msg, u);
  delete u.await;
  await putUser(env, mode, chatId, u);
  if (msg) await edit(env, msg, brandListText(u), brandListKeyboard(mode, u));
  else await send(env, chatId, brandListText(u), brandListKeyboard(mode, u));
}

async function askBrand(env, mode, chatId, msg, u) {
  if ((u.brands || []).length >= BRAND_MAX) {
    await send(env, chatId, `브랜드는 ${BRAND_MAX}개까지 등록할 수 있어요. 🗑 로 몇 개 지운 뒤 추가해 주세요.`);
    return;
  }
  u.await = Date.now();
  await putUser(env, mode, chatId, u);
  const head = mode === "t" ? "🧪 테스트\n\n" : "";
  if (msg) await edit(env, msg, head + BRAND_GUIDE);
  else await send(env, chatId, head + BRAND_GUIDE);
}

// 입력 한 줄 → 검색어 여러 개. 문장으로 길게 써도 군더더기 말은 뺀다.
// 따로 떨어진 단어일 때만 뺀다 ('OO브랜드' 처럼 이름에 붙은 건 그대로)
const FILLER = /(^|\s)(알림|알려\s*주세요|알려\s*줘|부탁\s*(드립니다|드려요|해요)?|해\s*주세요|주세요|등록|신청|브랜드|받고\s*싶어요|받을게요)(?=\s|$)/g;
function brandTerms(text) {
  return [...new Set(String(text).split(/[,，\n、/]+|\s+(?:그리고|랑|하고)\s+/)
    .map((t) => t.replace(FILLER, " ").replace(FILLER, " ").replace(/\s+/g, " ").trim())
    .filter((t) => t))].slice(0, 5);
}

async function onBrandInput(env, mode, chatId, u, text) {
  const terms = brandTerms(text);
  if (!terms.length) {
    await send(env, chatId, "브랜드 이름을 짧게 보내주세요. 예) 세포랩");
    return;
  }
  const index = await brandIndex();
  u.cands = u.cands || { base: 0, list: [] };
  if (u.cands.list.length > 60) { // 오래된 버튼 후보는 버린다 (그 버튼을 누르면 다시 입력해 달라고 안내)
    u.cands.base += u.cands.list.length - 30;
    u.cands.list = u.cands.list.slice(-30);
  }
  const addCand = (e, label) => {
    u.cands.list.push({ ...e, label });
    return u.cands.base + u.cands.list.length - 1;
  };
  const msgs = [];
  for (const term of terms) {
    if (normB(term).length < 2) {
      msgs.push([`'${esc(term)}' 은(는) 너무 짧아요. 두 글자 이상 보내주세요.`, null]);
      continue;
    }
    const found = searchBrands(index, term);
    const rows = found.map((r) => {
      const label = `${r.name} · ${r.n ? cosText(r.cos) + " · " + r.n + "회" : "편성 없음"}${r.cat ? " · " + r.cat : ""}`;
      return [{ i: addCand({ t: "b", v: r.name }, label), label }];
    });
    const kw = term.replace(/\s+/g, " ").slice(0, 20);
    rows.push([{ i: addCand({ t: "k", v: kw }, `🔎 '${kw}' 들어간 방송 모두`), label: `🔎 '${kw}' 들어간 방송 모두` }]);
    const long = term.length > 15 ? "💡 조금 더 짧게 보내주시면 더 잘 찾아요.\n\n" : "";
    const head = found.length
      ? `${long}🔎 <b>'${esc(term)}'</b> 찾은 브랜드 (최근 4개월 전체 홈쇼핑 편성)\n눌러서 등록해요. 여러 개 골라도 되고, 다시 누르면 취소돼요.`
      : `${long}🔎 <b>'${esc(term)}'</b> - 최근 4개월 전체 홈쇼핑 편성에 이 이름이 들어간 브랜드·상품이 없어요.\n그래도 등록하면 앞으로 나올 때 알려드려요.`;
    if (!found.length) await env.SUBS.put("miss:" + term.slice(0, 40), today(), { expirationTtl: 90 * 86400 });
    msgs.push([head, rows]);
  }
  u.await = Date.now(); // 이어서 더 보낼 수 있게
  await putUser(env, mode, chatId, u);
  for (const [head, rows] of msgs) {
    if (!rows) { await send(env, chatId, head); continue; }
    const kb = rows.map(([b]) => [{ text: (u.brands || []).some((e) => sameBrand(e, u.cands.list[b.i - u.cands.base])) ? "✅ " + b.label : b.label, callback_data: `b:${mode}:c${b.i}` }]);
    kb.push([{ text: "✔ 다 골랐어요 (목록 보기)", callback_data: `b:${mode}:list` }]);
    await send(env, chatId, head, { inline_keyboard: kb });
  }
}

async function onBrandCallback(env, mode, chatId, msg, u, arg) {
  if (arg === "add") return askBrand(env, mode, chatId, msg, u);
  if (arg === "list") return showBrands(env, mode, chatId, null, u);
  const n = Number(arg.slice(1));
  if (arg[0] === "d") {
    if (Number.isInteger(n) && u.brands?.[n]) u.brands.splice(n, 1);
    await putUser(env, mode, chatId, u);
    if (u.brands?.length) await edit(env, msg, brandListText(u), brandListKeyboard(mode, u));
    else await edit(env, msg, "🔔 등록한 브랜드를 모두 지웠어요. 다시 등록하려면 /brand");
    return;
  }
  if (arg[0] !== "c") return;
  const cand = u.cands?.list?.[n - (u.cands?.base || 0)];
  if (!cand) {
    await send(env, chatId, "오래된 버튼이에요. 브랜드 이름을 다시 보내주세요.");
    return askBrand(env, mode, chatId, null, u);
  }
  const entry = { t: cand.t, v: cand.v };
  u.brands = u.brands || [];
  const at = u.brands.findIndex((e) => sameBrand(e, entry));
  let added = false;
  if (at >= 0) u.brands.splice(at, 1);
  else if (u.brands.length >= BRAND_MAX) {
    await send(env, chatId, `브랜드는 ${BRAND_MAX}개까지예요. /brand 에서 몇 개 지운 뒤 추가해 주세요.`);
    return;
  } else { u.brands.push(entry); added = true; }
  await putUser(env, mode, chatId, u);
  // 누른 버튼에 ✅ 표시 (같은 메시지의 다른 버튼도 지금 등록 상태에 맞게)
  const rows = (msg.reply_markup?.inline_keyboard || []).map((row) => row.map((b) => {
    const m = /^b:[ut]:c(\d+)$/.exec(b.callback_data || "");
    const c = m && u.cands.list[Number(m[1]) - u.cands.base];
    if (!c) return b;
    const label = b.text.replace(/^✅ /, "");
    return { ...b, text: (u.brands.some((e) => sameBrand(e, c)) ? "✅ " : "") + label };
  }));
  await tg(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id, reply_markup: { inline_keyboard: rows } });

  // 승인 전인 사람: 첫 등록 때 관리자 승인 요청 한 번 (승인된 사람은 바로, 관리자는 submit 에서 바로 승인)
  if (added && u.status !== "approved" && !(reqOf(u) || []).includes("brand")) {
    u.picks = [...new Set([...(reqOf(u) || []), "brand"])];
    await submit(env, mode, chatId, null, u);
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

  // 브랜드 이름을 기다리는 중이면 (명령어가 아닌) 메시지를 브랜드 검색어로 받는다. 관리자 /test 중이면 테스트 기록 쪽.
  if (!cmd.startsWith("/") && msg.text) {
    for (const mode of isAdmin ? ["t", "u"] : ["u"]) {
      const w = await getUser(env, mode, chatId);
      if (w?.await && Date.now() - w.await < AWAIT_MS) return onBrandInput(env, mode, chatId, w, msg.text);
    }
  }

  if (cmd === "/brand") {
    const u = (await getUser(env, "u", chatId)) || {
      name: [from.first_name, from.last_name].filter(Boolean).join(" "), username: from.username || "",
      status: "picking", opened: today(),
    };
    await showBrands(env, "u", chatId, null, u);
  } else if (cmd === "/stop") {
    const u = await getUser(env, "u", chatId);
    await env.SUBS.delete(key("u", chatId));
    await send(env, chatId, "알림을 그만 보낼게요. 다시 받으려면 /start");
    if (u?.status === "approved" && !isAdmin) {
      for (const a of admins(env)) await send(env, a, `🚪 알림 해지: ${who(u, chatId)}\nID: <code>${chatId}</code>`);
    }
  } else if (cmd === "/test" && isAdmin) {
    await env.SUBS.delete(key("t", chatId));
    await startPicker(env, "t", chatId, from);
  } else if (cmd === "/check" && isAdmin) {
    // 아침 발송 시계 점검: GITHUB_TOKEN 으로 ① 읽기(수집 기록 조회) ② 쓰기(관리자에게만 셀럽PGM 테스트 발송 요청)
    if (!env.GITHUB_TOKEN) {
      await send(env, chatId, "❌ Cloudflare 에 GITHUB_TOKEN 이 없어요. Settings → Variables and Secrets 를 확인하세요.");
      return;
    }
    try {
      const state = await celebScrapeState(env);
      await dispatch(env, "telegram-daily-food.yml", { only: "celeb", admin_only: "true", scheduled: "false" });
      await send(env, chatId, "✅ GitHub 토큰 정상이에요.\n① 기록 읽기 OK (오늘 셀럽PGM 수집: " + esc(state) + ")\n② 실행 요청 OK - 1~2분 안에 셀럽PGM 테스트 메시지가 관리자에게만 와요.\n\n내일부터 05:30 수집 · 07:00 발송이 자동으로 돌아요.");
    } catch (e) {
      const msg = String(e && e.message || e);
      const hint = /HTTP 401/.test(msg) ? "토큰 값이 틀렸거나 만료됐어요."
        : /HTTP 403|HTTP 404/.test(msg) ? "토큰 권한을 확인하세요: Repository access 에 hdhs, Actions 는 Read and write."
        : "";
      await send(env, chatId, "❌ GitHub 토큰 확인 실패\n" + esc(msg) + (hint ? "\n→ " + hint : ""));
    }
  } else if (cmd === "/list" && isAdmin) {
    const users = await listUsers(env);
    const ok = users.filter(([, u]) => u.status === "approved");
    const wait = users.filter(([, u]) => reqOf(u)?.length);
    const draft = users.filter(([, u]) => u.status === "picking" && !reqOf(u)?.length);
    const lines = [`👥 받는 사람 ${ok.length}명`];
    for (const [id, u] of ok) lines.push(`• ${who(u, id)} <code>${id}</code>\n  ${names(u.topics)}`
      + (u.brands?.length ? `\n  🔔 ${esc(brandNames(u.brands))}` : ""));
    if (wait.length) {
      lines.push("", `⏳ 승인 대기 ${wait.length}건`);
      for (const [id, u] of wait) lines.push(`• ${who(u, id)} <code>${id}</code> → ${names(reqOf(u))}`);
    }
    if (draft.length) {
      // START 는 눌렀지만 [📮 신청하기]까지 안 눌러서 승인 요청이 안 간 사람
      lines.push("", `✏️ 신청 화면만 열고 [신청하기]를 안 누른 사람 ${draft.length}명`);
      for (const [id, u] of draft) lines.push(`• ${who(u, id)} <code>${id}</code>${u.opened ? " · " + u.opened : ""}`);
    }
    // 브랜드 검색에서 아무것도 못 찾은 입력 (최근 90일) - 별칭 표(notify/brand_alias.json)에 넣을 후보
    const miss = (await env.SUBS.list({ prefix: "miss:" })).keys.map((k) => k.name.slice(5));
    if (miss.length) lines.push("", `🔎 브랜드 검색 못 찾은 입력 ${miss.length}개`, esc(miss.slice(0, 40).join(", ")));
    await send(env, chatId, lines.join("\n").slice(0, 4000));
  } else if (cmd === "/status") {
    const u = await getUser(env, "u", chatId);
    const text = !u ? "아직 신청 전이에요. /start 를 눌러주세요."
      : u.status === "approved" ? `받는 알림: ${names(u.topics)}` + (reqOf(u)?.length ? `\n변경 승인 대기: ${names(reqOf(u))}` : "")
      : reqOf(u)?.length ? `승인 대기 중: ${names(reqOf(u))}`
      : "아직 신청 전이에요. /start 에서 알림을 고르고 [📮 신청하기]를 눌러 주세요.";
    const br = u?.brands?.length ? `\n🔔 브랜드: ${esc(brandNames(u.brands))} (바꾸려면 /brand)` : "";
    await send(env, chatId, text + br);
  } else {
    // /start 나 아무 말 → 고르기 화면
    await startPicker(env, "u", chatId, from);
  }
}

// 새 게스트 후보 버튼 (아침 발송이 관리자에게 보내는 메시지) - 관리자만 누를 수 있다.
// 정한 것은 KV "g:이름" = add | skip 에 저장되고, 다음 리스트부터 💗 (넣어) / 후보 제외 (빼) 로 반영된다.
async function onGuest(env, cq, chatId, data) {
  const msg = cq.message;
  if (!admins(env).includes(chatId)) {
    await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "관리자만 정할 수 있어요." });
    return;
  }
  const add = data.startsWith("g:a:");
  const name = data.slice(4);
  await env.SUBS.put("g:" + name, add ? "add" : "skip");
  await tg(env, "answerCallbackQuery", {
    callback_query_id: cq.id, text: add ? `💗 ${name} - 게스트로 넣었어요` : `🚫 ${name} - 뺐어요`,
  });
  // 누른 이름의 버튼에 ✅ 표시 (다시 눌러 바꿀 수 있게 버튼은 그대로 둔다)
  const rows = (msg.reply_markup?.inline_keyboard || []).map((row) => {
    if (!row.some((b) => b.callback_data?.slice(4) === name)) return row;
    return row.map((b) => {
      const isAdd = b.callback_data.startsWith("g:a:");
      const picked = isAdd === add;
      return { ...b, text: (picked ? "✅ " : "") + (isAdd ? `💗 ${name} 넣어` : "🚫 빼") };
    });
  });
  await tg(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id, reply_markup: { inline_keyboard: rows } });
}

async function guestDecisions(env) {
  const out = { add: [], skip: [] };
  let cursor;
  do {
    const page = await env.SUBS.list({ prefix: "g:", cursor });
    for (const k of page.keys) {
      const v = await env.SUBS.get(k.name);
      if (v === "add" || v === "skip") out[v].push(k.name.slice(2));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

async function onCallback(env, cq) {
  const msg = cq.message;
  if (!msg) return;
  const chatId = String(msg.chat.id);
  if ((cq.data || "").startsWith("g:")) return onGuest(env, cq, chatId, cq.data);
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
  const [kind, mode, arg] = (cq.data || "").split(":");
  if (mode !== "u" && mode !== "t") return;

  if (kind === "b") {
    const u = await getUser(env, mode, chatId);
    if (!u) {
      await send(env, chatId, "다시 /brand 를 눌러주세요.");
      return;
    }
    return onBrandCallback(env, mode, chatId, msg, u, arg || "");
  }

  if (kind === "m" || kind === "p") {
    const u = await getUser(env, mode, chatId);
    if (!u) {
      await send(env, chatId, "다시 /start 를 눌러주세요.");
      return;
    }
    const cats = (u.picks || []).filter((k) => k !== "celeb");
    if (kind === "m") {
      if (arg === "back") {
        if (u.await) { delete u.await; await putUser(env, mode, chatId, u); }
        await edit(env, msg, PICK_TEXT, menuKeyboard(mode));
      } else if (arg === "brand") {
        return showBrands(env, mode, chatId, msg, u);
      } else if (arg === "celeb") {
        u.picks = ["celeb"]; // 셀럽PGM 만 - 누르면 바로 신청
        return submit(env, mode, chatId, msg, u);
      } else if (arg === "cat" || arg === "both") {
        u.picks = arg === "both" ? ["celeb"] : []; // 카테고리는 전체 해제 상태에서 시작
        await putUser(env, mode, chatId, u);
        await edit(env, msg, CAT_TEXT(arg === "both"), catKeyboard(mode, []));
      }
      return;
    }
    if (arg === "ok") {
      if (!cats.length) {
        await send(env, chatId, "카테고리를 하나 이상 골라주세요.");
        return;
      }
      return submit(env, mode, chatId, msg, u);
    }
    if (!CATS.some(([k]) => k === arg)) return;
    const set = new Set(u.picks || []);
    set.has(arg) ? set.delete(arg) : set.add(arg);
    u.picks = ORDER.filter((k) => set.has(k));
    await putUser(env, mode, chatId, u);
    await tg(env, "editMessageReplyMarkup", {
      chat_id: chatId, message_id: msg.message_id,
      reply_markup: catKeyboard(mode, u.picks.filter((k) => k !== "celeb")),
    });
  } else if ((kind === "a" || kind === "r") && admins(env).includes(chatId) && arg) {
    await decide(env, cq, kind === "a", mode, arg);
  }
}

// 텔레그램 연결 (webhook 주소 + 받을 신호 종류 + 명령어 메뉴).
// 받을 신호가 바뀌면 HOOK_VERSION 을 올린다 - 다음 메시지 때 자동으로 다시 연결된다 (/setup 안 열어도 됨).
const HOOK_VERSION = "3"; // 2: 버튼(callback_query) 추가, 3: /brand 명령 메뉴
async function connect(env, origin) {
  const r = await tg(env, "setWebhook", {
    url: `${origin}/webhook`,
    secret_token: await webhookSecret(env),
    allowed_updates: ["message", "callback_query"],
  });
  await tg(env, "setMyCommands", { commands: [
    { command: "start", description: "알림 신청·변경" },
    { command: "brand", description: "브랜드 알림 등록·삭제" },
    { command: "status", description: "내 알림 보기" },
    { command: "stop", description: "알림 그만 받기" },
  ] });
  if (r.ok && env.SUBS) await env.SUBS.put("cfg:hook", HOOK_VERSION);
  return r;
}

// ---------------- 아침 발송 시계 (Cloudflare Cron) ----------------
// GitHub 예약 실행은 3~6시간씩 늦게 돌아서, 정확한 시간은 여기서 잡고 GitHub 에 "지금 실행"을 요청한다.
// 시간표 (wrangler.jsonc 의 triggers.crons, UTC):
//   20:30 UTC = KST 05:30  셀럽PGM 수집
//   21:15 UTC = KST 06:15  수집이 실패했거나 안 돌았으면 한 번 더 (성공했으면 건너뜀)
//   22:00 UTC = KST 07:00  아침 알림 발송
//   UTC 23:10 / 01:10 / 11:10 / 13:10 = KST 08:10 / 10:10 / 20:10 / 22:10  rep-pgm-scrape (대표프로그램 수집)
//   일 23:45 UTC = 월 KST 08:45  드라마·예능 편성 수집 (scrape-dramavariety.yml) - 월요일 아침 정각이 중요해 손으로 돌리던 것
//   (무료 플랜은 Cron 이 계정당 5개까지라 rep-pgm 네 번은 한 줄로 묶었다 - 이걸로 5개 다 씀)
// 필요: Cloudflare Secret GITHUB_TOKEN (hdhs 저장소 Actions 읽기·쓰기 권한 토큰)
const GH_REPO = "hdmzp/hdhs";

async function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${GH_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "hdhs-bot",
      ...(init.body ? { "content-type": "application/json" } : {}),
    },
  });
}

async function dispatch(env, workflow, inputs) {
  const r = await gh(env, `/actions/workflows/${workflow}/dispatches`, {
    method: "POST", body: JSON.stringify({ ref: "main", ...(inputs ? { inputs } : {}) }),
  });
  if (r.status !== 204) throw new Error(`${workflow} 실행 요청 실패: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
}

// 오늘 05:25(KST) 이후에 시작한 셀럽PGM 수집 중 가장 최근 것의 상태: success | failure | running | none
async function celebScrapeState(env) {
  const r = await gh(env, "/actions/workflows/scrape-celebpgm.yml/runs?per_page=10");
  if (!r.ok) throw new Error(`수집 기록 조회 실패: HTTP ${r.status}`);
  const since = new Date();
  since.setUTCHours(20, 25, 0, 0);
  if (since > new Date()) since.setUTCDate(since.getUTCDate() - 1);
  const runs = ((await r.json()).workflow_runs || []).filter((x) => new Date(x.created_at) >= since);
  if (!runs.length) return "none";
  const last = runs[0];
  if (last.status !== "completed") return "running";
  return last.conclusion === "success" ? "success" : "failure";
}

const kstToday = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

async function onCron(env, cron) {
  const tell = async (text) => { for (const a of admins(env)) await send(env, a, text); };
  if (!env.GITHUB_TOKEN) {
    await tell("⚠️ 아침 발송 시계: Cloudflare 에 GITHUB_TOKEN 이 없어 실행을 못 했어요. (GitHub 예비 예약으로 늦게라도 나가요)");
    return;
  }
  try {
    if (cron === "30 20 * * *") {
      await dispatch(env, "scrape-celebpgm.yml");
    } else if (cron === "15 21 * * *") {
      const state = await celebScrapeState(env);
      if (state === "failure" || state === "none") {
        await dispatch(env, "scrape-celebpgm.yml");
        await tell(`🔁 셀럽PGM 수집이 ${state === "none" ? "안 돌아서" : "실패해서"} 한 번 더 돌려요.`);
      }
    } else if (cron === "0 22 * * *") {
      await dispatch(env, "telegram-daily-food.yml", { only: "all", admin_only: "false", scheduled: "true" });
    } else if (cron === "10 1,11,13,23 * * *") {
      // 고정PGM·셀럽 대표프로그램 수집 KST 08:10 / 10:10 / 20:10 / 22:10 (GitHub 예약은 7시간씩 밀려서 옮겼다)
      await dispatch(env, "rep-pgm-scrape.yml");
    } else if (cron === "45 23 * * SUN") {
      // 매주 월요일 KST 08:45 드라마·예능 편성 수집 (UTC 로는 일요일 23:45)
      await dispatch(env, "scrape-dramavariety.yml");
    }
  } catch (e) {
    await tell("⚠️ 아침 발송 시계 오류 (" + cron + "): " + esc(String(e && e.message || e)));
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(onCron(env, event.cron));
  },

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
        if (update.message && (await env.SUBS.get("cfg:hook")) !== HOOK_VERSION) await connect(env, url.origin);
        if (update.message) await onMessage(env, update.message);
        else if (update.callback_query) await onCallback(env, update.callback_query);
      } catch (e) {
        console.log("처리 실패", e && e.stack);
      }
      return text("ok"); // 실패해도 200 - 텔레그램이 같은 걸 계속 다시 보내지 않게
    }

    if (url.pathname === "/export") {
      // 승인된 사람·관리자·게스트 결정·보고 양식 받는 사람:
      // {users: {ID: {name, username, topics, since, brands}}, admins: [ID], guests: {add, skip}, report: [ID] | null}
      if (!env.BOT_TOKEN || request.headers.get("Authorization") !== "Bearer " + (await exportKey(env))) {
        return text("forbidden", 403);
      }
      const users = {};
      for (const [id, u] of await listUsers(env)) {
        if (u.status === "approved") users[id] = { name: u.name, username: u.username, topics: u.topics, since: u.since, brands: u.brands || [] };
      }
      const report = JSON.parse((await env.SUBS.get("cfg:report")) || "null");
      return new Response(JSON.stringify({ users, admins: admins(env), guests: await guestDecisions(env), report }),
                          { headers: { "content-type": "application/json; charset=utf-8" } });
    }

    if (url.pathname === "/sent") {
      // 아침 알림을 오늘 보냈는지 - 보낸 뒤 POST 로 표시, GitHub 예비 예약은 GET 으로 보고 이미 보냈으면 건너뛴다.
      if (!env.BOT_TOKEN || request.headers.get("Authorization") !== "Bearer " + (await exportKey(env))) {
        return text("forbidden", 403);
      }
      const day = kstToday();
      if (request.method === "POST") await env.SUBS.put("sent:" + day, new Date().toISOString(), { expirationTtl: 7 * 86400 });
      const at = await env.SUBS.get("sent:" + day);
      return new Response(JSON.stringify({ date: day, sent: !!at, at }), { headers: { "content-type": "application/json; charset=utf-8" } });
    }

    if (url.pathname === "/report" && request.method === "POST") {
      // 셀럽PGM 보고 양식 받는 사람 - hdhs_private 의 telegram/보고수신자.txt 를 그 저장소 Actions 가 올린다.
      // 열쇠는 /export 와 같다 (봇 토큰에서 만든 값). 본문: {"ids": ["123", ...]}
      if (!env.BOT_TOKEN || request.headers.get("Authorization") !== "Bearer " + (await exportKey(env))) {
        return text("forbidden", 403);
      }
      const body = await request.json().catch(() => ({}));
      const ids = [...new Set((body.ids || []).map((x) => String(x).trim()).filter((x) => /^-?\d+$|^@\w+$/.test(x)))];
      await env.SUBS.put("cfg:report", JSON.stringify(ids));
      return new Response(JSON.stringify({ ok: true, report: ids }), { headers: { "content-type": "application/json; charset=utf-8" } });
    }

    if (url.pathname.startsWith("/lavangba/")) {
      // 라방바(live.ecomm-data.com) 중계 - 2026-11-11부터 라방바가 GitHub Actions IP에 405를 줘서
      // 수집기(tools/lavangba_relay.py)가 막히면 여기로 보낸다. 열쇠는 /export 와 같다.
      // 열린 프록시가 되지 않게 편성 목록(list_hs)과 방송 상세(report/hsshow/{id}) 두 경로만 허용한다.
      if (!env.BOT_TOKEN || request.headers.get("Authorization") !== "Bearer " + (await exportKey(env))) {
        return text("forbidden", 403);
      }
      const path = url.pathname.slice("/lavangba".length);
      const allowed =
        (request.method === "POST" && path === "/api/schedule/list_hs") ||
        (request.method === "GET" && /^\/report\/hsshow\/[0-9A-Za-z_-]{1,64}$/.test(path));
      if (!allowed) return text("not allowed", 404);
      const upstream = await fetch("https://live.ecomm-data.com" + path + url.search, {
        method: request.method,
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
          "Content-Type": "application/json",
          "Origin": "https://live.ecomm-data.com",
          "Referer": "https://live.ecomm-data.com/schedule/hs",
          "Accept-Language": "ko-KR,ko;q=0.9",
        },
        body: request.method === "POST" ? await request.text() : undefined,
      });
      return new Response(upstream.body, {
        status: upstream.status,
        headers: { "content-type": upstream.headers.get("content-type") || "application/octet-stream" },
      });
    }

    if (url.pathname === "/setup") {
      if (!env.BOT_TOKEN) return text("❌ BOT_TOKEN 설정이 없어요. Settings → Variables and Secrets 를 확인하세요.");
      if (!admins(env).length) return text("❌ ADMIN_ID 설정이 없어요. wrangler.jsonc 를 확인하세요.");
      if (!env.SUBS) return text("❌ KV 저장공간(SUBS)이 연결되지 않았어요. wrangler.jsonc 를 확인하세요.");
      const r = await connect(env, url.origin);
      const result = await r.json();
      return text(result.ok ? "✅ 연결 완료! 이제 봇에서 START 를 눌러보세요."
                            : "❌ 연결 실패: " + JSON.stringify(result) + "\n(BOT_TOKEN 이 맞는지 확인하세요)");
    }

    return text("hdhs 알림 신청 봇");
  },
};
