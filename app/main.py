import os
import logging
import uuid
from fastapi import FastAPI, Depends, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session

import models
import auth
import database
from database import get_db, Base
from config import is_setup_complete, save_config
from template_engine import templates
from routers import skills as skills_router
from routers import admin as admin_router
from routers import groups as groups_router
from routers import tickets as tickets_router
from routers import education as education_router
from routers import announcements as announcements_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs("data", exist_ok=True)
os.makedirs("data/avatars", exist_ok=True)
Base.metadata.create_all(bind=database.engine)

from sqlalchemy import text as _text
with database.engine.connect() as _conn:
    try:
        _conn.execute(_text("ALTER TABLE educational_links ADD COLUMN step_order INTEGER"))
        _conn.commit()
    except Exception:
        pass  # カラム既存の場合は無視

app = FastAPI(title="Skill View.")

app.mount("/static", StaticFiles(directory="static"), name="static")


app.include_router(skills_router.router)
app.include_router(admin_router.router)
app.include_router(groups_router.router)
app.include_router(tickets_router.router)
app.include_router(education_router.router)
app.include_router(announcements_router.router)


# ─── 承認バッジ用ミドルウェア ─────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware

class PendingApprovalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.pending_approval_count = 0
        request.state.pending_user_count = 0
        request.state.rejected_approval_count = 0
        request.state.my_pending_count = 0
        db = database.SessionLocal()
        try:
            user = auth.get_current_user(request, db)
            if user and user.is_approved:
                if user.role == "admin":
                    # Admin は承認者の指定に関わらず、全ユーザーの保留中申請を承認できるため、
                    # ナビゲーションのバッジ／強調表示も全件数で表示する（/approvals の表示と一致させる）
                    count = (
                        db.query(models.UserSkillLevel)
                        .filter(models.UserSkillLevel.approval_status == "pending")
                        .count()
                    )
                    request.state.pending_approval_count = count
                elif user.role == "manager":
                    count = (
                        db.query(models.UserSkillLevel)
                        .filter(
                            models.UserSkillLevel.approver_id == user.id,
                            models.UserSkillLevel.approval_status == "pending",
                        )
                        .count()
                    )
                    request.state.pending_approval_count = count
                else:
                    # 一般ユーザー：自分の差し戻し件数・申請中件数
                    request.state.rejected_approval_count = (
                        db.query(models.UserSkillLevel)
                        .filter(
                            models.UserSkillLevel.user_id == user.id,
                            models.UserSkillLevel.approval_status == "rejected",
                        )
                        .count()
                    )
                    request.state.my_pending_count = (
                        db.query(models.UserSkillLevel)
                        .filter(
                            models.UserSkillLevel.user_id == user.id,
                            models.UserSkillLevel.approval_status == "pending",
                        )
                        .count()
                    )
            if user and user.role == "admin" and user.is_approved:
                request.state.pending_user_count = (
                    db.query(models.User)
                    .filter(models.User.is_approved == False)
                    .count()
                )
        except Exception:
            pass
        finally:
            db.close()
        response = await call_next(request)
        return response

app.add_middleware(PendingApprovalMiddleware)


# ─── 例外ハンドラ ──────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 302:
        location = exc.headers.get("Location", "/login")
        return RedirectResponse(url=location)
    if exc.status_code == 403:
        return templates.TemplateResponse(request, "error.html", {
            "code": 403, "message": exc.detail or "アクセス権限がありません"
        }, status_code=403)
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "error.html", {
            "code": 404, "message": "ページが見つかりません"
        }, status_code=404)
    return templates.TemplateResponse(request, "error.html", {
        "code": exc.status_code, "message": str(exc.detail)
    }, status_code=exc.status_code)


# ─── セットアップ ────────────────────────────────────────────────
@app.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request):
    if is_setup_complete():
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@app.post("/setup", response_class=HTMLResponse)
async def setup_post(
    request: Request,
    db: Session = Depends(get_db),
):
    if is_setup_complete():
        return RedirectResponse("/login", status_code=302)

    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    email = form.get("email", "").strip()

    if not username or not password:
        return templates.TemplateResponse(request, "setup.html", {
            "error": "ユーザー名とパスワードは必須です"
        })
    if len(password) < 6:
        return templates.TemplateResponse(request, "setup.html", {
            "error": "パスワードは6文字以上にしてください"
        })

    existing = db.query(models.User).filter(models.User.username == username).first()
    if existing:
        return templates.TemplateResponse(request, "setup.html", {
            "error": "そのユーザー名は既に使用されています"
        })

    # カテゴリ・スキルカタログ・サブスキルの初期データインポート（任意）
    catalog_import_data = None
    catalog_import_file = form.get("catalog_import_file")
    if catalog_import_file is not None and getattr(catalog_import_file, "filename", ""):
        import json as _json
        content = await catalog_import_file.read()
        try:
            catalog_import_data = _json.loads(content.decode("utf-8-sig"))
        except Exception:
            return templates.TemplateResponse(request, "setup.html", {
                "error": "カタログのインポートファイルの読み込みに失敗しました（一括エクスポートのJSON形式を確認してください）"
            })

    admin = models.User(
        username=username,
        email=email or None,
        display_name=username,
        password_hash=auth.hash_password(password),
        role="admin",
        is_approved=True,
    )
    db.add(admin)

    # SMTP 設定を保存（入力がある場合のみ）
    smtp_fields = {
        "smtp_host": form.get("smtp_host", "").strip(),
        "smtp_port": form.get("smtp_port", "").strip() or "587",
        "smtp_user": form.get("smtp_user", "").strip(),
        "smtp_password": form.get("smtp_password", "").strip(),
        "smtp_from_name": form.get("smtp_from_name", "").strip() or "スキルマップ",
        "app_url": form.get("app_url", "").strip() or "http://localhost:8190",
    }
    if smtp_fields["smtp_host"] and smtp_fields["smtp_user"]:
        for key, val in smtp_fields.items():
            row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
            if row:
                row.value = val
            else:
                db.add(models.AppSetting(key=key, value=val))

    db.commit()

    if catalog_import_data is not None:
        skills_router._apply_bulk_import(catalog_import_data, db)

    save_config({"setup_complete": True})
    return RedirectResponse("/login?msg=setup_done", status_code=303)


@app.on_event("startup")
def _startup():
    db = next(get_db())
    try:
        # ── Userテーブルへのカラム追加を最初に実行（ORM参照前に完了が必要） ──
        _migrate_user_suppress_ann_popup()
        _migrate_user_must_change_password()
        # マイグレーションを先に実行（モデルにカラムが追加されているため）
        _migrate_approval_columns(db)
        _migrate_skill_history_table()
        _migrate_avatar_column(db)
        _migrate_group_skills_table()
        _migrate_group_transfers_table()
        _migrate_group_parent_column()
        _migrate_tag_archive()

        user_count = db.query(models.User).count()
        if not is_setup_complete() and user_count > 0:
            save_config({"setup_complete": True})
        _migrate_group_managers_table(db)
        _migrate_tickets_tables()
        _migrate_education_table()
        _migrate_announcements_table()
        _migrate_sub_skills_table()
        _migrate_sub_skill_levels_table()
        _migrate_skill_evidences_table()
        _migrate_skill_evidence_columns()
        _migrate_user_skill_level_override()
        _migrate_skill_goals_table()
        _migrate_user_badges_table()
        _migrate_admin_todos_table()
        # suppress_ann_popup は冒頭で実行済み
        _migrate_categories(db)
        _sync_tier_names(db)
    finally:
        db.close()


