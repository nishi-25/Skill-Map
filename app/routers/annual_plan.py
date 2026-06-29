import calendar as _calendar
from collections import OrderedDict
from datetime import date as _date, timedelta as _timedelta

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session

import models
import auth
from database import get_db
from template_engine import templates
from routers.business_map import _collect_leaf_subskill_ids, _make_area_visibility_predicate
from routers.groups import _get_managed_groups

router = APIRouter()

PLAN_TYPE_ICONS = {
    "skill": "bi-lightning-charge",
    "sub_skill": "bi-lightning-charge-fill",
    "business_area": "bi-diagram-3",
    "certification": "bi-patch-check",
    "exam": "bi-clipboard-check",
}

_WEEKDAY_LABELS_JA = ["日", "月", "火", "水", "木", "金", "土"]
VIEW_TYPES = ("year", "month", "week", "day")


def _row_to_json_safe(row: dict) -> dict:
    """カレンダーの日付クリックモーダル表示用に、date型を含まないJSON化可能な形へ変換する"""
    return {
        "label": row["label"],
        "plan_type": row["plan_type"],
        "target_date": row["target_date"].strftime("%Y/%m/%d"),
        "note": row.get("note") or "",
        "achieved": row["achieved"],
        "achieved_at": row["achieved_at"].strftime("%Y/%m/%d") if row.get("achieved_at") else None,
        "is_overdue": row["is_overdue"],
        "action_url": row.get("action_url"),
        "edit_form_action": row.get("edit_form_action"),
        "delete_form_action": row.get("delete_form_action"),
        "editable": row.get("editable", False),
    }


def _add_months(d: _date, delta: int) -> _date:
    """日付を月単位でシフトする（日は1日に正規化、年の跨ぎに対応）"""
    total = d.month - 1 + delta
    y = d.year + total // 12
    m = total % 12 + 1
    return _date(y, m, 1)


def _week_start(d: _date) -> _date:
    """指定日を含む週の開始日（日曜）を返す"""
    return d - _timedelta(days=(d.weekday() + 1) % 7)


def _month_grid(year: int, month: int, rows_by_date: dict) -> dict:
    """指定月1ヶ月分のカレンダー（週ごとの日付マトリクス＋当日の計画一覧）を構築する（日曜始まり）"""
    cal = _calendar.Calendar(firstweekday=6)
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        week_cells = []
        for day in week:
            if day == 0:
                week_cells.append(None)
                continue
            day_rows = rows_by_date.get(_date(year, month, day), [])
            week_cells.append({
                "day": day,
                "date_iso": _date(year, month, day).isoformat(),
                "entries": [_row_to_json_safe(r) for r in day_rows],
            })
        weeks.append(week_cells)
    return {"month": month, "label": f"{month}月", "weeks": weeks}


def _build_year_grid(year: int, rows_by_date: dict) -> list:
    """指定年の1〜12月分のカレンダーを構築する"""
    return [_month_grid(year, month, rows_by_date) for month in range(1, 13)]


def _skill_goal_status(db: Session, user: "models.User", goal: "models.SkillGoal"):
    """スキル目標の達成可否・達成日を判定する"""
    usl = db.query(models.UserSkillLevel).filter(
        models.UserSkillLevel.user_id == user.id,
        models.UserSkillLevel.skill_id == goal.skill_id,
    ).first()
    achieved = bool(usl and usl.level >= goal.target_level and usl.approval_status == "approved")
    if not achieved:
        return False, None
    hist = (
        db.query(models.SkillLevelHistory)
        .filter(
            models.SkillLevelHistory.user_id == user.id,
            models.SkillLevelHistory.skill_id == goal.skill_id,
            models.SkillLevelHistory.level >= goal.target_level,
        )
        .order_by(models.SkillLevelHistory.changed_at)
        .first()
    )
    achieved_at = hist.changed_at.date() if hist else (usl.updated_at.date() if usl.updated_at else None)
    return True, achieved_at


