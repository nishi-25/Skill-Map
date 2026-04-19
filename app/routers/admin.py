from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
import auth
import mail
from database import get_db
from template_engine import templates

router = APIRouter(prefix="/admin")


@router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user = auth.require_admin(request, db)
    user_count = db.query(models.User).count()
    pending_count = db.query(models.User).filter(models.User.is_approved == False).count()
    skill_count = db.query(models.Skill).count()
    cat_count = db.query(models.Category).count()
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "current_user": user,
        "user_count": user_count, "pending_count": pending_count,
        "skill_count": skill_count, "cat_count": cat_count,
        "users": users,
    })


@router.get("/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    user = auth.require_admin(request, db)
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/users.html", {
        "current_user": user, "users": users
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
    if target and target.id != current_user.id:
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
    """管理者がユーザーのパスワードを強制リセット"""
    current_user = auth.require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == uid).first()
    if target and len(new_password) >= 6:
        target.password_hash = auth.hash_password(new_password)
        db.commit()
    return RedirectResponse("/admin/users", status_code=302)


# ─── メール (SMTP) 設定 ─────────────────────────────────────────

SMTP_KEYS = ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from_name", "app_url"]


@router.get("/settings/mail", response_class=HTMLResponse)
def mail_settings_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_admin(request, db)
    # DB から現在の設定を読み込み
    rows = db.query(models.AppSetting).filter(
        models.AppSetting.key.in_(SMTP_KEYS)
    ).all()
    settings = {r.key: r.value for r in rows}
    return templates.TemplateResponse(request, "admin/mail_settings.html", {
        "current_user": user,
        "settings": settings,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.post("/settings/mail")
def mail_settings_post(
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
        row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
        if row:
            row.value = val
        else:
            db.add(models.AppSetting(key=key, value=val))
    db.commit()
    return RedirectResponse("/admin/settings/mail?success=設定を保存しました", status_code=302)


@router.post("/settings/mail/test")
def mail_test_post(
    request: Request,
    test_email: str = Form(""),
    db: Session = Depends(get_db),
):
    auth.require_admin(request, db)
    if not test_email.strip():
        return RedirectResponse("/admin/settings/mail?error=送信先メールアドレスを入力してください", status_code=302)
    ok, msg = mail.send_test_mail(test_email.strip())
    if ok:
        return RedirectResponse(f"/admin/settings/mail?success={msg}", status_code=302)
    return RedirectResponse(f"/admin/settings/mail?error={msg}", status_code=302)