def _migrate_categories(db):
    """カテゴリ名の改訂・新設・統合を既存DBに適用する（一度だけ実行・冪等）"""
    _MIGRATION_KEY = "category_migration_v1_done"
    if db.query(models.AppSetting).filter(models.AppSetting.key == _MIGRATION_KEY).first():
        return

    # 新規インストール（カテゴリが1件も無い）の場合は移行対象データが無いため、
    # フラグだけ立てて以降このマイグレーションを実行しないようにする。
    # ここでスキップしないと _get_or_create() が新規カテゴリを作成したり
    # サブスキルのシードが走ったりして、まっさらな環境にもデフォルトの
    # カテゴリ・スキルが投入されてしまう
    if db.query(models.Category).count() == 0:
        db.add(models.AppSetting(key=_MIGRATION_KEY, value="skipped_fresh_install"))
        db.commit()
        return

    from sqlalchemy.orm import Session as _Session

    def _get_or_create(name: str, color: str) -> models.Category:
        cat = db.query(models.Category).filter(models.Category.name == name).first()
        if not cat:
            cat = models.Category(name=name, color=color)
            db.add(cat)
            db.flush()
        return cat

    def _rename(old: str, new: str, color: str):
        cat = db.query(models.Category).filter(models.Category.name == old).first()
        if cat:
            cat.name = new
            cat.color = color
            db.flush()

    def _move_skills(from_name: str, to_name: str):
        src = db.query(models.Category).filter(models.Category.name == from_name).first()
        dst = db.query(models.Category).filter(models.Category.name == to_name).first()
        if src and dst:
            db.query(models.Skill).filter(models.Skill.category_id == src.id).update(
                {"category_id": dst.id}
            )
            db.flush()

    def _delete_empty(name: str):
        cat = db.query(models.Category).filter(models.Category.name == name).first()
        if cat and db.query(models.Skill).filter(models.Skill.category_id == cat.id).count() == 0:
            db.delete(cat)
            db.flush()

    def _move_skill_by_name(skill_name: str, to_cat_name: str):
        dst = db.query(models.Category).filter(models.Category.name == to_cat_name).first()
        skill = db.query(models.Skill).filter(models.Skill.name == skill_name).first()
        if skill and dst:
            skill.category_id = dst.id
            db.flush()

    def _delete_skill(skill_name: str):
        skill = db.query(models.Skill).filter(models.Skill.name == skill_name).first()
        if skill:
            db.delete(skill)
            db.flush()

    def _update_skill(skill_name: str, *, tier: str = None, desc: str = None, new_name: str = None):
        skill = db.query(models.Skill).filter(models.Skill.name == skill_name).first()
        if not skill:
            return
        if tier:
            skill.tier = tier
        if desc:
            skill.description = desc
        if new_name:
            skill.name = new_name
        db.flush()

    # ── カテゴリ名の変更 ──────────────────────────────────────
    _rename("HILS基盤・操作",        "HILS基盤・構築",            "#dc2626")
    _rename("dSPACEツール",          "HILSプラットフォームツール", "#2563eb")
    _rename("モデル開発（Simulink）", "Simulinkモデル開発",        "#f97316")
    _rename("車載・ECU・通信",       "制御・パワトレ知識",         "#374151")
    _rename("ハードウェア・W/H設計", "ハードウェア・W/H",          "#d97706")
    _rename("ソフト検証・テスト",    "ソフトウェアテスト",         "#7c3aed")
    _rename("DevOps・自動化",        "DevOps・開発基盤",           "#0f766e")

    # プログラミングはカラーだけ更新
    prog = db.query(models.Category).filter(models.Category.name == "プログラミング").first()
    if prog:
        prog.color = "#6366f1"
        db.flush()

    # ── テスト管理・品質 + プロジェクト管理 → 品質・プロセス管理 ──
    _rename("テスト管理・品質", "品質・プロセス管理", "#be185d")
    _move_skills("プロジェクト管理", "品質・プロセス管理")
    _delete_empty("プロジェクト管理")

    # ── 新カテゴリを作成 ──────────────────────────────────────
    _get_or_create("計測・診断ツール", "#0891b2")
    _get_or_create("テスト自動化",     "#059669")
    _get_or_create("車載通信・診断",   "#9333ea")

    # ── スキルの移動 ──────────────────────────────────────────
    # AutomationDesk は HILSプラットフォームツール → テスト自動化
    _move_skill_by_name("AutomationDesk 基礎", "テスト自動化")
    _move_skill_by_name("AutomationDesk 応用", "テスト自動化")
    # AutomationDesk・ecu.test の基礎/応用統合
    _update_skill("AutomationDesk 基礎", new_name="AutomationDesk", tier="intermediate",
                  desc="AutomationDeskでHILSテストシーケンスを作成・自動化し外部ツールと連携するスキル")
    _delete_skill("AutomationDesk 応用")
    _update_skill("ecu.test 基礎", new_name="ecu.test", tier="intermediate",
                  desc="ecu.testでHILSテストケースを作成・実行し結果管理・自動化まで行うスキル")
    _delete_skill("ecu.test 応用")
    # CANoe は ソフトウェアテスト → 計測・診断ツール
    _move_skill_by_name("CANoe 活用", "計測・診断ツール")
    # テスト自動化設計 は ソフトウェアテスト → テスト自動化
    _move_skill_by_name("テスト自動化設計", "テスト自動化")
    # HILSテスト自動化 は DevOps・開発基盤 → テスト自動化
    _move_skill_by_name("HILSテスト自動化", "テスト自動化")
    # CAN/LIN/Ethernet は 制御・パワトレ知識 → 車載通信・診断
    _move_skill_by_name("CAN通信",    "車載通信・診断")
    _move_skill_by_name("LIN通信",    "車載通信・診断")
    _move_skill_by_name("車載Ethernet", "車載通信・診断")

    # ── 名前の修正（前回マイグレーション誤変換の補正） ──────────
    _rename("xILSモデル開発", "Simulinkモデル開発", "#f97316")

    # ── スキルの削除（不要・重複） ────────────────────────────
    _delete_skill("CANoe 活用")               # 「CANoe 通信解析・シミュレーション」と重複
    _delete_skill("Simulink コード生成")       # 使用しない
    _delete_skill("Simulink Design Verifier") # 使用しない
    _delete_skill("SystemDesk / AUTOSAR設定") # 使用しない
    _delete_skill("GitHub / GitLab 操作")     # 「Git / GitHub」に統合
    _delete_skill("Git 基礎")                 # 「Git / GitHub」に統合（既存レコードを削除しても新規追加で補完）

    # ── スキルのtier・説明・名前の更新 ───────────────────────
    _update_skill("CAN通信",  tier="basic")
    _update_skill("LIN通信",  tier="basic")
    _update_skill("機能安全（ISO 26262）",
                  tier="intermediate",
                  desc="ASIL分類・安全要求・ハザード分析の概要理解（知識レベル）")
    _update_skill("インフラ管理・IaC",
                  new_name="クラウド・インフラ管理",
                  desc="AWS等クラウドサービス活用・サーバ管理・インフラ構築・コスト最適化")
    _update_skill("モデル結合・I/F設計",
                  desc="複数モデルの統合・信号インタフェース（Bus/Mux/Constant等）設計・検証")
    _update_skill("dSPACE VEOS（仮想ECU）", new_name="VEOS（仮想ECU）")
    _update_skill("Linux基礎",
                  new_name="コマンドライン操作",
                  desc="Linux bash / Windows PowerShell・コマンドプロンプト共通の操作。ls/dir・ping・grep・find等のコマンド活用")

    # 注意: 起動時のスキル自動追加はここでは行わない
    # DB が正（UIでの変更・削除を保持するため）
    # 新規インストール時もカテゴリ・スキルカタログは投入しない（管理画面から作成する）

    # ── Git / GitHub 基礎サブスキルのシード ─────────────────
    git_skill = db.query(models.Skill).filter(models.Skill.name == "Git / GitHub").first()
    if git_skill and db.query(models.SubSkill).filter(models.SubSkill.skill_id == git_skill.id).count() == 0:
        sub_skill_seeds = [
            ("リポジトリの作成",        "git init でローカルリポジトリを新規作成する"),
            ("リポジトリのクローン",    "git clone でリモートリポジトリをローカルに複製する"),
            ("git status（状態確認）",  "作業ツリー・インデックスの変更状態を確認する"),
            ("ブランチの作成",          "git branch / git checkout -b で新しいブランチを作成する"),
            ("ブランチの切り替え",      "git checkout / git switch で作業ブランチを切り替える"),
            ("ファイルのステージング",  "git add で変更ファイルをインデックスに登録する"),
            ("コミット",                "git commit でスナップショットをリポジトリに記録する"),
            ("git diff（差分確認）",    "git diff でステージング前後・コミット間の差分を確認する"),
            ("git log（履歴確認）",     "git log でコミット履歴・変更内容の一覧を確認する"),
            ("git fetch（フェッチ）",   "git fetch でリモートの変更情報を取得する（マージなし）"),
            ("git merge（マージ）",     "git merge で指定ブランチを現在のブランチに統合する"),
            ("コンフリクトの解消",      "マージ時の競合箇所を特定し手動で解決する"),
            ("リモートへのプッシュ",    "git push でローカルのコミットをリモートに送信する"),
            ("リモートからのプル",      "git pull でリモートの変更をローカルに取り込む（fetch+merge）"),
            ("プルリクエストの作成",    "GitHub上でPRを作成しレビュワーにレビューを依頼する"),
            ("プルリクエストのマージ",  "レビュー承認後にPRをベースブランチにマージする"),
            ("Issue管理",               "GitHub IssueでタスクやバグをトラッキングしClosedまで管理する"),
            ("ラベル活用",              "IssueやPRにラベルを付けて種別・優先度を分類する"),
            (".gitignoreの設定",        "不要ファイル・フォルダをバージョン管理対象から除外する"),
            ("git stash",               "作業中の変更を一時退避し後で復元する"),
        ]
        for i, (name, desc) in enumerate(sub_skill_seeds):
            db.add(models.SubSkill(skill_id=git_skill.id, name=name, description=desc, order_index=i))

    # ── Git / GitHub 応用スキル＋サブスキルのシード ──────────
    devops_cat = db.query(models.Category).filter(models.Category.name == "DevOps・開発基盤").first()
    adv_skill = db.query(models.Skill).filter(models.Skill.name == "Git / GitHub 応用").first()
    if not adv_skill and devops_cat:
        adv_skill = models.Skill(
            name="Git / GitHub 応用",
            description="ブランチ戦略・履歴操作・チーム運用など中級以上のGit/GitHub活用",
            category_id=devops_cat.id,
            tier="intermediate",
        )
        db.add(adv_skill)
        db.flush()
    if adv_skill and db.query(models.SubSkill).filter(models.SubSkill.skill_id == adv_skill.id).count() == 0:
        adv_seeds = [
            ("ブランチ戦略（GitHub Flow等）",   "チーム開発のブランチ運用ルールを設計・運用する"),
            ("ブランチルールの作成・管理",       "Branch protection rules / Rulesetsでマージ条件・レビュー必須・force push禁止等を設定する"),
            ("git rebase",                      "コミット履歴の整理・ブランチの付け替えを行う"),
            ("git reset",                       "コミットや変更を取り消す（--soft / --mixed / --hard）"),
            ("git revert",                      "既存コミットを打ち消す新コミットを作成する"),
            ("git tag",                         "リリースポイントにタグを付けてバージョンを管理する"),
            ("コードレビューの実施",            "PRのコードを読みレビューコメント・承認・変更要求を行う"),
            ("git log 応用",                    "--graph / --oneline / --author等で履歴を視覚的に分析する"),
            ("git bisect",                      "バイナリサーチでバグが混入したコミットを特定する"),
            ("git submodule",                   "外部リポジトリをサブモジュールとして組み込む・管理する"),
        ]
        for i, (name, desc) in enumerate(adv_seeds):
            db.add(models.SubSkill(skill_id=adv_skill.id, name=name, description=desc, order_index=i))
        db.flush()

    # ── GitHub Actions サブスキルのシード ─────────────────────
    actions_skill = db.query(models.Skill).filter(models.Skill.name == "GitHub Actions").first()
    if actions_skill and db.query(models.SubSkill).filter(models.SubSkill.skill_id == actions_skill.id).count() == 0:
        actions_seeds = [
            ("ワークフローファイルの作成",        ".github/workflows/ 配下にYAMLファイルを作成する"),
            ("トリガーの設定（on:）",             "push / pull_request / schedule / workflow_dispatch 等で起動条件を定義する"),
            ("ランナーの指定（runs-on:）",        "ubuntu-latest 等のGitHub提供ランナーを指定する"),
            ("ジョブとステップの定義",            "jobs: / steps: でタスクの実行単位を構成する"),
            ("アクションの利用（uses:）",         "actions/checkout 等の公開アクションをステップで利用する"),
            ("環境変数の設定（env:）",            "ワークフロー・ジョブ・ステップレベルで環境変数を定義する"),
            ("条件実行（if:）",                   "if: 式でステップ・ジョブの実行条件を制御する"),
            ("ジョブ間の依存関係（needs:）",      "needs: で複数ジョブの実行順序・依存を制御する"),
            ("アーティファクトの保存・取得",      "upload/download-artifact でジョブ間・ワークフロー間でファイルを受け渡す"),
            ("マトリックス戦略（matrix:）",       "複数OS・バージョンの組み合わせでジョブを並列実行する"),
            ("Variables（リポジトリ変数）の設定", "リポジトリ・環境レベルの変数を定義し ${{ vars.XXX }} で参照する"),
            ("Environments（デプロイ環境）の設定","ステージング・本番等の環境を定義し承認者・待機時間・保護ルールを設定する"),
            ("GitHub Secrets管理",               "APIキー等のシークレットを登録し ${{ secrets.XXX }} でワークフローに渡す"),
            ("再利用可能ワークフロー",            "workflow_call: で共通ワークフローを定義し他ワークフローから呼び出す"),
            ("セルフホストランナーの設定",        "自社環境のサーバをランナーとして登録しジョブを実行させる"),
        ]
        for i, (name, desc) in enumerate(actions_seeds):
            db.add(models.SubSkill(skill_id=actions_skill.id, name=name, description=desc, order_index=i))
        db.flush()

    # ── JIRA サブスキルのシード ───────────────────────────────
    def _seed_tool(skill_name, skill_desc, cat_name, tier, seeds):
        cat = db.query(models.Category).filter(models.Category.name == cat_name).first()
        skill = db.query(models.Skill).filter(models.Skill.name == skill_name).first()
        if not skill and cat:
            skill = models.Skill(name=skill_name, description=skill_desc, category_id=cat.id, tier=tier)
            db.add(skill)
            db.flush()
        if skill and db.query(models.SubSkill).filter(models.SubSkill.skill_id == skill.id).count() == 0:
            for i, (n, d) in enumerate(seeds):
                db.add(models.SubSkill(skill_id=skill.id, name=n, description=d, order_index=i))
            db.flush()

    _seed_tool("課題追跡・プロジェクト管理（JIRA）", "チームのタスク・バグ・スプリントをJIRAで管理するスキル",
               "品質・プロセス管理", "basic", [
        ("プロジェクトの作成・設定",          "スクラム/カンバンプロジェクトを作成し基本設定を行う"),
        ("Issue（チケット）の作成・管理",      "バグ・タスク・ストーリー等のIssueを作成し担当者・優先度を設定する"),
        ("バックログの管理・優先度付け",       "バックログ一覧でIssueを整理しスプリントへの振り分けを行う"),
        ("スプリントの計画・実行",             "スプリントを作成しチームの作業量を見積もり開始・完了させる"),
        ("ボードの活用（スクラム/カンバン）",  "ボード上でIssueのステータスを更新し進捗を可視化する"),
        ("ワークフローのカスタマイズ",         "Issueのステータス遷移・条件・バリデーションを定義する"),
        ("フィルター・JQLの活用",             "JQL（Jira Query Language）で条件を絞り込みIssueを検索する"),
        ("ダッシュボード・レポートの作成",     "バーンダウンチャートや速度レポートでスプリント状況を把握する"),
        ("Confluenceとの連携",               "JIRAのIssueをConfluenceページに埋め込み進捗を一元管理する"),
    ])

    _seed_tool("チームWiki・ナレッジ管理（Confluence）", "チームのナレッジをConfluenceで管理・共有するスキル",
               "品質・プロセス管理", "basic", [
        ("スペースの作成・管理",              "チーム・プロジェクト単位でスペースを作成しナビゲーションを整える"),
        ("ページの作成・編集",                "リッチテキストエディタでページを作成し見出し・表・画像を挿入する"),
        ("テンプレートの活用",               "会議メモ・仕様書・手順書等のテンプレートを使ってページを効率作成する"),
        ("ラベル・タグによる整理",            "ページにラベルを付けて検索性を高め関連ページをグルーピングする"),
        ("マクロの活用",                     "目次・コードブロック・JIRAリンク等のマクロでページを強化する"),
        ("JIRAとの連携",                     "JIRAのIssueをConfluenceに埋め込み仕様書と課題を紐付ける"),
        ("アクセス権限の設定",               "スペース・ページ単位で閲覧・編集権限をグループ・ユーザーに付与する"),
        ("ページツリーの設計",               "ドキュメント構造をページツリーで整理しチームの知識基盤を構築する"),
    ])

    _seed_tool("JFrog Artifactory", "ビルド成果物をArtifactoryで管理しCI/CDパイプラインに組み込むスキル",
               "DevOps・開発基盤", "intermediate", [
        ("リポジトリの作成・設定",            "Local/Remote/Virtualリポジトリを作成しパッケージ形式を設定する"),
        ("アーティファクトのアップロード",    "CLIやCIジョブからビルド成果物をArtifactoryにアップロードする"),
        ("アーティファクトのダウンロード・参照", "リポジトリからパッケージを取得しバージョンを管理する"),
        ("CI/CDパイプラインとの連携",         "GitHub Actions等のCIからArtifactoryへ自動デプロイする"),
        ("アクセス権限・ユーザー管理",        "グループ・ロール・パーミッションターゲットでアクセスを制御する"),
        ("コンポーネント検索・バージョン管理", "AQLやUIで成果物を検索しバージョン履歴・メタデータを確認する"),
        ("クリーンアップポリシーの設定",      "保持ポリシーを設定し古いアーティファクトを自動削除する"),
        ("Xray連携（セキュリティスキャン）",  "JFrog Xrayで依存パッケージの脆弱性・ライセンス違反をスキャンする"),
    ])

    _seed_tool("ビジネスチャット（Teams）", "Teamsでチームコミュニケーション・会議・ファイル共有を行うスキル",
               "品質・プロセス管理", "basic", [
        ("チャンネルの作成・管理",        "チームにチャンネルを作成しトピック別にコミュニケーションを整理する"),
        ("ミーティングの設定・開催",      "会議を設定し画面共有・レコーディング・ブレイクアウトを活用する"),
        ("ファイル共有・共同編集",        "チャンネルにファイルをアップロードしOfficeファイルをリアルタイム共同編集する"),
        ("通知設定のカスタマイズ",        "チャンネル・メンション・キーワードの通知をニーズに合わせて設定する"),
        ("タブ・アプリの追加",            "JIRAやConfluence等のアプリをタブとして追加しTeamsから直接操作する"),
        ("Webhookによる通知連携",         "受信Webhookを設定しCI/CDやモニタリング結果をTeamsチャンネルに通知する"),
        ("Teamsフォンの活用",            "通話・転送・ボイスメールを活用し社内コミュニケーションを一元化する"),
    ])

    _seed_tool("ビジネスチャット（Slack）", "Slackでチームコミュニケーション・通知連携・ワークフローを行うスキル",
               "品質・プロセス管理", "basic", [
        ("チャンネルの作成・管理",        "パブリック/プライベートチャンネルを作成しトピック・用途で整理する"),
        ("メッセージ活用（スレッド・書式）", "スレッドで返信を整理しMarkdown記法で見やすいメッセージを作成する"),
        ("ワークフロービルダー",          "定型業務（日次報告・承認依頼等）をワークフローで自動化する"),
        ("アプリ・連携の設定",            "GitHub/JIRA/Grafana等の外部サービスを連携してSlackで情報を集約する"),
        ("Incoming Webhook通知",          "CI/CDやモニタリングの結果をWebhook経由でSlackチャンネルに送信する"),
        ("リマインダー・スケジュール投稿", "/remindコマンドやスケジュール投稿で定時通知・タスクリマインドを行う"),
        ("Slashコマンドの活用",           "/コマンドでJIRA・GitHubショートカット等を素早く操作する"),
    ])

    _seed_tool("ドキュメント・ファイル管理（SharePoint）", "SharePointでチームサイト・ドキュメント管理・社内ポータルを構築するスキル",
               "品質・プロセス管理", "basic", [
        ("サイトの作成・設定",            "チームサイト/コミュニケーションサイトを作成しナビゲーションを設定する"),
        ("ドキュメントライブラリの管理",  "フォルダ構造を設計しファイルのアップロード・バージョン管理を行う"),
        ("リスト・フォームの作成",        "カスタムリストで台帳・進捗管理を行いフォームで入力UIを整える"),
        ("アクセス権限の設定",            "サイト・ライブラリ・フォルダ単位でグループ・ユーザーの権限を管理する"),
        ("バージョン管理・チェックアウト", "ファイルのバージョン履歴を確認しチェックアウトで同時編集を防ぐ"),
        ("検索・メタデータの活用",        "メタデータ列を追加しフィルタリング・検索でファイルを効率的に見つける"),
        ("Power Platformとの連携",        "Power AutomateやPower AppsからSharePointリスト・ライブラリを操作する"),
        ("Teamsタブへの追加",             "SharePointページ・ライブラリをTeamsタブに追加してアクセスを一元化する"),
    ])

    _seed_tool("業務フロー自動化（Power Automate）", "業務フローをPower Automateで自動化するスキル",
               "品質・プロセス管理", "basic", [
        ("フローの作成（自動化/インスタント/スケジュール）",
                                          "トリガー種別に応じたフローを作成し自動実行の仕組みを構築する"),
        ("トリガーとアクションの設定",    "SharePoint更新・Teams受信・定時等のトリガーとアクションを組み合わせる"),
        ("条件分岐・ループの活用",        "ConditionやApply to eachで分岐・繰り返し処理を実装する"),
        ("SharePoint / Teams連携フロー",  "SharePointのリスト変更を起点にTeamsへ通知するフローを作成する"),
        ("承認フローの構築",              "承認アクションで多段階の承認プロセスをフロー化し結果をメールで通知する"),
        ("変数・式の活用",                "変数の初期化・設定と式エディタでデータ加工・文字列操作を行う"),
        ("エラーハンドリングと再試行",    "スコープとRunAfterでエラー時の処理を設定し失敗を検知・通知する"),
        ("HTTPアクション・Webhook連携",   "HTTPアクションで外部API呼び出しやWebhook送信を組み込む"),
    ])

    _seed_tool("データ可視化・BI（Power BI）", "データをPower BIで分析・可視化しレポート・ダッシュボードを作成するスキル",
               "プログラミング", "basic", [
        ("データソースへの接続",          "Excel/SharePoint/SQL/APIなど各種データソースに接続しデータを取り込む"),
        ("Power Queryでのデータ整形",     "Power Queryエディタで列の変換・フィルタ・結合・型変換を行う"),
        ("ビジュアルの作成",              "棒グラフ・折れ線・テーブル・マップ等のビジュアルを配置しデータを表現する"),
        ("レポートの設計",                "ページ・フィルター・スライサーを組み合わせてインタラクティブなレポートを作る"),
        ("ダッシュボードの作成",          "レポートのビジュアルをピン留めしリアルタイム更新ダッシュボードを構築する"),
        ("DAX関数の活用",                 "SUM/CALCULATE/FILTER等のDAX関数でカスタム集計・メジャーを作成する"),
        ("データモデルの設計",            "テーブル間のリレーションシップを定義しスタースキーマを構築する"),
        ("レポートの共有・パブリッシュ",  "Power BI Serviceに発行しワークスペース共有・アクセス権を設定する"),
    ])

    _seed_tool("ローコードアプリ開発（Power Apps）", "Power Appsでローコードビジネスアプリを開発するスキル",
               "プログラミング", "basic", [
        ("キャンバスアプリの作成",        "白紙のキャンバスからUI部品をドラッグ&ドロップで配置してアプリを構築する"),
        ("データソースへの接続",          "SharePoint/Dataverse/SQL等に接続しアプリからデータを読み書きする"),
        ("フォーム・ギャラリーの設計",    "フォームで入力UIを作成しギャラリーで一覧表示を実装する"),
        ("Power Fx数式の活用",            "If/Filter/LookUp/Patch等の数式でロジック・データ操作を実装する"),
        ("ボタン・ナビゲーションの実装",  "ボタンのOnSelect・Navigateで画面遷移・操作フローを構築する"),
        ("バリデーション・エラー処理",    "入力値の検証とNotify関数でユーザーへのフィードバックを実装する"),
        ("Power Automateとの連携",        "アプリからPower Automateフローを呼び出し複雑な処理をフロー側に委譲する"),
        ("アプリの共有・展開",            "ユーザー・グループへアプリを共有しTeamsタブとして公開する"),
    ])

    db.add(models.AppSetting(key=_MIGRATION_KEY, value="done"))
    db.commit()


