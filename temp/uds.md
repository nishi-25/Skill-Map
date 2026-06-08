# UDS / OBD-II 診断通信リファレンス

> 規格ベース：ISO 14229 (UDS) / SAE J1979・ISO 15031-5 (OBD-II)  
> 作成日：2026-06-08

---

## 目次

1. [UDS SID一覧](#1-uds-sid一覧)
2. [ネガティブレスポンスコード（NRC）](#2-ネガティブレスポンスコードnrc)
3. [OBD-II PID一覧（SID 0x01）](#3-obd-ii-pid一覧sid-0x01)
4. [PID 0x01 詳細パラメータ](#4-pid-0x01-詳細パラメータ)
5. [送受信サンプル](#5-送受信サンプル)

---

## 1. UDS SID一覧

### 1-1. 診断セッション・通信管理

| SID | サービス名 | ポジティブ応答SID | 概要 |
|-----|-----------|-----------------|------|
| 0x10 | DiagnosticSessionControl | 0x50 | セッション切替（Default / Extended / Programming） |
| 0x11 | ECUReset | 0x51 | ECUリセット（Hard / Soft / KeyOffOn） |
| 0x27 | SecurityAccess | 0x67 | セキュリティ解除（Seed/Key交換） |
| 0x28 | CommunicationControl | 0x68 | 通信制御（送受信ON/OFF） |
| 0x3E | TesterPresent | 0x7E | セッション維持（タイムアウト防止） |
| 0x83 | AccessTimingParameter | 0xC3 | タイミングパラメータ読み取り/設定 |
| 0x84 | SecuredDataTransmission | 0xC4 | 暗号化データ転送 |
| 0x85 | ControlDTCSetting | 0xC5 | DTC記録ON/OFF |
| 0x86 | ResponseOnEvent | 0xC6 | イベント応答設定 |
| 0x87 | LinkControl | 0xC7 | 通信速度切替 |

### 1-2. データ読み書き

| SID | サービス名 | ポジティブ応答SID | 概要 |
|-----|-----------|-----------------|------|
| 0x22 | ReadDataByIdentifier | 0x62 | DIDによるデータ読み取り |
| 0x23 | ReadMemoryByAddress | 0x63 | アドレス指定メモリ読み取り |
| 0x24 | ReadScalingDataByIdentifier | 0x64 | スケーリング情報読み取り |
| 0x2A | ReadDataByPeriodicIdentifier | 0x6A | 周期的データ読み取り |
| 0x2C | DynamicallyDefineDataIdentifier | 0x6C | 動的DID定義 |
| 0x2E | WriteDataByIdentifier | 0x6E | DIDによるデータ書き込み |
| 0x3D | WriteMemoryByAddress | 0x7D | アドレス指定メモリ書き込み |

### 1-3. DTC（故障コード）管理

| SID | サービス名 | ポジティブ応答SID | 概要 |
|-----|-----------|-----------------|------|
| 0x14 | ClearDiagnosticInformation | 0x54 | DTC消去 |
| 0x19 | ReadDTCInformation | 0x59 | DTC情報読み取り（サブファンクション多数） |

### 1-4. 入出力制御・ルーティン

| SID | サービス名 | ポジティブ応答SID | 概要 |
|-----|-----------|-----------------|------|
| 0x2F | InputOutputControlByIdentifier | 0x6F | I/O強制制御 |
| 0x31 | RoutineControl | 0x71 | ルーティン実行（Start / Stop / Result） |

### 1-5. フラッシュ書き込み

| SID | サービス名 | ポジティブ応答SID | 概要 |
|-----|-----------|-----------------|------|
| 0x34 | RequestDownload | 0x74 | ダウンロード要求 |
| 0x35 | RequestUpload | 0x75 | アップロード要求 |
| 0x36 | TransferData | 0x76 | データ転送 |
| 0x37 | RequestTransferExit | 0x77 | 転送終了 |
| 0x38 | RequestFileTransfer | 0x78 | ファイル転送要求 |

> **レスポンスSIDの法則**：ポジティブ応答 = 要求SID + 0x40  
> ネガティブ応答は共通で **0x7F**（後述NRC参照）

---

## 2. ネガティブレスポンスコード（NRC）

| NRC | 名称 | 説明 |
|-----|------|------|
| 0x10 | generalReject | 一般的拒否 |
| 0x11 | serviceNotSupported | サービス非サポート |
| 0x12 | subFunctionNotSupported | サブファンクション非サポート |
| 0x13 | incorrectMessageLengthOrInvalidFormat | メッセージ長・フォーマット不正 |
| 0x22 | conditionsNotCorrect | 実行条件不成立 |
| 0x24 | requestSequenceError | リクエスト順序エラー |
| 0x31 | requestOutOfRange | リクエスト範囲外 |
| 0x33 | securityAccessDenied | セキュリティアクセス拒否 |
| 0x35 | invalidKey | キー不一致 |
| 0x36 | exceededNumberOfAttempts | 試行回数超過 |
| 0x37 | requiredTimeDelayNotExpired | 待機時間未経過 |
| 0x70 | uploadDownloadNotAccepted | アップロード/ダウンロード拒否 |
| 0x71 | transferDataSuspended | データ転送中断 |
| 0x72 | generalProgrammingFailure | プログラミング一般エラー |
| 0x78 | requestCorrectlyReceivedResponsePending | 受信済み・応答保留中（処理継続） |
| 0x7E | serviceNotSupportedInActiveSession | 現セッションでサービス非サポート |
| 0x7F | serviceNotSupportedInActiveSession | 現セッションでサービス非サポート |

---

## 3. OBD-II PID一覧（SID 0x01）

> SID 0x01 は **SAE J1979 / ISO 15031-5** で規定。必須PIDは法規で搭載義務あり。

### 3-1. サポートPID確認（32個単位のビットマップ）

| PID | 対象範囲 | 備考 |
|-----|---------|------|
| 0x00 | PID 0x01–0x20 サポート確認 | 必須 |
| 0x20 | PID 0x21–0x40 サポート確認 | — |
| 0x40 | PID 0x41–0x60 サポート確認 | — |
| 0x60 | PID 0x61–0x80 サポート確認 | — |

### 3-2. 主要PID一覧

| PID | 名称 | バイト数 | 計算式 | 単位 | 範囲 | 必須 |
|-----|------|---------|--------|------|------|------|
| 0x01 | DTCステータス・MIL状態 | 4 | 後述 | — | — | ✅ |
| 0x03 | 燃料システムステータス | 2 | — | — | — | ✅ |
| 0x04 | 計算エンジン負荷 | 1 | A×100/255 | % | 0–100 | ✅ |
| 0x05 | エンジン冷却水温度 | 1 | A−40 | ℃ | −40–215 | ✅ |
| 0x06 | 短期燃料トリム バンク1 | 1 | (A−128)×100/128 | % | −100–99.2 | — |
| 0x07 | 長期燃料トリム バンク1 | 1 | (A−128)×100/128 | % | −100–99.2 | — |
| 0x08 | 短期燃料トリム バンク2 | 1 | (A−128)×100/128 | % | −100–99.2 | — |
| 0x09 | 長期燃料トリム バンク2 | 1 | (A−128)×100/128 | % | −100–99.2 | — |
| 0x0A | 燃料圧力 | 1 | A×3 | kPa | 0–765 | — |
| 0x0B | 吸気圧力（MAP） | 1 | A | kPa | 0–255 | — |
| 0x0C | エンジン回転数 | 2 | (256×A+B)/4 | rpm | 0–16383.75 | ✅ |
| 0x0D | 車速 | 1 | A | km/h | 0–255 | ✅ |
| 0x0E | タイミングアドバンス | 1 | A/2−64 | ° | −64–63.5 | ✅ |
| 0x0F | 吸気温度 | 1 | A−40 | ℃ | −40–215 | ✅ |
| 0x10 | MAFエアフローレート | 2 | (256×A+B)/100 | g/s | 0–655.35 | ✅ |
| 0x11 | スロットル開度 | 1 | A×100/255 | % | 0–100 | ✅ |
| 0x13 | O2センサー搭載位置（2バンク） | 1 | ビットマップ | — | — | — |
| 0x1C | OBD規格種別 | 1 | 列挙値 | — | — | ✅ |
| 0x1F | エンジン始動後経過時間 | 2 | 256×A+B | 秒 | 0–65535 | — |
| 0x21 | MIL点灯後走行距離 | 2 | 256×A+B | km | 0–65535 | — |
| 0x2C | EGRコマンド値 | 1 | A×100/255 | % | 0–100 | — |
| 0x2D | EGRエラー | 1 | (A−128)×100/128 | % | −100–99.2 | — |
| 0x2F | 燃料タンク液位 | 1 | A×100/255 | % | 0–100 | — |
| 0x33 | 大気圧 | 1 | A | kPa | 0–255 | — |
| 0x42 | 制御モジュール電圧 | 2 | (256×A+B)/1000 | V | 0–65.535 | — |
| 0x43 | 絶対エンジン負荷 | 2 | (256×A+B)×100/255 | % | 0–25700 | — |
| 0x45 | 相対スロットル開度 | 1 | A×100/255 | % | 0–100 | — |
| 0x46 | 外気温度 | 1 | A−40 | ℃ | −40–215 | — |
| 0x49 | アクセル開度D | 1 | A×100/255 | % | 0–100 | — |
| 0x4A | アクセル開度E | 1 | A×100/255 | % | 0–100 | — |
| 0x4B | アクセル開度F | 1 | A×100/255 | % | 0–100 | — |
| 0x4C | スロットルアクチュエータ制御 | 1 | A×100/255 | % | 0–100 | — |
| 0x51 | 燃料種別 | 1 | 列挙値 | — | — | — |
| 0x5C | エンジンオイル温度 | 1 | A−40 | ℃ | −40–215 | — |

---

## 4. PID 0x01 詳細パラメータ

> 応答は **4バイト固定**（Byte A〜D）

### Byte A：MILランプ状態 + DTCカウント

| ビット | 内容 |
|--------|------|
| Bit 7 | **MILランプ** `1`=点灯 / `0`=消灯 |
| Bit 6–0 | **格納DTC件数**（0〜127件） |

### Byte B：エンジン系監視テスト

| ビット | 名称 | 内容 |
|--------|------|------|
| Bit 7 | MisfireMonitoring | 失火監視 サポート有無 |
| Bit 6 | FuelSystemMonitoring | 燃料システム監視 サポート有無 |
| Bit 5 | ComponentMonitoring | コンポーネント監視 サポート有無 |
| Bit 4 | 予約 | — |
| Bit 3 | MisfireMonitoringComplete | 失火監視 完了フラグ |
| Bit 2 | FuelSystemMonitoringComplete | 燃料システム監視 完了フラグ |
| Bit 1 | ComponentMonitoringComplete | コンポーネント監視 完了フラグ |
| Bit 0 | 予約 | — |

### Byte C：エンジンタイプ識別

| ビット | 内容 |
|--------|------|
| Bit 3 | `0` = 火花点火（ガソリン） / `1` = 圧縮点火（ディーゼル） |
| その他 | 予約 |

> ⚠️ **Byte C の Bit3 によって Byte D の解釈が変わる**

### Byte D（パターン①）：火花点火エンジン（Byte C Bit3 = 0）

| ビット | 名称 | 内容 |
|--------|------|------|
| Bit 7 | EGRSystem | EGR監視 サポート有無 |
| Bit 6 | OxygenSensorHeater | O2センサーヒーター監視 サポート有無 |
| Bit 5 | OxygenSensor | O2センサー監視 サポート有無 |
| Bit 4 | ACRefrigerant | A/C冷媒監視 サポート有無 |
| Bit 3 | SecondaryAirSystem | 2次エア監視 サポート有無 |
| Bit 2 | EvaporativeSystem | エバポ監視 サポート有無 |
| Bit 1 | HeatedCatalyst | 加熱触媒監視 サポート有無 |
| Bit 0 | Catalyst | 触媒監視 サポート有無 |

### Byte D（パターン②）：圧縮点火エンジン（Byte C Bit3 = 1）

| ビット | 名称 | 内容 |
|--------|------|------|
| Bit 7 | EGRAndVVTSystem | EGR/VVT監視 サポート有無 |
| Bit 6 | PMFilterMonitoring | PMフィルター監視 サポート有無 |
| Bit 5 | ExhaustGasSensor | 排気ガスセンサー監視 サポート有無 |
| Bit 4 | 予約 | — |
| Bit 3 | BoostPressure | ブースト圧監視 サポート有無 |
| Bit 2 | 予約 | — |
| Bit 1 | NOxSCRMonitor | NOx/SCR監視 サポート有無 |
| Bit 0 | NMHCCatalyst | NMHCキャタリスト監視 サポート有無 |

---

## 5. 送受信サンプル

> フォーマット：`CAN-ID  データ長  [データバイト列]`  
> ブロードキャスト要求：`7DF`、ECU応答：`7E8`（ECU #1）

---

### 5-1. OBD-II：PIDサポート確認（SID 0x01 / PID 0x00）

```
# 要求（全ECUブロードキャスト）
7DF  02  01 00  00 00 00 00

# 応答（ECU #1）
7E8  06  41 00  BE 1F A8 13

# 解析
SID応答 = 0x41（= 0x01 + 0x40）
PID     = 0x00
Data    = BE 1F A8 13
        = 1011 1110  0001 1111  1010 1000  0001 0011

# サポートPID（Bit=1がサポートあり）
Byte1(BE): PID 0x01 0x03 0x04 0x05 0x06 0x07 がサポート
Byte2(1F): PID 0x0C 0x0D 0x0E 0x0F 0x10 がサポート
Byte3(A8): PID 0x11 0x13 0x15 がサポート
Byte4(13): PID 0x1C 0x1F がサポート
```

---

### 5-2. OBD-II：PID 0x01（DTCステータス確認）

```
# 要求
7DF  02  01 01  00 00 00 00

# 応答
7E8  06  41 01  81 07 E5 00

# 解析
Byte A = 0x81 = 1000 0001
  → Bit7 = 1  → MIL点灯
  → Bit6-0 = 0000001 → DTC 1件格納

Byte B = 0x07 = 0000 0111
  → Bit3=0: 失火監視 完了
  → Bit2=1: 燃料システム監視 完了
  → Bit1=1: コンポーネント監視 完了
  → Bit0=1: （予約）

Byte C = 0xE5 = 1110 0101
  → Bit3 = 0 → ガソリンエンジン（Byte DはパターンA）

Byte D = 0x00
  → 全監視テスト 未完了 or 非サポート
```

---

### 5-3. OBD-II：エンジン回転数読み取り（SID 0x01 / PID 0x0C）

```
# 要求
7DF  02  01 0C  00 00 00 00

# 応答
7E8  04  41 0C  1A F8

# 解析
Byte A = 0x1A = 26
Byte B = 0xF8 = 248

RPM = (256 × 26 + 248) / 4
    = (6656 + 248) / 4
    = 6904 / 4
    = 1726 rpm
```

---

### 5-4. OBD-II：冷却水温度読み取り（SID 0x01 / PID 0x05）

```
# 要求
7DF  02  01 05  00 00 00 00

# 応答
7E8  03  41 05  6E

# 解析
Byte A = 0x6E = 110

温度[℃] = A − 40 = 110 − 40 = 70℃
```

---

### 5-5. UDS：診断セッション切替（SID 0x10）

```
# 要求：Extendedセッションへ切替
7E0  02  10 03  00 00 00 00

# ポジティブ応答
7E8  06  50 03  00 32 01 F4

# 解析
SID応答 = 0x50（= 0x10 + 0x40）
SubFunc = 0x03（ExtendedDiagnosticSession）
P2Server_max    = 0x0032 = 50ms
P2*Server_max   = 0x01F4 = 500ms（×10ms = 5000ms）
```

---

### 5-6. UDS：DID読み取り（SID 0x22）

```
# 要求：VIN（DID 0xF190）読み取り
7E0  03  22 F1 90  00 00 00

# ポジティブ応答（VIN = "1HGBH41JXMN109186"）
7E8  14  62 F1 90
         31 48 47 42 48 34 31 4A
         58 4D 4E 31 30 39 31 38 36

# 解析
SID応答 = 0x62（= 0x22 + 0x40）
DID     = 0xF190
Data    = ASCII変換 → "1HGBH41JXMN109186"（17バイト）
```

---

### 5-7. UDS：DID書き込み（SID 0x2E）

```
# 要求：任意DID（0x0200）にデータ書き込み
7E0  05  2E 02 00  AB CD

# ポジティブ応答
7E8  03  6E 02 00

# 解析
SID応答 = 0x6E（= 0x2E + 0x40）
DID     = 0x0200
→ 書き込み成功（応答データなし、DIDのエコーのみ）
```

---

### 5-8. UDS：ネガティブレスポンス例

```
# 要求：非サポートSID（0x99）
7E0  02  99 00  00 00 00 00

# ネガティブ応答
7E8  03  7F 99 11

# 解析
0x7F = ネガティブレスポンス固定
0x99 = 要求したSID
0x11 = NRC → serviceNotSupported（サービス非サポート）
```

---

### 5-9. UDS：TesterPresent（SID 0x3E）

```
# 要求：セッション維持（応答不要指定）
7E0  02  3E 80  00 00 00 00

# 応答なし（SubFunction Bit7=1 → suppressPositiveResponse）

# 要求：セッション維持（応答あり）
7E0  02  3E 00  00 00 00 00

# ポジティブ応答
7E8  02  7E 00
```

---

### 5-10. UDS：DTC読み取り（SID 0x19）

```
# 要求：全DTCのステータス読み取り（SubFunc 0x02）
7E0  03  19 02 FF  00 00 00

# ポジティブ応答（DTC: P0300, P0171 の例）
7E8  0A  59 02 FF
         03 00 08  # DTC P0300（ランダム失火） ステータス=0x08
         01 71 09  # DTC P0171（燃料系リーン） ステータス=0x09

# DTCフォーマット（ISO 14229）
# Byte1上位2bit: 00=P, 01=C, 10=B, 11=U
# 例）03 00 = 0000 0011  0000 0000 → P0300
```

---

## 付録：OBD-II vs UDS 比較

| 項目 | OBD-II（SAE J1979） | UDS（ISO 14229） |
|------|---------------------|-----------------|
| 主な用途 | 排ガス・走行データ読み取り | OEM診断・書き込み・制御全般 |
| SID体系 | 0x01〜0x0A | 0x10〜0x3E |
| 識別子 | PID（1バイト） | DID（2バイト） |
| アクセス制限 | 原則オープン | セッション・セキュリティ依存 |
| 法規根拠 | EPA/CARB・EU OBD・JOBD | ISO 26262（機能安全）等 |
| CAN ID（例） | 0x7DF（要求）/ 0x7E8（応答） | 0x7E0（要求）/ 0x7E8（応答） |

---

*本ドキュメントは ISO 14229 / SAE J1979 / ISO 15031-5 に基づく。  
OEMおよびサプライヤ固有のDID・PIDは各仕様書を参照のこと。*
