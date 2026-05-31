from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta

import models
import auth
from database import get_db
from template_engine import templates

router = APIRouter()

NEW_HOURS = 1  # 新着と判定する時間（時間）


@router.get("/announcements", response_class=HTMLResponse)
def announcements_list(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    q = db.query(models.Announcement)
    if user.role != "admin":
        q = q.filter(models.Announcement.is_published == True)
    all_anns = q.order_by(models.Announcement.created_at.desc()).all()

    cutoff = datetime.utcnow() - timedelta(hours=NEW_HOURS)

    if user.role == "admin":
        # Admin: 投稿から1時間以内を「新着」、それ以降を「過去の投稿」
        new_anns  = [a for a in all_anns if a.created_at and a.created_at >= cutoff]
        past_anns = [a for a in all_anns if not a.created_at or a.created_at < cutoff]
    else:
        # Manager/User: 全件渡してクライアント側でlocalStorageの既読状態で分離
        new_anns  = all_anns
        past_anns = []

    return templates.TemplateResponse(request, "announcements.html", {
        "current_user": user,
        "announcements": all_anns,
        "new_anns":  new_anns,
        "past_anns": past_anns,
    })


@router.get("/announcements/new", response_class=HTMLResponse)
def announcement_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    if user.role != "admin":
        return RedirectResponse("/announcements", status_code=303)
    return templates.TemplateResponse(request, "announcement_form.html", {
        "current_user": user, "ann": None, "error": None,
    })


@router.post("/announcements/new")
async def announcement_new_post(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    ann_type: str = Form("feature"),
    scheduled_at: str = Form(""),
    is_published: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    if user.role != "admin":
        return RedirectResponse("/announcements", status_code=303)

    sched = None
    if scheduled_at.strip():
        try:
            sched = datetime.strptime(scheduled_at.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    ann = models.Announcement(
        title=title.strip(),
        content=content.strip(),
        ann_type=ann_type,
        scheduled_at=sched,
        is_published=is_published,
        created_by=user.id,
    )
    db.add(ann)
    db.commit()
    return RedirectResponse("/announcements", status_code=303)


@router.get("/announcements/{ann_id}/edit", response_class=HTMLResponse)
def announcement_edit_get(ann_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    if user.role != "admin":
        return RedirectResponse("/announcements", status_code=303)
    ann = db.query(models.Announcement).filter(models.Announcement.id == ann_id).first()
    if not ann:
        return RedirectResponse("/announcements", status_code=303)
    return templates.TemplateResponse(request, "announcement_form.html", {
        "current_user": user, "ann": ann, "error": None,
    })


@router.post("/announcements/{ann_id}/edit")
async def announcement_edit_post(
    ann_id: int,
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    ann_type: str = Form("feature"),
    scheduled_at: str = Form(""),
    is_published: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    if user.role != "admin":
        return RedirectResponse("/announcements", status_code=303)
    ann = db.query(models.Announcement).filter(models.Announcement.id == ann_id).first()
    if not ann:
        return RedirectResponse("/announcements", status_code=303)

    sched = None
    if scheduled_at.strip():
        try:
            sched = datetime.strptime(scheduled_at.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    ann.title        = title.strip()
    ann.content      = content.strip()
    ann.ann_type     = ann_type
    ann.scheduled_at = sched
    ann.is_published = is_published
    db.commit()
    return RedirectResponse("/announcements", status_code=303)


@router.post("/announcements/{ann_id}/delete")
def announcement_delete(ann_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    if user.role != "admin":
        return RedirectResponse("/announcements", status_code=303)
    ann = db.query(models.Announcement).filter(models.Announcement.id == ann_id).first()
    if ann:
        db.delete(ann)
        db.commit()
    return RedirectResponse("/announcements", status_code=303)


@router.get("/api/announcements/popup")
def announcements_popup(request: Request, db: Session = Depends(get_db)):
    """ポップアップ用: 未読のお知らせを返す（suppress設定済みユーザーは空を返す）"""
    user = auth.get_current_user(request, db)
    if not user or not user.is_approved:
        return JSONResponse({"announcements": [], "suppress": True})
    if getattr(user, "suppress_ann_popup", False):
        return JSONResponse({"announcements": [], "suppress": True})

    # セッションに既読IDを持つ（セッション未対応の場合はCookieで管理）
    # ここではDBのお知らせ全件を返し、クライアント側でlocalStorageと照合する
    anns = (
        db.query(models.Announcement)
        .filter(models.Announcement.is_published == True)
        .order_by(models.Announcement.created_at.desc())
        .limit(5)
        .all()
    )
    return JSONResponse({
        "suppress": False,
        "announcements": [
            {
                "id":         a.id,
                "title":      a.title,
                "type":       models.ANNOUNCEMENT_TYPES.get(a.ann_type, a.ann_type),
                "type_key":   a.ann_type,
                "type_color": models.ANNOUNCEMENT_TYPE_COLORS.get(a.ann_type, "secondary"),
                "content":    a.content[:200] + ("…" if len(a.content) > 200 else ""),
                "created_at": a.created_at.strftime("%Y/%m/%d") if a.created_at else "",
            }
            for a in anns
        ]
    })


@router.post("/api/announcements/suppress-popup")
def suppress_popup(request: Request, db: Session = Depends(get_db)):
    """ポップアップを今後表示しない設定"""
    user = auth.require_approved(request, db)
    user.suppress_ann_popup = True
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/announcements/enable-popup")
def enable_popup(request: Request, db: Session = Depends(get_db)):
    """ポップアップ表示を再有効化"""
    user = auth.require_approved(request, db)
    user.suppress_ann_popup = False
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/announcements/latest")
def announcements_latest(request: Request, db: Session = Depends(get_db)):
    """ベルアイコン用: 公開中のお知らせ一覧を返す"""
    user = auth.require_approved(request, db)
    anns = (
        db.query(models.Announcement)
        .filter(models.Announcement.is_published == True)
        .order_by(models.Announcement.created_at.desc())
        .limit(10)
        .all()
    )
    return JSONResponse({
        "announcements": [
            {
                "id":           a.id,
                "title":        a.title,
                "type":         models.ANNOUNCEMENT_TYPES.get(a.ann_type, a.ann_type),
                "type_key":     a.ann_type,
                "type_color":   models.ANNOUNCEMENT_TYPE_COLORS.get(a.ann_type, "secondary"),
                "scheduled_at": a.scheduled_at.strftime("%Y/%m/%d") if a.scheduled_at else None,
                "created_at":   a.created_at.strftime("%Y/%m/%d") if a.created_at else "",
                "snippet":      a.content[:80] + ("…" if len(a.content) > 80 else ""),
            }
            for a in anns
        ]
    })