def _business_area_status(db: Session, user: "models.User", area: "models.BusinessMapArea", done_sub_ids: set):
    """業務エリア完了の達成可否・達成日を判定する（エリア自身＋配下の全サブスキルが完了済みか）"""
    sub_ids = _collect_leaf_subskill_ids(area)
    achieved = len(sub_ids) > 0 and sub_ids.issubset(done_sub_ids)
    if not achieved:
        return False, None
    rows = db.query(models.UserSubSkillLevel).filter(
        models.UserSubSkillLevel.user_id == user.id,
        models.UserSubSkillLevel.sub_skill_id.in_(sub_ids),
        models.UserSubSkillLevel.can_do == True,
    ).all()
    ts = [r.updated_at for r in rows if r.updated_at]
    return True, (max(ts).date() if ts else None)


def _certification_status(db: Session, user: "models.User", catalog_id: int, target_date: _date):
    """資格取得の達成可否・達成日を判定する（目標日までにカタログに紐づく資格が登録されているか）"""
    certs = (
        db.query(models.Certification)
        .filter(models.Certification.user_id == user.id, models.Certification.catalog_id == catalog_id)
        .all()
    )
    on_time = sorted(
        d for c in certs
        if (d := (c.issued_date or (c.created_at.date() if c.created_at else None))) and d <= target_date
    )
    if not on_time:
        return False, None
    return True, on_time[0]


def _exam_status(db: Session, user: "models.User", exam_id: int, target_date: _date):
    """試験合格の達成可否・達成日を判定する（目標日までに合格しているか。exam+userは一意のため最大1件）"""
    a = (
        db.query(models.ExamAssignment)
        .filter(
            models.ExamAssignment.user_id == user.id,
            models.ExamAssignment.exam_id == exam_id,
            models.ExamAssignment.passed == True,
        )
        .first()
    )
    if not a:
        return False, None
    achieved_at = a.graded_at.date() if a.graded_at else (a.submitted_at.date() if a.submitted_at else None)
    if not achieved_at or achieved_at > target_date:
        return False, None
    return True, achieved_at


def _area_breadcrumb_label(area: "models.BusinessMapArea") -> str:
    chain = []
    node = area
    while node:
        chain.append(node.name)
        node = node.parent
    return " > ".join(reversed(chain))


def _collect_all_rows(db: Session, user: "models.User") -> list:
    """ユーザーの全計画行（スキル目標／サブスキル習得／業務エリア完了／資格取得／試験合格）を年で絞り込まず収集する"""
    today = _date.today()
    done_sub_ts = {
        r.sub_skill_id: r.updated_at
        for r in db.query(models.UserSubSkillLevel).filter(
            models.UserSubSkillLevel.user_id == user.id,
            models.UserSubSkillLevel.can_do == True,
        ).all()
    }
    done_sub_ids = set(done_sub_ts.keys())

    rows = []

    goals = db.query(models.SkillGoal).filter(models.SkillGoal.user_id == user.id).all()
    for g in goals:
        if not g.target_date or not g.skill:
            continue
        achieved, achieved_at = _skill_goal_status(db, user, g)
        rows.append({
            "id": g.id,
            "plan_type": "skill",
            "label": g.skill.name,
            "target_date": g.target_date,
            "note": g.note,
            "achieved": achieved,
            "achieved_at": achieved_at,
            "is_overdue": (not achieved) and g.target_date < today,
            "action_url": f"/skills/{g.skill_id}/declare",
            "delete_form_action": f"/skills/{g.skill_id}/goal/delete",
            "editable": False,  # 目標レベルの変更はスキル申告ページから行う
        })

    items = db.query(models.AnnualPlanItem).filter(models.AnnualPlanItem.user_id == user.id).all()
    for it in items:
        if it.plan_type == "sub_skill":
            ss = it.sub_skill
            if not ss or not ss.skill or ss.skill.is_archived:
                continue
            achieved = ss.id in done_sub_ids
            ts = done_sub_ts.get(ss.id)
            achieved_at = ts.date() if achieved and ts else None
            label = f"{ss.skill.name} › {ss.name}"
            action_url = f"/skills/{ss.skill_id}/declare"
        elif it.plan_type == "business_area":
            area = it.business_map_area
            if not area:
                continue
            achieved, achieved_at = _business_area_status(db, user, area, done_sub_ids)
            label = _area_breadcrumb_label(area)
            action_url = f"/skills/business-map?parent_id={area.parent_id}" if area.parent_id else "/skills/business-map"
        elif it.plan_type == "certification":
            cat = it.certification_catalog
            if not cat:
                continue
            achieved, achieved_at = _certification_status(db, user, cat.id, it.target_date)
            label = cat.name
            action_url = "/certifications"
        elif it.plan_type == "exam":
            exam = it.exam
            if not exam:
                continue
            achieved, achieved_at = _exam_status(db, user, exam.id, it.target_date)
            label = exam.title
            action_url = "/exams/my"
        else:
            continue

        rows.append({
            "id": it.id,
            "plan_type": it.plan_type,
            "label": label,
            "target_date": it.target_date,
            "note": it.note,
            "achieved": achieved,
            "achieved_at": achieved_at,
            "is_overdue": (not achieved) and it.target_date < today,
            "action_url": action_url,
            "edit_form_action": f"/annual-plan/items/{it.id}/edit",
            "delete_form_action": f"/annual-plan/items/{it.id}/delete",
            "editable": True,
        })

    rows.sort(key=lambda r: r["target_date"])
    return rows


