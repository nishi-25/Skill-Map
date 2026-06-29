# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Docker (primary workflow)

```bash
cp .env.example .env
docker compose up -d --build
```

App is served at `http://localhost:8190` (docker-compose maps `8190 -> 8000`). Runtime data (SQLite DB, avatars, uploads) persists in the `skillmap_data` volume mounted at `/app/data`. First run redirects to `/setup` to create the admin account.

After changing `app/` code, you must `docker compose up -d --build` again — there is no bind mount / hot reload in the container.

### Local development (no Docker)

```bash
cd app
pip install -r requirements.txt
uvicorn main:app --reload --port 8190
```

Run from `app/`, not the repo root — the code uses relative `templates/`, `static/`, and `data/` paths. Default DB is SQLite at `app/data/skillmap.db` unless `DATABASE_URL` is set (e.g. `DATABASE_URL=sqlite:////tmp/foo.db`).

### Admin password recovery

```bash
docker exec -it skillmap python reset_admin_password.py
# or locally: cd app && python reset_admin_password.py
```

### Tests and linting

There is no automated test suite or lint configuration in this repository. Verify changes by running the app (above) and exercising the affected page/flow in a browser.

## High-level architecture

This is a **server-rendered FastAPI monolith** (Jinja2 + Bootstrap 5, no SPA/build step). `app/main.py` (~3100 lines) owns app startup, login/register/password-reset/setup flow, dashboard, profile, and every startup-time schema migration helper. Route handlers beyond that are split across 12 routers in `app/routers/`, each `include_router`'d in `main.py`:

| Router | Owns |
|---|---|
| `skills.py` (largest, ~3700 lines) | Category/skill catalog CRUD, tier settings, self-assessment + approval workflow, skill matrix, member detail/timeline, skill goals, sub-skill evidence, and the JSON endpoints the UI calls |
| `business_map.py` | Business-area hierarchy (tree of `BusinessMapArea`), the "declare via business map" entry point, the area-management UI, and the mindmap visualization (`_build_stats_tree`, `_flatten_tree_nodes`) |
| `annual_plan.py` | Year/month/week/day calendar of goals (skills, sub-skills, business areas, certifications, exams) with drag-and-drop scheduling |
| `groups.py` | Group CRUD, hierarchy (parent/child), member assignment, required-skill inheritance |
| `certifications.py` / `exams.py` | Certification catalog + user certifications; exam definitions, assignments, grading (written + practical) |
| `education.py` | Educational links and `LearningPathArea` step-by-step learning paths |
| `admin.py` | Admin dashboard, user approval/role management, SMTP settings |
| `tickets.py` / `announcements.py` / `wiki.py` | Support ticket chat, admin announcements, Markdown wiki pages |
| `manual.py` | The in-app user manual (`/manual/*`, Jinja templates under `templates/manual/`) and its search index (`manual_search_index.py`) |

Persistence is **SQLAlchemy** models in `app/models.py` (~35 model classes — `User`, `Category`, `Skill`/`SubSkill`, `UserSkillLevel`/`UserSubSkillLevel`, `SkillLevelHistory`, `Group`/`GroupMembership`, `BusinessMapArea`/`BusinessMapAreaSkill`, `SkillGoal`, `Certification(Catalog)`, `Exam` + question/criterion/assignment/answer tables, `WikiPage`, `AnnualPlanItem`, etc.).

**No Alembic** — `app/main.py`'s `_startup()` (FastAPI startup event) runs `Base.metadata.create_all()` for new tables and then a long sequence of idempotent `_migrate_*()` helper functions that `ALTER TABLE ... ADD COLUMN` (guarded by `sqlalchemy.inspect`) for columns added to tables that already shipped. **Any new column/table needs a corresponding `_migrate_*` helper added to that startup sequence**, or existing SQLite databases break.

DB/config wiring: `app/database.py` builds the engine from `app/config.py:get_db_url()` (`DATABASE_URL` env var, else `sqlite:///data/skillmap.db`); setup-complete state is a separate flag in `app/data/config.json`.

Auth: `app/auth.py` — signed-cookie sessions (`itsdangerous`) + bcrypt. Route handlers call `auth.require_login` / `require_approved` / `require_admin` / `require_manager_or_admin` rather than checking `current_user.role` inline; these raise `HTTPException(302/403)` which becomes a redirect/error page.

Frontend: Jinja2 templates in `app/templates/` extend `base.html` (sidebar nav + layout). `app/template_engine.py` registers shared Jinja globals (`SKILL_LEVELS`, `SKILL_TIERS`, `TIER_COLORS`, `APPROVAL_STATUS`, `TICKET_*`, `WIKI_*`, etc.) and filters (`jst` for JST-shifted datetime display, `markdown` for sanitized Markdown rendering via `bleach`) — templates use these instead of redefining display maps inline. No frontend build step: pages use vanilla JS plus Bootstrap 5 JS components (modals, offcanvas, collapse, tabs) and, where drag-and-drop reordering/assignment is needed (business map management, annual plan, education paths, admin todos), SortableJS loaded from a CDN `<script>` tag per-template.

## Key conventions

- **Server-rendered request/response cycle**: GET renders a template; a failed POST re-renders the same template with an `error` value; a successful POST does `RedirectResponse(..., status_code=303)`.
- **Pass `current_user` into every authenticated template.** The base layout/nav depends on it, and sidebar badge counts depend on `request.state.pending_approval_count` / `pending_user_count` / `my_pending_count` / `my_exam_pending_count` / `exam_grading_count`, populated per-request by `PendingApprovalMiddleware` in `main.py`.
- **Keep HTML-form and JSON paths in sync.** Some flows (e.g. skill self-assessment) exist both as a normal form POST and as an `/api/...` AJAX endpoint. If approval/history logic changes, update both.
- **Approval flow is role-sensitive**: admin/manager submissions auto-approve; regular users must pick an approver; approved changes append to `SkillLevelHistory`, pending/rejected stay on `UserSkillLevel`.
- **Group skill inheritance is recursive** — reuse `_get_all_group_skill_ids`, `_get_ancestor_skill_ids`, `_is_descendant_of` in `routers/groups.py` rather than re-deriving hierarchy logic.
- **Business-area hierarchy is also recursive** — reuse `_build_stats_tree`, `_collect_leaf_subskill_ids`, `_flatten_tree_nodes`, `_make_area_visibility_predicate` in `routers/business_map.py` (group-based area visibility for non-admin/manager users must go through the `is_visible` predicate, applied recursively).
- **Deadline semantics differ by goal type in annual plan** (`routers/annual_plan.py`): skill/sub-skill/business-area achievement is date-independent (achieved whenever the underlying record exists), but certification/exam achievement requires completion **on or before** the goal's `target_date` — don't conflate the two when touching `_*_status()` helpers.
- **The manual's table of contents/search is data-driven** — adding a manual page means adding both the route in `routers/manual.py` and an entry in `manual_search_index.py`'s `PAGES` (and ideally `MANUAL_INDEX`) list, not just dropping a template under `templates/manual/`.
- **UI copy is Japanese.** Match existing terminology/tone; don't introduce English UI strings without reason.
