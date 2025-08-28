from pathlib import Path
from time import time
from datetime import date, datetime, timedelta
from werkzeug.routing import BuildError
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from typing import Dict, Any, Optional, List
import json
import sqlite3  # reserved for future use

from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
import subprocess, sys, os

# ---- Flask app ----
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev")
# enables flash()

# --- Password gate (HTTP Basic Auth) ---
# Set APP_PASSWORD in your environment. Optionally set APP_USER to pin a username.
@app.get(\"/healthz\")
def healthz():
    return \"ok\", 200

EXEMPT_PATHS = {
    \"/login\", \"/logout\", \"/healthz\",
    \"/static/manifest.webmanifest\",
    \"/service-worker.js\",
}
EXEMPT_PREFIXES = (\"/static/\",)

@app.before_request
def password_gate():
    required = os.environ.get(\"APP_PASSWORD\")
    if not required:
        return  # gate disabled when no password configured

    p = request.path
    if request.method == \"HEAD\" or p in EXEMPT_PATHS or p.startswith(EXEMPT_PREFIXES):
        return

    auth = request.authorization
    expected_user = os.environ.get(\"APP_USER\")  # optional
    if auth and ((expected_user is None or auth.username == expected_user) and auth.password == required):
        return

    return Response(
        \"Authentication required\", 401,
        {\"WWW-Authenticate\": 'Basic realm=\"ClarityLedger\"'}
    )

# ---- Debug endpoints (temporary) ----
from truist.debug_config import debug_bp
app.register_blueprint(debug_bp)

# Optional: log where CONFIG_DIR points on boot
app.logger.info(\"[Config] Using CONFIG_DIR=%s\", os.environ.get(\"CONFIG_DIR\"))

# ---- Blueprints (admin UI + keyword APIs) ----
from truist.admin_categories import admin_categories_bp, load_cfg
app.register_blueprint(admin_categories_bp)

# ---- Live parser/config imports ----
from truist.parser_web import (
    generate_summary,
    _parse_any_date,
)
from truist import filter_config as fc

# ------------------ CATEGORY TREE + CFG ------------------
cfg = {
    \"CATEGORY_KEYWORDS\": getattr(fc, \"CATEGORY_KEYWORDS\", {}),
    \"SUBCATEGORY_MAPS\": getattr(fc, \"SUBCATEGORY_MAPS\", {}),
    \"SUBSUBCATEGORY_MAPS\": getattr(fc, \"SUBSUBCATEGORY_MAPS\", {}),
    \"SUBSUBSUBCATEGORY_MAPS\": getattr(fc, \"SUBSUBSUBCATEGORY_MAPS\", {}),
    \"KEYWORDS\": getattr(fc, \"KEYWORDS\", {}),
}


def build_category_tree(cfg_in=None):
    cfg_local = cfg_in or load_cfg()
    cats = set()
    cats.update(cfg_local[\"SUBCATEGORY_MAPS\"].keys())
    cats.update(cfg_local[\"CATEGORY_KEYWORDS\"].keys())
    cats.update(cfg_local.get(\"SUBSUBCATEGORY_MAPS\", {}).keys())
    cats.update(cfg_local.get(\"SUBSUBSUBCATEGORY_MAPS\", {}).keys())
    tree = []
    for cat in sorted(cats):
        sub_map = cfg_local[\"SUBCATEGORY_MAPS\"].get(cat, {}) or {}
        cat_node = {\"name\": cat, \"total\": None, \"subs\": []}
        for sub in sorted(sub_map.keys()):
            ssub_map = (cfg_local.get(\"SUBSUBCATEGORY_MAPS\", {}).get(cat, {}) or {}).get(sub, {}) or {}
            sub_node = {\"name\": sub, \"total\": None, \"subs\": []}
            for ssub in sorted(ssub_map.keys()):
                sss_map = ((cfg_local.get(\"SUBSUBSUBCATEGORY_MAPS\", {}).get(cat, {}) or {})
                           .get(sub, {}) or {}).get(ssub, {}) or {}
                ssub_node = {\"name\": ssub, \"total\": None, \"subs\": []}
                for sss in sorted(sss_map.keys()):
                    ssub_node[\"subs\"].append({\"name\": sss, \"total\": None, \"subs\": []})
                sub_node[\"subs\"].append(ssub_node)
            cat_node[\"subs\"].append(sub_node)
        tree.append(cat_node)
    return tree

# ------------------ FILE HELPERS ------------------
def _statements_dir() -> Path:
    # Use the same env var the parser reads (preferred), then accept legacy TRUIST_DATA_DIR.
    dir_env = os.environ.get(\"STATEMENTS_DIR\") or os.environ.get(\"TRUIST_DATA_DIR\")
    if dir_env:
        p = Path(dir_env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    # Final fallback: persistent disk path
    p = Path(\"/var/data/statements\")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _manual_file() -> Path:
    return _statements_dir() / \"manual_transactions.json\"

def load_manual_transactions():
    try:
        with open(_manual_file(), \"r\", encoding=\"utf-8\") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []

# Log chosen statements directory after helpers are defined
app.logger.info(\"[Statements] Using dir: %s\", str(_statements_dir()))


def save_manual_transaction(tx: dict):
    with open(_manual_file(), \"a\", encoding=\"utf-8\") as f:
        f.write(json.dumps(tx) + \"\n\")

def _normalize_form_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, \"%Y-%m-%d\").strftime(\"%m/%d/%Y\")
    except ValueError:
        return raw

def save_manual_form_transaction(form, tx_type: str):
    raw_amount = abs(float(form[\"amount\"]))
    amount = raw_amount if tx_type == \"income\" else -raw_amount
    tx = {
        \"date\": _normalize_form_date(form[\"date\"]),
        \"description\": form[\"description\"].upper(),
        \"amount\": amount,
        \"category\": form.get(\"category\", \"\"),
        \"subcategory\": form.get(\"subcategory\", \"\"),
        \"sub_subcategory\": form.get(\"sub_subcategory\", \"\")
    }
    save_manual_transaction(tx)

# ... (SNIP: full file continues exactly as in your paste, unchanged) ...
