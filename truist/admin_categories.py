# truist/admin_categories.py
from __future__ import annotations

import copy
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Blueprint, jsonify, flash, redirect, render_template, request, url_for

# Live config + summary/tx access
import truist.filter_config as fc
from truist.parser_web import generate_summary, get_transactions_for_path

# Blueprint lives under /admin
admin_categories_bp = Blueprint("admin_categories", __name__, url_prefix="/admin")

# --------- PROJECT-ROOT paths (Option A) ---------
# ...\Truist\truist\admin_categories.py  -> parents[1] == ...\Truist
PROJECT_ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = PROJECT_ROOT / "categories.json"
BACKUP_DIR = PROJECT_ROOT / "categories_backups"
BACKUP_DIR.mkdir(exist_ok=True)

# --- Defaults when JSON does not exist yet ---
EMPTY_CFG = {
    "CATEGORY_KEYWORDS": {},
    "SUBCATEGORY_MAPS": {},
    "SUBSUBCATEGORY_MAPS": {},
    "SUBSUBSUBCATEGORY_MAPS": {},
    "CUSTOM_TRANSACTION_KEYWORDS": {},
    "OMIT_KEYWORDS": [],
}

# ---------- small helpers ----------
def _wants_json() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    xreq = (request.headers.get("X-Requested-With") or "").lower()
    return (
        "application/json" in accept
        or request.is_json
        or xreq == "fetch"
        or request.args.get("ajax") == "1"
    )

# ---------- config I/O (seed + merge overrides) ----------
def _config_dir() -> Path:
    p = Path(os.environ.get("CONFIG_DIR", "config"))
    p.mkdir(parents=True, exist_ok=True)
    return p

def _seed_if_missing(src: Path, dst: Path) -> None:
    if not dst.exists() and src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

def _merge_list_unique(a: List[str] | None, b: List[str] | None) -> List[str]:
    out: List[str] = list(a or [])
    for x in (b or []):
        if x not in out:
            out.append(x)
    return out

def _merge_nested_dict(dst: Dict, src: Dict) -> Dict:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_nested_dict(dst[k], v)
        else:
            dst[k] = v
    return dst

def _merge_keywords(defaults: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key, def_val in defaults.items():
        if key not in overrides:
            merged[key] = def_val
            continue
        over_val = overrides[key]
        if isinstance(def_val, dict) and isinstance(over_val, dict):
            tmp = dict(def_val)
            tmp.update(over_val)
            merged[key] = tmp
        elif isinstance(def_val, list) and isinstance(over_val, list):
            merged[key] = list(dict.fromkeys(def_val + over_val))
        else:
            merged[key] = over_val
    for key, over_val in overrides.items():
        if key not in merged:
            merged[key] = over_val
    return merged

def load_cfg() -> Dict[str, Any]:
    """
    Load live config from CONFIG_DIR, seed categories.json from the repo
    (root or truist/) on first boot, and merge keyword overrides on top of
    code defaults from truist.filter_config.
    """
    cfg_dir = _config_dir()

    # Find a seed file in the repo (either location)
    seed_candidates = [
        PROJECT_ROOT / "categories.json",            # repo root
        Path(__file__).with_name("categories.json"), # truist/categories.json
    ]
    seed = next((c for c in seed_candidates if c.exists()), None)

    live_categories = cfg_dir / "categories.json"
    if seed:
        _seed_if_missing(seed, live_categories)

    categories = _load_json(live_categories, fallback={})

    defaults = {
        "CATEGORY_KEYWORDS": getattr(fc, "CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS": getattr(fc, "SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBSUBCATEGORY_MAPS", {}),
        "OMIT_KEYWORDS": getattr(fc, "OMIT_KEYWORDS", []),
        "CUSTOM_TRANSACTION_KEYWORDS": getattr(fc, "CUSTOM_TRANSACTION_KEYWORDS", {}),
    }
    overrides_path = cfg_dir / "filter_overrides.json"
    overrides = _load_json(overrides_path, fallback={})
    merged = _merge_keywords(defaults, overrides)

    return {
        "CATEGORIES": categories,
        "CATEGORY_KEYWORDS": merged.get("CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS": merged.get("SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS": merged.get("SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": merged.get("SUBSUBSUBCATEGORY_MAPS", {}),
        "OMIT_KEYWORDS": merged.get("OMIT_KEYWORDS", []),
        "CUSTOM_TRANSACTION_KEYWORDS": merged.get("CUSTOM_TRANSACTION_KEYWORDS", {}),
        "_PATHS": {
            "CONFIG_DIR": str(cfg_dir),
            "CATEGORIES_PATH": str(live_categories),
            "KEYWORD_OVERRIDES_PATH": str(overrides_path),
        },
    }

def save_cfg(cfg: Dict[str, Any]) -> None:
    """
    Persist ONLY editable keyword maps to CONFIG_DIR/filter_overrides.json.
    Also keep a timestamped backup, and separately back up project-root categories.json.
    """
    paths = load_cfg().get("_PATHS", {})
    overrides_path = Path(paths.get("KEYWORD_OVERRIDES_PATH", "config/filter_overrides.json"))
    backups_dir = overrides_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "CATEGORY_KEYWORDS": cfg.get("CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS": cfg.get("SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS": cfg.get("SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": cfg.get("SUBSUBSUBCATEGORY_MAPS", {}),
        "CUSTOM_TRANSACTION_KEYWORDS": cfg.get("CUSTOM_TRANSACTION_KEYWORDS", {}),
        "OMIT_KEYWORDS": cfg.get("OMIT_KEYWORDS", []),
    }

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if overrides_path.exists():
        (backups_dir / f"filter_overrides.{ts}.json").write_text(
            overrides_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    overrides_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also back up project-root categories.json if present
    if JSON_PATH.exists():
        shutil.copy(JSON_PATH, BACKUP_DIR / f"categories.{ts}.json")

# -------------------------
# Helpers for edit/delete
# -------------------------
def has_children(cfg, level, cat, sub=None, ssub=None, sss=None) -> bool:
    if level == "category":
        return bool(cfg["SUBCATEGORY_MAPS"].get(cat, {}))
    if level == "subcategory":
        return bool(cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}))
    if level == "subsubcategory":
        return bool(cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}))
    if level == "subsubsubcategory":
        return False
    return False

def has_keywords_at(cfg, level, cat, sub=None, ssub=None, sss=None) -> bool:
    if level == "category":
        return bool(cfg["CATEGORY_KEYWORDS"].get(cat, []))
    if level == "subcategory":
        return bool(cfg["SUBCATEGORY_MAPS"].get(cat, {}).get(sub, []))
    if level == "subsubcategory":
        return bool(cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, []))
    if level == "subsubsubcategory":
        return bool(cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).get(sss, []))
    return False

def delete_path_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None) -> None:
    if level == "subsubsubcategory":
        node = cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {})
        if sss in node:
            del node[sss]
        if not cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}):
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].pop(ssub, None)
        if not cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}):
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if not cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}):
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return

    if level == "subsubcategory":
        node = cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {})
        if ssub in node:
            del node[ssub]
        if not cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}):
            cfg["SUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if not cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}):
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).pop(ssub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return

    if level == "subcategory":
        cfg["SUBCATEGORY_MAPS"].get(cat, {}).pop(sub, None)
        if cfg["SUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).pop(sub, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).pop(sub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return

    if level == "category":
        cfg["CATEGORY_KEYWORDS"].pop(cat, None)
        cfg["SUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return
    
def delete_path_cascade_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None):
    """
    Fully delete the given node and all of its descendants + keywords.
    """
    if level == "category":
        cfg["CATEGORY_KEYWORDS"].pop(cat, None)
        cfg["SUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return

    if level == "subcategory":
        if cat in cfg["SUBCATEGORY_MAPS"]:
            cfg["SUBCATEGORY_MAPS"][cat].pop(sub, None)
        if cat in cfg["SUBSUBCATEGORY_MAPS"]:
            cfg["SUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if cat in cfg["SUBSUBSUBCATEGORY_MAPS"]:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        return

    if level == "subsubcategory":
        if cat in cfg["SUBSUBCATEGORY_MAPS"] and sub in cfg["SUBSUBCATEGORY_MAPS"][cat]:
            cfg["SUBSUBCATEGORY_MAPS"][cat][sub].pop(ssub, None)
        if cat in cfg["SUBSUBSUBCATEGORY_MAPS"] and sub in cfg["SUBSUBSUBCATEGORY_MAPS"][cat]:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].pop(ssub, None)
        return

    if level == "subsubsubcategory":
        if cat in cfg["SUBSUBSUBCATEGORY_MAPS"] and sub in cfg["SUBSUBSUBCATEGORY_MAPS"][cat]:
            if ssub in cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub]:
                cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub].pop(sss, None)
        return


def rename_path_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None, new_label="") -> None:
    if level == "category":
        new_cat = new_label
        if new_cat == cat or not new_cat:
            return
        if cat in cfg["CATEGORY_KEYWORDS"]:
            cfg["CATEGORY_KEYWORDS"][new_cat] = cfg["CATEGORY_KEYWORDS"].pop(cat)
        else:
            cfg["CATEGORY_KEYWORDS"].setdefault(new_cat, [])
        if cat in cfg["SUBCATEGORY_MAPS"]:
            cfg["SUBCATEGORY_MAPS"][new_cat] = cfg["SUBCATEGORY_MAPS"].pop(cat)
        if cat in cfg["SUBSUBCATEGORY_MAPS"]:
            cfg["SUBSUBCATEGORY_MAPS"][new_cat] = cfg["SUBSUBCATEGORY_MAPS"].pop(cat)
        if cat in cfg["SUBSUBSUBCATEGORY_MAPS"]:
            cfg["SUBSUBSUBCATEGORY_MAPS"][new_cat] = cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat)
        return

    if level == "subcategory":
        new_sub = new_label
        if not new_sub or new_sub == sub:
            return
        subs = cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
        subs[new_sub] = subs.pop(sub, [])
        ssub_map = cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        ssub_map[new_sub] = ssub_map.pop(sub, {})
        sss_map = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        sss_map[new_sub] = sss_map.pop(sub, {})
        return

    if level == "subsubcategory":
        new_ssub = new_label
        if not new_ssub or new_ssub == ssub:
            return
        sub_map = cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {})
        sub_map[new_ssub] = sub_map.pop(ssub, [])
        sss_map = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {})
        sss_map[new_ssub] = sss_map.pop(ssub, {})
        return

    if level == "subsubsubcategory":
        new_sss = new_label
        if not new_sss or new_sss == sss:
            return
        sss_dict = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, {})
        sss_dict[new_sss] = sss_dict.pop(sss, [])
        return

