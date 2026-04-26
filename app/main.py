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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs("data", exist_ok=True)
os.makedirs("data/avatars", exist_ok=True)
Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="スキルマップ")

app.mount("/static", StaticFiles(directory="static"), name="static")


app.include_router(skills_router.router)
app.include_router(admin_router.router)
app.include_router(groups_router.router)


# ─── 承認バッジ用ミドルウェア ─────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware

class PendingApprovalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.pending_approval_count = 0
        request.state.pending_user_count = 0
        db = database.SessionLocal()
        try:
            user = auth.get_current_user(request, db)
            if user and user.role in ("admin", "manager") and user.is_approved:
                count = (
                    db.query(models.UserSkillLevel)
                    .filter(
                        models.UserSkillLevel.approver_id == user.id,
                        models.UserSkillLevel.approval_status == "pending",
                    )
                    .count()
                )
                request.state.pending_approval_count = count
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
    save_config({"setup_complete": True})
    return RedirectResponse("/login?msg=setup_done", status_code=303)


@app.on_event("startup")
def _startup():
    db = next(get_db())
    try:
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
        _seed_catalog(db)
    finally:
        db.close()


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


# ITエンジニア向けスキルカタログのシードデータ
_SEED_CATEGORIES = [
    {"name": "バージョン管理",      "color": "#f97316"},
    {"name": "コンテナ技術",        "color": "#0ea5e9"},
    {"name": "CI/CD",               "color": "#10b981"},
    {"name": "プログラミング言語",   "color": "#8b5cf6"},
    {"name": "クラウド",            "color": "#f59e0b"},
    {"name": "データベース",        "color": "#ec4899"},
    {"name": "インフラ・OS",        "color": "#6366f1"},
    {"name": "セキュリティ",        "color": "#ef4444"},
    {"name": "フロントエンド",      "color": "#14b8a6"},
    {"name": "プロジェクト管理",    "color": "#84cc16"},
]

