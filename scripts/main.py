"""
GitHub Actions から毎日実行されるメイン処理。

やること:
  1. コマンド用チャンネル(通知先と同じチャンネル)の新着メッセージを読み、
     !mute / !tag などのコマンドを実行して返信
  2. Yahoo!ファイナンスの値上がり率ランキングから、+10%(既定)以上の銘柄を取得
  3. ミュート銘柄を除外
  4. 株探(kabutan)発の「本日のランキング【値上がり率】」記事(Yahoo!ファイナンス
     ニュース経由)から、銘柄ごとの短評をそのまま抜粋する(AIには推測させない)
  5. 各銘柄について、Yahoo!ファイナンスのニュース・掲示板・X(Yahoo!リアルタイム検索)から
     材料を集め、Claude APIで急騰理由の要約を行う(カテゴリ分類はAIにはさせない。
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


def build_message(shown, suppressed_counts, muted_count, total_found):
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    header = f"**📈 本日の値上がり率+{THRESHOLD_PCT:.0f}%以上 検知**（{now_str} JST時点）"
    summary = f"検知: {total_found}件 / 表示: {len(shown)}件"
    if muted_count:
        summary += f" / ミュート銘柄で非表示: {muted_count}件"

    lines = [header, summary, ""]

    if not shown and not suppressed_counts:
        lines.append("該当する銘柄はありませんでした。")

    for s in shown:
        tag_suffix = f" [{s['category']}]" if s.get("category") else ""
        lines.append(
            f"**{s['code']} {s['name']}**  **+{s['change_pct']:.1f}%**  ({s['price']}円){tag_suffix}"
        )
        lines.append(s["reason"])
        lines.append(f"📰 株探コメント: {s['kabutan_comment']}")
        lines.append("")

    if suppressed_counts:
        lines.append("――― カテゴリ非表示 ―――")
        for cat, cnt in suppressed_counts.items():
            lines.append(f"{cat} {cnt}件")

    return "\n".join(lines)


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel_id = os.environ["CHANNEL_ID"]
    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"

    client = DiscordClient(token)

    # 1. コマンド処理を先に行う(ミュート設定を直後の判定に反映させるため)
    process_commands(client, channel_id)

    mute_codes = set(storage.load("mute_codes.json", []))
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

    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    claude_calls = 0
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    shown = []
    suppressed_counts = {}

    # 5. 各銘柄について材料を集め、理由を要約する(カテゴリはtagsからのみ取得)
    for s in surges:
        code, name, pct = s["code"], s["name"], s["change_pct"]
        news = yahoo_stocks.get_stock_news(code)
        bbs_posts = research.get_yahoo_bbs(code)
        x_posts = research.get_x_buzz(name)

        reason = None
        if have_key and claude_calls < MAX_CLAUDE_CALLS:
            try:
                reason, usage = research.summarize_reason(
                    code, name, pct, news, bbs_posts, x_posts, model=CLAUDE_MODEL
                )
                claude_calls += 1
                total_usage["input_tokens"] += usage["input_tokens"]
                total_usage["output_tokens"] += usage["output_tokens"]
            except Exception as e:
                print(f"[main] Claude summarize failed for {code}: {e}")

        if not reason:
            reason = research.fallback_reason(news)

        s["reason"] = reason
        s["category"] = tags.get(code)
        s["kabutan_comment"] = kabutan_comments.get(code) or "記載なし"

        # 6. 手動タグのカテゴリがミュートされていればまとめてカウント、そうでなければ詳細表示
        if s["category"] and s["category"] in mute_categories:
            suppressed_counts[s["category"]] = suppressed_counts.get(s["category"], 0) + 1
        else:
            shown.append(s)

    text = build_message(shown, suppressed_counts, muted_count, total_found)

    if claude_calls:
        price_in, price_out = PRICE_TABLE_USD.get(CLAUDE_MODEL, (0.0, 0.0))
        cost_usd = (
            total_usage["input_tokens"] / 1_000_000 * price_in
            + total_usage["output_tokens"] / 1_000_000 * price_out
        )
        cost_jpy = cost_usd * USD_JPY_RATE
        text += f"\n\n💰 本日のClaude判定コスト: 約{cost_jpy:.1f}円 ({CLAUDE_MODEL}, {claude_calls}件判定)"

    print(text)
    if not dry_run:
        client.send_message(channel_id, text)


if __name__ == "__main__":
    main()
