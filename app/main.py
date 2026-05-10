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

app = FastAPI(title="スキルマップ")

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
        _migrate_group_managers_table(db)
        _migrate_tickets_tables()
        _migrate_education_table()
        _migrate_announcements_table()
        _seed_catalog(db)
        _sync_tier_names(db)
    finally:
        db.close()


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


# 車両開発・HILS向けスキルカタログのシードデータ
_SEED_CATEGORIES = [
    {"name": "HILS基盤・操作",          "color": "#dc2626"},
    {"name": "dSPACEツール",            "color": "#2563eb"},
    {"name": "ソフト検証・テスト",      "color": "#7c3aed"},
    {"name": "モデル開発（Simulink）",  "color": "#f97316"},
    {"name": "DevOps・自動化",          "color": "#059669"},
    {"name": "ハードウェア・W/H設計",   "color": "#d97706"},
    {"name": "プログラミング",          "color": "#0891b2"},
    {"name": "テスト管理・品質",        "color": "#be185d"},
    {"name": "車載・ECU・通信",         "color": "#374151"},
    {"name": "プロジェクト管理",        "color": "#0f766e"},
]

_SEED_SKILLS = [
    # ── HILS基盤・操作 ──────────────────────────────────────
    {"name": "HILS基本操作",            "cat": "HILS基盤・操作", "tier": "beginner",     "desc": "HILSの電源投入・基本操作・ステータス確認"},
    {"name": "HILS構成理解",            "cat": "HILS基盤・操作", "tier": "beginner",     "desc": "HILSシステム全体構成（HW/SW/モデル）の理解"},
    {"name": "HILSキャリブレーション",  "cat": "HILS基盤・操作", "tier": "basic",        "desc": "センサ・アクチュエータの校正・調整手順"},
    {"name": "HILS障害切り分け",        "cat": "HILS基盤・操作", "tier": "intermediate", "desc": "HW/SW/モデル起因の問題を切り分け・原因特定"},
    {"name": "HILSシステム設計",        "cat": "HILS基盤・操作", "tier": "intermediate", "desc": "要件からHILS全体構成の設計・機器選定"},
    {"name": "HILS環境整備・保守",      "cat": "HILS基盤・操作", "tier": "advanced",     "desc": "HILSラック整備・定期メンテナンス・信頼性確保"},

    # ── dSPACEツール ────────────────────────────────────────
    {"name": "ControlDesk 基本操作",    "cat": "dSPACEツール", "tier": "beginner",     "desc": "レイアウト作成・変数モニタリング・データ記録"},
    {"name": "SCALEXIO / DS1007 操作",  "cat": "dSPACEツール", "tier": "beginner",     "desc": "dSPACEリアルタイムボードの基本操作・起動"},
    {"name": "ConfigurationDesk",       "cat": "dSPACEツール", "tier": "basic",        "desc": "I/O・通信設定、ハードウェアコンフィグレーション"},
    {"name": "AutomationDesk 基礎",     "cat": "dSPACEツール", "tier": "basic",        "desc": "テスト自動化シーケンス作成・実行・結果確認"},
    {"name": "ModelDesk",               "cat": "dSPACEツール", "tier": "intermediate", "desc": "車両モデル・ドライバモデルの設定・シナリオ実行"},
    {"name": "AutomationDesk 応用",     "cat": "dSPACEツール", "tier": "intermediate", "desc": "Python / MATLAB連携・高度な自動化シーケンス構築"},
    {"name": "SystemDesk / AUTOSAR設定","cat": "dSPACEツール", "tier": "intermediate", "desc": "AUTOSARアーキテクチャ記述・SWC設定・生成"},
    {"name": "dSPACE VEOS（仮想ECU）",  "cat": "dSPACEツール", "tier": "advanced",     "desc": "実機レスSIL環境構築・仮想ECUでのテスト実施"},

    # ── ソフト検証・テスト ───────────────────────────────────
    {"name": "テスト仕様書読解",        "cat": "ソフト検証・テスト", "tier": "beginner",     "desc": "テスト仕様書の内容理解・テスト項目把握"},
    {"name": "テスト実行・記録",        "cat": "ソフト検証・テスト", "tier": "beginner",     "desc": "手順に従ったテスト実行・結果記録・エビデンス取得"},
    {"name": "テスト分析・設計",        "cat": "ソフト検証・テスト", "tier": "basic",        "desc": "要求仕様からテスト観点を抽出・テスト設計技法の活用"},
    {"name": "テストケース作成",        "cat": "ソフト検証・テスト", "tier": "basic",        "desc": "同値分割・境界値・状態遷移・デシジョンテーブル活用"},
    {"name": "テスト結果解析",          "cat": "ソフト検証・テスト", "tier": "basic",        "desc": "波形・ログ解析・NG原因の特定・レポート作成"},
    {"name": "網羅率分析・管理",        "cat": "ソフト検証・テスト", "tier": "intermediate", "desc": "C0/C1カバレッジ分析・未検証部位の特定と対策"},
    {"name": "回帰テスト設計",          "cat": "ソフト検証・テスト", "tier": "intermediate", "desc": "変更影響範囲の特定・効率的な回帰テスト設計"},
    {"name": "CANoe 活用",              "cat": "ソフト検証・テスト", "tier": "intermediate", "desc": "CANoeによる通信ログ取得・シミュレーション・診断"},
    {"name": "テスト自動化設計",        "cat": "ソフト検証・テスト", "tier": "advanced",     "desc": "テストフレームワーク設計・自動化戦略の立案・実装"},

    # ── モデル開発（Simulink） ───────────────────────────────
    {"name": "MATLAB 基礎",             "cat": "モデル開発（Simulink）", "tier": "beginner",     "desc": "MATLAB Script・数値計算・基本データ操作・グラフ描画"},
    {"name": "Simulink 基礎",           "cat": "モデル開発（Simulink）", "tier": "beginner",     "desc": "基本ブロック操作・信号接続・シミュレーション実行"},
    {"name": "Stateflow",               "cat": "モデル開発（Simulink）", "tier": "basic",        "desc": "状態遷移・フローチャート設計・イベント処理"},
    {"name": "プラントモデル開発",      "cat": "モデル開発（Simulink）", "tier": "basic",        "desc": "車両・アクチュエータ・センサの物理モデル開発"},
    {"name": "モデル結合・I/F設計",     "cat": "モデル開発（Simulink）", "tier": "intermediate", "desc": "複数モデルの統合・信号インタフェース設計・検証"},
    {"name": "S-Function 開発",         "cat": "モデル開発（Simulink）", "tier": "intermediate", "desc": "カスタムブロック（C / MATLAB S-Function）開発"},
    {"name": "Simulink コード生成",     "cat": "モデル開発（Simulink）", "tier": "advanced",     "desc": "Embedded Coder / RTW活用・量産品質コード生成"},
    {"name": "Simulink Design Verifier","cat": "モデル開発（Simulink）", "tier": "advanced",     "desc": "モデルの形式検証・テストケース自動生成"},

    # ── DevOps・自動化 ───────────────────────────────────────
    {"name": "Git 基礎",                "cat": "DevOps・自動化", "tier": "beginner",     "desc": "バージョン管理・ブランチ・コミット・マージ"},
    {"name": "GitHub / GitLab 操作",    "cat": "DevOps・自動化", "tier": "beginner",     "desc": "PR・Issue・コードレビュー・リモートリポジトリ管理"},
    {"name": "GitHub Actions",          "cat": "DevOps・自動化", "tier": "basic",        "desc": "CI/CDパイプライン構築・自動テスト・通知連携"},
    {"name": "Docker 基礎",             "cat": "DevOps・自動化", "tier": "basic",        "desc": "Dockerfile・イメージ・コンテナ操作・Docker Compose"},
    {"name": "HILSテスト自動化",        "cat": "DevOps・自動化", "tier": "intermediate", "desc": "AutomationDesk / Python連携によるHILSテスト自動化"},
    {"name": "CI/CDパイプライン構築",   "cat": "DevOps・自動化", "tier": "intermediate", "desc": "テスト・ビルド・結果通知の自動化パイプライン設計"},
    {"name": "社内ツール・アプリ開発",  "cat": "DevOps・自動化", "tier": "intermediate", "desc": "業務効率化Web/GUIアプリの企画・設計・開発・運用"},
    {"name": "インフラ管理・IaC",       "cat": "DevOps・自動化", "tier": "advanced",     "desc": "サーバ・ネットワーク管理・Infrastructure as Code"},

    # ── ハードウェア・W/H設計 ────────────────────────────────
    {"name": "回路図・配線図読解",      "cat": "ハードウェア・W/H設計", "tier": "beginner",     "desc": "電気回路図・車両配線図の読み方・記号理解"},
    {"name": "W/H基礎知識",             "cat": "ハードウェア・W/H設計", "tier": "beginner",     "desc": "ワイヤハーネスの基本構成・端子・コネクタ種類"},
    {"name": "ECU接続・結線",           "cat": "ハードウェア・W/H設計", "tier": "basic",        "desc": "ECUコネクタへの配線接続・導通確認・ピンアサイン"},
    {"name": "W/H設計",                 "cat": "ハードウェア・W/H設計", "tier": "basic",        "desc": "HILS用ハーネス設計・仕様書作成・製作指示"},
    {"name": "HILSラック組立・配線",    "cat": "ハードウェア・W/H設計", "tier": "intermediate", "desc": "dSPACEボード・ECU・電源のラック組み立て・配線"},
    {"name": "電源設計",                "cat": "ハードウェア・W/H設計", "tier": "intermediate", "desc": "電源要件定義・回路設計・保護回路・ノイズ対策"},
    {"name": "HILS筐体・構成設計",      "cat": "ハードウェア・W/H設計", "tier": "advanced",     "desc": "HILSシステム全体のHW構成設計・機器選定・仕様策定"},

    # ── プログラミング ───────────────────────────────────────
    {"name": "Python 基礎",             "cat": "プログラミング", "tier": "beginner",     "desc": "変数・制御文・関数・ファイル操作・基本ライブラリ"},
    {"name": "C言語 基礎",              "cat": "プログラミング", "tier": "beginner",     "desc": "変数・ポインタ・構造体・組込み向けC言語基礎"},
    {"name": "Python 応用",             "cat": "プログラミング", "tier": "basic",        "desc": "クラス・外部ライブラリ（pandas/numpy等）・データ処理"},
    {"name": "C++ 基礎",                "cat": "プログラミング", "tier": "basic",        "desc": "クラス・継承・STL・組込みC++"},
    {"name": "GUI開発（Python）",       "cat": "プログラミング", "tier": "intermediate", "desc": "PyQt / Tkinter・デスクトップアプリ設計・開発"},
    {"name": "データ解析・可視化",      "cat": "プログラミング", "tier": "intermediate", "desc": "pandas/matplotlib・測定データ集計・グラフ可視化"},
    {"name": "Webアプリ開発",           "cat": "プログラミング", "tier": "intermediate", "desc": "FastAPI / Flask・REST API設計・フロントエンド連携"},

    # ── テスト管理・品質 ─────────────────────────────────────
    {"name": "テスト計画策定",          "cat": "テスト管理・品質", "tier": "basic",        "desc": "テスト方針・スコープ・スケジュール・リソース計画"},
    {"name": "不具合管理",              "cat": "テスト管理・品質", "tier": "basic",        "desc": "バグトラッキング・重要度分類・修正確認・クローズ管理"},
    {"name": "テストレポート作成",      "cat": "テスト管理・品質", "tier": "basic",        "desc": "テスト結果集計・品質評価・リリース判定レポート作成"},
    {"name": "品質指標管理",            "cat": "テスト管理・品質", "tier": "intermediate", "desc": "KPI設定・品質メトリクス収集・傾向分析・改善活動"},
    {"name": "QMSプロセス理解",         "cat": "テスト管理・品質", "tier": "intermediate", "desc": "品質マネジメントシステム・プロセス準拠・監査対応"},
    {"name": "ASPICE",                  "cat": "テスト管理・品質", "tier": "advanced",     "desc": "Automotive SPICEプロセスアセスメント・改善施策立案"},

    # ── 車載・ECU・通信 ──────────────────────────────────────
    {"name": "車両基礎知識",            "cat": "車載・ECU・通信", "tier": "beginner",     "desc": "車両系統（パワトレ・シャシ・ボデー）の基本的な理解"},
    {"name": "ECU基礎知識",             "cat": "車載・ECU・通信", "tier": "beginner",     "desc": "ECUの役割・入出力・ソフトウェア構成の基本"},
    {"name": "CAN通信",                 "cat": "車載・ECU・通信", "tier": "basic",        "desc": "CANプロトコル・DBC・メッセージ・信号定義の理解"},
    {"name": "LIN通信",                 "cat": "車載・ECU・通信", "tier": "basic",        "desc": "LINプロトコル・マスタ/スレーブ・スケジュール表"},
    {"name": "車載Ethernet",            "cat": "車載・ECU・通信", "tier": "intermediate", "desc": "100BASE-T1・DoIP・AVB/TSN・車載ネットワーク設計"},
    {"name": "AUTOSAR知識",             "cat": "車載・ECU・通信", "tier": "intermediate", "desc": "AUTOSARアーキテクチャ・SWC・RTE・BSWの理解"},
    {"name": "機能安全（ISO 26262）",   "cat": "車載・ECU・通信", "tier": "advanced",     "desc": "ASIL・安全要求・ハザード分析・V&V・FSM設計"},

    # ── プロジェクト管理 ─────────────────────────────────────
    {"name": "タスク管理・進捗報告",    "cat": "プロジェクト管理", "tier": "beginner",     "desc": "課題管理・優先度付け・定例での進捗報告"},
    {"name": "技術ドキュメント作成",    "cat": "プロジェクト管理", "tier": "beginner",     "desc": "議事録・設計書・手順書・技術メモの作成"},
    {"name": "スケジュール管理",        "cat": "プロジェクト管理", "tier": "basic",        "desc": "WBS作成・マイルストーン設定・リスク管理"},
    {"name": "課題管理ツール活用",      "cat": "プロジェクト管理", "tier": "basic",        "desc": "Jira / GitHub Issues等のチケット運用・ワークフロー設計"},
    {"name": "技術レビュー実施",        "cat": "プロジェクト管理", "tier": "basic",        "desc": "設計・コード・テスト仕様のレビュー実施・建設的指摘"},
    {"name": "チームリード",            "cat": "プロジェクト管理", "tier": "intermediate", "desc": "タスク割当・メンバー育成・技術的意思決定・調整"},
    {"name": "プロセス改善",            "cat": "プロジェクト管理", "tier": "advanced",     "desc": "課題分析・改善提案・KPIによる効果測定・横展開"},
]


