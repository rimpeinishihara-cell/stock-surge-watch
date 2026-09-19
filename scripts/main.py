"""
GitHub Actions から毎日実行されるメイン処理。

やること:
  1. コマンド用チャンネル(通知先と同じチャンネル)の新着メッセージを読み、
     !mute / !tag などのコマンドを実行して返信
  2. Yahoo!ファイナンスの値上がり率ランキングから、+10%(既定)以上の銘柄を取得
  3. ミュート銘柄を除外
  4. 株探(kabutan)発の「本日のランキング【値上がり率】」記事(Yahoo!ファイナンス
     ニュース経由)から、銘柄ごとの短評をそのまま抜粋する(AIには推測させない)
  5. 各銘柄について、前営業日15:30以降のYahoo!ファイナンス掲示板の投稿を集め、
     Claude APIで「掲示板の声まとめ」を作る(カテゴリ分類はAIにはさせない。
     `!tag` で手動登録されたカテゴリのみを使う)
  6. ミュートされたカテゴリ(手動タグ)の銘柄は「カテゴリ名 N件」とまとめ、詳細は表示しない
  7. Discordに投稿する
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import commands
import research
import storage
import yahoo_stocks
from discord_client import DiscordClient

JST = ZoneInfo("Asia/Tokyo")

THRESHOLD_PCT = float(os.environ.get("SURGE_THRESHOLD_PCT") or "10")
MAX_CLAUDE_CALLS = int(os.environ.get("MAX_CLAUDE_CALLS_PER_RUN") or "40")
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL") or research.DEFAULT_MODEL

# $ / 1M tokens (input, output) — https://docs.claude.com/ の価格表を参照
PRICE_TABLE_USD = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
}
# 為替は概算の固定レート(厳密なリアルタイムレートは取得していない)
USD_JPY_RATE = 150.0


def process_commands(client: DiscordClient, channel_id: str):
    state = storage.load("last_message_id.json", {"id": None})
    messages = client.get_messages_after(channel_id, state.get("id"))
    if not messages:
        return
    for msg in messages:
        state["id"] = msg["id"]
        if msg.get("author", {}).get("bot"):
            continue
        reply = commands.handle_command(msg.get("content", ""))
        if reply:
            client.send_message(channel_id, reply)
    storage.save("last_message_id.json", state)


def mute_buttons(code: str) -> list:
    """銘柄ごとの「1か月/3か月/一生 非表示」ボタン。クリックはworker/(Cloudflare Worker)が受け取る。"""
    return [{
        "type": 1,
        "components": [
            {"type": 2, "style": 2, "label": "1か月非表示", "custom_id": f"mute:30:{code}"},
            {"type": 2, "style": 2, "label": "3か月非表示", "custom_id": f"mute:90:{code}"},
            {"type": 2, "style": 4, "label": "一生非表示", "custom_id": f"mute:perm:{code}"},
        ],
    }]


def build_messages(shown, suppressed_counts, muted_count, total_found, cost_line=None):
    """投稿するメッセージを [(本文, ボタン or None), ...] の順で返す(銘柄ごとに1メッセージ)。"""
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    header = [
        f"**📈 本日の値上がり率+{THRESHOLD_PCT:.0f}%以上 検知**（{now_str} JST時点）",
        f"検知: {total_found}件 / 表示: {len(shown)}件"
        + (f" / ミュート銘柄で非表示: {muted_count}件" if muted_count else ""),
    ]
    if not shown and not suppressed_counts:
        header += ["", "該当する銘柄はありませんでした。"]
    messages = [("\n".join(header), None)]

    for s in shown:
        tag_suffix = f" [{s['category']}]" if s.get("category") else ""
        lines = [
            f"**{s['code']} {s['name']}**  **+{s['change_pct']:.1f}%**  ({s['price']}円){tag_suffix}",
            f"🗣️ 掲示板まとめ: {s['bbs_summary']}",
            f"📰 株探コメント: {s['kabutan_comment']}",
        ]
        messages.append(("\n".join(lines), mute_buttons(s["code"])))

    footer = []
    if suppressed_counts:
        footer.append("――― カテゴリ非表示 ―――")
        footer += [f"{cat} {cnt}件" for cat, cnt in suppressed_counts.items()]
    if cost_line:
        footer += ["", cost_line] if footer else [cost_line]
    if footer:
        messages.append(("\n".join(footer), None))

    return messages


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel_id = os.environ["CHANNEL_ID"]
    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"

    client = DiscordClient(token)

    # 1. コマンド処理を先に行う(ミュート設定を直後の判定に反映させるため)
    process_commands(client, channel_id)

    mute_codes = commands.load_active_mute_codes()  # 期限切れは自動で間引かれる
    mute_categories = set(storage.load("mute_categories.json", []))
    tags = storage.load("tags.json", {})

    # 2. 値上がり率ランキングを取得
    surges = yahoo_stocks.get_surge_list(THRESHOLD_PCT)
    total_found = len(surges)

    # 3. ミュート銘柄を除外
    surges = [s for s in surges if s["code"] not in mute_codes]
    muted_count = total_found - len(surges)

    # 4. 株探「本日のランキング」記事の個別コメントをまとめて取得(全銘柄で共通の1記事)
    kabutan_comments = yahoo_stocks.fetch_kabutan_ranking_comments(
        [s["code"] for s in surges[:5]]
    )

    bbs_cutoff = research.previous_business_day_1530()

    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    claude_calls = 0
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    shown = []
    suppressed_counts = {}

    # 5. 各銘柄について前営業日15:30以降の掲示板投稿を集め、声をまとめる
    #    (カテゴリがミュートされている銘柄は、掲示板取得もClaude呼び出しも行わず
    #    スキップする。「クソ株」のようなカテゴリはまさに詳細を見たくない銘柄
    #    であることが多く、無駄なトークン消費を避けるため)
    for s in surges:
        code, name, pct = s["code"], s["name"], s["change_pct"]
        category = tags.get(code)
        s["category"] = category
        s["kabutan_comment"] = kabutan_comments.get(code) or "記載なし"

        if category and category in mute_categories:
            suppressed_counts[category] = suppressed_counts.get(category, 0) + 1
            continue

        bbs_posts = research.get_yahoo_bbs(code, bbs_cutoff)

        bbs_summary = None
        if have_key and claude_calls < MAX_CLAUDE_CALLS:
            try:
                bbs_summary, usage = research.summarize_bbs(
                    code, name, pct, bbs_posts, bbs_cutoff, model=CLAUDE_MODEL
                )
                claude_calls += 1
                total_usage["input_tokens"] += usage["input_tokens"]
                total_usage["output_tokens"] += usage["output_tokens"]
            except Exception as e:
                print(f"[main] Claude summarize failed for {code}: {e}")

        if not bbs_summary:
            bbs_summary = research.fallback_bbs_summary(bbs_posts)

        s["bbs_summary"] = bbs_summary
        shown.append(s)

    cost_line = None
    if claude_calls:
        price_in, price_out = PRICE_TABLE_USD.get(CLAUDE_MODEL, (0.0, 0.0))
        cost_usd = (
            total_usage["input_tokens"] / 1_000_000 * price_in
            + total_usage["output_tokens"] / 1_000_000 * price_out
        )
        cost_jpy = cost_usd * USD_JPY_RATE
        cost_line = f"💰 本日のClaude判定コスト: 約{cost_jpy:.1f}円 ({CLAUDE_MODEL}, {claude_calls}件判定)"

    for content, components in build_messages(
        shown, suppressed_counts, muted_count, total_found, cost_line
    ):
        print(content)
        print("---")
        if not dry_run:
            client.send_message(channel_id, content, components)


if __name__ == "__main__":
    main()
