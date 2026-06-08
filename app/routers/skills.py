from typing import List, Optional
import csv
import io
import os
from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from collections import defaultdict

import models
import auth
from database import get_db
from template_engine import templates
from routers.groups import _get_all_group_skill_ids

router = APIRouter()


# ════════════════════════════════════════════════════════════════
# カテゴリー管理（Admin / Manager のみ）
# ════════════════════════════════════════════════════════════════

@router.get("/categories", response_class=HTMLResponse)
def categories_list(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)

    if user.role == "manager":
        # Managerは自グループのスキルが属するカテゴリのみ表示
        skill_ids = _get_manager_skill_ids(user, db)
        if skill_ids:
            cat_ids = {s.category_id for s in
                       db.query(models.Skill).filter(
                           models.Skill.id.in_(skill_ids),
                           models.Skill.category_id.isnot(None)
                       ).all()}
            cats = db.query(models.Category).filter(
                models.Category.id.in_(cat_ids)
            ).order_by(models.Category.name).all()
        else:
            cats = []
    else:
        cats = db.query(models.Category).order_by(models.Category.name).all()

    return templates.TemplateResponse(request, "categories.html", {
        "current_user": user, "categories": cats
    })


@router.get("/categories/new", response_class=HTMLResponse)
def category_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    return templates.TemplateResponse(request, "category_form.html", {
        "current_user": user, "category": None, "error": None
    })


@router.post("/categories/new")
def category_new_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form("#6366f1"),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    if db.query(models.Category).filter(models.Category.name == name).first():
        return templates.TemplateResponse(request, "category_form.html", {
            "current_user": user, "category": None,
            "error": "そのカテゴリー名は既に使用されています"
        })
    db.add(models.Category(
        name=name, description=description or None,
        color=color, created_by=user.id
    ))
    db.commit()
    return RedirectResponse("/categories", status_code=303)


