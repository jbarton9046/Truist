# web_app/app.py
from pathlib import Path
from time import time
from datetime import date, datetime, timedelta
from werkzeug.routing import BuildError
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from typing import Dict, Any, Optional, List
import json
import sqlite3  # reserved for future use
import subprocess, sys, os
from truist import filter_config as fc
from truist.parser_web import (
    MANUAL_FILE,
    load_manual_transactions,
    _parse_any_date,
    JSON_PATH,
    get_statements_base_dir,
    get_transactions_for_path,
    generate_summary,
    _load_category_config,
    recent_activity_summary,
    categorize_transaction,
)

from flask import Flask, render_template, abort, request, redirect, url_for, jsonify, Response, flash

# ---- ONE canonical overrides file path (read + write use the same file) ----
try:
    _DESC_OVERRIDES_FILE = get_statements_base_dir() / "desc_overrides.json"
except Exception:
    _DESC_OVERRIDES_FILE = Path(".data/statements") / "desc_overrides.json"

os.environ.setdefault("DESC_OVERRIDES_FILE", str(_DESC_OVERRIDES_FILE))

def _save_desc_overrides(d: dict) -> None:
    p = _desc_overrides_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# ---- Flask app ----
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev")  # enables flash()

@app.get("/__debug/fp")
def _debug_fp():
    date_s = (request.args.get("date") or "")[:10]
    try:
        amt = float(request.args.get("amt") or request.args.get("amount") or 0.0)
    except Exception:
        amt = 0.0
    orig = (request.args.get("orig") or "").strip()
    k1 = _fingerprint_tx(date_s, amt, orig)
    k2 = _fingerprint_tx(date_s, -amt, orig)
    by_fp = (_load_desc_overrides() or {}).get("by_fingerprint", {}) or {}
    return jsonify({"key_pos": k1, "key_neg": k2, "present": (k1 in by_fp) or (k2 in by_fp)})

@app.get("/__debug/desc_overrides")
def debug_desc_overrides():
    try:
        p = _desc_overrides_path()
        exists = p.exists()
        info = {
            "path": str(p),
            "exists": exists,
            "size": (p.stat().st_size if exists else 0),
            "mtime": (datetime.fromtimestamp(p.stat().st_mtime).isoformat() if exists else None),
        }
        data = _load_desc_overrides()
        info["counts"] = {k: len(v or {}) for k, v in (data or {}).items()}
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# === CLARITYLEDGER :: APP ANCHOR ==============================================
# === ID: APP-ANCHOR-7C1F3D2 ===================================================
# === Purpose: Stable insertion point for future patches / merges. =============
# === Search tokens: CLARITYLEDGER-APP-ANCHOR, APP-ANCHOR-7C1F3D2 =============
# ==============================================================================
CLARITYLEDGER_APP_ANCHOR_INFO = {
    "id": "APP-ANCHOR-7C1F3D2",
    "name": "CLARITYLEDGER-APP-ANCHOR",
    "version": "1.0",
    "placed_at": "app.py",
    "notes": (
        "This block is a persistent marker used to locate safe insertion points "
        "for automated edits and future code drops. Do not remove or rename. "
        "OK to move within file if necessary."
    ),
}

def _clarityledger_app_anchor_probe() -> bool:
    """
    No-op probe tied to APP-ANCHOR-7C1F3D2. Returns True so tests/tools can
    confirm the anchor is present without touching runtime behavior.
    """
    return True
# === END CLARITYLEDGER :: APP ANCHOR (APP-ANCHOR-7C1F3D2) =====================
# ==============================================================================

# --- Auth exemptions (must be defined before password_gate) ---
EXEMPT_PATHS = {
    "/login",
    "/logout",
    "/healthz",
    "/static/manifest.webmanifest",
    "/service-worker.js",
}
EXEMPT_PREFIXES = ("/static/",)

from pathlib import Path
import json, os, time
from flask import jsonify, request

def _desc_overrides_path():
    p = os.environ.get("DESC_OVERRIDES_FILE")
    if p:
        return Path(p)
    base = os.environ.get("STATEMENTS_DIR") or "/var/data/statements"
    return Path(base) / "desc_overrides.json"

def _fingerprint_for_save(date_str, amount, original_desc):
    # must match parser_web._fp_str
    try:
        from truist import parser_web as pw
        d = pw._parse_any_date(date_str)
    except Exception:
        from datetime import datetime
        d = None
        try:
            d = datetime.fromisoformat(date_str)
        except Exception:
            pass
    ds = d.strftime("%Y-%m-%d") if d else (str(date_str) or "")[:10]
    try:
        amt = float(amount or 0.0)
    except Exception:
        try:
            amt = float(str(amount).replace(",", ""))
        except Exception:
            amt = 0.0
    od = (original_desc or "").strip().upper()
    return f"{ds}|{amt:.2f}|{od}"


# --- helper: compute movers from summary (PLACE ABOVE ROUTES) ---
def _compute_category_movers(monthly: dict) -> dict:
    """Return {rows:[{category,prev,latest,delta,pct}], prev_month, latest_month} for the 2 most recent months."""
    if not monthly:
        return {"rows": [], "prev_month": None, "latest_month": None}

    keys = sorted(monthly.keys())
    if len(keys) == 1:
        latest_key = keys[-1]
        latest_cats = (monthly.get(latest_key, {}) or {}).get("categories", {}) or {}
        rows = []
        for name, data in latest_cats.items():
            latest = float((data or {}).get("total", 0.0) or 0.0)
            if abs(latest) < 1e-9:
                continue
            rows.append({
                "category": name,
                "prev": 0.0,
                "latest": round(latest, 2),
                "delta": round(latest, 2),
                "pct": None,
            })
        rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
        return {"rows": rows, "prev_month": None, "latest_month": latest_key}

    prev_key, latest_key = keys[-2], keys[-1]
    prev_cats = (monthly.get(prev_key, {}) or {}).get("categories", {}) or {}
    latest_cats = (monthly.get(latest_key, {}) or {}).get("categories", {}) or {}

    names = set(prev_cats.keys()) | set(latest_cats.keys())
    rows = []
    for name in names:
        prev = float((prev_cats.get(name, {}) or {}).get("total", 0.0) or 0.0)
        latest = float((latest_cats.get(name, {}) or {}).get("total", 0.0) or 0.0)
        if abs(prev) < 1e-9 and abs(latest) < 1e-9:
            continue
        delta = round(latest - prev, 2)
        pct = None if abs(prev) < 1e-9 else round((delta / prev) * 100.0, 2)
        rows.append({
            "category": name,
            "prev": round(prev, 2),
            "latest": round(latest, 2),
            "delta": delta,
            "pct": pct,
        })

    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return {"rows": rows, "prev_month": prev_key, "latest_month": latest_key}


# ---- Safe URL helper to avoid BuildError in templates ----
def safe_url(endpoint: str, **values) -> str:
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return "#"  # graceful no-op if route is missing

# expose to Jinja
app.jinja_env.globals["safe_url"] = safe_url

@app.before_request
def password_gate():
    required = os.environ.get("APP_PASSWORD")
    if not required:
        return  # gate disabled when no password configured
    p = request.path
    if request.method == "HEAD" or p in EXEMPT_PATHS or p.startswith(EXEMPT_PREFIXES):
        return
    auth = request.authorization
    expected_user = os.environ.get("APP_USER")  # optional
    if auth and ((expected_user is None or auth.username == expected_user) and auth.password == required):
        return
    return Response(
        "Authentication required", 401, {"WWW-Authenticate": 'Basic realm="ClarityLedger"'}
    )

# ---- Debug endpoints (optional) ----
try:
    from truist.debug_config import debug_bp  # type: ignore
    app.register_blueprint(debug_bp)
except Exception:
    pass

# Optional: log where CONFIG_DIR points on boot
app.logger.info("[Config] Using CONFIG_DIR=%s", os.environ.get("CONFIG_DIR"))

# ---- Blueprints (admin UI + keyword APIs) ----
from truist.admin_categories import admin_categories_bp, load_cfg
app.register_blueprint(admin_categories_bp)

# ------------------ CATEGORY TREE + CFG ------------------
cfg = {
    "CATEGORY_KEYWORDS": getattr(fc, "CATEGORY_KEYWORDS", {}),
    "SUBCATEGORY_MAPS": getattr(fc, "SUBCATEGORY_MAPS", {}),
    "SUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBCATEGORY_MAPS", {}),
    "SUBSUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBSUBCATEGORY_MAPS", {}),
    "KEYWORDS": getattr(fc, "KEYWORDS", {}),
}



def _build_monthly_live() -> dict:
    ck, sm, *_ = _load_category_config()
    ov = _load_desc_overrides()
    monthly = generate_summary(ck, sm, desc_overrides=ov)
    _apply_hide_rules_to_summary(monthly)
    _rebuild_categories_from_tree(monthly)
    return monthly

def _flatten_display_transactions(monthly: dict) -> list:
    """Only rows that passed omit rules and are not Transfers/hidden cats."""
    rows = []
    for m in monthly.values():
        for _, data in (m.get("categories") or {}).items():
            rows.extend(data.get("transactions", []) or [])
    return rows



# --- helper: load description overrides (txid + fingerprint) ---
def _load_desc_overrides():
    p = _desc_overrides_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    return {
        "by_txid": data.get("by_txid", {}) or {},
        "by_fingerprint": data.get("by_fingerprint", {}) or {},
    }


def _norm_month(val) -> str:
    """
    Normalize a month key or date string into 'YYYY-MM'.
    Accepts 'YYYY-MM', 'YYYY-MM-DD', 'MM/DD/YYYY', or anything parseable
    by _parse_any_date. Returns '0000-00' if unparseable.
    """
    if not val:
        return "0000-00"
    s = str(val).strip()

    # Already looks like YYYY-MM
    if len(s) >= 7 and s[4] == "-":
        return s[:7]

    d = _parse_any_date(s)
    if d:
        return f"{d.year:04d}-{d.month:02d}"

    return "0000-00"
# ---------------------------------------------------------------------------


def append_manual_tx(tx: dict, path: Path = MANUAL_FILE) -> dict:
    # validate + normalize
    if "amount" not in tx:
        raise ValueError("Missing 'amount'")

    # derive a single description and use it for both fields
    desc = (tx.get("description") or tx.get("name") or tx.get("memo") or "Manual").strip()

    norm = {
        "date": tx.get("date") or date.today().isoformat(),
        "name": desc,
        "description": desc,   # important for keyword matching
        "amount": float(tx["amount"]),
        "pending": False,
        "source": "manual",
    }
    for k in ("category", "subcategory", "sub_subcategory", "memo", "transaction_id"):
        if k in tx and tx[k] not in (None, ""):
            norm[k] = tx[k]

    # append as NDJSON with surrounding newlines (prevents glued JSON / decode errors)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(norm, separators=(",", ":")).encode("utf-8")
    with path.open("ab") as f:
        f.write(b"\n")
        f.write(line)
        f.write(b"\n")

    return norm

def build_category_tree(cfg_in=None):
    cfg_local = cfg_in or load_cfg()
    cats = set()
    cats.update(cfg_local["SUBCATEGORY_MAPS"].keys())
    cats.update(cfg_local["CATEGORY_KEYWORDS"].keys())
    cats.update(cfg_local.get("SUBSUBCATEGORY_MAPS", {}).keys())
    cats.update(cfg_local.get("SUBSUBSUBCATEGORY_MAPS", {}).keys())

    tree = []
    for cat in sorted(cats):
        sub_map = cfg_local["SUBCATEGORY_MAPS"].get(cat, {}) or {}
        cat_node = {"name": cat, "total": None, "subs": []}
        for sub in sorted(sub_map.keys()):
            ssub_map = (cfg_local.get("SUBSUBCATEGORY_MAPS", {}).get(cat, {}) or {}).get(sub, {}) or {}
            sub_node = {"name": sub, "total": None, "subs": []}
            for ssub in sorted(ssub_map.keys()):
                sss_map = ((cfg_local.get("SUBSUBSUBCATEGORY_MAPS", {}).get(cat, {}) or {})
                           .get(sub, {}) or {}).get(ssub, {}) or {}
                ssub_node = {"name": ssub, "total": None, "subs": []}
                for sss in sorted(sss_map.keys()):
                    ssub_node["subs"].append({"name": sss, "total": None, "subs": []})
                sub_node["subs"].append(ssub_node)
            cat_node["subs"].append(sub_node)
        tree.append(cat_node)
    return tree

