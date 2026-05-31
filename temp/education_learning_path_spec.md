# 学習パス機能 仕様書

## 概要

スキルカタログに登録された各スキルに対して、段階的な学習リソース（外部URL）をステップ順に紐づけて管理・表示する機能。ユーザーは所属グループに割り当てられたスキルの学習パスのみ閲覧でき、各ステップの完了状態を個人ごとに記録できる。

---

## データモデル

### テーブル: `educational_links`

| カラム名 | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | INTEGER (PK) | ✓ | 自動採番 |
| `title` | VARCHAR(200) | ✓ | リソースのタイトル |
| `url` | VARCHAR(1000) | ✓ | 外部リンクURL |
| `description` | TEXT | - | 説明・メモ（任意） |
| `category_id` | INTEGER (FK→categories) | - | 関連カテゴリー |
| `skill_id` | INTEGER (FK→skills) | - | 紐づけスキル |
| `step_order` | INTEGER | - | 学習ステップの順番（小さい順に表示、NULL は末尾） |
| `created_by` | INTEGER (FK→users) | ✓ | 登録者 |
| `created_at` | DATETIME | ✓ | 登録日時 |
| `updated_at` | DATETIME | ✓ | 更新日時 |

### テーブル: `user_learning_progress`（新規）

ユーザーごとの学習ステップ完了状態を管理する。

| カラム名 | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | INTEGER (PK) | ✓ | 自動採番 |
| `user_id` | INTEGER (FK→users) | ✓ | 完了したユーザー |
| `educational_link_id` | INTEGER (FK→educational_links) | ✓ | 完了したステップ |
| `completed_at` | DATETIME | ✓ | 完了日時（自動） |

**制約**: `(user_id, educational_link_id)` に UNIQUE 制約。

### マイグレーション

`Base.metadata.create_all()` で `educational_links` テーブルへの `step_order` カラム追加と `user_learning_progress` テーブルの新規作成を起動時に自動処理。

```python
# app/main.py（起動処理）
Base.metadata.create_all(bind=database.engine)  # user_learning_progress は自動作成

with database.engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE educational_links ADD COLUMN step_order INTEGER"))
        conn.commit()
    except Exception:
        pass  # カラム既存の場合はスキップ
```

---

## ナビゲーション

| 項目 | 値 |
|---|---|
| ラベル | 学習パス |
| アイコン | `bi-signpost-split` |
| URL | `/education` |
| 表示対象 | 承認済みユーザー全員 |

---

## 画面・エンドポイント一覧

### 1. 学習パス一覧 `GET /education`

**アクセス権限**: 承認済みユーザー全員

**スコープ制御**:
- `admin` / `manager`: 全スキルの学習パスを表示
- `user`: 所属グループに割り当てられたスキルカタログのリソースのみ表示
  - グループ未所属の場合は警告バナーを表示し、パスは非表示

**テンプレート変数**:

| 変数 | 型 | 内容 |
|---|---|---|
| `path_groups` | `list[(Skill, list[Link], list[Link])]` | (スキル, 未完了ステップ, 完了済みステップ) のタプルリスト |
| `filter_categories` | `list[Category]` | カテゴリーフィルター用（path_groups に含まれるカテゴリーのみ） |
| `is_scoped` | `bool` | User ロールのスコープ制御が有効か |
| `no_group` | `bool` | グループ未所属か |

**表示内容（共通）**:
- 上部にカテゴリーフィルターチップ（クライアントサイドJS）
- スキルごとに折りたたみカード（デフォルト閉じ）
- カードヘッダー: スキル名・カテゴリーバッジ・難易度バッジ

**ロール別の差異**:

| 要素 | `user` | `admin` / `manager` |
|---|---|---|
| 進捗バッジ・ミニ進捗バー | ✓ 表示 | ✗ 非表示（ステップ数のみ） |
| 完了ボタン / 未完了に戻すボタン | ✓ 表示 | ✗ 非表示 |
| 完了済みセクション | ✓ 表示 | ✗ 非表示 |
| `+` ステップ追加ボタン（インライン） | ✗ 非表示 | ✓ 表示（モーダルが開く） |
| 「管理」ボタン（管理ページへ） | ✗ 非表示 | ✓ 表示 |

