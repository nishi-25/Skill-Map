import os
import json
import shutil
import uuid as _uuid
from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List

import models
import auth
from database import get_db
from template_engine import templates
from routers.groups import _get_managed_groups

router = APIRouter(prefix="/exams")

EXAM_UPLOAD_DIR = "/app/data/uploads/exams"


# ════════════════════════════════════════════════════════════════
# ヘルパー
# ════════════════════════════════════════════════════════════════

def _save_upload(upload_file: UploadFile):
    os.makedirs(EXAM_UPLOAD_DIR, exist_ok=True)
    original_name = upload_file.filename
    ext = os.path.splitext(original_name)[1] if "." in original_name else ""
    saved_name = f"{_uuid.uuid4()}{ext}"
    save_path = os.path.join(EXAM_UPLOAD_DIR, saved_name)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(upload_file.file, f)
    return save_path, original_name


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _get_assignable_users(user: models.User, db) -> list:
    """試験を割り当てられるユーザー一覧（Managerは管理グループのメンバー、Adminは全承認済みユーザー）"""
    if user.role == "admin":
        return (
            db.query(models.User)
            .filter(models.User.is_approved == True, models.User.id != user.id)
            .order_by(models.User.display_name, models.User.username)
            .all()
        )
    managed_ids = [g.id for g in _get_managed_groups(user, db)]
    if not managed_ids:
        return []
    user_ids = {
        m.user_id for m in db.query(models.GroupMembership)
        .filter(models.GroupMembership.group_id.in_(managed_ids))
        .all()
        if m.user_id != user.id
    }
    if not user_ids:
        return []
    return (
        db.query(models.User)
        .filter(models.User.id.in_(user_ids), models.User.is_approved == True)
        .order_by(models.User.display_name, models.User.username)
        .all()
    )


def _manager_can_access_user(user: models.User, target_user_id: int, db) -> bool:
    if user.role == "admin":
        return True
    if target_user_id == user.id:
        return True
    managed_ids = {g.id for g in _get_managed_groups(user, db)}
    if not managed_ids:
        return False
    return db.query(models.GroupMembership).filter(
        models.GroupMembership.user_id == target_user_id,
        models.GroupMembership.group_id.in_(managed_ids),
    ).first() is not None


def _skills_by_category(db) -> "OrderedDict":
    """カテゴリ別スキル一覧（受験条件の対象スキル選択用）"""
    skills = (
        db.query(models.Skill)
        .filter(models.Skill.is_archived == False)
        .order_by(models.Skill.name)
        .all()
    )
    skills_by_category: "OrderedDict" = OrderedDict()
    for sk in skills:
        cat_name = sk.category.name if sk.category else "未分類"
        if cat_name not in skills_by_category:
            skills_by_category[cat_name] = {"category": sk.category, "skills": []}
        skills_by_category[cat_name]["skills"].append(sk)
    return skills_by_category


def _check_exam_eligibility(user_id: int, exam: "models.Exam", db) -> dict:
    """試験の受験条件（対象スキルの対象ティアの取得率）を判定する"""
    if not exam.target_skill_id or not exam.target_tier or not exam.required_completion_rate:
        return {"applicable": False, "eligible": True}
    subs = db.query(models.SubSkill).filter(
        models.SubSkill.skill_id == exam.target_skill_id,
        models.SubSkill.tier == exam.target_tier,
    ).all()
    if not subs:
        return {"applicable": False, "eligible": True}
    sub_ids = [s.id for s in subs]
    done = db.query(models.UserSubSkillLevel).filter(
        models.UserSubSkillLevel.user_id == user_id,
        models.UserSubSkillLevel.sub_skill_id.in_(sub_ids),
        models.UserSubSkillLevel.can_do == True,
    ).count()
    rate = round(done / len(subs) * 100)
    tier_names = models.get_tier_display_names(db)
    return {
        "applicable": True,
        "eligible": rate >= exam.required_completion_rate,
        "rate": rate,
        "required": exam.required_completion_rate,
        "skill_name": exam.target_skill.name if exam.target_skill else "",
        "tier_name": tier_names.get(exam.target_tier, exam.target_tier),
    }