# ------------------ FILE HELPERS ------------------
def _statements_dir() -> Path:
    # Use the same env var the parser reads (preferred), then accept legacy TRUIST_DATA_DIR.
    dir_env = os.environ.get("STATEMENTS_DIR") or os.environ.get("TRUIST_DATA_DIR")
    if dir_env:
        p = Path(dir_env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    # Final fallback: persistent disk path
    p = Path("/var/data/statements")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _normalize_form_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return raw

def save_manual_form_transaction(form, tx_type: str):
    raw_amount = abs(float(form["amount"]))
    amount = raw_amount if tx_type == "income" else -raw_amount
    append_manual_tx({
        "date": _normalize_form_date(form["date"]),
        "description": form["description"],
        "amount": amount,
        "category": form.get("category", ""),
        "subcategory": form.get("subcategory", ""),
        "sub_subcategory": form.get("sub_subcategory", "")
    })

# ------------------ SUMMARY PRUNING / REBUILD ------------------
def _apply_hide_rules_to_summary(summary_data):
    """
    Prune hidden/sentinel transfers (±10002.02) and the specific Robinhood -$450.00
    from the monthly tree, recompute node totals, and recompute per-month income/expense/net totals.
    Also removes now-empty categories.
    """
    EPS = 0.005
    HIDE_SENTINELS = (10002.02, -10002.02)

    def _amt(t) -> float:
        try:
            return float(t.get("amount", t.get("amt", 0.0)) or 0.0)
        except Exception:
            return 0.0

    def _desc(t) -> str:
        return (t.get("description") or t.get("desc") or "").upper()

    def _is_sentinel_amount(a: float) -> bool:
        return any(abs(a - h) < EPS for h in HIDE_SENTINELS)

    def _is_robinhood_450(t) -> bool:
        a = _amt(t)
        d = _desc(t)
        return ("ROBINHOOD" in d) and (abs(a + 450.00) < EPS)

    def _should_hide(t) -> bool:
        a = _amt(t)
        if _is_sentinel_amount(a):
            return True
        if _is_robinhood_450(t):
            return True
        return False

    def _sum_signed_tx(txs):
        s = 0.0
        for t in (txs or []):
            s += _amt(t)
        return s

    def _prune_and_total(node):
        """Recursively drop hidden transactions at leaves and compute totals bottom-up."""
        if not isinstance(node, dict):
            return 0.0
        children = node.get("children") or []
        # Leaf
        if not children:
            txs = list(node.get("transactions") or [])
            kept = [t for t in txs if not _should_hide(t)]
            node["transactions"] = kept
            total = _sum_signed_tx(kept)
            node["total"] = round(total, 2)
            return total
        # Non-leaf
        total = 0.0
        for ch in children:
            total += _prune_and_total(ch)
        node["total"] = round(total, 2)
        return total

    def _prune_empty_nodes(node):
        """Remove empty children (no transactions/children, ~zero total)."""
        if not isinstance(node, dict):
            return False
        children = node.get("children") or []
        new_children = []
        for ch in children:
            if _prune_empty_nodes(ch):
                new_children.append(ch)
        node["children"] = new_children
        if new_children:
            return True
        txs = node.get("transactions") or []
        if txs:
            return True
        try:
            tot = float(node.get("total", 0.0) or 0.0)
        except Exception:
            tot = 0.0
        return abs(tot) > EPS

    if not isinstance(summary_data, dict):
        return

    for _mk, month_blob in summary_data.items():
        if not isinstance(month_blob, dict):
            continue
        tree = month_blob.get("tree") or []
        for top in tree:
            _prune_and_total(top)

        pruned_tree = []
        for top in tree:
            if _prune_empty_nodes(top):
                pruned_tree.append(top)
        month_blob["tree"] = pruned_tree

        income_sum = 0.0
        expense_sum = 0.0

        def _walk_leaves(n):
            nonlocal income_sum, expense_sum
            ch = n.get("children") or []
            if ch:
                for c in ch:
                    _walk_leaves(c)
            else:
                for t in (n.get("transactions") or []):
                    a = _amt(t)
                    if a > 0:
                        income_sum += a
                    elif a < 0:
                        expense_sum += (-a)

        for top in pruned_tree:
            _walk_leaves(top)

        month_blob["income_total"] = round(income_sum, 2)
        month_blob["expense_total"] = round(expense_sum, 2)
        month_blob["net_cash_flow"] = round(income_sum - expense_sum, 2)

# Rebuild categories mapping from pruned tree (so Income/JL Pay shows consistently)
def _rebuild_categories_from_tree(summary_data: dict) -> None:
    if not summary_data:
        return

    EPS = 0.005
    def _amt(x):
        try: return float(x)
        except Exception: return 0.0

    def _is_hidden_amount(a: float) -> bool:
        return abs(a - 10002.02) < EPS or abs(a + 10002.02) < EPS

    for _, month in summary_data.items():
        tree = month.get("tree") or []
        cats: dict[str, dict] = {}

        def add_row(top: str, sub: str, ssub: str, s3: str, tx: dict):
            amt = _amt(tx.get("amount", tx.get("amt", 0.0)) or 0.0)
            if _is_hidden_amount(amt):
                return
            desc = tx.get("description") or tx.get("desc") or ""

            if top not in cats:
                cats[top] = {
                    "transactions": [],
                    "total": 0.0,
                    "subcategories": {},
                    "subsubcategories": {},
                    "subsubsubcategories": {},
                }

            # flat transaction list at top level
            cats[top]["transactions"].append({
                "date": tx.get("date", ""),
                "description": desc,
                "amount": amt,
                "category": top,
                "subcategory": sub or "",
            })
            cats[top]["total"] += amt

            # level 1
            if sub:
                cats[top]["subcategories"][sub] = cats[top]["subcategories"].get(sub, 0.0) + amt
                # level 2
                if ssub:
                    lvl2 = cats[top]["subsubcategories"].setdefault(sub, {})
                    lvl2[ssub] = lvl2.get(ssub, 0.0) + amt
                    # level 3
                    if s3:
                        lvl3_sub = cats[top]["subsubsubcategories"].setdefault(sub, {})
                        lvl3_ssub = lvl3_sub.setdefault(ssub, {})
                        lvl3_ssub[s3] = lvl3_ssub.get(s3, 0.0) + amt

        def walk(node: dict, parts: list[str]):
            name = (node.get("name") or "").strip()
            here = parts + ([name] if name else [])
            children = node.get("children") or []
            if children:
                for ch in children:
                    walk(ch, here)
            else:
                top = here[0] if here else "Uncategorized"
                sub = here[1] if len(here) > 1 else ""
                ssub = here[2] if len(here) > 2 else ""
                s3 = here[3] if len(here) > 3 else ""
                for tx in (node.get("transactions") or []):
                    add_row(top, sub, ssub, s3, tx)

        for top_node in tree:
            walk(top_node, [])

        month["categories"] = cats


_MONTHLY_CACHE = {"key": None, "built_at": 0.0, "monthly": None, "cfg": None}
_CACHE_TTL_SEC = 30  # rebuild at most every 30s unless data/config changed

def _cache_fingerprint() -> tuple:
    try:
        manual_m = MANUAL_FILE.stat().st_mtime if MANUAL_FILE.exists() else 0
    except Exception:
        manual_m = 0
    try:
        cfg_dir = Path(os.environ.get("CONFIG_DIR", "config"))
        ovrd = cfg_dir / "filter_overrides.json"
        ov_m = ovrd.stat().st_mtime if ovrd.exists() else 0
    except Exception:
        ov_m = 0
    try:
        json_m = JSON_PATH.stat().st_mtime if JSON_PATH else 0
    except Exception:
        json_m = 0
    # NEW: also watch desc_overrides.json
    try:
        ov2_m = _DESC_OVERRIDES_FILE.stat().st_mtime if _DESC_OVERRIDES_FILE.exists() else 0
    except Exception:
        ov2_m = 0

    return (manual_m, ov_m, ov2_m, json_m)



def build_monthly(force: bool = False):
    """
    Returns (monthly, cfg_live). Cached for a short TTL and invalidated
    automatically if manual transactions or config files change.
    """
    fp = _cache_fingerprint()
    now = time()
    c = _MONTHLY_CACHE

    if (not force) and c["monthly"] is not None and c["key"] == fp and (now - c["built_at"] < _CACHE_TTL_SEC):
        return c["monthly"], c["cfg"]

    cfg_live = load_cfg()

    # Load description overrides up-front so they apply BEFORE categorization.
    ov = _load_desc_overrides()

    monthly = generate_summary(
        cfg_live["CATEGORY_KEYWORDS"],
        cfg_live["SUBCATEGORY_MAPS"],
        desc_overrides=ov
    ) or {}

    _apply_hide_rules_to_summary(monthly)
    _rebuild_categories_from_tree(monthly)

    c.update({"key": fp, "built_at": now, "monthly": monthly, "cfg": cfg_live})
    return monthly, cfg_live


# --- Goals storage ---
def _goals_file() -> Path:
    return _statements_dir() / "goals.json"

def load_goals() -> dict:
    try:
        with open(_goals_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"monthly_goals": {}, "updated_at": None}

def save_goals(goals: dict):
    out = {
        "monthly_goals": goals.get("monthly_goals", {}),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    with open(_goals_file(), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

def _last_n_month_labels(n=12, end=None):
    end = end or datetime.today().replace(day=1)
    months = []
    for i in range(n-1, -1, -1):
        d = end - relativedelta(months=i)
        months.append(d.strftime("%Y-%m"))
    return months

def _month_key(dt_str):
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d").strftime("%Y-%m")
    except Exception:
        try:
            return datetime.strptime(dt_str, "%m/%d/%Y").strftime("%Y-%m")
        except Exception:
            return None
        


# ------------------ MIDDLEWARE ------------------
@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# ------------------ TEMPLATE INJECT ------------------
@app.context_processor
def inject_builder_url():
    try:
        return {"BUILDER_URL": url_for("category_builder")}
    except Exception:
        return {"BUILDER_URL": ""}

@app.context_processor
def inject_helpers():
    def endpoint_url(preferred: str, fallback: str, **values):
        try:
            return url_for(preferred, **values)
        except BuildError:
            return url_for(fallback, **values)
    return {"endpoint_url": endpoint_url}

# ------------------ ROUTES ------------------

@app.post("/refresh_data")
def refresh_data():
    try:
        env = os.environ.copy()
        env["NONINTERACTIVE"] = "1"

        args = [sys.executable, "-m", "truist.plaid_fetch"]

        d = (request.args.get("days") or "").strip()
        s = (request.args.get("start") or "").strip()
        e = (request.args.get("end") or "").strip()
        if d.isdigit():
            args += ["--days", d]
        if s:
            args += ["--since", s]
        if e:
            args += ["--end", e]

        proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=300)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            app.logger.error("plaid_fetch failed rc=%s\n%s", proc.returncode, out)
            return jsonify(ok=False, rc=proc.returncode, out=out), 500
        return jsonify(ok=True, rc=0, out=out)
    except Exception as e:
        app.logger.exception("refresh_data error")
        return jsonify(ok=False, error=str(e)), 500




@app.route("/builder")
def category_builder():
    cfg_live = load_cfg()
    return render_template("category_builder.html", cfg=cfg_live)

@app.route("/")
def index():
    try:
        # Load live config and overrides, then build monthly via the SAME pipeline
        ck, sm, *_ = _load_category_config()
        ov = _load_desc_overrides()
        summary_data = generate_summary(ck, sm, desc_overrides=ov)
        _apply_hide_rules_to_summary(summary_data)
        _rebuild_categories_from_tree(summary_data)
    except Exception as e:
        try:
            app.logger.exception("generate_summary() failed on /: %s", e)
        except Exception:
            pass
        summary_data = {}

    if summary_data:
        # Keep your existing “latest month” totals
        latest_key = sorted(summary_data.keys())[-1]
        latest = summary_data.get(latest_key, {}) or {}
        income_total = float(latest.get("income_total", 0.0))
        expense_total = float(latest.get("expense_total", 0.0))

        # Build Most Recent transactions ACROSS all months from the re-categorized txns
        all_tx = _flatten_display_transactions(summary_data)

        def _dt(t):
            return _parse_any_date(t.get("date") or "") or datetime.min

        transactions = sorted(all_tx, key=_dt, reverse=True)[:15]  # adjust count if you like
    else:
        transactions = []
        income_total = 0.0
        expense_total = 0.0

    return render_template(
        "index.html",
        summary_data=summary_data,
        transactions=transactions,  # “Most Recent” now reflects overrides + recategorization
        income=income_total,
        expense=expense_total,
    )


@app.route("/categories")
def categories():
    cfg_live = load_cfg()
    return render_template(
        "category_breakdown.html",
        category_tree=build_category_tree(cfg_live),
        CFG=cfg_live
    )

# --- REPLACE your existing /cash route with this (adds date_today) ---
@app.route("/cash", methods=["GET"])
def cash_page():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary_data = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)
    _apply_hide_rules_to_summary(summary_data)

    return render_template(
        "cash.html",
        summary_data=summary_data,
        category_keywords=cfg_live["CATEGORY_KEYWORDS"],
        subcategory_maps=cfg_live["SUBCATEGORY_MAPS"],
        subsubcategory_maps=cfg_live.get("SUBSUBCATEGORY_MAPS", {}),
        date_today=date.today().isoformat(),  # ⬅ added
    )


@app.route("/add-income", methods=["GET"])
def add_income():
    return redirect(url_for("cash_page"))

@app.route("/add-expense", methods=["GET"])
def add_expense():
    return redirect(url_for("cash_page"))

@app.route("/submit-income", methods=["POST"])
def submit_income():
    try:
        raw_amount = abs(float(request.form["amount"]))
    except Exception:
        flash("Amount must be a valid number.", "warning")
        return redirect(url_for("cash_page"))

    append_manual_tx({
        "date": _normalize_form_date(request.form.get("date", "")),
        "description": request.form.get("description", ""),
        "amount": raw_amount,
        "category": request.form.get("category", ""),
        "subcategory": request.form.get("subcategory", ""),
        "sub_subcategory": request.form.get("sub_subcategory", ""),
    })

    # bust cached monthly summary so cash page reflects the new entry
    try:
        _MONTHLY_CACHE["monthly"]  = None
        _MONTHLY_CACHE["key"]      = None
        _MONTHLY_CACHE["built_at"] = 0.0
    except Exception:
        pass

    flash("Income recorded.", "success")
    return redirect(url_for("cash_page"))

@app.route("/submit-expense", methods=["POST"])
def submit_expense():
    try:
        # uses your helper to coerce sign and append
        save_manual_form_transaction(request.form, "expense")
    except Exception:
        flash("Please check the form values.", "warning")
        return redirect(url_for("cash_page"))

    # bust cache
    try:
        _MONTHLY_CACHE["monthly"]  = None
        _MONTHLY_CACHE["key"]      = None
        _MONTHLY_CACHE["built_at"] = 0.0
    except Exception:
        pass

    flash("Expense recorded.", "success")
    return redirect(url_for("cash_page"))

# --- DEBUG: inspect category keyword sources ---
@app.get("/__debug/keywords")
def debug_keywords():
    import truist.filter_config as fc
    from pathlib import Path
    import json

    # Load categories.json (project root)
    project_root = Path(__file__).resolve().parents[1]
    json_path = project_root / "categories.json"
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
    else:
        json_data = {}

    # Grab path from query param
    path = request.args.get("path", "")

    # Get from filter_config.py
    py_keywords = fc.CATEGORY_KEYWORDS.get(path, {})

    # Get from categories.json
    json_keywords = json_data.get("CATEGORY_KEYWORDS", {}).get(path, {})

    return jsonify({
        "path": path,
        "filter_config.py": {
            "file": str(fc.__file__),
            "keywords": py_keywords
        },
        "categories.json": {
            "file": str(json_path),
            "exists": json_path.exists(),
            "keywords": json_keywords
        }
    })


@app.route("/api/category_movers", methods=["GET"])
def api_category_movers():
    try:
        monthly = _build_monthly_live()          # <- optional helper
        mov = _compute_category_movers(monthly)
        return jsonify(
            ok=True,
            latest_month=mov["latest_month"],
            prev_month=mov["prev_month"],
            rows=mov["rows"],
        )
    except Exception as e:
        try: app.logger.exception("category_movers failed: %s", e)
        except Exception: pass
        return jsonify(ok=True, latest_month=None, prev_month=None, rows=[])



# ---------- helpers for API ----------
def _extract_transactions(summary):
    for key in ("transactions", "all_transactions", "tx", "items"):
        if key in summary and isinstance(summary[key], list):
            return summary[key]
    for key in ("categories", "by_category", "category_groups"):
        if key in summary and isinstance(summary[key], dict):
            txs = []
            for cat, blob in summary[key].items():
                for k in ("transactions", "tx", "items", "list"):
                    if isinstance(blob, dict) and k in blob and isinstance(blob[k], list):
                        for t in blob[k]:
                            t2 = dict(t)
                            t2.setdefault("category", cat)
                            txs.append(t2)
                if isinstance(blob, list) and blob and isinstance(blob[0], dict):
                    for t in blob:
                        t2 = dict(t)
                        t2.setdefault("category", cat)
                        txs.append(t2)
            if txs:
                return txs
    return []

def _top_level_category_of(tx):
    for key in ("category", "top_category", "category_path", "path", "cat"):
        if key in tx and tx[key]:
            val = tx[key]
            if isinstance(val, str):
                return val
            if isinstance(val, (list, tuple)) and val:
                return val[0]
    return "Uncategorized"

@app.get("/debug/hidden-categories")
def debug_hidden_categories():
    from truist.parser_web import _hidden_categories
    lst = sorted([str(x) for x in _hidden_categories()])
    return jsonify({"hidden": lst})

@app.get("/healthz")
def healthz():
    return jsonify(ok=True), 200

@app.route("/api/categories/monthly")
def api_categories_monthly():
    """
    Returns monthly totals by category.
    deep=1 : walk the full summary tree and emit deep paths like "A / B / C".
    """
    deep = str(request.args.get("deep", "0")).lower() in ("1", "true", "yes")
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)


    # If we have raw txs (first-run / cache-miss path), fall back to a simple top-only rollup.
    txs = _extract_transactions(summary)
    if txs and not deep:
        months = _last_n_month_labels(12)
        bucket = {}
        for tx in txs:
            m = _month_key(tx.get("date", ""))
            if not m or m not in months:
                continue
            cat = _top_level_category_of(tx)  # your existing categorizer
            amt = float(tx.get("amount") or 0.0)
            val = -amt if amt < 0 else amt  # positive magnitudes for both income/expense
            bucket.setdefault(cat, {})
            bucket[cat][m] = bucket[cat].get(m, 0.0) + val

        categories = [
            {"name": cat, "path": [cat], "monthly": [bucket[cat].get(m, 0.0) for m in months]}
            for cat in bucket
        ]
        categories.sort(key=lambda c: sum(c["monthly"]), reverse=True)
        return jsonify({"months": months, "categories": categories})

    # Otherwise use the monthly summary tree (same source as your dashboard cards)
    _apply_hide_rules_to_summary(summary)  # keep current hide logic

    # Establish last 12 months window from summary keys
    months_all = sorted(summary.keys(), key=_norm_month)
    months_sel = months_all[-12:]
    months = [_norm_month(k) for k in months_sel]

    if not deep:
        # Top-level rollup (backward compatible)
        top_bucket = {}
        for i, mkey in enumerate(months_sel):
            month_blob = summary.get(mkey) or {}
            for top in (month_blob.get("tree") or []):
                cat = (top.get("name") or "Uncategorized")
                tot = float(top.get("total") or 0.0)
                val = abs(tot)
                if cat not in top_bucket:
                    top_bucket[cat] = [0.0] * len(months)
                top_bucket[cat][i] += val
        categories = [{"name": cat, "path": [cat], "monthly": series} for cat, series in top_bucket.items()]
        categories.sort(key=lambda c: sum(c["monthly"]), reverse=True)
        return jsonify({"months": months, "categories": categories})

    # ---- deep=1: walk the full tree and emit deep path names "A / B / C" ----
    deep_bucket: Dict[str, List[float]] = {}

    def walk(node: dict, path_parts: list[str], month_index: int):
        name = (node.get("name") or "Uncategorized")
        path_now = path_parts + [name]
        key = " / ".join(path_now)
        total = abs(float(node.get("total") or 0.0))
        if key not in deep_bucket:
            deep_bucket[key] = [0.0] * len(months)
        deep_bucket[key][month_index] += total
        for child in (node.get("children") or []):
            walk(child, path_now, month_index)

    for i, mkey in enumerate(months_sel):
        month_blob = summary.get(mkey) or {}
        for top in (month_blob.get("tree") or []):
            walk(top, [], i)

    categories = [{"name": key, "path": key.split(" / "), "monthly": series} for key, series in deep_bucket.items()]
    categories.sort(key=lambda c: sum(c["monthly"]), reverse=True)
    return jsonify({"months": months, "categories": categories})

