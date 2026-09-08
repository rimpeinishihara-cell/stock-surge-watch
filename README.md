# stock-surge-watch

日本の上場企業で、株価上昇率が+10%(既定)以上の銘柄を毎日自動で検知し、
上がった理由を要約してDiscordに通知するボット。

## 仕組み

1. 毎日 [cron-job.org](https://cron-job.org) から GitHub Actions の `workflow_dispatch` API を叩いて起動
   (GitHub Actions自体の `schedule` トリガーは実行遅延・スキップが多く不安定なため使用していない)
2. `https://kabutan.jp/warning/`(株探の値上がり率ランキング、全市場)を上位ページから取得し、
   前日比+10%以上の銘柄を抽出する(ランキングは降順なので、閾値を下回った時点で取得を打ち切る)
3. ミュートされている銘柄(`!mute`)を除外する
4. 各銘柄について、以下から材料を集める
   - かぶたんの個別銘柄ニュース見出し(`/stock/news?code=XXXX`)
   - Yahoo!ファイナンス掲示板の直近の投稿(`/quote/XXXX.T/forum`)
   - X(旧Twitter)の関連投稿 — Yahoo!リアルタイム検索経由(下記「なぜX公式APIを使わないのか」を参照)
5. 集めた材料をClaude API(既定: Haiku)に渡し、急騰理由の要約(1〜2文)と
   カテゴリ分類を行う。カテゴリは固定ではなく、Discordの `!addcat` コマンドで
   自由に追加・編集・削除できる
6. ミュートされているカテゴリ(`!mutecat`)の銘柄は詳細を表示せず
   「カテゴリ名 N件」とまとめて表示する
7. Discordに投稿する
8. 状態(ミュート設定・カテゴリ定義・最終処理メッセージID)を `state/` にコミットして保存

## なぜX公式APIを使わないのか

X APIは2026年時点で無料枠が廃止され完全従量課金制のため、[wom-buzz-watch](https://github.com/rimpeinishihara-cell/wom-buzz-watch)
と同じく、Yahoo!リアルタイム検索(X投稿の非公式ミラー、無料)を代替として使っている。
これは非公式手段であり、サイト側の仕様変更で動かなくなる可能性がある。

## Discordコマンド(ミュート機能)

通知先チャンネルに以下を打ち込むと、次回の実行(1日1回)から反映される。

```
!help                        コマンド一覧
!mute 2330                   銘柄をミュート(今後一切通知しない)
!unmute 2330                 ミュート解除
!muted                       現在のミュート設定を表示

!addcat 仕手筋 出来高が急増し値動きが荒い銘柄     カテゴリを追加/更新(自由に作れる)
!delcat 仕手筋               カテゴリを削除
!categories                  カテゴリ一覧を表示
!mutecat クソ株              そのカテゴリを「クソ株 5件」のようにまとめて非表示
!unmutecat クソ株            カテゴリのミュート解除
```

カテゴリは決算・業績/材料/バイオ株/クソ株/市況・全体/その他を初期値として同梱しているが、
`!delcat` で削除、`!addcat` で好きな名前・説明のカテゴリに変更できる。

反応は最大1日遅れる(GitHub Actionsが1日1回しか起動しないため)。

## セットアップ

```bash
pip install -r requirements.txt
python scripts/main.py --dry-run  # 環境変数を設定した上でローカル動作確認(実際にはDRY_RUN=true環境変数を使用)
```

### 必要なSecrets(GitHubリポジトリの Settings → Secrets and variables → Actions)

| Name | 内容 |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord Developer PortalでBotを作成し取得したトークン |
| `CHANNEL_ID` | 通知先・コマンド受付チャンネルのID(開発者モードで「IDをコピー」) |
| `ANTHROPIC_API_KEY` | Claude判定用(未設定でも動くが、理由要約・カテゴリ分類ができずフォールバックのみになる) |

### 任意のVariables

| Name | 内容 | 既定値 |
|---|---|---|
| `SURGE_THRESHOLD_PCT` | 検知する上昇率のしきい値(%) | `10` |
| `ANTHROPIC_MODEL` | 使用するモデルID | `claude-haiku-4-5-20251001` |
| `MAX_CLAUDE_CALLS_PER_RUN` | 1回の実行で判定する銘柄数の上限(超過分はフォールバック要約) | `40` |

### Discord Botの作成手順

1. https://discord.com/developers/applications → New Application
2. 左メニュー「Bot」→ Reset Token → トークンをコピー(GitHub Secretsの `DISCORD_BOT_TOKEN` に設定)
3. 同じページの **MESSAGE CONTENT INTENT** をON → Save Changes
4. 左メニュー「OAuth2」→「URL Generator」→ SCOPES: `bot` にチェック
5. BOT PERMISSIONSで `View Channels` `Send Messages` `Read Message History` にチェック
6. 生成されたURLを開き、サーバーに招待
7. 通知先チャンネルを右クリック→「IDをコピー」(開発者モードが必要)→ `CHANNEL_ID` に設定

### cron-job.orgでの毎日実行設定

[monthly-yoy-watch](https://github.com/rimpeinishihara-cell/monthly-yoy-watch) と同じ手順
(fine-grained PAT発行 → `workflow_dispatch` をPOSTで叩くジョブ作成)。

## コスト目安

Haikuモデル使用時、1銘柄あたり数千トークン程度。値上がり銘柄が1日数十件の日でも
月間で数百円程度の見込み(`MAX_CLAUDE_CALLS_PER_RUN` で上限を調整可能)。
実際の請求は [Anthropic Console](https://console.anthropic.com/settings/limits) で確認すること。

## 既知の制限

- Yahoo!ファイナンス掲示板・Yahoo!リアルタイム検索はいずれも非公式手段(サイト構造への依存)であり、
  仕様変更で動かなくなる可能性がある
- かぶたんの値上がり率ランキングのテーブル構造が変わった場合、`scripts/kabutan.py` の修正が必要
- ミュート・カテゴリの反映は次回の実行(1日1回)まで遅延する