_SEED_SKILLS = [
    # バージョン管理
    {"name": "Git 基礎",         "cat": "バージョン管理",    "tier": "beginner",     "desc": "add/commit/push/pull・ステージング"},
    {"name": "GitHub",           "cat": "バージョン管理",    "tier": "beginner",     "desc": "PR・Issue・リモートリポジトリ管理"},
    {"name": "Git ブランチ戦略", "cat": "バージョン管理",    "tier": "basic",        "desc": "GitFlow・GitHub Flow・コンフリクト解消"},
    {"name": "GitLab",           "cat": "バージョン管理",    "tier": "basic",        "desc": "GitLab CI/CDパイプライン・マージリクエスト"},
    # コンテナ技術
    {"name": "Docker 基礎",      "cat": "コンテナ技術",      "tier": "beginner",     "desc": "Dockerfile・イメージビルド・コンテナ実行"},
    {"name": "Docker Compose",   "cat": "コンテナ技術",      "tier": "basic",        "desc": "複数コンテナ定義・ネットワーク・ボリューム"},
    {"name": "Kubernetes 基礎",  "cat": "コンテナ技術",      "tier": "intermediate", "desc": "Pod/Deployment/Service/ConfigMap"},
    {"name": "Kubernetes 運用",  "cat": "コンテナ技術",      "tier": "advanced",     "desc": "Helm・RBAC・HPA・ネットワークポリシー"},
    # CI/CD
    {"name": "GitHub Actions",   "cat": "CI/CD",             "tier": "basic",        "desc": "ワークフロー定義・自動テスト/デプロイ"},
    {"name": "Jenkins",          "cat": "CI/CD",             "tier": "intermediate", "desc": "パイプライン構築・プラグイン管理"},
    {"name": "CircleCI",         "cat": "CI/CD",             "tier": "intermediate", "desc": "Orbs活用・パイプライン最適化"},
    {"name": "ArgoCD",           "cat": "CI/CD",             "tier": "advanced",     "desc": "GitOpsによる継続的デプロイ"},
    # プログラミング言語
    {"name": "Python",           "cat": "プログラミング言語", "tier": "beginner",     "desc": "データ型・関数・クラス・ライブラリ活用"},
    {"name": "JavaScript",       "cat": "プログラミング言語", "tier": "beginner",     "desc": "DOM操作・非同期処理・ES6+構文"},
    {"name": "TypeScript",       "cat": "プログラミング言語", "tier": "basic",        "desc": "型定義・インターフェース・ジェネリクス"},
    {"name": "Go",               "cat": "プログラミング言語", "tier": "intermediate", "desc": "goroutine・並行処理・APIサーバー構築"},
    {"name": "Java",             "cat": "プログラミング言語", "tier": "intermediate", "desc": "OOP・Spring Boot・Maven/Gradle"},
    {"name": "Rust",             "cat": "プログラミング言語", "tier": "advanced",     "desc": "所有権・メモリ安全・高性能システム"},
    # クラウド
    {"name": "AWS 基礎 (EC2/S3/VPC)",       "cat": "クラウド", "tier": "basic",        "desc": "主要サービスの基本操作・IAM設定"},
    {"name": "AWS 応用 (EKS/Lambda/RDS)",    "cat": "クラウド", "tier": "intermediate", "desc": "サーバーレス・マネージドDB・コンテナ"},
    {"name": "GCP / Firebase",               "cat": "クラウド", "tier": "intermediate", "desc": "GCEから BigQuery・Firebase活用"},
    {"name": "Azure",                        "cat": "クラウド", "tier": "intermediate", "desc": "AKS・Azure DevOps・Active Directory"},
    {"name": "IaC (Terraform)",              "cat": "クラウド", "tier": "advanced",     "desc": "宣言的インフラ管理・モジュール設計"},
    # データベース
    {"name": "SQL 基礎",                     "cat": "データベース", "tier": "beginner",     "desc": "SELECT/INSERT/UPDATE/DELETE・JOIN"},
    {"name": "MySQL / PostgreSQL",           "cat": "データベース", "tier": "basic",        "desc": "インデックス・トランザクション・クエリ最適化"},
    {"name": "Redis",                        "cat": "データベース", "tier": "intermediate", "desc": "キャッシュ・セッション管理・Pub/Sub"},
    {"name": "MongoDB",                      "cat": "データベース", "tier": "intermediate", "desc": "ドキュメント指向・集計パイプライン"},
    # インフラ・OS
    {"name": "Linux 基礎",                   "cat": "インフラ・OS", "tier": "beginner",     "desc": "ファイル操作・パーミッション・プロセス管理"},
    {"name": "Linux シェルスクリプト",        "cat": "インフラ・OS", "tier": "basic",        "desc": "自動化スクリプト・cron・ログ管理"},
    {"name": "Nginx / Apache",               "cat": "インフラ・OS", "tier": "basic",        "desc": "Webサーバー設定・リバースプロキシ・SSL"},
    {"name": "Ansible",                      "cat": "インフラ・OS", "tier": "intermediate", "desc": "構成管理・プレイブック・Roleの設計"},
    # セキュリティ
    {"name": "セキュリティ基礎 (OWASP)",     "cat": "セキュリティ", "tier": "beginner",     "desc": "OWASP Top10・典型的脆弱性の概要"},
    {"name": "認証・認可設計",               "cat": "セキュリティ", "tier": "intermediate", "desc": "OAuth2/OIDC・JWT・RBAC設計"},
    {"name": "脆弱性診断",                   "cat": "セキュリティ", "tier": "advanced",     "desc": "ペネトレテスト・診断ツール・修正提案"},
    # フロントエンド
    {"name": "HTML / CSS 基礎",              "cat": "フロントエンド", "tier": "beginner",     "desc": "マークアップ・Flexbox/Grid・レスポンシブ"},
    {"name": "React / Vue.js",               "cat": "フロントエンド", "tier": "basic",        "desc": "コンポーネント設計・状態管理・Hooks"},
    {"name": "Next.js / Nuxt.js",            "cat": "フロントエンド", "tier": "intermediate", "desc": "SSR/SSG・APIルート・パフォーマンス最適化"},
    # プロジェクト管理
    {"name": "アジャイル / スクラム",        "cat": "プロジェクト管理", "tier": "basic",        "desc": "スプリント・バックログ・レトロスペクティブ"},
    {"name": "Jira / Confluence",            "cat": "プロジェクト管理", "tier": "basic",        "desc": "チケット管理・ドキュメント管理"},
    {"name": "コードレビュー",               "cat": "プロジェクト管理", "tier": "basic",        "desc": "レビュー観点・建設的フィードバック"},
    {"name": "技術設計・アーキテクチャ",     "cat": "プロジェクト管理", "tier": "advanced",     "desc": "システム設計・ADR・技術選定の意思決定"},
]


def _seed_catalog(db):
    """カテゴリーとスキルカタログが空の場合のみシードデータを投入する"""
    if db.query(models.Skill).count() > 0:
        return

    cat_map: dict[str, int] = {}
    for c in _SEED_CATEGORIES:
        existing = db.query(models.Category).filter(models.Category.name == c["name"]).first()
        if not existing:
            obj = models.Category(name=c["name"], color=c["color"])
            db.add(obj)
            db.flush()
            cat_map[c["name"]] = obj.id
        else:
            cat_map[c["name"]] = existing.id

    for s in _SEED_SKILLS:
        db.add(models.Skill(
            name=s["name"],
            description=s["desc"],
            category_id=cat_map.get(s["cat"]),
            tier=s["tier"],
        ))
    db.commit()
    logger.info("スキルカタログのシードデータを投入しました")


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


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, msg: str = ""):
    if not is_setup_complete():
        return RedirectResponse("/setup")
    return templates.TemplateResponse(request, "login.html", {
        "error": None,
        "setup_done": msg == "setup_done",
        "password_reset": msg == "password_reset",
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
    response = RedirectResponse("/dashboard", status_code=303)
    auth.create_session_cookie(response, user.id)
    return response


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
    email: str = Form(""),
    display_name: str = Form(""),
    db: Session = Depends(get_db),
):
    if len(password) < 6:
        return templates.TemplateResponse(request, "register.html", {
            "error": "パスワードは6文字以上にしてください"
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

        return templates.TemplateResponse(request, "dashboard.html", {
            "current_user": current_user,
            "view_mode": view_mode,
            "view_label": view_label,
            "target_group": target_group,
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

        from collections import defaultdict
        tier_stats: dict[str, dict] = {}
        for tier_key in models.SKILL_TIERS:
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