# -------- Charts --------
@app.route("/charts")
def charts_page():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)

    _apply_hide_rules_to_summary(summary)
    _rebuild_categories_from_tree(summary)  # <-- add this
    since_date = cfg_live.get("SUMMARY_SINCE_DATE")
    cat_monthly = build_top_level_monthly_from_summary(summary, months_back=12, since_date=since_date)
    return render_template("charts.html", cat_monthly=cat_monthly)



@app.get("/api/cat_monthly")
def api_cat_monthly():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)

    _apply_hide_rules_to_summary(summary)
    _rebuild_categories_from_tree(summary)  # <-- add this

    months_back = int(request.args.get("months_back") or 12)
    since_date = request.args.get("since_date") or cfg_live.get("SUMMARY_SINCE_DATE")
    payload = build_top_level_monthly_from_summary(summary, months_back=months_back, since_date=since_date)
    return jsonify(payload)



@app.get("/api/cat_monthly_debug")
def api_cat_monthly_debug():
    ym = (request.args.get("ym") or "").strip()
    if not ym:
        return jsonify({"error": "pass ?ym=YYYY-MM"}), 400    
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)

    _apply_hide_rules_to_summary(summary)
    _rebuild_categories_from_tree(summary)  # <-- add this

    bucket = summary.get(ym) or {}
    cats = (bucket.get("categories") or {})
    income_total = abs(float((cats.get("Income") or {}).get("total", 0.0)))

    # Pull Income transactions from the rebuilt categories map (most accurate).
    txs = ((cats.get("Income") or {}).get("transactions") or [])
    txs_sorted = sorted(txs, key=lambda t: abs(float(t.get("amount", 0.0))), reverse=True)[:40]

    return jsonify({
        "ym": ym,
        "income_total": income_total,
        "income_tx_sample": [
            {
                "date": t.get("date"),
                "amount": t.get("amount"),
                "desc": t.get("description"),
                "subcategory": t.get("subcategory"),
                "sub_subcategory": t.get("sub_subcategory"),
            } for t in txs_sorted
        ]
    })

# -------- Goals --------
@app.route("/goals")
def goals_page():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)
    _apply_hide_rules_to_summary(summary)
    _rebuild_categories_from_tree(summary)
    cat_monthly = build_top_level_monthly_from_summary(
        summary, months_back=12, since_date="2025-04-21"
    )
    return render_template("goals.html", cat_monthly=cat_monthly)

@app.get("/api/goals")
def api_goals_get():
    return jsonify(load_goals())

@app.post("/api/goals")
def api_goals_set():
    data = request.get_json(silent=True) or {}
    goals = {"monthly_goals": data.get("monthly_goals", {})}
    save_goals(goals)
    return jsonify({"ok": True, **load_goals()})

def build_cat_monthly_somehow():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)

    _apply_hide_rules_to_summary(summary)

    def norm_month(k: str) -> str:
        k = (k or "").strip()
        if "_" in k:
            y, m = k.split("_", 1)
            return f"{y}-{m}"
        return k[:7]

    months_all = sorted(summary.keys(), key=norm_month)
    months_sel = months_all[-12:]
    months = [norm_month(k) for k in months_sel]

    bucket = defaultdict(lambda: [0.0] * len(months))

    def walk_leaves(node, path, month_index):
        name = (node.get("name") or "Uncategorized").strip() or "Uncategorized"
        children = node.get("children") or []
        if children:
            for ch in children:
                walk_leaves(ch, path + [name], month_index)
        else:
            tot = float(node.get("total") or 0.0)
            full_path = " / ".join(path + [name])
            bucket[full_path][month_index] += abs(tot)

    for i, mkey in enumerate(months_sel):
        month_blob = summary.get(mkey) or {}
        for top in (month_blob.get("tree") or []):
            walk_leaves(top, [], i)

    categories = [{"name": n, "monthly": arr} for n, arr in bucket.items()]
    categories.sort(key=lambda c: sum(c["monthly"]), reverse=True)
    return {"months": months, "categories": categories}