**`+` ボタン / インライン追加モーダル（`admin` / `manager` のみ）**:
- 各スキルカードのヘッダーに緑の `+` ボタンを表示
- クリックすると Bootstrap モーダルが開く（ページ遷移なし）
- モーダルに入力: タイトル（必須）・URL（必須）・説明（任意）
- 送信後は `POST /education/path/{skill_id}/add` に `next=/education` を付与して送信
- 追加完了後は `/education` にリダイレクト（モーダルを開いたカードの画面を維持）
- ファビコンプレビュー付き

---

### 2. 完了トグル `POST /education/progress/{link_id}/toggle`

**アクセス権限**: 承認済みユーザー全員（ただし `admin` / `manager` は UI から非表示）

完了 ↔ 未完了 を切り替える。`user_learning_progress` への INSERT または DELETE。

成功時: `/education` にリダイレクト

---

### 3. 学習パス管理 `GET /education/path/{skill_id}`

**アクセス権限**: `admin` / `manager` のみ

**表示内容**:
- 左カラム: 選択スキルの全ステップ一覧（ステップ番号・タイトル・URL・説明・順番変更・削除）
- 右カラム（sticky）: ステップ追加フォーム（タイトル・URL・説明）

**ステップ追加時の動作**:
- `step_order` は自動採番（既存の最大値 + 1）
- `category_id` はスキルの `category_id` を自動セット

---

### 4. ステップ追加 `POST /education/path/{skill_id}/add`

**アクセス権限**: `admin` / `manager` のみ

| パラメータ | 型 | 必須 | 説明 |
|---|---|---|---|
| `title` | str | ✓ | ステップタイトル |
| `url` | str | ✓ | リソースURL |
| `description` | str | - | 説明 |
| `next` | str | - | リダイレクト先パス（`/` 始まりのみ有効、デフォルト: `/education/path/{skill_id}`） |

成功時: `next` パラメータが指定されていればそこへ、なければ `/education/path/{skill_id}` にリダイレクト

> **用途**: 学習パス一覧の `+` ボタンからモーダル経由で追加する場合は `next=/education` を渡し、管理ページへ遷移させずに一覧に留まる。

---

### 5. ステップ順番変更 `POST /education/path/{skill_id}/reorder/{link_id}`

**アクセス権限**: `admin` / `manager` のみ

| パラメータ | 型 | 必須 | 説明 |
|---|---|---|---|
| `step_order` | int | ✓ | 新しいステップ番号 |

成功時: `/education/path/{skill_id}` にリダイレクト

---

### 6. ステップ削除 `POST /education/path/{skill_id}/delete/{link_id}`

**アクセス権限**: `admin` / `manager` のみ

成功時: `/education/path/{skill_id}` にリダイレクト

---

### 7. リソース個別追加フォーム `GET /education/new`

**アクセス権限**: `admin` / `manager` のみ

スキルに紐づけない個別リソースの登録フォーム。スキルを選択した場合:
- ステップ番号フィールドが出現
- 「学習パス管理ページ」へのリンクが出現

---

### 8. リソース編集 `GET/POST /education/{link_id}/edit`

**アクセス権限**: `admin` / `manager` のみ

---

### 9. リソース削除 `POST /education/{link_id}/delete`

**アクセス権限**: `admin` / `manager` のみ

成功時: `/education` にリダイレクト

---

## ファイル構成

```
app/
├── models.py                        # EducationalLink + UserLearningProgress モデル
├── main.py                          # 起動時マイグレーション処理
├── routers/
│   └── education.py                 # 全エンドポイント
└── templates/
    ├── education.html               # 学習パス一覧ページ（ユーザー向け）
    ├── education_path.html          # 学習パス管理ページ（Manager/Admin）
    └── education_form.html          # リソース個別追加・編集フォーム
```

`base.html` のナビゲーションに `/education` リンクを追加（アイコン: `bi-signpost-split`、ラベル: 学習パス）。

---

## スコープ制御の詳細ロジック

```python
def _get_user_scope(user, db) -> dict | None:
    # admin / manager → None（制限なし）
    # user → 参加グループのスキルIDセットを返す
    # グループ未所属 → {"skill_ids": set(), "cat_ids": set(), "no_group": True}
```

`/education` の学習パス表示では:
- `scope is None` → 全スキルのリソースを取得
- `scope["no_group"]` or `scope["skill_ids"]` が空 → 学習パス非表示
- それ以外 → `skill_id IN (scope["skill_ids"])` でフィルター

---

## UI コンポーネント

### 学習パス一覧（スキルカード）

