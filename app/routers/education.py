from collections import defaultdict

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

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
def education_list(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    scope = _get_user_scope(user, db)

    # スキルに紐づくリソースをスコープ込みで取得
    path_query = db.query(models.EducationalLink).filter(
        models.EducationalLink.skill_id.isnot(None)
    )
    if scope is not None:
        if scope["no_group"] or not scope["skill_ids"]:
            path_query = path_query.filter(False)
        else:
            path_query = path_query.filter(
                models.EducationalLink.skill_id.in_(scope["skill_ids"])
            )
    path_links = path_query.all()

    # スキルID → step_order 順にソートしたリンク一覧
    _groups: dict = defaultdict(list)
    for lnk in path_links:
        _groups[lnk.skill_id].append(lnk)
    for sid in _groups:
        _groups[sid].sort(key=lambda x: (x.step_order is None, x.step_order or 0))

    # 現ユーザーの完了済みリンクID
    completed_ids = {
        p.educational_link_id
        for p in db.query(models.UserLearningProgress)
        .filter(models.UserLearningProgress.user_id == user.id)
        .all()
    }

    # スキルごとに active / done に分割（スキルが削除済みのリンクはスキップ）
    path_groups = []
    for sid, lnks in _groups.items():
        skill_obj = lnks[0].skill
        if skill_obj is None:
            continue  # 紐づくスキルが削除済みの場合はスキップ
        active = [l for l in lnks if l.id not in completed_ids]
        done   = [l for l in lnks if l.id in completed_ids]
        path_groups.append((skill_obj, active, done))
    path_groups.sort(key=lambda t: (t[0].category.name if t[0].category else "", t[0].name))

    # カテゴリーフィルター用（path_groups に含まれるカテゴリーのみ）
    seen: dict = {}
    for skill_obj, _, __ in path_groups:
        if skill_obj.category and skill_obj.category_id not in seen:
            seen[skill_obj.category_id] = skill_obj.category
    filter_categories = sorted(seen.values(), key=lambda c: c.name)

    return templates.TemplateResponse(request, "education.html", {
        "current_user": user,
        "is_scoped": scope is not None,
        "no_group":  scope is not None and scope.get("no_group", False),
        "path_groups": path_groups,
        "filter_categories": filter_categories,
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
    step_order: int = Form(0),
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
        step_order=step_order or None,
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
    step_order: int = Form(0),
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
    link.step_order  = step_order or None
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


# ── 学習パス一括管理 ─────────────────────────────────────────────────

@router.get("/education/path/{skill_id}", response_class=HTMLResponse)
def education_path_get(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/education", status_code=303)
    steps = (
        db.query(models.EducationalLink)
        .filter(models.EducationalLink.skill_id == skill_id)
        .all()
    )
    steps.sort(key=lambda x: (x.step_order is None, x.step_order or 0))
    return templates.TemplateResponse(request, "education_path.html", {
        "current_user": user,
        "skill": skill,
        "steps": steps,
        "error": None,
    })


@router.post("/education/path/{skill_id}/add")
async def education_path_add(
    skill_id: int,
    request: Request,
    title: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
    next: str = Form("/education/path/{skill_id}"),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/education", status_code=303)

    if not title.strip() or not url.strip():
        steps = (
            db.query(models.EducationalLink)
            .filter(models.EducationalLink.skill_id == skill_id)
            .all()
        )
        steps.sort(key=lambda x: (x.step_order is None, x.step_order or 0))
        return templates.TemplateResponse(request, "education_path.html", {
            "current_user": user,
            "skill": skill,
            "steps": steps,
            "error": "タイトルとURLは必須です",
        })

    # 次のステップ番号を自動計算
    existing = (
        db.query(models.EducationalLink)
        .filter(models.EducationalLink.skill_id == skill_id,
                models.EducationalLink.step_order.isnot(None))
        .all()
    )
    next_order = max((s.step_order for s in existing), default=0) + 1

    link = models.EducationalLink(
        title=title.strip(),
        url=url.strip(),
        description=description.strip() or None,
        category_id=skill.category_id,
        skill_id=skill_id,
        step_order=next_order,
        created_by=user.id,
    )
    db.add(link)
    db.commit()
    redirect_to = next if next.startswith("/") else f"/education/path/{skill_id}"
    return RedirectResponse(redirect_to, status_code=303)


@router.post("/education/path/{skill_id}/delete/{link_id}")
def education_path_delete(skill_id: int, link_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == link_id).first()
    if link:
        db.delete(link)
        db.commit()
    return RedirectResponse(f"/education/path/{skill_id}", status_code=303)


@router.post("/education/progress/{link_id}/toggle")
def toggle_progress(link_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    existing = (
        db.query(models.UserLearningProgress)
        .filter(
            models.UserLearningProgress.user_id == user.id,
            models.UserLearningProgress.educational_link_id == link_id,
        )
        .first()
    )
    if existing:
        db.delete(existing)
    else:
        db.add(models.UserLearningProgress(user_id=user.id, educational_link_id=link_id))
    db.commit()
    return RedirectResponse("/education", status_code=303)


@router.post("/education/path/{skill_id}/reorder/{link_id}")
async def education_path_reorder(
    skill_id: int,
    link_id: int,
    request: Request,
    step_order: int = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == link_id).first()
    if link:
        link.step_order = step_order or None
        db.commit()
    return RedirectResponse(f"/education/path/{skill_id}", status_code=303)
