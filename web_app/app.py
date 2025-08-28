from __future__ import annotations

# Standard library
import json
import os
import subprocess
import sys
import sqlite3  # reserved for future use
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from time import time
from typing import Any, Dict, List, Optional

# Third-party
from dateutil.relativedelta import relativedelta
from werkzeug.routing import BuildError
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

# Local (unified data source for all pages)
from truist.parser_web import (
    generate_summary,
    _parse_any_date,
)
from truist.admin_categories import admin_categories_bp, load_cfg

# ---- Flask app ----
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev")

# --- Password gate (HTTP Basic Auth) ---
EXEMPT_PATHS = {
    "/login", "/logout", "/healthz",
    "/static/manifest.webmanifest",
    "/service-worker.js",
}
EXEMPT_PREFIXES = ("/static/",)

@app.get("/healthz")
def healthz():
    return "ok", 200

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
        "Authentication required", 401,
        {"WWW-Authenticate": 'Basic realm="ClarityLedger"'}
    )

# ---- Blueprints (admin UI + keyword APIs) ----
app.register_blueprint(admin_categories_bp)

# ------------------ FILE HELPERS ------------------
def _statements_dir() -> Path:
    dir_env = os.environ.get("STATEMENTS_DIR") or os.environ.get("TRUIST_DATA_DIR")
    if dir_env:
        p = Path(dir_env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = Path("/var/data/statements")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _manual_file() -> Path:
    return _statements_dir() / "manual_transactions.json"

def load_manual_transactions():
    out = []
    try:
        with open(_manual_file(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out

def save_manual_transaction(tx: dict):
    with open(_manual_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(tx) + "\n")

def _normalize_form_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return raw

def save_manual_form_transaction(form, tx_type: str):
    raw_amount = abs(float(form["amount"]))
    amount = raw_amount if tx_type == "income" else -raw_amount
    tx = {
        "date": _normalize_form_date(form["date"]),
        "description": form["description"].upper(),
        "amount": amount,
        "category": form.get("category", ""),
        "subcategory": form.get("subcategory", ""),
        "sub_subcategory": form.get("sub_subcategory", "")
    }
    save_manual_transaction(tx)

# ------------------ HIDE/PRUNE + MONTHLY BUILD ------------------
def _apply_hide_rules_to_summary(summary_data):
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
        return sum(_amt(t) for t in (txs or []))

    def _prune_and_total(node):
        if not isinstance(node, dict):
            return 0.0

        children = node.get("children") or []
        if not children:
            txs = list(node.get("transactions") or [])
            kept = [t for t in txs if not _should_hide(t)]
            node["transactions"] = kept
            total = _sum_signed_tx(kept)
            node["total"] = round(total, 2)
            return total

        total = 0.0
        for ch in children:
            total += _prune_and_total(ch)
        node["total"] = round(total, 2)
        return total

    def _prune_empty_nodes(node):
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

    for _, month_blob in summary_data.items():
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

        month_blob["income_total"]  = round(income_sum, 2)
        month_blob["expense_total"] = round(expense_sum, 2)
        month_blob["net_cash_flow"] = round(income_sum - expense_sum, 2)

def _rebuild_categories_from_tree(summary_data: dict) -> None:
    if not summary_data:
        return

    def _is_hidden_amount(a: float) -> bool:
        HIDE_AMOUNTS = [10002.02, -10002.02]
        EPS = 0.005
        try:
            v = float(a)
        except Exception:
            return False
        return any(abs(v - h) < EPS for h in HIDE_AMOUNTS)

    for _, month in summary_data.items():
        tree = month.get("tree") or []
        cats: dict[str, dict] = {}

        def add_leaf_tx(top: str, sub: str, tx: dict):
            desc = tx.get("description") or tx.get("desc") or ""
            try:
                amt = float(tx.get("amount", tx.get("amt", 0.0)) or 0.0)
            except Exception:
                amt = 0.0
            if _is_hidden_amount(amt):
                return

            row = {
                "date": tx.get("date", ""),
                "description": desc,
                "amount": amt,
                "category": top,
                "subcategory": sub,
            }

            if top not in cats:
                cats[top] = {"transactions": [], "total": 0.0, "subs": {}}
            cats[top]["transactions"].append(row)
            cats[top]["total"] += amt

            if sub:
                subs = cats[top]["subs"]
                if sub not in subs:
                    subs[sub] = {"transactions": [], "total": 0.0}
                subs[sub]["transactions"].append(row)
                subs[sub]["total"] += amt

        def walk(node: dict, parts: list[str]):
            name = (node.get("name") or "").strip()
            kids = node.get("children") or []
            here = parts + ([name] if name else [])
            if kids:
                for ch in kids:
                    walk(ch, here)
            else:
                top = here[0] if here else ""
                sub = here[1] if len(here) > 1 else ""
                for tx in (node.get("transactions") or []):
                    add_leaf_tx(top, sub, tx)

        for top_node in tree:
            walk(top_node, [])

        month["categories"] = cats

def build_monthly():
    cfg_live = load_cfg()
    monthly = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"]) or {}
    _apply_hide_rules_to_summary(monthly)
    _rebuild_categories_from_tree(monthly)
    return monthly, cfg_live

def _norm_month(k):
    if not k:
        return ""
    s = str(k)
    return s[:7] if len(s) >= 7 else s

# ------------------ TEMPLATE HELPERS ------------------
@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

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
        env["NONINTERACTIVE"] = "1"  # prevents input() in plaid_fetch
        proc = subprocess.run(
            [sys.executable, "-m", "truist.plaid_fetch"],
            capture_output=True, text=True, env=env, timeout=180
        )
        if proc.returncode != 0:
            return jsonify({"ok": False, "error": proc.stderr or proc.stdout}), 500
        return jsonify({"ok": True, "out": proc.stdout})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/builder")
def category_builder():
    cfg_live = load_cfg()
    return render_template("category_builder.html", cfg=cfg_live)

@app.route("/")
def index():
    cfg_live = load_cfg()
    summary_data = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"])
    _apply_hide_rules_to_summary(summary_data)

    transactions = load_manual_transactions()
    income_total = sum(t.get("amount", 0) for t in transactions if t.get("amount", 0) > 0)
    expense_total = sum(-t.get("amount", 0) for t in transactions if t.get("amount", 0) < 0)

    return render_template(
        "index.html",
        summary_data=summary_data,
        transactions=transactions,
        income=income_total,
        expense=expense_total
    )

@app.route("/categories")
def categories():
    cfg_live = load_cfg()
    cats = set()
    cats.update((cfg_live.get("SUBCATEGORY_MAPS") or {}).keys())
    cats.update((cfg_live.get("CATEGORY_KEYWORDS") or {}).keys())
    cats.update((cfg_live.get("SUBSUBCATEGORY_MAPS") or {}).keys())
    cats.update((cfg_live.get("SUBSUBSUBCATEGORY_MAPS") or {}).keys())
    tree = []
    for cat in sorted(cats):
        sub_map = cfg_live.get("SUBCATEGORY_MAPS", {}).get(cat, {}) or {}
        cat_node = {"name": cat, "total": None, "subs": []}
        for sub in sorted(sub_map.keys()):
            ssub_map = (cfg_live.get("SUBSUBCATEGORY_MAPS", {}).get(cat, {}) or {}).get(sub, {}) or {}
            sub_node = {"name": sub, "total": None, "subs": []}
            for ssub in sorted(ssub_map.keys()):
                sss_map = ((cfg_live.get("SUBSUBSUBCATEGORY_MAPS", {}).get(cat, {}) or {})
                           .get(sub, {}) or {}).get(ssub, {}) or {}
                ssub_node = {"name": ssub, "total": None, "subs": []}
                for sss in sorted(sss_map.keys()):
                    ssub_node["subs"].append({"name": sss, "total": None, "subs": []})
                sub_node["subs"].append(ssub_node)
        tree.append(cat_node)

    return render_template("category_breakdown.html", category_tree=tree, CFG=cfg_live)

@app.route("/cash", methods=["GET"])
def cash_page():
    cfg_live = load_cfg()
    summary_data = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"])
    _apply_hide_rules_to_summary(summary_data)
    return render_template(
        "cash.html",
        summary_data=summary_data,
        category_keywords=cfg_live["CATEGORY_KEYWORDS"],
        subcategory_maps=cfg_live["SUBCATEGORY_MAPS"],
        subsubcategory_maps=cfg_live.get("SUBSUBCATEGORY_MAPS", {})
    )

@app.route("/add-income", methods=["GET"])
def add_income():
    return redirect(url_for("cash_page"))

@app.route("/add-expense", methods=["GET"])
def add_expense():
    return redirect(url_for("cash_page"))

@app.route("/submit-income", methods=["POST"])
def submit_income():
    raw_amount = abs(float(request.form["amount"]))
    tx = {
        "date": request.form["date"],
        "description": request.form["description"].upper(),
        "amount": raw_amount
    }
    save_manual_transaction(tx)
    return redirect(url_for("index"))

@app.route("/submit-expense", methods=["POST"])
def submit_expense():
    save_manual_form_transaction(request.form, "expense")
    return redirect(url_for("index", r=int(time())))

# ------------------ RECENT ACTIVITY API ------------------
@app.route("/api/recent-activity", methods=["GET"], endpoint="api_recent_activity")
def api_recent_activity():
    try:
        days = int(request.args.get("days", "30"))
    except Exception:
        days = 30
    include_income = (request.args.get("include_income", "0").lower() in {"1", "true", "yes", "on"})

    monthly, cfg_live = build_monthly()
    months_sorted = sorted(monthly.keys(), key=_norm_month)
    now = datetime.now()

    payload = {
        "as_of": now.strftime("%Y-%m-%d"),
        "latest_month": None,
        "prev_month": None,
        "latest_totals": {
            "income": 0.0, "expense": 0.0, "net": 0.0,
            "delta_income": 0.0, "delta_expense": 0.0, "delta_net": 0.0,
            "pct_income": None, "pct_expense": None, "pct_net": None,
        },
        "movers_abs": [],
        "recent_txs": [],
        "recent_windows": {
            "last_7_income": 0.0, "last_7_expense": 0.0,
            "last_30_income": 0.0, "last_30_expense": 0.0,
        },
    }

    if not months_sorted:
        return jsonify({**payload, "ok": True, "data": payload})

    latest_key = months_sorted[-1]
    prev_key   = months_sorted[-2] if len(months_sorted) > 1 else None
    latest     = monthly.get(latest_key, {}) or {}
    prev       = monthly.get(prev_key, {}) or {}

    payload["latest_month"] = _norm_month(latest_key)
    payload["prev_month"]   = _norm_month(prev_key) if prev_key else None

    def _pct(new, old):
        if old is None:
            return None
        try:
            oldf = float(old)
        except Exception:
            return None
        if abs(oldf) < 1e-9:
            return 1.0 if abs(float(new or 0.0)) > 0 else 0.0
        return (float(new or 0.0) - oldf) / oldf

    inc  = float(latest.get("income_total")  or 0.0)
    exp  = float(latest.get("expense_total") or 0.0)
    net  = inc - exp

    pinc = float(prev.get("income_total")  or 0.0) if prev_key else None
    pexp = float(prev.get("expense_total") or 0.0) if prev_key else None
    pnet = (pinc - pexp) if prev_key is not None else None

    payload["latest_totals"] = {
        "income": inc, "expense": exp, "net": net,
        "delta_income": (inc - (pinc or 0.0)),
        "delta_expense": (exp - (pexp or 0.0)),
        "delta_net": (net - (pnet or 0.0)),
        "pct_income": _pct(inc, pinc),
        "pct_expense": _pct(exp, pexp),
        "pct_net": _pct(net, pnet),
    }

    def _month_outflows_for(month_key_val):
        if not month_key_val:
            return {}
        sums = {}
        cats = ((monthly.get(month_key_val, {}) or {}).get("categories", {}) or {})
        for cname, cdata in cats.items():
            total = 0.0
            for t in (cdata.get("transactions") or []):
                try:
                    amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
                except Exception:
                    amt = 0.0
                if amt < 0:
                    total += -amt
            sums[cname] = sums.get(cname, 0.0) + total
        return sums

    curr_s = _month_outflows_for(latest_key)
    prev_s = _month_outflows_for(prev_key) if prev_key else {}

    cats = set(curr_s) | set(prev_s)
    movers = []
    for c in cats:
        prev_v   = round(prev_s.get(c, 0.0), 2)
        latest_v = round(curr_s.get(c, 0.0), 2)
        delta    = round(latest_v - prev_v, 2)
        delta_pct = None if prev_v == 0 else (latest_v - prev_v) / prev_v
        movers.append({
            "category": c,
            "prev": prev_v,
            "latest": latest_v,
            "delta": delta,
            "delta_pct": delta_pct
        })

    movers = [r for r in movers if abs(r["delta"]) >= 0.01]
    movers.sort(key=lambda r: abs(r["delta"]), reverse=True)
    payload["movers_abs"] = movers[:10]

    def _date_key(s):
        dt = _parse_any_date(s or "")
        return (dt or datetime.fromtimestamp(0)).timestamp()

    def _is_hidden_amount(x):
        try:
            xv = float(x)
        except Exception:
            return False
        return abs(xv - 10002.02) < 0.005 or abs(xv + 10002.02) < 0.005

    flattened = []
    for mk in reversed(months_sorted):
        cats = (monthly.get(mk, {}) or {}).get("categories", {}) or {}
        for cname, cdata in cats.items():
            for t in (cdata.get("transactions") or []):
                try:
                    amt = float(t.get("amount", t.get("amt", 0.0)) or 0.0)
                except Exception:
                    amt = 0.0
                if _is_hidden_amount(amt):
                    continue
                if (not include_income) and (amt > 0):
                    continue
                flattened.append({
                    "date": t.get("date", ""),
                    "desc": t.get("description", "") or t.get("desc", "") or "",
                    "amount": amt,
                    "category": t.get("category", "") or cname,
                    "subcategory": t.get("subcategory", "") or ""
                })

    flattened.sort(key=lambda x: (_date_key(x["date"]), abs(x["amount"])), reverse=True)
    payload["recent_txs"] = flattened[:25]

    cut7  = now - timedelta(days=7)
    cut30 = now - timedelta(days=30)

    def in_win(d, cut):
        dt = _parse_any_date(d) if d else None
        return (dt is not None) and (dt >= cut)

    last7_exp  = sum(abs(t["amount"]) for t in flattened if t["amount"] < 0 and in_win(t["date"], cut7))
    last7_inc  = sum(t["amount"]       for t in flattened if t["amount"] > 0 and in_win(t["date"], cut7))
    last30_exp = sum(abs(t["amount"]) for t in flattened if t["amount"] < 0 and in_win(t["date"], cut30))
    last30_inc = sum(t["amount"]       for t in flattened if t["amount"] > 0 and in_win(t["date"], cut30))
    payload["recent_windows"] = {
        "last_7_expense": round(last7_exp, 2),
        "last_7_income":  round(last7_inc, 2),
        "last_30_expense": round(last30_exp, 2),
        "last_30_income":  round(last30_inc, 2),
    }

    return jsonify({**payload, "ok": True, "data": payload})

# ------------------ CATEGORY MOVERS WRAPPER ------------------
@app.route("/api/category_movers", methods=["GET"])
def api_category_movers():
    ra_resp = api_recent_activity()
    try:
        data = ra_resp.get_json() or {}
    except Exception:
        return jsonify(ok=True, latest_month=None, prev_month=None, rows=[])

    payload = data.get("data") or data
    rows = payload.get("movers_abs") or []
    return jsonify(
        ok=True,
        latest_month=payload.get("latest_month"),
        prev_month=payload.get("prev_month"),
        rows=rows
    )

# ------------------ CFG HELPERS ------------------
def _cfg_top_names(cfg_live: Dict[str, Any]) -> List[str]:
    names = set()
    names.update((cfg_live.get("SUBCATEGORY_MAPS") or {}).keys())
    names.update((cfg_live.get("CATEGORY_KEYWORDS") or {}).keys())
    names.update((cfg_live.get("SUBSUBCATEGORY_MAPS") or {}).keys())
    names.update((cfg_live.get("SUBSUBSUBCATEGORY_MAPS") or {}).keys())
    return sorted(n for n in names if n)

def _cfg_children_for(level: str, cat: str, sub: str, ssub: str, cfg_live: Dict[str, Any]) -> List[str]:
    smap  = cfg_live.get("SUBCATEGORY_MAPS") or {}
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

# ------------------ PATH TRANSACTIONS (Drawer) ------------------
@app.get("/api/path/transactions")
def api_path_transactions():
    level = (request.args.get("level") or "category").strip().lower()
    cat   = request.args.get("cat")  or ""
    sub   = request.args.get("sub")  or ""
    ssub  = request.args.get("ssub") or ""
    sss   = request.args.get("sss")  or ""

    month_req = (request.args.get("month") or "").strip()

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

    monthly, cfg_live = build_monthly()

    months_all_sorted = sorted(monthly.keys(), key=_norm_month)
    if not months_all_sorted:
        return jsonify({
            "ok": True, "path": [], "month": month_req, "months": [],
            "transactions": [], "children": _cfg_top_names(cfg_live), "total": 0.0,
            "magnitude_total": 0.0
        })

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

    focus_key = None
    if month_req:
        for k in months_sel:
            if _norm_month(k) == month_req[:7]:
                focus_key = k
                break
    if not focus_key:
        focus_key = months_sel[-1]
    focus_norm = _norm_month(focus_key)

    parts = []
    if cat:
        parts.append(cat)
    if level in {"subcategory", "subsubcategory", "subsubsubcategory"} and sub:
        parts.append(sub)
    if level in {"subsubcategory", "subsubsubcategory"} and ssub:
        parts.append(ssub)
    if level in {"subsubsubcategory"} and sss:
        parts.append(sss)

    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _hidden(a: float) -> bool:
        try:
            aa = float(a)
        except Exception:
            return False
        return any(abs(aa - h) < EPS for h in HIDE_AMOUNTS)

    txs = []
    children_from_tree = set()

    for mk in months_sel:
        blob = monthly.get(mk, {}) or {}
        tree = blob.get("tree") or []

        node = _find_node_by_path(tree, parts) if parts else None

        if node:
            for ch in (node.get("children") or []):
                nm = (ch.get("name") or "").strip()
                if nm:
                    children_from_tree.add(nm)

            def gather(n: Dict[str, Any]):
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
                        if _hidden(amt):
                            continue
                        txs.append({
                            "date": t.get("date", ""),
                            "description": t.get("description", t.get("desc", "")),
                            "amount": amt,
                            "category": t.get("category", ""),
                            "subcategory": t.get("subcategory", ""),
                        })
            gather(node)
        else:
            if not parts:
                for top in (tree or []):
                    nm = (top.get("name") or "").strip()
                    if nm:
                        children_from_tree.add(nm)

                def gather_all(n: Dict[str, Any]):
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
                            if _hidden(amt):
                                continue
                            txs.append({
                                "date": t.get("date", ""),
                                "description": t.get("description", t.get("desc", "")),
                                "amount": amt,
                                "category": t.get("category", ""),
                                "subcategory": t.get("subcategory", ""),
                            })

                for top in (tree or []):
                    gather_all(top)

    cfg_children = _cfg_children_for(level, cat, sub, ssub, cfg_live)
    children_set = set(cfg_children)
    children_set.update(children_from_tree)
    children = sorted(c for c in children_set if c)

    def _key_tx(t):
        dt = _parse_any_date(t.get("date", "")) or datetime(1970, 1, 1)
        return (dt, abs(float(t.get("amount", 0.0))))
    txs.sort(key=_key_tx, reverse=True)

    total = sum(float(t["amount"]) for t in txs)
    magnitude_total = sum(abs(float(t["amount"])) for t in txs)

    return jsonify({
        "ok": True,
        "path": parts,
        "month": focus_norm,
        "months": months_norm,
        "transactions": txs,
        "children": children,
        "total": total,
        "magnitude_total": magnitude_total
    })

# -------- Goals --------
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

def build_top_level_monthly_from_summary(
    summary: Dict[str, Any],
    months_back: int = 12,
    since: Optional[str] = None,
    since_date: Optional[str] = None,
) -> Dict[str, Any]:
    def parse_any_date(s: str) -> Optional[date]:
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except Exception:
                pass
        return None

    def tx_sum_on_or_after(node: Dict[str, Any], cutoff: date) -> float:
        children = node.get("children") or []
        if children:
            return sum(tx_sum_on_or_after(ch, cutoff) for ch in children)
        total = 0.0
        for tx in (node.get("transactions") or []):
            d = parse_any_date(tx.get("date"))
            if d and d >= cutoff:
                try:
                    a = float(tx.get("amount", tx.get("amt", 0.0)))
                except Exception:
                    a = 0.0
                total += abs(a)
        return total

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

    for i, raw_mkey in enumerate(months_sel):
        m_key_norm = _norm_month(raw_mkey)
        month_blob = summary.get(raw_mkey) or {}
        for top in (month_blob.get("tree") or []):
            top_name = (top.get("name") or "Uncategorized").strip() or "Uncategorized"
            if since_day and since_month_from_day == m_key_norm:
                val = tx_sum_on_or_after(top, since_day)
                if val == 0.0:
                    try:
                        val = abs(float(top.get("total") or 0.0))
                    except Exception:
                        val = 0.0
            else:
                try:
                    val = abs(float(top.get("total") or 0.0))
                except Exception:
                    val = 0.0
            bucket[top_name][i] += val

    categories = [{"name": k, "path": [k], "monthly": v} for k, v in bucket.items()]
    categories.sort(key=lambda c: sum(c["monthly"]), reverse=True)
    return {"months": months, "categories": categories}

@app.route("/goals")
def goals_page():
    cfg_live = load_cfg()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"])
    _apply_hide_rules_to_summary(summary)

    cat_monthly = build_top_level_monthly_from_summary(
        summary,
        months_back=12,
        since_date="2025-04-21"
    )
    return render_template("goals.html", cat_monthly=cat_monthly)

@app.get("/api/goals")
def api_goals_get():
    try:
        with open(_statements_dir() / "goals.json", "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"monthly_goals": {}, "updated_at": None})

@app.post("/api/goals")
def api_goals_set():
    data = request.get_json(silent=True) or {}
    out = {
        "monthly_goals": data.get("monthly_goals", {}),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    with open(_statements_dir() / "goals.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return jsonify({"ok": True, **out})

# -------- Simple pages --------
@app.route("/charts")
def charts_page():
    return render_template("charts.html")

@app.route("/explorer")
def all_items_explorer():
    cfg_live = load_cfg()
    summary = generate_summary(cfg_live["CATEGORY_KEYWORDS"], cfg_live["SUBCATEGORY_MAPS"])
    _apply_hide_rules_to_summary(summary)
    cat_monthly = build_top_level_monthly_from_summary(
        summary,
        months_back=int(request.args.get("months", "12") or 12),
        since=request.args.get("since"),
        since_date=request.args.get("since_date"),
    )
    return render_template("all_items_explorer.html", cat_monthly=cat_monthly)

# Entrypoint for gunicorn
# --- compatibility alias for old navbar link ---
@app.get("/transactions")
@app.get("/all-transactions")
@app.get("/all_transactions")
def all_transactions_page():
    # Keeps old endpoint name used in templates; points to Category Builder
    return redirect("/admin/categories", code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
