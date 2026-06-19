import os
import shutil
import uuid as _uuid

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

import models
import auth
from database import get_db
from template_engine import templates, _render_markdown

router = APIRouter(prefix="/wiki")

WIKI_UPLOAD_DIR = os.path.join("data", "uploads", "wiki")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _user_group_ids(user: models.User, db: Session) -> set[int]:
    rows = (
        db.query(models.GroupMembership.group_id)
        .filter(models.GroupMembership.user_id == user.id)
        .all()
    )
    return {r[0] for r in rows}


def _parse_scope(value: str, user: models.User, db: Session):
    """公開範囲セレクトの値を (visibility, group_id) に変換する"""
    value = (value or "").strip()
    if value == "all":
        return "all", None
    if not value:
        return "private", None
    try:
        gid = int(value)
    except ValueError:
        return "private", None
    if gid not in _user_group_ids(user, db) and user.role != "admin":
        return "private", None
    return "group", gid


def _can_view(page: models.WikiPage, user: models.User, db: Session) -> bool:
    if user.role == "admin":
        return True
    if page.created_by == user.id:
        return True
    if page.visibility == "all":
        return True
    if page.visibility == "group" and page.group_id is not None:
        return page.group_id in _user_group_ids(user, db)
    return False


def _can_edit(page: models.WikiPage, user: models.User, db: Session) -> bool:
    if user.role == "admin":
        return True
    if page.created_by == user.id:
        return True
    if page.edit_mode != "members":
        return False
    if page.visibility == "all":
        return True
    if page.visibility == "group" and page.group_id is not None:
        return page.group_id in _user_group_ids(user, db)
    return False


def _can_delete(page: models.WikiPage, user: models.User) -> bool:
    return user.role == "admin" or page.created_by == user.id


@router.get("", response_class=HTMLResponse)
def wiki_list(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    group_ids = _user_group_ids(user, db)

    if user.role == "admin":
        pages = db.query(models.WikiPage).order_by(models.WikiPage.updated_at.desc()).all()
    else:
        pages = (
            db.query(models.WikiPage)
            .filter(or_(
                models.WikiPage.created_by == user.id,
                models.WikiPage.visibility == "all",
                and_(models.WikiPage.visibility == "group", models.WikiPage.group_id.in_(group_ids)) if group_ids else False,
            ))
            .order_by(models.WikiPage.updated_at.desc())
            .all()
        )

    personal_pages = [p for p in pages if p.visibility == "private"]
    global_pages = [p for p in pages if p.visibility == "all"]
    group_pages = [p for p in pages if p.visibility == "group"]

    groups_with_pages = []
    seen_group_ids = set()
    for p in group_pages:
        if p.group_id not in seen_group_ids:
            seen_group_ids.add(p.group_id)
            groups_with_pages.append(p.group)
    groups_with_pages.sort(key=lambda g: g.name)

    pages_by_group = {}
    for p in group_pages:
        pages_by_group.setdefault(p.group_id, []).append(p)

    my_groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).order_by(models.Group.name).all() if group_ids else []

    return templates.TemplateResponse(request, "wiki_list.html", {
        "current_user": user,
        "personal_pages": personal_pages,
        "global_pages": global_pages,
        "groups_with_pages": groups_with_pages,
        "pages_by_group": pages_by_group,
        "my_groups": my_groups,
    })


@router.get("/new", response_class=HTMLResponse)
def wiki_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    group_ids = _user_group_ids(user, db)
    my_groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).order_by(models.Group.name).all() if group_ids else []
    return templates.TemplateResponse(request, "wiki_form.html", {
        "current_user": user, "page": None, "my_groups": my_groups, "error": None,
    })


@router.post("/new")
def wiki_new_post(
    request: Request,
    title: str = Form(...),
    content: str = Form(""),
    scope: str = Form(""),
    edit_mode: str = Form("owner"),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)

    visibility, gid = _parse_scope(scope, user, db)
    if edit_mode not in models.WIKI_EDIT_MODES or visibility == "private":
        edit_mode = "owner"

    page = models.WikiPage(
        title=title.strip(),
        content=content,
        group_id=gid,
        visibility=visibility,
        edit_mode=edit_mode,
        created_by=user.id,
    )
    db.add(page)
    db.commit()
    return RedirectResponse(f"/wiki/{page.id}", status_code=303)


