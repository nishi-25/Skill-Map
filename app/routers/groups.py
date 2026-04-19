from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from typing import List

import models
import auth
from database import get_db
from template_engine import templates

router = APIRouter(prefix="/groups")


def _can_manage_group(user: models.User, group: models.Group) -> bool:
    """グループを編集できるか (admin か そのグループのmanager)"""
    return user.role == "admin" or (
        user.role == "manager" and group.manager_id == user.id
    )


def _get_skills_by_cat(db) -> tuple:
    """カテゴリーとスキルをまとめて取得（フォーム表示用）"""
    all_categories = db.query(models.Category).order_by(models.Category.name).all()
    all_skills = db.query(models.Skill).order_by(models.Skill.name).all()
    skills_by_cat: dict[int, list] = {}
    for sk in all_skills:
        if sk.category_id:
            skills_by_cat.setdefault(sk.category_id, []).append(sk)
    return all_categories, skills_by_cat


def _get_all_group_skill_ids(group: models.Group, visited: set | None = None) -> set[int]:
    """グループ自身 + 親グループから再帰的に継承されたスキルIDの集合を返す"""
    if visited is None:
        visited = set()
    if group.id in visited:
        return set()  # 循環防止
    visited.add(group.id)
    ids = {sk.id for sk in group.skills}
    if group.parent:
        ids |= _get_all_group_skill_ids(group.parent, visited)
    return ids


def _get_ancestor_skill_ids(group: models.Group) -> set[int]:
    """親グループ以上から継承されたスキルIDのみ返す（自身は含まない）"""
    if not group.parent:
        return set()
    return _get_all_group_skill_ids(group.parent)


def _is_descendant_of(group_id: int, potential_ancestor_id: int, db) -> bool:
    """group_id が potential_ancestor_id の子孫かチェック（循環防止用）"""
    visited = set()
    current = db.query(models.Group).filter(models.Group.id == group_id).first()
    while current and current.parent_id:
        if current.parent_id in visited:
            return False
        if current.parent_id == potential_ancestor_id:
            return True
        visited.add(current.parent_id)
        current = db.query(models.Group).filter(models.Group.id == current.parent_id).first()
    return False


# ─── 一覧 ────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def groups_list(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    if user.role == "admin":
        groups = db.query(models.Group).order_by(models.Group.name).all()
    else:
        groups = db.query(models.Group).filter(
            models.Group.manager_id == user.id
        ).order_by(models.Group.name).all()
    return templates.TemplateResponse(request, "groups.html", {
        "current_user": user, "groups": groups,
    })


# ─── 作成 ────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def group_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    managers = db.query(models.User).filter(
        models.User.role.in_(["admin", "manager"]),
        models.User.is_approved == True,
    ).all()
    all_categories, skills_by_cat = _get_skills_by_cat(db)
    all_groups = db.query(models.Group).order_by(models.Group.name).all()
    return templates.TemplateResponse(request, "group_form.html", {
        "current_user": user, "group": None,
        "managers": managers, "error": None,
        "all_categories": all_categories,
        "skills_by_cat": skills_by_cat,
        "assigned_skill_ids": set(),
        "inherited_skill_ids": set(),
        "all_groups": all_groups,
    })


@router.post("/new", response_class=HTMLResponse)
async def group_new_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    manager_id: int = Form(0),
    parent_id: int = Form(0),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    form = await request.form()
    skill_ids = [int(v) for v in form.getlist("skill_ids")]
    all_groups = db.query(models.Group).order_by(models.Group.name).all()

    if db.query(models.Group).filter(models.Group.name == name).first():
        managers = db.query(models.User).filter(
            models.User.role.in_(["admin", "manager"]),
            models.User.is_approved == True,
        ).all()
        all_categories, skills_by_cat = _get_skills_by_cat(db)
        return templates.TemplateResponse(request, "group_form.html", {
            "current_user": user, "group": None,
            "managers": managers,
            "error": "そのグループ名は既に使用されています",
            "all_categories": all_categories,
            "skills_by_cat": skills_by_cat,
            "assigned_skill_ids": set(skill_ids),
            "inherited_skill_ids": set(),
            "all_groups": all_groups,
        })
    # managerが自分で作る場合は自身をmanagerに固定
    assigned_manager = manager_id if (user.role == "admin" and manager_id) else user.id
    group = models.Group(
        name=name,
        description=description or None,
        manager_id=assigned_manager,
        parent_id=parent_id if parent_id else None,
    )
    db.add(group)
    db.flush()

    # スキル割当
    if skill_ids:
        assigned_skills = db.query(models.Skill).filter(models.Skill.id.in_(skill_ids)).all()
        group.skills = assigned_skills

    db.commit()
    return RedirectResponse("/groups", status_code=303)


