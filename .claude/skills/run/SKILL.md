---
name: run
description: Launch this FastAPI skill-map app against an isolated copy of the data (never the live Docker container) and drive it with Playwright for manual verification of UI changes.
---

# Running skill-map for verification

**Never point this at the live `skillmap` Docker container or its data volume.** That container serves real users. Always verify against an isolated venv + an isolated copy of the SQLite DB. Only rebuild/restart the real container (`docker compose up -d --build`) when the user explicitly asks to deploy the change — confirm first, since that's a shared, hard-to-reverse action.

## 1. Isolated venv (create once, reuse across sessions)

```bash
python3 -m venv /tmp/skillmap_venv
/tmp/skillmap_venv/bin/pip install -q -r app/requirements.txt
```

## 2. Isolated data copy

```bash
mkdir -p /tmp/skillmap_dev/data
cp app/data/skillmap.db /tmp/skillmap_dev/data/skillmap.db        # omit to start from an empty DB instead
cp app/data/config.json /tmp/skillmap_dev/data/config.json 2>/dev/null
```

## 3. Start the server in the background

```bash
cd app
DATABASE_URL="sqlite:////tmp/skillmap_dev/data/skillmap.db" SECRET_KEY="dev-test-secret" \
  /tmp/skillmap_venv/bin/uvicorn main:app --host 127.0.0.1 --port 8191 > /tmp/skillmap_dev/server.log 2>&1 &
```

Poll until it answers (`until curl -sf -o /dev/null http://127.0.0.1:8191/login; do sleep 1; done`), or check `/tmp/skillmap_dev/server.log` for `Application startup complete` vs. a migration traceback (every schema change has a `_migrate_*` helper run from `main.py`'s `_startup()` — a traceback here usually means a new column/table is missing its migration helper). Stop the server afterwards with `pkill -f "uvicorn main:app --host 127.0.0.1 --port 8191"`.

## 4. Get a logged-in session

New registrations need admin approval, so don't go through `/register`. Create or reset a throwaway login directly against the **isolated** DB:

```bash
cd app
DATABASE_URL="sqlite:////tmp/skillmap_dev/data/skillmap.db" /tmp/skillmap_venv/bin/python3 -c "
import database, models, auth
db = database.SessionLocal()
u = db.query(models.User).filter_by(username='dev_test').first()
if not u:
    u = models.User(username='dev_test', email='dev_test@example.com', display_name='Dev Test',
                     password_hash=auth.hash_password('testpass123'), role='admin', is_approved=True)
    db.add(u)
else:
    u.password_hash = auth.hash_password('testpass123'); u.is_approved = True; u.role = 'admin'
db.commit()
"
```

Set `role` to `'user'` (and create a second account) instead when the change being verified is role-sensitive (sidebar sections, permission gates, etc.) — see CLAUDE.md's auth conventions.

## 5. Drive it with Playwright

In a scratch dir, install a Playwright version matching whatever Chromium build is already cached (`npx playwright --version` to check, then `npm install playwright@<that version>`).

**Headless launch gotcha**: a bare `chromium.launch()` can fail with `Executable doesn't exist ... chrome-headless-shell` when only a full Chromium build is cached (no separate headless-shell variant downloaded). Fix one of two ways:
- `npx playwright install chromium` (downloads the missing headless-shell binary — needs network access), or
- Point `executablePath` at the cached full build directly, e.g. on macOS: the highest-numbered `chromium-*` directory under `~/Library/Caches/ms-playwright/` (Linux: `~/.cache/ms-playwright/`), then `.../chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` (path/arch varies — `ls` the cache dir first).

**Two things every script needs, or clicks get silently swallowed:**
- Skip the first-login product tour: `await page.addInitScript(() => localStorage.setItem('skillmap_quickstart_done', '1'))` before the first `page.goto`.
- Dismiss the announcement popup right after login: `await page.keyboard.press('Escape')`. `#annPopupModal` uses `data-bs-backdrop="static"` and intercepts pointer events on everything behind it until dismissed.

Minimal driver:

```js
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch(); // add executablePath here if the headless-shell error above hits
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(() => localStorage.setItem('skillmap_quickstart_done', '1'));
  await page.goto('http://127.0.0.1:8191/login');
  await page.fill('input[name=username]', 'dev_test');
  await page.fill('input[name=password]', 'testpass123');
  await page.click('button[type=submit]');
  await page.waitForLoadState('networkidle');
  await page.keyboard.press('Escape').catch(() => {});
  // ... navigate / assert / screenshot the page(s) you changed ...
  await browser.close();
})();
```

For drag-and-drop (SortableJS, used in business map management, annual plan, education paths): synthetic `mouse.move`/`down`/`up` works, but a single fast jump from source to target is flaky — interpolate the move over ~15-20 steps with small `waitForTimeout`s between them, matching real pointer speed, or the drop can land on the wrong element.

## 6. Reflecting changes in the real app

Steps 1-5 never touch the live container or its data. When (and only when) the user explicitly asks to deploy: `docker compose up -d --build` from the repo root, then `docker ps` / `docker logs skillmap` to confirm a clean startup (watch for the same `_startup()` migration tracebacks as step 3 — `unhealthy` in `docker ps` while the process is actually serving requests is a known pre-existing healthcheck quirk in this repo, not necessarily a real problem).