def _migrate_sub_skills_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "sub_skills" not in insp.get_table_names():
        models.SubSkill.__table__.create(bind=database.engine)


def _migrate_sub_skill_levels_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "user_sub_skill_levels" not in insp.get_table_names():
        models.UserSubSkillLevel.__table__.create(bind=database.engine)


def _migrate_skill_evidences_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "skill_evidences" not in insp.get_table_names():
        models.SkillEvidence.__table__.create(bind=database.engine)


def _migrate_skill_evidence_columns():
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("skill_evidences")]
    with database.engine.begin() as conn:
        if "file_path" not in cols:
            conn.execute(text("ALTER TABLE skill_evidences ADD COLUMN file_path VARCHAR(500)"))
        if "original_filename" not in cols:
            conn.execute(text("ALTER TABLE skill_evidences ADD COLUMN original_filename VARCHAR(255)"))


def _migrate_user_skill_level_override():
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("user_skill_levels")]
    with database.engine.begin() as conn:
        if "override_level" not in cols:
            conn.execute(text("ALTER TABLE user_skill_levels ADD COLUMN override_level INTEGER"))
        if "override_reason" not in cols:
            conn.execute(text("ALTER TABLE user_skill_levels ADD COLUMN override_reason TEXT"))


def _migrate_skill_goals_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "skill_goals" not in insp.get_table_names():
        models.SkillGoal.__table__.create(bind=database.engine)