@router.get("/categories/{cat_id}/edit", response_class=HTMLResponse)
def category_edit_get(cat_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    cat = db.query(models.Category).filter(models.Category.id == cat_id).first()
    if not cat:
        return RedirectResponse("/categories", status_code=303)
    return templates.TemplateResponse(request, "category_form.html", {
        "current_user": user, "category": cat, "error": None
    })


@router.post("/categories/{cat_id}/edit")
def category_edit_post(
    cat_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form("#6366f1"),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    cat = db.query(models.Category).filter(models.Category.id == cat_id).first()
    if not cat:
        return RedirectResponse("/categories", status_code=303)
    dup = db.query(models.Category).filter(
        models.Category.name == name, models.Category.id != cat_id
    ).first()
    if dup:
        return templates.TemplateResponse(request, "category_form.html", {
            "current_user": user, "category": cat,
            "error": "そのカテゴリー名は既に使用されています"
        })
    cat.name = name
    cat.description = description or None
    cat.color = color
    db.commit()
    return RedirectResponse("/categories", status_code=303)


@router.post("/categories/{cat_id}/delete")
def category_delete(cat_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    cat = db.query(models.Category).filter(models.Category.id == cat_id).first()
    if cat:
        db.query(models.Skill).filter(models.Skill.category_id == cat_id).update(
            {"category_id": None}
        )
        db.delete(cat)
        db.commit()
    return RedirectResponse("/categories", status_code=303)


@router.post("/categories/bulk-delete")
def categories_bulk_delete(
    request: Request,
    cat_ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """複数カテゴリーを一括削除（紐づくスキルは未分類になる）"""
    user = auth.require_manager_or_admin(request, db)
    if cat_ids:
        db.query(models.Skill).filter(models.Skill.category_id.in_(cat_ids)).update(
            {"category_id": None}, synchronize_session=False
        )
        db.query(models.Category).filter(models.Category.id.in_(cat_ids)).delete(
            synchronize_session=False
        )
        db.commit()
    return RedirectResponse("/categories", status_code=303)


# ════════════════════════════════════════════════════════════════
# スキルカタログ管理（Admin / Manager のみ）
# ════════════════════════════════════════════════════════════════

def _get_manager_skill_ids(user, db) -> set:
    """Managerが管理するグループに割り当てられた全スキルIDを返す"""
    from sqlalchemy import text
    rows = db.execute(
        text("SELECT DISTINCT group_id FROM group_managers WHERE user_id = :uid"),
        {"uid": user.id}
    ).fetchall()
    gm_ids = {r[0] for r in rows}
    primary_ids = {g.id for g in db.query(models.Group).filter(
        models.Group.manager_id == user.id
    ).all()}
    all_group_ids = gm_ids | primary_ids
    if not all_group_ids:
        return set()
    groups = db.query(models.Group).filter(models.Group.id.in_(all_group_ids)).all()
    skill_ids: set = set()
    for g in groups:
        skill_ids |= _get_all_group_skill_ids(g)
    return skill_ids


@router.get("/skills/catalog", response_class=HTMLResponse)
def catalog_list(
    request: Request,
    category_id: int = 0,
    tier: str = "",
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.Skill)

    if user.role == "manager":
        # Managerは自グループに割り当てられたスキルのみ表示
        skill_ids = _get_manager_skill_ids(user, db)
        q = q.filter(models.Skill.id.in_(skill_ids)) if skill_ids else q.filter(models.Skill.id.in_([]))

    if category_id:
        q = q.filter(models.Skill.category_id == category_id)
    if tier:
        q = q.filter(models.Skill.tier == tier)

    from sqlalchemy import case as _case
    _tier_order = _case(
        (models.Skill.tier == "basic",        0),
        (models.Skill.tier == "intermediate", 1),
        (models.Skill.tier == "advanced",     2),
        else_=9,
    )
    skills = (
        q.outerjoin(models.Category, models.Skill.category_id == models.Category.id)
         .order_by(models.Category.name.nullslast(), _tier_order, models.Skill.name)
         .all()
    )

    # カテゴリもManagerのスコープに絞る
    if user.role == "manager":
        skill_ids_all = _get_manager_skill_ids(user, db)
        cat_ids = {s.category_id for s in skills if s.category_id}
        categories = db.query(models.Category).filter(
            models.Category.id.in_(cat_ids)
        ).order_by(models.Category.name).all()
    else:
        categories = db.query(models.Category).order_by(models.Category.name).all()

    all_tags = db.query(models.SkillTag).order_by(models.SkillTag.name).all()
    return templates.TemplateResponse(request, "skill_catalog.html", {
        "current_user": user, "skills": skills,
        "categories": categories,
        "sel_category": category_id, "sel_tier": tier,
        "all_tags": all_tags,
        "highlight": request.query_params.get("highlight", ""),
    })


@router.get("/skills/catalog/new", response_class=HTMLResponse)
def catalog_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    all_tags = db.query(models.SkillTag).order_by(models.SkillTag.name).all()
    return templates.TemplateResponse(request, "skill_catalog_form.html", {
        "current_user": user, "skill": None,
        "categories": categories, "error": None,
        "all_tags": all_tags,
    })


@router.post("/skills/catalog/new")
def catalog_new_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(0),
    tier: str = Form("basic"),
    tag_ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    skill = models.Skill(
        name=name,
        description=description or None,
        category_id=category_id or None,
        tier=tier,
        created_by=user.id,
    )
    db.add(skill)
    db.flush()
    if tag_ids:
        skill.tags = db.query(models.SkillTag).filter(models.SkillTag.id.in_(tag_ids)).all()
    db.commit()
    return RedirectResponse("/skills/catalog", status_code=303)


@router.get("/skills/catalog/{skill_id}/edit", response_class=HTMLResponse)
def catalog_edit_get(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/skills/catalog", status_code=303)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    all_tags = db.query(models.SkillTag).order_by(models.SkillTag.name).all()
    return templates.TemplateResponse(request, "skill_catalog_form.html", {
        "current_user": user, "skill": skill,
        "categories": categories, "error": None,
        "all_tags": all_tags,
    })


@router.post("/skills/catalog/{skill_id}/edit")
def catalog_edit_post(
    skill_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(0),
    tier: str = Form("basic"),
    tag_ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/skills/catalog", status_code=303)
    skill.name = name
    skill.description = description or None
    skill.category_id = category_id or None
    skill.tier = tier
    # タグの更新
    if tag_ids:
        skill.tags = db.query(models.SkillTag).filter(models.SkillTag.id.in_(tag_ids)).all()
    else:
        skill.tags = []
    db.commit()
    return RedirectResponse("/skills/catalog", status_code=303)


@router.post("/skills/catalog/{skill_id}/delete")
def catalog_delete(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if skill:
        # 関連データを明示的に削除（ORM cascade が効かないテーブルに対応）
        db.query(models.UserSkillLevel).filter(models.UserSkillLevel.skill_id == skill_id).delete()
        db.query(models.SkillLevelHistory).filter(models.SkillLevelHistory.skill_id == skill_id).delete()
        db.query(models.SkillEvidence).filter(models.SkillEvidence.skill_id == skill_id).delete()
        db.query(models.SkillGoal).filter(models.SkillGoal.skill_id == skill_id).delete()
        # sub_skills は cascade="all, delete-orphan" で自動削除されるが念のため
        db.query(models.SubSkill).filter(models.SubSkill.skill_id == skill_id).delete()
        db.delete(skill)
        db.commit()
    return RedirectResponse("/skills/catalog", status_code=303)


# ════════════════════════════════════════════════════════════════
# ティア名カスタマイズ（Admin / Manager のみ）
# ════════════════════════════════════════════════════════════════

@router.get("/skills/tier-settings", response_class=HTMLResponse)
def tier_settings_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    tier_names = models.get_tier_display_names(db)
    tier_order = ["basic", "intermediate", "advanced"]
    return templates.TemplateResponse(request, "tier_settings.html", {
        "current_user": user,
        "tier_names": tier_names,
        "tier_order": tier_order,
        "TIER_ICONS": models.TIER_ICONS,
        "TIER_COLORS": models.TIER_COLORS,
        "success": False,
    })


@router.post("/skills/tier-settings")
def tier_settings_post(
    request: Request,
    tier_basic: str = Form(""),
    tier_intermediate: str = Form(""),
    tier_advanced: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    mapping = {
        "basic": tier_basic.strip(),
        "intermediate": tier_intermediate.strip(),
        "advanced": tier_advanced.strip(),
    }
    for key, value in mapping.items():
        db_key = f"tier_name_{key}"
        setting = db.query(models.AppSetting).filter(
            models.AppSetting.key == db_key
        ).first()
        name = value or models.DEFAULT_TIER_NAMES[key]
        if setting:
            setting.value = name
        else:
            db.add(models.AppSetting(key=db_key, value=name))
    db.commit()

    tier_names = models.get_tier_display_names(db)
    tier_order = ["basic", "intermediate", "advanced"]
    return templates.TemplateResponse(request, "tier_settings.html", {
        "current_user": user,
        "tier_names": tier_names,
        "tier_order": tier_order,
        "TIER_ICONS": models.TIER_ICONS,
        "TIER_COLORS": models.TIER_COLORS,
        "success": True,
    })


# ════════════════════════════════════════════════════════════════
# ユーザーのスキルレベル自己申告（全承認済みユーザー）
# ════════════════════════════════════════════════════════════════

@router.get("/skills", response_class=HTMLResponse)
def skills_my(
    request: Request,
    category_id: int = 0,
    tier: str = "",
    group_id: int = 0,
    view: str = "",
    view_as: int = 0,
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)

    # ── 代理閲覧/編集: Admin は全員、Manager は自分の担当グループメンバー ──
    target_user = user  # デフォルトは自分
    editable_users: list = []  # 選択可能なユーザー一覧（Admin/Manager用）
    if user.role == "admin":
        editable_users = (
            db.query(models.User)
            .filter(models.User.is_approved == True, models.User.id != user.id,
                    models.User.role == "user")
            .order_by(models.User.display_name, models.User.username).all()
        )
        if view_as:
            t = db.query(models.User).filter(models.User.id == view_as).first()
            if t:
                target_user = t
    elif user.role == "manager":
        # 自分が管理するグループのメンバーのみ
        from sqlalchemy import text as _text_va
        member_ids: set[int] = set()
        for row in db.execute(
            _text_va("SELECT DISTINCT gm.user_id FROM group_memberships gm "
                     "JOIN group_managers mgr ON gm.group_id=mgr.group_id "
                     "WHERE mgr.user_id=:uid"), {"uid": user.id}
        ).fetchall():
            member_ids.add(row[0])
        for row in db.execute(
            _text_va("SELECT DISTINCT gm.user_id FROM group_memberships gm "
                     "JOIN groups g ON gm.group_id=g.id "
                     "WHERE g.manager_id=:uid"), {"uid": user.id}
        ).fetchall():
            member_ids.add(row[0])
        member_ids.discard(user.id)
        if member_ids:
            editable_users = (
                db.query(models.User)
                .filter(models.User.id.in_(member_ids), models.User.is_approved == True)
                .order_by(models.User.display_name, models.User.username).all()
            )
        if view_as and view_as in member_ids:
            t = db.query(models.User).filter(models.User.id == view_as).first()
            if t:
                target_user = t

    # カスタムティア名
    tier_names = models.get_tier_display_names(db)
    tier_order = ["basic", "intermediate", "advanced"]

    # 対象ユーザーの所属グループ
    my_groups = (
        db.query(models.Group)
        .join(models.GroupMembership)
        .filter(models.GroupMembership.user_id == target_user.id)
        .order_by(models.Group.name)
        .all()
    )

    # グループでスキル絞込み
    group_skill_ids = None
    if group_id:
        sel_group = db.query(models.Group).filter(models.Group.id == group_id).first()
        if sel_group:
            group_skill_ids = _get_all_group_skill_ids(sel_group)
    elif target_user.role == "user":
        # User ロールは所属グループのスキルのみ自動表示
        if my_groups:
            auto_ids: set[int] = set()
            for grp in my_groups:
                auto_ids.update(_get_all_group_skill_ids(grp))
            group_skill_ids = auto_ids
        else:
            group_skill_ids = set()

    # 全カタログ取得（tierフィルタなし → 概要計算用）
    q_all = db.query(models.Skill)
    if category_id:
        q_all = q_all.filter(models.Skill.category_id == category_id)
    all_catalog = q_all.order_by(models.Skill.tier, models.Skill.name).all()
    if group_skill_ids is not None:
        all_catalog = [sk for sk in all_catalog if sk.id in group_skill_ids]

    # 対象ユーザーのスキルレベル（全ステータス）
    my_levels = (
        db.query(models.UserSkillLevel)
        .filter(models.UserSkillLevel.user_id == target_user.id)
        .all()
    )
    # レベル表示・集計は承認済みのみ
    my_level_map: dict[int, int] = {
        sl.skill_id: sl.level for sl in my_levels if sl.approval_status == "approved"
    }
    # 申請中のレベル（ドット横に「申請中 〇〇」と表示するため）
    my_pending_level_map: dict[int, int] = {
        sl.skill_id: sl.level for sl in my_levels if sl.approval_status == "pending"
    }
    # ステータス列・ドット色は全ステータスを参照
    my_approval_map: dict[int, str] = {
        sl.skill_id: sl.approval_status for sl in my_levels
    }
    my_approver_map: dict[int, int] = {
        sl.skill_id: sl.approver_id for sl in my_levels if sl.approver_id
    }

    # ── ティア概要（overview_mode） ──
    overview_mode = not tier and view != "all"
    tier_summary = {}
    for t_key in tier_order:
        t_skills = [s for s in all_catalog if s.tier == t_key]
        t_acquired = sum(1 for s in t_skills if my_level_map.get(s.id, 0) > 0)
        tier_summary[t_key] = {"total": len(t_skills), "acquired": t_acquired}

    # tierが選択されている場合のみ詳細スキル一覧
    if tier:
        catalog = [sk for sk in all_catalog if sk.tier == tier]
    else:
        catalog = all_catalog

    categories = db.query(models.Category).order_by(models.Category.name).all()

    # 承認者候補: 自分が所属するグループの担当 Manager のみ
    # グループ未所属の場合は Manager/Admin 全員を fallback として表示
    from sqlalchemy import select as _select

    memberships = (
        db.query(models.GroupMembership)
        .filter(models.GroupMembership.user_id == target_user.id)
        .all()
    )
    if memberships:
        group_ids = [m.group_id for m in memberships]
        # group_managers テーブルから co-manager を ORM で取得（SQLite 互換）
        co_mgr_rows = db.execute(
            _select(models.group_managers.c.user_id)
            .where(models.group_managers.c.group_id.in_(group_ids))
            .distinct()
        ).fetchall()
        co_mgr_ids = {r[0] for r in co_mgr_rows}
        # primary manager_id も加える
        primary_mgr_ids = {
            g.manager_id
            for g in db.query(models.Group).filter(models.Group.id.in_(group_ids)).all()
            if g.manager_id
        }
        approver_ids = (co_mgr_ids | primary_mgr_ids) - {user.id}

        if approver_ids:
            approvers = (
                db.query(models.User)
                .filter(
                    models.User.id.in_(approver_ids),
                    models.User.is_approved == True,
                )
                .order_by(models.User.display_name, models.User.username)
                .all()
            )
        else:
            # 担当 Manager が見つからない場合は Manager/Admin 全員
            approvers = (
                db.query(models.User)
                .filter(
                    models.User.is_approved == True,
                    models.User.role.in_(["manager", "admin"]),
                    models.User.id != user.id,
                )
                .order_by(models.User.display_name, models.User.username)
                .all()
            )
    else:
        # グループ未所属: Manager/Admin 全員を fallback
        approvers = (
            db.query(models.User)
            .filter(
                models.User.is_approved == True,
                models.User.role.in_(["manager", "admin"]),
                models.User.id != user.id,
            )
            .order_by(models.User.display_name, models.User.username)
            .all()
        )

    by_tier: dict[str, list] = defaultdict(list)
    for sk in catalog:
        by_tier[sk.tier].append(sk)

    # ── カテゴリ別スキルマップ（新UI用） ──
    from collections import OrderedDict

    # 表示するスキルのID一覧
    displayed_skill_ids = [sk.id for sk in catalog]

    # 全スキルのサブスキルを一括取得（skill_id → [SubSkill]マップ）
    sub_skills_map: dict[int, list] = defaultdict(list)
    if displayed_skill_ids:
        all_subs = (
            db.query(models.SubSkill)
            .filter(models.SubSkill.skill_id.in_(displayed_skill_ids))
            .order_by(models.SubSkill.skill_id, models.SubSkill.order_index)
            .all()
        )
        for ss in all_subs:
            sub_skills_map[ss.skill_id].append(ss)

    # ユーザーが「できる」にしているサブスキルIDのセット（一括取得）
    done_sub_ids: set = set()
    if displayed_skill_ids:
        done_records = (
            db.query(models.UserSubSkillLevel)
            .filter(
                models.UserSubSkillLevel.user_id == target_user.id,
                models.UserSubSkillLevel.can_do == True,
            )
            .all()
        )
        done_sub_ids = {r.sub_skill_id for r in done_records}

    # 各スキルの現在の自動計算レベル
    auto_level_map: dict[int, int] = {}
    for skill_id, subs in sub_skills_map.items():
        done = sum(1 for ss in subs if ss.id in done_sub_ids)
        auto_level_map[skill_id] = calc_level_from_ratio(done, len(subs))

    # ── 希少性スコア: 各スキルを何人が申告済みか（チーム内） ──
    # 対象: 全承認済みユーザーの承認済み申告
    skill_holder_map: dict[int, int] = {}  # skill_id → 人数
    if displayed_skill_ids:
        from sqlalchemy import func as _func
        rows = (
            db.query(models.UserSkillLevel.skill_id, _func.count(models.UserSkillLevel.user_id))
            .filter(
                models.UserSkillLevel.skill_id.in_(displayed_skill_ids),
                models.UserSkillLevel.approval_status == "approved",
                models.UserSkillLevel.level > 0,
            )
            .group_by(models.UserSkillLevel.skill_id)
            .all()
        )
        skill_holder_map = {skill_id: cnt for skill_id, cnt in rows}

    # ── おすすめスキル: グループでよく申告されている／自分の既得カテゴリと関連 ──
    recommended_skills: list = []
    if target_user.role == "user":
        from sqlalchemy import func as _rec_func
        acquired_ids = {sid for sid, lv in my_level_map.items() if lv > 0}
        excluded_ids = acquired_ids | set(my_pending_level_map.keys())

        group_member_ids: set[int] = set()
        if my_groups:
            rows_gm = db.execute(
                _select(models.GroupMembership.user_id)
                .where(models.GroupMembership.group_id.in_([g.id for g in my_groups]))
                .distinct()
            ).fetchall()
            group_member_ids = {r[0] for r in rows_gm} - {target_user.id}

        group_holder_map: dict[int, int] = {}
        if group_member_ids:
            rows_gh = (
                db.query(models.UserSkillLevel.skill_id, _rec_func.count(models.UserSkillLevel.user_id))
                .filter(
                    models.UserSkillLevel.user_id.in_(group_member_ids),
                    models.UserSkillLevel.approval_status == "approved",
                    models.UserSkillLevel.level > 0,
                )
                .group_by(models.UserSkillLevel.skill_id)
                .all()
            )
            group_holder_map = {sid: cnt for sid, cnt in rows_gh}

        acquired_cat_ids = {
            sk.category_id for sk in all_catalog
            if sk.id in acquired_ids and sk.category_id
        }

        candidates = []
        for sk in all_catalog:
            if sk.id in excluded_ids or sk.is_archived:
                continue
            group_count = group_holder_map.get(sk.id, 0)
            related = sk.category_id in acquired_cat_ids
            score = group_count * 2 + (1 if related else 0)
            if score > 0:
                candidates.append((score, group_count, related, sk))

        candidates.sort(key=lambda c: (-c[0], -c[1], c[3].name))
        recommended_skills = [
            {"skill": sk, "group_count": gc, "related": rel}
            for score, gc, rel, sk in candidates[:8]
        ]

    # カテゴリ別スキルのOrderedDict
    skills_by_category: OrderedDict = OrderedDict()
    for sk in catalog:
        cat_name = sk.category.name if sk.category else "未分類"
        if cat_name not in skills_by_category:
            skills_by_category[cat_name] = {
                "category": sk.category,
                "skills": []
            }
        skills_by_category[cat_name]["skills"].append(sk)

    return templates.TemplateResponse(request, "skills.html", {
        "current_user": user,
        "by_tier": by_tier,
        "tier_order": tier_order,
        "my_level_map": my_level_map,
        "my_pending_level_map": my_pending_level_map,
        "my_approval_map": my_approval_map,
        "my_approver_map": my_approver_map,
        "approvers": approvers,
        "categories": categories,
        "sel_category": category_id,
        "sel_tier": tier,
        "total_catalog": len(all_catalog),
        "total_set": sum(1 for sk in all_catalog if my_level_map.get(sk.id, 0) > 0),
        "my_groups": my_groups,
        "sel_group": group_id,
        "overview_mode": overview_mode,
        "tier_summary": tier_summary,
        "TIER_NAMES": tier_names,
        "TIER_ICONS": models.TIER_ICONS,
        "TIER_DESCRIPTIONS": models.TIER_DESCRIPTIONS,
        "TIER_COLORS": models.TIER_COLORS,
        "SKILL_LEVELS": models.SKILL_LEVELS,
        "LEVEL_COLORS": models.LEVEL_COLORS,
        "APPROVAL_STATUS": models.APPROVAL_STATUS,
        "APPROVAL_STATUS_COLORS": models.APPROVAL_STATUS_COLORS,
        "view": view,
        # 新UI用
        "sub_skills_map": dict(sub_skills_map),
        "done_sub_ids": done_sub_ids,
        "auto_level_map": auto_level_map,
        "skills_by_category": skills_by_category,
        "skill_holder_map": skill_holder_map,
        "recommended_skills": recommended_skills,
        # 代理閲覧/編集
        "target_user": target_user,
        "view_as": target_user.id if target_user.id != user.id else 0,
        "editable_users": editable_users,
    })


@router.post("/skills/{skill_id}/level")
def set_skill_level(
    skill_id: int,
    request: Request,
    level: int = Form(...),
    approver_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """ユーザーが自分のスキルレベルを登録・更新する"""
    user = auth.require_approved(request, db)
    level = max(0, min(4, level))

    # Admin/Manager は自動承認
    is_auto_approve = user.role in ("admin", "manager")

    if not is_auto_approve:
        # 一般ユーザーは承認者必須
        approver = db.query(models.User).filter(
            models.User.id == approver_id,
            models.User.is_approved == True,
            models.User.id != user.id,
        ).first()
        if not approver:
            referer = request.headers.get("referer", "/skills")
            return RedirectResponse(referer, status_code=303)

    existing = (db.query(models.UserSkillLevel)
                .filter(
                    models.UserSkillLevel.user_id == user.id,
                    models.UserSkillLevel.skill_id == skill_id,
                ).first())

    if is_auto_approve:
        # 承認前のレベルを取得（履歴用）
        prev_history = (
            db.query(models.SkillLevelHistory)
            .filter(
                models.SkillLevelHistory.user_id == user.id,
                models.SkillLevelHistory.skill_id == skill_id,
            )
            .order_by(models.SkillLevelHistory.changed_at.desc())
            .first()
        )
        previous_level = prev_history.level if prev_history else 0

        if existing:
            existing.level = level
            existing.approver_id = None
            existing.approval_status = "approved"
            existing.approved_at = func.now()
            existing.approver_comment = "自動承認"
        else:
            db.add(models.UserSkillLevel(
                user_id=user.id, skill_id=skill_id, level=level,
                approver_id=None, approval_status="approved",
                approved_at=func.now(), approver_comment="自動承認",
            ))

        # 履歴を記録
        db.add(models.SkillLevelHistory(
            user_id=user.id,
            skill_id=skill_id,
            level=level,
            previous_level=previous_level,
            approved_by=user.id,
        ))
    else:
        if existing:
            existing.level = level
            existing.approver_id = approver_id
            existing.approval_status = "pending"
            existing.approved_at = None
            existing.approver_comment = None
        else:
            db.add(models.UserSkillLevel(
                user_id=user.id, skill_id=skill_id, level=level,
                approver_id=approver_id, approval_status="pending",
            ))

    db.commit()
    referer = request.headers.get("referer", "/skills")
    return RedirectResponse(referer, status_code=303)


# ════════════════════════════════════════════════════════════════
# 承認ワークフロー
# ════════════════════════════════════════════════════════════════

@router.get("/approvals", response_class=HTMLResponse)
def approvals_list(request: Request, db: Session = Depends(get_db)):
    """承認依頼一覧。Admin は全ユーザーの全申告を確認可能、Manager は担当分のみ。"""
    user = auth.require_manager_or_admin(request, db)

    if user.role == "admin":
        # Admin: 全ユーザーの全申告を表示
        pending = (
            db.query(models.UserSkillLevel)
            .filter(models.UserSkillLevel.approval_status == "pending")
            .order_by(models.UserSkillLevel.updated_at.desc())
            .all()
        )
        history = (
            db.query(models.UserSkillLevel)
            .filter(models.UserSkillLevel.approval_status.in_(["approved", "rejected"]))
            .order_by(models.UserSkillLevel.approved_at.desc())
            .limit(200)
            .all()
        )
    else:
        # Manager: 自分が承認者に指定されたもののみ
        pending = (
            db.query(models.UserSkillLevel)
            .filter(
                models.UserSkillLevel.approver_id == user.id,
                models.UserSkillLevel.approval_status == "pending",
            )
            .all()
        )
        history = (
            db.query(models.UserSkillLevel)
            .filter(
                models.UserSkillLevel.approver_id == user.id,
                models.UserSkillLevel.approval_status.in_(["approved", "rejected"]),
            )
            .order_by(models.UserSkillLevel.approved_at.desc())
            .limit(50)
            .all()
        )
    # pending + history 両方のエビデンスを一括取得 (user_id, skill_id) -> list
    all_records = list(pending) + list(history)
    evidence_map: dict[tuple, list] = {}
    if all_records:
        all_ev = db.query(models.SkillEvidence).filter(
            models.SkillEvidence.user_id.in_([r.user_id for r in all_records]),
            models.SkillEvidence.skill_id.in_([r.skill_id for r in all_records]),
        ).order_by(models.SkillEvidence.created_at).all()
        for ev in all_ev:
            key = (ev.user_id, ev.skill_id)
            evidence_map.setdefault(key, []).append(ev)

    return templates.TemplateResponse(request, "approvals.html", {
        "current_user": user,
        "pending": pending,
        "history": history,
        "is_admin_all_view": user.role == "admin",
        "evidence_map": evidence_map,
        "SKILL_LEVELS": models.SKILL_LEVELS,
        "LEVEL_COLORS": models.LEVEL_COLORS,
        "APPROVAL_STATUS": models.APPROVAL_STATUS,
        "APPROVAL_STATUS_COLORS": models.APPROVAL_STATUS_COLORS,
    })


@router.post("/approvals/{record_id}/approve")
def approve_skill(
    record_id: int,
    request: Request,
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    """スキルレベルを承認する。Admin は承認者に関係なく全申告を承認可能。"""
    user = auth.require_manager_or_admin(request, db)
    _q = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id == record_id,
        models.UserSkillLevel.approval_status == "pending",
    )
    if user.role != "admin":
        _q = _q.filter(models.UserSkillLevel.approver_id == user.id)
    record = _q.first()
    if record:
        # 承認前のレベルを取得（履歴用）
        prev_history = (
            db.query(models.SkillLevelHistory)
            .filter(
                models.SkillLevelHistory.user_id == record.user_id,
                models.SkillLevelHistory.skill_id == record.skill_id,
            )
            .order_by(models.SkillLevelHistory.changed_at.desc())
            .first()
        )
        previous_level = prev_history.level if prev_history else 0

        record.approval_status = "approved"
        record.approved_at = func.now()
        record.approver_comment = comment or None

        # 承認履歴を記録
        db.add(models.SkillLevelHistory(
            user_id=record.user_id,
            skill_id=record.skill_id,
            level=record.level,
            previous_level=previous_level,
            approved_by=user.id,
        ))
        db.commit()
    return RedirectResponse("/approvals", status_code=303)


@router.post("/approvals/{record_id}/reject")
def reject_skill(
    record_id: int,
    request: Request,
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    """スキルレベルを差し戻す。Admin は承認者に関係なく全申告を差し戻し可能。"""
    user = auth.require_manager_or_admin(request, db)
    _rq = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id == record_id,
        models.UserSkillLevel.approval_status == "pending",
    )
    if user.role != "admin":
        _rq = _rq.filter(models.UserSkillLevel.approver_id == user.id)
    record = _rq.first()
    if record:
        record.approval_status = "rejected"
        record.approved_at = func.now()
        record.approver_comment = comment or None
        db.commit()
    return RedirectResponse("/approvals", status_code=303)


@router.post("/api/approvals/{record_id}/revoke")
def revoke_approval(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """承認済み・差し戻し済みレコードを pending に戻す（Admin/Manager）"""
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id == record_id,
        models.UserSkillLevel.approval_status.in_(["approved", "rejected"]),
    )
    if user.role != "admin":
        q = q.filter(models.UserSkillLevel.approver_id == user.id)
    record = q.first()
    if not record:
        return JSONResponse({"ok": False, "error": "対象レコードが見つかりません"}, status_code=404)
    record.approval_status = "pending"
    record.approved_at = None
    record.approver_comment = None
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/approvals/{record_id}/edit")
def edit_approval(
    record_id: int,
    request: Request,
    level: int = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    """承認済みレコードのレベル・コメントを編集する（Admin/Manager）"""
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id == record_id,
        models.UserSkillLevel.approval_status.in_(["approved", "rejected"]),
    )
    if user.role != "admin":
        q = q.filter(models.UserSkillLevel.approver_id == user.id)
    record = q.first()
    if not record:
        return JSONResponse({"ok": False, "error": "対象レコードが見つかりません"}, status_code=404)
    if level not in models.SKILL_LEVELS:
        return JSONResponse({"ok": False, "error": "無効なレベル"}, status_code=400)
    record.level = level
    record.approver_comment = comment.strip() or None
    record.approved_at = func.now()
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/approvals/bulk-action")
def bulk_approval_action(
    request: Request,
    action: str = Form(...),
    record_ids: List[int] = Form(default=[]),
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    """一括承認・一括差し戻し（Admin/Managerのみ）"""
    user = auth.require_manager_or_admin(request, db)
    if action not in ("approve", "reject") or not record_ids:
        return JSONResponse({"ok": False, "error": "無効なリクエスト"}, status_code=400)

    q = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id.in_(record_ids),
        models.UserSkillLevel.approval_status == "pending",
    )
    # Admin は全件対象、Manager は自分が承認者のもののみ
    if user.role != "admin":
        q = q.filter(models.UserSkillLevel.approver_id == user.id)
    records = q.all()

    processed = 0
    for record in records:
        if action == "approve":
            prev_history = (
                db.query(models.SkillLevelHistory)
                .filter(
                    models.SkillLevelHistory.user_id == record.user_id,
                    models.SkillLevelHistory.skill_id == record.skill_id,
                )
                .order_by(models.SkillLevelHistory.changed_at.desc())
                .first()
            )
            previous_level = prev_history.level if prev_history else 0
            record.approval_status = "approved"
            record.approved_at = func.now()
            record.approver_comment = comment or None
            db.add(models.SkillLevelHistory(
                user_id=record.user_id,
                skill_id=record.skill_id,
                level=record.level,
                previous_level=previous_level,
                approved_by=user.id,
            ))
        else:
            record.approval_status = "rejected"
            record.approved_at = func.now()
            record.approver_comment = comment or None
        processed += 1

    db.commit()
    return JSONResponse({"ok": True, "processed": processed})


@router.get("/api/approvals/{record_id}/sub-skills")
def approval_sub_skills_api(record_id: int, request: Request, db: Session = Depends(get_db)):
    """承認画面用：申請者が選択しているサブスキルの状況をJSONで返す（Admin/Manager）"""
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.UserSkillLevel).filter(models.UserSkillLevel.id == record_id)
    if user.role != "admin":
        q = q.filter(models.UserSkillLevel.approver_id == user.id)
    record = q.first()
    if not record:
        raise HTTPException(status_code=404)

    sub_skills = (
        db.query(models.SubSkill)
        .filter(models.SubSkill.skill_id == record.skill_id)
        .order_by(models.SubSkill.order_index)
        .all()
    )
    done_ids: set[int] = set()
    if sub_skills:
        done_ids = {
            r.sub_skill_id for r in
            db.query(models.UserSubSkillLevel).filter(
                models.UserSubSkillLevel.user_id == record.user_id,
                models.UserSubSkillLevel.sub_skill_id.in_([ss.id for ss in sub_skills]),
                models.UserSubSkillLevel.can_do == True,
            ).all()
        }

    return JSONResponse({
        "skill_name": record.skill.name,
        "user_name": record.user.display_name or record.user.username,
        "sub_skills": [
            {"id": ss.id, "name": ss.name, "done": ss.id in done_ids}
            for ss in sub_skills
        ],
    })


@router.get("/approvals/my", response_class=HTMLResponse)
def my_approvals(request: Request, db: Session = Depends(get_db)):
    """自分が申請したスキルレベルの承認状況一覧"""
    user = auth.require_approved(request, db)
    records = (
        db.query(models.UserSkillLevel)
        .filter(models.UserSkillLevel.user_id == user.id)
        .order_by(models.UserSkillLevel.updated_at.desc())
        .all()
    )
    # 承認者リスト（再申請モーダル用）
    group_ids = [m.group_id for m in db.query(models.GroupMembership)
                 .filter(models.GroupMembership.user_id == user.id).all()]
    if group_ids:
        from sqlalchemy import select as sa_select
        co_mgr_ids = set(
            row[0] for row in db.execute(
                sa_select(models.group_managers.c.user_id).where(
                    models.group_managers.c.group_id.in_(group_ids)
                ).distinct()
            ).fetchall()
        )
        primary_mgr_ids = {
            g.manager_id
            for g in db.query(models.Group).filter(models.Group.id.in_(group_ids)).all()
            if g.manager_id
        }
        approver_ids = (co_mgr_ids | primary_mgr_ids) - {user.id}
        approvers = (
            db.query(models.User)
            .filter(models.User.id.in_(approver_ids), models.User.is_approved == True)
            .order_by(models.User.display_name, models.User.username)
            .all()
        ) if approver_ids else (
            db.query(models.User)
            .filter(models.User.is_approved == True,
                    models.User.role.in_(["manager", "admin"]),
                    models.User.id != user.id)
            .order_by(models.User.display_name, models.User.username)
            .all()
        )
    else:
        approvers = (
            db.query(models.User)
            .filter(models.User.is_approved == True,
                    models.User.role.in_(["manager", "admin"]),
                    models.User.id != user.id)
            .order_by(models.User.display_name, models.User.username)
            .all()
        )
    return templates.TemplateResponse(request, "my_approvals.html", {
        "current_user": user,
        "records": records,
        "approvers": approvers,
    })


@router.get("/api/my-skills/{skill_id}/detail")
def my_skill_detail_api(skill_id: int, request: Request, db: Session = Depends(get_db)):
    """自分の申請状況画面用：申請したサブスキルの選択状況とエビデンスをJSONで返す"""
    user = auth.require_approved(request, db)

    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404)

    sub_skills = (
        db.query(models.SubSkill)
        .filter(models.SubSkill.skill_id == skill_id)
        .order_by(models.SubSkill.order_index)
        .all()
    )
    done_ids: set[int] = set()
    if sub_skills:
        done_ids = {
            r.sub_skill_id for r in
            db.query(models.UserSubSkillLevel).filter(
                models.UserSubSkillLevel.user_id == user.id,
                models.UserSubSkillLevel.sub_skill_id.in_([ss.id for ss in sub_skills]),
                models.UserSubSkillLevel.can_do == True,
            ).all()
        }

    return JSONResponse({
        "skill_name": skill.name,
        "sub_skills": [
            {"id": ss.id, "name": ss.name, "done": ss.id in done_ids}
            for ss in sub_skills
        ],
        "evidences": [
            {
                "id": ev.id,
                "evidence_type": ev.evidence_type,
                "title": ev.title or "",
                "content": ev.content or "",
                "original_filename": ev.original_filename or "",
                "created_at": ev.created_at.strftime("%Y-%m-%d") if ev.created_at else "",
            }
            for ev in db.query(models.SkillEvidence).filter(
                models.SkillEvidence.user_id == user.id,
                models.SkillEvidence.skill_id == skill_id,
            ).order_by(models.SkillEvidence.created_at.desc()).all()
        ],
    })


# ════════════════════════════════════════════════════════════════
# JSON API（AJAX 用）
# ════════════════════════════════════════════════════════════════

@router.post("/api/skills/{skill_id}/level")
def api_set_skill_level(
    skill_id: int,
    request: Request,
    level: int = Form(...),
    approver_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """AJAX: ユーザーのスキルレベルを登録・更新し JSON を返す"""
    user = auth.require_approved(request, db)
    level = max(0, min(4, level))

    is_auto_approve = user.role in ("admin", "manager")

    if not is_auto_approve:
        # 一般ユーザーは承認者必須
        approver = db.query(models.User).filter(
            models.User.id == approver_id,
            models.User.is_approved == True,
            models.User.id != user.id,
        ).first()
        if not approver:
            return JSONResponse({"ok": False, "error": "無効な承認者です"}, status_code=400)

    existing = (db.query(models.UserSkillLevel)
                .filter(
                    models.UserSkillLevel.user_id == user.id,
                    models.UserSkillLevel.skill_id == skill_id,
                ).first())

    if is_auto_approve:
        # 承認前のレベルを取得（履歴用）
        prev_history = (
            db.query(models.SkillLevelHistory)
            .filter(
                models.SkillLevelHistory.user_id == user.id,
                models.SkillLevelHistory.skill_id == skill_id,
            )
            .order_by(models.SkillLevelHistory.changed_at.desc())
            .first()
        )
        previous_level = prev_history.level if prev_history else 0

        if existing:
            existing.level = level
            existing.approver_id = None
            existing.approval_status = "approved"
            existing.approved_at = func.now()
            existing.approver_comment = "自動承認"
        else:
            db.add(models.UserSkillLevel(
                user_id=user.id, skill_id=skill_id, level=level,
                approver_id=None, approval_status="approved",
                approved_at=func.now(), approver_comment="自動承認",
            ))

        db.add(models.SkillLevelHistory(
            user_id=user.id,
            skill_id=skill_id,
            level=level,
            previous_level=previous_level,
            approved_by=user.id,
        ))
        db.commit()
        return JSONResponse({
            "ok": True,
            "skill_id": skill_id,
            "level": level,
            "level_name": models.SKILL_LEVELS[level],
            "level_color": models.LEVEL_COLORS[level],
            "approval_status": "approved",
            "approval_status_name": "承認済み",
        })
    else:
        if existing:
            existing.level = level
            existing.approver_id = approver_id
            existing.approval_status = "pending"
            existing.approved_at = None
            existing.approver_comment = None
        else:
            db.add(models.UserSkillLevel(
                user_id=user.id, skill_id=skill_id, level=level,
                approver_id=approver_id, approval_status="pending",
            ))
        db.commit()
        return JSONResponse({
            "ok": True,
            "skill_id": skill_id,
            "level": level,
            "level_name": models.SKILL_LEVELS[level],
            "level_color": models.LEVEL_COLORS[level],
            "approval_status": "pending",
            "approval_status_name": "承認待ち",
        })


@router.post("/api/approvals/my/{record_id}/withdraw")
def withdraw_my_approval(record_id: int, request: Request, db: Session = Depends(get_db)):
    """自分の承認待ち申請を取り下げる（レコード削除）"""
    user = auth.require_approved(request, db)
    rec = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.id == record_id,
            models.UserSkillLevel.user_id == user.id,
            models.UserSkillLevel.approval_status == "pending",
        )
        .first()
    )
    if not rec:
        return JSONResponse({"ok": False, "error": "取り下げ可能な申請が見つかりません"}, status_code=404)
    db.delete(rec)
    db.commit()
    return JSONResponse({"ok": True})


# ════════════════════════════════════════════════════════════════
# スキル申告フロー再設計（案5）
# ════════════════════════════════════════════════════════════════

def calc_level_from_ratio(done: int, total: int) -> int:
    """サブスキル達成率からレベルを自動計算する
    0: 未経験 / 1: 入門 / 2: 実務可 / 3: 指導可 / 4: エキスパート
    """
    if total == 0 or done == 0:
        return 0
    ratio = done / total
    if ratio <= 0.25:
        return 1   # 入門
    if ratio <= 0.50:
        return 2   # 基礎
    if ratio <= 0.75:
        return 3   # 中級
    return 4       # 上級


@router.get("/api/skills/{skill_id}/panel")
def skill_panel_api(skill_id: int, request: Request, view_as: int = 0, db: Session = Depends(get_db)):
    """2ペインUI用：右ペインに必要なデータをJSONで返す。view_as で代理閲覧対応。"""
    from fastapi.responses import JSONResponse as _JSONResponse
    user = auth.require_approved(request, db)

    # 代理閲覧権限チェック
    panel_user = user
    if view_as and view_as != user.id:
        if user.role == "admin":
            t = db.query(models.User).filter(models.User.id == view_as).first()
            if t:
                panel_user = t
        elif user.role == "manager":
            from sqlalchemy import text as _txt_p
            rows = db.execute(
                _txt_p("SELECT user_id FROM group_memberships WHERE group_id IN "
                       "(SELECT group_id FROM group_managers WHERE user_id=:uid "
                       "UNION SELECT id FROM groups WHERE manager_id=:uid)"), {"uid": user.id}
            ).fetchall()
            allowed = {r[0] for r in rows}
            if view_as in allowed:
                t = db.query(models.User).filter(models.User.id == view_as).first()
                if t:
                    panel_user = t

    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404)

    sub_skills = (
        db.query(models.SubSkill)
        .filter(models.SubSkill.skill_id == skill_id)
        .order_by(models.SubSkill.order_index)
        .all()
    )

    done_ids: set[int] = set()
    if sub_skills:
        done_ids = {
            r.sub_skill_id for r in
            db.query(models.UserSubSkillLevel).filter(
                models.UserSubSkillLevel.user_id == panel_user.id,
                models.UserSubSkillLevel.sub_skill_id.in_([ss.id for ss in sub_skills]),
                models.UserSubSkillLevel.can_do == True,
            ).all()
        }

    current_usl = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id == panel_user.id,
            models.UserSkillLevel.skill_id == skill_id,
        )
        .first()
    )

    calc_lv = calc_level_from_ratio(len(done_ids), len(sub_skills))

    # グループマネージャー（panel_userのグループから取得）
    from sqlalchemy import text as _text2
    mgr_ids: set[int] = set()
    for row in db.execute(
        _text2("SELECT DISTINCT gm.user_id FROM group_managers gm "
               "JOIN group_memberships gms ON gm.group_id = gms.group_id "
               "WHERE gms.user_id = :uid"), {"uid": panel_user.id}
    ).fetchall():
        mgr_ids.add(row[0])
    for row in db.execute(
        _text2("SELECT DISTINCT g.manager_id FROM groups g "
               "JOIN group_memberships gms ON g.id = gms.group_id "
               "WHERE gms.user_id = :uid AND g.manager_id IS NOT NULL"), {"uid": panel_user.id}
    ).fetchall():
        mgr_ids.add(row[0])

    managers = []
    if mgr_ids:
        managers = [
            {"id": u.id, "name": u.display_name or u.username}
            for u in db.query(models.User).filter(
                models.User.id.in_(mgr_ids),
                models.User.role.in_(["manager", "admin"]),
                models.User.is_approved == True,
            ).order_by(models.User.display_name).all()
        ]

    # 目標データ
    goal_obj = db.query(models.SkillGoal).filter(
        models.SkillGoal.user_id == panel_user.id,
        models.SkillGoal.skill_id == skill_id,
    ).first()
    is_proxy = panel_user.id != user.id

    return _JSONResponse({
        "skill": {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description or "",
            "tier": skill.tier,
            "tier_name": models.DEFAULT_TIER_NAMES.get(skill.tier, skill.tier),
            "category_name": skill.category.name if skill.category else "",
            "category_color": skill.category.color if skill.category else "#999",
        },
        "sub_skills": [
            {"id": ss.id, "name": ss.name, "description": ss.description or "", "done": ss.id in done_ids}
            for ss in sub_skills
        ],
        "current_level": current_usl.level if current_usl else 0,
        "current_level_name": models.SKILL_LEVELS.get(current_usl.level if current_usl else 0, "未経験"),
        "current_level_color": models.LEVEL_COLORS.get(current_usl.level if current_usl else 0, "secondary"),
        "current_status": current_usl.approval_status if current_usl else None,
        "override_level": current_usl.override_level if current_usl else None,
        "override_reason": current_usl.override_reason or "" if current_usl else "",
        "calc_level": calc_lv,
        "calc_level_name": models.SKILL_LEVELS.get(calc_lv, "未経験"),
        "skill_levels": models.SKILL_LEVELS,
        "level_colors": models.LEVEL_COLORS,
        "group_managers": managers,
        "is_auto_approve": user.role in ("admin", "manager"),
        "is_proxy": is_proxy,
        "proxy_user_id": panel_user.id if is_proxy else 0,
        "proxy_user_name": (panel_user.display_name or panel_user.username) if is_proxy else "",
        "evidences": [
            {
                "id": ev.id,
                "evidence_type": ev.evidence_type,
                "title": ev.title or "",
                "content": ev.content or "",
                "original_filename": ev.original_filename or "",
                "created_at": ev.created_at.strftime("%Y-%m-%d") if ev.created_at else "",
            }
            for ev in db.query(models.SkillEvidence).filter(
                models.SkillEvidence.user_id == panel_user.id,
                models.SkillEvidence.skill_id == skill_id,
            ).order_by(models.SkillEvidence.created_at.desc()).all()
        ],
        "goal": {
            "target_level": goal_obj.target_level,
            "target_level_name": models.SKILL_LEVELS.get(goal_obj.target_level, ""),
            "target_date": goal_obj.target_date.isoformat() if goal_obj.target_date else None,
            "note": goal_obj.note or "",
        } if goal_obj else None,
    })


@router.get("/skills/{skill_id}/declare", response_class=HTMLResponse)
def skill_declare_get(skill_id: int, request: Request, db: Session = Depends(get_db)):
    """スキル申告フォーム（サブスキルチェック + エビデンス）。Admin/Manager は申告不可。"""
    user = auth.require_approved(request, db)
    # Admin/Manager は申告ページにアクセス不可（管理者は申告しない）
    if user.role in ("admin", "manager"):
        return RedirectResponse("/skills/catalog", status_code=303)

    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/skills", status_code=303)

    sub_skills = (
        db.query(models.SubSkill)
        .filter(models.SubSkill.skill_id == skill_id)
        .order_by(models.SubSkill.order_index)
        .all()
    )

    # ユーザーが「できる」にしているサブスキルIDのset
    done_records = (
        db.query(models.UserSubSkillLevel)
        .filter(
            models.UserSubSkillLevel.user_id == user.id,
            models.UserSubSkillLevel.sub_skill_id.in_([ss.id for ss in sub_skills]),
            models.UserSubSkillLevel.can_do == True,
        )
        .all()
    )
    done_ids = {r.sub_skill_id for r in done_records}

    # 現在のUserSkillLevel
    current = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id == user.id,
            models.UserSkillLevel.skill_id == skill_id,
        )
        .first()
    )

    # 既存エビデンス
    evidences = (
        db.query(models.SkillEvidence)
        .filter(
            models.SkillEvidence.user_id == user.id,
            models.SkillEvidence.skill_id == skill_id,
        )
        .order_by(models.SkillEvidence.created_at.desc())
        .all()
    )

    # 現在の達成率から計算したレベル
    calc_level = calc_level_from_ratio(len(done_ids), len(sub_skills))

    # ユーザーが所属するグループのマネージャー一覧を取得
    from sqlalchemy import text as _text

    group_manager_ids = set()
    # group_managers テーブル（多対多）から取得
    gm_rows = db.execute(
        _text("SELECT DISTINCT gm.user_id FROM group_managers gm "
              "JOIN group_memberships gms ON gm.group_id = gms.group_id "
              "WHERE gms.user_id = :uid"),
        {"uid": user.id}
    ).fetchall()
    for row in gm_rows:
        group_manager_ids.add(row[0])

    # グループの manager_id（単数形）も含める
    single_mgr_rows = db.execute(
        _text("SELECT DISTINCT g.manager_id FROM groups g "
              "JOIN group_memberships gms ON g.id = gms.group_id "
              "WHERE gms.user_id = :uid AND g.manager_id IS NOT NULL"),
        {"uid": user.id}
    ).fetchall()
    for row in single_mgr_rows:
        group_manager_ids.add(row[0])

    group_managers = []
    if group_manager_ids:
        group_managers = (
            db.query(models.User)
            .filter(
                models.User.id.in_(group_manager_ids),
                models.User.role.in_(["manager", "admin"]),
                models.User.is_approved == True,
            )
            .order_by(models.User.display_name)
            .all()
        )

    # 目標データ
    skill_goal = db.query(models.SkillGoal).filter(
        models.SkillGoal.user_id == user.id,
        models.SkillGoal.skill_id == skill_id,
    ).first()

    return templates.TemplateResponse(request, "skill_declare.html", {
        "current_user": user,
        "skill": skill,
        "sub_skills": sub_skills,
        "done_ids": done_ids,
        "current": current,
        "evidences": evidences,
        "calc_level": calc_level,
        "SKILL_LEVELS": models.SKILL_LEVELS,
        "LEVEL_COLORS": models.LEVEL_COLORS,
        "APPROVAL_STATUS": models.APPROVAL_STATUS,
        "APPROVAL_STATUS_COLORS": models.APPROVAL_STATUS_COLORS,
        "group_managers": group_managers,
        "skill_goal": skill_goal,
    })


@router.post("/skills/{skill_id}/declare")
def skill_declare_post(
    skill_id: int,
    request: Request,
    sub_skill_ids: List[int] = Form(default=[]),
    override_level: Optional[int] = Form(default=None),
    override_reason: str = Form(default=""),
    approver_id: Optional[int] = Form(default=None),
    for_user_id: int = Form(default=0),
    db: Session = Depends(get_db),
):
    """スキル申告フォーム送信処理。Admin/Manager は代理申告のみ可。"""
    user = auth.require_approved(request, db)

    # 代理申告モードの解決
    declare_user = user  # 申告対象ユーザー
    is_proxy = False
    if for_user_id and for_user_id != user.id:
        if user.role == "admin":
            t = db.query(models.User).filter(models.User.id == for_user_id).first()
            if t:
                declare_user = t
                is_proxy = True
        elif user.role == "manager":
            from sqlalchemy import text as _txt_dec
            rows = db.execute(
                _txt_dec("SELECT user_id FROM group_memberships WHERE group_id IN "
                         "(SELECT group_id FROM group_managers WHERE user_id=:uid "
                         "UNION SELECT id FROM groups WHERE manager_id=:uid)"), {"uid": user.id}
            ).fetchall()
            allowed = {r[0] for r in rows}
            if for_user_id in allowed:
                t = db.query(models.User).filter(models.User.id == for_user_id).first()
                if t:
                    declare_user = t
                    is_proxy = True

    # Admin/Manager 自身は代理申告のみ（自分自身への申告は不可）
    if user.role in ("admin", "manager") and not is_proxy:
        return RedirectResponse("/skills/catalog", status_code=303)

    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/skills", status_code=303)

    # 1. UserSubSkillLevel を upsert
    all_sub_skills = (
        db.query(models.SubSkill)
        .filter(models.SubSkill.skill_id == skill_id)
        .all()
    )
    checked_ids = set(sub_skill_ids)

    for ss in all_sub_skills:
        existing = (
            db.query(models.UserSubSkillLevel)
            .filter(
                models.UserSubSkillLevel.user_id == declare_user.id,
                models.UserSubSkillLevel.sub_skill_id == ss.id,
            )
            .first()
        )
        can_do = ss.id in checked_ids
        if existing:
            existing.can_do = can_do
        else:
            db.add(models.UserSubSkillLevel(
                user_id=declare_user.id,
                sub_skill_id=ss.id,
                can_do=can_do,
            ))

    # 2. 達成率からレベルを計算
    done_count = len(checked_ids)
    total_count = len(all_sub_skills)
    auto_level = calc_level_from_ratio(done_count, total_count)

    # 3. override_level が指定されている場合は使用（reason必須チェック）
    if override_level is not None and override_level != auto_level:
        if not override_reason.strip():
            override_level = None

    final_level = override_level if override_level is not None else auto_level

    # 4. UserSkillLevel を upsert
    # 代理申告 or Admin/Manager は自動承認
    is_auto_approve = is_proxy or user.role in ("admin", "manager")
    approval_status = "approved" if is_auto_approve else "pending"

    existing_usl = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id == declare_user.id,
            models.UserSkillLevel.skill_id == skill_id,
        )
        .first()
    )

    if existing_usl:
        existing_usl.level = final_level
        existing_usl.approval_status = approval_status
        existing_usl.override_level = override_level
        existing_usl.override_reason = override_reason.strip() or None
        if is_auto_approve:
            existing_usl.approved_at = func.now()
            existing_usl.approver_comment = "自動承認（サブスキル申告）"
    else:
        new_usl = models.UserSkillLevel(
            user_id=declare_user.id,
            skill_id=skill_id,
            level=final_level,
            approval_status=approval_status,
            override_level=override_level,
            override_reason=override_reason.strip() or None,
        )
        if is_auto_approve:
            new_usl.approved_at = func.now()
            new_usl.approver_comment = "自動承認（サブスキル申告）" if not is_proxy else f"代理申告（{user.display_name or user.username}）"
        db.add(new_usl)

    # 承認者の設定（一般ユーザーのみ）
    if not is_auto_approve and approver_id:
        valid_approver = db.query(models.User).filter(
            models.User.id == approver_id,
            models.User.role.in_(["manager", "admin"]),
        ).first()
        if valid_approver:
            if existing_usl:
                existing_usl.approver_id = valid_approver.id
            else:
                new_usl.approver_id = valid_approver.id

    _award_badges(declare_user.id, db)
    db.commit()
    redirect_url = f"/skills?view_as={declare_user.id}" if is_proxy else "/skills"
    return RedirectResponse(redirect_url, status_code=303)


