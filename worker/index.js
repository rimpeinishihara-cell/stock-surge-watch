/**
 * Discordのボタン(「1か月非表示」「3か月非表示」「一生非表示」)のクリックを受け取り、
 * リポジトリの state/mute_codes.json を更新する Cloudflare Worker。
 *
 * Discordの「Interactions Endpoint URL」にこのWorkerのURLを設定して使う。
 * 1日1回しか動かないGitHub Actionsではクリックを受け取れないため、
 * クリック受付だけをこの常時待機のWorkerに任せている。
 *
 * 環境変数:
 *   DISCORD_PUBLIC_KEY  … Discordアプリの公開キー(署名検証用・秘密ではない)
 *   APPLICATION_ID      … DiscordアプリのID(フォローアップ返信用・秘密ではない)
 *   GITHUB_REPO         … "owner/repo"
 *   GITHUB_BRANCH       … 更新対象ブランチ
 *   GITHUB_TOKEN        … Contents: Read and write 権限のfine-grained PAT(Secret)
 *   ALLOWED_USER_IDS    … 操作を許可するDiscordユーザーID(カンマ区切り、空なら全員許可)
 */

const MUTE_DAYS = { "30": 30, "90": 90 };
const JST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

const json = (obj) =>
  new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });

const ephemeral = (content) => json({ type: 4, data: { content, flags: 64 } });

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

async function verifySignature(request, body, publicKeyHex) {
  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  if (!signature || !timestamp) return false;

  const message = new TextEncoder().encode(timestamp + body);
  const rawKey = hexToBytes(publicKeyHex);
  const sig = hexToBytes(signature);
  try {
    const key = await crypto.subtle.importKey("raw", rawKey, { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, sig, message);
  } catch (e) {
    const key = await crypto.subtle.importKey(
      "raw", rawKey, { name: "NODE-ED25519", namedCurve: "NODE-ED25519" }, false, ["verify"]
    );
    return await crypto.subtle.verify("NODE-ED25519", key, sig, message);
  }
}

const b64encode = (str) => btoa(unescape(encodeURIComponent(str)));
const b64decode = (b64) => decodeURIComponent(escape(atob(b64.replace(/\n/g, ""))));

function expiryDate(days) {
  return new Date(Date.now() + JST_OFFSET_MS + days * DAY_MS).toISOString().slice(0, 10);
}

/** state/mute_codes.json の {コード: 期限 or null} に1銘柄を書き込んでコミットする。 */
async function writeMute(env, code, expiry) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/state/mute_codes.json`;
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "stock-surge-buttons",
  };

  // ワークフローの状態コミットと衝突(sha不一致)することがあるので数回リトライする
  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await fetch(`${url}?ref=${env.GITHUB_BRANCH}`, { headers });
    if (!res.ok) throw new Error(`GitHub GET ${res.status}`);
    const file = await res.json();

    let mutes = JSON.parse(b64decode(file.content));
    if (Array.isArray(mutes)) mutes = Object.fromEntries(mutes.map((c) => [c, null]));
    mutes[code] = expiry;

    const sorted = Object.fromEntries(Object.keys(mutes).sort().map((k) => [k, mutes[k]]));
    const put = await fetch(url, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        message: `chore: mute ${code} via Discord button`,
        content: b64encode(JSON.stringify(sorted, null, 2)),
        sha: file.sha,
        branch: env.GITHUB_BRANCH,
      }),
    });
    if (put.ok) return;
    if (put.status !== 409 && put.status !== 422) throw new Error(`GitHub PUT ${put.status}`);
  }
  throw new Error("GitHub PUT conflict (retries exhausted)");
}

async function handleMute(interaction, env, mode, code) {
  let reply;
  try {
    const expiry = mode === "perm" ? null : expiryDate(MUTE_DAYS[mode]);
    await writeMute(env, code, expiry);
    reply = expiry
      ? `🔇 ${code} を ${expiry} まで非表示にしました(次回実行から反映)`
      : `🔇 ${code} を無期限で非表示にしました(次回実行から反映)`;
  } catch (e) {
    reply = `⚠️ ${code} の非表示に失敗しました: ${e.message}`;
  }
  await fetch(
    `https://discord.com/api/v10/webhooks/${env.APPLICATION_ID}/${interaction.token}/messages/@original`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: reply }),
    }
  );
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("stock-surge-buttons");

    const body = await request.text();
    if (!(await verifySignature(request, body, env.DISCORD_PUBLIC_KEY))) {
      return new Response("invalid request signature", { status: 401 });
    }

    const interaction = JSON.parse(body);

    if (interaction.type === 1) return json({ type: 1 }); // PING

    if (interaction.type === 3) {
      // MESSAGE_COMPONENT(ボタン)
      const userId = interaction.member?.user?.id ?? interaction.user?.id;
      const allowed = (env.ALLOWED_USER_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
      if (allowed.length && !allowed.includes(userId)) return ephemeral("この操作を行う権限がありません。");

      const [action, mode, code] = (interaction.data?.custom_id || "").split(":");
      if (action !== "mute" || !code || (!(mode in MUTE_DAYS) && mode !== "perm")) {
        return ephemeral("不明な操作です。");
      }

      // 3秒制限があるため、先に「考え中」の応答を返してから裏で更新する
      ctx.waitUntil(handleMute(interaction, env, mode, code));
      return json({ type: 5, data: { flags: 64 } });
    }

    return new Response("unsupported interaction", { status: 400 });
  },
};