def _migrate_user_badges_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "user_badges" not in insp.get_table_names():
        models.UserBadge.__table__.create(bind=database.engine)


def _migrate_admin_todos_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "admin_todos" not in insp.get_table_names():
        models.AdminTodo.__table__.create(bind=database.engine)


def _migrate_user_suppress_ann_popup():
    from sqlalchemy import inspect as sa_inspect, text as _txt
    insp = sa_inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("users")]
    if "suppress_ann_popup" not in cols:
        with database.engine.begin() as conn:
            conn.execute(_txt("ALTER TABLE users ADD COLUMN suppress_ann_popup BOOLEAN DEFAULT 0 NOT NULL"))


def _migrate_user_must_change_password():
    from sqlalchemy import inspect as sa_inspect, text as _txt
    insp = sa_inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("users")]
    if "must_change_password" not in cols:
        with database.engine.begin() as conn:
            conn.execute(_txt("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0 NOT NULL"))


def _migrate_announcements_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "announcements" not in insp.get_table_names():
        models.Announcement.__table__.create(bind=database.engine)


def _migrate_education_table():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "educational_links" not in insp.get_table_names():
        models.EducationalLink.__table__.create(bind=database.engine)


def _migrate_tickets_tables():
    """tickets / ticket_messages テーブルを作成（未存在の場合）"""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    names = insp.get_table_names()
    if "tickets" not in names:
        models.Ticket.__table__.create(bind=database.engine)
    if "ticket_messages" not in names:
        models.TicketMessage.__table__.create(bind=database.engine)


def _migrate_group_managers_table(db):
    """group_managers テーブルを作成し、既存の manager_id を移行する"""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(database.engine)
    if "group_managers" not in insp.get_table_names():
        models.group_managers.create(bind=database.engine)
        # 既存グループの manager_id を group_managers に移行
        groups = db.query(models.Group).filter(models.Group.manager_id.isnot(None)).all()
        for g in groups:
            exists = db.execute(
                text("SELECT 1 FROM group_managers WHERE group_id=:gid AND user_id=:uid"),
                {"gid": g.id, "uid": g.manager_id}
            ).first()
            if not exists:
                db.execute(
                    text("INSERT INTO group_managers (group_id, user_id) VALUES (:gid, :uid)"),
                    {"gid": g.id, "uid": g.manager_id}
                )
        db.commit()


def _sync_tier_names(db):
    """DB保存のティア名が旧デフォルト（ビギナー等）なら新デフォルトに更新する"""
    OLD_NAMES = {"ビギナー", "ベーシック", "アドバンスド", "エキスパート", "初心者向け"}
    for key, new_name in models.DEFAULT_TIER_NAMES.items():
        setting = db.query(models.AppSetting).filter(
            models.AppSetting.key == f"tier_name_{key}"
        ).first()
        if setting and setting.value in OLD_NAMES:
            setting.value = new_name
    db.commit()


def _migrate_approval_columns(db):
    """user_skill_levels テーブルに承認関連カラムを追加（既存DBの互換性維持）"""
    from sqlalchemy import text, inspect
    insp = inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("user_skill_levels")]
    with database.engine.begin() as conn:
        if "approval_status" not in cols:
            conn.execute(text(
                "ALTER TABLE user_skill_levels ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'"
            ))
        if "approver_id" not in cols:
            conn.execute(text(
                "ALTER TABLE user_skill_levels ADD COLUMN approver_id INTEGER"
            ))
        if "approved_at" not in cols:
            conn.execute(text(
                "ALTER TABLE user_skill_levels ADD COLUMN approved_at DATETIME"
            ))
        if "approver_comment" not in cols:
            conn.execute(text(
                "ALTER TABLE user_skill_levels ADD COLUMN approver_comment TEXT"
            ))


def _migrate_skill_history_table():
    """skill_level_history テーブルが存在しなければ作成"""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "skill_level_history" not in insp.get_table_names():
        models.SkillLevelHistory.__table__.create(bind=database.engine)


def _migrate_avatar_column(db):
    """users テーブルに avatar_path カラムを追加（既存DBの互換性維持）"""
    from sqlalchemy import text, inspect
    insp = inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("users")]
    if "avatar_path" not in cols:
        with database.engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar_path VARCHAR(255)"))


def _migrate_group_skills_table():
    """group_skills テーブルが存在しなければ作成"""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "group_skills" not in insp.get_table_names():
        models.group_skills.create(bind=database.engine)


def _migrate_group_transfers_table():
    """group_transfers テーブルが存在しなければ作成"""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(database.engine)
    if "group_transfers" not in insp.get_table_names():
        models.GroupTransfer.__table__.create(bind=database.engine)


def _migrate_group_parent_column():
    """groups テーブルに parent_id カラムを追加（既存DBの互換性維持）"""
    from sqlalchemy import text, inspect
    insp = inspect(database.engine)
    cols = [c["name"] for c in insp.get_columns("groups")]
    if "parent_id" not in cols:
        with database.engine.begin() as conn:
            conn.execute(text("ALTER TABLE groups ADD COLUMN parent_id INTEGER REFERENCES groups(id)"))


def _migrate_tag_archive():
    """skill_tags・skill_tag_associations テーブル作成と skills.is_archived カラム追加"""
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(database.engine)
    table_names = insp.get_table_names()

    if "skill_tags" not in table_names:
        models.SkillTag.__table__.create(bind=database.engine)
    if "skill_tag_associations" not in table_names:
        models.skill_tag_associations.create(bind=database.engine)

    cols = [c["name"] for c in insp.get_columns("skills")]
    if "is_archived" not in cols:
        with database.engine.begin() as conn:
            conn.execute(text("ALTER TABLE skills ADD COLUMN is_archived BOOLEAN DEFAULT 0 NOT NULL"))


# ─── ルート ──────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    if not is_setup_complete():
        return RedirectResponse("/setup")
    user = auth.get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if not user.is_approved:
        return RedirectResponse("/pending")
    return RedirectResponse("/dashboard")


# ─── 認証 ────────────────────────────────────────────────────────
@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_get(request: Request, sent: str = "", error: str = ""):
    return templates.TemplateResponse(request, "forgot_password.html", {
        "sent": sent, "error": error,
    })


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_post(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    import mail as mail_mod
    email = email.strip()
    if not email:
        return RedirectResponse("/forgot-password?error=メールアドレスを入力してください", status_code=302)

    user = db.query(models.User).filter(models.User.email == email).first()
    if user and user.is_approved:
        # トークン生成（30分有効）
        token = auth.serializer.dumps({"uid": user.id, "purpose": "pw_reset"})
        settings = mail_mod._get_settings()
        app_url = settings.get("app_url", "http://localhost:8190").rstrip("/")
        reset_url = f"{app_url}/reset-password?token={token}"
        mail_mod.send_password_reset_mail(
            to_email=user.email,
            display_name=user.display_name or user.username,
            reset_url=reset_url,
        )
    # ユーザーの有無に関係なく同じメッセージ（情報漏洩防止）
    return RedirectResponse("/forgot-password?sent=1", status_code=302)


RESET_TOKEN_MAX_AGE = 1800  # 30分


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_get(request: Request, token: str = "", error: str = ""):
    if not token:
        return RedirectResponse("/forgot-password")
    # トークン有効性チェック
    try:
        data = auth.serializer.loads(token, max_age=RESET_TOKEN_MAX_AGE)
        if data.get("purpose") != "pw_reset":
            raise Exception("invalid purpose")
    except Exception:
        return templates.TemplateResponse(request, "reset_password.html", {
            "error": "リンクの有効期限が切れているか、無効なリンクです。再度リセットを申請してください。",
            "token": "",
        })
    return templates.TemplateResponse(request, "reset_password.html", {
        "error": error, "token": token,
    })


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_post(
    request: Request,
    token: str = Form(""),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
):
    if not token:
        return RedirectResponse("/forgot-password")

    try:
        data = auth.serializer.loads(token, max_age=RESET_TOKEN_MAX_AGE)
        if data.get("purpose") != "pw_reset":
            raise Exception("invalid purpose")
        user_id = data["uid"]
    except Exception:
        return templates.TemplateResponse(request, "reset_password.html", {
            "error": "リンクの有効期限が切れています。再度リセットを申請してください。",
            "token": "",
        })

    if len(new_password) < 6:
        return templates.TemplateResponse(request, "reset_password.html", {
            "error": "パスワードは6文字以上にしてください。",
            "token": token,
        })

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return RedirectResponse("/forgot-password?error=ユーザーが見つかりません", status_code=302)

    user.password_hash = auth.hash_password(new_password)
    db.commit()
    return RedirectResponse("/login?msg=password_reset", status_code=303)


PROMO_VIDEO_PATH = os.path.join("data", "promo.mp4")


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, msg: str = ""):
    if not is_setup_complete():
        return RedirectResponse("/setup")
    return templates.TemplateResponse(request, "login.html", {
        "error": None,
        "setup_done": msg == "setup_done",
        "password_reset": msg == "password_reset",
        "promo_video_exists": os.path.isfile(PROMO_VIDEO_PATH),
    })


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not auth.verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {
            "error": "ユーザー名またはパスワードが違います",
            "setup_done": False, "password_reset": False,
        })
    if not user.is_approved:
        response = RedirectResponse("/pending", status_code=303)
        auth.create_session_cookie(response, user.id)
        return response
    # 仮パスワードログイン → 強制パスワード変更
    if getattr(user, "must_change_password", False):
        response = RedirectResponse("/change-password", status_code=303)
        auth.create_session_cookie(response, user.id)
        return response
    response = RedirectResponse("/dashboard", status_code=303)
    auth.create_session_cookie(response, user.id)
    return response