# ===== Core MOVE helper (merge-safe) =====
def _move_node_in_cfg(cfg, level, src, dst_parent):
    level = (level or "").strip()
    s = {k: (src.get(k) or "").strip() for k in ("cat", "sub", "ssub", "sss")}
    d = {k: (dst_parent.get(k) or "").strip() for k in ("cat", "sub", "ssub")}

    if level not in {"subcategory", "subsubcategory", "subsubsubcategory"}:
        raise ValueError("Invalid level for move")

    if level == "subcategory":
        cat_from = s["cat"]; sub_name = s["sub"]; cat_to = d["cat"]
        if not cat_from or not sub_name or not cat_to:
            raise ValueError("Missing cat/sub for move")

        cfg["SUBCATEGORY_MAPS"].setdefault(cat_to, {})
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat_to, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat_to, {})

        src_kw = (cfg["SUBCATEGORY_MAPS"].get(cat_from, {}) or {}).pop(sub_name, [])
        src_ssub = (cfg["SUBSUBCATEGORY_MAPS"].get(cat_from, {}) or {}).pop(sub_name, {})
        src_sss = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from, {}) or {}).pop(sub_name, {})

        if cfg["SUBCATEGORY_MAPS"].get(cat_from) == {}:
            cfg["SUBCATEGORY_MAPS"].pop(cat_from, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat_from) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat_from, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat_from, None)

        dst_kw = cfg["SUBCATEGORY_MAPS"][cat_to].get(sub_name, [])
        cfg["SUBCATEGORY_MAPS"][cat_to][sub_name] = _merge_list_unique(dst_kw, src_kw)

        dst_ssub = cfg["SUBSUBCATEGORY_MAPS"][cat_to].get(sub_name, {})
        cfg["SUBSUBCATEGORY_MAPS"][cat_to][sub_name] = _merge_nested_dict(dst_ssub or {}, src_ssub or {})

        dst_sss = cfg["SUBSUBSUBCATEGORY_MAPS"][cat_to].get(sub_name, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat_to][sub_name] = _merge_nested_dict(dst_sss or {}, src_sss or {})
        return

    if level == "subsubcategory":
        cat = s["cat"]; sub_from = s["sub"]; ssub = s["ssub"]
        cat_to = d["cat"]; sub_to = d["sub"]
        if not cat or not sub_from or not ssub or not cat_to or not sub_to:
            raise ValueError("Missing cat/sub/ssub for move")

        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat_to, {}).setdefault(sub_to, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat_to, {}).setdefault(sub_to, {})

        src_kw = (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}) or {}).pop(ssub, [])
        src_sss = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}) or {}).pop(ssub, {})

        if cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from) == {}:
            cfg["SUBSUBCATEGORY_MAPS"][cat].pop(sub_from, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub_from, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)

        dst_kw = cfg["SUBSUBCATEGORY_MAPS"][cat_to][sub_to].get(ssub, [])
        cfg["SUBSUBCATEGORY_MAPS"][cat_to][sub_to][ssub] = _merge_list_unique(dst_kw, src_kw)

        dst_sss = cfg["SUBSUBSUBCATEGORY_MAPS"][cat_to][sub_to].get(ssub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat_to][sub_to][ssub] = _merge_nested_dict(dst_sss or {}, src_sss or {})
        return

    # subsubsubcategory
    cat = s["cat"]; sub = s["sub"]; ssub_from = s["ssub"]; sss = s["sss"]
    cat_to = d["cat"]; sub_to = d["sub"]; ssub_to = d["ssub"]
    if not cat or not sub or not ssub_from or not sss or not cat_to or not sub_to or not ssub_to:
        raise ValueError("Missing cat/sub/ssub/sss for move")

    cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat_to, {}).setdefault(sub_to, {}).setdefault(ssub_to, {})

    payload = (cfg["SUBSUBSUBCATEGORY_MAPS"]
               .get(cat, {}).get(sub, {}).get(ssub_from, {}) or {}).pop(sss, [])

    if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub_from) == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].pop(ssub_from, None)
    if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub) == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
    if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)

    dst_kw = cfg["SUBSUBSUBCATEGORY_MAPS"][cat_to][sub_to][ssub_to].get(sss, [])
    cfg["SUBSUBSUBCATEGORY_MAPS"][cat_to][sub_to][ssub_to][sss] = _merge_list_unique(dst_kw, payload)

