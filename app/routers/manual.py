from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

import auth
from database import get_db
from template_engine import templates
from manual_search_index import PAGES_BY_KEY, build_toc_groups, get_prev_next, search_manual

router = APIRouter(prefix="/manual", tags=["Manual"])
search_router = APIRouter(tags=["Manual"])


def _manual_ctx(request: Request, db: Session, key: str) -> dict:
    """マニュアルページ共通のテンプレートコンテキスト(current_user・TOC・前へ/次へ・最終更新日)を組み立てる"""
    current_user = auth.get_current_user(request, db)
    prev_page, next_page = get_prev_next(key)
    return {
        "current_user": current_user,
        "toc_groups": build_toc_groups(current_user),
        "prev_page": prev_page,
        "next_page": next_page,
        "current_page": PAGES_BY_KEY.get(key),
    }


def _page(request: Request, db: Session, key: str, template: str):
    return templates.TemplateResponse(request, template, _manual_ctx(request, db, key))


# ─── はじめに ──────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
def manual_index(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "index", "manual/index.html")


@router.get("/quickstart", response_class=HTMLResponse)
def manual_quickstart(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "quickstart", "manual/quickstart.html")


@router.get("/roles", response_class=HTMLResponse)
def manual_roles(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "roles", "manual/roles.html")


@router.get("/login", response_class=HTMLResponse)
def manual_login(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "login", "manual/login.html")


# ─── 全ユーザー共通 ──────────────────────────────────────────────
@router.get("/dashboard", response_class=HTMLResponse)
def manual_dashboard(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "dashboard", "manual/dashboard.html")


@router.get("/profile", response_class=HTMLResponse)
def manual_profile(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "profile", "manual/profile.html")


@router.get("/search", response_class=HTMLResponse)
def manual_search_feature(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "search", "manual/search.html")


@router.get("/tickets", response_class=HTMLResponse)
def manual_tickets(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "tickets", "manual/tickets.html")


@router.get("/wiki", response_class=HTMLResponse)
def manual_wiki(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "wiki", "manual/wiki.html")


# ─── User機能 ──────────────────────────────────────────────────
@router.get("/skillmap", response_class=HTMLResponse)
def manual_skillmap(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "skillmap", "manual/skillmap.html")


@router.get("/business-map", response_class=HTMLResponse)
def manual_business_map(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "business-map", "manual/business-map.html")


@router.get("/my-approvals", response_class=HTMLResponse)
def manual_my_approvals(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "my-approvals", "manual/my-approvals.html")


@router.get("/timeline", response_class=HTMLResponse)
def manual_timeline(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "timeline", "manual/timeline.html")


@router.get("/education", response_class=HTMLResponse)
def manual_education(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "education", "manual/education.html")


@router.get("/certifications", response_class=HTMLResponse)
def manual_certifications(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "certifications", "manual/certifications.html")


@router.get("/exams-my", response_class=HTMLResponse)
def manual_exams_my(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "exams-my", "manual/exams-my.html")


@router.get("/annual-plan", response_class=HTMLResponse)
def manual_annual_plan(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "annual-plan", "manual/annual-plan.html")


# ─── Manager以上 ──────────────────────────────────────────────
@router.get("/annual-plan-team", response_class=HTMLResponse)
def manual_annual_plan_team_redirect(request: Request, db: Session = Depends(get_db)):
    from fastapi.responses import RedirectResponse as _Redirect
    return _Redirect("/manual/annual-plan-members", status_code=303)


@router.get("/annual-plan-members", response_class=HTMLResponse)
def manual_annual_plan_members(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "annual-plan-members", "manual/annual-plan-members.html")


@router.get("/approvals", response_class=HTMLResponse)
def manual_approvals(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "approvals", "manual/approvals.html")


@router.get("/matrix", response_class=HTMLResponse)
def manual_matrix(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "matrix", "manual/matrix.html")


@router.get("/catalog", response_class=HTMLResponse)
def manual_catalog(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "catalog", "manual/catalog.html")


@router.get("/categories", response_class=HTMLResponse)
def manual_categories(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "categories", "manual/categories.html")


@router.get("/groups", response_class=HTMLResponse)
def manual_groups(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "groups", "manual/groups.html")


@router.get("/education-mgmt", response_class=HTMLResponse)
def manual_education_mgmt(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "education-mgmt", "manual/education-mgmt.html")


@router.get("/business-map-manage", response_class=HTMLResponse)
def manual_business_map_manage(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "business-map-manage", "manual/business-map-manage.html")


@router.get("/certifications-matrix", response_class=HTMLResponse)
def manual_certifications_matrix(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "certifications-matrix", "manual/certifications-matrix.html")


@router.get("/certifications-catalog", response_class=HTMLResponse)
def manual_certifications_catalog(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "certifications-catalog", "manual/certifications-catalog.html")


@router.get("/exams-management", response_class=HTMLResponse)
def manual_exams_management(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "exams-management", "manual/exams-management.html")


# ─── ユースケース ──────────────────────────────────────────────
@router.get("/usecase/user", response_class=HTMLResponse)
def manual_usecase_user(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "usecase-user", "manual/usecase_user.html")


@router.get("/usecase/manager", response_class=HTMLResponse)
def manual_usecase_manager(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "usecase-manager", "manual/usecase_manager.html")


# ─── APIリファレンス・FAQ ────────────────────────────────────────
@router.get("/api", response_class=HTMLResponse)
def manual_api(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "api", "manual/api.html")


@router.get("/faq", response_class=HTMLResponse)
def manual_faq(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, "faq", "manual/faq.html")


# ─── Admin専用(ログイン中のAdminロールのみ閲覧可) ──────────────────────
@router.get("/admin", response_class=HTMLResponse)
def manual_admin_index(request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    return _page(request, db, "admin-index", "manual/admin/index.html")


@router.get("/admin/users", response_class=HTMLResponse)
def manual_admin_users(request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    return _page(request, db, "admin-users", "manual/admin/users.html")


@router.get("/admin/mail", response_class=HTMLResponse)
def manual_admin_mail(request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    return _page(request, db, "admin-mail", "manual/admin/mail.html")


@router.get("/admin/maintenance", response_class=HTMLResponse)
def manual_admin_maintenance(request: Request, db: Session = Depends(get_db)):
    auth.require_admin(request, db)
    return _page(request, db, "admin-maintenance", "manual/admin/maintenance.html")


# ─── マニュアル内検索API(ログイン不要) ───────────────────────────────
@search_router.get("/api/manual/search")
def api_manual_search(q: str = ""):
    return JSONResponse({"results": search_manual(q)})