# ─── 編集 ────────────────────────────────────────────────────────

@router.get("/{gid}/edit", response_class=HTMLResponse)
def group_edit_get(gid: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if not group or not _can_manage_group(user, group):
        return RedirectResponse("/groups", status_code=303)
    managers = db.query(models.User).filter(
        models.User.role.in_(["admin", "manager"]),
        models.User.is_approved == True,
    ).all()
    all_categories, skills_by_cat = _get_skills_by_cat(db)
    assigned_skill_ids = {sk.id for sk in group.skills}
    inherited_skill_ids = _get_ancestor_skill_ids(group)
    # 親候補: 自分自身と自分の子孫は除外
    all_groups = [g for g in db.query(models.Group).order_by(models.Group.name).all()
                  if g.id != gid and not _is_descendant_of(g.id, gid, db)]
    return templates.TemplateResponse(request, "group_form.html", {
        "current_user": user, "group": group,
        "managers": managers, "error": None,
        "all_categories": all_categories,
        "skills_by_cat": skills_by_cat,
        "assigned_skill_ids": assigned_skill_ids,
        "inherited_skill_ids": inherited_skill_ids,
        "all_groups": all_groups,
    })


@router.post("/{gid}/edit", response_class=HTMLResponse)
async def group_edit_post(
    gid: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    manager_id: int = Form(0),
    parent_id: int = Form(0),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if not group or not _can_manage_group(user, group):
        return RedirectResponse("/groups", status_code=303)

    form = await request.form()
    skill_ids = [int(v) for v in form.getlist("skill_ids")]

    dup = db.query(models.Group).filter(
        models.Group.name == name, models.Group.id != gid
    ).first()
    if dup:
        managers = db.query(models.User).filter(
            models.User.role.in_(["admin", "manager"]),
            models.User.is_approved == True,
        ).all()
        all_categories, skills_by_cat = _get_skills_by_cat(db)
        all_groups = [g for g in db.query(models.Group).order_by(models.Group.name).all()
                      if g.id != gid and not _is_descendant_of(g.id, gid, db)]
        return templates.TemplateResponse(request, "group_form.html", {
            "current_user": user, "group": group,
            "managers": managers,
            "error": "そのグループ名は既に使用されています",
            "all_categories": all_categories,
            "skills_by_cat": skills_by_cat,
            "assigned_skill_ids": set(skill_ids),
            "inherited_skill_ids": _get_ancestor_skill_ids(group),
            "all_groups": all_groups,
        })
    group.name = name
    group.description = description or None
    if user.role == "admin" and manager_id:
        group.manager_id = manager_id

    # 循環防止: 自分自身 or 自分の子孫を親にしない
    if parent_id and parent_id != gid and not _is_descendant_of(parent_id, gid, db):
        group.parent_id = parent_id
    else:
        group.parent_id = None

    # スキル割当の更新
    assigned_skills = db.query(models.Skill).filter(models.Skill.id.in_(skill_ids)).all() if skill_ids else []
    group.skills = assigned_skills

    db.commit()
    return RedirectResponse("/groups", status_code=303)


# ─── 削除 ────────────────────────────────────────────────────────

@router.post("/{gid}/delete")
def group_delete(gid: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if group and _can_manage_group(user, group):
        db.delete(group)
        db.commit()
    return RedirectResponse("/groups", status_code=303)


# ─── メンバー管理 ────────────────────────────────────────────────

@router.post("/{gid}/members/add")
def group_member_add(
    gid: int,
    request: Request,
    user_id: int = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if not group or not _can_manage_group(user, group):
        return RedirectResponse("/groups", status_code=303)
    exists = db.query(models.GroupMembership).filter(
        models.GroupMembership.group_id == gid,
        models.GroupMembership.user_id == user_id,
    ).first()
    if not exists:
        db.add(models.GroupMembership(group_id=gid, user_id=user_id))
        db.commit()
    return RedirectResponse(f"/groups/{gid}", status_code=303)


@router.post("/{gid}/members/{uid}/remove")
def group_member_remove(
    gid: int, uid: int, request: Request, db: Session = Depends(get_db)
):
    user = auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if not group or not _can_manage_group(user, group):
        return RedirectResponse("/groups", status_code=303)
    m = db.query(models.GroupMembership).filter(
        models.GroupMembership.group_id == gid,
        models.GroupMembership.user_id == uid,
    ).first()
    if m:
        db.delete(m)
        db.commit()
    return RedirectResponse(f"/groups/{gid}", status_code=303)


# ─── 継承スキル API ──────────────────────────────────────────────

@router.get("/api/{gid}/inherited-skills")
def inherited_skills_api(gid: int, request: Request, db: Session = Depends(get_db)):
    """指定グループとその祖先の全スキルIDを返す（フォームの動的更新用）"""
    from fastapi.responses import JSONResponse
    auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if not group:
        return JSONResponse({"skill_ids": []})
    all_ids = list(_get_all_group_skill_ids(group))
    return JSONResponse({"skill_ids": all_ids})


# ─── メンバー異動 ────────────────────────────────────────────────

@router.post("/{gid}/members/{uid}/transfer")
def group_member_transfer(
    gid: int,
    uid: int,
    request: Request,
    to_group_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """メンバーを別のグループに異動（旧グループ削除→新グループ追加→履歴記録）"""
    user = auth.require_manager_or_admin(request, db)
    from_group = db.query(models.Group).filter(models.Group.id == gid).first()
    to_group = db.query(models.Group).filter(models.Group.id == to_group_id).first()
    if not from_group or not to_group or not _can_manage_group(user, from_group):
        return RedirectResponse(f"/groups/{gid}", status_code=303)
    if gid == to_group_id:
        return RedirectResponse(f"/groups/{gid}", status_code=303)

    # 旧グループから削除
    old_membership = db.query(models.GroupMembership).filter(
        models.GroupMembership.group_id == gid,
        models.GroupMembership.user_id == uid,
    ).first()
    if old_membership:
        db.delete(old_membership)

    # 新グループに追加（重複チェック）
    existing = db.query(models.GroupMembership).filter(
        models.GroupMembership.group_id == to_group_id,
        models.GroupMembership.user_id == uid,
    ).first()
    if not existing:
        db.add(models.GroupMembership(group_id=to_group_id, user_id=uid))

    # 異動履歴を記録
    db.add(models.GroupTransfer(
        user_id=uid,
        from_group_id=gid,
        to_group_id=to_group_id,
        transferred_by=user.id,
    ))
    db.commit()
    return RedirectResponse(f"/groups/{gid}", status_code=303)


# ─── グループ詳細（スキルマップ参照） ───────────────────────────

@router.get("/{gid}", response_class=HTMLResponse)
def group_detail(
    gid: int,
    request: Request,
    category_id: int = 0,
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == gid).first()
    if not group:
        return RedirectResponse("/groups", status_code=303)
    # admin か このグループのmanagerのみ閲覧可
    if not _can_manage_group(user, group):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="このグループの閲覧権限がありません")

    members = [m.user for m in group.memberships]
    member_ids = [m.user_id for m in group.memberships]

    # メンバーのスキルレベル
    all_levels = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id.in_(member_ids),
            models.UserSkillLevel.approval_status == "approved",
        )
        .all()
    ) if member_ids else []

    if category_id:
        all_levels = [sl for sl in all_levels if sl.skill.category_id == category_id]

    # メンバーごとにスキルレベルをまとめる (level > 0 のみ表示)
    skills_by_user: dict[int, list] = {m.id: [] for m in members}
    for sl in all_levels:
        if sl.user_id in skills_by_user and sl.level > 0:
            skills_by_user[sl.user_id].append(sl)

    categories = db.query(models.Category).order_by(models.Category.name).all()

    # 追加できるユーザー（既にメンバーでない承認済みuserのみ）
    addable = db.query(models.User).filter(
        models.User.is_approved == True,
        models.User.role == "user",
        ~models.User.id.in_(member_ids),
    ).all() if _can_manage_group(user, group) else []

    # レーダーチャート用: メンバーごとのカテゴリー別平均レベル
    radar_data = {}
    cat_names = [c.name for c in categories]
    for m in members:
        cat_avg = {}
        for c in categories:
            cat_levels = [sl.level for sl in skills_by_user[m.id] if sl.skill.category and sl.skill.category.name == c.name]
            cat_avg[c.name] = round(sum(cat_levels) / len(cat_levels), 1) if cat_levels else 0
        radar_data[m.id] = cat_avg

    # 異動先グループ候補（自グループ以外）
    other_groups = db.query(models.Group).filter(
        models.Group.id != gid
    ).order_by(models.Group.name).all()

    # 異動履歴（このグループに関連するもの）
    transfer_history = (
        db.query(models.GroupTransfer)
        .filter(
            (models.GroupTransfer.from_group_id == gid) |
            (models.GroupTransfer.to_group_id == gid)
        )
        .order_by(models.GroupTransfer.transferred_at.desc())
        .limit(30)
        .all()
    )

    return templates.TemplateResponse(request, "group_detail.html", {
        "current_user": user, "group": group,
        "members": members, "skills_by_user": skills_by_user,
        "categories": categories, "sel_category": category_id,
        "addable": addable,
        "radar_data": radar_data,
        "cat_names": cat_names,
        "other_groups": other_groups,
        "transfer_history": transfer_history,
    })