# -------------------------
# Pages / routing
# -------------------------
@admin_categories_bp.route("/categories", methods=["GET"], endpoint="categories_page")
def categories_page():
    cfg = load_cfg()
    summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    return render_template("manage_categories.html", cfg=cfg, summary_data=summary_data)

# Keep an alias endpoint name many templates might reference
@admin_categories_bp.route("/categories", methods=["GET"], endpoint="index")
def categories_index():
    return categories_page()

# -------------------------
# Drawer data endpoint (PLAIN ROWS) — keep this path for drawer.js
# -------------------------
@admin_categories_bp.get("/categories/inspect")
def categories_inspect_rows():
    """
    Return plain transaction rows for the requested path (drawer-friendly).
    Query: level, cat, sub, ssub, sss, limit, allow_hidden
    """
    level = request.args.get("level", "category")
    cat   = request.args.get("cat", "")
    sub   = request.args.get("sub", "")
    ssub  = request.args.get("ssub", "")
    sss   = request.args.get("sss", "")
    limit = int(request.args.get("limit", 5000))
    allow_hidden = str(request.args.get("allow_hidden", "0")).lower() in {"1", "true", "yes", "on"}

    rows = get_transactions_for_path(
        level=level, cat=cat, sub=sub, ssub=ssub, sss=sss, limit=limit, allow_hidden=allow_hidden
    )
    return jsonify(rows)

# -------------------------
# Manage panel inspector (STRUCTURED) — renamed to avoid collision
# -------------------------
def _safe_date_key(s: str) -> int:
    if not s:
        return 0
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except Exception:
            pass
    try:
        return int(datetime.strptime(str(s)[:10], "%Y-%m-%d").timestamp())
    except Exception:
        return 0

def _extract_desc(tx):
    return (tx.get("description") or tx.get("desc") or tx.get("merchant") or "").strip()

def _collect_descendant_transactions(node):
    out = []
    if not node:
        return out
    children = node.get("children") or []
    if children:
        for ch in children:
            out.extend(_collect_descendant_transactions(ch))
    else:
        out.extend(node.get("transactions") or [])
    return out

def _find_nodes_by_path(tree, cat=None, sub=None, ssub=None, sss=None):
    matches = []
    def walk(node, ancestors):
        parts = ancestors + [node.get("name")]
        depth = len(parts)
        ok = True
        if depth >= 1 and cat  and parts[0] != cat:  ok = False
        if depth >= 2 and sub  and parts[1] != sub:  ok = False
        if depth >= 3 and ssub and parts[2] != ssub: ok = False
        if depth == 4 and sss  and parts[3] != sss:  ok = False
        if ok:
            if ((sss and depth == 4) or
                (ssub and not sss and depth == 3) or
                (sub and not ssub and depth == 2) or
                (cat and not sub and depth == 1)):
                matches.append(node)
        for ch in (node.get("children") or []):
            walk(ch, parts)
    for top in tree:
        walk(top, [])
    return matches

def _keywords_and_children(cfg, level, cat, sub=None, ssub=None, sss=None):
    if level == "category":
        return cfg["CATEGORY_KEYWORDS"].get(cat, [])[:], sorted(list((cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {}).keys()))
    if level == "subcategory":
        return (cfg["SUBCATEGORY_MAPS"].get(cat, {}).get(sub, []) or [])[:], sorted(list((cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}) or {}).keys()))
    if level == "subsubcategory":
        return (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, []) or [])[:], sorted(list((cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}) or {}).keys()))
    if level == "subsubsubcategory":
        return (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).get(sss, []) or [])[:], []
    return [], []

@admin_categories_bp.route("/categories/inspect_detail", methods=["GET"])
def inspect_path_detail():
    """
    Returns details for Manage panel:
      { ok: true, data: { keywords:[], children:[], transactions:[{date, desc, amount}] } }
    Query params: level, cat, sub, ssub, sss, limit
    """
    cfg = load_cfg()

    level = (request.args.get("level") or "").strip()
    cat   = (request.args.get("cat")   or "").strip()
    sub   = (request.args.get("sub")   or "").strip()
    ssub  = (request.args.get("ssub")  or "").strip()
    sss   = (request.args.get("sss")   or "").strip()

    try:
        limit = int(request.args.get("limit", "100000"))
    except Exception:
        limit = 100000

    if level not in {"category", "subcategory", "subsubcategory", "subsubsubcategory"}:
        return jsonify({"ok": False, "error": "Invalid level"}), 400
    if not cat:
        return jsonify({"ok": False, "error": "Category is required"}), 400

    kw, children = _keywords_and_children(cfg, level, cat, sub or None, ssub or None, sss or None)

    try:
        summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    except Exception:
        summary_data = {}

    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _is_hidden_amount(x: float) -> bool:
        try:
            xv = float(x)
        except Exception:
            return False
        return any(abs(xv - h) < EPS for h in HIDE_AMOUNTS)

    collected = []
    for month_key in sorted(summary_data.keys(), reverse=True):
        month = summary_data[month_key] or {}
        tree = month.get("tree") or []
        if not tree:
            continue
        if level == "subsubsubcategory" and sss:
            targets = _find_nodes_by_path(tree, cat=cat, sub=sub, ssub=ssub, sss=sss)
        elif level == "subsubcategory":
            targets = _find_nodes_by_path(tree, cat=cat, sub=sub, ssub=ssub)
        elif level == "subcategory":
            targets = _find_nodes_by_path(tree, cat=cat, sub=sub)
        else:
            targets = _find_nodes_by_path(tree, cat=cat)
        for node in targets:
            collected.extend(_collect_descendant_transactions(node))

    norm = []
    for tx in collected:
        date_str = (tx.get("date") or "").strip()
        desc_str = _extract_desc(tx)
        amt = tx.get("amount")
        try:
            amt = float(amt)
        except Exception:
            try:
                amt = float(tx.get("amt"))
            except Exception:
                amt = 0.0
        if _is_hidden_amount(amt):
            continue
        norm.append({"date": date_str, "desc": desc_str, "amount": amt, "_k": _safe_date_key(date_str)})

    norm.sort(key=lambda x: x["_k"], reverse=True)
    for n in norm:
        n.pop("_k", None)
    if limit and limit > 0:
        norm = norm[:limit]

    return jsonify({"ok": True, "data": {"keywords": kw, "children": children, "transactions": norm}})