# ------------------ Deep monthly builder (for All Categories) ------------------
def build_cat_monthly_from_summary(
    summary: Dict[str, Any],
    months_back: int = 12,
    since: Optional[str] = None,
    since_date: Optional[str] = None,
) -> Dict[str, Any]:
    cfg_live = load_cfg()
    rev_sub_to_cat: Dict[str, str] = {}
    seen_sub = defaultdict(set)
    for cat, submap in (cfg_live.get("SUBCATEGORY_MAPS") or {}).items():
        for sub in submap.keys():
            s = str(sub).strip()
            if s:
                seen_sub[s].add(cat)
    for sub, parents in seen_sub.items():
        if len(parents) == 1:
            rev_sub_to_cat[sub] = next(iter(parents))

    rev_ssub_to_pair: Dict[str, tuple[str, str]] = {}
    seen_ssub = defaultdict(set)
    for cat, subdict in (cfg_live.get("SUBSUBCATEGORY_MAPS") or {}).items():
        for sub, ssubdict in (subdict or {}).items():
            for ssub in (ssubdict or {}).keys():
                s = str(ssub).strip()
                if s:
                    seen_ssub[s].add((cat, sub))
    for ssub, parents in seen_ssub.items():
        if len(parents) == 1:
            rev_ssub_to_pair[ssub] = next(iter(parents))

    rev_sss_to_trip: Dict[str, tuple[str, str, str]] = {}
    seen_sss = defaultdict(set)
    for cat, subdict in (cfg_live.get("SUBSUBSUBCATEGORY_MAPS") or {}).items():
        for sub, ssubdict in (subdict or {}).items():
            for ssub, sssdict in (ssubdict or {}).items():
                for sss in (sssdict or {}).keys():
                    s = str(sss).strip()
                    if s:
                        seen_sss[s].add((cat, sub, ssub))
    for sss, parents in seen_sss.items():
        if len(parents) == 1:
            rev_sss_to_trip[sss] = next(iter(parents))

    def canonicalize_segments(segs: list[str]) -> list[str]:
        s = [x for x in segs if x and str(x).strip()]
        if not s:
            return s
        if len(s) == 1:
            name = s[0]
            if name in rev_sub_to_cat:
                return [rev_sub_to_cat[name], name]
            if name in rev_ssub_to_pair:
                c, sub = rev_ssub_to_pair[name]
                return [c, sub, name]
            if name in rev_sss_to_trip:
                c, sub, ssub = rev_sss_to_trip[name]
                return [c, sub, ssub, name]
            return s
        return s

    def parse_any_date(s: str) -> Optional[date]:
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except Exception:
                pass
        return None

    months_all_sorted = sorted(summary.keys(), key=_norm_month)
    since_day = parse_any_date(since_date) if since_date else None
    since_month_from_day = f"{since_day.year:04d}-{since_day.month:02d}" if since_day else None
    clip_key = (since or since_month_from_day)
    if clip_key:
        s = clip_key.strip()[:7]
        months_all_sorted = [k for k in months_all_sorted if _norm_month(k) >= s]

    months_sel = months_all_sorted[-max(1, months_back):]
    months = [_norm_month(k) for k in months_sel]

    bucket = defaultdict(lambda: [0.0] * len(months))
    paths: Dict[str, tuple[str, ...]] = {}

    def add_amount(path_segs: list[str], i: int, amt: float):
        segs = canonicalize_segments(path_segs)
        if not segs:
            return
        full_path = " / ".join(segs)
        paths[full_path] = tuple(segs)
        bucket[full_path][i] += max(0.0, amt)

    def tx_amount_on_or_after(node: Dict[str, Any], cutoff: date) -> float:
        txs = node.get("transactions") or []
        if not txs:
            return -1.0
        subtotal = 0.0
        seen_any = False
        for tx in txs:
            try:
                d = datetime.strptime(str(tx.get("date"))[:10], "%Y-%m-%d").date()
            except Exception:
                try:
                    d = datetime.strptime(str(tx.get("date"))[:10], "%m/%d/%Y").date()
                except Exception:
                    continue
            if d >= cutoff:
                seen_any = True
                try:
                    a = float(tx.get("amount", tx.get("amt", 0.0)))
                except Exception:
                    a = 0.0
                subtotal += abs(a)
        return subtotal if seen_any else 0.0

    def walk(node: Dict[str, Any], path: list[str], month_idx: int, m_key_norm: str) -> float:
        name = (node.get("name") or "Uncategorized").strip() or "Uncategorized"
        children = node.get("children") or []
        this_path = path + [name]
        if children:
            subtotal = 0.0
            for ch in children:
                subtotal += walk(ch, this_path, month_idx, m_key_norm)
            if subtotal == 0.0:
                if since_day and since_month_from_day == m_key_norm:
                    partial = tx_amount_on_or_after(node, since_day)
                    if partial > 0.0:
                        add_amount(this_path, month_idx, partial)
                        return partial
                total_here = abs(float(node.get("total") or 0.0))
                if total_here > 0.0:
                    add_amount(this_path, month_idx, total_here)
                    return total_here
            return subtotal
        if since_day and since_month_from_day == m_key_norm:
            partial = tx_amount_on_or_after(node, since_day)
            if partial > 0.0:
                add_amount(this_path, month_idx, partial)
                return partial
            if partial == 0.0:
                return 0.0
        total_here = abs(float(node.get("total") or 0.0))
        if total_here > 0.0:
            add_amount(this_path, month_idx, total_here)
        return total_here

    for i, raw_mkey in enumerate(months_sel):
        m_key_norm = _norm_month(raw_mkey)
        month_blob = summary.get(raw_mkey) or {}
        for top in (month_blob.get("tree") or []):
            walk(top, [], i, m_key_norm)

    categories = []
    for n, arr in bucket.items():
        if any(v > 0 for v in arr):
            categories.append({"name": n, "path": list(paths.get(n, (n,))), "monthly": arr})
    categories.sort(key=lambda c: sum(c["monthly"]), reverse=True)
    return {"months": months, "categories": categories}

def build_top_level_monthly_from_summary(summary, months_back=12, since_date=None):
    """
    Build a charts payload that:
      • Uses the SAME totals as the summary cards
      • Emits true top-level rows (['Income'], ['Groceries'], …) for overview
      • Also emits deeper LEAVES (['Income','Employer'], ['Groceries','Trader Joe’s'], …)
        without double-counting (parents are only added when a branch has no children)
    """
    # Accept either the whole blob or just the monthly map
    msum = summary.get("monthly_summaries") if isinstance(summary, dict) else None
    if not isinstance(msum, dict):
        msum = summary or {}

    # Months (ascending)
    month_keys = sorted(msum.keys())
    if since_date:
        ym_cut = str(since_date)[:7]
        month_keys = [m for m in month_keys if m >= ym_cut]
    if months_back:
        try:
            n = int(months_back)
            if n > 0:
                month_keys = month_keys[-n:]
        except Exception:
            pass

    if not month_keys:
        return {"months": [], "categories": []}

    L = len(month_keys)

    # Helper: card-consistent totals
    def clamp_total(cat, v):
        return abs(float(v or 0.0))
    
    # Build top-level series (exactly what cards show)
    top_series = {}  # { 'Income': [..L..], 'Groceries': [..L..], ... }
    # And deep leaf series (paths length >= 2)
    leaves = {}      # { ('Income','Employer'): [..L..], ('Groceries','TJ'): [..L..], ... }

    def put(series_map, path_tuple, idx, val):
        key = tuple(path_tuple)
        if key not in series_map:
            series_map[key] = [0.0]*L
        series_map[key][idx] = round(float(val or 0.0), 2)

    # Walk months
    for j, ym in enumerate(month_keys):
        cats_map = (msum.get(ym) or {}).get("categories") or {}

        # 1) Top-level totals
        for cat, data in cats_map.items():
            put(top_series, (cat,), j, clamp_total(cat, data.get("total", 0.0)))

        # 2) Deep breakdown — add the deepest available per branch without double counting
        for cat, data in cats_map.items():
            total_cat = clamp_total(cat, data.get("total", 0.0))
            if total_cat <= 0:
                continue

            sub_map     = data.get("subcategories") or {}               # { sub: amt }
            subsub_map  = data.get("subsubcategories") or {}            # { sub: { ssub: amt } }
            sub3_map    = data.get("subsubsubcategories") or {}         # { sub: { ssub: { s3: amt } } }

            # 2a) Add level-3 leaves if present
            if sub3_map:
                for sub, ssubs in (sub3_map or {}).items():
                    for ssub, s3s in (ssubs or {}).items():
                        for s3, amt in (s3s or {}).items():
                            put(leaves, (cat, sub, ssub, s3), j, clamp_total(cat, amt))

            # 2b) Add level-2 leaves that do NOT have level-3 children
            if subsub_map:
                for sub, ssubs in (subsub_map or {}).items():
                    # if this (sub, ssub) exists at level-3, skip here (children already added)
                    sub3_for_sub = (sub3_map or {}).get(sub) or {}
                    for ssub, amt in (ssubs or {}).items():
                        if ssub in sub3_for_sub:
                            continue
                        put(leaves, (cat, sub, ssub), j, clamp_total(cat, amt))

            # 2c) Add level-1 leaves that do NOT have deeper children
            if sub_map:
                for sub, amt in (sub_map or {}).items():
                    if sub in (subsub_map or {}):
                        continue  # this sub has level-2 children
                    if sub in (sub3_map or {}):
                        continue  # this sub has level-3 children
                    put(leaves, (cat, sub), j, clamp_total(cat, amt))

    # Compose payload:
    #  - include ALL top-level rows (['Income'], ['Groceries'], …)
    #  - include ALL deep leaves (paths length >= 2)
    categories = []

    # top-level first (stable order: Income first then alpha)
    top_order = list(top_series.keys())
    if "Income" in top_order:
        top_order = ["Income"] + sorted([c for c in top_order if c != "Income"])
    else:
        top_order = sorted(top_order)

    for cat in top_order:
        categories.append({"name": cat, "path": [cat], "monthly": top_series[cat]})

    # then deep leaves (sorted for stability)
    for path in sorted(leaves.keys()):
        categories.append({"name": path[-1], "path": list(path), "monthly": leaves[path]})

    return {"months": month_keys, "categories": categories}

def _fingerprint_tx(date_s: str, amount: Any, orig_desc: str) -> str:
    """
    A stable fingerprint for a transaction when no transaction_id exists.
    Uses (YYYY-MM-DD, signed amount rounded to cents, UPPER(description)).
    Works well for most bank exports.
    """
    try:
        # normalize date to YYYY-MM-DD if possible
        d = _parse_any_date(date_s)
        ds = d.strftime("%Y-%m-%d") if d else (date_s or "")
    except Exception:
        ds = date_s or ""
    try:
        amt = float(amount or 0.0)
    except Exception:
        try:
            amt = float(str(amount).replace(",", ""))
        except Exception:
            amt = 0.0
    return f"{ds}|{amt:.2f}|{(orig_desc or '').strip().upper()}"

@app.get("/transactions")
def transactions_page():
    """
    Render transactions.html with data that already has:
      - desc_overrides applied
      - categories re-evaluated from the overridden description
      - Transfers/omitted items removed (uses categorized buckets)
    """
    try:
        ck, sm, *_ = _load_category_config()
        ov = _load_desc_overrides()
        monthly = generate_summary(ck, sm, desc_overrides=ov)
        _apply_hide_rules_to_summary(monthly)
        _rebuild_categories_from_tree(monthly)

        rows = _flatten_display_transactions(monthly)

    except Exception as e:
        try:
            app.logger.exception("generate_summary() failed on /transactions: %s", e)
        except Exception: pass
        monthly = {}

    rows = _flatten_display_transactions(monthly)

    def _dt(tx):
        return _parse_any_date(tx.get("date") or "") or datetime.min

    transactions = sorted(rows, key=_dt, reverse=True)

    return render_template(
        "transactions.html",
        transactions=transactions,   # now filtered + re-categorized
        summary_data=monthly,
    )




@app.post("/api/tx/edit_description")
def edit_description():
    """
    Body (JSON):
      {
        "transaction_id": "optional",
        "date": "YYYY-MM-DD",
        "amount": -34.56,                 # bank-signed amount is fine
        "original_description": "STRAIGHT TALK *... (bank text)",
        "new_description": "WALMART"
      }
    Returns:
      { ok: true, new_description: "...", new_category: "..." }
    """
    try:
        payload = request.get_json(force=True) or {}
        txid  = (payload.get("transaction_id") or "").strip()
        date_s = (payload.get("date") or "")[:10]
        try:
            amt = float(payload.get("amount") or 0.0)
        except Exception:
            amt = 0.0
        orig = (payload.get("original_description") or "").strip()
        newd = (payload.get("new_description") or "").strip()

        if not newd:
            return jsonify({"ok": False, "error": "new_description required"}), 400

        # --- Load & update overrides using shared helpers ---
        ov = _load_desc_overrides()
        by_id = ov.get("by_txid", {}) or {}
        by_fp = ov.get("by_fingerprint", {}) or {}

        # Prefer txid mapping when available
        if txid:
            by_id[txid] = newd

        # Also store robust fingerprint (both signs to be safe)
        if date_s and orig:
            k1 = _fingerprint_tx(date_s, amt, orig)
            k2 = _fingerprint_tx(date_s, -amt, orig)
            by_fp[k1] = newd
            by_fp[k2] = newd

        ov["by_txid"] = by_id
        ov["by_fingerprint"] = by_fp

        # Persist via helper (writes to get_statements_base_dir()/desc_overrides.json)
        _save_desc_overrides(ov)

        # --- Bust caches (safe if absent) ---
        mc = globals().get("_MONTHLY_CACHE")
        if isinstance(mc, dict):
            mc["monthly"]  = None
            mc["key"]      = None
            mc["built_at"] = 0.0

        # --- Compute new category from edited description for immediate UI feedback ---
        ck, sm, *_ = _load_category_config()
        new_cat = categorize_transaction(newd, abs(amt), ck) or ""

        return jsonify({"ok": True, "new_description": newd, "new_category": new_cat})
    except Exception as e:
        try:
            app.logger.exception("edit_description failed: %s", e)
        except Exception:
            pass
        return jsonify({"ok": False, "error": "internal error"}), 500




