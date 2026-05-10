from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_

import models
import auth
from database import get_db
from template_engine import templates
from routers.groups import _get_all_group_skill_ids

router = APIRouter()


def _get_user_scope(user, db) -> dict | None:
    """User ロールの場合、参加グループに基づくスキルID・カテゴリIDを返す。
    Manager/Admin の場合は None（制限なし）。
    グループ未所属の場合は空セットを含む dict を返す。"""
    if user.role != "user":
        return None  # 制限なし

    memberships = (
        db.query(models.GroupMembership)
        .filter(models.GroupMembership.user_id == user.id)
        .all()
    )
    if not memberships:
        return {"skill_ids": set(), "cat_ids": set(), "no_group": True}

    skill_ids: set[int] = set()
    for m in memberships:
        skill_ids |= _get_all_group_skill_ids(m.group)

    cat_ids: set[int] = set()
    if skill_ids:
        for s in db.query(models.Skill).filter(models.Skill.id.in_(skill_ids)).all():
            if s.category_id:
                cat_ids.add(s.category_id)

    return {"skill_ids": skill_ids, "cat_ids": cat_ids, "no_group": False}


@router.get("/education", response_class=HTMLResponse)
def education_list(
    request: Request,
    category_id: int = 0,
    skill_id: int = 0,
    q: str = "",
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    scope = _get_user_scope(user, db)

    query = db.query(models.EducationalLink)

    # ── User スコープ絞り込み ──────────────────────────────────────
    if scope is not None:
        if scope["no_group"] or (not scope["skill_ids"] and not scope["cat_ids"]):
            # グループ未所属 or グループにスキルなし → カテゴリ・スキル未指定のリンクのみ
            query = query.filter(
                models.EducationalLink.category_id.is_(None),
                models.EducationalLink.skill_id.is_(None),
            )
        else:
            # 参加グループのカテゴリ or スキルに一致するリンク＋未分類リンク
            query = query.filter(
                or_(
                    models.EducationalLink.category_id.in_(scope["cat_ids"]),
                    models.EducationalLink.skill_id.in_(scope["skill_ids"]),
                    # カテゴリ・スキル未指定（全員向け）
                    (models.EducationalLink.category_id.is_(None) &
                     models.EducationalLink.skill_id.is_(None)),
                )
            )
        # フィルターパラメータもスコープ内に限定
        if category_id and scope["cat_ids"] and category_id not in scope["cat_ids"]:
            category_id = 0
        if skill_id and scope["skill_ids"] and skill_id not in scope["skill_ids"]:
            skill_id = 0

    # ── ユーザー操作フィルター ─────────────────────────────────────
    if category_id:
        query = query.filter(models.EducationalLink.category_id == category_id)
    if skill_id:
        query = query.filter(models.EducationalLink.skill_id == skill_id)
    if q:
        query = query.filter(
            models.EducationalLink.title.ilike(f"%{q}%") |
            models.EducationalLink.description.ilike(f"%{q}%")
        )
    links = query.order_by(
        models.EducationalLink.category_id,
        models.EducationalLink.title,
    ).all()

    # ── フィルタードロップダウンの選択肢 ──────────────────────────
    if scope is not None and not scope["no_group"] and scope["cat_ids"]:
        categories = (
            db.query(models.Category)
            .filter(models.Category.id.in_(scope["cat_ids"]))
            .order_by(models.Category.name).all()
        )
        skills = (
            db.query(models.Skill)
            .filter(models.Skill.id.in_(scope["skill_ids"]))
            .order_by(models.Skill.name).all()
        )
    else:
        categories = db.query(models.Category).order_by(models.Category.name).all()
        skills     = db.query(models.Skill).order_by(models.Skill.name).all()

    return templates.TemplateResponse(request, "education.html", {
        "current_user": user,
        "links": links,
        "categories": categories,
        "skills": skills,
        "sel_category": category_id,
        "sel_skill": skill_id,
        "q": q,
        "is_scoped": scope is not None,          # User スコープが有効か
        "no_group":  scope is not None and scope.get("no_group", False),
    })


@router.get("/education/new", response_class=HTMLResponse)
def education_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    skills     = db.query(models.Skill).order_by(models.Skill.category_id, models.Skill.name).all()
    return templates.TemplateResponse(request, "education_form.html", {
        "current_user": user,
        "link": None,
        "categories": categories,
        "skills": skills,
        "error": None,
    })


@router.post("/education/new")
async def education_new_post(
    request: Request,
    title: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(0),
    skill_id: int = Form(0),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)

    if not title.strip() or not url.strip():
        categories = db.query(models.Category).order_by(models.Category.name).all()
        skills     = db.query(models.Skill).order_by(models.Skill.name).all()
        return templates.TemplateResponse(request, "education_form.html", {
            "current_user": user, "link": None,
            "categories": categories, "skills": skills,
            "error": "タイトルとURLは必須です",
        })

    link = models.EducationalLink(
        title=title.strip(),
        url=url.strip(),
        description=description.strip() or None,
        category_id=category_id or None,
        skill_id=skill_id or None,
        created_by=user.id,
    )
    db.add(link)
    db.commit()
    return RedirectResponse("/education", status_code=303)


@router.get("/education/{link_id}/edit", response_class=HTMLResponse)
def education_edit_get(link_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == link_id).first()
    if not link:
        return RedirectResponse("/education", status_code=303)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    skills     = db.query(models.Skill).order_by(models.Skill.category_id, models.Skill.name).all()
    return templates.TemplateResponse(request, "education_form.html", {
        "current_user": user,
        "link": link,
        "categories": categories,
        "skills": skills,
        "error": None,
    })


@router.post("/education/{link_id}/edit")
async def education_edit_post(
    link_id: int,
    request: Request,
    title: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(0),
    skill_id: int = Form(0),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == link_id).first()
    if not link:
        return RedirectResponse("/education", status_code=303)

    link.title       = title.strip()
    link.url         = url.strip()
    link.description = description.strip() or None
    link.category_id = category_id or None
    link.skill_id    = skill_id or None
    db.commit()
    return RedirectResponse("/education", status_code=303)


@router.post("/education/{link_id}/delete")
def education_delete(link_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == link_id).first()
    if link:
        db.delete(link)
        db.commit()
    return RedirectResponse("/education", status_code=303)