# ----- JSON editor actions -----
@admin_categories_bp.route("/categories/validate", methods=["POST"])
def validate_json():
    text = request.form.get("json_text", "")
    try:
        data = json.loads(text)
        for key in EMPTY_CFG.keys():
            if key not in data:
                data[key] = EMPTY_CFG[key]
        return jsonify({"ok": True, "message": "Valid JSON."})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400

@admin_categories_bp.route("/categories/save", methods=["POST"])
def save_json():
    text = request.form.get("json_text", "")
    try:
        data = json.loads(text)

        cfg_live = load_cfg()
        paths = cfg_live.get("_PATHS", {})
        categories_path = Path(paths.get("CATEGORIES_PATH", "config/categories.json"))
        overrides_path  = Path(paths.get("KEYWORD_OVERRIDES_PATH", "config/filter_overrides.json"))

        if isinstance(data, dict) and "CATEGORIES" in data:
            categories_payload = data.get("CATEGORIES") or {}
            categories_path.write_text(json.dumps(categories_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        overrides_payload = {
            "CATEGORY_KEYWORDS": data.get("CATEGORY_KEYWORDS", cfg_live.get("CATEGORY_KEYWORDS", {})),
            "SUBCATEGORY_MAPS": data.get("SUBCATEGORY_MAPS", cfg_live.get("SUBCATEGORY_MAPS", {})),
            "SUBSUBCATEGORY_MAPS": data.get("SUBSUBCATEGORY_MAPS", cfg_live.get("SUBSUBCATEGORY_MAPS", {})),
            "SUBSUBSUBCATEGORY_MAPS": data.get("SUBSUBSUBCATEGORY_MAPS", cfg_live.get("SUBSUBSUBCATEGORY_MAPS", {})),
            "CUSTOM_TRANSACTION_KEYWORDS": data.get("CUSTOM_TRANSACTION_KEYWORDS", cfg_live.get("CUSTOM_TRANSACTION_KEYWORDS", {})),
            "OMIT_KEYWORDS": data.get("OMIT_KEYWORDS", cfg_live.get("OMIT_KEYWORDS", [])),
        }

        if overrides_path.exists():
            backups_dir = overrides_path.parent / "backups"
            backups_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            (backups_dir / f"filter_overrides.{ts}.json").write_text(
                overrides_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        overrides_path.write_text(json.dumps(overrides_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        flash("Configuration saved.", "success")
        return redirect(url_for("admin_categories.categories_page"))
    except Exception as e:
        flash(f"Save failed: {e}", "danger")
        return redirect(url_for("admin_categories.categories_page"))

# =========================================================
# Single endpoint to upsert path AND optionally keyword
# =========================================================
def _add_keyword_cascade_up(cfg, level, cat, sub=None, ssub=None, sss=None, keyword="") -> bool:
    KW = (keyword or "").strip().upper()
    if not KW or not cat:
        return False

    cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
    if sub:
        cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, [])
    if sub and ssub:
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, [])
    if sub and ssub and sss:
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, {}).setdefault(sss, [])

    added = False
    def add(arr):
        nonlocal added
        if KW not in arr:
            arr.append(KW)
            added = True

    if level == "category":
        add(cfg["CATEGORY_KEYWORDS"][cat])
    elif level == "subcategory":
        add(cfg["SUBCATEGORY_MAPS"][cat][sub]); add(cfg["CATEGORY_KEYWORDS"][cat])
    elif level == "subsubcategory":
        add(cfg["SUBSUBCATEGORY_MAPS"][cat][sub][ssub]); add(cfg["SUBCATEGORY_MAPS"][cat][sub]); add(cfg["CATEGORY_KEYWORDS"][cat])
    elif level == "subsubsubcategory":
        add(cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub][sss]); add(cfg["SUBSUBCATEGORY_MAPS"][cat][sub][ssub]); add(cfg["SUBCATEGORY_MAPS"][cat][sub]); add(cfg["CATEGORY_KEYWORDS"][cat])
    return added

@admin_categories_bp.route("/categories/upsert", methods=["POST"])
def upsert_path_and_keyword():
    cfg = load_cfg()

    cat  = (request.form.get("cat")  or (request.json.get("cat")  if request.is_json else "") or "").strip()
    sub  = (request.form.get("sub")  or (request.json.get("sub")  if request.is_json else "") or "").strip()
    ssub = (request.form.get("ssub") or (request.json.get("ssub") if request.is_json else "") or "").strip()
    sss  = (request.form.get("sss")  or (request.json.get("sss")  if request.is_json else "") or "").strip()

    target_level = (request.form.get("target_level") or (request.json.get("target_level") if request.is_json else "")).strip()
    target_label = (request.form.get("target_label") or (request.json.get("target_label") if request.is_json else "")).strip()
    keyword      = ((request.form.get("keyword") or (request.json.get("keyword") if request.is_json else "")).strip().upper())

    if not cat:
        msg = "Category is required (pick existing or enter a new one)."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))

    cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
    cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})

    if sub:
        cfg["SUBCATEGORY_MAPS"][cat].setdefault(sub, [])
        cfg["SUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})

    if sub and ssub:
        cfg["SUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, [])
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, {})

    if sub and ssub and sss:
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub].setdefault(sss, [])

    added_keyword = False
    if keyword and target_level and target_label:
        try:
            if target_level == "subcategory" and not sub:
                sub = target_label
            elif target_level == "subsubcategory":
                if not sub:
                    msg = "Subcategory is required when targeting a sub-subcategory."
                    if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
                    flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))
                if not ssub:
                    ssub = target_label
            elif target_level == "subsubsubcategory":
                missing = []
                if not sub:  missing.append("Subcategory")
                if not ssub: missing.append("Sub-subcategory")
                if missing:
                    msg = f"{', '.join(missing)} required when targeting a sub-sub-subcategory."
                    if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
                    flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))
                if not sss:
                    sss = target_label

            cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
            if sub: cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, [])
            if sub and ssub: cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, [])
            if sub and ssub and sss: cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, {}).setdefault(sss, [])

            added_keyword = _add_keyword_cascade_up(cfg, target_level, cat, sub or None, ssub or None, sss or None, keyword)
        except KeyError:
            msg = "Invalid target path for keyword; please ensure parents exist."
            if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
            flash(msg, "danger"); return redirect(url_for("admin_categories.categories_page"))

    save_cfg(cfg)

    if _wants_json():
        return jsonify({"ok": True, "added_keyword": added_keyword})

    if added_keyword:
        flash(f'Saved path and added keyword "{keyword}" to {target_level} "{target_label}" (and parents).', "success")
    else:
        flash("Saved/updated path.", "success")
    return redirect(url_for("admin_categories.categories_page"))

