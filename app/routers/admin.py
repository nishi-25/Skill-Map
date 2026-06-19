import os
from fastapi import APIRouter, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

import models
import auth
import mail
from config import get_config, save_config
from database import get_db
from template_engine import templates

PROMO_VIDEO_PATH = os.path.join("data", "promo.mp4")

router = APIRouter(prefix="/admin")


@router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    """管理パネルのトップは廃止し、ユーザー管理に統合"""
    auth.require_admin(request, db)
    return RedirectResponse("/admin/users", status_code=301)


@router.get("/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    user = auth.require_admin(request, db)
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/users.html", {
        "current_user": user, "users": users,
        "user_count": len(users),
        "pending_count": sum(1 for u in users if not u.is_approved),
    })


@router.post("/users/{uid}/approve")
def approve_user(uid: int, request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if target:
        target.is_approved = True
        db.commit()

        # 承認通知メールを送信
        if mail.is_mail_configured() and target.email:
            mail.send_approval_notice(
                user_email=target.email,
                display_name=target.display_name or target.username,
            )
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{uid}/reject")
def reject_user(uid: int, request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if target and target.role != "admin":
        target.is_approved = False
        db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{uid}/change-role")
def change_role(
    uid: int,
    request: Request,
    role: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if target and target.id != current_user.id and role in ("manager", "user"):
        target.role = role
        db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{uid}/delete")
def delete_user(uid: int, request: Request, db: Session = Depends(get_db)):
    current_user = auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if not target or target.id == current_user.id:
        return RedirectResponse("/admin/users", status_code=302)

    # created_by が NOT NULL なテーブルは削除前に処理
    # Ticket（TicketMessage も連鎖削除）
    tickets = db.query(models.Ticket).filter(models.Ticket.created_by == uid).all()
    for t in tickets:
        db.query(models.TicketMessage).filter(models.TicketMessage.ticket_id == t.id).delete()
        db.delete(t)
    # Announcement
    db.query(models.Announcement).filter(models.Announcement.created_by == uid).delete()
    # EducationalLink
    db.query(models.EducationalLink).filter(models.EducationalLink.created_by == uid).delete()
    # AdminTodo（作成者のみ削除。担当者欄はNULLで可）
    db.query(models.AdminTodo).filter(models.AdminTodo.created_by == uid).delete()
    # approver_id が自分のUserSkillLevelは承認者を外す（NULLは可）
    db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.approver_id == uid
    ).update({"approver_id": None})

    # 本人に紐づくレコードを削除
    # （SQLiteはFK制約のON DELETE CASCADEを実行時に強制しないため、
    #   ここで明示的に削除しないとIDが再利用された際に新規ユーザーへ
    #   バッジ等が引き継がれてしまう）
    db.query(models.UserBadge).filter(models.UserBadge.user_id == uid).delete()
    db.query(models.UserSubSkillLevel).filter(models.UserSubSkillLevel.user_id == uid).delete()
    db.query(models.SkillEvidence).filter(models.SkillEvidence.user_id == uid).delete()
    db.query(models.SkillGoal).filter(models.SkillGoal.user_id == uid).delete()

    db.flush()
    db.delete(target)
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{uid}/reset-password")
def reset_password(
    uid: int,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    """管理者がユーザーのパスワードを強制リセット（従来の直接指定）"""
    current_user = auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if target and len(new_password) >= 6:
        target.password_hash = auth.hash_password(new_password)
        db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{uid}/issue-temp-password")
def issue_temp_password(uid: int, request: Request, db: Session = Depends(get_db)):
    """仮パスワードを発行してユーザーに設定する。次回ログイン時に強制変更を求める"""
    import secrets, string
    auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if not target or target.role == "admin":
        return RedirectResponse("/admin/users?error=対象ユーザーが見つかりません", status_code=302)
    # ランダムな仮パスワード生成（英数字8文字）
    chars = string.ascii_letters + string.digits
    temp_pw = ''.join(secrets.choice(chars) for _ in range(10))
    target.password_hash = auth.hash_password(temp_pw)
    target.must_change_password = True
    db.commit()
    # 仮パスワードをクエリパラメータで一度だけ表示
    from urllib.parse import quote
    return RedirectResponse(
        f"/admin/users?temp_pw_user={target.username}&temp_pw={quote(temp_pw)}",
        status_code=302
    )


# ─── メール (SMTP) 設定 ─────────────────────────────────────────

NOTIFY_KEYS = [
    "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from_name", "app_url",
]


def _load_notify_settings(db) -> dict:
    rows = db.query(models.AppSetting).filter(models.AppSetting.key.in_(NOTIFY_KEYS)).all()
    return {r.key: r.value for r in rows}


def _save_setting(db, key: str, val: str):
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    if row:
        row.value = val
    else:
        db.add(models.AppSetting(key=key, value=val))


# 旧 URL → 新 URL リダイレクト
@router.get("/settings/mail", response_class=HTMLResponse)
def mail_settings_redirect(request: Request):
    return RedirectResponse("/admin/settings/notifications", status_code=301)


@router.get("/settings/notifications", response_class=HTMLResponse)
def notify_settings_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_admin(request, db)
    settings = _load_notify_settings(db)
    return templates.TemplateResponse(request, "admin/notification_settings.html", {
        "current_user": user,
        "settings": settings,
        "success": request.query_params.get("success"),
        "error":   request.query_params.get("error"),
    })


@router.post("/settings/notifications")
def notify_settings_post(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from_name: str = Form("スキルマップ"),
    app_url: str = Form("http://localhost:8190"),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    values = {
        "smtp_host": smtp_host.strip(),
        "smtp_port": smtp_port.strip() or "587",
        "smtp_user": smtp_user.strip(),
        "smtp_password": smtp_password.strip(),
        "smtp_from_name": smtp_from_name.strip() or "スキルマップ",
        "app_url": app_url.strip() or "http://localhost:8190",
    }
    for key, val in values.items():
        _save_setting(db, key, val)
    db.commit()
    return RedirectResponse("/admin/settings/notifications?success=通知設定を保存しました", status_code=302)


@router.post("/settings/notifications/test-mail")
def mail_test_post(
    request: Request,
    test_email: str = Form(""),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    if not test_email.strip():
        return RedirectResponse("/admin/settings/notifications?error=送信先メールアドレスを入力してください", status_code=302)
    ok, msg = mail.send_test_mail(test_email.strip())
    if ok:
        return RedirectResponse(f"/admin/settings/notifications?success={msg}", status_code=302)
    return RedirectResponse(f"/admin/settings/notifications?error={msg}", status_code=302)


# 旧テスト送信互換
@router.post("/settings/mail/test")
def mail_test_compat(request: Request, test_email: str = Form(""), db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    ok, msg = mail.send_test_mail(test_email.strip()) if test_email.strip() else (False, "メールアドレス未入力")
    dest = "success" if ok else "error"
    return RedirectResponse(f"/admin/settings/notifications?{dest}={msg}", status_code=302)


# ══════════════════════════════════════════════════════════════
# AI 設定
# ══════════════════════════════════════════════════════════════

@router.get("/settings/ai", response_class=HTMLResponse)
def ai_settings_get(request: Request, db: Session = Depends(get_db)):
    """AI設定はデータ管理ページに統合済み"""
    auth.require_admin(request, db)
    return RedirectResponse("/admin/settings/data", status_code=301)


@router.post("/settings/ai")
def ai_settings_post(
    request: Request,
    ai_enabled: str = Form(""),
    ai_provider: str = Form("anthropic"),
    local_llm_url: str = Form(""),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    save_config({
        "ai_summary_enabled": ai_enabled == "on",
        "ai_provider":        ai_provider.strip() or "anthropic",
        "local_llm_url":      local_llm_url.strip(),
    })
    return RedirectResponse("/admin/settings/data?ai_success=AI設定を保存しました", status_code=302)


# ══════════════════════════════════════════════════════════════
# データ管理（カテゴリ・スキルカタログ・サブスキルの一括エクスポート/インポート）
# ══════════════════════════════════════════════════════════════

@router.get("/settings/data", response_class=HTMLResponse)
def data_settings_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_admin(request, db)
    cfg = get_config()
    return templates.TemplateResponse(request, "admin/data_settings.html", {
        "current_user": user,
        "category_count": db.query(models.Category).count(),
        "skill_count": db.query(models.Skill).filter(models.Skill.is_archived == False).count(),
        "sub_skill_count": db.query(models.SubSkill).count(),
        "education_path_count": db.query(models.EducationalLink).filter(models.EducationalLink.skill_id.isnot(None)).count(),
        "certification_catalog_count": db.query(models.CertificationCatalog).count(),
        "business_map_area_count": db.query(models.BusinessMapArea).count(),
        "todo_count": db.query(models.AdminTodo).count(),
        "wiki_count": db.query(models.WikiPage).count(),
        "exam_count": db.query(models.Exam).filter(models.Exam.is_archived == False).count(),
        "promo_video_exists": os.path.isfile(PROMO_VIDEO_PATH),
        "ai_enabled":   cfg.get("ai_summary_enabled", False),
        "ai_provider":  cfg.get("ai_provider", "anthropic"),
        "local_llm_url": cfg.get("local_llm_url", ""),
    })


# ══════════════════════════════════════════════════════════════
# Admin ToDoリスト
# ══════════════════════════════════════════════════════════════

_TODO_SEED = [
    # 高優先度
    ("チーム内希少性スコア表示",
     "各スキルを「チームで自分だけ」「上位X%」などと表示しモチベーション向上につなげる",
     "high", 0),
    # 中優先度
    ("スキルカバレッジヒートマップ改善",
     "スキル×メンバーのマトリクスで色分けしカバレッジを視覚化する（/skills/matrix のデータが増えた後）",
     "medium", 0),
    ("メンバー成長レポート（定期サマリー）",
     "月次で「先月より〇件スキルが増えた」をダッシュボードまたはメール通知で提供する",
     "medium", 1),
    ("申告レベル別通知",
     "承認完了時・目標達成時にSlack/Teams Webhook通知を送れるようにする",
     "medium", 2),
    # 長期目線
    ("AIによるスキル推奨",
     "同じロールのメンバーが持っていて自分が持っていないスキルを提案する（ユーザー3名以上に有効）",
     "long_term", 0),
    ("ローテーション計画支援",
     "スキルセットでメンバーをマッチングし「このポジションには誰が適任か」を可視化する",
     "long_term", 1),
    ("スキル有効期限・再評価",
     "申告から一定期間経過したスキルに「再確認してください」アラートを出す",
     "long_term", 2),
]


def _seed_admin_todos(db):
    """初回のみToDoシードデータを投入する"""
    if db.query(models.AdminTodo).count() > 0:
        return
    for title, desc, priority, order_index in _TODO_SEED:
        db.add(models.AdminTodo(
            title=title,
            description=desc,
            priority=priority,
            status="pending",
            order_index=order_index,
        ))
    db.commit()


@router.get("/todos", response_class=HTMLResponse)
def admin_todos_list(request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    _seed_admin_todos(db)
    all_todos = db.query(models.AdminTodo).order_by(models.AdminTodo.order_index, models.AdminTodo.id).all()
    columns = {
        "pending":     [t for t in all_todos if t.status == "pending"],
        "in_progress": [t for t in all_todos if t.status == "in_progress"],
        "review":      [t for t in all_todos if t.status == "review"],
        "done":        [t for t in all_todos if t.status == "done"],
    }
    return templates.TemplateResponse(request, "admin_todos.html", {
        "current_user": auth.get_current_user(request, db),
        "columns": columns,
        "all_todos": all_todos,
    })


@router.post("/todos")
def admin_todo_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(default=""),
    priority: str = Form(default="medium"),
    status: str = Form(default="pending"),
    db: Session = Depends(get_db),
):
    user = auth.require_admin(request, db)
    max_order = db.query(models.AdminTodo).filter(
        models.AdminTodo.status == status
    ).count()
    db.add(models.AdminTodo(
        title=title.strip(),
        description=description.strip() or None,
        priority=priority,
        status=status,
        order_index=max_order,
        created_by=user.id,
    ))
    db.commit()
    return RedirectResponse("/admin/todos", status_code=303)


@router.post("/todos/{todo_id}/move")
def admin_todo_move(
    todo_id: int,
    request: Request,
    status: str = Form(...),
    order_index: int = Form(default=0),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    todo = db.query(models.AdminTodo).filter(models.AdminTodo.id == todo_id).first()
    if todo:
        todo.status = status
        todo.order_index = order_index
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/todos/{todo_id}/edit")
def admin_todo_edit(
    todo_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(default=""),
    priority: str = Form(default="medium"),
    status: str = Form(default="pending"),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    todo = db.query(models.AdminTodo).filter(models.AdminTodo.id == todo_id).first()
    if todo:
        todo.title = title.strip()
        todo.description = description.strip() or None
        todo.priority = priority
        todo.status = status
        db.commit()
    return RedirectResponse("/admin/todos", status_code=303)


@router.post("/todos/{todo_id}/status")
def admin_todo_status(
    todo_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    todo = db.query(models.AdminTodo).filter(models.AdminTodo.id == todo_id).first()
    if todo:
        todo.status = status
        db.commit()
    return RedirectResponse("/admin/todos", status_code=303)


@router.post("/todos/{todo_id}/delete")
def admin_todo_delete(
    todo_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    todo = db.query(models.AdminTodo).filter(models.AdminTodo.id == todo_id).first()
    if todo:
        db.delete(todo)
        db.commit()
    return RedirectResponse("/admin/todos", status_code=303)


@router.get("/todos/export")
def admin_todos_export(request: Request, db: Session = Depends(get_db)):
    """ToDoリストを1つのJSONファイルで一括エクスポート"""
    from fastapi.responses import Response as _Response
    import json as _json
    from datetime import datetime as _dt
    auth.require_admin(request, db)

    todos = db.query(models.AdminTodo).order_by(
        models.AdminTodo.status, models.AdminTodo.order_index, models.AdminTodo.id
    ).all()

    data = {
        "exported_at": _dt.now().isoformat(),
        "todos": [
            {
                "title": t.title,
                "description": t.description or "",
                "priority": t.priority,
                "status": t.status,
                "order_index": t.order_index,
            }
            for t in todos
        ],
    }

    body = _json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"skillmap_todos_{_dt.now().strftime('%Y%m%d')}.json"
    return _Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/todos/import")
async def admin_todos_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """一括エクスポートJSONファイルからToDoリストを一括インポートする（同名タイトルは新規追加せずスキップ）"""
    import json as _json
    user = auth.require_admin(request, db)

    content = await file.read()
    try:
        data = _json.loads(content.decode("utf-8-sig"))
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON の解析に失敗しました"}, status_code=400)

    added = skipped = 0
    for item in data.get("todos", []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        if db.query(models.AdminTodo).filter(models.AdminTodo.title == title).first():
            skipped += 1
            continue
        status = item.get("status") or "pending"
        max_order = db.query(models.AdminTodo).filter(models.AdminTodo.status == status).count()
        db.add(models.AdminTodo(
            title=title,
            description=(item.get("description") or "").strip() or None,
            priority=item.get("priority") or "medium",
            status=status,
            order_index=max_order,
            created_by=user.id,
        ))
        added += 1

    db.commit()
    return JSONResponse({"ok": True, "added": added, "skipped": skipped})