@router.get("/annual-plan", response_class=HTMLResponse)
def annual_plan_view(
    request: Request,
    view: str = "",
    date: str = "",
    year: int = 0,
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    today = _date.today()

    anchor = None
    if date.strip():
        try:
            anchor = _date.fromisoformat(date.strip())
        except ValueError:
            anchor = None
    if anchor is None and year:
        anchor = _date(year, 1, 1)
        view = view or "year"
    if anchor is None:
        anchor = today
    if view not in VIEW_TYPES:
        view = "month"

    all_rows = _collect_all_rows(db, user)
    rows_by_date: dict = {}
    for r in all_rows:
        rows_by_date.setdefault(r["target_date"], []).append(r)

    calendar_months = month_weeks = week_days = day_entries = None

    if view == "year":
        calendar_months = _build_year_grid(anchor.year, rows_by_date)
        visible_rows = [r for r in all_rows if r["target_date"].year == anchor.year]
        period_label = f"{anchor.year}年"
        prev_anchor = _date(anchor.year - 1, 1, 1)
        next_anchor = _date(anchor.year + 1, 1, 1)
        is_current = anchor.year == today.year
    elif view == "week":
        wk_start = _week_start(anchor)
        wk_end = wk_start + _timedelta(days=6)
        week_days = []
        for i in range(7):
            d = wk_start + _timedelta(days=i)
            week_days.append({
                "date_iso": d.isoformat(),
                "label": f"{d.month}/{d.day}",
                "weekday": _WEEKDAY_LABELS_JA[(d.weekday() + 1) % 7],
                "is_today": d == today,
                "entries": [_row_to_json_safe(r) for r in rows_by_date.get(d, [])],
            })
        visible_rows = [r for r in all_rows if wk_start <= r["target_date"] <= wk_end]
        if wk_start.year == wk_end.year:
            period_label = f"{wk_start.year}年 {wk_start.month}/{wk_start.day} 〜 {wk_end.month}/{wk_end.day}"
        else:
            period_label = f"{wk_start.year}/{wk_start.month}/{wk_start.day} 〜 {wk_end.year}/{wk_end.month}/{wk_end.day}"
        prev_anchor = anchor - _timedelta(days=7)
        next_anchor = anchor + _timedelta(days=7)
        is_current = wk_start <= today <= wk_end
    elif view == "day":
        day_entries = [_row_to_json_safe(r) for r in rows_by_date.get(anchor, [])]
        visible_rows = rows_by_date.get(anchor, [])
        period_label = f"{anchor.year}年{anchor.month}月{anchor.day}日（{_WEEKDAY_LABELS_JA[(anchor.weekday() + 1) % 7]}）"
        prev_anchor = anchor - _timedelta(days=1)
        next_anchor = anchor + _timedelta(days=1)
        is_current = anchor == today
    else:  # month
        month_weeks = _month_grid(anchor.year, anchor.month, rows_by_date)
        visible_rows = [
            r for r in all_rows
            if r["target_date"].year == anchor.year and r["target_date"].month == anchor.month
        ]
        period_label = f"{anchor.year}年{anchor.month}月"
        prev_anchor = _add_months(anchor, -1)
        next_anchor = _add_months(anchor, 1)
        is_current = anchor.year == today.year and anchor.month == today.month

    total_count = len(visible_rows)
    achieved_count = sum(1 for r in visible_rows if r["achieved"])
    overdue_count = sum(1 for r in visible_rows if r["is_overdue"])

    # ── ピッカー／ドラッグプール用の選択肢 ──
    skills = db.query(models.Skill).filter(models.Skill.is_archived == False).order_by(models.Skill.name).all()
    skills_by_category: "OrderedDict" = OrderedDict()
    for sk in skills:
        cat_name = sk.category.name if sk.category else "未分類"
        if cat_name not in skills_by_category:
            skills_by_category[cat_name] = {"category": sk.category, "skills": []}
        skills_by_category[cat_name]["skills"].append(sk)

    cert_catalog = (
        db.query(models.CertificationCatalog)
        .filter(models.CertificationCatalog.is_archived == False)
        .order_by(models.CertificationCatalog.name)
        .all()
    )
    exams = db.query(models.Exam).filter(models.Exam.is_archived == False).order_by(models.Exam.title).all()

    goals = db.query(models.SkillGoal).filter(models.SkillGoal.user_id == user.id).all()
    skill_goal_by_id = {g.skill_id: {"target_level": g.target_level, "note": g.note or ""} for g in goals}

    is_visible = _make_area_visibility_predicate(user)
    all_areas = db.query(models.BusinessMapArea).order_by(models.BusinessMapArea.name).all()
    area_pool = []
    for a in all_areas:
        if is_visible is not None and not is_visible(a):
            continue
        subs = [
            {"id": a_sk.sub_skill_id, "name": a_sk.sub_skill.name, "skill_name": a_sk.sub_skill.skill.name}
            for a_sk in a.area_sub_skills
            if a_sk.sub_skill and a_sk.sub_skill.skill and not a_sk.sub_skill.skill.is_archived
        ]
        if not subs:
            continue  # サブスキルが直接割り当てられていない「ただの階層」フォルダは一覧に出さない（展開しても何も出ないため）
        area_pool.append({"id": a.id, "label": _area_breadcrumb_label(a), "sub_skills": subs})
    area_pool.sort(key=lambda x: x["label"])
    area_options = [{"id": x["id"], "label": x["label"]} for x in area_pool]

    return templates.TemplateResponse(request, "annual_plan.html", {
        "current_user": user,
        "view": view,
        "anchor_iso": anchor.isoformat(),
        "today_iso": today.isoformat(),
        "today": today,
        "period_label": period_label,
        "is_current": is_current,
        "prev_iso": prev_anchor.isoformat(),
        "next_iso": next_anchor.isoformat(),
        "calendar_months": calendar_months,
        "month_weeks": month_weeks,
        "week_days": week_days,
        "day_entries": day_entries,
        "PLAN_TYPES": models.PLAN_TYPES,
        "PLAN_TYPE_ICONS": PLAN_TYPE_ICONS,
        "total_count": total_count,
        "achieved_count": achieved_count,
        "overdue_count": overdue_count,
        "skills": skills,
        "skills_by_category": skills_by_category,
        "cert_catalog": cert_catalog,
        "exams": exams,
        "area_options": area_options,
        "area_pool": area_pool,
        "skill_goal_by_id": skill_goal_by_id,
    })


@router.post("/annual-plan/items/new")
def annual_plan_item_new(
    request: Request,
    plan_type: str = Form(...),
    target_id: int = Form(...),
    target_date: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    if plan_type not in ("sub_skill", "business_area", "certification", "exam"):
        return RedirectResponse("/annual-plan", status_code=303)
    try:
        td = _date.fromisoformat(target_date.strip())
    except ValueError:
        return RedirectResponse("/annual-plan", status_code=303)

    item = models.AnnualPlanItem(
        user_id=user.id, plan_type=plan_type, target_date=td, note=note.strip() or None,
    )
    if plan_type == "sub_skill":
        item.sub_skill_id = target_id
    elif plan_type == "business_area":
        item.business_map_area_id = target_id
    elif plan_type == "certification":
        item.certification_catalog_id = target_id
    elif plan_type == "exam":
        item.exam_id = target_id
    db.add(item)
    db.commit()
    return RedirectResponse(f"/annual-plan?view=month&date={td.isoformat()}", status_code=303)


@router.post("/annual-plan/items/{item_id}/edit")
def annual_plan_item_edit(
    item_id: int,
    request: Request,
    target_date: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    item = db.query(models.AnnualPlanItem).filter(
        models.AnnualPlanItem.id == item_id,
        models.AnnualPlanItem.user_id == user.id,
    ).first()
    if not item:
        return RedirectResponse("/annual-plan", status_code=303)
    try:
        td = _date.fromisoformat(target_date.strip())
    except ValueError:
        return RedirectResponse(f"/annual-plan?view=month&date={item.target_date.isoformat()}", status_code=303)
    item.target_date = td
    item.note = note.strip() or None
    db.commit()
    return RedirectResponse(f"/annual-plan?view=month&date={td.isoformat()}", status_code=303)


@router.post("/annual-plan/items/{item_id}/delete")
def annual_plan_item_delete(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    item = db.query(models.AnnualPlanItem).filter(
        models.AnnualPlanItem.id == item_id,
        models.AnnualPlanItem.user_id == user.id,
    ).first()
    anchor_date = item.target_date.isoformat() if item else _date.today().isoformat()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(f"/annual-plan?view=month&date={anchor_date}", status_code=303)


@router.get("/annual-plan/team", response_class=HTMLResponse)
def annual_plan_team_view(request: Request, db: Session = Depends(get_db)):
    """Manager/Admin向け: 担当メンバーの年間計画の遅延状況を一覧確認する"""
    user = auth.require_manager_or_admin(request, db)

    is_manager = user.role == "manager"
    if is_manager:
        managed_group_ids = {g.id for g in _get_managed_groups(user, db)}
        managed_member_ids = {
            m.user_id
            for gid in managed_group_ids
            for m in db.query(models.GroupMembership).filter(models.GroupMembership.group_id == gid).all()
        }
        members = (
            db.query(models.User)
            .filter(
                models.User.id.in_(managed_member_ids),
                models.User.is_approved == True,
                models.User.role == "user",
            )
            .order_by(models.User.display_name, models.User.username)
            .all()
        ) if managed_member_ids else []
    else:
        members = (
            db.query(models.User)
            .filter(models.User.is_approved == True, models.User.role == "user")
            .order_by(models.User.display_name, models.User.username)
            .all()
        )

    today = _date.today()
    member_rows = []
    for m in members:
        rows = _collect_all_rows(db, m)
        overdue = sorted((r for r in rows if r["is_overdue"]), key=lambda r: r["target_date"])
        for r in overdue:
            r["days_overdue"] = (today - r["target_date"]).days
        member_rows.append({
            "user": m,
            "total": len(rows),
            "achieved": sum(1 for r in rows if r["achieved"]),
            "overdue_items": overdue,
        })
    member_rows.sort(key=lambda x: -len(x["overdue_items"]))

    return templates.TemplateResponse(request, "annual_plan_team.html", {
        "current_user": user,
        "is_manager": is_manager,
        "member_rows": member_rows,
        "total_overdue": sum(len(x["overdue_items"]) for x in member_rows),
        "PLAN_TYPES": models.PLAN_TYPES,
        "PLAN_TYPE_ICONS": PLAN_TYPE_ICONS,
    })