# ----------------------------
# (Legacy) Separate add routes
# ----------------------------
@admin_categories_bp.route("/categories/add_label", methods=["POST"])
def add_label():
    cfg = load_cfg()
    level = request.form.get("level", "").strip()
    label = request.form.get("label", "").strip()

    if not level or not label:
        flash("Level and label are required.", "warning")
        return redirect(url_for("admin_categories.categories_page"))

    if level == "category":
        cfg["CATEGORY_KEYWORDS"].setdefault(label, [])
        cfg["SUBCATEGORY_MAPS"].setdefault(label, {})
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(label, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(label, {})

    elif level == "subcategory":
        cat = request.form.get("parent_category", "").strip()
        if not cat:
            flash("Parent category is required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBCATEGORY_MAPS"][cat].setdefault(label, [])
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBCATEGORY_MAPS"][cat].setdefault(label, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].setdefault(label, {})

    elif level == "subsubcategory":
        cat = request.form.get("parent_category", "").strip()
        sub = request.form.get("parent_subcategory", "").strip()
        if not cat or not sub:
            flash("Parent category and subcategory are required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBCATEGORY_MAPS"][cat][sub].setdefault(label, [])
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(label, {})

    elif level == "subsubsubcategory":
        cat = request.form.get("parent_category", "").strip()
        sub = request.form.get("parent_subcategory", "").strip()
        ssub = request.form.get("parent_subsubcategory", "").strip()
        if not cat or not sub or not ssub:
            flash("Parent category, subcategory, and sub-subcategory are required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub].setdefault(label, [])

    else:
        flash("Invalid level.", "danger")
        return redirect(url_for("admin_categories.categories_page"))

    save_cfg(cfg)
    flash(f"Added {level}: {label}", "success")
    return redirect(url_for("admin_categories.categories_page"))

@admin_categories_bp.route("/categories/add_keyword", methods=["POST"])
def add_keyword():
    cfg = load_cfg()
    scope = request.form.get("scope", "").strip()
    keyword = (request.form.get("keyword", "") or "").strip().upper()

    if not scope or not keyword:
        flash("Scope and keyword are required.", "warning")
        return redirect(url_for("admin_categories.categories_page"))

    if scope == "category":
        cat = request.form.get("category", "").strip()
        if not cat:
            flash("Category is required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
        if keyword not in cfg["CATEGORY_KEYWORDS"][cat]:
            cfg["CATEGORY_KEYWORDS"][cat].append(keyword)

    elif scope == "subcategory":
        cat = request.form.get("category", "").strip()
        sub = request.form.get("target_label", "").strip()
        if not cat or not sub:
            flash("Category and Subcategory are required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBCATEGORY_MAPS"][cat].setdefault(sub, [])
        if keyword not in cfg["SUBCATEGORY_MAPS"][cat][sub]:
            cfg["SUBCATEGORY_MAPS"][cat][sub].append(keyword)

    elif scope == "subsubcategory":
        cat = request.form.get("category", "").strip()
        sub = request.form.get("subcategory", "").strip()
        ssub = request.form.get("target_label", "").strip()
        if not cat or not sub or not ssub:
            flash("Category, Subcategory, and Sub-subcategory are required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, [])
        if keyword not in cfg["SUBSUBCATEGORY_MAPS"][cat][sub][ssub]:
            cfg["SUBSUBCATEGORY_MAPS"][cat][sub][ssub].append(keyword)

    elif scope == "subsubsubcategory":
        cat = request.form.get("category", "").strip()
        sub = request.form.get("subcategory", "").strip()
        ssub = request.form.get("subsubcategory", "").strip()
        sss = request.form.get("target_label", "").strip()
        if not cat or not sub or not ssub or not sss:
            flash("Category, Subcategory, Sub-subcategory, and Sub-sub-subcategory are required.", "warning")
            return redirect(url_for("admin_categories.categories_page"))
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub].setdefault(sss, [])
        if keyword not in cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub][sss]:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub][sss].append(keyword)

    else:
        flash("Invalid scope.", "danger")
        return redirect(url_for("admin_categories.categories_page"))

    save_cfg(cfg)
    flash(f"Added keyword: {keyword}", "success")
    return redirect(url_for("admin_categories.categories_page"))

# ==========================================
# Rename + Delete
# ==========================================
@admin_categories_bp.route("/categories/rename", methods=["POST"])
def rename_path():
    cfg = load_cfg()
    lvl = (request.form.get("level") or (request.json.get("level") if request.is_json else "")).strip()
    cat = (request.form.get("cat") or (request.json.get("cat") if request.is_json else "")).strip()
    sub = (request.form.get("sub") or (request.json.get("sub") if request.is_json else "")).strip()
    ssub = (request.form.get("ssub") or (request.json.get("ssub") if request.is_json else "")).strip()
    sss = (request.form.get("sss") or (request.json.get("sss") if request.is_json else "")).strip()
    new_label = (request.form.get("new_label") or (request.json.get("new_label") if request.is_json else "")).strip()

    if not lvl or not new_label:
        msg = "Level and new name are required."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))

    try:
        rename_path_in_cfg(cfg, lvl, cat, sub or None, ssub or None, sss or None, new_label=new_label)
        save_cfg(cfg)
        if _wants_json(): return jsonify({"ok": True, "new_label": new_label})
        flash(f"Renamed {lvl} to “{new_label}”.", "success")
    except Exception as e:
        if _wants_json(): return jsonify({"ok": False, "error": str(e)}), 400
        flash(f"Rename failed: {e}", "danger")
    return redirect(url_for("admin_categories.categories_page"))