@router.post("/upload-image")
async def wiki_upload_image(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    auth.require_approved(request, db)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return JSONResponse({"error": "対応していない画像形式です"}, status_code=400)

    os.makedirs(WIKI_UPLOAD_DIR, exist_ok=True)
    saved_name = f"{_uuid.uuid4()}{ext}"
    save_path = os.path.join(WIKI_UPLOAD_DIR, saved_name)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return JSONResponse({"url": f"/wiki/uploads/{saved_name}"})


@router.get("/uploads/{filename}")
def wiki_serve_upload(filename: str, request: Request, db: Session = Depends(get_db)):
    auth.require_approved(request, db)
    safe_name = os.path.basename(filename)
    path = os.path.join(WIKI_UPLOAD_DIR, safe_name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path)


@router.post("/preview")
def wiki_preview(
    request: Request,
    content: str = Form(""),
    db: Session = Depends(get_db),
):
    auth.require_approved(request, db)
    return JSONResponse({"html": _render_markdown(content)})


@router.get("/{wiki_id:int}", response_class=HTMLResponse)
def wiki_view(wiki_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    page = db.query(models.WikiPage).filter(models.WikiPage.id == wiki_id).first()
    if not page:
        return RedirectResponse("/wiki", status_code=303)
    if not _can_view(page, user, db):
        raise HTTPException(status_code=403, detail="このWikiページを閲覧する権限がありません")

    return templates.TemplateResponse(request, "wiki_view.html", {
        "current_user": user,
        "page": page,
        "can_edit": _can_edit(page, user, db),
        "can_delete": _can_delete(page, user),
    })


@router.get("/{wiki_id:int}/edit", response_class=HTMLResponse)
def wiki_edit_get(wiki_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    page = db.query(models.WikiPage).filter(models.WikiPage.id == wiki_id).first()
    if not page:
        return RedirectResponse("/wiki", status_code=303)
    if not _can_edit(page, user, db):
        raise HTTPException(status_code=403, detail="このWikiページを編集する権限がありません")

    group_ids = _user_group_ids(user, db)
    my_groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).order_by(models.Group.name).all() if group_ids else []
    return templates.TemplateResponse(request, "wiki_form.html", {
        "current_user": user, "page": page, "my_groups": my_groups, "error": None,
    })


@router.post("/{wiki_id:int}/edit")
def wiki_edit_post(
    wiki_id: int,
    request: Request,
    title: str = Form(...),
    content: str = Form(""),
    scope: str = Form(""),
    edit_mode: str = Form("owner"),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    page = db.query(models.WikiPage).filter(models.WikiPage.id == wiki_id).first()
    if not page:
        return RedirectResponse("/wiki", status_code=303)
    if not _can_edit(page, user, db):
        raise HTTPException(status_code=403, detail="このWikiページを編集する権限がありません")

    # 公開範囲・編集モードは作成者または管理者のみ変更可能
    if page.created_by == user.id or user.role == "admin":
        visibility, gid = _parse_scope(scope, user, db)
        if edit_mode not in models.WIKI_EDIT_MODES or visibility == "private":
            edit_mode = "owner"
        page.visibility = visibility
        page.group_id = gid
        page.edit_mode = edit_mode

    page.title = title.strip()
    page.content = content
    db.commit()
    return RedirectResponse(f"/wiki/{page.id}", status_code=303)


@router.post("/{wiki_id:int}/delete")
def wiki_delete(wiki_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    page = db.query(models.WikiPage).filter(models.WikiPage.id == wiki_id).first()
    if not page:
        return RedirectResponse("/wiki", status_code=303)
    if not _can_delete(page, user):
        raise HTTPException(status_code=403, detail="このWikiページを削除する権限がありません")

    db.delete(page)
    db.commit()
    return RedirectResponse("/wiki", status_code=303)


# ── Wiki エクスポート / インポート ─────────────────────────────────────────────

@router.get("/export")
def wiki_export(request: Request, db: Session = Depends(get_db)):
    """Wikiページ全件をJSONエクスポート（Admin専用）"""
    import json as _json
    from fastapi.responses import Response as _Response
    from datetime import datetime as _dt
    auth.require_admin(request, db)

    pages = db.query(models.WikiPage).order_by(models.WikiPage.id).all()
    data = {
        "exported_at": _dt.now().isoformat(),
        "wiki_pages": [
            {
                "title": p.title,
                "content": p.content,
                "visibility": p.visibility,
                "group_name": p.group.name if p.group else None,
                "edit_mode": p.edit_mode,
                "created_by_username": p.creator.username if p.creator else None,
            }
            for p in pages
        ],
    }
    body = _json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"skillmap_wiki_{_dt.now().strftime('%Y%m%d')}.json"
    return _Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/import")
async def wiki_import(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("add"),
    db: Session = Depends(get_db),
):
    """WikiページをJSONからインポート（Admin専用）"""
    import json as _json
    user = auth.require_admin(request, db)

    content = await file.read()
    try:
        data = _json.loads(content.decode("utf-8-sig"))
    except Exception:
        return JSONResponse({"ok": False, "error": "JSONの解析に失敗しました"}, status_code=400)

    pages_data = data.get("wiki_pages", [])
    if not isinstance(pages_data, list):
        return JSONResponse({"ok": False, "error": "wiki_pages フィールドが見つかりません"}, status_code=400)

    # グループ名 → ID マップ
    group_map = {g.name: g.id for g in db.query(models.Group).all()}

    added = updated = skipped = 0
    for item in pages_data:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        group_id = group_map.get(item.get("group_name")) if item.get("group_name") else None
        existing = db.query(models.WikiPage).filter(models.WikiPage.title == title).first()

        if existing:
            if mode == "update":
                existing.content = item.get("content", "")
                existing.visibility = item.get("visibility", "private")
                existing.group_id = group_id
                existing.edit_mode = item.get("edit_mode", "owner")
                updated += 1
            else:
                skipped += 1
        else:
            db.add(models.WikiPage(
                title=title,
                content=item.get("content", ""),
                visibility=item.get("visibility", "private"),
                group_id=group_id,
                edit_mode=item.get("edit_mode", "owner"),
                created_by=user.id,
            ))
            added += 1

    db.commit()
    return JSONResponse({"ok": True, "added": added, "updated": updated, "skipped": skipped})
