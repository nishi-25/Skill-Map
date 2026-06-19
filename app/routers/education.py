from collections import defaultdict

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

import models
import auth
from database import get_db
from template_engine import templates
from routers.groups import _get_all_group_skill_ids

router = APIRouter()


def _build_free_areas_tree(areas, completed_ids):
    result = []
    for area in areas:
        steps = sorted(area.steps, key=lambda x: (x.step_order is None, x.step_order or 0))
        done_count = sum(1 for s in steps if s.id in completed_ids)
        children = _build_free_areas_tree(
            sorted(area.children, key=lambda a: (a.order_index, a.id)),
            completed_ids,
        )
        result.append({
            "area": area,
            "steps": steps,
            "completed_ids": completed_ids,
            "done_count": done_count,
            "total": len(steps),
            "children": children,
        })
    return result


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

    # スキルに紐づくリソースをスコープ込みで取得（フリーエリア所属のリンクは除外）
    path_query = db.query(models.EducationalLink).filter(
        models.EducationalLink.skill_id.isnot(None),
        models.EducationalLink.area_id.is_(None),
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

    # スキルごとに全ステップ・完了ID付きのdictに整理（スキルが削除済みはスキップ）
    path_groups = []
    for sid, lnks in _groups.items():
        skill_obj = lnks[0].skill
        if skill_obj is None:
            continue
        done_count = sum(1 for l in lnks if l.id in completed_ids)
        total = len(lnks)
        path_groups.append({
            "skill": skill_obj,
            "steps": lnks,
            "completed_ids": completed_ids,
            "done_count": done_count,
            "total": total,
        })
    path_groups.sort(key=lambda g: (g["skill"].category.name if g["skill"].category else "", g["skill"].name))

    # カテゴリーフィルター用
    seen: dict = {}
    for g in path_groups:
        s = g["skill"]
        if s.category and s.category_id not in seen:
            seen[s.category_id] = s.category
    filter_categories = sorted(seen.values(), key=lambda c: c.name)

    # フリーエリア（LearningPathArea）をツリーとして取得
    top_areas = (
        db.query(models.LearningPathArea)
        .filter(models.LearningPathArea.parent_id.is_(None))
        .order_by(models.LearningPathArea.order_index, models.LearningPathArea.id)
        .all()
    )
    free_areas = _build_free_areas_tree(top_areas, completed_ids)

    return templates.TemplateResponse(request, "education.html", {
        "current_user": user,
        "is_scoped": scope is not None,
        "no_group":  scope is not None and scope.get("no_group", False),
        "path_groups": path_groups,
        "filter_categories": filter_categories,
        "free_areas": free_areas,
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
    next: str = Form(""),
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


@router.post("/education/path/{skill_id}/delete-all")
def education_path_delete_all(skill_id: int, request: Request, db: Session = Depends(get_db)):
    """スキルに紐づく学習パスのステップを全件削除する"""
    user = auth.require_manager_or_admin(request, db)
    db.query(models.EducationalLink).filter(models.EducationalLink.skill_id == skill_id).delete()
    db.commit()
    return RedirectResponse("/education", status_code=303)


@router.post("/education/progress/{link_id}/toggle")
def toggle_progress(link_id: int, request: Request, next: str = Form(""), db: Session = Depends(get_db)):
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
    redirect_to = next if next.startswith("/") else "/education"
    return RedirectResponse(redirect_to, status_code=303)


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


# ── 学習パス フリーエリア管理 ─────────────────────────────────────────────────

@router.get("/education/area/new", response_class=HTMLResponse)
def education_area_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    skills = db.query(models.Skill).order_by(models.Skill.category_id, models.Skill.name).all()
    return templates.TemplateResponse(request, "education_area_form.html", {
        "current_user": user,
        "area": None,
        "categories": categories,
        "skills": skills,
        "error": None,
    })


@router.post("/education/area/new")
async def education_area_new_post(
    request: Request,
    name: str = Form(""),
    category_id: int = Form(0),
    skill_id: int = Form(0),
    parent_id: int = Form(0),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    name = name.strip()

    # 名称未指定時はカテゴリ・スキル名を自動使用
    if not name:
        if skill_id:
            sk = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
            name = sk.name if sk else ""
        elif category_id:
            cat = db.query(models.Category).filter(models.Category.id == category_id).first()
            name = cat.name if cat else ""

    if not name:
        categories = db.query(models.Category).order_by(models.Category.name).all()
        skills = db.query(models.Skill).order_by(models.Skill.name).all()
        return templates.TemplateResponse(request, "education_area_form.html", {
            "current_user": user, "area": None,
            "categories": categories, "skills": skills,
            "error": "エリア名、またはカテゴリ・スキルのいずれかを指定してください",
        })

    count = db.query(models.LearningPathArea).count()
    area = models.LearningPathArea(
        name=name,
        parent_id=parent_id or None,
        category_id=category_id or None,
        skill_id=skill_id or None,
        order_index=count,
        created_by=user.id,
    )
    db.add(area)
    db.commit()
    redirect_to = next if next.startswith("/") else f"/education/area/{area.id}"
    return RedirectResponse(redirect_to, status_code=303)


@router.get("/education/area/{area_id}/edit", response_class=HTMLResponse)
def education_area_edit_get(area_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    area = db.query(models.LearningPathArea).filter(models.LearningPathArea.id == area_id).first()
    if not area:
        return RedirectResponse("/education", status_code=303)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    skills = db.query(models.Skill).order_by(models.Skill.category_id, models.Skill.name).all()
    return templates.TemplateResponse(request, "education_area_form.html", {
        "current_user": user,
        "area": area,
        "categories": categories,
        "skills": skills,
        "error": None,
    })


@router.post("/education/area/{area_id}/edit")
async def education_area_edit_post(
    area_id: int,
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    category_id: int = Form(0),
    skill_id: int = Form(0),
    next_url: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    area = db.query(models.LearningPathArea).filter(models.LearningPathArea.id == area_id).first()
    if not area:
        return RedirectResponse("/education", status_code=303)

    name = name.strip()
    if not name:
        if skill_id:
            sk = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
            name = sk.name if sk else area.name
        elif category_id:
            cat = db.query(models.Category).filter(models.Category.id == category_id).first()
            name = cat.name if cat else area.name

    if not name:
        categories = db.query(models.Category).order_by(models.Category.name).all()
        skills = db.query(models.Skill).order_by(models.Skill.name).all()
        return templates.TemplateResponse(request, "education_area_form.html", {
            "current_user": user, "area": area,
            "categories": categories, "skills": skills,
            "error": "エリア名、またはカテゴリ・スキルのいずれかを指定してください",
        })

    area.name = name
    area.description = description.strip() or None
    area.category_id = category_id or None
    area.skill_id = skill_id or None
    db.commit()
    redirect_to = next_url.strip() if next_url.strip() else f"/education/area/{area_id}"
    return RedirectResponse(redirect_to, status_code=303)


@router.get("/education/area/{area_id}", response_class=HTMLResponse)
def education_area_get(area_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    area = db.query(models.LearningPathArea).filter(models.LearningPathArea.id == area_id).first()
    if not area:
        return RedirectResponse("/education", status_code=303)
    steps = sorted(area.steps, key=lambda x: (x.step_order is None, x.step_order or 0))
    return templates.TemplateResponse(request, "education_area.html", {
        "current_user": user,
        "area": area,
        "steps": steps,
        "error": None,
    })


@router.post("/education/area/{area_id}/add")
async def education_area_add_step(
    area_id: int,
    request: Request,
    title: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    area = db.query(models.LearningPathArea).filter(models.LearningPathArea.id == area_id).first()
    if not area:
        return RedirectResponse("/education", status_code=303)

    if not title.strip() or not url.strip():
        steps = sorted(area.steps, key=lambda x: (x.step_order is None, x.step_order or 0))
        return templates.TemplateResponse(request, "education_area.html", {
            "current_user": user, "area": area, "steps": steps,
            "error": "タイトルとURLは必須です",
        })

    existing = [s for s in area.steps if s.step_order is not None]
    next_order = max((s.step_order for s in existing), default=0) + 1

    link = models.EducationalLink(
        title=title.strip(),
        url=url.strip(),
        description=description.strip() or None,
        area_id=area_id,
        category_id=area.category_id,
        skill_id=area.skill_id,
        step_order=next_order,
        created_by=user.id,
    )
    db.add(link)
    db.commit()
    return RedirectResponse(f"/education/area/{area_id}", status_code=303)


@router.post("/education/area/{area_id}/step/{step_id}/delete")
def education_area_delete_step(area_id: int, step_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == step_id).first()
    if link:
        db.delete(link)
        db.commit()
    return RedirectResponse(f"/education/area/{area_id}", status_code=303)


@router.post("/education/area/{area_id}/step/{step_id}/reorder")
async def education_area_reorder_step(
    area_id: int, step_id: int, request: Request,
    step_order: int = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    link = db.query(models.EducationalLink).filter(models.EducationalLink.id == step_id).first()
    if link:
        link.step_order = step_order or None
        db.commit()
    return RedirectResponse(f"/education/area/{area_id}", status_code=303)


@router.post("/education/area/{area_id}/delete")
def education_area_delete(area_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    area = db.query(models.LearningPathArea).filter(models.LearningPathArea.id == area_id).first()
    if area:
        db.delete(area)
        db.commit()
    return RedirectResponse("/education", status_code=303)


# ── 学習パス 一括エクスポート / インポート（データ管理ページ用） ──────────────

@router.get("/education/paths/export")
def education_paths_export(request: Request, db: Session = Depends(get_db)):
    """スキルに紐づく学習パス（ステップ）を1つのJSONファイルで一括エクスポート"""
    from fastapi.responses import Response as _Response
    import json as _json
    from datetime import datetime as _dt
    auth.require_admin(request, db)

    links = (
        db.query(models.EducationalLink)
        .filter(models.EducationalLink.skill_id.isnot(None))
        .order_by(models.EducationalLink.skill_id, models.EducationalLink.step_order)
        .all()
    )

    data = {
        "exported_at": _dt.now().isoformat(),
        "education_paths": [
            {
                "skill_name": lk.skill.name,
                "title": lk.title,
                "url": lk.url,
                "description": lk.description or "",
                "step_order": lk.step_order,
            }
            for lk in links if lk.skill is not None
        ],
    }

    body = _json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"skillmap_education_paths_{_dt.now().strftime('%Y%m%d')}.json"
    return _Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/education/paths/import")
async def education_paths_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """一括エクスポートJSONファイルから学習パスを一括インポートする
    （スキル名で照合し、同じスキル×タイトルの組み合わせは新規追加せずスキップ）"""
    import json as _json
    user = auth.require_admin(request, db)

    content = await file.read()
    try:
        data = _json.loads(content.decode("utf-8-sig"))
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON の解析に失敗しました"}, status_code=400)

    added = skipped = skipped_no_skill = 0
    for item in data.get("education_paths", []):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        skill_name = (item.get("skill_name") or "").strip()
        if not title or not url or not skill_name:
            continue

        skill = db.query(models.Skill).filter(models.Skill.name == skill_name).first()
        if not skill:
            skipped_no_skill += 1
            continue

        existing = (
            db.query(models.EducationalLink)
            .filter(
                models.EducationalLink.skill_id == skill.id,
                models.EducationalLink.title == title,
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        db.add(models.EducationalLink(
            title=title,
            url=url,
            description=(item.get("description") or "").strip() or None,
            category_id=skill.category_id,
            skill_id=skill.id,
            step_order=item.get("step_order"),
            created_by=user.id,
        ))
        added += 1

    db.commit()
    return JSONResponse({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "skipped_no_skill": skipped_no_skill,
    })
