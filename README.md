# 🌟 ClarityLedger (BartonTech) — Personal Finance Dashboard

A lean, fast dashboard to **ingest**, **categorize**, and **visualize** your transactions with a powerful off-canvas **Category Manager** drawer — built for local dev and Render deploys with persistent storage.

> 🧩 Mixes Plaid, CSV/JSON imports, deep keyword-based categorization (up to 4 levels), return/refund normalization, omit/hidden rules, and a Bootstrap 5 UI that’s friendly on desktop and mobile.

---

## 🧭 Table of Contents
- [🧰 Tech & Stack](#-tech--stack)
- [✨ Features](#-features)
- [🗂 Project Layout](#-project-layout)
- [🚀 Quickstart (Local)](#-quickstart-local)
- [⚙️ Configuration Merge & Source of Truth](#️-configuration-merge--source-of-truth)
- [📁 Data Locations & Discovery](#-data-locations--discovery)
- [➕/➖ Amount Model (UI vs. Expense Math)](#-amount-model-ui-vs-expense-math)
- [🏦 Plaid Integration](#-plaid-integration)
- [🧮 Admin Category Builder (Drawer)](#-admin-category-builder-drawer)
- [🔐 Password Protection](#-password-protection)
- [📱 Mobile Polish (Quick Wins)](#-mobile-polish-quick-wins)
- [🛠 HTTP API (Used by the UI)](#-http-api-used-by-the-ui)
- [🧵 Handy One-Liners](#-handy-one-liners)
- [🧯 Troubleshooting](#-troubleshooting)
- [☁️ Render Notes](#️-render-notes)
- [🤝 Contributing](#-contributing)
- [📜 License](#-license)

---

## 🧰 Tech & Stack
- **Backend:** Python 3.11 · Flask · Jinja2 · Gunicorn  
- **Data & Ingest:** Plaid API (`plaid-python`) · CSV/JSON autodiscovery  
- **Frontend:** Bootstrap 5.3 · Vanilla JS (off-canvas drawer) · HTML templates  
- **Config:** `.env` via `python-dotenv` · JSON config & live overrides  
- **Deploy:** Render (or any WSGI host) with persistent disk under `/var/data`  
- **PWA (optional):** `manifest.webmanifest` (service worker optional)

---

## ✨ Features
- 📥 **Ingest**
  - Plaid Transactions API (paged fetch ➜ timestamped dump + master dedupe)
  - CSV/JSON imports from multiple common bank formats
- 🧠 **Categorization**
  - Up to **4 levels**: Category → Subcategory → Sub-sub → Sub-sub-sub
  - Keyword maps with strict/loose matching & exact per-transaction overrides
  - Transfer detection, return/refund normalization, interest income handling
  - Global omit rules (by keyword or by amount range)
- 🙈 **Hidden Categories**
  - Excluded from totals **but still visible/manageable** in the admin drawer
- 🧰 **Admin Category Builder**
  - Drawer with breadcrumbs, month switcher, pills for children
  - Inspect/rename/upsert categories, add/remove keywords, see path transactions
- 📊 **APIs for Dashboard**
  - Recent activity snapshot, category movers, tree aggregation for UI
- 📦 **Render-ready**
  - Autodiscovers data roots; prefers `/var/data` for persistence
  - Verbose startup logs show effective config and discovery roots
- 📱 **Mobile-friendly**
  - Bootstrap responsive UI; off-canvas drawer works great on phones

---

## 🗂 Project Layout
```
.
├─ web_app/
│  └─ app.py                  # Flask app / routes / blueprints / password gate / refresh_data
├─ truist/
│  ├─ parser_web.py           # parsing, categorization, summaries, APIs, hidden/omit logic
│  ├─ admin_categories.py     # admin endpoints for drawer (inspect/rename/upsert/keywords)
│  ├─ filter_config.py        # default keyword maps (Python)
│  ├─ categories.json         # keyword maps (JSON layer, merged if present)
│  ├─ plaid_fetch.py          # Plaid CLI fetcher (dump + master dedupe)
│  ├─ templates/
│  │  ├─ layout.html          # base layout (Bootstrap, drawer host, service worker reg)
│  │  ├─ navbar.html          # top nav
│  │  ├─ _drawer.html         # off-canvas Category Manager
│  │  └─ cash.html            # dashboard page
│  └─ static/
│     ├─ js/
│     │  ├─ drawer.js         # drawer client logic (inspects/renames/upserts/keywords)
│     │  └─ movers_weekly.js  # recent/movers widgets
│     ├─ icons/               # favicons/PWA icons
│     └─ manifest.webmanifest # PWA manifest
└─ config/                    # (optional) live overrides via CONFIG_DIR/filter_overrides.json
```

---

## 🚀 Quickstart (Local)
1. **Install deps**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Create `.env`**
   ```bash
   PLAID_CLIENT_ID=...
   PLAID_SECRET=...
   PLAID_ENV=sandbox        # or development/production
   APP_PASSWORD=some-strong-password   # enables Basic Auth gate
   # optional:
   CONFIG_DIR=./config
   ```
3. **Run dev server**
   ```bash
   export FLASK_APP=web_app.app
   flask run --reload
   ```
4. **Open** http://localhost:5000 (you’ll be prompted for Basic Auth if `APP_PASSWORD` is set).

---

## ⚙️ Configuration Merge & Source of Truth
Config is reloaded per call, merged in this order (later wins):
1) Python defaults: `truist/filter_config.py`  
2) JSON: `categories.json` (repo root or `truist/`)  
3) Live overrides: `${CONFIG_DIR}/filter_overrides.json` (e.g. `config/filter_overrides.json` or `/var/data/config/filter_overrides.json` in Render)

Useful override keys (examples):
```json
{
  "HIDDEN_CATEGORIES": ["Camera Cat", "Old Stuff"],
  "OMIT_KEYWORDS": ["TEST CHARGE", "SAMPLE"],
  "AMOUNT_OMIT_RULES": [
    {"contains": "AMAZON", "min": 0, "max": 1.00}
  ],
  "CUSTOM_TRANSACTION_KEYWORDS": {
    "STARBUCKS 123 - $4.50": { "category": "Eating Out", "subcategory": "Coffee" }
  },
  "SUBCATEGORY_MAPS": {
    "Groceries/Home": { "Costco": ["COSTCO"], "Target": ["TARGET"] }
  }
}
```
> Hidden categories are **excluded** from totals but still available in the drawer so you can manage keywords, rename, etc.

---

## 📁 Data Locations & Discovery
On startup (and for drawer/API calls) you’ll see logs like:
```
[ClarityLedger] Category config source: categories.json (...) + overrides (/var/data/config/filter_overrides.json)
[ClarityLedger] JSON_PATH = /opt/render/project/src/truist/categories.json
[ClarityLedger] scan roots: ['/var/data/statements', '/var/data/plaid', '/opt/render/project/src/statements', '/opt/render/project/src/plaid', '/opt/render/project/src/truist/statements', '/opt/render/project/src/truist/plaid']
```
ClarityLedger scans **in order** for CSV/JSON statements (first hit wins for base dirs). Common files:
- `plaid_YYYY-MM-DD_HH-MM-SS.json` (fresh dumps)
- `all_transactions.json` (the master merged list ClarityLedger reads)
- `manual_transactions.json` (NDJSON; you can append your own entries)

> The app avoids parsing secrets by ignoring JSON files like `token.json`, `client_secret.json`, etc.

---

## ➕/➖ Amount Model (UI vs. Expense Math)
- **UI sign** (`tx["amount"]`):
  - Income: `+abs(amount)`
  - Expense purchase: `-abs(amount)`
  - Expense return/refund: `+abs(amount)` (detected by keywords like `RETURN`, `REFUND`, `REVERSAL`)
- **Expense math** (`tx["expense_amount"]`):
  - Purchase: `+abs(amount)` (adds to spend)
  - Return: `-abs(amount)` (reduces spend)

Totals:
- `"Income"` category sums **income amounts**
- All other categories sum **expense_amount** (so returns reduce the category)

Transfers and any category in `HIDDEN_CATEGORIES` are excluded from totals and recent lists.

---

## 🏦 Plaid Integration
### Environment
```
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_ENV=sandbox|development|production
```

### Access Token
The CLI fetcher (`truist/plaid_fetch.py`) stores/reads the access token at:
- **Local/Render preferred**: `/var/data/statements/access_token.json`
- Fallback used by the fetcher: `truist/statements/access_token.json`

If you already linked via Plaid Link, copy the token to both locations (safe on Render):
```bash
mkdir -p /opt/render/project/src/truist/statements
cp -f /var/data/statements/access_token.json /opt/render/project/src/truist/statements/access_token.json
```

### Fetching Transactions (CLI)
```bash
python truist/plaid_fetch.py --days 2
# or exact window:
python truist/plaid_fetch.py --since 2025-08-01 --end 2025-08-27
```
This will:
1) Page through Plaid Transactions API  
2) Save a timestamped dump: `plaid_YYYY-MM-DD_HH-MM-SS.json`  
3) Update `all_transactions.json` (dedupe by `(name, amount, date)`; pending tx are skipped)

### Creating a Link Token (when linking new items)
```bash
python truist/plaid_fetch.py link
# prints a one-time link_token you can use with Plaid Link
```

### In-App Refresh Button
`POST /refresh_data` (wired to the “Refresh” button) runs:
```python
subprocess.run([sys.executable, "truist/plaid_fetch.py"], check=True)
```
Ensure the access token file exists at one of the locations above; otherwise the fetcher will prompt for `public_token` (which is not possible in a non-interactive Render process).

---

## 🧮 Admin Category Builder (Drawer)
- Open any **pill** (category/subcategory) and click **Manage** to open the off-canvas drawer
- **Inspect** shows the last `N` transactions for the selected path (category → sub → sub² → sub³)
- **Rename** a category (top level) or the selected child
- **Upsert** a child (create if missing, rename if exists)
- **Keywords**: list, add, remove — per category or per child
- **Path Transactions**: mini table of recent matching txs

> Hidden categories (e.g., “Camera Cat”) still appear in the drawer for management but are excluded from totals and recent widgets.

---

## 🔐 Password Protection
`web_app/app.py` includes a lightweight HTTP **Basic Auth** gate:
- Set `APP_PASSWORD` to enable the gate
- Optionally set `APP_USER` (defaults to `admin`)
- Exemptions include health checks and Plaid Link routes (as needed)

Render example:
```
APP_USER=admin
APP_PASSWORD=super-long-random-string
```
You’ll be prompted by the browser once; credentials are cached for the session.

---

## 📱 Mobile Polish (Quick Wins)
- **Sticky header offsets** auto-measured (`--nav-h`) for consistent table headers below the fixed navbar
- **Safe areas** for iPhone notch: `env(safe-area-inset-*)`
- **Off-canvas drawer width** adapts well; default 480px (tune in CSS)
- Use `.table-responsive` wrappers for wide tables; sticky headers still work via `position: sticky; top: 0;`
- PWA manifest is wired; adding a `service-worker.js` at `/` enables full app caching if desired

---

## 🛠 HTTP API (Used by the UI)
- `GET /api/recent-activity` → JSON: latest month totals/deltas, recent windows (7/30d), movers
- `GET /api/category_movers` → JSON: movers vs previous month
- `POST /refresh_data` → runs `plaid_fetch.py` (see **Plaid** above)
- Admin (drawer):
  - `GET /admin/categories/inspect?level=...&cat=...` (+ `sub`, `ssub`, `sss`, `limit`)
  - `POST /admin/categories/rename`
  - `POST /admin/categories/upsert`
  - `GET /admin/categories/keywords`
  - `POST /admin/categories/keywords/add`
  - `POST /admin/categories/keywords/remove`

---

## 🧵 Handy One-Liners
**Where is my config coming from?**
```bash
# Watch logs on boot: prints category config source + JSON_PATH + scan roots
```
**What categories are hidden?**
```python
from truist.parser_web import list_hidden_categories
print(list_hidden_categories())
```
**Manually run a fetch (Render shell):**
```bash
python truist/plaid_fetch.py --days 2
```
**Ensure token file exists (Render):**
```bash
mkdir -p /opt/render/project/src/truist/statements
cp -f /var/data/statements/access_token.json /opt/render/project/src/truist/statements/access_token.json
```

---

## 🧯 Troubleshooting
- **Refresh button says fetch failed (500):**
  - Check logs: likely missing `access_token.json`
  - Copy the token file to `truist/statements/access_token.json` as shown above
- **Hidden category still shows in totals:**
  - Confirm it’s listed in `HIDDEN_CATEGORIES` in your **live override** JSON
  - Restart app or refresh; drawer will still show it for management
- **Too many/duplicated txs:**
  - `all_transactions.json` dedupes by `(name, amount, date)`; ensure your bank export uses stable `name`
- **Service worker 404:**
  - Optional. If you want PWA caching, serve `/service-worker.js` at the **origin root** (not `/static/`)

---

## ☁️ Render Notes
- Use a **Persistent Disk** mounted at `/var/data` (default on many templates)
- The app scans these roots (in order):  
  `/var/data/statements`, `/var/data/plaid`, `<repo>/statements`, `<repo>/plaid`, `truist/statements`, `truist/plaid`
- Environment:
  - `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`
  - `APP_PASSWORD` (and optional `APP_USER`)
  - Optional `CONFIG_DIR` (e.g., `/var/data/config`) with `filter_overrides.json`
- Health checks:
  - `GET /healthz` returns 200 when app is up

---

## 🤝 Contributing
1. Fork & branch
2. Keep PRs focused (UI, parsing, Plaid, or admin drawer)
3. Add concise before/after screenshots for UI tweaks
4. Include sample data if touching parsing logic

---

## 📜 License
MIT — do what you want, no warranty. If you make it better, consider sharing back. 💙

---

> 🧡 Thanks for using ClarityLedger. It’s designed to stay pragmatic: easy to run, easy to tweak, and easy to trust.