# ================= END: TRANSACTIONS PAGE + DESCRIPTION EDIT API ===============


# ------------------ Explorer ------------------
@app.route("/explorer")
def all_items_explorer():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)

    _apply_hide_rules_to_summary(summary)
    cat_monthly = build_cat_monthly_from_summary(
        summary,
        months_back=int(request.args.get("months", "12") or 12),
        since=request.args.get("since"),
        since_date=request.args.get("since_date"),
    )

    return render_template("all_items_explorer.html", cat_monthly=cat_monthly)


# ------------------ ALL CATEGORIES (deep tree) ------------------
@app.route("/all-categories", endpoint="all_categories_page")
def all_categories_page():
    cfg_live = load_cfg()
    ov = _load_desc_overrides()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"], desc_overrides=ov)

    _apply_hide_rules_to_summary(summary)
    cat_monthly = build_cat_monthly_from_summary(
        summary,
        months_back=int(request.args.get("months", "12") or 12),
        since=request.args.get("since"),
        since_date=request.args.get("since_date"),
    )
    return render_template("all_categories.html", cat_monthly=cat_monthly)

# ------------------ helpers: cfg children ------------------
def _cfg_top_names(cfg_live: Dict[str, Any]) -> List[str]:
    names = set()
    names.update((cfg_live.get("SUBCATEGORY_MAPS") or {}).keys())
    names.update((cfg_live.get("CATEGORY_KEYWORDS") or {}).keys())
    names.update((cfg_live.get("SUBSUBCATEGORY_MAPS") or {}).keys())
    names.update((cfg_live.get("SUBSUBSUBCATEGORY_MAPS") or {}).keys())
    return sorted(n for n in names if n)

def _cfg_children_for(level: str, cat: str, sub: str, ssub: str, cfg_live: Dict[str, Any]) -> List[str]:
    smap = cfg_live.get("SUBCATEGORY_MAPS") or {}
    ssmap = cfg_live.get("SUBSUBCATEGORY_MAPS") or {}
    sssmap = cfg_live.get("SUBSUBSUBCATEGORY_MAPS") or {}
    if not cat:
        return _cfg_top_names(cfg_live)
    level = (level or "category").lower()
    if level == "category":
        return sorted((smap.get(cat, {}) or {}).keys())
    if level == "subcategory" and sub:
        return sorted(((ssmap.get(cat, {}) or {}).get(sub, {}) or {}).keys())
    if level == "subsubcategory" and sub and ssub:
        return sorted((((sssmap.get(cat, {}) or {}).get(sub, {}) or {}).get(ssub, {}) or {}).keys())
    return []

