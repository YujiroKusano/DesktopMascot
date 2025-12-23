# 猫型AIマスコット（エド）

デスクトップ上で動く猫（PySide6）。日本語で会話し、吹き出しを表示します。右クリックメニューから操作できます。

## 動作環境
- Windows 10/11（他OSは未検証）
- Python 3.10+

## セットアップ
```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```
（失敗する場合は `python -m pip ...` に置き換え）

## 起動
起動方法は用途に応じて選べます。

- コンソール非表示（通常利用・推奨 / Windows）

```powershell
pythonw app.pyw
```

- コンソール表示（デバッグ用）

```powershell
py mascot.py
```

エクスプローラから `app.pyw` をダブルクリックしても起動できます。

## 使い方
- 右クリックメニュー
  - 話しかける…: 右下に入力欄を開きます（フォーカス済み）。
  - 設定…: 設定ダイアログを開きます（保存すると即時反映）。
  - 終了: アプリを終了します。
- 入力欄（右下）
  - Enter: 送信、Esc: 閉じる。
  - 依存が入っていればマイクボタン（🎤）で押している間だけ録音→離すと認識送信（任意）。

補足
- チャットモード（`talk.chat_mode: true`）では、吹き出しの代わりに右下のチャットパネルが開き、履歴が見やすくなります。

## 設定（設定… → GUI）
設定ダイアログは `config/mascot.json` の `ui.tabs[].fields[]` を読み、動的に生成されています。保存すると JSON に書き戻され、即再読込されます。

- 主なタブ
  - マスコット: 画像サイズ、更新周期、移動/停止/睡眠、アニメ間隔など
  - 会話: 吹き出しON/OFF、自動発話間隔、メッセージ一覧
  - ネット: 回答最大文字数、タイムアウト
  - LLM: LM Studioのエンドポイント、モデル名、温度、トークン、システムプロンプト

ポイント
- `ui.enforce_all: true` のため、基本は UI 定義の値（各 field の `value`）が読み込み時に実値へ反映されます。
- 時刻の自動付与は無効（`context.include_time=false`）。必要時だけONにしてください。

## LM Studio との連携
- `config/mascot.json` → `llm.enabled` を `true` にする。
- `llm.base_url` に LM Studio の OpenAI 互換URLを設定（例: `http://localhost:1234/v1`）。
- `llm.model` に使用モデル名（例: `LMStudio-openai/gpt-oss-20b`）。
- 会話は日本語・簡潔を前提。最大文字数は `net.answer_max_chars`（既定: 220）で制御。

## 音声入力（任意）
押している間だけ録音（プッシュトーク）。離すと音声認識→送信します。

依存（任意で導入）:
```powershell
py -m pip install SpeechRecognition sounddevice numpy
```
依存が無い場合はバブルで案内が出ます（通常のテキスト会話はそのまま利用可）。

## 現在の仕様（要点）
- 会話は軽量でシンプル（RAG/検索は未統合）。
- 右クリック「話しかける…」は入力欄を右下に直接表示（サブメニューは廃止）。
- 「入力欄を表示」「右下固定」などの個別メニューは統合/削除済み。
- テストモード/省電力モードは廃止。
- 設定保存時に自動で再読込し、タイマ/アニメ/会話設定を反映。
- UIスレッド以外からのUI操作は行わない（内部シグナルで橋渡し）。

## よくある調整
- 返答が長い/短い: `net.answer_max_chars` を変更（例: 180〜260）。
- 口調がブレる: `llm.temperature` を下げる（0.2〜0.5）。
- 遅い: 小さいモデルを選ぶ、`llm.max_tokens` を下げる、`context_turns` を下げる。

## 既知の制限/今後
- LLM は現在時刻/場所を自動では知りません（`context.include_time` をONにすると注入可能）。
- リアルタイム検索/家電操作/PC操作は未統合（ツール化して「必要時のみ実行」する構成を検討中）。
- 「万能な猫（分類→必要アクション→最終回答）」は、軽量ルータ＋ツール群＋安全確認で段階的に追加予定。
- カメラ情報からVLMやLoRAなどを使用し、ユーザーの行動を学習しAIが定期的に以下の判断を行う
  - ユーザーの行動の記録および可視化
  - 健康管理
  - 家電操作