@admin_categories_bp.route("/categories/delete", methods=["POST"])
def delete_path():
    cfg = load_cfg()
    level = (request.form.get("level") or (request.json.get("level") if request.is_json else "")).strip()
    cat   = (request.form.get("cat")   or (request.json.get("cat")   if request.is_json else "")).strip()
    sub   = (request.form.get("sub")   or (request.json.get("sub")   if request.is_json else "")).strip()
    ssub  = (request.form.get("ssub")  or (request.json.get("ssub")  if request.is_json else "")).strip()
    sss   = (request.form.get("sss")   or (request.json.get("sss")   if request.is_json else "")).strip()
    cascade = (request.form.get("cascade") or (request.json.get("cascade") if request.is_json else "")).strip().lower() in {"1","true","yes","on"}

    if not level:
        msg = "Level is required."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))

    if cascade:
        try:
            delete_path_cascade_in_cfg(cfg, level, cat, sub or None, ssub or None, sss or None)
            save_cfg(cfg)
            if _wants_json(): return jsonify({"ok": True, "cascade": True})
            flash(f"Deleted {level} and all descendants.", "success")
        except Exception as e:
            if _wants_json(): return jsonify({"ok": False, "error": str(e)}), 400
            flash(f"Cascade delete failed: {e}", "danger")
        return redirect(url_for("admin_categories.categories_page"))

    if has_children(cfg, level, cat, sub or None, ssub or None, sss or None):
        msg = "Cannot delete: this item has children. Enable 'cascade' to remove descendants."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))

    if has_keywords_at(cfg, level, cat, sub or None, ssub or None, sss or None):
        msg = "Cannot delete: this item has keywords attached. Enable 'cascade' to remove them."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))

    try:
        delete_path_in_cfg(cfg, level, cat, sub or None, ssub or None, sss or None)
        save_cfg(cfg)
        if _wants_json(): return jsonify({"ok": True, "cascade": False})
        flash(f"Deleted {level}.", "success")
    except Exception as e:
        if _wants_json(): return jsonify({"ok": False, "error": str(e)}), 400
        flash(f"Delete failed: {e}", "danger")

    return redirect(url_for("admin_categories.categories_page"))

# =========================================================
# Keyword add/remove (REST for the drawer)
# =========================================================
def _remove_keyword_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None, keyword="") -> bool:
    kw = (keyword or "").strip().upper()
    if not kw:
        return False
    found = False
    if level == "category":
        arr = cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
        if kw in arr: arr.remove(kw); found = True
    elif level == "subcategory":
        arr = cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, [])
        if kw in arr: arr.remove(kw); found = True
    elif level == "subsubcategory":
        arr = cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, [])
        if kw in arr: arr.remove(kw); found = True
    elif level == "subsubsubcategory":
        arr = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, {}).setdefault(sss, [])
        if kw in arr: arr.remove(kw); found = True
    return found

def _get_json_or_form(key: str) -> str:
    if request.is_json:
        val = (request.json.get(key) or "").strip()
    else:
        val = (request.form.get(key) or "").strip()
    return val

# ---- READ KEYWORDS for the drawer (GET/POST; admin-scoped aliases) ----
def _keywords_read_handler():
    cfg = load_cfg()

    # accept both GET (args) and POST (json/form)
    g = request.args
    j = request.get_json(silent=True) or {}
    f = request.form or {}

    def pick(k, default=""):
        v = (g.get(k) or j.get(k) or f.get(k) or default)
        return v.strip() if isinstance(v, str) else v

    level = pick("level", "category")
    cat   = pick("cat")
    sub   = pick("sub")
    ssub  = pick("ssub")
    sss   = pick("sss")

    if level not in {"category","subcategory","subsubcategory","subsubsubcategory"} or not cat:
        return jsonify({"ok": False, "error": "Invalid request"}), 400

    kws, _children = _keywords_and_children(cfg, level, cat, sub or None, ssub or None, sss or None)
    return jsonify({"ok": True, "keywords": kws})

# canonical (admin) path
@admin_categories_bp.route("/categories/keywords", methods=["GET","POST"])
def get_keywords_for_path():
    return _keywords_read_handler()

# admin-scoped aliases your JS may probe
@admin_categories_bp.route("/api/keywords", methods=["GET","POST"])
def get_keywords_alias_api():
    return _keywords_read_handler()

@admin_categories_bp.route("/categories/keywords_for_name", methods=["GET","POST"])
def get_keywords_for_name_compat():
    return _keywords_read_handler()

@admin_categories_bp.route("/api/keywords_for_name", methods=["GET","POST"])
def get_keywords_for_name_api_compat():
    return _keywords_read_handler()



@admin_categories_bp.route("/categories/keyword/add", methods=["POST"])
def keyword_add_api():
    cfg = load_cfg()
    level = _get_json_or_form("level")
    cat   = _get_json_or_form("cat")
    sub   = _get_json_or_form("sub")
    ssub  = _get_json_or_form("ssub")
    sss   = _get_json_or_form("sss")
    kw    = _get_json_or_form("keyword").upper()

    if level not in {"category", "subcategory", "subsubcategory", "subsubsubcategory"} or not cat or not kw:
        msg = "Invalid request: level, cat, and keyword are required."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "danger"); return redirect(url_for("admin_categories.categories_page"))

    cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
    cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})

    added = _add_keyword_cascade_up(cfg, level, cat, sub or None, ssub or None, sss or None, kw)
    save_cfg(cfg)

    if _wants_json():
        return jsonify({"ok": True, "added": added})
    flash(("Added keyword." if added else "Keyword already present."), "success")
    return redirect(url_for("admin_categories.categories_page"))

@admin_categories_bp.route("/categories/keyword/remove", methods=["POST"])
def keyword_remove_api():
    cfg = load_cfg()
    level = _get_json_or_form("level")
    cat   = _get_json_or_form("cat")
    sub   = _get_json_or_form("sub")
    ssub  = _get_json_or_form("ssub")
    sss   = _get_json_or_form("sss")
    kw    = _get_json_or_form("keyword").upper()

    if level not in {"category", "subcategory", "subsubcategory", "subsubsubcategory"} or not cat or not kw:
        msg = "Invalid request: level, cat, and keyword are required."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "danger"); return redirect(url_for("admin_categories.categories_page"))

    removed = _remove_keyword_in_cfg(cfg, level, cat, sub or None, ssub or None, sss or None, kw)
    save_cfg(cfg)

    if _wants_json():
        return jsonify({"ok": True, "removed": removed})
    flash(("Removed keyword." if removed else "Keyword not found."), "success")
    return redirect(url_for("admin_categories.categories_page"))

# ---------- Drawer-friendly keyword endpoints (aliases + unified) ----------
# NOTE: unique endpoint=... names avoid Flask collisions