# ------------------ PATH TRANSACTIONS API (for drawer drill) ------------------
def _find_node_by_path(tree: List[Dict[str, Any]], path: List[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    curr_list = tree or []
    node = None
    for seg in path:
        seg = (seg or "").strip()
        found = None
        for n in curr_list:
            if (n.get("name") or "").strip() == seg:
                found = n
                break
        if not found:
            return None
        node = found
        curr_list = node.get("children") or []
    return node

# Make sure these are imported somewhere near the top of app.py:
# from flask import request, jsonify
# from truist.admin_categories import load_cfg
# from truist.parser_web import generate_summary, get_transactions_for_path, _parse_any_date

# DEPRECATED: legacy alias — forwards to the override-aware handler
@app.get("/api/txns_for_path")
def api_txns_for_path_compat():
    app.logger.warning("DEPRECATED /api/txns_for_path called; using /api/path/transactions")
    return api_path_transactions()


@app.get("/api/path/transactions")
def api_path_transactions():
    level = (request.args.get("level") or "category").strip().lower()
    cat = request.args.get("cat") or ""
    sub = request.args.get("sub") or ""
    ssub = request.args.get("ssub") or ""
    sss = request.args.get("sss") or ""

    month_raw = (request.args.get("month") or "").strip().lower()   # "YYYY-MM", "all", or ""
    show_all_months = month_raw in {"all", "*"}
    months_param = (request.args.get("months") or "").strip().lower()
    if months_param == "all":
        months_back = 10**9
    else:
        try:
            months_back = int(months_param) if months_param else 12
        except Exception:
            months_back = 12

    since = (request.args.get("since") or "").strip()
    since_date = (request.args.get("since_date") or "").strip()

    # Build all months (already pruned/categorized)
    monthly, cfg_live = build_monthly()
    months_all_sorted = sorted(monthly.keys(), key=_norm_month)

    if not months_all_sorted:
        return jsonify({
            "ok": True,
            "path": [],
            "month": ("all" if show_all_months else ""),
            "months": [],
            "transactions": [],
            "children": _cfg_top_names(cfg_live),
            "total": 0.0,
            "magnitude_total": 0.0
        })

    # Clip by since / since_date if provided
    since_month_key = None
    if since:
        since_month_key = since[:7]
    elif since_date:
        d = _parse_any_date(since_date)
        if d:
            since_month_key = f"{d.year:04d}-{d.month:02d}"
    if since_month_key:
        months_all_sorted = [k for k in months_all_sorted if _norm_month(k) >= since_month_key]

    months_sel = months_all_sorted[-max(1, months_back):]
    months_norm = [_norm_month(k) for k in months_sel]

    # Focus month (default to latest) unless showing all
    focus_key = None
    if not show_all_months:
        if month_raw:
            for k in months_sel:
                if _norm_month(k) == month_raw[:7]:
                    focus_key = k
                    break
        if not focus_key:
            focus_key = months_sel[-1]
    focus_norm = _norm_month(focus_key) if focus_key else None

    # Build path parts from query
    parts = []
    if cat: parts.append(cat)
    if level in {"subcategory", "subsubcategory", "subsubsubcategory"} and sub: parts.append(sub)
    if level in {"subsubcategory", "subsubsubcategory"} and ssub: parts.append(ssub)
    if level in {"subsubsubcategory"} and sss: parts.append(sss)

    # Hide only special transfer amounts (±10002.02)
    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _hidden(a: float) -> bool:
        try: aa = float(a)
        except Exception: return False
        return any(abs(aa - h) < EPS for h in HIDE_AMOUNTS)

    # Collect transactions across the selected window (filter to focus later if needed)
    txs = []
    children_from_tree = set()

    for mk in months_sel:
        blob = monthly.get(mk, {}) or {}
        tree = blob.get("tree") or []
        node = _find_node_by_path(tree, parts) if parts else None

        if node:
            # children for drill UI
            for ch in (node.get("children") or []):
                nm = (ch.get("name") or "").strip()
                if nm:
                    children_from_tree.add(nm)

            def gather(n):
                ch = n.get("children") or []
                if ch:
                    for c in ch:
                        gather(c)
                else:
                    for t in (n.get("transactions") or []):
                        try:
                            amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
                        except Exception:
                            amt = 0.0
                        if _hidden(amt): continue
                        txs.append({
                            "date": t.get("date", ""),
                            "description": t.get("description", t.get("desc", "")),
                            "amount": amt,
                            "category": t.get("category", ""),
                            "subcategory": t.get("subcategory", ""),
                        })
            gather(node)
        else:
            # No exact node match
            if not parts:
                # Top-level: list top-level children and gather ALL txs (for the window)
                for top in (tree or []):
                    nm = (top.get("name") or "").strip()
                    if nm:
                        children_from_tree.add(nm)

                def gather_all(n):
                    ch = n.get("children") or []
                    if ch:
                        for c in ch:
                            gather_all(c)
                    else:
                        for t in (n.get("transactions") or []):
                            try:
                                amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
                            except Exception:
                                amt = 0.0
                            if _hidden(amt): continue
                            txs.append({
                                "date": t.get("date", ""),
                                "description": t.get("description", t.get("desc", "")),
                                "amount": amt,
                                "category": t.get("category", ""),
                                "subcategory": t.get("subcategory", ""),
                            })
                for top in (tree or []):
                    gather_all(top)
            else:
                # Path specified but doesn’t exist in this month — don’t gather “all” here.
                pass

    # Keep a copy before focus-filtering
    txs_all_months_for_path = list(txs)

    # Focus filter (skip if showing all months)
    def _month_key(datestr: str) -> str:
        dt = _parse_any_date(datestr or "")
        return dt.strftime("%Y-%m") if dt else ""

    if not show_all_months:
        txs = [t for t in txs if _month_key(t.get("date")) == (focus_norm or "")]

    # Fallback: if path selected and focused month is empty, show all-months for that path
    used_fallback = False
    if (parts and not show_all_months and not txs):
        txs = txs_all_months_for_path
        used_fallback = True

    # Backfill category/subcategory from requested path
    if cat:
        for t in txs:
            if not (t.get("category") or "").strip():
                t["category"] = cat
    if sub and level in {"subcategory", "subsubcategory", "subsubsubcategory"}:
        for t in txs:
            if not (t.get("subcategory") or "").strip():
                t["subcategory"] = sub

    # Merge cfg-derived children with tree-observed children
    cfg_children = _cfg_children_for(level, cat, sub, ssub, cfg_live)
    children_set = set(cfg_children)
    children_set.update(children_from_tree)
    children = sorted(c for c in children_set if c)

    # Sort newest-first
    def _key_tx(t):
        dt = _parse_any_date(t.get("date", "")) or datetime(1970, 1, 1)
        return (dt, abs(float(t.get("amount", 0.0))))
    txs.sort(key=_key_tx, reverse=True)

    total = sum(float(t["amount"]) for t in txs)
    magnitude_total = sum(abs(float(t["amount"])) for t in txs)

    return jsonify({
        "ok": True,
        "path": parts,
        "month": ("all" if show_all_months else (focus_norm or "")),
        "months": months_norm,
        "transactions": txs,
        "children": children,
        "total": total,
        "magnitude_total": magnitude_total,
        "fallback_all_months_for_path": used_fallback
    })


# Serve BOTH spellings so old/new JS keep working
@app.get("/api/recent-activity")
@app.get("/api/recent_activity")
def api_recent_activity():
    """
    Dashboard snapshot used by index.html.
    Rebuilds with the latest description overrides so edits show up immediately.
    """
    try:
        # recent_activity_summary already uses generate_summary(..., desc_overrides=ov)
        data = recent_activity_summary()
    except Exception as e:
        data = {
            "as_of": None,
            "latest_month": None,
            "prev_month": None,
            "latest_totals": {
                "income": 0.0, "expense": 0.0, "net": 0.0,
                "prev_income": 0.0, "prev_expense": 0.0, "prev_net": 0.0,
                "delta_income": 0.0, "delta_expense": 0.0, "delta_net": 0.0,
                "pct_income": None, "pct_expense": None, "pct_net": None,
            },
            "movers_abs": [],
            "top_ups": [],
            "top_downs": [],
            "recent_txs": [],
            "recent_windows": {
                "last_7_expense": 0.0, "last_7_income": 0.0,
                "last_30_expense": 0.0, "last_30_income": 0.0,
            },
            "error": str(e),
        }
    return jsonify({"data": data})


# ------------------ SUBSCRIPTIONS API ------------------
@app.get("/api/subscriptions")
def api_subscriptions():
    # window handling (default 30 days; allow ?win=all or any integer)
    win_raw = (request.args.get("win") or "").strip().lower()
    today = datetime.today()
    cutoff = None
    if win_raw in {"", "30"}:
        cutoff = today - timedelta(days=30)
    elif win_raw in {"all", "*"}:
        cutoff = None
    elif win_raw.isdigit():
        cutoff = today - timedelta(days=int(win_raw))
    else:
        cutoff = today - timedelta(days=30)

    # Unified monthly (pruned + categories rebuilt)
    monthly, cfg_live = build_monthly()

    txs: List[Dict[str, Any]] = []
    for _, blob in monthly.items():
        cats = (blob.get("categories") or {})
        subcat = cats.get("Subscriptions") or {}
        for t in (subcat.get("transactions") or []):
            try:
                amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
            except Exception:
                amt = 0.0
            txs.append({
                "date": t.get("date", ""),
                "description": t.get("description", t.get("desc", "")),
                "amount": amt,
                "category": t.get("category", "") or "Subscriptions",
                "subcategory": t.get("subcategory", ""),
            })

    def _parse(dt): return _parse_any_date(dt) if dt else None
    if cutoff:
        txs = [t for t in txs if (_parse(t["date"]) and _parse(t["date"]) >= cutoff)]

    def norm_merchant(desc: str) -> str:
        if not desc: return "(unknown)"
        s = str(desc).upper()
        # normalize broadly: remove punctuation and digits → spaces
        for ch in "0123456789'\"*#-_.\\/(),[]:;@!&+$%^~?{}<>=|":
            s = s.replace(ch, " ")
        noise = ("ONLINE","PURCHASE","PAYMENT","AUTOPAY","SUBSCRIPTION","RECURRING","WWW","COM","INC","LLC","CORP","THE")
        for w in noise:
            s = s.replace(f" {w} ", " ")
        s = " ".join(s.split()).strip()
        return s or "(unknown)"

    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in txs:
        buckets[norm_merchant(t["description"])].append(t)

    merchants = []
    for m, arr in buckets.items():
        total = 0.0
        last_dt: Optional[datetime] = None
        for t in arr:
            try:
                amt = float(t.get("amount", 0.0) or 0.0)
            except Exception:
                amt = 0.0
            total += abs(amt)
            d = _parse_any_date(t.get("date", ""))
            if d:
                dd = datetime(d.year, d.month, d.day)
                if (last_dt is None) or (dd > last_dt):
                    last_dt = dd
        avg = (total / len(arr)) if arr else 0.0
        merchants.append({
            "merchant": m,
            "count": len(arr),
            "total": round(total, 2),
            "avg": round(avg, 2),
            "last": last_dt.strftime("%Y-%m-%d") if last_dt else ""
        })
    merchants.sort(key=lambda r: r["total"], reverse=True)

    def _tx_key(t):
        d = _parse_any_date(t.get("date", "")) or datetime(1970, 1, 1)
        try: a = abs(float(t.get("amount", 0.0)))
        except Exception: a = 0.0
        return (d, a)
    txs.sort(key=_tx_key, reverse=True)

    win_echo = "all" if cutoff is None else str((today - cutoff).days)
    return jsonify({"ok": True, "window": win_echo, "transactions": txs, "merchants": merchants})

# ------------------ RECURRING PAGE + API ------------------
@app.route("/recurring", endpoint="recurring_page")
def recurring_page():
    return render_template("recurring.html")

@app.get("/api/recurrents")
def api_recurrents():
    # (The full implementation from your previous message is retained.)
    # NOTE: If you need the *entire* recurring analyzer content restored verbatim and it differed from this,
    # drop me the last known good copy and I'll merge it 1:1.
    import importlib
    from collections import defaultdict as _dd
    from truist import recurring_config as RC

    # Pick up config edits without restart
    RC = importlib.reload(RC)

    # ---- Query params (default 30-day window; allow ?win=all or any integer) ----
    win_raw = (request.args.get("win") or "").strip().lower()
    horizon = int(request.args.get("horizon") or 30)
    min_occ = int(request.args.get("min_occ") or 2)
    top_n = int(request.args.get("top_n") or 8)  # how many top fixed bills to return

    today = datetime.today().date()
    cutoff = None  # date cutoff
    if win_raw in {"", "30"}:
        cutoff = today - timedelta(days=30)
    elif win_raw in {"all", "*"}:
        cutoff = None
    elif win_raw.isdigit():
        cutoff = today - timedelta(days=int(win_raw))
    else:
        cutoff = today - timedelta(days=30)

    # ---- Config helpers
    def _as_list(val):
        if not val: return []
        if isinstance(val, (list, tuple)): return [str(x) for x in val]
        return [str(val)]

    # Pull + normalize config
    RC_CATS = set(x.upper() for x in _as_list(getattr(RC, "RECURRING_CATEGORIES", [])))
    RC_MERCH_RAW = [x for x in _as_list(getattr(RC, "RECURRING_MERCHANTS", []))]
    RC_KEYS = [x.upper() for x in _as_list(getattr(RC, "RECURRING_KEYWORDS", []))]
    RC_DENY = [x.upper() for x in _as_list(getattr(RC, "DENY_MERCHANTS", []))]
    RC_DENY_SUBCATS = [x.upper() for x in _as_list(getattr(RC, "DENY_SUBCATEGORIES", []))]
    RC_TWO_PM = [x.upper() for x in _as_list(getattr(RC, "TWO_PER_MONTH_MERCHANTS", []))]
    RC_INCOME_KEYS = [x.upper() for x in _as_list(getattr(RC, "RECURRING_INCOME_KEYWORDS", []))]
    RC_SPLIT_BY_AMT = [x.upper() for x in _as_list(getattr(RC, "SPLIT_VENDOR_BY_AMOUNT", []))]
    RC_AMT_LABELS = getattr(RC, "AMOUNT_LABELS", {}) or {}
    RC_VAR_TOL_MAP = getattr(RC, "VARIANCE_TOLERANCE", {}) or {}
    RC_BI_CAP_MAP = getattr(RC, "BIWEEKLY_MAX_PER_MONTH", {}) or {}
    MISSED_GRACE_DAYS = int(getattr(RC, "MISSED_GRACE_DAYS", 7))

    # Variable income config (mobile deposits + tips)
    VINC = getattr(RC, "VARIABLE_INCOME", {}) or {}
    VINC_ENABLED = bool(VINC.get("ENABLED", True))
    VINC_WINDOW = int(VINC.get("WINDOW_DAYS", 120))
    VINC_MIN_WEEKS = int(VINC.get("MIN_WEEKS", 3))
    VINC_TRIM_PCT = float(VINC.get("TRIM_PCT", 0.20))
    VINC_INC_MERCH = [str(x).upper() for x in _as_list(VINC.get("INCLUDE_MERCHANTS", []))]
    VINC_INC_KEYS = [str(x).upper() for x in _as_list(VINC.get("INCLUDE_KEYWORDS", []))]
    VINC_INC_SUB = [str(x).upper() for x in _as_list(VINC.get("INCLUDE_SUBCATEGORIES", []))]
    VINC_EXC_MERCH = [str(x).upper() for x in _as_list(VINC.get("EXCLUDE_MERCHANTS", []))]

    # ---- Summary data
    monthly, cfg_live = build_monthly()
    months_sorted = sorted(monthly.keys(), key=_norm_month)

    # ---- Helper normalizers/hard filters
    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _hidden_amt(a: float) -> bool:
        try: aa = float(a)
        except Exception: return False
        return any(abs(aa - h) < EPS for h in HIDE_AMOUNTS)

    def _d(s):
        dt = _parse_any_date(s or "")
        return (dt.date() if hasattr(dt, "date") else dt) if dt else None

    def _cmp(s: str) -> str:
        s = (s or "").upper()
        return "".join(ch for ch in s if ch.isalnum())

    ALLOW_CMP_MAP = { _cmp(orig): orig for orig in RC_MERCH_RAW }
    ALLOW_CMP = list(ALLOW_CMP_MAP.keys())
    DENY_CMP = [_cmp(x) for x in RC_DENY]
    DENY_SUBCATS_CMP = [_cmp(x) for x in RC_DENY_SUBCATS]
    SPLIT_CMP = [_cmp(x) for x in RC_SPLIT_BY_AMT]
    VAR_TOL_CMP = { _cmp(k): float(v) for k, v in RC_VAR_TOL_MAP.items() }
    BI_CAP_CMP = { _cmp(k): int(v) for k, v in RC_BI_CAP_MAP.items() }
    ALLOW_SINGLE_CMP = [_cmp(x) for x in _as_list(getattr(RC, "ALLOW_SINGLE_OCCURRENCES", []))]

    CC_DENY_MERCH = [
        "AMEX","AMERICAN EXPRESS","DISCOVER","CAPITAL ONE","CHASE CARD","CITI CARD","CITICARD",
        "BARCLAY","BARCLAYCARD","WELLS FARGO CARD","US BANK CARD","CARDMEMBER SERVICES",
        "SYNCHRONY","SYNCB","APPLE CARD","GOLDMAN SACHS BANK","ELAN FINANCIAL",
        "BANK OF AMERICA CARD","BOA CARD","NAVY FEDERAL CARD","BANKCARD","CREDIT CARD"
    ]
    CC_HINTS = ["CREDIT CARD","CARD PAYMENT","CC PAYMENT","CC PYMT","CARDMEMBER","CARD SERVICES"]
    CC_SUBCATS = ["CREDIT CARD","CREDIT CARDS","CREDIT CARD PAYMENT"]
    CC_DENY_CMP = [_cmp(x) for x in (CC_DENY_MERCH + CC_HINTS)] + [_cmp(x) for x in _as_list(getattr(RC, "CREDIT_CARD_DENY_MERCHANTS", []))]
    CC_SUBCATS_CMP = [_cmp(x) for x in (CC_SUBCATS + _as_list(getattr(RC, "CREDIT_CARD_DENY_SUBCATEGORIES", [])))]

    def is_credit_card_like(raw_desc: str, subcat: str, cat_top: str) -> bool:
        d = _cmp(raw_desc)
        if any(h in d for h in CC_DENY_CMP):
            return True
        sc = _cmp(subcat or "")
        if sc and any(sc == h for h in CC_SUBCATS_CMP):
            return True
        ct = _cmp(cat_top or "")
        if "CREDITCARD" in ct or "CREDITCARDS" in ct:
            return True
        return False

    def force_monthly_vendor(vkey: str) -> bool:
        k = _cmp(vkey)
        return any(tag in k for tag in ("ADOBE","VERIZON","OPENAI","OPENAIINC","OPENAIAPI","OPENAICOM"))

    def is_sams_vendor(vkey: str) -> bool:
        k = _cmp(vkey)
        return any(tag in k for tag in ("SAMSCLUB","SAMSCLUBMEMBERSHIP","SAMS","SAM SCLUB"))

    def norm_merchant(desc: str) -> str:
        if not desc: return "(unknown)"
        s = str(desc).upper()
        for ch in "0123456789'\"*#-_.\\/(),[]:;@!&+$%^~?{}<>=|":
            s = s.replace(ch, " ")
        for w in ("ONLINE","PURCHASE","PAYMENT","AUTOPAY","SUBSCRIPTION","RECURRING","WWW","COM","INC","LLC","CORP","THE"):
            s = s.replace(f" {w} ", " ")
        return " ".join(s.split()).strip() or "(unknown)"

    CANON = getattr(RC, "CANONICAL_VENDOR_ALIASES", {}) or {}
    _CANON_REV = {}
    for canon_name, variants in CANON.items():
        for v in (variants or []):
            v_cmp = "".join(ch for ch in str(v).upper() if ch.isalnum())
            if v_cmp:
                _CANON_REV[v_cmp] = canon_name

    def canonical_vendor_key(raw_desc: str, fallback_norm: str) -> str:
        desc_cmp = _cmp(raw_desc)
        matches = [ALLOW_CMP_MAP[k] for k in ALLOW_CMP if k in desc_cmp]
        if matches:
            chosen = max(matches, key=lambda x: len(_cmp(x)))
            chosen_cmp = _cmp(chosen)
            if chosen_cmp in _CANON_REV:
                return _CANON_REV[chosen_cmp]
            return chosen
        return fallback_norm

    def allow_tx(cat_top: str, subcat: str, raw_desc: str, amt: float) -> bool:
        cat_up = (cat_top or "").upper()
        desc_up = (raw_desc or "").upper()
        merch_cmp = _cmp(raw_desc)
        subcat_cmp = _cmp(subcat or "")

        if is_credit_card_like(raw_desc, subcat, cat_top):
            return False

        # HOT-FIX: Sarasota water via Paymentus
        if (("PAYMENTUS" in desc_up and "SARASOTA" in desc_up) or ("SARASOTA" in desc_up and "UTILIT" in desc_up)):
            return True

        if subcat_cmp and any(subcat_cmp == d for d in DENY_SUBCATS_CMP):
            return False
        if any(d in merch_cmp for d in DENY_CMP):
            return False
        if any(m in merch_cmp for m in ALLOW_CMP):
            return True
        if cat_up == "INCOME" and any(k in desc_up for k in RC_INCOME_KEYS):
            return True
        if cat_up in RC_CATS:
            return True
        if any(k in desc_up for k in RC_KEYS):
            return True
        return False

    def looks_like_income(rows_subset, merch_key):
        key_cmp = _cmp(merch_key)
        income_key_cmps = [_cmp(x) for x in RC_INCOME_KEYS]
        if any(ik in key_cmp for ik in income_key_cmps):
            return True
        for r in rows_subset:
            if (r.get("category","").strip().upper() == "INCOME"):
                return True
            desc_cmp = _cmp(r.get("description",""))
            if any(ik in desc_cmp for ik in income_key_cmps):
                return True
        return False

    # ---- Flatten eligible txs for recurring streams
    flat: List[Dict[str, Any]] = []

    def _gather(node, out_list, top_name):
        ch = node.get("children") or []
        if ch:
            for c in ch:
                _gather(c, out_list, top_name)
        else:
            for t in (node.get("transactions") or []):
                try:
                    amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
                except Exception:
                    amt = 0.0
                if _hidden_amt(amt):
                    continue
                d = _d(t.get("date",""))
                if cutoff and (not d or d < cutoff):
                    continue
                raw_desc = (t.get("description") or t.get("desc","") or "")
                cat = (t.get("category","") or top_name or "").strip()
                subcat = (t.get("subcategory","") or "").strip()
                if allow_tx(cat, subcat, raw_desc, amt):
                    merch_norm = norm_merchant(raw_desc)
                    merch_key = canonical_vendor_key(raw_desc, merch_norm)
                    out_list.append({
                        "date": t.get("date",""),
                        "description": raw_desc,
                        "amount": amt,
                        "category": cat,
                        "subcategory": subcat,
                        "merchant_norm": merch_norm,
                        "merchant_key": merch_key,
                        "cat_top": cat or top_name or "",
                    })

    for mk in months_sorted:
        blob = monthly.get(mk, {}) or {}
        for top in (blob.get("tree") or []):
            _gather(top, flat, (top.get("name") or "").strip())

    if not flat and not VINC_ENABLED:
        return jsonify({
            "ok": True, "window": "30", "horizon": horizon,
            "streams": [], "upcoming": [], "by_week": [], "by_month": [], "transactions": [],
            "floor": 0.0, "floor_by_category": [],
            "income_expected": 0.0, "income_recurring": 0.0, "variable_income_monthly": 0.0,
            "variable_income_weekly": 0.0, "variable_income_weeks_used": 0,
            "leftover": 0.0, "this_week_due": 0.0, "top_fixed_bills": [], "top_fixed_merchants": [],
            "by_week_net": [], "projected_month": {}, "changes": {"month": None, "new": [], "stopped": [], "price_changes": []},
        })

    # ---- Cluster + build streams
    def median(nums):
        nums = sorted(nums); n = len(nums)
        if n == 0: return 0.0
        mid = n // 2
        return nums[mid] if (n % 2 == 1) else (nums[mid-1] + nums[mid]) / 2.0

    def cadence_from_days(days: float):
        if days <= 0: return ("unknown", None)
        if 26 <= days <= 35: return ("monthly", ("months", 1))
        if 11 <= days <= 17: return ("biweekly", ("days", 14))
        if 50 <= days <= 75: return ("bi-monthly", ("months", 2))
        if 80 <= days <= 105: return ("quarterly", ("months", 3))
        if 350 <= days <= 390: return ("annual", ("years", 1))
        return ("unknown", ("days", int(round(days))))

    by_merch = _dd(list)
    for t in flat:
        by_merch[t["merchant_key"]].append(t)

    def is_two_per_month(merchant_norm_or_key: str) -> bool:
        m = (merchant_norm_or_key or "").upper()
        return any(k in m for k in RC_TWO_PM)

    def biweekly_cap_for(merchant_key: str, rows_subset) -> int:
        cmpk = _cmp(merchant_key)
        if cmpk in BI_CAP_CMP: return BI_CAP_CMP[cmpk]
        if looks_like_income(rows_subset, merchant_key): return 3
        return 2 if is_two_per_month(merchant_key) else 1

    def label_for(merch, rep_amount: float, fallback_norms: list[str]) -> str:
        key_up = (merch or "").upper()
        labels = {}
        for k, mapping in (RC_AMT_LABELS or {}).items():
            if key_up.find((k or "").upper()) != -1:
                labels.update(mapping or {})
        cents = int(round(rep_amount * 100))
        for amt, lbl in labels.items():
            if int(round(float(amt) * 100)) == cents:
                return lbl
        if fallback_norms:
            counts = _dd(int)
            for nm in fallback_norms:
                counts[nm] += 1
            return max(counts.items(), key=lambda kv: kv[1])[0]
        return merch or "(unknown)"

    streams: List[Dict[str, Any]] = []
    streams_tx: List[Dict[str, Any]] = []

    def emit_stream(merch, rows_subset):
        dates = [_d(r["date"]) for r in rows_subset if _d(r["date"])]
        if not dates: return
        dates.sort(reverse=True)

        # cadence detection
        if len(dates) >= 2:
            intervals = [(dates[i] - dates[i+1]).days for i in range(len(dates)-1)]
            med = median(intervals) if intervals else 0
            freq, step = cadence_from_days(med)
            if freq == "unknown":
                freq, step = ("monthly", ("months", 1))
        else:
            if is_sams_vendor(merch):
                freq, step = ("annual", ("years", 1))
            else:
                freq, step = ("monthly", ("months", 1))
        if force_monthly_vendor(merch):
            freq, step = ("monthly", ("months", 1))

        if freq == "biweekly":
            per_month = _dd(int)
            for r in rows_subset:
                d = _d(r["date"])
                if not d: continue
                key = f"{d.year:04d}-{d.month:02d}"
                per_month[key] += 1
            cap = biweekly_cap_for(merch, rows_subset)
            if any(v >= cap + 2 for v in per_month.values()):
                freq, step = ("monthly", ("months", 1))

        amts = [abs(float(r.get("amount", 0.0) or 0.0)) for r in rows_subset]
        rep_amount = round(median(amts) if amts else 0.0, 2)
        total = round(sum(amts), 2)
        cats = sorted({(r.get("category") or "").strip() for r in rows_subset if r.get("category")})[:4]
        norms = [r["merchant_norm"] for r in rows_subset if r.get("merchant_norm")]
        merchant_label = label_for(merch, rep_amount, norms)

        first = min(d for d in dates if d)
        last = max(d for d in dates if d)
        next_due = None
        if step and last:
            kind, val = step
            if kind == "days":
                next_due = last + timedelta(days=int(val))
            elif kind == "months":
                next_due = last + relativedelta(months=+int(val))
            elif kind == "years":
                next_due = last + relativedelta(years=+int(val))

        split = any(s in _cmp(merch) for s in SPLIT_CMP)
        cents_bucket = int(round(rep_amount * 100)) if split else None
        changes_key = f"{merch}|{cents_bucket}" if split else merch
        income_flag = looks_like_income(rows_subset, merch)

        for r in rows_subset:
            r["_stream_key"] = changes_key

        streams.append({
            "merchant": merchant_label,
            "amount": rep_amount,
            "count": len(rows_subset),
            "total": total,
            "categories": cats,
            "first": first.isoformat() if first else "",
            "last": last.isoformat() if last else "",
            "next": next_due.isoformat() if next_due else "",
            "freq": freq,
            "interval_days": (med if len(dates) >= 2 else None),
            "descriptions": sorted({(r.get("description") or "")[:80] for r in rows_subset if r.get("description")})[:3],
            "_key": changes_key,
            "is_income": income_flag,
        })
        streams_tx.extend(rows_subset)

    # Group & emit
    for merch, rows in by_merch.items():
        merch_cmp_key = _cmp(merch)
        vendor_priority = any(m in merch_cmp_key for m in ALLOW_CMP)
        split_by_amount = any(s in merch_cmp_key for s in SPLIT_CMP)

        if vendor_priority and split_by_amount:
            buckets = _dd(list)
            for r in rows:
                cents = int(round(abs(float(r.get("amount", 0.0))) * 100))
                buckets[cents].append(r)
            m_cmp = _cmp(merch)
            for _, subset in buckets.items():
                if len(subset) < min_occ and not looks_like_income(subset, merch):
                    if not (is_sams_vendor(merch) or any(a in m_cmp for a in ALLOW_SINGLE_CMP)):
                        continue
                emit_stream(merch, subset)
            continue

        if vendor_priority:
            m_cmp = _cmp(merch)
            if len(rows) < min_occ and not looks_like_income(rows, merch):
                if not (is_sams_vendor(merch) or any(a in m_cmp for a in ALLOW_SINGLE_CMP)):
                    continue
            emit_stream(merch, rows)
            continue

        def cluster_by_amount(rows_):
            clusters: List[List[Dict[str, Any]]] = []
            for r in rows_:
                a = abs(float(r.get("amount", 0.0) or 0.0))
                placed = False
                for cl in clusters:
                    m = median([abs(float(x.get("amount", 0.0) or 0.0)) for x in cl])
                    tol = max(3.0, 0.05 * max(m, a, 1.0))  # $3 or 5%
                    if abs(a - m) <= tol:
                        cl.append(r)
                        placed = True
                        break
                if not placed:
                    clusters.append([r])
            return clusters

        for cl in cluster_by_amount(rows):
            m_cmp = _cmp(merch)
            if len(cl) < min_occ and not looks_like_income(cl, merch):
                if not (is_sams_vendor(merch) or any(a in m_cmp for a in ALLOW_SINGLE_CMP)):
                    continue
            emit_stream(merch, cl)

    streams.sort(key=lambda s: (s["total"], s["count"]), reverse=True)

    # ---- Forecast upcoming
    horizon_end = today + timedelta(days=horizon)
    upcoming: List[Dict[str, Any]] = []

    def add_occurrences(s):
        if not s.get("next") or not s.get("freq") or s["freq"] == "unknown":
            return
        dt = _d(s["next"])
        if not dt: return
        mapping = {
            "biweekly": ("days", 14),
            "monthly": ("months", 1),
            "bi-monthly": ("months", 2),
            "quarterly": ("months", 3),
            "semiannual": ("months", 6),
            "annual": ("years", 1),
        }
        kind, step_val = mapping.get(s["freq"], ("months", 1))
        cur = dt
        while cur <= horizon_end:
            if cur >= today:
                upcoming.append({"date": cur.isoformat(), "merchant": s["merchant"], "amount": s["amount"]})
            if kind == "days":
                cur = cur + timedelta(days=step_val)
            elif kind == "months":
                cur = cur + relativedelta(months=+step_val)
            elif kind == "years":
                cur = cur + relativedelta(years=+step_val)

    for s in streams:
        add_occurrences(s)

    by_week = _dd(float)
    by_month = _dd(float)
    for ev in upcoming:
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        monday = d - timedelta(days=d.weekday())
        by_week[monday.isoformat()] += float(ev["amount"])
        by_month[d.strftime("%Y-%m")] += float(ev["amount"])

    by_week_list = [{"week": k, "total": round(v, 2)} for k, v in sorted(by_week.items())]
    by_month_list = [{"month": k, "total": round(v, 2)} for k, v in sorted(by_month.items())]

    # ---- Floor / income totals (monthly equivalents)
    def is_income_stream(s): return bool(s.get("is_income"))
    monthly_equiv_ratio = { "biweekly": 2.0, "monthly": 1.0, "bi-monthly": 1.0/2.0, "quarterly": 1.0/3.0, "semiannual": 1.0/6.0, "annual": 1.0/12.0 }

    floor_total = 0.0
    floor_by_cat_map = _dd(float)
    income_recurring = 0.0
    for s in streams:
        ratio = monthly_equiv_ratio.get(s.get("freq"), 1.0)
        monthly_equiv = float(s["amount"]) * ratio
        if is_income_stream(s):
            income_recurring += monthly_equiv
        else:
            floor_total += monthly_equiv
            top_cat = (s.get("categories") or ["Other"])[0] or "Other"
            floor_by_cat_map[top_cat] += monthly_equiv
    floor_total = round(floor_total, 2)
    income_recurring = round(income_recurring, 2)
    floor_by_category = [ {"category": k, "total": round(v, 2)} for k, v in sorted(floor_by_cat_map.items(), key=lambda kv: kv[1], reverse=True) ]

    # ======================== VARIABLE INCOME =========================
    variable_weekly = 0.0
    weeks_used = 0
    if VINC_ENABLED:
        win_cut = today - timedelta(days=VINC_WINDOW)
        week_sums = _dd(float)

        def consider_income(date_obj, amount, desc_up="", subcat_up="", is_manual=False):
            if not date_obj or date_obj < win_cut: return
            if amount <= 0: return
            hit = any(m in desc_up for m in VINC_INC_MERCH) or \
                  any(k in desc_up for k in VINC_INC_KEYS) or \
                  (subcat_up in VINC_INC_SUB if subcat_up else False)
            if not hit: return
            if any(x in desc_up for x in VINC_EXC_MERCH): return
            if any(k in desc_up for k in RC_INCOME_KEYS): return
            monday = date_obj - timedelta(days=date_obj.weekday())
            week_sums[monday] += float(amount)

        def _gather_var(node, top_name):
            ch = node.get("children") or []
            if ch:
                for c in ch:
                    _gather_var(c, top_name)
            else:
                for t in (node.get("transactions") or []):
                    d = _d(t.get("date",""))
                    try: amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
                    except Exception: amt = 0.0
                    desc_up = (t.get("description") or t.get("desc","") or "").upper()
                    sub_up = (t.get("subcategory","") or "").upper()
                    consider_income(d, amt, desc_up, sub_up)

        for mk in months_sorted:
            blob = monthly.get(mk, {}) or {}
            for top in (blob.get("tree") or []):
                _gather_var(top, (top.get("name") or "").strip())

        try:
            for tx in (load_manual_transactions(MANUAL_FILE) or []):
                d = _d(tx.get("date",""))
                try: amt = float(tx.get("amount", 0.0) or 0.0)
                except Exception: amt = 0.0
                desc_up = (tx.get("description","") or "").upper()
                sub_up = (tx.get("sub_subcategory","") or tx.get("subcategory","") or "").upper()
                consider_income(d, amt, desc_up, sub_up, is_manual=True)
        except Exception:
            pass

        weeks = sorted(week_sums.keys())
        vals = [week_sums[w] for w in weeks]
        weeks_used = len(vals)

        def trimmed_mean(values, trim_pct):
            if not values: return 0.0
            v = sorted(values)
            k = int(len(v) * max(0.0, min(0.45, float(trim_pct))))
            v2 = v[k: len(v)-k] if len(v) - 2*k > 0 else v
            return sum(v2) / len(v2) if v2 else 0.0

        if weeks_used >= VINC_MIN_WEEKS:
            variable_weekly = trimmed_mean(vals, VINC_TRIM_PCT)
        elif weeks_used > 0:
            variable_weekly = sum(vals) / weeks_used

    variable_monthly = round(variable_weekly * 4.33, 2)
    # ====================== END VARIABLE INCOME =======================

    income_expected = round(income_recurring + variable_monthly, 2)
    leftover = round(income_expected - floor_total, 2)

    # ---- This week's due (streams only; variable income is not "due")
    monday_this_week = today - timedelta(days=today.weekday())
    sunday_this_week = monday_this_week + timedelta(days=6)
    this_week_due = 0.0
    for ev in upcoming:
        d = _d(ev["date"])
        if d and monday_this_week <= d <= sunday_this_week:
            this_week_due += float(ev["amount"])
    this_week_due = round(this_week_due, 2)

    def monthly_equiv_for_stream(s):
        ratio = {"biweekly": 2.0, "monthly": 1.0, "bi-monthly": 1.0/2.0, "quarterly": 1.0/3.0, "semiannual": 1.0/6.0, "annual": 1.0/12.0}.get(s.get("freq"), 1.0)
        return round(float(s.get("amount", 0.0)) * ratio, 2)

    top_fixed_bills = []
    for s in streams:
        if s.get("is_income"): continue
        meq = monthly_equiv_for_stream(s)
        top_fixed_bills.append({
            "merchant": s.get("merchant", ""),
            "monthly_equiv": meq,
            "freq": s.get("freq", "monthly"),
            "amount_basis": float(s.get("amount", 0.0)),
            "next": s.get("next", ""),
            "last": s.get("last", ""),
            "count": int(s.get("count", 0)),
            "categories": s.get("categories", []) or [],
            "_key": s.get("_key", ""),
        })
    top_fixed_bills.sort(key=lambda r: (r["monthly_equiv"], r["count"]), reverse=True)
    if top_n > 0:
        top_fixed_bills = top_fixed_bills[:top_n]
    top_fixed_merchants = list(top_fixed_bills)

    weekly_income_expected = round((income_recurring / 4.33) + variable_weekly, 2)

    weeks_in_horizon = []
    cur = today
    start_monday = cur - timedelta(days=cur.weekday())
    end_date = today + timedelta(days=horizon)
    cur_monday = start_monday
    while cur_monday <= end_date:
        weeks_in_horizon.append(cur_monday)
        cur_monday = cur_monday + timedelta(days=7)

    by_week_map = {w: 0.0 for w in weeks_in_horizon}
    for ev in upcoming:
        d = _d(ev["date"])
        if not d: continue
        if d < start_monday or d > end_date: continue
        w = d - timedelta(days=d.weekday())
        by_week_map[w] = by_week_map.get(w, 0.0) + float(ev["amount"])

    by_week_net = []
    for w in sorted(by_week_map.keys()):
        out = round(by_week_map[w], 2)
        net = round(weekly_income_expected - out, 2)
        by_week_net.append({
            "week": w.isoformat(),
            "income_expected": weekly_income_expected,
            "bills_due": out,
            "net": net
        })

    projected_month = {
        "income_recurring": income_recurring,
        "variable_income_monthly": variable_monthly,
        "income_expected": income_expected,
        "fixed_floor": floor_total,
        "leftover": leftover
    }

    win_echo = "all" if cutoff is None else str((today - cutoff).days)
    return jsonify({
        "ok": True,
        "window": win_echo,
        "horizon": horizon,
        "streams": streams,
        "upcoming": upcoming,
        "by_week": by_week_list,
        "by_month": by_month_list,
        "transactions": streams_tx,  # each tx may include "_stream_key"
        "floor": floor_total,
        "floor_by_category": floor_by_category,
        "income_expected": income_expected,
        "income_recurring": income_recurring,
        "variable_income_monthly": variable_monthly,
        "variable_income_weekly": round(variable_weekly, 2),
        "variable_income_weeks_used": weeks_used,
        "leftover": leftover,
        "this_week_due":  this_week_due,
        "top_fixed_bills": top_fixed_bills,
        "top_fixed_merchants": top_fixed_bills,
        "by_week_net": by_week_net,
        "projected_month": projected_month,
        "changes": {"month": None, "new": [], "stopped": [], "price_changes": []},
    })

@app.get("/api/summary")
def api_summary():
    monthly = _build_monthly_live()
    return jsonify(ok=True, summary=monthly)

@app.post("/api/manual")
def api_manual_add():
    data = request.get_json(silent=True) or {}
    kind = str(data.get("kind") or data.get("type") or "").lower()  # optional

    try:
        # optional: coerce sign if UI sends kind/type
        if "amount" in data:
            data["amount"] = float(data["amount"])
            if kind == "expense" and data["amount"] > 0:
                data["amount"] = -data["amount"]
            elif kind == "income" and data["amount"] < 0:
                data["amount"] = -data["amount"]

        saved = append_manual_tx(data)

        # 🔧 bust cached monthly summary so drawer/overview pick up the new entry
        try:
            _MONTHLY_CACHE["monthly"]  = None
            _MONTHLY_CACHE["key"]      = None
            _MONTHLY_CACHE["built_at"] = 0.0
        except Exception:
            pass

        return jsonify({"ok": True, "saved": saved}), 201
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400



# ------------------ FORECAST / RUNWAY ------------------
@app.get("/api/forecast")
def api_forecast():
    try:
        weeks = int(request.args.get("weeks", "13"))
    except Exception:
        weeks = 13
    try:
        balance = float(request.args.get("balance", "0") or 0.0)
    except Exception:
        balance = 0.0

    monthly, cfg_live = build_monthly()
    months_sorted = sorted((_norm_month(k) for k in monthly.keys()))
    recent = months_sorted[-3:] if months_sorted else []

    weekly_samples = []
    for mk in recent:
        blob = monthly.get(mk.replace("-", "_")) or monthly.get(mk) or {}
        net = float(blob.get("net_cash_flow") or 0.0)
        weekly_samples.append(net / 4.33)
    if not weekly_samples:
        weekly_samples = [0.0]

    base_week = sum(weekly_samples) / len(weekly_samples)
    hi_week = base_week * 1.3
    lo_week = base_week * 0.7

    labels = []
    base_seq, hi_seq, lo_seq = [], [], []
    today = datetime.today().date()
    for i in range(weeks):
        end = today + timedelta(days=7 * (i + 1))
        labels.append(end.strftime("%Y-%m-%d"))
        base_seq.append(round(base_week, 2))
        hi_seq.append(round(hi_week, 2))
        lo_seq.append(round(lo_week, 2))

    runway_days = None
    if base_week < 0:
        try:
            runway_days = int(max(0, (balance / abs(base_week)) * 7))
        except Exception:
            runway_days = None

    return jsonify({
        "as_of": today.strftime("%Y-%m-%d"),
        "weeks": labels,
        "base": base_seq,
        "hi": hi_seq,
        "lo": lo_seq,
        "runway_days": runway_days
    })

# ------------------ ALL TRANSACTIONS: flat list & search ------------------
@app.get("/api/tx/all")
def api_tx_all():
    """
    Flat list of transactions across months, with search & filters.
    Query:
      q=... (space-separated terms in desc/category/subcategory)
      type=all|income|expense
      date_from=YYYY-MM-DD
      date_to=YYYY-MM-DD
      months=all|12|24 (default 24)
      limit=int (default 4000)
    """
    
    # Build monthly via same pipeline (overrides applied, then categorized)
    try:
        ck, sm, *_ = _load_category_config()
        ov = _load_desc_overrides()
        monthly = generate_summary(ck, sm, desc_overrides=ov)
        _apply_hide_rules_to_summary(monthly)
        _rebuild_categories_from_tree(monthly)

        rows = _flatten_display_transactions(monthly)

    except Exception as e:
        try:
            app.logger.exception("generate_summary() failed on /api/tx/all: %s", e)
        except Exception:
            pass
        monthly = {}

    # Use curated rows (already excludes Transfers/hidden/omitted)
    rows = _flatten_display_transactions(monthly)

    # Ensure client gets immutable original + a txid
    def _txid(r):
        return (r.get("transaction_id") or r.get("id") or r.get("tx_id")
                or r.get("_id") or r.get("uid") or "")

    for r in rows:
        r["original_description"] = (
            r.get("original_description") or
            r.get("description_raw") or
            r.get("description") or ""
        )
        r["transaction_id"] = _txid(r)

    # ---------- Filters (same semantics you had) ----------
    q = (request.args.get("q") or "").strip().lower()
    q_terms = [t for t in q.split() if t]
    tx_type = (request.args.get("type") or "all").lower().strip()
    df = (request.args.get("date_from") or "").strip()
    dt = (request.args.get("date_to") or "").strip()
    months_param = (request.args.get("months") or "24").strip().lower()
    try:
        limit = int(request.args.get("limit") or 4000)
    except Exception:
        limit = 4000

    # Months filter (by transaction date)
    if months_param and months_param != "all":
        try:
            n = int(months_param)
            end = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
            start = (end - relativedelta(months=n)).replace(day=1)
            def _in_last_n_months(r):
                d = _parse_any_date(r.get("date"))
                return (d is not None) and (d >= start)
            rows = list(filter(_in_last_n_months, rows))
        except Exception:
            pass

    # Date range filter
    def _in_range(r):
        d = _parse_any_date(r.get("date"))
        if not d:
            return False
        if df:
            try:
                if d < datetime.strptime(df, "%Y-%m-%d"):
                    return False
            except Exception:
                pass
        if dt:
            try:
                if d > datetime.strptime(dt, "%Y-%m-%d"):
                    return False
            except Exception:
                pass
        return True
    if df or dt:
        rows = list(filter(_in_range, rows))

    # Type filter (by category)
    if tx_type == "income":
        rows = [r for r in rows if r.get("category") == "Income"]
    elif tx_type == "expense":
        rows = [r for r in rows if r.get("category") != "Income"]

    # q filter (AND across terms)
    if q_terms:
        def _match(r):
            hay = " ".join([
                str(r.get("description", "")),
                str(r.get("category", "")),
                str(r.get("subcategory", "")),
            ]).lower()
            return all(t in hay for t in q_terms)
        rows = list(filter(_match, rows))

    # Newest first and limit
    def _dt(tx):
        return _parse_any_date(tx.get("date") or "") or datetime.min
    rows.sort(key=_dt, reverse=True)

    rows = rows[: max(1, limit)]
    return jsonify({"transactions": rows})


# ------------------ MAIN ------------------
if __name__ == "__main__":
    app.run(debug=True)

# ======== Admin debug endpoint retained ========
@app.get("/admin/debug/income_probe")
def income_probe():
    import copy
    needle = (request.args.get("q") or "MOBILE DEPOSIT").upper()
    cfg = load_cfg()
    monthly_raw = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"]) or {}

    def scan(tree):
        count = 0; sum_amt = 0.0; rows = []
        def walk(n):
            kids = n.get("children") or []
            if kids:
                for c in kids:
                    walk(c)
            else:
                for tx in (n.get("transactions") or []):
                    desc = (tx.get("description") or tx.get("desc") or "").upper()
                    try: amt = float(tx.get("amount", tx.get("amt", 0.0)) or 0.0)
                    except Exception: amt = 0.0
                    if needle in desc:
                        rows.append({"date": tx.get("date",""), "desc": tx.get("description",""), "amt": amt})
                        nonlocal count, sum_amt
                        count += 1; sum_amt += amt
        for top in (tree or []):
            walk(top)
        return count, round(sum_amt,2), rows[:25]

    pre = {}
    for mk, blob in monthly_raw.items():
        c, s, _ = scan(blob.get("tree") or [])
        pre[_norm_month(mk)] = {"count": c, "sum": s}

    monthly = copy.deepcopy(monthly_raw)
    _apply_hide_rules_to_summary(monthly)
    post = {}
    for mk, blob in monthly.items():
        c, s, _ = scan(blob.get("tree") or [])
        post[_norm_month(mk)] = {"count": c, "sum": s}

    return jsonify({"needle": needle, "pre": pre, "post": post})