EVIDENCE_UPLOAD_DIR = "/app/data/uploads/evidence"


@router.post("/skills/{skill_id}/evidence/add")
async def skill_evidence_add(
    skill_id: int,
    request: Request,
    evidence_type: str = Form(...),          # 'url', 'note', 'file'
    title: str = Form(default=""),
    content: str = Form(default=""),
    upload_file: UploadFile = File(default=None),
    db: Session = Depends(get_db),
):
    """スキルエビデンスを追加する"""
    import shutil
    import uuid as _uuid
    user = auth.require_approved(request, db)

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        if is_ajax:
            return JSONResponse({"success": False, "error": "スキルが見つかりません"}, status_code=404)
        return RedirectResponse("/skills", status_code=303)

    evidence = None
    if evidence_type == "file" and upload_file and upload_file.filename:
        os.makedirs(EVIDENCE_UPLOAD_DIR, exist_ok=True)
        original_name = upload_file.filename
        ext = os.path.splitext(original_name)[1] if "." in original_name else ""
        saved_name = f"{_uuid.uuid4()}{ext}"
        save_path = os.path.join(EVIDENCE_UPLOAD_DIR, saved_name)
        with open(save_path, "wb") as f:
            shutil.copyfileobj(upload_file.file, f)
        evidence = models.SkillEvidence(
            user_id=user.id,
            skill_id=skill_id,
            evidence_type="file",
            title=title.strip() or original_name,
            content="",
            file_path=save_path,
            original_filename=original_name,
        )
    elif evidence_type == "url":
        if is_ajax and not content.strip():
            return JSONResponse({"success": False, "error": "URLを入力してください"}, status_code=400)
        evidence = models.SkillEvidence(
            user_id=user.id,
            skill_id=skill_id,
            evidence_type="url",
            title=title.strip() or None,
            content=content.strip(),
        )
    else:
        if is_ajax and not content.strip():
            return JSONResponse({"success": False, "error": "内容を入力してください"}, status_code=400)
        evidence = models.SkillEvidence(
            user_id=user.id,
            skill_id=skill_id,
            evidence_type="note",
            title=title.strip() or None,
            content=content.strip(),
        )

    if evidence is None:
        if is_ajax:
            return JSONResponse({"success": False, "error": "ファイルを選択してください"}, status_code=400)
        return RedirectResponse(f"/skills/{skill_id}/declare", status_code=303)

    db.add(evidence)
    db.commit()

    if is_ajax:
        db.refresh(evidence)
        return JSONResponse({
            "success": True,
            "evidence": {
                "id": evidence.id,
                "evidence_type": evidence.evidence_type,
                "title": evidence.title,
                "content": evidence.content,
                "original_filename": evidence.original_filename,
                "download_url": (f"/skills/evidence/{evidence.id}/download"
                                 if evidence.evidence_type == "file" else None),
                "delete_url": f"/skills/evidence/{evidence.id}/delete",
            },
        })
    return RedirectResponse(f"/skills/{skill_id}/declare", status_code=303)


