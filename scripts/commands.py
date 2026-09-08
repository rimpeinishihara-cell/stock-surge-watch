"""
コマンド用チャンネル(通知先と同じチャンネル)に投稿されたメッセージを解釈して実行する。

対応コマンド:
  !help                          … コマンド一覧を表示
  !mute <証券コード>              … その銘柄を今後通知しない
  !unmute <証券コード>            … ミュート解除
  !muted                         … 現在のミュート設定(銘柄・カテゴリ)を表示

  !tag <証券コード> <カテゴリ名>   … 銘柄にカテゴリを手動で付与する
  !untag <証券コード>             … タグを削除
  !tags                          … 現在のタグ付け一覧を表示
  !mutecat <カテゴリ名>           … そのカテゴリの銘柄をまとめて非表示にする(「◯◯ N件」表示)
  !unmutecat <カテゴリ名>         … カテゴリのミュート解除

カテゴリ(「クソ株」等)はAIが自動判定するのではなく、ここでユーザーが手動で
付与したタグに基づく。掲示板・SNSの断片情報からの自動判定は誤判定
(ハルシネーション)のリスクがあるため採用していない。

反映は次回の実行(1日1回)からになる。
"""
from __future__ import annotations

import storage


def _help_text() -> str:
    tags = storage.load("tags.json", {})
    if tags:
        tag_list = "\n".join(f"  ・{code} → {cat}" for code, cat in sorted(tags.items()))
    else:
        tag_list = "  (なし)"
    return f"""**📈 株価急騰ウォッチ コマンド一覧**
(このチャンネルに打ち込んでください。反映は次回の実行(1日1回)からになります)

`!mute <証券コード>`
  その銘柄を今後一切通知しないようにします(例: `!mute 2330`)
`!unmute <証券コード>`
  ミュートを解除します
`!muted`
  現在のミュート設定(銘柄・カテゴリ)を表示します

`!tag <証券コード> <カテゴリ名>`
  銘柄にカテゴリを手動で付与します(例: `!tag 8995 クソ株`)
  カテゴリはAIが自動判定するのではなく、ここで付けたタグのみが使われます
`!untag <証券コード>`
  タグを削除します
`!tags`
  現在のタグ付け一覧を表示します
`!mutecat <カテゴリ名>`
  そのカテゴリが付いた銘柄を詳細表示せず「カテゴリ名 N件」のようにまとめます
  (例: `!mutecat クソ株`。タグが付いていない銘柄はまとめられず常に詳細表示されます)
`!unmutecat <カテゴリ名>`
  カテゴリのミュートを解除します

`!help`
  この一覧を表示します

**現在のタグ付け**
{tag_list}
"""


def handle_command(content: str) -> str | None:
    content = content.strip()
    if not content.startswith("!"):
        return None

    parts = content.split(maxsplit=1)
    cmd = parts[0][1:].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "help":
        return _help_text()
    if cmd == "mute":
        return _cmd_mute(arg)
    if cmd == "unmute":
        return _cmd_unmute(arg)
    if cmd == "muted":
        return _cmd_muted()
    if cmd == "tag":
        return _cmd_tag(arg)
    if cmd == "untag":
        return _cmd_untag(arg)
    if cmd == "tags":
        return _cmd_tags()
    if cmd == "mutecat":
        return _cmd_mutecat(arg)
    if cmd == "unmutecat":
        return _cmd_unmutecat(arg)

    return f"❓ 知らないコマンドです: `!{cmd}`\n`!help` でコマンド一覧を見られます。"


def _cmd_mute(arg: str) -> str:
    code = arg.strip().upper()
    if not code:
        return "使い方: `!mute 2330`"
    codes = storage.load("mute_codes.json", [])
    if code in codes:
        return f"すでにミュート済みです: {code}"
    codes.append(code)
    storage.save("mute_codes.json", codes)
    return f"🔇 ミュートしました: {code}(次回実行から反映)"


def _cmd_unmute(arg: str) -> str:
    code = arg.strip().upper()
    codes = storage.load("mute_codes.json", [])
    if code not in codes:
        return f"ミュートされていません: {code}"
    codes.remove(code)
    storage.save("mute_codes.json", codes)
    return f"🔊 ミュート解除しました: {code}"


def _cmd_muted() -> str:
    codes = storage.load("mute_codes.json", [])
    cats = storage.load("mute_categories.json", [])
    lines = ["**現在のミュート設定**"]
    lines.append("銘柄: " + (", ".join(codes) if codes else "(なし)"))
    lines.append("カテゴリ: " + (", ".join(cats) if cats else "(なし)"))
    return "\n".join(lines)


def _cmd_tag(arg: str) -> str:
    bits = arg.split(maxsplit=1)
    if len(bits) < 2:
        return "使い方: `!tag 証券コード カテゴリ名`\n例: `!tag 8995 クソ株`"
    code, cat = bits[0].strip().upper(), bits[1].strip()
    tags = storage.load("tags.json", {})
    is_update = code in tags
    tags[code] = cat
    storage.save("tags.json", tags)
    verb = "更新" if is_update else "登録"
    return f"✅ タグを{verb}しました: {code} → **{cat}**"


def _cmd_untag(arg: str) -> str:
    code = arg.strip().upper()
    if not code:
        return "使い方: `!untag 8995`"
    tags = storage.load("tags.json", {})
    if code not in tags:
        return f"タグが付いていません: {code}"
    del tags[code]
    storage.save("tags.json", tags)
    return f"🗑️ タグを削除しました: {code}"


def _cmd_tags() -> str:
    tags = storage.load("tags.json", {})
    if not tags:
        return "現在、タグ付けされている銘柄はありません。`!tag` で登録できます。"
    lines = ["**現在のタグ付け一覧**"]
    for code, cat in sorted(tags.items()):
        lines.append(f"・{code} → {cat}")
    return "\n".join(lines)


def _cmd_mutecat(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "使い方: `!mutecat クソ株`\n`!tags` で現在のタグ付け一覧を見られます。"
    mute_cats = storage.load("mute_categories.json", [])
    if name in mute_cats:
        return f"すでにミュート済みのカテゴリです: {name}"
    mute_cats.append(name)
    storage.save("mute_categories.json", mute_cats)
    return f"🔇 カテゴリをミュートしました: {name}(次回実行から反映)"


def _cmd_unmutecat(arg: str) -> str:
    name = arg.strip()
    mute_cats = storage.load("mute_categories.json", [])
    if name not in mute_cats:
        return f"ミュートされていないカテゴリです: {name}"
    mute_cats.remove(name)
    storage.save("mute_categories.json", mute_cats)
    return f"🔊 カテゴリのミュートを解除しました: {name}"
