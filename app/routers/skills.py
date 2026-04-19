from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
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


# ════════════════════════════════════════════════════════════════
# スキルカタログ管理（Admin / Manager のみ）
# ════════════════════════════════════════════════════════════════

@router.get("/skills/catalog", response_class=HTMLResponse)
def catalog_list(
    request: Request,
    category_id: int = 0,
    tier: str = "",
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.Skill)
    if category_id:
        q = q.filter(models.Skill.category_id == category_id)
    if tier:
        q = q.filter(models.Skill.tier == tier)
    skills = q.order_by(models.Skill.tier, models.Skill.name).all()
    categories = db.query(models.Category).order_by(models.Category.name).all()
    return templates.TemplateResponse(request, "skill_catalog.html", {
        "current_user": user, "skills": skills,
        "categories": categories,
        "sel_category": category_id, "sel_tier": tier,
    })


@router.get("/skills/catalog/new", response_class=HTMLResponse)
def catalog_new_get(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    return templates.TemplateResponse(request, "skill_catalog_form.html", {
        "current_user": user, "skill": None,
        "categories": categories, "error": None,
    })


@router.post("/skills/catalog/new")
def catalog_new_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(0),
    tier: str = Form("basic"),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    db.add(models.Skill(
        name=name,
        description=description or None,
        category_id=category_id or None,
        tier=tier,
        created_by=user.id,
    ))
    db.commit()
    return RedirectResponse("/skills/catalog", status_code=303)


@router.get("/skills/catalog/{skill_id}/edit", response_class=HTMLResponse)
def catalog_edit_get(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        return RedirectResponse("/skills/catalog", status_code=303)
    categories = db.query(models.Category).order_by(models.Category.name).all()
    return templates.TemplateResponse(request, "skill_catalog_form.html", {
        "current_user": user, "skill": skill,
        "categories": categories, "error": None,
    })


@router.post("/skills/catalog/{skill_id}/edit")
def catalog_edit_post(
    skill_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(0),
    tier: str = Form("basic"),
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
    db.commit()
    return RedirectResponse("/skills/catalog", status_code=303)


@router.post("/skills/catalog/{skill_id}/delete")
def catalog_delete(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if skill:
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
    tier_order = ["beginner", "basic", "intermediate", "advanced"]
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
    tier_beginner: str = Form(""),
    tier_basic: str = Form(""),
    tier_intermediate: str = Form(""),
    tier_advanced: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    mapping = {
        "beginner": tier_beginner.strip(),
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
    tier_order = ["beginner", "basic", "intermediate", "advanced"]
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
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)

    # カスタムティア名
    tier_names = models.get_tier_display_names(db)
    tier_order = ["beginner", "basic", "intermediate", "advanced"]

    # ユーザーの所属グループ
    my_groups = (
        db.query(models.Group)
        .join(models.GroupMembership)
        .filter(models.GroupMembership.user_id == user.id)
        .order_by(models.Group.name)
        .all()
    )

    # グループでスキル絞込み
    group_skill_ids = None
    if group_id:
        sel_group = db.query(models.Group).filter(models.Group.id == group_id).first()
        if sel_group:
            group_skill_ids = _get_all_group_skill_ids(sel_group)

    # 全カタログ取得（tierフィルタなし → 概要計算用）
    q_all = db.query(models.Skill)
    if category_id:
        q_all = q_all.filter(models.Skill.category_id == category_id)
    all_catalog = q_all.order_by(models.Skill.tier, models.Skill.name).all()
    if group_skill_ids is not None:
        all_catalog = [sk for sk in all_catalog if sk.id in group_skill_ids]

    # 自分のスキルレベル
    my_levels = (
        db.query(models.UserSkillLevel)
        .filter(models.UserSkillLevel.user_id == user.id)
        .all()
    )
    my_level_map: dict[int, int] = {
        sl.skill_id: sl.level for sl in my_levels
    }
    my_approval_map: dict[int, str] = {
        sl.skill_id: sl.approval_status for sl in my_levels
    }
    my_approver_map: dict[int, int] = {
        sl.skill_id: sl.approver_id for sl in my_levels if sl.approver_id
    }

    # ── ティア概要（overview_mode） ──
    overview_mode = not tier
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

    # 承認者候補: 自分以外の承認済みユーザー
    approvers = (
        db.query(models.User)
        .filter(models.User.is_approved == True, models.User.id != user.id)
        .order_by(models.User.display_name, models.User.username)
        .all()
    )

    by_tier: dict[str, list] = defaultdict(list)
    for sk in catalog:
        by_tier[sk.tier].append(sk)

    return templates.TemplateResponse(request, "skills.html", {
        "current_user": user,
        "by_tier": by_tier,
        "tier_order": tier_order,
        "my_level_map": my_level_map,
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
    """承認者として自分に割り当てられた承認依頼一覧（Admin/Managerのみ）"""
    user = auth.require_manager_or_admin(request, db)
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
    return templates.TemplateResponse(request, "approvals.html", {
        "current_user": user,
        "pending": pending,
        "history": history,
    })


@router.post("/approvals/{record_id}/approve")
def approve_skill(
    record_id: int,
    request: Request,
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    """スキルレベルを承認する"""
    user = auth.require_manager_or_admin(request, db)
    record = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id == record_id,
        models.UserSkillLevel.approver_id == user.id,
        models.UserSkillLevel.approval_status == "pending",
    ).first()
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
    """スキルレベルを差し戻す"""
    user = auth.require_manager_or_admin(request, db)
    record = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.id == record_id,
        models.UserSkillLevel.approver_id == user.id,
        models.UserSkillLevel.approval_status == "pending",
    ).first()
    if record:
        record.approval_status = "rejected"
        record.approved_at = func.now()
        record.approver_comment = comment or None
        db.commit()
    return RedirectResponse("/approvals", status_code=303)


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
    return templates.TemplateResponse(request, "my_approvals.html", {
        "current_user": user,
        "records": records,
    })


# ════════════════════════════════════════════════════════════════
# JSON API（AJAX 用）
# ════════════════════════════════════════════════════════════════

from fastapi.responses import JSONResponse

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
    cat_user_avg: dict[str, dict[str, float]] = {}  # {cat_name: {user_name: avg}}
    cat_names_ordered = []
    for cat in categories:
        cat_skills = [sk for sk in skills if sk.category_id == cat.id]
        if not cat_skills:
            continue
        cat_names_ordered.append(cat.name)
        cat_user_avg[cat.name] = {}
        for u in users:
            vals = [level_map.get((u.id, sk.id), 0) for sk in cat_skills]
            filled = [v for v in vals if v > 0]
            cat_user_avg[cat.name][u.display_name or u.username] = round(
                sum(filled) / len(filled), 2
            ) if filled else 0.0

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
        "level_dist": level_dist,
        "growth_trend": growth_trend,
        "user_avg_ranking": user_avg_ranking,
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