@router.post("/skills/evidence/{evidence_id}/delete")
def skill_evidence_delete(
    evidence_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """スキルエビデンスを削除する"""
    user = auth.require_approved(request, db)

    evidence = (
        db.query(models.SkillEvidence)
        .filter(
            models.SkillEvidence.id == evidence_id,
            models.SkillEvidence.user_id == user.id,
        )
        .first()
    )
    if evidence:
        skill_id = evidence.skill_id
        db.delete(evidence)
        db.commit()
        referer = request.headers.get("referer", "")
        if referer:
            return RedirectResponse(referer, status_code=303)
        return RedirectResponse(f"/skills/{skill_id}/declare", status_code=303)

    return RedirectResponse("/skills", status_code=303)


@router.get("/skills/evidence/{evidence_id}/download")
def skill_evidence_download(evidence_id: int, request: Request, db: Session = Depends(get_db)):
    """アップロードされたエビデンスファイルをダウンロードする"""
    from fastapi.responses import FileResponse
    user = auth.require_approved(request, db)
    q = db.query(models.SkillEvidence).filter(models.SkillEvidence.id == evidence_id)
    # Admin/Manager は全員のファイルをダウンロード可能、User は自分のみ
    if user.role not in ("admin", "manager"):
        q = q.filter(models.SkillEvidence.user_id == user.id)
    ev = q.first()
    if not ev or not ev.file_path or not os.path.exists(ev.file_path):
        raise HTTPException(status_code=404)
    return FileResponse(ev.file_path, filename=ev.original_filename or "download")


@router.get("/api/dashboard/stats")
def api_dashboard_stats(request: Request, db: Session = Depends(get_db)):
    """AJAX: ダッシュボード用の集計 JSON を返す"""
    user = auth.require_approved(request, db)

    my_levels = (db.query(models.UserSkillLevel)
                 .filter(
                     models.UserSkillLevel.user_id == user.id,
                     models.UserSkillLevel.approval_status == "approved",
                 ).all())
    total = len(my_levels)
    catalog_total = db.query(models.Skill).count()
    avg_level = round(sum(sl.level for sl in my_levels) / total, 1) if total else 0.0

    level_dist = {str(i): 0 for i in range(5)}
    for sl in my_levels:
        level_dist[str(sl.level)] += 1

    cat_stats: dict[str, float] = {}
    cat_counts: dict[str, int] = {}
    for sl in my_levels:
        if sl.skill.category:
            n = sl.skill.category.name
            cat_stats[n] = cat_stats.get(n, 0.0) + sl.level
            cat_counts[n] = cat_counts.get(n, 0) + 1

    cat_avg = {}
    for n in cat_stats:
        cat_avg[n] = round(cat_stats[n] / cat_counts[n], 1) if cat_counts[n] else 0

    tier_stats = {}
    for tk in models.SKILL_TIERS:
        tier_total = db.query(models.Skill).filter(models.Skill.tier == tk).count()
        tier_done = sum(1 for sl in my_levels if sl.skill.tier == tk and sl.level > 0)
        tier_stats[tk] = {"total": tier_total, "done": tier_done}

    return JSONResponse({
        "total": total,
        "catalog_total": catalog_total,
        "avg_level": avg_level,
        "level_dist": level_dist,
        "cat_avg": cat_avg,
        "tier_stats": tier_stats,
    })


# ════════════════════════════════════════════════════════════════
# スキルマトリクス（社員×スキル ヒートマップ）
# ════════════════════════════════════════════════════════════════

@router.get("/skills/matrix", response_class=HTMLResponse)
def skill_matrix(
    request: Request,
    category_id: int = 0,
    group_id: int = 0,
    db: Session = Depends(get_db),
):
    """管理者/マネージャー向け: 社員のスキル状態を一覧表示"""
    user = auth.require_manager_or_admin(request, db)

    # Manager が閲覧可能なグループ ID
    is_manager = user.role == "manager"
    if is_manager:
        managed_group_ids = {g.id for g in user.managed_groups}
        managed_member_ids = {
            m.user_id
            for gid in managed_group_ids
            for m in db.query(models.GroupMembership)
                .filter(models.GroupMembership.group_id == gid).all()
        }

    # 対象ユーザーの絞り込み
    if group_id:
        # Manager は自分の担当グループのみ
        if is_manager and group_id not in managed_group_ids:
            group_id = 0
        group = db.query(models.Group).filter(models.Group.id == group_id).first() if group_id else None
        member_ids = [m.user_id for m in group.memberships] if group else []
        users = (db.query(models.User)
                 .filter(models.User.id.in_(member_ids),
                         models.User.role != "admin")
                 .order_by(models.User.display_name, models.User.username)
                 .all()) if member_ids else []
    else:
        if is_manager:
            # Manager: 担当グループのメンバーのみ（Admin 除外）
            users = (db.query(models.User)
                     .filter(models.User.id.in_(managed_member_ids),
                             models.User.is_approved == True,
                             models.User.role != "admin")
                     .order_by(models.User.display_name, models.User.username)
                     .all()) if managed_member_ids else []
        else:
            # Admin: 自分以外の全承認済みユーザー（Admin 除外）
            users = (db.query(models.User)
                     .filter(models.User.is_approved == True,
                             models.User.role != "admin")
                     .order_by(models.User.display_name, models.User.username)
                     .all())

    # スキルカタログ
    q = db.query(models.Skill)
    if category_id:
        q = q.filter(models.Skill.category_id == category_id)
    skills = q.order_by(models.Skill.category_id, models.Skill.name).all()

    # グループが選択されていて、そのグループにスキル割当がある場合はフィルタ（継承含む）
    if group_id:
        sel_group_obj = db.query(models.Group).filter(models.Group.id == group_id).first()
        if sel_group_obj:
            group_skill_ids = _get_all_group_skill_ids(sel_group_obj)
            if group_skill_ids:
                skills = [sk for sk in skills if sk.id in group_skill_ids]

    # 全承認済みスキルレベル取得
    user_ids = [u.id for u in users]
    all_levels = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id.in_(user_ids),
            models.UserSkillLevel.approval_status == "approved",
        )
        .all()
    ) if user_ids else []

    # {(user_id, skill_id): level} のマップ
    level_map = {}
    for sl in all_levels:
        level_map[(sl.user_id, sl.skill_id)] = sl.level

    categories = db.query(models.Category).order_by(models.Category.name).all()
    if is_manager:
        groups = (db.query(models.Group)
                  .filter(models.Group.id.in_(managed_group_ids))
                  .order_by(models.Group.name).all())
    else:
        groups = db.query(models.Group).order_by(models.Group.name).all()

    # ── 分析データ ──
    from datetime import timedelta
    from collections import defaultdict

    # 1) カテゴリー別平均（レーダーチャート用）
    cat_user_avg: dict[str, dict[str, float]] = {}  # {cat_name: {user_name: avg}} チャート用
    cat_user_avg_id: dict[str, dict[int, float]] = {}  # {cat_name: {user_id: avg}} テンプレート用
    cat_coverage: dict[str, dict] = {}  # {cat_name: {covered, total, pct}}
    cat_names_ordered = []
    for cat in categories:
        cat_skills = [sk for sk in skills if sk.category_id == cat.id]
        if not cat_skills:
            continue
        cat_names_ordered.append(cat.name)
        cat_user_avg[cat.name] = {}
        cat_user_avg_id[cat.name] = {}
        for u in users:
            vals = [level_map.get((u.id, sk.id), 0) for sk in cat_skills]
            filled = [v for v in vals if v > 0]
            avg_val = round(sum(filled) / len(filled), 2) if filled else 0.0
            cat_user_avg[cat.name][u.display_name or u.username] = avg_val
            cat_user_avg_id[cat.name][u.id] = avg_val
        # カテゴリカバレッジ（申告者が1人以上いるスキル数）
        covered = sum(
            1 for sk in cat_skills
            if any(level_map.get((u.id, sk.id), 0) > 0 for u in users)
        )
        total = len(cat_skills)
        cat_coverage[cat.name] = {
            "covered": covered,
            "total": total,
            "pct": int(covered / total * 100) if total else 0,
        }

    # 2) レベル分布（ドーナツ用）
    level_dist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for u in users:
        for sk in skills:
            lv = level_map.get((u.id, sk.id), 0)
            level_dist[lv] += 1

    # 3) 成長トレンド（折れ線グラフ用）: 月別の平均レベル推移
    growth_trend: list[dict] = []
    if user_ids:
        history_all = (
            db.query(models.SkillLevelHistory)
            .filter(models.SkillLevelHistory.user_id.in_(user_ids))
            .order_by(models.SkillLevelHistory.changed_at.asc())
            .all()
        )
        monthly: dict[str, list[int]] = defaultdict(list)
        for rec in history_all:
            if rec.changed_at:
                month_key = (rec.changed_at + timedelta(hours=9)).strftime("%Y-%m")
                monthly[month_key].append(rec.level)
        for month_key in sorted(monthly.keys()):
            vals = monthly[month_key]
            growth_trend.append({
                "month": month_key,
                "avg": round(sum(vals) / len(vals), 2),
                "count": len(vals),
            })

    # 4) ユーザー別平均（横棒グラフ用）
    user_avg_ranking = []
    for u in users:
        vals = [level_map.get((u.id, sk.id), 0) for sk in skills]
        filled = [v for v in vals if v > 0]
        avg = round(sum(filled) / len(filled), 2) if filled else 0.0
        user_avg_ranking.append({
            "name": u.display_name or u.username,
            "avg": avg,
            "count": len(filled),
        })
    user_avg_ranking.sort(key=lambda x: x["avg"], reverse=True)

    # メンバー別強み・弱み分析
    member_analysis = []
    for u in users:
        # カテゴリ別平均（申告済みスキルのみ）
        cat_avgs = []
        for cat_name, umap in cat_user_avg_id.items():
            avg_val = umap.get(u.id, 0.0)
            # そのカテゴリのスキル申告数も算出
            cat_skills_local = [sk for sk in skills if (sk.category.name if sk.category else "未分類") == cat_name]
            declared = sum(1 for sk in cat_skills_local if level_map.get((u.id, sk.id), 0) > 0)
            if declared > 0:
                cat_avgs.append({"cat": cat_name, "avg": avg_val, "declared": declared, "total": len(cat_skills_local)})

        cat_avgs.sort(key=lambda x: x["avg"], reverse=True)
        strong = cat_avgs[:3]
        weak   = sorted([c for c in cat_avgs if c["avg"] < 2.0], key=lambda x: x["avg"])[:3]

        # 全体申告数と平均
        all_vals  = [level_map.get((u.id, sk.id), 0) for sk in skills]
        all_filled = [v for v in all_vals if v > 0]
        overall_avg = round(sum(all_filled) / len(all_filled), 2) if all_filled else 0.0

        member_analysis.append({
            "user":        u,
            "overall_avg": overall_avg,
            "declared":    len(all_filled),
            "total":       len(skills),
            "strong":      strong,
            "weak":        weak,
        })

    from config import get_config as _gcfg
    _cfg = _gcfg()
    return templates.TemplateResponse(request, "skill_matrix.html", {
        "current_user": user,
        "users": users,
        "skills": skills,
        "level_map": level_map,
        "categories": categories,
        "groups": groups,
        "sel_category": category_id,
        "sel_group": group_id,
        # 分析データ
        "cat_names_ordered": cat_names_ordered,
        "cat_user_avg": cat_user_avg,
        "cat_user_avg_id": cat_user_avg_id,
        "cat_coverage": cat_coverage,
        "level_dist": level_dist,
        "growth_trend": growth_trend,
        "user_avg_ranking": user_avg_ranking,
        "member_analysis": member_analysis,
        "ai_summary_enabled": _cfg.get("ai_summary_enabled", False),
    })


