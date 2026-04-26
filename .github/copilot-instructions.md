# Skill-Map Copilot Instructions

## Commands

### Docker

```bash
cp .env.example .env
docker compose up -d --build
```

The app is exposed at `http://localhost:8190`. `docker-compose.yml` maps port `8190 -> 8000` and persists runtime data in the `skillmap_data` volume.

### Local development

Run Python commands from `app/`, not the repository root. The code uses relative `templates/`, `static/`, and `data/` paths.

```bash
cd app
pip install -r requirements.txt
uvicorn main:app --reload --port 8190
```

### Admin password recovery

```bash
docker exec -it skillmap python reset_admin_password.py
```

or:

```bash
cd app
python reset_admin_password.py
```

### Tests and linting

This repository does not currently define an automated test suite or lint configuration. There is no repo-standard single-test command yet.

## High-level architecture

- This is a **server-rendered FastAPI monolith**. `app/main.py` owns application startup, setup flow, login/register/password reset, dashboard, profile pages, manual pages, and startup-time schema/data migration helpers.
- Route responsibilities are split across three routers:
  - `app/routers/skills.py`: category and skill catalog management, tier settings, self-assessment, approval workflow, skill matrix, member detail/timeline, and JSON endpoints used by the UI.
  - `app/routers/groups.py`: group CRUD, member assignment, parent/child hierarchy, inherited group skills, and manager-scoped group operations.
  - `app/routers/admin.py`: admin dashboard, user approval/role management, and SMTP settings.
- Persistence is handled with **SQLAlchemy** models in `app/models.py`. Core entities are `User`, `Category`, `Skill`, `UserSkillLevel`, `SkillLevelHistory`, `Group`, `GroupMembership`, `GroupTransfer`, and key-value `AppSetting`.
- Database configuration lives in `app/database.py` and `app/config.py`. The default database is SQLite at `app/data/skillmap.db` unless `DATABASE_URL` is set. Setup state is stored separately in `app/data/config.json`.
- `app/main.py` performs **manual compatibility migrations on startup** with helper functions like `_migrate_approval_columns()` and `_migrate_group_parent_column()`. There is no Alembic migration layer.
- The frontend is Jinja2 + Bootstrap + Chart.js. `app/template_engine.py` registers globals such as `SKILL_LEVELS`, `SKILL_TIERS`, and approval/tier color maps, plus the `jst` datetime filter. Templates rely on those globals rather than recreating display mappings inline.
- Mail delivery in `app/mail.py` reads SMTP settings from `app_settings` first, then falls back to environment variables. Admin UI edits the DB-backed values.

## Key conventions

- **Preserve the server-rendered workflow**: GET handlers usually render a template, invalid POST handlers re-render that same template with an `error` value, and successful POST handlers redirect with `RedirectResponse(..., status_code=303)`.
- **Use the auth helpers instead of hand-rolled guards**. `auth.require_login`, `require_approved`, `require_admin`, and `require_manager_or_admin` are the normal permission entry points. They intentionally raise `HTTPException` with redirect/403 semantics that `main.py` converts into redirects or error pages.
- **Pass `current_user` to templates** when rendering authenticated pages. The base layout and navigation depend on it, and pending badges also depend on `request.state.pending_approval_count` / `pending_user_count` populated by `PendingApprovalMiddleware`.
- **Keep HTML and JSON behaviors in sync**. Skill self-assessment exists both as normal form posts (`/skills/{skill_id}/level`) and AJAX (`/api/skills/{skill_id}/level`). If the approval or history logic changes, update both paths.
- **Approval flow is role-sensitive**:
  - Admin/manager submissions are auto-approved immediately.
  - Regular users must choose an approver.
  - Approved changes append to `SkillLevelHistory`; rejected/pending items stay in `UserSkillLevel`.
- **Group skill inheritance is recursive**. Reuse `_get_all_group_skill_ids`, `_get_ancestor_skill_ids`, and `_is_descendant_of` in `routers/groups.py` instead of duplicating hierarchy logic.
- **Schema changes require startup migration code**. If you add a column or table, update the startup migration helpers in `app/main.py` so existing SQLite databases stay usable.
- **UI copy is primarily Japanese**. Preserve existing Japanese terminology, labels, and validation/error tone unless there is a clear reason to change the product language.
