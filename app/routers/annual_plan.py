import calendar as _calendar
from datetime import date as _date

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session

import models
import auth
from database import get_db
from template_engine import templates
from routers.business_map import _collect_leaf_subskill_ids, _make_area_visibility_predicate

router = APIRouter()

PLAN_TYPE_ICONS = {
    "skill": "bi-lightning-charge",
    "business_area": "bi-diagram-3",
    "certification": "bi-patch-check",
    "exam": "bi-clipboard-check",
}

_WEEKDAY_LABELS_JA = ["日", "月", "火", "水", "木", "金", "土"]


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


def _build_calendar_months(year: int, all_rows: list) -> list:
    """指定年の1〜12月分のカレンダー（週ごとの日付マトリクス＋当日の計画一覧）を構築する（日曜始まり）"""
    rows_by_date: dict = {}
    for row in all_rows:
        rows_by_date.setdefault(row["target_date"], []).append(row)

    cal = _calendar.Calendar(firstweekday=6)
    months = []
    for month in range(1, 13):
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
                    "entries": [_row_to_json_safe(r) for r in day_rows],
                })
            weeks.append(week_cells)
        months.append({"month": month, "label": f"{month}月", "weeks": weeks})
    return months


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


def _certification_status(db: Session, user: "models.User", catalog_id: int):
    """資格取得の達成可否・達成日を判定する（カタログに紐づく資格登録が存在するか）"""
    cert = (
        db.query(models.Certification)
        .filter(models.Certification.user_id == user.id, models.Certification.catalog_id == catalog_id)
        .order_by(models.Certification.issued_date)
        .first()
    )
    if not cert:
        return False, None
    achieved_at = cert.issued_date or (cert.created_at.date() if cert.created_at else None)
    return True, achieved_at


def _exam_status(db: Session, user: "models.User", exam_id: int):
    """試験合格の達成可否・達成日を判定する"""
    a = (
        db.query(models.ExamAssignment)
        .filter(
            models.ExamAssignment.user_id == user.id,
            models.ExamAssignment.exam_id == exam_id,
            models.ExamAssignment.passed == True,
        )
        .order_by(models.ExamAssignment.graded_at)
        .first()
    )
    if not a:
        return False, None
    achieved_at = a.graded_at.date() if a.graded_at else (a.submitted_at.date() if a.submitted_at else None)
    return True, achieved_at


def _area_breadcrumb_label(area: "models.BusinessMapArea") -> str:
    chain = []
    node = area
    while node:
        chain.append(node.name)
        node = node.parent
    return " > ".join(reversed(chain))