def _reset_assignment(assignment: models.ExamAssignment, db):
    """再割当時に過去の回答・採点・提出物をリセットする"""
    for ans in list(assignment.answers):
        db.delete(ans)
    for cs in list(assignment.criterion_scores):
        db.delete(cs)
    for ev in list(assignment.evidences):
        if ev.file_path and os.path.exists(ev.file_path):
            os.remove(ev.file_path)
        db.delete(ev)
    assignment.status = "assigned"
    assignment.started_at = None
    assignment.written_submitted_at = None
    assignment.submitted_at = None
    assignment.graded_at = None
    assignment.graded_by = None
    assignment.score = None
    assignment.max_score = None
    assignment.passed = None
    assignment.feedback = None


# ════════════════════════════════════════════════════════════════
# 試験カタログ（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/my", response_class=HTMLResponse)
def exam_my_list(request: Request, db: Session = Depends(get_db)):
    """自分に割り当てられた試験一覧"""
    user = auth.require_approved(request, db)
    assignments = (
        db.query(models.ExamAssignment)
        .join(models.Exam)
        .filter(
            models.ExamAssignment.user_id == user.id,
            models.Exam.is_archived == False,
        )
        .order_by(models.ExamAssignment.assigned_at.desc())
        .all()
    )
    return templates.TemplateResponse(request, "exam_my_list.html", {
        "current_user": user,
        "assignments": assignments,
        "EXAM_ASSIGN_STATUS": models.EXAM_ASSIGN_STATUS,
        "EXAM_ASSIGN_STATUS_COLORS": models.EXAM_ASSIGN_STATUS_COLORS,
    })


@router.get("", response_class=HTMLResponse)
def exams_catalog(request: Request, show_archived: int = 0, db: Session = Depends(get_db)):
    """試験カタログ一覧（Manager/Admin共有）"""
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.Exam)
    if not show_archived:
        q = q.filter(models.Exam.is_archived == False)
    exams = q.order_by(models.Exam.created_at.desc()).all()

    question_counts = dict(
        db.query(models.ExamQuestion.exam_id, func.count(models.ExamQuestion.id))
        .group_by(models.ExamQuestion.exam_id).all()
    )
    criteria_counts = dict(
        db.query(models.ExamCriterion.exam_id, func.count(models.ExamCriterion.id))
        .group_by(models.ExamCriterion.exam_id).all()
    )
    assignment_counts = dict(
        db.query(models.ExamAssignment.exam_id, func.count(models.ExamAssignment.id))
        .group_by(models.ExamAssignment.exam_id).all()
    )

    return templates.TemplateResponse(request, "exams.html", {
        "current_user": user,
        "exams": exams,
        "question_counts": question_counts,
        "criteria_counts": criteria_counts,
        "assignment_counts": assignment_counts,
        "show_archived": show_archived,
    })