# ════════════════════════════════════════════════════════════════
# メンバースキル詳細（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/members/{user_id}/skills", response_class=HTMLResponse)
def member_skill_detail(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Manager以上がメンバーの取得スキル状況を詳細確認"""
    current_user = auth.require_manager_or_admin(request, db)

    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        return RedirectResponse("/dashboard", status_code=303)

    # Manager は自分の担当グループのメンバーのみ閲覧可
    if current_user.role == "manager":
        managed_group_ids = [g.id for g in current_user.managed_groups]
        is_member = (db.query(models.GroupMembership)
                     .filter(
                         models.GroupMembership.user_id == user_id,
                         models.GroupMembership.group_id.in_(managed_group_ids),
                     ).first())
        if not is_member and user_id != current_user.id:
            return RedirectResponse("/dashboard", status_code=303)

    # 承認済みスキルレベル
    skill_levels = (db.query(models.UserSkillLevel)
                    .filter(
                        models.UserSkillLevel.user_id == target.id,
                        models.UserSkillLevel.approval_status == "approved",
                    ).all())

    # カテゴリー別に分類
    categories = db.query(models.Category).order_by(models.Category.name).all()
    cat_skills: dict[str, list] = {}
    uncategorized = []
    for sl in skill_levels:
        if sl.level == 0:
            continue
        if sl.skill.category:
            cname = sl.skill.category.name
            if cname not in cat_skills:
                cat_skills[cname] = []
            cat_skills[cname].append(sl)
        else:
            uncategorized.append(sl)
    # カテゴリーごとにレベル降順
    for k in cat_skills:
        cat_skills[k].sort(key=lambda sl: sl.level, reverse=True)
    uncategorized.sort(key=lambda sl: sl.level, reverse=True)

    # サマリー
    total = sum(1 for sl in skill_levels if sl.level > 0)
    avg_level = round(sum(sl.level for sl in skill_levels if sl.level > 0) / total, 1) if total else 0.0
    catalog_total = db.query(models.Skill).count()

    # レベル分布
    level_dist = {i: 0 for i in range(5)}
    for sl in skill_levels:
        level_dist[sl.level] += 1

    # ティア別
    tier_stats = {}
    for tier_key in models.SKILL_TIERS:
        tier_catalog = db.query(models.Skill).filter(models.Skill.tier == tier_key).count()
        tier_done = sum(1 for sl in skill_levels if sl.skill.tier == tier_key and sl.level > 0)
        tier_stats[tier_key] = {"total": tier_catalog, "done": tier_done}

    # 所属グループ
    user_groups = (db.query(models.Group)
                   .join(models.GroupMembership)
                   .filter(models.GroupMembership.user_id == target.id)
                   .order_by(models.Group.name).all())

    # 成長履歴（直近）
    recent_history = (db.query(models.SkillLevelHistory)
                      .filter(models.SkillLevelHistory.user_id == target.id)
                      .order_by(models.SkillLevelHistory.changed_at.desc())
                      .limit(10).all())

    return templates.TemplateResponse(request, "member_detail.html", {
        "current_user": current_user,
        "target": target,
        "cat_skills": cat_skills,
        "uncategorized": uncategorized,
        "total": total,
        "avg_level": avg_level,
        "catalog_total": catalog_total,
        "level_dist": level_dist,
        "tier_stats": tier_stats,
        "user_groups": user_groups,
        "recent_history": recent_history,
        "categories": categories,
    })


# ════════════════════════════════════════════════════════════════
# スキル成長タイムライン
# ════════════════════════════════════════════════════════════════

@router.get("/skills/timeline", response_class=HTMLResponse)
def skill_timeline(
    request: Request,
    user_id: int = 0,
    db: Session = Depends(get_db),
):
    """スキルの成長を時系列で確認"""
    current_user = auth.require_approved(request, db)
    is_privileged = current_user.role in ("admin", "manager")

    # 表示対象ユーザー
    if user_id and is_privileged:
        target = db.query(models.User).filter(models.User.id == user_id).first()
        if not target:
            target = current_user
    else:
        target = current_user

    # ユーザー選択候補（管理者/マネージャー用）
    all_users = []
    if is_privileged:
        all_users = (db.query(models.User)
                     .filter(models.User.is_approved == True)
                     .order_by(models.User.display_name, models.User.username)
                     .all())

    # 成長履歴の取得
    history = (
        db.query(models.SkillLevelHistory)
        .filter(models.SkillLevelHistory.user_id == target.id)
        .order_by(models.SkillLevelHistory.changed_at.asc())
        .all()
    )

    # 最新の承認済みスキルレベル
    current_levels = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id == target.id,
            models.UserSkillLevel.approval_status == "approved",
        )
        .all()
    )

    # カテゴリーごとの平均レベル推移データを構築
    from datetime import timedelta
    cat_timeline: dict[str, list] = {}
    for rec in history:
        cat_name = rec.skill.category.name if rec.skill.category else "未分類"
        if cat_name not in cat_timeline:
            cat_timeline[cat_name] = []
        cat_timeline[cat_name].append({
            "date": (rec.changed_at + timedelta(hours=9)).strftime("%Y-%m-%d") if rec.changed_at else "",
            "skill": rec.skill.name,
            "level": rec.level,
            "prev": rec.previous_level or 0,
        })

    # スキルごとの成長推移データ
    skill_timeline: dict[str, list] = {}
    for rec in history:
        sname = rec.skill.name
        if sname not in skill_timeline:
            skill_timeline[sname] = []
        skill_timeline[sname].append({
            "date": (rec.changed_at + timedelta(hours=9)).strftime("%Y-%m-%d") if rec.changed_at else "",
            "level": rec.level,
        })

    # 成長サマリー
    growth_count = sum(1 for r in history if (r.previous_level or 0) < r.level)
    recent_history = list(reversed(history[-20:]))

    return templates.TemplateResponse(request, "skill_timeline.html", {
        "current_user": current_user,
        "target": target,
        "all_users": all_users,
        "history": recent_history,
        "current_levels": current_levels,
        "cat_timeline": cat_timeline,
        "skill_timeline": skill_timeline,
        "growth_count": growth_count,
        "total_changes": len(history),
        "is_privileged": is_privileged,
    })


@router.get("/api/skills/timeline/{target_user_id}")
def api_skill_timeline(
    target_user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """AJAX: 特定ユーザーのスキル成長履歴JSON"""
    current_user = auth.require_approved(request, db)
    is_privileged = current_user.role in ("admin", "manager")
    if target_user_id != current_user.id and not is_privileged:
        return JSONResponse({"error": "権限がありません"}, status_code=403)

    history = (
        db.query(models.SkillLevelHistory)
        .filter(models.SkillLevelHistory.user_id == target_user_id)
        .order_by(models.SkillLevelHistory.changed_at.asc())
        .all()
    )

    from datetime import timedelta
    data = []
    for rec in history:
        data.append({
            "date": (rec.changed_at + timedelta(hours=9)).strftime("%Y-%m-%d") if rec.changed_at else "",
            "skill": rec.skill.name,
            "category": rec.skill.category.name if rec.skill.category else "未分類",
            "level": rec.level,
            "previous_level": rec.previous_level or 0,
        })

    return JSONResponse({"ok": True, "data": data})


# ════════════════════════════════════════════════════════════════
# スキルタグ管理
# ════════════════════════════════════════════════════════════════

@router.get("/tags", response_class=HTMLResponse)
def tags_list(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    tags = db.query(models.SkillTag).order_by(models.SkillTag.name).all()
    try:
        return templates.TemplateResponse(request, "tags.html", {
            "current_user": user, "tags": tags
        })
    except Exception:
        return JSONResponse([
            {"id": t.id, "name": t.name, "color": t.color} for t in tags
        ])


@router.post("/tags/new")
def tag_new_post(
    request: Request,
    name: str = Form(...),
    color: str = Form("#6c757d"),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    if not db.query(models.SkillTag).filter(models.SkillTag.name == name).first():
        db.add(models.SkillTag(name=name, color=color))
        db.commit()
    return RedirectResponse("/tags", status_code=303)


@router.post("/tags/{tag_id}/delete")
def tag_delete(tag_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    tag = db.query(models.SkillTag).filter(models.SkillTag.id == tag_id).first()
    if tag:
        db.delete(tag)
        db.commit()
    return RedirectResponse("/tags", status_code=303)


# ════════════════════════════════════════════════════════════════
# スキル一括操作
# ════════════════════════════════════════════════════════════════

@router.post("/skills/bulk-action")
def skills_bulk_action(
    request: Request,
    skill_ids: List[int] = Form(default=[]),
    action: str = Form(default=""),
    category_id: Optional[int] = Form(default=None),
    tier: Optional[str] = Form(default=None),
    tag_id: Optional[int] = Form(default=None),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    if not skill_ids:
        return RedirectResponse("/skills/catalog", status_code=303)

    skills = db.query(models.Skill).filter(models.Skill.id.in_(skill_ids)).all()

    if action == "archive":
        for skill in skills:
            skill.is_archived = True
    elif action == "unarchive":
        for skill in skills:
            skill.is_archived = False
    elif action == "change_category" and category_id is not None:
        for skill in skills:
            skill.category_id = category_id or None
    elif action == "change_tier" and tier:
        for skill in skills:
            skill.tier = tier
    elif action == "add_tag" and tag_id is not None:
        tag = db.query(models.SkillTag).filter(models.SkillTag.id == tag_id).first()
        if tag:
            for skill in skills:
                if tag not in skill.tags:
                    skill.tags.append(tag)
    elif action == "delete":
        # 関連データを全て削除してからスキルを削除
        sub_ids = [r[0] for r in db.query(models.SubSkill.id).filter(
            models.SubSkill.skill_id.in_(skill_ids)).all()]
        if sub_ids:
            db.query(models.UserSubSkillLevel).filter(
                models.UserSubSkillLevel.sub_skill_id.in_(sub_ids)
            ).delete(synchronize_session=False)
        db.query(models.SubSkill).filter(
            models.SubSkill.skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(models.UserSkillLevel).filter(
            models.UserSkillLevel.skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(models.SkillLevelHistory).filter(
            models.SkillLevelHistory.skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(models.SkillEvidence).filter(
            models.SkillEvidence.skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(models.SkillGoal).filter(
            models.SkillGoal.skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(models.Skill).filter(
            models.Skill.id.in_(skill_ids)
        ).delete(synchronize_session=False)

    db.commit()
    return RedirectResponse("/skills/catalog", status_code=303)


# ════════════════════════════════════════════════════════════════
# サブスキル管理（Admin / Manager のみ）
# ════════════════════════════════════════════════════════════════

@router.post("/skills/{skill_id}/sub-skills/add")
def sub_skill_add(skill_id: int, request: Request,
                  name: str = Form(...), description: str = Form(""),
                  db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404)
    max_order = db.query(func.max(models.SubSkill.order_index)).filter(
        models.SubSkill.skill_id == skill_id).scalar() or -1
    db.add(models.SubSkill(
        skill_id=skill_id, name=name.strip(),
        description=description.strip() or None,
        order_index=max_order + 1,
        created_by=user.id,
    ))
    db.commit()
    return RedirectResponse(f"/skills/catalog?highlight={skill_id}", status_code=303)


@router.post("/skills/sub-skills/{sub_id}/delete")
def sub_skill_delete(sub_id: int, request: Request, db: Session = Depends(get_db)):
    auth.require_manager_or_admin(request, db)
    ss = db.query(models.SubSkill).filter(models.SubSkill.id == sub_id).first()
    if ss:
        db.delete(ss)
        db.commit()
    return RedirectResponse(f"/skills/catalog?highlight={ss.skill_id if ss else ''}", status_code=303)


@router.post("/skills/sub-skills/{sub_id}/edit")
def sub_skill_edit(sub_id: int, request: Request,
                   name: str = Form(...), description: str = Form(""),
                   db: Session = Depends(get_db)):
    auth.require_manager_or_admin(request, db)
    ss = db.query(models.SubSkill).filter(models.SubSkill.id == sub_id).first()
    if ss:
        ss.name = name.strip()
        ss.description = description.strip() or None
        db.commit()
    return RedirectResponse(f"/skills/catalog?highlight={ss.skill_id if ss else ''}", status_code=303)


@router.post("/skills/sub-skills/reorder")
async def sub_skill_reorder(request: Request, db: Session = Depends(get_db)):
    auth.require_manager_or_admin(request, db)
    data = await request.json()
    for i, sub_id in enumerate(data.get("ids", [])):
        ss = db.query(models.SubSkill).filter(models.SubSkill.id == sub_id).first()
        if ss:
            ss.order_index = i
    db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════
# スキル目標設定
# ════════════════════════════════════════════════════════════════

@router.post("/skills/{skill_id}/goal")
def skill_goal_set(
    skill_id: int,
    request: Request,
    target_level: int = Form(...),
    target_date: str = Form(default=""),
    note: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    existing = db.query(models.SkillGoal).filter(
        models.SkillGoal.user_id == user.id,
        models.SkillGoal.skill_id == skill_id,
    ).first()
    from datetime import date as _date
    td = None
    if target_date.strip():
        try:
            td = _date.fromisoformat(target_date.strip())
        except ValueError:
            pass
    if existing:
        existing.target_level = target_level
        existing.target_date = td
        existing.note = note.strip() or None
    else:
        db.add(models.SkillGoal(
            user_id=user.id, skill_id=skill_id,
            target_level=target_level, target_date=td,
            note=note.strip() or None,
        ))
    db.commit()
    return RedirectResponse(f"/skills/{skill_id}/declare", status_code=303)


@router.post("/skills/{skill_id}/goal/delete")
def skill_goal_delete(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    db.query(models.SkillGoal).filter(
        models.SkillGoal.user_id == user.id,
        models.SkillGoal.skill_id == skill_id,
    ).delete()
    db.commit()
    return RedirectResponse(f"/skills/{skill_id}/declare", status_code=303)


# ════════════════════════════════════════════════════════════════
# 達成バッジ付与ヘルパー
# ════════════════════════════════════════════════════════════════

def _award_badges(user_id: int, db) -> list:
    """申告後にバッジ条件をチェックして新しいバッジを付与する。新規取得バッジキーのリストを返す。"""
    from models import UserSkillLevel, UserSubSkillLevel, UserBadge, BADGE_DEFS, Skill

    already = {b.badge_key for b in db.query(UserBadge).filter(UserBadge.user_id == user_id).all()}
    new_badges = []

    def award(key):
        if key not in already:
            db.add(UserBadge(user_id=user_id, badge_key=key))
            new_badges.append(key)
            already.add(key)

    levels = db.query(UserSkillLevel).filter(
        UserSkillLevel.user_id == user_id,
        UserSkillLevel.approval_status.in_(["approved", "pending"]),
    ).all()

    # 初申告
    if levels:
        award("first_declare")
    # 初承認
    if any(l.approval_status == "approved" for l in levels):
        award("first_approved")
    # 上級スキル
    if any(l.level == 4 for l in levels):
        award("level4_skill")
    # サブスキルチェック数
    done_count = db.query(UserSubSkillLevel).filter(
        UserSubSkillLevel.user_id == user_id,
        UserSubSkillLevel.can_do == True,
    ).count()
    if done_count >= 10:
        award("sub_check_10")
    if done_count >= 50:
        award("sub_check_50")
    # カテゴリ数
    declared_skill_ids = {l.skill_id for l in levels}
    cat_ids = {s.category_id for s in db.query(Skill).filter(Skill.id.in_(declared_skill_ids)).all() if s.category_id}
    if len(cat_ids) >= 5:
        award("multi_cat_5")
    # カテゴリ制覇（1カテゴリの全スキルを申告）
    for cat_id in cat_ids:
        cat_skills = db.query(Skill).filter(Skill.category_id == cat_id, Skill.is_archived == False).all()
        if cat_skills and all(s.id in declared_skill_ids for s in cat_skills):
            award("cat_complete")
            break

    if new_badges:
        db.flush()
    return new_badges


# ════════════════════════════════════════════════════════════════
# 一括エクスポート / インポート（カテゴリ + スキル + サブスキル）
# ════════════════════════════════════════════════════════════════

@router.get("/skills/bulk-export")
def bulk_export(request: Request, db: Session = Depends(get_db)):
    """カテゴリ・スキルカタログ・サブスキルを1つのJSONファイルで一括エクスポート"""
    from fastapi.responses import Response as _Response
    import json as _json
    from datetime import datetime as _dt
    auth.require_manager_or_admin(request, db)

    cats = db.query(models.Category).order_by(models.Category.name).all()
    skills = db.query(models.Skill).filter(models.Skill.is_archived == False).order_by(models.Skill.id).all()
    sub_skills = db.query(models.SubSkill).order_by(models.SubSkill.skill_id, models.SubSkill.order_index).all()

    data = {
        "exported_at": _dt.now().isoformat(),
        "categories": [
            {"id": c.id, "name": c.name, "color": c.color, "description": c.description or ""}
            for c in cats
        ],
        "skills": [
            {
                "id": sk.id,
                "name": sk.name,
                "description": sk.description or "",
                "tier": sk.tier,
                "category_name": sk.category.name if sk.category else "",
            }
            for sk in skills
        ],
        "sub_skills": [
            {
                "skill_id": ss.skill_id,
                "skill_name": ss.skill.name if ss.skill else "",
                "name": ss.name,
                "description": ss.description or "",
                "order_index": ss.order_index,
            }
            for ss in sub_skills
        ],
    }

    body = _json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"skillmap_bulk_{_dt.now().strftime('%Y%m%d')}.json"
    return _Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _delete_all_catalog_data(db: Session):
    """カテゴリ・スキル・サブスキルと、それらに紐づくユーザーデータを完全に削除する。

    「全削除してインポート」モードの前処理。スキル・サブスキルに依存する
    申告レベル・エビデンス・目標・タグ等を先に削除してから本体を削除する。
    """
    from sqlalchemy import text
    sub_skill_ids = [r[0] for r in db.query(models.SubSkill.id).all()]
    if sub_skill_ids:
        db.query(models.UserSubSkillLevel).filter(
            models.UserSubSkillLevel.sub_skill_id.in_(sub_skill_ids)
        ).delete(synchronize_session=False)
    db.query(models.SkillEvidence).delete(synchronize_session=False)
    db.query(models.SkillGoal).delete(synchronize_session=False)
    db.execute(text("DELETE FROM skill_tag_associations"))
    db.query(models.UserSkillLevel).delete(synchronize_session=False)
    db.query(models.SkillLevelHistory).delete(synchronize_session=False)
    db.query(models.EducationalLink).update({models.EducationalLink.skill_id: None}, synchronize_session=False)
    db.query(models.SubSkill).delete(synchronize_session=False)
    db.query(models.Skill).delete(synchronize_session=False)
    db.query(models.Category).delete(synchronize_session=False)
    db.commit()


def _apply_bulk_import(data: dict, db: Session, mode: str = "add") -> dict:
    """一括エクスポート形式のデータ（カテゴリ・スキル・サブスキル）をDBに反映する。

    初期セットアップ画面と管理者設定画面の両方から呼ばれる共通ロジック。

    mode:
      "add"         同名のカテゴリ・スキルは新規作成せずスキップする（新規追加のみ行う・デフォルト）
      "replace_all" インポート前に既存のカテゴリ・スキル・サブスキル（とそれに紐づく申告データ）を
                    すべて削除してからインポートする
    """
    if mode == "replace_all":
        _delete_all_catalog_data(db)

    added_cats = added_skills = added_subs = 0
    skipped_cats = skipped_skills = skipped_subs = 0

    # ─ カテゴリのインポート ─
    cat_name_to_id: dict[str, int] = {}
    for c in data.get("categories", []):
        name = (c.get("name") or "").strip()
        if not name:
            continue
        existing = db.query(models.Category).filter(models.Category.name == name).first()
        if existing:
            cat_name_to_id[name] = existing.id
            skipped_cats += 1
        else:
            new_cat = models.Category(
                name=name,
                color=c.get("color") or "#6366f1",
                description=c.get("description") or None,
            )
            db.add(new_cat)
            db.flush()
            cat_name_to_id[name] = new_cat.id
            added_cats += 1

    # ─ スキルのインポート ─
    skill_id_map: dict[int, int] = {}  # 旧id -> 新id
    for sk in data.get("skills", []):
        name = (sk.get("name") or "").strip()
        if not name:
            continue
        cat_name = (sk.get("category_name") or "").strip()
        cat_id = cat_name_to_id.get(cat_name)
        if not cat_id and cat_name:
            # カテゴリが存在しない場合は新規作成
            new_cat = models.Category(name=cat_name, color="#6366f1")
            db.add(new_cat)
            db.flush()
            cat_name_to_id[cat_name] = new_cat.id
            cat_id = new_cat.id

        existing = db.query(models.Skill).filter(models.Skill.name == name).first()
        if existing:
            skill_id_map[sk.get("id", 0)] = existing.id
            skipped_skills += 1
        else:
            new_skill = models.Skill(
                name=name,
                description=sk.get("description") or None,
                tier=sk.get("tier") or "basic",
                category_id=cat_id,
            )
            db.add(new_skill)
            db.flush()
            skill_id_map[sk.get("id", 0)] = new_skill.id
            added_skills += 1

    # ─ サブスキルのインポート ─
    # skill_id_map に存在しないサブスキルは必ずスキップ（name フォールバックは削除）
    # フォールバックを使うと別スキルへの誤挿入が発生するため
    for ss in data.get("sub_skills", []):
        ss_name = (ss.get("name") or "").strip()
        if not ss_name:
            continue
        old_skill_id = ss.get("skill_id", 0)
        new_skill_id = skill_id_map.get(old_skill_id)
        if not new_skill_id:
            skipped_subs += 1
            continue
        existing_ss = db.query(models.SubSkill).filter(
            models.SubSkill.skill_id == new_skill_id,
            models.SubSkill.name == ss_name,
        ).first()
        if existing_ss:
            skipped_subs += 1
        else:
            db.add(models.SubSkill(
                skill_id=new_skill_id,
                name=ss_name,
                description=ss.get("description") or None,
                order_index=ss.get("order_index", 0),
            ))
            added_subs += 1

    db.commit()
    return {
        "added_categories": added_cats,
        "skipped_categories": skipped_cats,
        "added_skills": added_skills,
        "skipped_skills": skipped_skills,
        "added_sub_skills": added_subs,
        "skipped_sub_skills": skipped_subs,
    }


@router.post("/skills/bulk-import")
async def bulk_import(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(default="add"),
    db: Session = Depends(get_db),
):
    """一括エクスポートJSONファイルからカテゴリ・スキル・サブスキルを一括インポート

    mode="add": 既存データに新規分のみ追加（デフォルト）
    mode="replace_all": 既存のカテゴリ・スキル・サブスキル（と紐づく申告データ）を全削除してからインポート
    """
    import json as _json
    auth.require_manager_or_admin(request, db)

    if mode not in ("add", "replace_all"):
        mode = "add"

    content = await file.read()
    try:
        data = _json.loads(content.decode("utf-8-sig"))
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON の解析に失敗しました"}, status_code=400)

    result = _apply_bulk_import(data, db, mode=mode)
    return JSONResponse({"ok": True, "mode": mode, **result})


# ════════════════════════════════════════════════════════════════
# AI スキル要約
# ════════════════════════════════════════════════════════════════

@router.get("/api/users/{target_user_id}/skill-summary")
async def skill_summary_api(
    target_user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """LLM を使ってユーザーのスキルを自然言語で要約する"""
    import os as _os
    from config import get_config as _get_cfg

    user = auth.require_manager_or_admin(request, db)

    cfg = _get_cfg()
    if not cfg.get("ai_summary_enabled", False):
        return JSONResponse(
            {"ok": False, "error": "AI要約機能は現在無効です。管理者設定の「AI設定」から有効にしてください。"},
            status_code=503,
        )

    provider = cfg.get("ai_provider", "anthropic")

    if provider == "anthropic":
        import anthropic as _anthropic
        api_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return JSONResponse(
                {"ok": False, "error": "ANTHROPIC_API_KEY が設定されていません。環境変数に設定してください。"},
                status_code=503,
            )

    target = db.query(models.User).filter(models.User.id == target_user_id).first()
    if not target:
        return JSONResponse({"ok": False, "error": "ユーザーが見つかりません"}, status_code=404)

    # 承認済みスキルを取得
    approved = (
        db.query(models.UserSkillLevel)
        .filter(
            models.UserSkillLevel.user_id == target_user_id,
            models.UserSkillLevel.approval_status == "approved",
        )
        .all()
    )

    if not approved:
        return JSONResponse({"ok": True, "summary": f"{target.display_name or target.username} さんはまだ承認済みスキルがありません。"})

    # カテゴリ別にグループ化
    cat_groups: dict[str, list[str]] = {}
    for usl in approved:
        sk = usl.skill
        if not sk:
            continue
        cat = sk.category.name if sk.category else "その他"
        level_name = models.SKILL_LEVELS.get(usl.level, "未経験")
        cat_groups.setdefault(cat, []).append(f"  - {sk.name}：{level_name}")

    skill_lines = []
    for cat, items in sorted(cat_groups.items()):
        skill_lines.append(f"【{cat}】")
        skill_lines.extend(items)

    skill_text = "\n".join(skill_lines)
    name = target.display_name or target.username

    prompt = f"""\
以下は社内スキルマップシステムに登録された、エンジニア「{name}」さんの承認済みスキル一覧です。
レベルは「未経験／入門／実務可／指導可／エキスパート」の5段階です。

{skill_text}

この情報をもとに、{name} さんの技術的な強みと特徴を、採用・アサイン・育成の参考になるよう200〜300文字程度の自然な日本語で要約してください。
箇条書きは使わず、読みやすい文章形式にしてください。"""

    if provider == "anthropic":
        client = _anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = message.content[0].text.strip()
    else:
        # ローカル LLM（OpenAI 互換 API）
        import httpx as _httpx
        local_url = cfg.get("local_llm_url", "").rstrip("/")
        if not local_url:
            return JSONResponse({"ok": False, "error": "ローカル LLM の URL が設定されていません。"}, status_code=503)
        resp = _httpx.post(
            f"{local_url}/chat/completions",
            json={"model": "local", "messages": [{"role": "user", "content": prompt}], "max_tokens": 512},
            timeout=60,
        )
        resp.raise_for_status()
        summary = resp.json()["choices"][0]["message"]["content"].strip()

    return JSONResponse({"ok": True, "summary": summary, "name": name, "skill_count": len(approved)})