@admin_categories_bp.get("/categories/keywords_for_name", endpoint="keywords_for_name")
@admin_categories_bp.get("/api/keywords", endpoint="keywords_read_api")
def keywords_read():
    cfg = load_cfg()

    # accept either explicit level or infer from provided parts
    level = (request.args.get("level") or "").strip()
    cat   = (request.args.get("cat")   or "").strip()
    sub   = (request.args.get("sub")   or "").strip()
    ssub  = (request.args.get("ssub")  or "").strip()
    sss   = (request.args.get("sss")   or "").strip()

    if not level:
        level = "category"
        if sss:      level = "subsubsubcategory"
        elif ssub:   level = "subsubcategory"
        elif sub:    level = "subcategory"

    if not cat:
        return jsonify({"ok": False, "error": "Category (cat) is required"}), 400

    # ✅ get keywords using your existing helper
    keywords, _children = _keywords_and_children(
        cfg,
        level,
        cat,
        sub or None,
        ssub or None,
        sss or None
    )

    return jsonify({
        "ok": True,
        "keywords": keywords,
        "data": keywords,
        "items": keywords
    })



# ================================
# Misc / Uncategorized transactions
# ================================
@admin_categories_bp.route("/categories/misc", methods=["GET"])
def list_misc_transactions():
    cfg = load_cfg()

    raw_labels = (request.args.get("labels") or "Miscellaneous|Uncategorized|Unknown").strip()
    labels = [s.strip().lower() for s in (raw_labels.replace(",", "|").split("|")) if s.strip()]
    try:
        limit = int(request.args.get("limit", "300"))
    except Exception:
        limit = 300
    q = (request.args.get("q") or "").strip().lower()
    try:
        min_abs = float(request.args.get("min_abs", "0") or "0")
    except Exception:
        min_abs = 0.0

    try:
        summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    except Exception:
        summary_data = {}

    def _walk(node, ancestors):
        yield node, ancestors
        for ch in (node.get("children") or []):
            yield from _walk(ch, ancestors + [node.get("name")])

    def _collect_leaf_txs_with_paths(node, ancestors):
        out = []
        children = node.get("children") or []
        if children:
            for ch in children:
                out.extend(_collect_leaf_txs_with_paths(ch, ancestors + [node.get("name")]))
        else:
            txs = node.get("transactions") or []
            full_path = [*ancestors, node.get("name")]
            for tx in txs:
                out.append((tx, full_path))
        return out

    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _is_hidden_amount(x: float) -> bool:
        try:
            xv = float(x)
        except Exception:
            return False
        return any(abs(xv - h) < EPS for h in HIDE_AMOUNTS)

    rows = []
    for month_key in sorted(summary_data.keys(), reverse=True):
        month = summary_data[month_key] or {}
        tree = month.get("tree") or []
        for top in tree:
            for node, _anc in _walk(top, []):
                name = (node.get("name") or "").strip()
                if name.lower() in labels:
                    for tx, path_list in _collect_leaf_txs_with_paths(node, []):
                        date_str = (tx.get("date") or "").strip()
                        desc_str = _extract_desc(tx)
                        amt = tx.get("amount")
                        try:
                            amt = float(amt)
                        except Exception:
                            try:
                                amt = float(tx.get("amt"))
                            except Exception:
                                amt = 0.0
                        if _is_hidden_amount(amt):
                            continue
                        if min_abs and abs(amt) < min_abs:
                            continue
                        if q and q not in desc_str.lower():
                            continue

                        rows.append({
                            "date": date_str,
                            "desc": desc_str,
                            "amount": amt,
                            "path": " > ".join(path_list or [name]),
                            "_k": _safe_date_key(date_str)
                        })

    rows.sort(key=lambda r: r["_k"], reverse=True)
    for r in rows:
        r.pop("_k", None)
    if limit and limit > 0:
        rows = rows[:limit]

    return jsonify({
        "ok": True,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "count": len(rows),
        "transactions": rows
    })

# =========================================================
# Cascade PREVIEW endpoint (counts before deleting)
# =========================================================
def _count_descendants_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None):
    counts = {"categories": 0, "subcategories": 0, "subsubcategories": 0, "subsubsubcategories": 0, "keywords": 0}

    def _kw_count_category(c): return len(cfg["CATEGORY_KEYWORDS"].get(c, []) or [])
    def _kw_count_sub(c, s): return len(cfg["SUBCATEGORY_MAPS"].get(c, {}).get(s, []) or [])
    def _kw_count_ssub(c, s, ss): return len(cfg["SUBSUBCATEGORY_MAPS"].get(c, {}).get(s, {}).get(ss, []) or [])
    def _kw_count_sss(c, s, ss, sss_): return len(cfg["SUBSUBSUBCATEGORY_MAPS"].get(c, {}).get(s, {}).get(ss, {}).get(sss_, []) or [])

    if level == "category":
        counts["categories"] += 1
        counts["keywords"] += _kw_count_category(cat)
        subs = list((cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {}).keys())
        counts["subcategories"] += len(subs)
        for s in subs:
            counts["keywords"] += _kw_count_sub(cat, s)
            ssubs = list((cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(s, {}) or {}).keys())
            counts["subsubcategories"] += len(ssubs)
            for ss in ssubs:
                counts["keywords"] += _kw_count_ssub(cat, s, ss)
                ssss = list((cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(s, {}).get(ss, {}) or {}).keys())
                counts["subsubsubcategories"] += len(ssss)
                for ssss_label in ssss:
                    counts["keywords"] += _kw_count_sss(cat, s, ss, ssss_label)
        return counts

    if level == "subcategory":
        counts["subcategories"] += 1
        counts["keywords"] += _kw_count_sub(cat, sub)
        ssubs = list((cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}) or {}).keys())
        counts["subsubcategories"] += len(ssubs)
        for ss in ssubs:
            counts["keywords"] += _kw_count_ssub(cat, sub, ss)
            ssss = list((cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ss, {}) or {}).keys())
            counts["subsubsubcategories"] += len(ssss)
            for ssss_label in ssss:
                counts["keywords"] += _kw_count_sss(cat, sub, ss, ssss_label)
        return counts

    if level == "subsubcategory":
        counts["subsubcategories"] += 1
        counts["keywords"] += _kw_count_ssub(cat, sub, ssub)
        ssss = list((cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}) or {}).keys())
        counts["subsubsubcategories"] += len(ssss)
        for ssss_label in ssss:
            counts["keywords"] += _kw_count_sss(cat, sub, ssub, ssss_label)
        return counts

    if level == "subsubsubcategory":
        counts["subsubsubcategories"] += 1
        counts["keywords"] += _kw_count_sss(cat, sub, ssub, sss)
        return counts

    return counts