@router.get("/new", response_class=HTMLResponse)
def exam_new_form(request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    return templates.TemplateResponse(request, "exam_form.html", {
        "current_user": user,
        "exam": None,
        "skills_by_category": _skills_by_category(db),
        "tier_order": ["basic", "intermediate", "advanced"],
        "tier_names": models.get_tier_display_names(db),
    })


@router.post("/new")
def exam_new(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    has_written: str = Form(None),
    has_practical: str = Form(None),
    time_limit_minutes: str = Form(""),
    pass_score: str = Form(""),
    target_skill_id: str = Form(""),
    target_tier: str = Form("basic"),
    required_completion_rate: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)

    hw = has_written is not None
    hp = has_practical is not None
    if not hw and not hp:
        hw = hp = True

    tsid = int(target_skill_id) if target_skill_id.strip().isdigit() else None
    if tsid is None:
        ttier, trate = None, None
    else:
        ttier = target_tier if target_tier in models.TIER_ORDER else "basic"
        trate = int(required_completion_rate) if required_completion_rate.strip().isdigit() else 80

    exam = models.Exam(
        title=title.strip(),
        description=description.strip() or None,
        exam_type="combined",
        has_written=hw,
        has_practical=hp,
        time_limit_minutes=int(time_limit_minutes) if time_limit_minutes.strip().isdigit() else None,
        pass_score=int(pass_score) if pass_score.strip().isdigit() else None,
        target_skill_id=tsid,
        target_tier=ttier,
        required_completion_rate=trate,
        created_by=user.id,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)

    if hw:
        return RedirectResponse(f"/exams/{exam.id}/questions", status_code=303)
    return RedirectResponse(f"/exams/{exam.id}/criteria", status_code=303)


@router.get("/{exam_id}/edit", response_class=HTMLResponse)
def exam_edit_form(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse("/exams", status_code=303)
    return templates.TemplateResponse(request, "exam_form.html", {
        "current_user": user,
        "exam": exam,
        "skills_by_category": _skills_by_category(db),
        "tier_order": ["basic", "intermediate", "advanced"],
        "tier_names": models.get_tier_display_names(db),
    })


@router.post("/{exam_id}/edit")
def exam_edit(
    exam_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    has_written: str = Form(None),
    has_practical: str = Form(None),
    time_limit_minutes: str = Form(""),
    pass_score: str = Form(""),
    target_skill_id: str = Form(""),
    target_tier: str = Form("basic"),
    required_completion_rate: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse("/exams", status_code=303)

    hw = has_written is not None
    hp = has_practical is not None
    if not hw and not hp:
        hw = hp = True

    tsid = int(target_skill_id) if target_skill_id.strip().isdigit() else None
    if tsid is None:
        ttier, trate = None, None
    else:
        ttier = target_tier if target_tier in models.TIER_ORDER else "basic"
        trate = int(required_completion_rate) if required_completion_rate.strip().isdigit() else 80

    exam.title = title.strip()
    exam.description = description.strip() or None
    exam.has_written = hw
    exam.has_practical = hp
    exam.time_limit_minutes = int(time_limit_minutes) if time_limit_minutes.strip().isdigit() else None
    exam.pass_score = int(pass_score) if pass_score.strip().isdigit() else None
    exam.target_skill_id = tsid
    exam.target_tier = ttier
    exam.required_completion_rate = trate
    db.commit()
    return RedirectResponse("/exams", status_code=303)


@router.post("/{exam_id}/delete")
def exam_delete(exam_id: int, request: Request, db: Session = Depends(get_db)):
    """試験をアーカイブする（割当済みデータ保護のため物理削除しない）"""
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if exam:
        exam.is_archived = True
        db.commit()
    return RedirectResponse("/exams", status_code=303)


@router.post("/{exam_id}/restore")
def exam_restore(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if exam:
        exam.is_archived = False
        db.commit()
    return RedirectResponse("/exams?show_archived=1", status_code=303)


# ════════════════════════════════════════════════════════════════
# 学科試験：問題管理（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/{exam_id}/questions", response_class=HTMLResponse)
def exam_questions_list(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam or not exam.has_written:
        return RedirectResponse("/exams", status_code=303)

    questions = []
    for q in exam.questions:
        choices = json.loads(q.choices or "[]")
        correct_indices = json.loads(q.correct_indices or "[]")
        questions.append({
            "id": q.id,
            "question_text": q.question_text,
            "question_type": q.question_type,
            "choices": choices,
            "correct_indices": correct_indices,
            "points": q.points,
        })

    return templates.TemplateResponse(request, "exam_questions.html", {
        "current_user": user,
        "exam": exam,
        "questions": questions,
        "QUESTION_TYPES": models.QUESTION_TYPES,
    })


def _parse_choices_and_correct(choices_text: str, correct_text: str):
    choices = [c.strip() for c in (choices_text or "").splitlines() if c.strip()]
    correct_indices = []
    for part in (correct_text or "").split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1  # 1-based -> 0-based
            if 0 <= idx < len(choices):
                correct_indices.append(idx)
    correct_indices = sorted(set(correct_indices))
    return choices, correct_indices


@router.post("/{exam_id}/questions/add")
def exam_question_add(
    exam_id: int,
    request: Request,
    question_text: str = Form(...),
    question_type: str = Form(...),
    choices_text: str = Form(...),
    correct_text: str = Form(""),
    points: int = Form(1),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam or not exam.has_written:
        return RedirectResponse("/exams", status_code=303)
    if question_type not in models.QUESTION_TYPES:
        raise HTTPException(status_code=400, detail="invalid question_type")

    choices, correct_indices = _parse_choices_and_correct(choices_text, correct_text)
    if len(choices) < 2 or not correct_indices:
        return RedirectResponse(f"/exams/{exam_id}/questions", status_code=303)

    max_order = db.query(func.max(models.ExamQuestion.order_index)).filter(
        models.ExamQuestion.exam_id == exam_id
    ).scalar() or 0

    q = models.ExamQuestion(
        exam_id=exam_id,
        question_text=question_text.strip(),
        question_type=question_type,
        choices=json.dumps(choices, ensure_ascii=False),
        correct_indices=json.dumps(correct_indices),
        points=max(1, points),
        order_index=max_order + 1,
    )
    db.add(q)
    db.commit()
    return RedirectResponse(f"/exams/{exam_id}/questions", status_code=303)


@router.post("/{exam_id}/questions/{question_id}/edit")
def exam_question_edit(
    exam_id: int,
    question_id: int,
    request: Request,
    question_text: str = Form(...),
    question_type: str = Form(...),
    choices_text: str = Form(...),
    correct_text: str = Form(""),
    points: int = Form(1),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.ExamQuestion).filter(
        models.ExamQuestion.id == question_id, models.ExamQuestion.exam_id == exam_id
    ).first()
    if not q:
        return RedirectResponse(f"/exams/{exam_id}/questions", status_code=303)
    if question_type not in models.QUESTION_TYPES:
        raise HTTPException(status_code=400, detail="invalid question_type")

    choices, correct_indices = _parse_choices_and_correct(choices_text, correct_text)
    if len(choices) < 2 or not correct_indices:
        return RedirectResponse(f"/exams/{exam_id}/questions", status_code=303)

    q.question_text = question_text.strip()
    q.question_type = question_type
    q.choices = json.dumps(choices, ensure_ascii=False)
    q.correct_indices = json.dumps(correct_indices)
    q.points = max(1, points)
    db.commit()
    return RedirectResponse(f"/exams/{exam_id}/questions", status_code=303)


@router.post("/{exam_id}/questions/{question_id}/delete")
def exam_question_delete(exam_id: int, question_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    q = db.query(models.ExamQuestion).filter(
        models.ExamQuestion.id == question_id, models.ExamQuestion.exam_id == exam_id
    ).first()
    if q:
        db.delete(q)
        db.commit()
    return RedirectResponse(f"/exams/{exam_id}/questions", status_code=303)


# ════════════════════════════════════════════════════════════════
# 実技試験：評価項目管理（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/{exam_id}/criteria", response_class=HTMLResponse)
def exam_criteria_list(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam or not exam.has_practical:
        return RedirectResponse("/exams", status_code=303)

    return templates.TemplateResponse(request, "exam_criteria.html", {
        "current_user": user,
        "exam": exam,
        "criteria": exam.criteria,
    })


@router.post("/{exam_id}/criteria/add")
def exam_criterion_add(
    exam_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    max_score: int = Form(10),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam or not exam.has_practical:
        return RedirectResponse("/exams", status_code=303)

    max_order = db.query(func.max(models.ExamCriterion.order_index)).filter(
        models.ExamCriterion.exam_id == exam_id
    ).scalar() or 0

    c = models.ExamCriterion(
        exam_id=exam_id,
        title=title.strip(),
        description=description.strip() or None,
        max_score=max(1, max_score),
        order_index=max_order + 1,
    )
    db.add(c)
    db.commit()
    return RedirectResponse(f"/exams/{exam_id}/criteria", status_code=303)


@router.post("/{exam_id}/criteria/{criterion_id}/edit")
def exam_criterion_edit(
    exam_id: int,
    criterion_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    max_score: int = Form(10),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    c = db.query(models.ExamCriterion).filter(
        models.ExamCriterion.id == criterion_id, models.ExamCriterion.exam_id == exam_id
    ).first()
    if not c:
        return RedirectResponse(f"/exams/{exam_id}/criteria", status_code=303)
    c.title = title.strip()
    c.description = description.strip() or None
    c.max_score = max(1, max_score)
    db.commit()
    return RedirectResponse(f"/exams/{exam_id}/criteria", status_code=303)


@router.post("/{exam_id}/criteria/{criterion_id}/delete")
def exam_criterion_delete(exam_id: int, criterion_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    c = db.query(models.ExamCriterion).filter(
        models.ExamCriterion.id == criterion_id, models.ExamCriterion.exam_id == exam_id
    ).first()
    if c:
        db.delete(c)
        db.commit()
    return RedirectResponse(f"/exams/{exam_id}/criteria", status_code=303)


# ════════════════════════════════════════════════════════════════
# 割当（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/{exam_id}/assign", response_class=HTMLResponse)
def exam_assign_form(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse("/exams", status_code=303)

    candidates = _get_assignable_users(user, db)
    assigned_ids = {
        a.user_id for a in db.query(models.ExamAssignment).filter(models.ExamAssignment.exam_id == exam_id).all()
    }
    eligibility_map = {u.id: _check_exam_eligibility(u.id, exam, db) for u in candidates}

    return templates.TemplateResponse(request, "exam_assign.html", {
        "current_user": user,
        "exam": exam,
        "candidates": candidates,
        "assigned_ids": assigned_ids,
        "eligibility_map": eligibility_map,
    })


@router.post("/{exam_id}/assign")
def exam_assign(
    exam_id: int,
    request: Request,
    user_ids: List[int] = Form(default=[]),
    due_date: str = Form(""),
    db: Session = Depends(get_db),
):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse("/exams", status_code=303)

    candidates = {u.id for u in _get_assignable_users(user, db)}
    due = _parse_date(due_date)

    for target_id in user_ids:
        if target_id not in candidates:
            continue
        existing = db.query(models.ExamAssignment).filter(
            models.ExamAssignment.exam_id == exam_id, models.ExamAssignment.user_id == target_id
        ).first()
        if existing:
            _reset_assignment(existing, db)
            existing.assigned_by = user.id
            existing.assigned_at = func.now()
            existing.due_date = due
        else:
            db.add(models.ExamAssignment(
                exam_id=exam_id,
                user_id=target_id,
                assigned_by=user.id,
                due_date=due,
            ))
    db.commit()
    return RedirectResponse(f"/exams/{exam_id}/results", status_code=303)


# ════════════════════════════════════════════════════════════════
# 結果・採点（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/{exam_id}/results", response_class=HTMLResponse)
def exam_results_list(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    exam = db.query(models.Exam).filter(models.Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse("/exams", status_code=303)

    assignments = (
        db.query(models.ExamAssignment)
        .filter(models.ExamAssignment.exam_id == exam_id)
        .order_by(models.ExamAssignment.assigned_at.desc())
        .all()
    )
    if user.role == "manager":
        assignments = [a for a in assignments if _manager_can_access_user(user, a.user_id, db)]

    return templates.TemplateResponse(request, "exam_results.html", {
        "current_user": user,
        "exam": exam,
        "assignments": assignments,
        "EXAM_ASSIGN_STATUS": models.EXAM_ASSIGN_STATUS,
        "EXAM_ASSIGN_STATUS_COLORS": models.EXAM_ASSIGN_STATUS_COLORS,
    })


@router.get("/results/{assignment_id}", response_class=HTMLResponse)
def exam_result_detail(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    assignment = db.query(models.ExamAssignment).filter(models.ExamAssignment.id == assignment_id).first()
    if not assignment:
        return RedirectResponse("/exams", status_code=303)
    if not _manager_can_access_user(user, assignment.user_id, db):
        raise HTTPException(status_code=403)

    exam = assignment.exam
    question_details = []
    if exam.has_written and exam.questions:
        answers_by_qid = {a.question_id: a for a in assignment.answers}
        for q in exam.questions:
            ans = answers_by_qid.get(q.id)
            question_details.append({
                "question": q,
                "choices": json.loads(q.choices or "[]"),
                "correct_indices": json.loads(q.correct_indices or "[]"),
                "selected_indices": json.loads(ans.selected_indices or "[]") if ans else [],
                "is_correct": ans.is_correct if ans else None,
                "points_awarded": ans.points_awarded if ans else 0,
            })

    criterion_details = []
    if exam.has_practical and exam.criteria:
        scores_by_cid = {cs.criterion_id: cs for cs in assignment.criterion_scores}
        for c in exam.criteria:
            cs = scores_by_cid.get(c.id)
            criterion_details.append({
                "criterion": c,
                "score": cs.score if cs else None,
                "comment": cs.comment if cs else "",
            })

    return templates.TemplateResponse(request, "exam_grade.html", {
        "current_user": user,
        "exam": exam,
        "assignment": assignment,
        "question_details": question_details,
        "criterion_details": criterion_details,
        "EXAM_ASSIGN_STATUS": models.EXAM_ASSIGN_STATUS,
        "EXAM_ASSIGN_STATUS_COLORS": models.EXAM_ASSIGN_STATUS_COLORS,
    })


@router.post("/results/{assignment_id}/grade")
async def exam_result_grade(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_manager_or_admin(request, db)
    assignment = db.query(models.ExamAssignment).filter(models.ExamAssignment.id == assignment_id).first()
    if not assignment:
        return RedirectResponse("/exams", status_code=303)
    if not _manager_can_access_user(user, assignment.user_id, db):
        raise HTTPException(status_code=403)

    exam = assignment.exam
    form = await request.form()
    feedback = (form.get("feedback") or "").strip()

    if exam.has_practical and exam.criteria:
        scores_by_cid = {cs.criterion_id: cs for cs in assignment.criterion_scores}
        criteria_total = 0.0
        criteria_max = 0.0
        for c in exam.criteria:
            raw = form.get(f"score_{c.id}", "")
            comment = (form.get(f"comment_{c.id}") or "").strip()
            try:
                score_val = float(raw)
            except ValueError:
                score_val = 0.0
            score_val = max(0.0, min(float(c.max_score), score_val))

            cs = scores_by_cid.get(c.id)
            if cs:
                cs.score = score_val
                cs.comment = comment or None
            else:
                db.add(models.ExamCriterionScore(
                    assignment_id=assignment.id,
                    criterion_id=c.id,
                    score=score_val,
                    comment=comment or None,
                ))
            criteria_total += score_val
            criteria_max += c.max_score

        written_total = sum(a.points_awarded for a in assignment.answers)
        written_max = sum(q.points for q in exam.questions)

        total_score = criteria_total + written_total
        total_max = criteria_max + written_max
        assignment.score = total_score
        assignment.max_score = total_max
        assignment.passed = (total_score / total_max * 100 >= exam.pass_score) if exam.pass_score and total_max else None
        assignment.status = "graded"
        assignment.graded_at = func.now()
        assignment.graded_by = user.id

    assignment.feedback = feedback or None
    db.commit()
    return RedirectResponse(f"/exams/{exam.id}/results", status_code=303)


# ════════════════════════════════════════════════════════════════
# 受験（一般ユーザー）
# ════════════════════════════════════════════════════════════════

@router.get("/my/{assignment_id}", response_class=HTMLResponse)
def exam_take(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    assignment = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.id == assignment_id, models.ExamAssignment.user_id == user.id
    ).first()
    if not assignment:
        return RedirectResponse("/exams/my", status_code=303)

    exam = assignment.exam

    eligibility = _check_exam_eligibility(user.id, exam, db)
    eligibility_blocked = False
    if assignment.status == "assigned":
        if eligibility["applicable"] and not eligibility["eligible"]:
            eligibility_blocked = True
        else:
            assignment.status = "in_progress"
            assignment.started_at = func.now()
            db.commit()
            db.refresh(assignment)

    show_written = exam.has_written and bool(exam.questions)
    show_practical = exam.has_practical and bool(exam.criteria)

    # 学科・実技の両方がある試験は、学科を提出してから実技に進む2段階フローとする
    if assignment.status == "in_progress" and show_written and show_practical:
        stage = "practical" if assignment.written_submitted_at else "written"
    else:
        stage = "all"

    questions = []
    if show_written:
        answers_by_qid = {a.question_id: a for a in assignment.answers}
        for q in exam.questions:
            ans = answers_by_qid.get(q.id)
            questions.append({
                "id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "choices": json.loads(q.choices or "[]"),
                "correct_indices": json.loads(q.correct_indices or "[]"),
                "selected_indices": json.loads(ans.selected_indices or "[]") if ans else [],
                "is_correct": ans.is_correct if ans else None,
                "points": q.points,
                "points_awarded": ans.points_awarded if ans else 0,
            })

    criteria = []
    criterion_details = []
    evidences = []
    if show_practical:
        criteria = exam.criteria
        evidences = assignment.evidences
        scores_by_cid = {cs.criterion_id: cs for cs in assignment.criterion_scores}
        criterion_details = [{
            "criterion": c,
            "score": scores_by_cid.get(c.id).score if scores_by_cid.get(c.id) else None,
            "comment": scores_by_cid.get(c.id).comment if scores_by_cid.get(c.id) else "",
        } for c in exam.criteria]

    remaining_seconds = None
    if (
        exam.time_limit_minutes and show_written and assignment.status == "in_progress"
        and assignment.written_submitted_at is None and assignment.started_at
    ):
        elapsed = (datetime.utcnow() - assignment.started_at).total_seconds()
        remaining_seconds = max(0, int(exam.time_limit_minutes * 60 - elapsed))

    proceed_to_practical = request.query_params.get("proceed") == "1" or bool(evidences)

    return templates.TemplateResponse(request, "exam_take.html", {
        "current_user": user,
        "exam": exam,
        "assignment": assignment,
        "stage": stage,
        "questions": questions,
        "criteria": criteria,
        "criterion_details": criterion_details,
        "evidences": evidences,
        "remaining_seconds": remaining_seconds,
        "eligibility": eligibility,
        "eligibility_blocked": eligibility_blocked,
        "proceed_to_practical": proceed_to_practical,
        "EXAM_ASSIGN_STATUS": models.EXAM_ASSIGN_STATUS,
        "EXAM_ASSIGN_STATUS_COLORS": models.EXAM_ASSIGN_STATUS_COLORS,
    })


def _grade_written_answers(exam, assignment, form, db):
    for q in exam.questions:
        selected = sorted(int(v) for v in form.getlist(f"q_{q.id}") if str(v).isdigit())
        correct = sorted(json.loads(q.correct_indices or "[]"))
        is_correct = selected == correct
        points_awarded = q.points if is_correct else 0

        existing = db.query(models.ExamAnswer).filter(
            models.ExamAnswer.assignment_id == assignment.id,
            models.ExamAnswer.question_id == q.id,
        ).first()
        if existing:
            existing.selected_indices = json.dumps(selected)
            existing.is_correct = is_correct
            existing.points_awarded = points_awarded
        else:
            db.add(models.ExamAnswer(
                assignment_id=assignment.id,
                question_id=q.id,
                selected_indices=json.dumps(selected),
                is_correct=is_correct,
                points_awarded=points_awarded,
            ))


@router.post("/my/{assignment_id}/submit-written")
async def exam_submit_written(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    """学科・実技の両方がある試験で、学科部分のみを提出する（採点後、実技に進む）"""
    user = auth.require_approved(request, db)
    assignment = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.id == assignment_id, models.ExamAssignment.user_id == user.id
    ).first()
    if not assignment:
        return RedirectResponse("/exams/my", status_code=303)

    exam = assignment.exam
    if assignment.status in ("submitted", "graded") or assignment.written_submitted_at is not None:
        return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)

    form = await request.form()
    _grade_written_answers(exam, assignment, form, db)
    assignment.written_submitted_at = func.now()
    db.commit()
    return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)


@router.post("/my/{assignment_id}/submit")
async def exam_submit(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    assignment = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.id == assignment_id, models.ExamAssignment.user_id == user.id
    ).first()
    if not assignment:
        return RedirectResponse("/exams/my", status_code=303)

    exam = assignment.exam
    if assignment.status in ("submitted", "graded"):
        return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)

    has_practical = exam.has_practical and bool(exam.criteria)

    # 実技（評価項目）がある場合は提出物が1件以上必要
    if has_practical and not assignment.evidences:
        return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)

    if assignment.written_submitted_at is None:
        form = await request.form()
        _grade_written_answers(exam, assignment, form, db)
        assignment.written_submitted_at = func.now()
        db.flush()

    assignment.submitted_at = func.now()
    if has_practical:
        # 実技の採点待ち（Managerがルーブリック採点後に学科分も含めた合計が確定する）
        assignment.status = "submitted"
    else:
        total_score = sum(a.points_awarded for a in assignment.answers)
        total_max = sum(q.points for q in exam.questions)
        assignment.score = total_score
        assignment.max_score = total_max
        assignment.passed = (total_score / total_max * 100 >= exam.pass_score) if exam.pass_score and total_max else None
        assignment.status = "graded"
        assignment.graded_at = func.now()

    db.commit()
    return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)


# ════════════════════════════════════════════════════════════════
# 実技試験：提出物（一般ユーザー）
# ════════════════════════════════════════════════════════════════

@router.post("/my/{assignment_id}/evidence/add")
async def exam_evidence_add(
    assignment_id: int,
    request: Request,
    title: str = Form(""),
    upload_file: UploadFile = File(default=None),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    assignment = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.id == assignment_id, models.ExamAssignment.user_id == user.id
    ).first()
    if not assignment or not (assignment.exam.has_practical and assignment.exam.criteria):
        return RedirectResponse("/exams/my", status_code=303)
    if assignment.status == "graded":
        return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)

    if upload_file and upload_file.filename:
        save_path, original_name = _save_upload(upload_file)
        ev = models.ExamSubmissionEvidence(
            assignment_id=assignment.id,
            evidence_type="file",
            title=title.strip() or original_name,
            content="",
            file_path=save_path,
            original_filename=original_name,
        )
        db.add(ev)
        db.commit()

    return RedirectResponse(f"/exams/my/{assignment_id}?proceed=1", status_code=303)


@router.post("/my/{assignment_id}/evidence/{evidence_id}/delete")
def exam_evidence_delete(assignment_id: int, evidence_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    assignment = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.id == assignment_id, models.ExamAssignment.user_id == user.id
    ).first()
    if not assignment or assignment.status == "graded":
        return RedirectResponse(f"/exams/my/{assignment_id}", status_code=303)

    ev = db.query(models.ExamSubmissionEvidence).filter(
        models.ExamSubmissionEvidence.id == evidence_id,
        models.ExamSubmissionEvidence.assignment_id == assignment.id,
    ).first()
    if ev:
        if ev.file_path and os.path.exists(ev.file_path):
            os.remove(ev.file_path)
        db.delete(ev)
        db.commit()
    return RedirectResponse(f"/exams/my/{assignment_id}?proceed=1", status_code=303)


@router.get("/my/{assignment_id}/evidence/{evidence_id}/download")
def exam_evidence_download(assignment_id: int, evidence_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    ev = db.query(models.ExamSubmissionEvidence).filter(
        models.ExamSubmissionEvidence.id == evidence_id,
        models.ExamSubmissionEvidence.assignment_id == assignment_id,
    ).first()
    if not ev or not ev.file_path or not os.path.exists(ev.file_path):
        raise HTTPException(status_code=404)

    assignment = ev.assignment
    if assignment.user_id != user.id and not _manager_can_access_user(user, assignment.user_id, db):
        raise HTTPException(status_code=403)

    return FileResponse(ev.file_path, filename=ev.original_filename or "evidence")
