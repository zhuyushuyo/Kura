# Kura（蔵）3.0

**見つけた動画を、使える形で蔵に。** — YouTube などの動画を集めて、編集用 MP4・音声・字幕・文字起こし・出典つきで保存し、検索できるライブラリにしておく個人用アーカイブツールです（[yt-dlp](https://github.com/yt-dlp/yt-dlp) ベース）。

旧「YouTube Downloader Professional 2.3.1」の後継です。Windows と macOS で同じフォルダから動きます。

> English summary at the bottom.

---

## 起動のしかた

| OS | 起動するもの |
|---|---|
| Windows 10/11 | `Start Kura.bat` をダブルクリック |
| macOS | `Start Kura.command` をダブルクリック |

初回だけ、次のものを自動で準備します（数分・数百 MB のダウンロード）。

1. **uv**（Python の管理ツール）— 入っていなければインストール
2. **Python 3.12 の専用環境** — この PC のデータフォルダに作成（下記）
3. **ライブラリ** — PyQt6（画面）、yt-dlp + yt-dlp-ejs（ダウンロードエンジン）、Deno（YouTube 解析用の JS ランタイム）など

2 回目以降はすぐに起動します。`requirements.txt` が変わったときだけ再インストールします。

### ffmpeg について

映像と音声の結合、編集用 MP4 への変換、MP3/WAV 変換、サムネイル・字幕の埋め込みには ffmpeg が必要です。見つからないときはアプリが案内を出します（ステータスバーの「ffmpeg」から設定）。

- **Windows**: 「自動でダウンロード」で yt-dlp 公式ビルドを取得します。手元の `ffmpeg.exe` を指定することもできます（v2 の `plugin/ffmpeg.exe` も指定可）。
- **macOS**: Homebrew があれば `brew install ffmpeg` をボタン 1 つで実行します。ファイルを直接指定することもできます。

### macOS で起動できないとき

Google ドライブ経由のファイルは実行権限が外れたり、「開発元が未確認」と表示されたりすることがあります。

- 右クリック（Control+クリック）→「開く」→「開く」
- それでも動かない場合はターミナルで次を実行します：
  ```bash
  chmod +x "/path/to/Kura/ver3.0.0/Start Kura.command"
  # または
  bash "/path/to/Kura/ver3.0.0/Start Kura.command"
  ```

---

## データの保存場所（この PC 内）

アプリのフォルダ（Google ドライブ上）には **何も書き込みません**。設定・ライブラリ・ログ・Python 環境・ブラウザのデータ・作業用フォルダは、PC ごとに次の場所へ保存します。

| OS | 場所 |
|---|---|
| Windows | `%LOCALAPPDATA%\Kura` |
| macOS | `~/Library/Application Support/Kura` |

ダウンロード中のファイル（`.part` など）も PC 内の作業フォルダで扱い、**完成したファイルだけ**を保存先へ移します。保存先が Google ドライブでも、書きかけのファイルが同期されることはありません。

v2 の設定は「設定 → データ → v2 の設定を読み込む…」から `user_config.json` を選ぶと引き継げます（Windows の `G:/マイドライブ/…` の保存先は Mac の Google ドライブの場所に自動で読み替えます）。

---

## 主な機能

### ダウンロード
- **キュー**: URL の複数貼り付け、ドラッグ＆ドロップ、クリップボード監視、ページ内リンクの右クリックで追加。同時ダウンロード数、一時停止・再開・キャンセル・再試行。ダウンロード中も次の動画を選べます。終了しても一時停止として残り、次回再開できます。
- **プレイリスト・チャンネル**: 一覧から選んで追加（全選択・最新 N 本など）。ダウンロード済みは自動で除外。連続ダウンロードの間に 5〜10 秒の待機（設定可）。
- **プレビュー**: サムネイル・長さ・最大解像度・HDR・チャプター・字幕の有無、推定サイズ、変換の有無を表示。「詳細 → 形式を手動で選ぶ」で形式を直接指定。
- **区間切り出し**: 開始〜終了時刻、またはチャプターを指定してその部分だけ取得（正確カットも可）。
- **YouTube 以外**: yt-dlp が対応するサイト（Vimeo、ニコニコ動画など）でも使えます。

### 出力プリセット
| プリセット | 内容 |
|---|---|
| 編集用 1080p（MP4） | H.264/AAC の MP4。Premiere・DaVinci でそのまま使える（v2 の「Premiere Pro Safe」の後継）。縦型動画も短辺で判定 |
| 編集用 4K（MP4） | 4K の VP9/AV1 を H.264（互換重視）または HEVC（容量重視）へ自動変換。**GPU エンコーダを優先**（AMD AMF / NVIDIA NVENC / Intel QSV / Apple VideoToolbox）、使えなければ CPU |
| 最高画質（保存用） | 無変換で MKV に保存（HDR もそのまま） |
| 音声 M4A / MP3 320k / WAV | M4A は再エンコードなし、WAV は編集用 |

すべてのプリセットでサムネイル・タイトル・チャンネル・公開日・チャプターをファイルに埋め込みます（サムネイル画像が残らない）。よく使う組み合わせは「設定 → プリセット」で保存できます。

### 字幕・メタデータ
- 手動字幕・自動生成字幕（言語指定、例 `ja,en`）を **きれいな SRT** に変換。保存 / 動画に埋め込み / 両方。
- YouTube の自動字幕で起きる「同じ行の繰り返し」を取り除きます。
- タイムスタンプ付き **TXT**、表計算用 **CSV** の文字起こしを同時に出力。
- **出典をコピー**: APA 第 7 版・MLA 第 9 版・Chicago・日本語（SIST 風）。
- メタデータ（説明文・タグ・再生数・チャプターなど）を JSON で保存。ライブラリ全体を CSV で書き出し。

### ライブラリ
ダウンロードしたものをサムネイル付きで一覧・検索（タイトル・チャンネル・タグ）。ファイルを開く・フォルダで表示・別プリセットで再ダウンロード・URL/出典のコピー・削除。

### ファイル名のルール
既定は `{upload_date}_{title}_[{id}]` → 例 `2026-11-05_授業研究の実際_[dQw4w9WgXcQ].mp4`。
使える項目: `{upload_date}` 公開日、`{title}`、`{id}`、`{channel}`、`{dl_date}` 保存日、`{preset}`、`{height}`、`{playlist}`、`{index}`。
禁止文字は全角に置き換え、長いタイトルは短縮し、同名ファイルは ` (2)` を付けて上書きを防ぎます（Windows のパス長制限も考慮）。

---

## ログインについて（重要）

- 既定は **ゲスト（ログインしない）** です。年齢制限・メンバー限定など、必要なときだけログインしてください。
- yt-dlp の公式 Wiki は、アカウントを使ったダウンロードには停止のリスクがあるとして **捨てアカウント（サブアカウント）** の利用を勧めています。
- 方式: 内蔵ブラウザでログイン（クッキーはメモリ上で渡し、ファイルに保存しません）／Firefox のログインを借りる／（macOS のみ）Chrome・Safari／cookies.txt。
- Windows の Chrome・Edge は App-Bound 暗号化のため、外部からログイン情報を読めません。
- 内蔵ブラウザはサンドボックス有効、カメラ・マイク・位置情報などの権限は既定で拒否します。

## エンジンの更新

YouTube の仕様変更に追従するため、起動時に yt-dlp（+ yt-dlp-ejs、Deno）を自動更新します（設定で無効化可、安定版／ナイトリーを選択可）。ステータスバーで yt-dlp・JS ランタイム・ffmpeg・ログイン状態・保存先の空き容量を確認できます。

## エラーのとき

「ボット確認」「403」「形式なし」「年齢制限」などを判別して、原因と対処を日本語で表示します。キューの ⓘ から詳細とログを見られ、「エラー報告をコピー」で状況を共有できます（クッキーなどの秘密情報は含めません）。

## ショートカット

| キー | 動作 |
|---|---|
| Ctrl/⌘ + D | 開いている動画をダウンロード |
| Ctrl/⌘ + L | アドレスバーへ |
| Ctrl/⌘ + V（キュー画面） | クリップボードの URL を追加 |
| Ctrl/⌘ + Shift + V | クリップボードの URL を追加（どの画面でも） |
| Ctrl/⌘ + 1 / 2 / 3 / , | ブラウズ / キュー / ライブラリ / 設定 |
| F5 | 再読み込み |

## コマンドライン（任意）

```bash
# 例: 編集用 1080p、日本語字幕（SRT+TXT）、30〜90 秒だけ
python -m kura.core.cli "https://www.youtube.com/watch?v=..." --preset edit1080 --subs ja --clip 30-90
```

## フォルダ構成

```
ver3.0.0/
├── Start Kura.bat          Windows 用ランチャー
├── Start Kura.command      macOS 用ランチャー
├── requirements.txt
├── kura/                   アプリ本体
│   ├── core/               ダウンロードエンジン（GUI 非依存）
│   └── ui/                 画面（PyQt6）
└── tests/                  自動テスト
```

## 開発

```bash
uv venv && uv pip install -r requirements.txt pytest
QT_QPA_PLATFORM=offscreen python -m pytest
```
テストはローカルで生成した動画（DASH・HTML5・字幕）を使い、結合・変換・字幕整形・切り出し・同名回避などを実際にダウンロードして確認します。

## 注意

著作権と各サイトの利用規約を守り、権利のある・許可された用途で使ってください。

---

## English summary

Kura is a personal video archiving tool built on yt-dlp and PyQt6. Browse in the built-in browser, then download with editing-ready presets (H.264/AAC MP4 up to 1080p, 4K converted with GPU encoders, untouched best quality, M4A/MP3/WAV), clean SRT subtitles plus TXT/CSV transcripts, citations (APA/MLA/Chicago/Japanese), clips, playlists with duplicate skipping and polite delays, and a searchable library.

Start with `Start Kura.bat` (Windows) or `Start Kura.command` (macOS). The launcher installs uv, a private Python environment and the libraries into `%LOCALAPPDATA%\Kura` / `~/Library/Application Support/Kura`; nothing is written into the app folder, so it can live on Google Drive and be shared between computers. Guest mode is the default; sign in only when needed and preferably with a secondary account.

License: Apache-2.0.