@app.get("/change-password", response_class=HTMLResponse)
def change_password_get(request: Request, db: Session = Depends(get_db)):
    """仮パスワードログイン後の強制パスワード変更ページ"""
    user = auth.get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if not getattr(user, "must_change_password", False):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(request, "change_password.html", {
        "current_user": user, "error": None
    })


@app.post("/change-password", response_class=HTMLResponse)
def change_password_post(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if new_password != confirm_password:
        return templates.TemplateResponse(request, "change_password.html", {
            "current_user": user, "error": "パスワードが一致しません"
        })
    if len(new_password) < 8:
        return templates.TemplateResponse(request, "change_password.html", {
            "current_user": user, "error": "パスワードは8文字以上で設定してください"
        })
    user.password_hash = auth.hash_password(new_password)
    user.must_change_password = False
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    if not is_setup_complete():
        return RedirectResponse("/setup")
    return templates.TemplateResponse(request, "register.html", {"error": None})


@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(""),
    email: str = Form(""),
    display_name: str = Form(""),
    db: Session = Depends(get_db),
):
    if len(password) < 6:
        return templates.TemplateResponse(request, "register.html", {
            "error": "パスワードは6文字以上にしてください"
        })
    if password_confirm and password != password_confirm:
        return templates.TemplateResponse(request, "register.html", {
            "error": "パスワードと確認用パスワードが一致しません"
        })
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse(request, "register.html", {
            "error": "そのユーザー名は既に使用されています"
        })

    user = models.User(
        username=username,
        email=email or None,
        display_name=display_name or username,
        password_hash=auth.hash_password(password),
        role="user",
        is_approved=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Admin / Manager にメール通知
    import mail
    if mail.is_mail_configured():
        admins_managers = db.query(models.User).filter(
            models.User.role.in_(["admin", "manager"]),
            models.User.is_approved == True,
            models.User.email != None,
        ).all()
        for am in admins_managers:
            mail.send_registration_notice(
                admin_email=am.email,
                admin_name=am.display_name or am.username,
                new_username=username,
                new_display_name=display_name or username,
                new_email=email or "",
            )

    response = RedirectResponse("/pending", status_code=303)
    auth.create_session_cookie(response, user.id)
    return response


@app.get("/pending", response_class=HTMLResponse)
def pending(request: Request, db: Session = Depends(get_db)):
    user = auth.get_current_user(request, db)
    return templates.TemplateResponse(request, "pending.html", {
        "current_user": user
    })


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


# ─── マニュアル ──────────────────────────────────────────────
@app.get("/manual", response_class=HTMLResponse)
def manual_page(request: Request, db: Session = Depends(get_db)):
    current_user = auth.get_current_user(request, db)
    return templates.TemplateResponse(request, "manual.html", {
        "current_user": current_user,
    })


@app.get("/manual/admin", response_class=HTMLResponse)
def manual_admin_page(request: Request, db: Session = Depends(get_db)):
    current_user = auth.get_current_user(request, db)
    return templates.TemplateResponse(request, "manual_admin.html", {
        "current_user": current_user,
        "unlocked": False,
        "error": None,
    })


@app.post("/manual/admin", response_class=HTMLResponse)
async def manual_admin_verify(request: Request, db: Session = Depends(get_db)):
    current_user = auth.get_current_user(request, db)
    form = await request.form()
    password = form.get("password", "")

    # Admin ユーザーのパスワードで認証
    admin_user = (db.query(models.User)
                  .filter(models.User.role == "admin")
                  .first())
    if admin_user and auth.verify_password(password, admin_user.password_hash):
        return templates.TemplateResponse(request, "manual_admin.html", {
            "current_user": current_user,
            "unlocked": True,
            "error": None,
        })
    return templates.TemplateResponse(request, "manual_admin.html", {
        "current_user": current_user,
        "unlocked": False,
        "error": "パスワードが正しくありません",
    })


# ─── ダッシュボード ──────────────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user_id: int = 0,
    group_id: int = 0,
    db: Session = Depends(get_db),
):
    current_user = auth.require_approved(request, db)

    # ── フィルター対象を決定 ─────────────────────────────────────
    is_privileged = current_user.role in ("admin", "manager")
    view_mode = "self"          # self / user / group / all
    view_label = f"{current_user.display_name or current_user.username} さんのスキル状況"
    target_user = None
    target_group = None

    if is_privileged and user_id:
        target_user = db.query(models.User).filter(models.User.id == user_id).first()
        if target_user:
            view_mode = "user"
            view_label = f"{target_user.display_name or target_user.username} さんのスキル状況"
    elif is_privileged and group_id:
        target_group = db.query(models.Group).filter(models.Group.id == group_id).first()
        if target_group:
            view_mode = "group"
            view_label = f"グループ「{target_group.name}」のスキル状況"
    elif is_privileged and not user_id and not group_id:
        # Admin/Manager はデフォルトで全メンバー概要を表示
        view_mode = "all"
        view_label = "全メンバーのスキル状況" if current_user.role == "admin" else "担当メンバーのスキル状況"

    # ── ユーザー/グループ候補一覧（フィルターUI用） ──────────────
    all_users = []
    all_groups = []
    if is_privileged:
        all_users = (db.query(models.User)
                     .filter(models.User.is_approved == True)
                     .order_by(models.User.display_name).all())
        if current_user.role == "admin":
            all_groups = db.query(models.Group).order_by(models.Group.name).all()
        else:
            all_groups = (db.query(models.Group)
                          .filter(models.Group.manager_id == current_user.id)
                          .order_by(models.Group.name).all())

    # ── データ取得 ───────────────────────────────────────────────
    # Manager は担当グループのスキルのみカウント
    if current_user.role == "manager" and view_mode == "all":
        from routers.groups import _get_all_group_skill_ids as _gids_dash
        _mgr_skill_ids_dash: set[int] = set()
        for _dg in (db.query(models.Group)
                    .filter(models.Group.manager_id == current_user.id)
                    .all()):
            _mgr_skill_ids_dash.update(_gids_dash(_dg))
        # group_managers テーブルからも取得
        from sqlalchemy import text as _txt_dash
        for _row in db.execute(
            _txt_dash("SELECT DISTINCT group_id FROM group_managers WHERE user_id=:uid"),
            {"uid": current_user.id}
        ).fetchall():
            _g2 = db.query(models.Group).filter(models.Group.id == _row[0]).first()
            if _g2:
                _mgr_skill_ids_dash.update(_gids_dash(_g2))
        catalog_total = len(_mgr_skill_ids_dash)
    else:
        catalog_total = db.query(models.Skill).count()
    categories = db.query(models.Category).order_by(models.Category.name).all()

    # ── 共通分析データ計算 ───────────────────────────────────────
    from datetime import datetime, timedelta
    from collections import defaultdict

    # 1. カテゴリ別スキルレベル時系列トレンド（過去6ヶ月）
    skill_trends: dict = {}
    try:
        six_months_ago = datetime.utcnow() - timedelta(days=180)
        trend_histories = (db.query(models.SkillLevelHistory)
                           .filter(models.SkillLevelHistory.changed_at >= six_months_ago)
                           .all())
        monthly_data: dict = defaultdict(lambda: defaultdict(list))
        for _h in trend_histories:
            if _h.skill and _h.skill.category:
                _cat = _h.skill.category.name
                _month = _h.changed_at.strftime("%Y-%m")
                monthly_data[_cat][_month].append(_h.level)
        for _cat, _months in monthly_data.items():
            _trend = []
            for _m in sorted(_months.keys()):
                _lvls = _months[_m]
                _trend.append({"month": _m, "avg_level": round(sum(_lvls) / len(_lvls), 2)})
            skill_trends[_cat] = _trend
    except Exception:
        skill_trends = {}

    # 2. バスファクター1スキル一覧（承認済みレベル>=2の人数が1以下のスキル）
    bus_factor_skills: list = []
    try:
        _bf_catalog = db.query(models.Skill).all()
        for _sk in _bf_catalog:
            try:
                if _sk.is_archived:
                    continue
            except AttributeError:
                pass
            _holders = (db.query(models.UserSkillLevel)
                        .filter(
                            models.UserSkillLevel.skill_id == _sk.id,
                            models.UserSkillLevel.approval_status == "approved",
                            models.UserSkillLevel.level >= 2,
                        ).count())
            if _holders <= 1:
                bus_factor_skills.append({
                    "skill_id": _sk.id,
                    "skill_name": _sk.name,
                    "category_name": _sk.category.name if _sk.category else "",
                    "holder_count": _holders,
                })
    except Exception:
        bus_factor_skills = []

    if view_mode == "all":
        # 管理者概要モード: Admin=全ユーザー, Manager=担当グループのメンバー
        from collections import defaultdict
        if current_user.role == "admin":
            target_members = (db.query(models.User)
                              .filter(models.User.is_approved == True)
                              .order_by(models.User.display_name).all())
        else:
            # Manager: 担当グループのメンバー (重複排除)
            member_ids_set = set()
            for g in all_groups:
                for m in g.memberships:
                    member_ids_set.add(m.user_id)
            target_members = (db.query(models.User)
                              .filter(models.User.id.in_(member_ids_set))
                              .order_by(models.User.display_name).all()) if member_ids_set else []

        member_id_list = [m.id for m in target_members]
        all_levels = (db.query(models.UserSkillLevel)
                      .filter(
                          models.UserSkillLevel.user_id.in_(member_id_list),
                          models.UserSkillLevel.approval_status == "approved",
                      ).all()) if member_id_list else []

        total_members = len(target_members)
        total = sum(1 for sl in all_levels if sl.level > 0)
        avg_level = round(sum(sl.level for sl in all_levels) / len(all_levels), 1) if all_levels else 0.0
        max_sl = max(all_levels, key=lambda sl: sl.level, default=None)

        level_dist = {i: 0 for i in range(5)}
        for sl in all_levels:
            level_dist[sl.level] += 1

        cat_stats: dict[str, int] = {}
        for sl in all_levels:
            if sl.skill.category:
                n = sl.skill.category.name
                cat_stats[n] = cat_stats.get(n, 0) + 1
        top_cats = sorted(cat_stats.items(), key=lambda x: x[1], reverse=True)[:8]

        tier_stats: dict[str, dict] = {}
        for tier_key in models.SKILL_TIERS:
            tier_catalog = db.query(models.Skill).filter(models.Skill.tier == tier_key).count()
            tier_done = sum(1 for sl in all_levels if sl.skill.tier == tier_key and sl.level > 0)
            tier_stats[tier_key] = {
                "total": tier_catalog * max(total_members, 1),
                "done": tier_done,
            }

        # メンバーごとのレーダーデータ (上位8名)
        member_radar = {}
        member_summary = []
        for m in target_members:
            m_levels = [sl for sl in all_levels if sl.user_id == m.id]
            m_total = sum(1 for sl in m_levels if sl.level > 0)
            m_avg = round(sum(sl.level for sl in m_levels) / len(m_levels), 1) if m_levels else 0.0
            member_summary.append({"user": m, "total": m_total, "avg": m_avg})
            cat_avg = {}
            for c in categories:
                c_levels = [sl.level for sl in m_levels if sl.skill.category and sl.skill.category.name == c.name]
                cat_avg[c.name] = round(sum(c_levels) / len(c_levels), 1) if c_levels else 0
            member_radar[m.id] = cat_avg
        member_summary.sort(key=lambda x: x["avg"], reverse=True)

        # グループ別サマリー
        group_summary = []
        for g in all_groups:
            g_member_ids = [m.user_id for m in g.memberships]
            g_levels = [sl for sl in all_levels if sl.user_id in g_member_ids]
            g_total = sum(1 for sl in g_levels if sl.level > 0)
            g_avg = round(sum(sl.level for sl in g_levels) / len(g_levels), 1) if g_levels else 0.0
            group_summary.append({
                "group": g,
                "member_count": len(g_member_ids),
                "total": g_total,
                "avg": g_avg,
            })
        group_summary.sort(key=lambda x: x["avg"], reverse=True)

        recent = sorted([sl for sl in all_levels if sl.level > 0],
                        key=lambda sl: sl.updated_at or sl.skill.created_at,
                        reverse=True)[:15]

        # ── スキルカバレッジ分析 ─────────────────────────────────
        # Manager は担当グループに割り当てられたスキルのみ対象
        if current_user.role == "manager" and all_groups:
            from routers.groups import _get_all_group_skill_ids as _gcov_ids
            _manager_skill_ids: set[int] = set()
            for _g in all_groups:
                _manager_skill_ids.update(_gcov_ids(_g))
            all_catalog = db.query(models.Skill).filter(
                models.Skill.id.in_(_manager_skill_ids)
            ).all() if _manager_skill_ids else []
        else:
            all_catalog = db.query(models.Skill).all()
        coverage_data = []
        for sk in all_catalog:
            holders = [sl for sl in all_levels if sl.skill_id == sk.id and sl.level > 0]
            holder_count = len(holders)
            avg_lv = round(sum(h.level for h in holders) / holder_count, 1) if holder_count else 0.0
            risk = "vacant" if holder_count == 0 else ("single" if holder_count == 1 else "none")
            holder_names = [sl.user.display_name or sl.user.username for sl in holders]
            coverage_data.append({
                "skill": sk,
                "holder_count": holder_count,
                "avg_level": avg_lv,
                "risk": risk,
                "holders": holder_names,
            })
        coverage_data.sort(key=lambda x: x["holder_count"])
        cov_total = len(all_catalog)
        cov_covered = sum(1 for c in coverage_data if c["holder_count"] > 0)
        cov_single = sum(1 for c in coverage_data if c["risk"] == "single")
        cov_vacant = sum(1 for c in coverage_data if c["risk"] == "vacant")
        coverage_summary = {
            "total_skills": cov_total,
            "covered": cov_covered,
            "single_holder": cov_single,
            "vacant": cov_vacant,
            "coverage_pct": round(cov_covered / cov_total * 100, 1) if cov_total else 0,
        }
        # カテゴリー別カバレッジ
        cat_coverage = []
        for c in categories:
            c_skills = [sk for sk in all_catalog if sk.category_id == c.id]
            c_covered = sum(1 for sk in c_skills if any(sl.skill_id == sk.id and sl.level > 0 for sl in all_levels))
            cat_coverage.append({
                "name": c.name,
                "color": c.color,
                "total": len(c_skills),
                "covered": c_covered,
                "pct": round(c_covered / len(c_skills) * 100) if c_skills else 0,
            })

        # 成長速度ランキング: 直近30日のレベルアップ回数・合計上昇量
        from datetime import datetime, timedelta
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_history = (db.query(models.SkillLevelHistory)
                          .filter(
                              models.SkillLevelHistory.user_id.in_(member_id_list),
                              models.SkillLevelHistory.changed_at >= thirty_days_ago,
                          ).all()) if member_id_list else []

        growth_map: dict[int, dict] = {}  # user_id -> {ups, total_gain}
        for h in recent_history:
            prev = h.previous_level or 0
            gain = h.level - prev
            if gain > 0:
                if h.user_id not in growth_map:
                    growth_map[h.user_id] = {"ups": 0, "total_gain": 0}
                growth_map[h.user_id]["ups"] += 1
                growth_map[h.user_id]["total_gain"] += gain

        growth_ranking = []
        for m in target_members:
            g = growth_map.get(m.id, {"ups": 0, "total_gain": 0})
            growth_ranking.append({
                "user": m,
                "ups": g["ups"],
                "total_gain": g["total_gain"],
            })
        growth_ranking.sort(key=lambda x: (x["total_gain"], x["ups"]), reverse=True)

        # レーダー用: 上位メンバー (見やすさのため最大8名)
        top_members_for_radar = [ms["user"] for ms in member_summary[:8]]

        recommended_skills: list = []

        return templates.TemplateResponse(request, "dashboard.html", {
            "current_user": current_user,
            "view_mode": view_mode,
            "view_label": view_label,
            "target_user": None,
            "target_group": None,
            "all_users": all_users,
            "all_groups": all_groups,
            "sel_user_id": user_id,
            "sel_group_id": group_id,
            "total": total,
            "total_members": total_members,
            "catalog_total": catalog_total,
            "avg_level": avg_level,
            "max_sl": max_sl,
            "level_dist": level_dist,
            "top_cats": top_cats,
            "tier_stats": tier_stats,
            "recent": recent,
            "members": top_members_for_radar,
            "member_radar": member_radar,
            "member_summary": member_summary,
            "group_summary": group_summary,
            "growth_ranking": growth_ranking,
            "categories": categories,
            "coverage_data": coverage_data,
            "coverage_summary": coverage_summary,
            "cat_coverage": cat_coverage,
            "skill_trends": skill_trends,
            "bus_factor_skills": bus_factor_skills,
            "recommended_skills": recommended_skills,
            "user_badges": db.query(models.UserBadge).filter(
                models.UserBadge.user_id == current_user.id
            ).order_by(models.UserBadge.awarded_at.desc()).all(),
            "BADGE_DEFS": models.BADGE_DEFS,
        })

    elif view_mode == "group" and target_group:
        # グループモード: メンバー全員の集計
        member_ids = [m.user_id for m in target_group.memberships]
        all_levels = (db.query(models.UserSkillLevel)
                      .filter(
                          models.UserSkillLevel.user_id.in_(member_ids),
                          models.UserSkillLevel.approval_status == "approved",
                      )
                      .all()) if member_ids else []
        members = [m.user for m in target_group.memberships]

        # 基本集計
        total = sum(1 for sl in all_levels if sl.level > 0)
        avg_level = round(sum(sl.level for sl in all_levels) / len(all_levels), 1) if all_levels else 0.0
        max_sl = max(all_levels, key=lambda sl: sl.level, default=None)

        # レベル分布
        level_dist = {i: 0 for i in range(5)}
        for sl in all_levels:
            level_dist[sl.level] += 1

        # カテゴリー別件数
        cat_stats: dict[str, int] = {}
        for sl in all_levels:
            if sl.skill.category:
                n = sl.skill.category.name
                cat_stats[n] = cat_stats.get(n, 0) + 1
        top_cats = sorted(cat_stats.items(), key=lambda x: x[1], reverse=True)[:8]

        # ティア別達成状況 (グループ: 全メンバー合算 / メンバー数で平均)
        from collections import defaultdict
        tier_stats: dict[str, dict] = {}
        for tier_key in models.SKILL_TIERS:
            tier_catalog = db.query(models.Skill).filter(models.Skill.tier == tier_key).count()
            tier_done = sum(1 for sl in all_levels if sl.skill.tier == tier_key and sl.level > 0)
            # グループ平均化: コンテキストに合わせる
            tier_stats[tier_key] = {
                "total": tier_catalog * max(len(members), 1),
                "done": tier_done,
            }

        # メンバーごとのレーダーデータ
        member_radar = {}
        for m in members:
            m_levels = [sl for sl in all_levels if sl.user_id == m.id]
            cat_avg = {}
            for c in categories:
                c_levels = [sl.level for sl in m_levels if sl.skill.category and sl.skill.category.name == c.name]
                cat_avg[c.name] = round(sum(c_levels) / len(c_levels), 1) if c_levels else 0
            member_radar[m.id] = cat_avg

        # メンバー別サマリー
        member_summary = []
        for m in members:
            m_levels = [sl for sl in all_levels if sl.user_id == m.id]
            m_total = sum(1 for sl in m_levels if sl.level > 0)
            m_avg = round(sum(sl.level for sl in m_levels) / len(m_levels), 1) if m_levels else 0.0
            member_summary.append({
                "user": m,
                "total": m_total,
                "avg": m_avg,
            })
        member_summary.sort(key=lambda x: x["avg"], reverse=True)

        recent = sorted([sl for sl in all_levels if sl.level > 0],
                        key=lambda sl: sl.updated_at or sl.skill.created_at,
                        reverse=True)[:10]

        # ── スキルギャップ分析 ───────────────────────────────────
        from routers.groups import _get_all_group_skill_ids
        required_skill_ids = _get_all_group_skill_ids(target_group)
        required_skills = (db.query(models.Skill)
                           .filter(models.Skill.id.in_(required_skill_ids))
                           .order_by(models.Skill.name).all()) if required_skill_ids else []
        gap_data = []
        for sk in required_skills:
            member_levels = []
            for m in members:
                sl = next((s for s in all_levels if s.skill_id == sk.id and s.user_id == m.id), None)
                level = sl.level if sl else 0
                member_levels.append({"user": m, "level": level})
            holders = sum(1 for ml in member_levels if ml["level"] > 0)
            avg_lv = round(sum(ml["level"] for ml in member_levels) / len(member_levels), 1) if member_levels else 0.0
            coverage_pct = round(holders / len(members) * 100) if members else 0
            gap_data.append({
                "skill": sk,
                "members": member_levels,
                "avg": avg_lv,
                "holders": holders,
                "coverage_pct": coverage_pct,
            })
        gap_data.sort(key=lambda x: x["coverage_pct"])
        gap_total = len(required_skills)
        gap_full = sum(1 for g in gap_data if g["coverage_pct"] == 100)
        gap_partial = sum(1 for g in gap_data if 0 < g["coverage_pct"] < 100)
        gap_none = sum(1 for g in gap_data if g["coverage_pct"] == 0)
        gap_summary = {
            "total_required": gap_total,
            "fully_covered": gap_full,
            "partially_covered": gap_partial,
            "not_covered": gap_none,
        }

        recommended_skills: list = []

        # バッジ（管理者ビューでも自分のバッジを表示）
        user_badges_mgr = (
            db.query(models.UserBadge)
            .filter(models.UserBadge.user_id == current_user.id)
            .order_by(models.UserBadge.awarded_at.desc())
            .all()
        )

        return templates.TemplateResponse(request, "dashboard.html", {
            "current_user": current_user,
            "view_mode": view_mode,
            "view_label": view_label,
            "target_group": target_group,
            "all_users": all_users,
            "all_groups": all_groups,
            "sel_user_id": user_id,
            "user_badges": user_badges_mgr,
            "BADGE_DEFS": models.BADGE_DEFS,
            "sel_group_id": group_id,
            "total": total,
            "catalog_total": catalog_total,
            "avg_level": avg_level,
            "max_sl": max_sl,
            "level_dist": level_dist,
            "top_cats": top_cats,
            "tier_stats": tier_stats,
            "recent": recent,
            "members": members,
            "member_radar": member_radar,
            "member_summary": member_summary,
            "categories": categories,
            "gap_data": gap_data,
            "gap_summary": gap_summary,
            "skill_trends": skill_trends,
            "bus_factor_skills": bus_factor_skills,
            "recommended_skills": recommended_skills,
        })

    else:
        # 個人モード（self or 指定ユーザー）
        target = target_user if view_mode == "user" else current_user

        my_levels = (db.query(models.UserSkillLevel)
                     .filter(
                         models.UserSkillLevel.user_id == target.id,
                         models.UserSkillLevel.approval_status == "approved",
                     )
                     .order_by(models.UserSkillLevel.updated_at.desc())
                     .all())

        total = len(my_levels)
        avg_level = round(sum(sl.level for sl in my_levels) / total, 1) if total else 0.0
        max_sl = max(my_levels, key=lambda sl: sl.level, default=None)

        level_dist = {i: 0 for i in range(5)}
        for sl in my_levels:
            level_dist[sl.level] += 1

        cat_stats: dict[str, int] = {}
        for sl in my_levels:
            if sl.skill.category:
                n = sl.skill.category.name
                cat_stats[n] = cat_stats.get(n, 0) + 1
        top_cats = sorted(cat_stats.items(), key=lambda x: x[1], reverse=True)[:5]

        # User ロールはグループに割り当てられたスキルのみ集計対象
        from routers.groups import _get_all_group_skill_ids as _gids
        _dash_group_skill_ids: set | None = None
        if target.role == "user":
            _dash_groups = (db.query(models.Group)
                            .join(models.GroupMembership)
                            .filter(models.GroupMembership.user_id == target.id)
                            .all())
            _gset: set[int] = set()
            for _dg in _dash_groups:
                _gset.update(_gids(_dg))
            _dash_group_skill_ids = _gset
            # catalog_total を上書き（グループのスキル数）
            catalog_total = len(_gset)

        from collections import defaultdict
        tier_stats: dict[str, dict] = {}
        for tier_key in models.SKILL_TIERS:
            if _dash_group_skill_ids is not None:
                tier_catalog = db.query(models.Skill).filter(
                    models.Skill.tier == tier_key,
                    models.Skill.id.in_(_dash_group_skill_ids),
                ).count() if _dash_group_skill_ids else 0
            else:
                tier_catalog = db.query(models.Skill).filter(models.Skill.tier == tier_key).count()
            tier_done = sum(1 for sl in my_levels if sl.skill.tier == tier_key and sl.level > 0)
            tier_stats[tier_key] = {"total": tier_catalog, "done": tier_done}

        recent = my_levels[:5]

        # 個人向け次推奨スキル
        recommended_skills: list = []
        try:
            _rec_groups = (db.query(models.Group)
                           .join(models.GroupMembership)
                           .filter(models.GroupMembership.user_id == target.id)
                           .all())
            _req_ids: set = set()
            for _g in _rec_groups:
                for _gs in _g.skills:
                    _req_ids.add(_gs.id)
            _user_lv_map = {sl.skill_id: sl.level for sl in
                            db.query(models.UserSkillLevel)
                            .filter(models.UserSkillLevel.user_id == target.id).all()}
            _all_approved = (db.query(models.UserSkillLevel)
                             .filter(models.UserSkillLevel.approval_status == "approved").all())
            _team_lvl_map: dict = defaultdict(list)
            for _sl in _all_approved:
                _team_lvl_map[_sl.skill_id].append(_sl.level)
            _team_avg_map = {_sid: round(sum(_lvs) / len(_lvs), 2)
                             for _sid, _lvs in _team_lvl_map.items() if _lvs}
            _recs = []
            for _sk in db.query(models.Skill).all():
                try:
                    if _sk.is_archived:
                        continue
                except AttributeError:
                    pass
                _ulv = _user_lv_map.get(_sk.id, 0)
                _tavg = _team_avg_map.get(_sk.id, 0.0)
                _is_req = _sk.id in _req_ids
                if (_is_req and _ulv == 0) or (_tavg > _ulv):
                    _recs.append({
                        "skill_id": _sk.id,
                        "skill_name": _sk.name,
                        "user_level": _ulv,
                        "team_avg": _tavg,
                        "is_required": _is_req,
                    })
            _recs.sort(key=lambda x: (not x["is_required"], -(x["team_avg"] - x["user_level"])))
            recommended_skills = _recs[:5]
        except Exception:
            recommended_skills = []

        # バッジ取得（self モードは current_user、user モードは target）
        badge_target_id = current_user.id if view_mode == "self" else target.id
        user_badges = (db.query(models.UserBadge)
                       .filter(models.UserBadge.user_id == badge_target_id)
                       .order_by(models.UserBadge.awarded_at.desc())
                       .all())

        # 所属グループ情報（User ロール向け）
        my_groups_info = []
        if view_mode == "self" and current_user.role == "user":
            from routers.groups import _get_all_group_skill_ids
            for membership in (
                db.query(models.GroupMembership)
                .filter(models.GroupMembership.user_id == current_user.id)
                .all()
            ):
                grp = membership.group
                if grp:
                    skill_ids = _get_all_group_skill_ids(grp)
                    my_groups_info.append({
                        "name": grp.name,
                        "description": getattr(grp, 'description', None),
                        "manager": grp.manager,
                        "skill_count": len(skill_ids),
                    })

        return templates.TemplateResponse(request, "dashboard.html", {
            "current_user": current_user,
            "view_mode": view_mode,
            "view_label": view_label,
            "target_user": target if view_mode == "user" else None,
            "all_users": all_users,
            "all_groups": all_groups,
            "sel_user_id": user_id,
            "sel_group_id": group_id,
            "total": total,
            "catalog_total": catalog_total,
            "avg_level": avg_level,
            "max_sl": max_sl,
            "level_dist": level_dist,
            "top_cats": top_cats,
            "tier_stats": tier_stats,
            "recent": recent,
            "members": [],
            "member_radar": {},
            "member_summary": [],
            "categories": categories,
            "skill_trends": skill_trends,
            "bus_factor_skills": bus_factor_skills,
            "recommended_skills": recommended_skills,
            "user_badges": user_badges,
            "BADGE_DEFS": models.BADGE_DEFS,
            "my_groups_info": my_groups_info,
        })