@router.get("/annual-plan", response_class=HTMLResponse)
def annual_plan_view(request: Request, year: int = 0, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    today = _date.today()
    target_year = year or today.year

    done_sub_ids = {
        r.sub_skill_id
        for r in db.query(models.UserSubSkillLevel).filter(
            models.UserSubSkillLevel.user_id == user.id,
            models.UserSubSkillLevel.can_do == True,
        ).all()
    }

    rows_by_type: dict = {k: [] for k in models.PLAN_TYPES}

    # ── スキル習得（既存の SkillGoal を利用） ──
    goals = db.query(models.SkillGoal).filter(models.SkillGoal.user_id == user.id).all()
    for g in goals:
        if not g.target_date or g.target_date.year != target_year or not g.skill:
            continue
        achieved, achieved_at = _skill_goal_status(db, user, g)
        rows_by_type["skill"].append({
            "id": g.id,
            "plan_type": "skill",
            "label": g.skill.name,
            "target_date": g.target_date,
            "month": g.target_date.month,
            "note": g.note,
            "achieved": achieved,
            "achieved_at": achieved_at,
            "is_overdue": (not achieved) and g.target_date < today,
            "action_url": f"/skills/{g.skill_id}/declare",
            "delete_form_action": f"/skills/{g.skill_id}/goal/delete",
            "editable": False,  # 目標レベルの変更はスキル申告ページから行う
        })

    # ── 業務エリア完了／資格取得／試験合格（AnnualPlanItem） ──
    items = db.query(models.AnnualPlanItem).filter(models.AnnualPlanItem.user_id == user.id).all()
    for it in items:
        if it.target_date.year != target_year:
            continue

        if it.plan_type == "business_area":
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
            achieved, achieved_at = _certification_status(db, user, cat.id)
            label = cat.name
            action_url = "/certifications"
        elif it.plan_type == "exam":
            exam = it.exam
            if not exam:
                continue
            achieved, achieved_at = _exam_status(db, user, exam.id)
            label = exam.title
            action_url = "/exams/my"
        else:
            continue

        rows_by_type[it.plan_type].append({
            "id": it.id,
            "plan_type": it.plan_type,
            "label": label,
            "target_date": it.target_date,
            "month": it.target_date.month,
            "note": it.note,
            "achieved": achieved,
            "achieved_at": achieved_at,
            "is_overdue": (not achieved) and it.target_date < today,
            "action_url": action_url,
            "edit_form_action": f"/annual-plan/items/{it.id}/edit",
            "delete_form_action": f"/annual-plan/items/{it.id}/delete",
            "editable": True,
        })

    for k in rows_by_type:
        rows_by_type[k].sort(key=lambda r: r["target_date"])

    all_rows = [r for v in rows_by_type.values() for r in v]
    total_count = len(all_rows)
    achieved_count = sum(1 for r in all_rows if r["achieved"])
    overdue_count = sum(1 for r in all_rows if r["is_overdue"])
    calendar_months = _build_calendar_months(target_year, all_rows)

    # ── ピッカー用の選択肢 ──
    skills = db.query(models.Skill).filter(models.Skill.is_archived == False).order_by(models.Skill.name).all()
    cert_catalog = (
        db.query(models.CertificationCatalog)
        .filter(models.CertificationCatalog.is_archived == False)
        .order_by(models.CertificationCatalog.name)
        .all()
    )
    exams = db.query(models.Exam).filter(models.Exam.is_archived == False).order_by(models.Exam.title).all()

    is_visible = _make_area_visibility_predicate(user)
    all_areas = db.query(models.BusinessMapArea).order_by(models.BusinessMapArea.name).all()
    area_options = [
        {"id": a.id, "label": _area_breadcrumb_label(a)}
        for a in all_areas
        if is_visible is None or is_visible(a)
    ]
    area_options.sort(key=lambda x: x["label"])

    return templates.TemplateResponse(request, "annual_plan.html", {
        "current_user": user,
        "year": target_year,
        "today": today,
        "rows_by_type": rows_by_type,
        "calendar_months": calendar_months,
        "PLAN_TYPES": models.PLAN_TYPES,
        "PLAN_TYPE_ICONS": PLAN_TYPE_ICONS,
        "total_count": total_count,
        "achieved_count": achieved_count,
        "overdue_count": overdue_count,
        "skills": skills,
        "cert_catalog": cert_catalog,
        "exams": exams,
        "area_options": area_options,
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
    if plan_type not in ("business_area", "certification", "exam"):
        return RedirectResponse("/annual-plan", status_code=303)
    try:
        td = _date.fromisoformat(target_date.strip())
    except ValueError:
        return RedirectResponse("/annual-plan", status_code=303)

    item = models.AnnualPlanItem(
        user_id=user.id, plan_type=plan_type, target_date=td, note=note.strip() or None,
    )
    if plan_type == "business_area":
        item.business_map_area_id = target_id
    elif plan_type == "certification":
        item.certification_catalog_id = target_id
    elif plan_type == "exam":
        item.exam_id = target_id
    db.add(item)
    db.commit()
    return RedirectResponse(f"/annual-plan?year={td.year}", status_code=303)


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
        return RedirectResponse(f"/annual-plan?year={item.target_date.year}", status_code=303)
    item.target_date = td
    item.note = note.strip() or None
    db.commit()
    return RedirectResponse(f"/annual-plan?year={td.year}", status_code=303)


@router.post("/annual-plan/items/{item_id}/delete")
def annual_plan_item_delete(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    item = db.query(models.AnnualPlanItem).filter(
        models.AnnualPlanItem.id == item_id,
        models.AnnualPlanItem.user_id == user.id,
    ).first()
    year = item.target_date.year if item else _date.today().year
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(f"/annual-plan?year={year}", status_code=303)
