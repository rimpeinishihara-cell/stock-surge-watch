"""
コマンド用チャンネル(通知先と同じチャンネル)に投稿されたメッセージを解釈して実行する。

対応コマンド:
  !help                          … コマンド一覧を表示
  !mute <証券コード...> [期間]    … 銘柄(複数可)を通知しない。期間指定で自動解除
  !unmute <証券コード...>         … ミュート解除(複数可)
  !muted                         … 現在のミュート銘柄を表示

銘柄ごとの非表示はDiscordのボタン(1か月/3か月/一生)からも行える。

反映は次回の実行(1日1回)からになる。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import storage

JST = ZoneInfo("Asia/Tokyo")

_DURATION_RE = re.compile(r"^(\d+)(日|d|週間|週|w|ヶ月|ケ月|か月|カ月|m)$", re.IGNORECASE)
_DURATION_UNIT_DAYS = {
    "日": 1, "d": 1,
    "週間": 7, "週": 7, "w": 7,
    "ヶ月": 30, "ケ月": 30, "か月": 30, "カ月": 30, "m": 30,
}


def _today_jst() -> date:
    return datetime.now(JST).date()


def _parse_duration_days(token: str) -> int | None:
    """'30日' '4週' '1ヶ月' のような期間指定を日数に変換する。該当しなければNone。"""
    m = _DURATION_RE.match(token)
    if not m:
        return None
    n = int(m.group(1))
    return n * _DURATION_UNIT_DAYS[m.group(2).lower()]


def _help_text() -> str:
    return """**📈 株価急騰ウォッチ コマンド一覧**
(このチャンネルに打ち込んでください。反映は次回の実行(1日1回)からになります)

`!mute <証券コード...> [期間]`
  銘柄を通知しないようにします。複数銘柄をまとめて指定できます
  (例: `!mute 2330` (無期限) / `!mute 2330 6203 8995 30日` (期間限定・複数可))
  期間の指定例: `30日` `4週` `1ヶ月`。省略すると無期限で、期限が来ると自動解除されます
`!unmute <証券コード...>`
  ミュートを解除します(複数可、例: `!unmute 2330 6203`)
`!muted`
  現在のミュート銘柄を表示します

各銘柄の投稿に付いているボタン(1か月非表示 / 3か月非表示 / 一生非表示)でも非表示にできます

`!help`
  この一覧を表示します
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

    return f"❓ 知らないコマンドです: `!{cmd}`\n`!help` でコマンド一覧を見られます。"


def _load_mutes() -> dict:
    """mute_codes.jsonを {コード: 期限(ISO日付) or None(無期限)} の辞書で返す。
    旧形式(単純なコードのリスト)だった場合は無期限として辞書に変換する。"""
    mutes = storage.load("mute_codes.json", {})
    if isinstance(mutes, list):
        mutes = {c: None for c in mutes}
    return mutes


def load_active_mute_codes() -> set[str]:
    """
    期限切れを間引いた、現在有効なミュート銘柄コードの集合を返す(main.pyから使用)。
    旧リスト形式からの移行・期限切れの削除があった場合は正規化して保存し直す。
    """
    mutes = _load_mutes()
    original = storage.load("mute_codes.json", {})
    needs_save = isinstance(original, list)

    today = _today_jst()
    active = {}
    for code, expiry in mutes.items():
        if expiry:
            try:
                if date.fromisoformat(expiry) < today:
                    needs_save = True
                    continue
            except ValueError:
                pass
        active[code] = expiry

    if needs_save:
        storage.save("mute_codes.json", active)
    return set(active.keys())


def _cmd_mute(arg: str) -> str:
    bits = arg.split()
    if not bits:
        return (
            "使い方: `!mute 証券コード [証券コード...] [期間]`\n"
            "例: `!mute 2330` (無期限) / `!mute 2330 6203 8995 30日` (期間限定・複数可)\n"
            "期間の指定例: `30日` `4週` `1ヶ月`(未指定なら無期限)"
        )
    days = _parse_duration_days(bits[-1])
    codes = bits[:-1] if days is not None else bits
    codes = [c.upper() for c in codes]
    if not codes:
        return "使い方: `!mute 証券コード [証券コード...] [期間]`"

    expiry = (_today_jst() + timedelta(days=days)).isoformat() if days is not None else None

    mutes = _load_mutes()
    added, updated = [], []
    for code in codes:
        (updated if code in mutes else added).append(code)
        mutes[code] = expiry
    storage.save("mute_codes.json", mutes)

    period_note = f"{expiry}まで" if expiry else "無期限"
    lines = [f"🔇 ミュートしました({period_note}、次回実行から反映)"]
    if added:
        lines.append("新規: " + ", ".join(added))
    if updated:
        lines.append("更新: " + ", ".join(updated))
    return "\n".join(lines)


def _cmd_unmute(arg: str) -> str:
    codes = [c.upper() for c in arg.split()]
    if not codes:
        return "使い方: `!unmute 2330` / `!unmute 2330 6203`"
    mutes = _load_mutes()
    removed, missing = [], []
    for code in codes:
        if code in mutes:
            del mutes[code]
            removed.append(code)
        else:
            missing.append(code)
    storage.save("mute_codes.json", mutes)

    lines = []
    if removed:
        lines.append("🔊 ミュート解除しました: " + ", ".join(removed))
    if missing:
        lines.append("ミュートされていません: " + ", ".join(missing))
    return "\n".join(lines)


def _cmd_muted() -> str:
    mutes = _load_mutes()
    lines = ["**現在のミュート銘柄**"]
    if mutes:
        code_bits = [f"{c}({e}まで)" if e else c for c, e in sorted(mutes.items())]
        lines.append("銘柄: " + ", ".join(code_bits))
    else:
        lines.append("銘柄: (なし)")
    return "\n".join(lines)