# ─── CSVエクスポート ─────────────────────────────────────────────
@app.get("/export/skills-matrix")
def export_skills_matrix(
    request: Request,
    group_id: int = 0,
    db: Session = Depends(get_db),
):
    import csv
    import io
    from fastapi.responses import StreamingResponse
    from datetime import datetime

    auth.require_approved(request, db)

    # 全スキルを取得（アーカイブされていないもの）
    skills = (
        db.query(models.Skill)
        .filter(models.Skill.is_archived == False)
        .order_by(models.Skill.name)
        .all()
    )

    # ユーザー取得（承認済み、グループフィルタあれば絞り込み）
    if group_id:
        users = (
            db.query(models.User)
            .join(models.GroupMembership, models.GroupMembership.user_id == models.User.id)
            .filter(
                models.GroupMembership.group_id == group_id,
                models.User.is_approved == True,
            )
            .order_by(models.User.display_name)
            .all()
        )
    else:
        users = (
            db.query(models.User)
            .filter(models.User.is_approved == True)
            .order_by(models.User.display_name)
            .all()
        )

    # UserSkillLevel を user_id → skill_id → level のマップに変換
    user_ids = [u.id for u in users]
    skill_ids = [s.id for s in skills]
    rows = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id.in_(user_ids),
            models.UserSkillLevel.skill_id.in_(skill_ids),
        )
        .all()
    )
    level_map: dict[tuple[int, int], int] = {
        (r.user_id, r.skill_id): r.level for r in rows
    }

    # BOM付きUTF-8でExcel文字化け防止
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)

    # ヘッダー行
    writer.writerow(["ユーザー名"] + [s.name for s in skills])

    # データ行
    for u in users:
        display = u.display_name or u.username
        levels = [level_map.get((u.id, s.id), 0) for s in skills]
        writer.writerow([display] + levels)

    output.seek(0)
    filename = f"skills-matrix-{datetime.now().strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── プロフィール ────────────────────────────────────────────────