def _seed_catalog(db):
    """カテゴリーとスキルカタログが空の場合のみシードデータを投入する"""
    if db.query(models.Skill).count() > 0:
        return
    _do_seed(db)


def _do_seed(db):
    """シードデータを投入する（強制実行用）"""
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
        return templates.TemplateResponse(request, "admin/dashboard.html", {
            "current_user": user,
            "promo_error": "対応形式: .mp4 .webm .mov",
        })

    contents = await video.read()
    if len(contents) > 500 * 1024 * 1024:  # 500MB 上限
        return templates.TemplateResponse(request, "admin/dashboard.html", {
            "current_user": user,
            "promo_error": "ファイルサイズは500MB以下にしてください",
        })

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

    return RedirectResponse("/admin?promo_uploaded=1", status_code=303)


@app.post("/admin/delete-promo-video")
def delete_promo_video(request: Request, db: Session = Depends(get_db)):
    """プロモーション動画を削除"""
    user = auth.require_approved(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403)
    if os.path.isfile(PROMO_VIDEO_PATH):
        os.remove(PROMO_VIDEO_PATH)
    return RedirectResponse("/admin", status_code=303)


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
        {"name": "Git 基礎",         "category": "バージョン管理", "tier": "beginner",     "levels": [4, 3, 2, 4, 1], "avg": 2.8},
        {"name": "GitHub",           "category": "バージョン管理", "tier": "beginner",     "levels": [4, 3, 1, 4, 1], "avg": 2.6},
        {"name": "Docker 基礎",      "category": "コンテナ技術",   "tier": "beginner",     "levels": [3, 2, 1, 4, 0], "avg": 2.0},
        {"name": "Docker Compose",   "category": "コンテナ技術",   "tier": "basic",        "levels": [3, 3, 0, 4, 0], "avg": 2.0},
        {"name": "Kubernetes",       "category": "コンテナ技術",   "tier": "intermediate", "levels": [2, 1, 0, 3, 0], "avg": 1.2},
        {"name": "Python",           "category": "プログラミング", "tier": "beginner",     "levels": [4, 3, 2, 3, 2], "avg": 2.8},
        {"name": "TypeScript",       "category": "プログラミング", "tier": "basic",        "levels": [3, 2, 1, 3, 1], "avg": 2.0},
        {"name": "GitHub Actions",   "category": "CI/CD",          "tier": "basic",        "levels": [3, 4, 1, 4, 1], "avg": 2.6},
        {"name": "AWS 基礎",         "category": "クラウド",       "tier": "basic",        "levels": [2, 3, 1, 4, 0], "avg": 2.0},
        {"name": "SQL 基礎",         "category": "データベース",   "tier": "beginner",     "levels": [3, 2, 2, 3, 1], "avg": 2.2},
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