**User ロールの表示**:
```
カテゴリーフィルター: [すべて] [プログラミング] [制御] ...

┌──────────────────────────────────────────────────────────────┐
│ ■ スキル名  [カテゴリー] [難易度]  2/3 完了 ████░          ▼ │  ← クリックで展開
├──────────────────────────────────────────────────────────────┤
│ （展開時）                                                    │
│  ① ─── ┌───────────────────────────────────────────────┐    │
│  │     │ 🌐 タイトル                        [開く][完了]│    │
│  │     └───────────────────────────────────────────────┘    │
│  ─────────────── ✓ 完了済み (1) ───────────────            │
│  ✓ ─── │ ✅ タイトル 2（完了済み）          [開く][ ↩ ]│    │
└──────────────────────────────────────────────────────────────┘
```

**admin / manager ロールの表示**:
```
┌──────────────────────────────────────────────────────────────┐
│ ■ スキル名  [カテゴリー] [難易度]  3 ステップ   [+] [管理] ▼ │
├──────────────────────────────────────────────────────────────┤
│ （展開時 — 完了セクションなし、完了ボタンなし）               │
│  ① ─── │ 🌐 タイトル                               [開く]│  │
│  ② ─── │ 🌐 タイトル 2                             [開く]│  │
└──────────────────────────────────────────────────────────────┘

[+] クリック時 → モーダル
┌────────────────────────────────────┐
│ ステップを追加: スキル名           │
│ タイトル: [___________________]    │
│ URL:      [___________________]    │
│ 説明:     [___________________]    │
│              [キャンセル] [追加する]│
└────────────────────────────────────┘
```

**カードヘッダーの状態変化（User のみ）**:

| 状態 | ヘッダー背景 | アイコン | 進捗バッジ |
|---|---|---|---|
| 未着手 | `#fff7ed`（オレンジ系） | `collection-fill`（オレンジ） | `N ステップ`（グレー） |
| 進行中 | `#fff7ed` | `collection-fill`（オレンジ） | `X/Y 完了`（オレンジ）+ 進捗バー |
| 全完了 | `#f0fdf4`（グリーン系） | `check-circle-fill`（グリーン） | `完了`（グリーン） |

**admin / manager のカードヘッダー**: 常に `#fff7ed`、`N ステップ` バッジのみ（進捗追跡なし）

**カラー定数**:
- 未完了ステップ番号: `#f97316`（オレンジ）
- 完了済みステップ: `#dcfce7`（グリーン）、取り消し線付きタイトル
- 接続縦線: `#fed7aa`

### 学習パス管理ページ（2カラムレイアウト）

```
左カラム (col-lg-7)          右カラム (col-lg-5, sticky)
┌────────────────────┐       ┌────────────────────────────┐
│ 現在のステップ        │       │ ステップを追加               │
│                    │       │ タイトル: [___________]     │
│ ① タイトル          │       │ URL:     [___________]     │
│   順番:[1] ✓  🗑  │       │ 説明:    [___________]     │
│                    │       │                            │
│ ② タイトル 2        │       │ 次は Step N               │
│   順番:[2] ✓  🗑  │       │ [ステップを追加]             │
└────────────────────┘       └────────────────────────────┘
```

---

## 依存関係・前提条件

- **フレームワーク**: FastAPI + Jinja2 + SQLAlchemy
- **CSS**: Bootstrap 5 + Bootstrap Icons
- **DB**: SQLite（本番は PostgreSQL 等に変更可）
- **認証**: セッションクッキーベース（`auth.require_approved` / `auth.require_manager_or_admin`）
- **前提テーブル**: `users`, `skills`, `categories`, `groups`, `group_memberships`
- **ファビコン取得**: Google Favicon API (`https://www.google.com/s2/favicons?domain=...&sz=32`)

---

## 組み込み手順（別環境への移植）

1. `models.py` に `EducationalLink` と `UserLearningProgress` クラスを追加
2. `main.py` 起動処理に `Base.metadata.create_all()` と `step_order` カラムの `ALTER TABLE` を追加
3. `routers/education.py` を追加し、`main.py` で `include_router`
4. テンプレート 3 ファイルを `templates/` に追加（`education.html`, `education_path.html`, `education_form.html`）
5. `base.html` のナビゲーションに `/education` リンクを追加（アイコン: `bi-signpost-split`、ラベル: 学習パス）
6. グループ管理ルーターの `_get_all_group_skill_ids` 関数が必要（グループ内スキルIDの再帰取得）