## 参考（設定ファイルの場所）
- 設定: `config/mascot.json`
- 学習メモリ: `data/memory.json`

## ログ
- 起動・例外・LLM通信エラーなどは `logs/edo.log` に記録されます（ローテーションあり）。`app.pyw` からの起動でも確認できます。

## 構成（主要ファイル）
- `app.pyw`: ログ初期化＋アプリ起動（通常のエントリポイント）
- `mascot.py`: マスコット本体（移動/描画/右クリックメニュー）
- `talker.py`: 吹き出し／入力バー／チャットパネル、LLM応答の橋渡し
- `agent/llm.py`: LM Studio(OpenAI互換)への問い合わせ
- `agent/config.py`: 設定の既定値・読み書き
- `agent/memory.py`: 会話履歴・要約等の保存
- `config/mascot.json`: 設定と設定UIの定義
- `material/`: アイコンやスプライト素材

## 開発メモ
- 設定値はコードにハードコードせず、JSONで一元管理（UI定義も同ファイル内）。
- 認識/スレッドからのUI操作はシグナル経由でメインスレッドに委譲しています。

## アーキテクト設計（将来）v1では①と⑤のみ

```mermaid
flowchart TB
  subgraph UI["① UI層"]
    EdoShell["EdoShell<br/>デスクトップ猫UI / PySide6<br/>- 吹き出し/メニュー/状態表示<br/>- 入出力のみ（判断しない）"]
  end

  subgraph CORE["② 中枢制御"]
    EdoCore["EdoCore<br/>司令塔 / ルータ<br/>- 分類: 会話/検索/家電/視覚/記録<br/>- 安全チェック<br/>- 記憶参照の選択<br/>- 実行指示"]
  end

  subgraph SENSE["⑤' リアルタイム視覚センサー（CV）"]
    EdoWatch["EdoWatch<br/>Webカメラ リアルタイム解析（CV）<br/>- 動体検知（高頻度）<br/>- 顔/ランドマーク/まばたき/粗い視線（中頻度）<br/>- 姿勢/ジェスチャ（中頻度）<br/>- 物体検出（低頻度）<br/>- 平滑化/ヒステリシスでイベント化<br/>- 最新優先（フレームを溜めない）"]
  end

  subgraph DATA_TOOLS["③ 記憶 & ④ 手足"]
    EdoMemory["EdoMemory<br/>SQLite<br/>- 会話ログ<br/>- 10分ごとの環境データ<br/>- 行動イベント"]
    EdoHands["EdoHands<br/>ツール群<br/>- Web検索<br/>- PC操作<br/>- 家電操作（後で）"]
  end

  subgraph MODELS["⑤ 推論モデル群"]
    EdoMind["EdoMind<br/>LLM: 会話/要約/推論補助<br/>- 今: LM Studio API<br/>- 将来: llama.cpp / vLLM<br/>- 任意: LoRA適用"]
    EdoSight["EdoSight<br/>VLM: 視覚理解<br/>- カメラ/スクショ → 説明/推定<br/>- 任意: LoRA適用"]
  end

  subgraph TRAIN["⑥ 学習・更新（常駐させない）"]
    EdoForge["EdoForge<br/>LoRA学習・評価・更新<br/>- データ整形/学習/評価<br/>- 推論側へデプロイ"]
  end

  EdoShell -->|入力 音声/操作/イベント| EdoCore
  EdoWatch -->|行動イベント/状態| EdoCore
  EdoCore -->|必要時に静止画要求| EdoWatch
  EdoWatch -->|最新フレーム/キーフレーム| EdoSight

  EdoCore -->|保存| EdoMemory
  EdoCore -->|実行| EdoHands
  EdoCore -->|会話/要約| EdoMind
  EdoCore -->|視覚解析| EdoSight
  EdoHands -->|結果| EdoCore
  EdoMind -->|生成結果| EdoCore
  EdoSight -->|解析結果 テキスト| EdoCore
  EdoCore -->|応答/表示| EdoShell

  EdoMemory -->|学習用データ抽出| EdoForge
  EdoForge -->|LoRA/設定反映| EdoMind
  EdoForge -->|LoRA/設定反映| EdoSight
```