@admin_categories_bp.route("/categories/cascade_preview", methods=["GET"])
def cascade_preview():
    cfg = load_cfg()

    level = (request.args.get("level") or "").strip()
    cat   = (request.args.get("cat")   or "").strip()
    sub   = (request.args.get("sub")   or "").strip()
    ssub  = (request.args.get("ssub")  or "").strip()
    sss   = (request.args.get("sss")   or "").strip()

    if level not in {"category","subcategory","subsubcategory","subsubsubcategory"} or not cat:
        return jsonify({"ok": False, "error": "Invalid request"}), 400

    node_counts = _count_descendants_in_cfg(cfg, level, cat, sub or None, ssub or None, sss or None)
    total_nodes = sum([node_counts["categories"], node_counts["subcategories"], node_counts["subsubcategories"], node_counts["subsubsubcategories"]])

    try:
        summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    except Exception:
        summary_data = {}

    def collect_tx_count():
        total = 0
        for month_key in sorted(summary_data.keys(), reverse=True):
            month = summary_data[month_key] or {}
            tree = month.get("tree") or []
            if not tree:
                continue
            if level == "subsubsubcategory" and sss:
                targets = _find_nodes_by_path(tree, cat=cat, sub=sub, ssub=ssub, sss=sss)
            elif level == "subsubcategory":
                targets = _find_nodes_by_path(tree, cat=cat, sub=sub, ssub=ssub)
            elif level == "subcategory":
                targets = _find_nodes_by_path(tree, cat=cat, sub=sub)
            else:
                targets = _find_nodes_by_path(tree, cat=cat)
            for node in targets:
                total += len(_collect_descendant_transactions(node))
        return total

    tx_count = collect_tx_count()

    return jsonify({
        "ok": True,
        "data": {
            "nodes": {**node_counts, "total_nodes": total_nodes},
            "keywords": node_counts["keywords"],
            "transactions": tx_count
        }
    })

# =========================================================
# Bulk manage + Drag-and-drop tree (read + move)
# =========================================================
@admin_categories_bp.route("/api/manage_category", methods=["POST"])
def manage_category_bulk():
    payload = request.get_json(force=True) or {}
    level = (payload.get("level") or "").strip()
    ctx   = payload.get("ctx") or {}
    edits = payload.get("edits") or []
    deletes = payload.get("deletes") or []
    cascade = bool(payload.get("cascade", False))

    if level not in {"category","subcategory","subsubcategory","subsubsubcategory"}:
        return jsonify({"ok": False, "error": "Invalid level"}), 400

    cat  = (ctx.get("cat") or "").strip()
    sub  = (ctx.get("sub") or "").strip()
    ssub = (ctx.get("ssub") or "").strip()

    cfg = load_cfg()

    try:
        if level == "category":
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "category", cat=old, new_label=new)

        elif level == "subcategory":
            if not cat: return jsonify({"ok": False, "error": "Missing category context"}), 400
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "subcategory", cat=cat, sub=old, new_label=new)

        elif level == "subsubcategory":
            if not (cat and sub): return jsonify({"ok": False, "error": "Missing category/subcategory context"}), 400
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "subsubcategory", cat=cat, sub=sub, ssub=old, new_label=new)

        else:  # subsubsubcategory
            if not (cat and sub and ssub): return jsonify({"ok": False, "error": "Missing category/sub/ssub context"}), 400
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "subsubsubcategory", cat=cat, sub=sub, ssub=ssub, sss=old, new_label=new)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Rename failed: {e}"}), 400

    try:
        for name in deletes:
            name = (name or "").strip()
            if not name:
                continue
            dcat, dsub, dssub, dsss = cat, sub, ssub, None
            if level == "category":
                dcat, dsub, dssub, dsss = name, None, None, None
            elif level == "subcategory":
                dsub = name
            elif level == "subsubcategory":
                dssub = name
            else:
                dsss = name

            if cascade:
                delete_path_cascade_in_cfg(cfg, level, dcat, dsub or None, dssub or None, dsss or None)
            else:
                if has_children(cfg, level, dcat, dsub or None, dssub or None, dsss or None):
                    return jsonify({"ok": False, "error": f"Cannot delete '{name}': it has children. Enable cascade."}), 400
                if has_keywords_at(cfg, level, dcat, dsub or None, dssub or None, dsss or None):
                    return jsonify({"ok": False, "error": f"Cannot delete '{name}': it has keywords. Enable cascade."}), 400
                delete_path_in_cfg(cfg, level, dcat, dsub or None, dssub or None, dsss or None)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Delete failed: {e}"}), 400

    save_cfg(cfg)
    return jsonify({"ok": True, "cfg": cfg, "cascade": cascade})

def _cfg_to_tree(cfg):
    out = []
    for cat in sorted(set(list(cfg["CATEGORY_KEYWORDS"].keys()) + list(cfg["SUBCATEGORY_MAPS"].keys()))):
        node = {"name": cat, "level": "category", "children": []}
        submap = cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {}
        for sub in sorted(submap.keys()):
            sn = {"name": sub, "level": "subcategory", "children": []}
            ssubmap = (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}) or {}).get(sub, {}) or {}
            for ssub in sorted(ssubmap.keys()):
                ssn = {"name": ssub, "level": "subsubcategory", "children": []}
                sssmap = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}) or {}).get(sub, {}).get(ssub, {}) or {}
                for sss in sorted(sssmap.keys()):
                    ssn["children"].append({"name": sss, "level": "subsubsubcategory", "children": []})
                sn["children"].append(ssn)
            node["children"].append(sn)
        out.append(node)
    return out

@admin_categories_bp.get("/api/tree")
def api_tree_read():
    cfg = load_cfg()
    return jsonify({"ok": True, "tree": _cfg_to_tree(cfg)})

@admin_categories_bp.post("/api/tree/move")
def api_tree_move():
    data = request.get_json(force=True) or {}
    src = data.get("src") or {}
    dest = data.get("dest") or {}
    new_label = (data.get("new_label") or "").strip()

    cfg = load_cfg()
    try:
        _move_node_in_cfg(cfg, (src.get("level") or ""), src, dest)
        # optional inline rename during move
        if new_label and src.get("level") in {"subcategory","subsubcategory","subsubsubcategory"}:
            lvl = src.get("level")
            if lvl == "subcategory":
                rename_path_in_cfg(cfg, lvl, dest.get("cat"), new_label=new_label, sub=src.get("sub"))
            elif lvl == "subsubcategory":
                rename_path_in_cfg(cfg, lvl, dest.get("cat"), dest.get("sub"), src.get("ssub"), new_label=new_label)
            else:
                rename_path_in_cfg(cfg, lvl, dest.get("cat"), dest.get("sub"), dest.get("ssub"), src.get("sss"), new_label=new_label)
        save_cfg(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True})