@app.get("/profile", response_class=HTMLResponse)
def profile_get(request: Request, avatar_ok: str = "", db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    my_groups = (
        db.query(models.Group)
        .join(models.GroupMembership)
        .filter(models.GroupMembership.user_id == user.id)
        .order_by(models.Group.name)
        .all()
    )
    return templates.TemplateResponse(request, "profile.html", {
        "current_user": user, "success": bool(avatar_ok), "error": None,
        "my_groups": my_groups,
    })


@app.post("/profile", response_class=HTMLResponse)
def profile_post(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    error = None

    if new_password:
        if not auth.verify_password(current_password, user.password_hash):
            error = "現在のパスワードが正しくありません"
        elif len(new_password) < 6:
            error = "新しいパスワードは6文字以上にしてください"
        else:
            user.password_hash = auth.hash_password(new_password)

    if not error:
        user.display_name = display_name or user.username
        user.email = email or None
        db.commit()

    return templates.TemplateResponse(request, "profile.html", {
        "current_user": user,
        "success": not error, "error": error
    })


# ─── お知らせポップアップ設定 ────────────────────────────────────
@app.post("/profile/announcement-popup")
def profile_toggle_ann_popup(
    request: Request,
    suppress: str = Form(default=""),   # "1" = 非表示, "" = 表示
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    user.suppress_ann_popup = (suppress == "1")
    db.commit()
    return RedirectResponse("/profile?popup_saved=1", status_code=303)


# ─── アバター ────────────────────────────────────────────────────
ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2MB


@app.get("/avatars/{filename}")
def serve_avatar(filename: str):
    """アップロード済みアバター画像を返す"""
    safe_name = os.path.basename(filename)
    path = os.path.join("data", "avatars", safe_name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.post("/profile/avatar")
async def upload_avatar(
    request: Request,
    avatar: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)

    # 拡張子チェック
    ext = os.path.splitext(avatar.filename or "")[1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        return templates.TemplateResponse(request, "profile.html", {
            "current_user": user, "success": False,
            "error": "対応形式: JPG, PNG, GIF, WebP"
        })

    # サイズチェック
    contents = await avatar.read()
    if len(contents) > MAX_AVATAR_SIZE:
        return templates.TemplateResponse(request, "profile.html", {
            "current_user": user, "success": False,
            "error": "ファイルサイズは2MB以下にしてください"
        })

    # 旧ファイル削除
    if user.avatar_path:
        old_path = os.path.join("data", "avatars", os.path.basename(user.avatar_path))
        if os.path.isfile(old_path):
            os.remove(old_path)

    # 保存
    new_filename = f"{user.id}_{uuid.uuid4().hex[:8]}{ext}"
    save_path = os.path.join("data", "avatars", new_filename)
    with open(save_path, "wb") as f:
        f.write(contents)

    user.avatar_path = f"/avatars/{new_filename}"
    db.commit()

    return RedirectResponse("/profile?avatar_ok=1", status_code=303)


@app.post("/profile/avatar/delete")
def delete_avatar(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    if user.avatar_path:
        old_path = os.path.join("data", "avatars", os.path.basename(user.avatar_path))
        if os.path.isfile(old_path):
            os.remove(old_path)
        user.avatar_path = None
        db.commit()
    return RedirectResponse("/profile", status_code=303)


# ─── カタログ初期化（Admin のみ・完全削除） ─────────────────────────
@app.post("/admin/reset-catalog")
def reset_catalog(request: Request, db: Session = Depends(get_db)):
    """カテゴリ・スキル・申告データを全削除する（デフォルトデータは投入しない）"""
    user = auth.require_approved(request, db)
    if user.role != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403)

    from sqlalchemy import text
    db.query(models.UserSkillLevel).delete(synchronize_session=False)
    db.query(models.SkillLevelHistory).delete(synchronize_session=False)
    db.execute(text("DELETE FROM skill_tag_associations"))
    db.query(models.Skill).delete(synchronize_session=False)
    db.query(models.Category).delete(synchronize_session=False)
    db.commit()

    return RedirectResponse("/skills/catalog?reset=1", status_code=303)


# ─── プロモーション動画 ───────────────────────────────────────────
@app.get("/promo-video")
def serve_promo_video():
    """プロモーション動画を配信（認証不要）"""
    if not os.path.isfile(PROMO_VIDEO_PATH):
        raise HTTPException(status_code=404, detail="プロモーション動画が見つかりません")
    return FileResponse(PROMO_VIDEO_PATH, media_type="video/mp4")


@app.post("/admin/upload-promo-video")
async def upload_promo_video(
    request: Request,
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """管理者がプロモーション動画をアップロード"""
    user = auth.require_approved(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403)

    # mp4 のみ許可
    ext = os.path.splitext(video.filename or "")[1].lower()
    if ext not in (".mp4", ".webm", ".mov"):
        return RedirectResponse("/admin/settings/data?promo_error=対応形式: .mp4 .webm .mov", status_code=303)

    contents = await video.read()
    if len(contents) > 500 * 1024 * 1024:  # 500MB 上限
        return RedirectResponse("/admin/settings/data?promo_error=ファイルサイズは500MB以下にしてください", status_code=303)

    save_path = os.path.join("data", f"promo{ext}")
    # 既存の旧ファイルを削除
    for old in ["data/promo.mp4", "data/promo.webm", "data/promo.mov"]:
        if os.path.isfile(old) and old != save_path:
            os.remove(old)

    with open(save_path, "wb") as f:
        f.write(contents)

    # パスを更新（常に promo.mp4 として扱う）
    if save_path != PROMO_VIDEO_PATH:
        if os.path.isfile(PROMO_VIDEO_PATH):
            os.remove(PROMO_VIDEO_PATH)
        os.rename(save_path, PROMO_VIDEO_PATH)

    return RedirectResponse("/admin/settings/data?promo_uploaded=1", status_code=303)


@app.post("/admin/delete-promo-video")
def delete_promo_video(request: Request, db: Session = Depends(get_db)):
    """プロモーション動画を削除"""
    user = auth.require_approved(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403)
    if os.path.isfile(PROMO_VIDEO_PATH):
        os.remove(PROMO_VIDEO_PATH)
    return RedirectResponse("/admin/settings/data", status_code=303)


# ─── デモページ（認証不要） ──────────────────────────────────────
@app.get("/demo", response_class=HTMLResponse)
def demo_page(request: Request):
    DEMO_USERS = [
        {"name": "田中 太郎",  "initial": "田", "avg": 3.1, "rate": 88},
        {"name": "鈴木 花子",  "initial": "鈴", "avg": 2.5, "rate": 75},
        {"name": "佐藤 次郎",  "initial": "佐", "avg": 1.6, "rate": 50},
        {"name": "山田 美咲",  "initial": "山", "avg": 3.5, "rate": 100},
        {"name": "高橋 健一",  "initial": "高", "avg": 1.1, "rate": 38},
    ]
    DEMO_SKILLS = [
        {"name": "Git 基礎",         "category": "バージョン管理", "tier": "basic",     "levels": [4, 3, 2, 4, 1], "avg": 2.8},
        {"name": "GitHub",           "category": "バージョン管理", "tier": "basic",     "levels": [4, 3, 1, 4, 1], "avg": 2.6},
        {"name": "Docker 基礎",      "category": "コンテナ技術",   "tier": "basic",     "levels": [3, 2, 1, 4, 0], "avg": 2.0},
        {"name": "Docker Compose",   "category": "コンテナ技術",   "tier": "basic",        "levels": [3, 3, 0, 4, 0], "avg": 2.0},
        {"name": "Kubernetes",       "category": "コンテナ技術",   "tier": "intermediate", "levels": [2, 1, 0, 3, 0], "avg": 1.2},
        {"name": "Python",           "category": "プログラミング", "tier": "basic",     "levels": [4, 3, 2, 3, 2], "avg": 2.8},
        {"name": "TypeScript",       "category": "プログラミング", "tier": "basic",        "levels": [3, 2, 1, 3, 1], "avg": 2.0},
        {"name": "GitHub Actions",   "category": "CI/CD",          "tier": "basic",        "levels": [3, 4, 1, 4, 1], "avg": 2.6},
        {"name": "AWS 基礎",         "category": "クラウド",       "tier": "basic",        "levels": [2, 3, 1, 4, 0], "avg": 2.0},
        {"name": "SQL 基礎",         "category": "データベース",   "tier": "basic",     "levels": [3, 2, 2, 3, 1], "avg": 2.2},
    ]

    # レベル分布を計算
    all_levels = [lv for sk in DEMO_SKILLS for lv in sk["levels"]]
    level_dist = [all_levels.count(i) for i in range(5)]

    expert_count = sum(1 for lv in all_levels if lv == 4)
    avg_total = round(sum(lv for lv in all_levels if lv > 0) / max(sum(1 for lv in all_levels if lv > 0), 1), 1)

    demo_data = {
        "member_count": len(DEMO_USERS),
        "skill_count": len(DEMO_SKILLS),
        "avg_level": avg_total,
        "expert_count": expert_count,
        "users": DEMO_USERS,
        "skills": DEMO_SKILLS,
        "level_dist": level_dist,
    }
    return templates.TemplateResponse(request, "demo.html", {"demo": demo_data, "request": request})


# ─── グローバル検索 API ──────────────────────────────────────────
from fastapi.responses import JSONResponse as _JSONResponse

@app.get("/api/search")
def api_search(q: str = "", request: Request = None, db: Session = Depends(get_db)):
    """スキル名・ユーザー名・グループ名を横断検索"""
    user = auth.require_approved(request, db)
    q = q.strip()
    if not q or len(q) < 1:
        return _JSONResponse({"results": []})

    results = []
    q_lower = q.lower()

    skills = db.query(models.Skill).filter(
        models.Skill.name.ilike(f"%{q}%"),
        models.Skill.is_archived == False,
    ).limit(8).all()
    for s in skills:
        results.append({"type": "skill", "label": s.name, "url": "/skills/catalog", "icon": "bi-lightning-charge"})

    users = db.query(models.User).filter(
        models.User.is_approved == True,
        (models.User.display_name.ilike(f"%{q}%") | models.User.username.ilike(f"%{q}%")),
    ).limit(5).all()
    for u in users:
        results.append({
            "type": "user",
            "label": u.display_name or u.username,
            "url": f"/members/{u.id}/skills",
            "icon": "bi-person-fill",
        })

    if user.role in ("admin", "manager"):
        groups = db.query(models.Group).filter(
            models.Group.name.ilike(f"%{q}%")
        ).limit(5).all()
        for g in groups:
            results.append({"type": "group", "label": g.name, "url": f"/groups/{g.id}", "icon": "bi-people-fill"})

    return _JSONResponse({"results": results[:15]})


# ─── ダッシュボード トレンド API ──────────────────────────────────
from datetime import datetime, timedelta

@app.get("/api/dashboard/trend")
def api_dashboard_trend(
    group_id: int = 0,
    user_id: int = 0,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """週次スキル成長トレンドデータ（過去12週）"""
    user = auth.require_approved(request, db)

    today = datetime.utcnow().date()
    weeks = 12
    start_date = today - timedelta(weeks=weeks)

    q = db.query(models.SkillLevelHistory)

    if user_id and user_id != user.id and user.role in ("admin", "manager"):
        q = q.filter(models.SkillLevelHistory.user_id == user_id)
    elif group_id and user.role in ("admin", "manager"):
        grp = db.query(models.Group).filter(models.Group.id == group_id).first()
        if grp:
            member_ids = [m.user_id for m in grp.memberships]
            q = q.filter(models.SkillLevelHistory.user_id.in_(member_ids))
    else:
        q = q.filter(models.SkillLevelHistory.user_id == user.id)

    histories = q.filter(
        models.SkillLevelHistory.changed_at >= start_date
    ).all()

    labels = []
    counts = []
    for i in range(weeks - 1, -1, -1):
        week_start = today - timedelta(weeks=i + 1)
        week_end = today - timedelta(weeks=i)
        label = week_start.strftime("%-m/%-d")
        count = sum(
            1 for h in histories
            if week_start <= h.changed_at.date() < week_end and h.level > h.previous_level
        )
        labels.append(label)
        counts.append(count)

    return _JSONResponse({"labels": labels, "counts": counts})
