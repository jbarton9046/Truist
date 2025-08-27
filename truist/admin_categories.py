# truist/admin_categories.py
import json
import shutil
import copy
from datetime import datetime
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

# Import your live config + summary generator
import truist.filter_config as fc
from truist.parser_web import generate_summary

# Blueprint lives under /admin
admin_categories_bp = Blueprint("admin_categories", __name__, url_prefix="/admin")

# --------- PROJECT-ROOT paths (Option A) ---------
# ...\Truist\truist\admin_categories.py  -> parents[1] == ...\Truist
PROJECT_ROOT = Path(__file__).resolve().parents[1]
JSON_PATH    = PROJECT_ROOT / "categories.json"
BACKUP_DIR   = PROJECT_ROOT / "categories_backups"
BACKUP_DIR.mkdir(exist_ok=True)

# --- Defaults when JSON does not exist yet ---
EMPTY_CFG = {
    "CATEGORY_KEYWORDS": {},
    "SUBCATEGORY_MAPS": {},
    "SUBSUBCATEGORY_MAPS": {},
    "SUBSUBSUBCATEGORY_MAPS": {},
    "CUSTOM_TRANSACTION_KEYWORDS": {},
    "OMIT_KEYWORDS": []
}

# ---------- small helpers ----------
def _wants_json() -> bool:
    """Detect if caller expects JSON (fetch/AJAX) instead of HTML redirect."""
    accept = (request.headers.get("Accept") or "").lower()
    xreq   = (request.headers.get("X-Requested-With") or "").lower()
    return (
        "application/json" in accept
        or request.is_json
        or xreq == "fetch"
        or request.args.get("ajax") == "1"
    )

from pathlib import Path
import os, json
from typing import Dict, Any

# Import your code defaults
from truist import filter_config as fc  # keeps your existing Python defaults

def _config_dir() -> Path:
    """Return CONFIG_DIR (env) or ./config, ensure it exists."""
    p = Path(os.environ.get("CONFIG_DIR", "config"))
    p.mkdir(parents=True, exist_ok=True)
    return p

def _seed_if_missing(src: Path, dst: Path) -> None:
    """Copy seed file from repo to disk on first run."""
    if not dst.exists() and src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

def _merge_keywords(defaults: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge dicts for editable parts (override wins)."""
    merged = dict(defaults)
    for k, v in overrides.items():
        merged[k] = v
    return merged


# replace your existing load_cfg() with this version
def load_cfg() -> Dict[str, Any]:
    """
    Loads the live config from CONFIG_DIR, seeding from repo defaults on first run.
    Supports both layouts:
      - <repo_root>/categories.json
      - <repo_root>/truist/categories.json
    """
    cfg_dir = _config_dir()

    # Find a seed file in the repo (either location)
    project_root = Path(__file__).resolve().parents[1]
    seed_candidates = [
        project_root / "categories.json",             # repo root (your logs showed this)
        Path(__file__).with_name("categories.json"),  # truist/categories.json
    ]
    pkg_categories = None
    for cand in seed_candidates:
        if cand and cand.exists():
            pkg_categories = cand
            break

    live_categories = cfg_dir / "categories.json"    # /var/data/config/categories.json
    if pkg_categories:
        _seed_if_missing(pkg_categories, live_categories)

    # Load live categories (or {} if not present)
    categories = _load_json(live_categories, fallback={})

    # Keyword defaults from code + optional JSON overrides
    defaults = {
        "CATEGORY_KEYWORDS":      getattr(fc, "CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS":       getattr(fc, "SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS":    getattr(fc, "SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBSUBCATEGORY_MAPS", {}),
        "OMIT_KEYWORDS":          getattr(fc, "OMIT_KEYWORDS", []),
        # "REFUND_KEYWORDS": getattr(fc, "REFUND_KEYWORDS", []),  # add if you expose in UI
    }
    overrides_path = cfg_dir / "filter_overrides.json"
    overrides = _load_json(overrides_path, fallback={})
    merged = _merge_keywords(defaults, overrides)

    return {
        "CATEGORIES": categories,
        "CATEGORY_KEYWORDS":      merged.get("CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS":       merged.get("SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS":    merged.get("SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": merged.get("SUBSUBSUBCATEGORY_MAPS", {}),
        "OMIT_KEYWORDS":          merged.get("OMIT_KEYWORDS", []),
        "_PATHS": {
            "CONFIG_DIR": str(cfg_dir),
            "CATEGORIES_PATH": str(live_categories),
            "KEYWORD_OVERRIDES_PATH": str(overrides_path),
        },
    }





    # Bootstrap from Python config (first run / no JSON yet)
    cfg = EMPTY_CFG.copy()
    cfg["CATEGORY_KEYWORDS"]           = copy.deepcopy(getattr(fc, "CATEGORY_KEYWORDS", {}))
    cfg["SUBCATEGORY_MAPS"]            = copy.deepcopy(getattr(fc, "SUBCATEGORY_MAPS", {}))
    cfg["SUBSUBCATEGORY_MAPS"]         = copy.deepcopy(getattr(fc, "SUBSUBCATEGORY_MAPS", {}))
    cfg["SUBSUBSUBCATEGORY_MAPS"]      = copy.deepcopy(getattr(fc, "SUBSUBSUBCATEGORY_MAPS", {}))
    cfg["CUSTOM_TRANSACTION_KEYWORDS"] = copy.deepcopy(getattr(fc, "CUSTOM_TRANSACTION_KEYWORDS", {}))
    cfg["OMIT_KEYWORDS"]               = copy.deepcopy(getattr(fc, "OMIT_KEYWORDS", []))
    return cfg


def save_cfg(cfg):
    """
    Persist ONLY editable keyword maps to CONFIG_DIR/filter_overrides.json.
    Creates a timestamped backup in CONFIG_DIR/backups/ before writing.
    """
    paths = load_cfg().get("_PATHS", {})
    overrides_path = Path(paths.get("KEYWORD_OVERRIDES_PATH", "config/filter_overrides.json"))
    backups_dir = overrides_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Prepare overrides payload (only the editable parts)
    payload = {
        "CATEGORY_KEYWORDS": cfg.get("CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS": cfg.get("SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS": cfg.get("SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": cfg.get("SUBSUBSUBCATEGORY_MAPS", {}),
        "CUSTOM_TRANSACTION_KEYWORDS": cfg.get("CUSTOM_TRANSACTION_KEYWORDS", {}),
        "OMIT_KEYWORDS": cfg.get("OMIT_KEYWORDS", []),
    }

    # Backup if existing
    if overrides_path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        (backups_dir / f"filter_overrides.{ts}.json").write_text(
            overrides_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    overrides_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    shutil.copy(JSON_PATH, BACKUP_DIR / f"categories.{ts}.json")
    # save pretty
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False, sort_keys=True)

# -------------------------
# Helpers for edit/delete
# -------------------------
def has_children(cfg, level, cat, sub=None, ssub=None, sss=None):
    """Check if a node has immediate children."""
    if level == "category":
        return bool(cfg["SUBCATEGORY_MAPS"].get(cat, {}))
    if level == "subcategory":
        return bool(cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}))
    if level == "subsubcategory":
        return bool(cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}))
    if level == "subsubsubcategory":
        return False
    return False

def has_keywords_at(cfg, level, cat, sub=None, ssub=None, sss=None):
    """Check if a node has keywords attached directly at that level."""
    if level == "category":
        return bool(cfg["CATEGORY_KEYWORDS"].get(cat, []))
    if level == "subcategory":
        return bool(cfg["SUBCATEGORY_MAPS"].get(cat, {}).get(sub, []))
    if level == "subsubcategory":
        return bool(cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, []))
    if level == "subsubsubcategory":
        return bool(cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).get(sss, []))
    return False

def delete_path_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None):
    """Delete a node (assumes safety checked). Cleans up empty parents."""
    if level == "subsubsubcategory":
        node = cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {})
        if sss in node:
            del node[sss]
        # cleanup empties
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
        # mirror cleanup
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

def rename_path_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None, new_label=""):
    """Rename label at any depth, moving children + keywords."""
    if level == "category":
        new_cat = new_label
        if new_cat == cat or not new_cat:
            return
        # Move top-level keys
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

# ===== Merge helpers for safe moves =====
def _merge_list_unique(a, b):
    out = list(a or [])
    for x in (b or []):
        if x not in out:
            out.append(x)
    return out

def _merge_dict_of_lists(dst, src):
    # { key: [list] } merge
    for k, v in (src or {}).items():
        dst[k] = _merge_list_unique(dst.get(k, []), v)
    return dst

def _merge_nested_dict(dst, src):
    # deep merge dicts-of-dicts (used for SUBSUBCATEGORY_MAPS/SUBSUBSUBCATEGORY_MAPS)
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_nested_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


# ===== Core MOVE helper =====
def _move_node_in_cfg(cfg, level, src, dst_parent):
    """
    Reparent a node to a new parent. Merges if the target already has a child with the same label.
    level: "subcategory" | "subsubcategory" | "subsubsubcategory"
    src:        { "cat":..., "sub":..., "ssub":..., "sss":... }
    dst_parent: { "cat":..., "sub":..., "ssub":... }
    """
    level = (level or "").strip()
    s = {k: (src.get(k) or "").strip() for k in ("cat", "sub", "ssub", "sss")}
    d = {k: (dst_parent.get(k) or "").strip() for k in ("cat", "sub", "ssub")}

    if level not in {"subcategory", "subsubcategory", "subsubsubcategory"}:
        raise ValueError("Invalid level for move")

    if level == "subcategory":
        cat_from = s["cat"]; sub_name = s["sub"]; cat_to = d["cat"]
        if not cat_from or not sub_name or not cat_to:
            raise ValueError("Missing cat/sub for move")

        # ensure target parents exist
        cfg["SUBCATEGORY_MAPS"].setdefault(cat_to, {})
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat_to, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat_to, {})

        # grab moving payloads
        src_kw   = (cfg["SUBCATEGORY_MAPS"].get(cat_from, {}) or {}).pop(sub_name, [])
        src_ssub = (cfg["SUBSUBCATEGORY_MAPS"].get(cat_from, {}) or {}).pop(sub_name, {})
        src_sss  = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from, {}) or {}).pop(sub_name, {})

        # cleanup empties on source
        if cfg["SUBCATEGORY_MAPS"].get(cat_from) == {}:
            cfg["SUBCATEGORY_MAPS"].pop(cat_from, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat_from) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat_from, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat_from, None)

        # merge into destination (if same sub exists)
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

        # ensure parents on target
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat_to, {}).setdefault(sub_to, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat_to, {}).setdefault(sub_to, {})

        # pull moving payloads
        src_kw  = (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}) or {}).pop(ssub, [])
        src_sss = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}) or {}).pop(ssub, {})

        # cleanup empties on source
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from) == {}:
            cfg["SUBSUBCATEGORY_MAPS"][cat].pop(sub_from, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub_from, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)

        # merge into destination (if same ssub exists)
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
# CASCADE DELETE + PREVIEW HELPERS
# -------------------------
def delete_path_cascade_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None):
    """
    Recursively delete a node and all descendants, including keyword arrays,
    then clean up any empty parent dicts.
    """
    if level == "category":
        cfg["CATEGORY_KEYWORDS"].pop(cat, None)
        cfg["SUBCATEGORY_MAPS"].pop(cat, None)
        cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
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

    if level == "subsubcategory":
        cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).pop(ssub, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub) == {}:
            cfg["SUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)

        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).pop(ssub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return

    if level == "subsubsubcategory":
        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).pop(sss, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].pop(ssub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)
        return

def _count_descendants_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None):
    """
    Count descendant nodes & keywords under the given node (including the node's own keywords).
    Returns dict with counts per level and total keywords.
    """
    counts = {
        "categories": 0,
        "subcategories": 0,
        "subsubcategories": 0,
        "subsubsubcategories": 0,
        "keywords": 0
    }

    # Helpers to add keyword counts
    def _kw_count_category(c):
        return len(cfg["CATEGORY_KEYWORDS"].get(c, []) or [])

    def _kw_count_sub(c, s):
        return len(cfg["SUBCATEGORY_MAPS"].get(c, {}).get(s, []) or [])

    def _kw_count_ssub(c, s, ss):
        return len(cfg["SUBSUBCATEGORY_MAPS"].get(c, {}).get(s, {}).get(ss, []) or [])

    def _kw_count_sss(c, s, ss, sss_):
        return len(cfg["SUBSUBSUBCATEGORY_MAPS"].get(c, {}).get(s, {}).get(ss, {}).get(sss_, []) or [])

    # Traverse depending on starting level
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

# =========================================================
# Keyword add (cascade) helper
# =========================================================
def _add_keyword_cascade_up(cfg, level, cat, sub=None, ssub=None, sss=None, keyword="") -> bool:
    """Add keyword to the selected node AND all of its ancestors (upwards)."""
    KW = (keyword or "").strip().upper()
    if not KW or not cat:
        return False

    # Ensure parent structures exist
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
        add(cfg["SUBCATEGORY_MAPS"][cat][sub])
        add(cfg["CATEGORY_KEYWORDS"][cat])

    elif level == "subsubcategory":
        add(cfg["SUBSUBCATEGORY_MAPS"][cat][sub][ssub])
        add(cfg["SUBCATEGORY_MAPS"][cat][sub])
        add(cfg["CATEGORY_KEYWORDS"][cat])

    elif level == "subsubsubcategory":
        add(cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub][sss])
        add(cfg["SUBSUBCATEGORY_MAPS"][cat][sub][ssub])
        add(cfg["SUBCATEGORY_MAPS"][cat][sub])
        add(cfg["CATEGORY_KEYWORDS"][cat])

    return added

# -------------------------
# Pages / routing aliases
# -------------------------
@admin_categories_bp.route("/categories", methods=["GET"])
def categories_page():
    cfg = load_cfg()
    # USE LIVE cfg for summaries
    summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    return render_template("manage_categories.html", cfg=cfg, summary_data=summary_data)

# Alias so url_for('admin_categories.index', ...) works (dashboard deep-links)
@admin_categories_bp.route("/categories", methods=["GET"], endpoint="index")
def categories_index():
    return categories_page()

# ----- JSON editor actions -----
@admin_categories_bp.route("/categories/validate", methods=["POST"])
def validate_json():
    text = request.form.get("json_text", "")
    try:
        data = json.loads(text)
        # light structural sanity
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

        # Split into categories vs overrides
        cfg_live = load_cfg()
        paths = cfg_live.get("_PATHS", {})
        categories_path = Path(paths.get("CATEGORIES_PATH", "config/categories.json"))
        overrides_path  = Path(paths.get("KEYWORD_OVERRIDES_PATH", "config/filter_overrides.json"))

        # If the editor provided a CATEGORIES blob, persist it
        if isinstance(data, dict) and "CATEGORIES" in data:
            categories_payload = data.get("CATEGORIES") or {}
            categories_path.write_text(json.dumps(categories_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # Collect editable maps (fallback to existing live cfg for missing keys)
        overrides_payload = {
            "CATEGORY_KEYWORDS": data.get("CATEGORY_KEYWORDS", cfg_live.get("CATEGORY_KEYWORDS", {})),
            "SUBCATEGORY_MAPS": data.get("SUBCATEGORY_MAPS", cfg_live.get("SUBCATEGORY_MAPS", {})),
            "SUBSUBCATEGORY_MAPS": data.get("SUBSUBCATEGORY_MAPS", cfg_live.get("SUBSUBCATEGORY_MAPS", {})),
            "SUBSUBSUBCATEGORY_MAPS": data.get("SUBSUBSUBCATEGORY_MAPS", cfg_live.get("SUBSUBSUBCATEGORY_MAPS", {})),
            "CUSTOM_TRANSACTION_KEYWORDS": data.get("CUSTOM_TRANSACTION_KEYWORDS", cfg_live.get("CUSTOM_TRANSACTION_KEYWORDS", {})),
            "OMIT_KEYWORDS": data.get("OMIT_KEYWORDS", cfg_live.get("OMIT_KEYWORDS", [])),
        }

        # Backup and write overrides
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
    except Exception as e:
        flash(f"Save failed: {e}", "danger")
        return redirect(url_for("admin_categories.categories_page"))

# =========================================================
# Single endpoint to upsert path AND optionally keyword
# =========================================================
@admin_categories_bp.route("/categories/upsert", methods=["POST"])
def upsert_path_and_keyword():
    """
    Creates/normalizes the provided path (category -> sub -> ssub -> sss)
    and, if 'keyword' is provided, attaches it to the chosen target level.
    Supports both HTML form (redirect) and JSON-fetch (JSON response).
    """
    cfg = load_cfg()

    # Values can be new or existing (empty = not used)
    cat  = (request.form.get("cat")  or (request.json.get("cat")  if request.is_json else "") or "").strip()
    sub  = (request.form.get("sub")  or (request.json.get("sub")  if request.is_json else "") or "").strip()
    ssub = (request.form.get("ssub") or (request.json.get("ssub") if request.is_json else "") or "").strip()
    sss  = (request.form.get("sss")  or (request.json.get("sss")  if request.is_json else "") or "").strip()

    target_level = (request.form.get("target_level") or (request.json.get("target_level") if request.is_json else "")).strip()
    target_label = (request.form.get("target_label") or (request.json.get("target_label") if request.is_json else "")).strip()
    keyword      = ((request.form.get("keyword") or (request.json.get("keyword") if request.is_json else "")).strip().upper())

    # Validate: need at least a category to do anything meaningful
    if not cat:
        msg = "Category is required (pick existing or enter a new one)."
        if _wants_json():
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning")
        return redirect(url_for("admin_categories.categories_page"))

    # Ensure base structures
    cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
    cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})

    # Upsert sub if provided
    if sub:
        cfg["SUBCATEGORY_MAPS"][cat].setdefault(sub, [])
        cfg["SUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat].setdefault(sub, {})

    # Upsert sub-sub if provided
    if sub and ssub:
        cfg["SUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, [])
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, {})

    # Upsert sub-sub-sub if provided
    if sub and ssub and sss:
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].setdefault(ssub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub][ssub].setdefault(sss, [])

    added_keyword = False

    # Optionally attach keyword to the target (and bubble up to parents)
    if keyword and target_level and target_label:
        try:
            # Make sure path labels are filled when user just typed the target
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

            # Ensure structures exist up to the chosen depth
            cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
            if sub:
                cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, [])
            if sub and ssub:
                cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, [])
            if sub and ssub and sss:
                cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, {}).setdefault(sss, [])

            # Add keyword to target + all ancestors
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
# Rename + Delete (JSON-aware)
# ==========================================
@admin_categories_bp.route("/categories/rename", methods=["POST"])
def rename_path():
    cfg = load_cfg()
    level = (request.form.get("level") or (request.json.get("level") if request.is_json else "")).strip()
    cat   = (request.form.get("cat")   or (request.json.get("cat")   if request.is_json else "")).strip()
    sub   = (request.form.get("sub")   or (request.json.get("sub")   if request.is_json else "")).strip()
    ssub  = (request.form.get("ssub")  or (request.json.get("ssub")  if request.is_json else "")).strip()
    sss   = (request.form.get("sss")   or (request.json.get("sss")   if request.is_json else "")).strip()
    new_label = (request.form.get("new_label") or (request.json.get("new_label") if request.is_json else "")).strip()

    if not level or not new_label:
        msg = "Level and new name are required."
        if _wants_json(): return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning"); return redirect(url_for("admin_categories.categories_page"))

    try:
        rename_path_in_cfg(cfg, level, cat, sub, ssub, sss, new_label=new_label)
        save_cfg(cfg)
        if _wants_json(): return jsonify({"ok": True, "new_label": new_label})
        flash(f"Renamed {level} to “{new_label}”.", "success")
    except Exception as e:
        if _wants_json(): return jsonify({"ok": False, "error": str(e)}), 400
        flash(f"Rename failed: {e}", "danger")

    return redirect(url_for("admin_categories.categories_page"))


# ---- Added: Simple move endpoint for legacy callers ----

@admin_categories_bp.route("/categories/move", methods=["POST"])
def move_node():
    """
    Move a node (and its children/keywords) to a new parent.
    Payload:
      { "level":"subcategory"|"subsubcategory"|"subsubsubcategory",
        "src": { "cat":..., "sub":..., "ssub":..., "sss":... },
        "dst_parent": { "cat":..., "sub":..., "ssub":... } }
    """
    payload = request.get_json(force=True) or {}
    level = (payload.get("level") or "").strip()
    src = payload.get("src") or {}
    dst = payload.get("dst_parent") or {}

    if level not in {"subcategory","subsubcategory","subsubsubcategory"}:
        return jsonify({"ok": False, "error": "Invalid level"}), 400

    cfg = load_cfg()

    try:
      if level == "subcategory":
        cat_from = (src.get("cat") or "").strip()
        sub_name = (src.get("sub") or "").strip()
        cat_to   = (dst.get("cat") or "").strip()
        if not cat_from or not sub_name or not cat_to:
            raise ValueError("Missing cat/sub")
        # move keywords list
        cfg["SUBCATEGORY_MAPS"].setdefault(cat_to, {})[sub_name] = cfg["SUBCATEGORY_MAPS"].get(cat_from, {}).get(sub_name, [])
        cfg["SUBCATEGORY_MAPS"].get(cat_from, {}).pop(sub_name, None)
        if cfg["SUBCATEGORY_MAPS"].get(cat_from) == {}: cfg["SUBCATEGORY_MAPS"].pop(cat_from, None)
        # move sub-sub maps
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat_to, {})[sub_name] = cfg["SUBSUBCATEGORY_MAPS"].get(cat_from, {}).get(sub_name, {})
        cfg["SUBSUBCATEGORY_MAPS"].get(cat_from, {}).pop(sub_name, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat_from) == {}: cfg["SUBSUBCATEGORY_MAPS"].pop(cat_from, None)
        # move sub-sub-sub maps
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat_to, {})[sub_name] = cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from, {}).get(sub_name, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from, {}).pop(sub_name, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat_from) == {}: cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat_from, None)

      elif level == "subsubcategory":
        cat = (src.get("cat") or "").strip()
        sub_from = (src.get("sub") or "").strip()
        ssub = (src.get("ssub") or "").strip()
        sub_to = (dst.get("sub") or "").strip()
        if not cat or not sub_from or not ssub or not sub_to:
            raise ValueError("Missing cat/sub/ssub")
        # move keyword list
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub_to, {})[ssub] = cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}).get(ssub, [])
        cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}).pop(ssub, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from) == {}: cfg["SUBSUBCATEGORY_MAPS"][cat].pop(sub_from, None)
        # move deeper map
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub_to, {})[ssub] = cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}).get(ssub, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from, {}).pop(ssub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub_from) == {}: cfg["SUBSUBSUBCATEGORY_MAPS"][cat].pop(sub_from, None)

      else:  # subsubsubcategory
        cat = (src.get("cat") or "").strip()
        sub = (src.get("sub") or "").strip()
        ssub_from = (src.get("ssub") or "").strip()
        sss = (src.get("sss") or "").strip()
        ssub_to = (dst.get("ssub") or "").strip()
        if not cat or not sub or not ssub_from or not sss or not ssub_to:
            raise ValueError("Missing cat/sub/ssub/sss")
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub_to, {})[sss] = cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub_from, {}).get(sss, [])
        cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub_from, {}).pop(sss, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub_from) == {}: cfg["SUBSUBSUBCATEGORY_MAPS"][cat][sub].pop(ssub_from, None)

    except Exception as e:
      return jsonify({"ok": False, "error": str(e)}), 400

    save_cfg(cfg)
    return jsonify({"ok": True})

# ---- Drawer-friendly INLINE RENAME API ----
@admin_categories_bp.post("/api/cfg/rename")
def api_cfg_rename():
    """
    Minimal JSON API to rename a label at any depth. Writes to the same
    project-root categories.json used across Drawer, Dashboard, and All-Categories.
    """
    data = request.get_json(silent=True) or {}
    level = (data.get("level") or "").strip().lower()
    old   = (data.get("old") or "").strip()
    new   = (data.get("new") or "").strip()
    cat   = (data.get("cat") or "").strip()
    sub   = (data.get("sub") or "").strip()
    ssub  = (data.get("ssub") or "").strip()

    if level not in {"category","subcategory","subsubcategory","subsubsubcategory"}:
        return jsonify({"ok": False, "error": "Invalid 'level'"}), 400
    if not old or not new or old == new:
        return jsonify({"ok": False, "error": "Provide different 'old' and 'new' names."}), 400

    cfg = load_cfg()

    # Map "old" into correct slot based on level + context
    tcat, tsub, tssub, tsss = None, None, None, None
    if level == "category":
        tcat = old
    elif level == "subcategory":
        if not cat: return jsonify({"ok": False, "error": "Missing 'cat' for subcategory rename."}), 400
        tcat, tsub = cat, old
    elif level == "subsubcategory":
        if not (cat and sub): return jsonify({"ok": False, "error": "Missing 'cat' and 'sub' for subsubcategory rename."}), 400
        tcat, tsub, tssub = cat, sub, old
    else:  # subsubsubcategory
        sss = (data.get("sss") or "").strip()
        if not (cat and sub and sss): return jsonify({"ok": False, "error": "Missing 'cat','sub','sss' for subsubsubcategory rename."}), 400
        tcat, tsub, tssub, tsss = cat, sub, sss, old

    try:
        rename_path_in_cfg(cfg, level, tcat, tsub, tssub, tsss, new_label=new)
        save_cfg(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Rename failed: {e}"}), 400

    return jsonify({"ok": True, "level": level, "old": old, "new": new})

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

    # (non-cascade safety path)
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

def _add_keyword_in_cfg(cfg, level, cat, sub=None, ssub=None, sss=None, keyword="") -> bool:
    kw = (keyword or "").strip().upper()
    if not kw:
        return False
    added = False
    if level == "category":
        arr = cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
        if kw not in arr: arr.append(kw); added = True
    elif level == "subcategory":
        arr = cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub or "", [])
        if kw not in arr: arr.append(kw); added = True
    elif level == "subsubcategory":
        arr = cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub or "", {}).setdefault(ssub or "", [])
        if kw not in arr: arr.append(kw); added = True
    elif level == "subsubsubcategory":
        arr = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub or "", {}).setdefault(ssub or "", {}).setdefault(sss or "", [])
        if kw not in arr: arr.append(kw); added = True
    return added

def _get_json_or_form(key: str) -> str:
    if request.is_json:
        val = (request.json.get(key) or "").strip()
    else:
        val = (request.form.get(key) or "").strip()
    return val

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

    # Ensure structures exist where needed
    cfg["CATEGORY_KEYWORDS"].setdefault(cat, [])
    cfg["SUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {})
    cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {})

    # Cascade add to node + ancestors
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

# NEW: Lightweight keyword list endpoint (useful for chip refresh)
@admin_categories_bp.route("/categories/keyword/list", methods=["GET"])
def keyword_list_api():
    cfg = load_cfg()
    level = (request.args.get("level") or "").strip()
    cat   = (request.args.get("cat")   or "").strip()
    sub   = (request.args.get("sub")   or "").strip()
    ssub  = (request.args.get("ssub")  or "").strip()
    sss   = (request.args.get("sss")   or "").strip()

    if level not in {"category","subcategory","subsubcategory","subsubsubcategory"} or not cat:
        return jsonify({"ok": False, "error": "Invalid request"}), 400

    def _get_keywords():
        if level == "category":
            return cfg["CATEGORY_KEYWORDS"].get(cat, [])
        if level == "subcategory":
            return (cfg["SUBCATEGORY_MAPS"].get(cat, {}).get(sub, []) or [])
        if level == "subsubcategory":
            return (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, []) or [])
        return (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).get(sss, []) or [])

    return jsonify({"ok": True, "keywords": _get_keywords()})

# =========================================================
# Inspect endpoint used by the “Manage” offcanvas UI
# =========================================================
def _safe_date_key(s):
    """Convert date string -> sortable key (desc). Falls back to epoch 0."""
    if not s:
        return 0
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except Exception:
            pass
    # last resort: try first 10 chars like 2024-01-02
    try:
        return int(datetime.strptime(str(s)[:10], "%Y-%m-%d").timestamp())
    except Exception:
        return 0

def _extract_desc(tx):
    """Normalize a description field from various keys."""
    return (tx.get("description")
            or tx.get("desc")
            or tx.get("merchant")
            or "").strip()

def _collect_descendant_transactions(node):
    """Given a summary tree node, collect all leaf transactions beneath it."""
    out = []
    if node is None:
        return out
    children = node.get("children") or []
    if children:
        for ch in children:
            out.extend(_collect_descendant_transactions(ch))
    else:
        # leaf: may have transactions
        txs = node.get("transactions") or []
        out.extend(txs)
    return out

def _find_nodes_by_path(tree, cat=None, sub=None, ssub=None, sss=None):
    """
    Return list of nodes under tree matching the given path (at its depth),
    so we can collect their descendant transactions.
    """
    matches = []

    def walk(node, ancestors):
        parts = ancestors + [node.get("name")]
        depth = len(parts)  # 1..4
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
    """Return (keywords, children_labels) for the given node."""
    if level == "category":
        keywords = cfg["CATEGORY_KEYWORDS"].get(cat, [])[:]
        children = sorted(list((cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {}).keys()))
        return keywords, children

    if level == "subcategory":
        keywords = (cfg["SUBCATEGORY_MAPS"].get(cat, {}).get(sub, []) or [])[:]
        children = sorted(list((cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}) or {}).keys()))
        return keywords, children

    if level == "subsubcategory":
        keywords = (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, []) or [])[:]
        children = sorted(list((cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}) or {}).keys()))
        return keywords, children

    if level == "subsubsubcategory":
        keywords = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).get(sss, []) or [])[:]
        return keywords, []

    return [], []

@admin_categories_bp.route("/categories/inspect", methods=["GET"])
def inspect_path():
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

    # optional limit (default very high)
    try:
        limit = int(request.args.get("limit", "100000"))
    except Exception:
        limit = 100000

    if level not in {"category", "subcategory", "subsubcategory", "subsubsubcategory"}:
        return jsonify({"ok": False, "error": "Invalid level"}), 400
    if not cat:
        return jsonify({"ok": False, "error": "Category is required"}), 400

    # Keywords + children from cfg
    kw, children = _keywords_and_children(cfg, level, cat, sub or None, ssub or None, sss or None)

    # Transactions: recompute summary and collect across months (USE LIVE cfg!)
    try:
        summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    except Exception:
        summary_data = {}

    collected = []

    # ---------- HIDE RULES ----------
    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _is_hidden_amount(x: float) -> bool:
        try:
            xv = float(x)
        except Exception:
            return False
        return any(abs(xv - h) < EPS for h in HIDE_AMOUNTS)
    # --------------------------------

    # Iterate months newest->oldest (keys like YYYY_MM or YYYY-MM)
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
        else:  # category
            targets = _find_nodes_by_path(tree, cat=cat)

        for node in targets:
            collected.extend(_collect_descendant_transactions(node))

    # Normalize, FILTER, sort desc by date, then cap to 'limit'
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

        norm.append({
            "date": date_str,
            "desc": desc_str,
            "amount": amt,
            "_k": _safe_date_key(date_str)
        })

    norm.sort(key=lambda x: x["_k"], reverse=True)
    for n in norm:
        n.pop("_k", None)
    if limit and limit > 0:
        norm = norm[:limit]

    return jsonify({
        "ok": True,
        "data": {
            "keywords": kw,
            "children": children,
            "transactions": norm
        }
    })

# ================================
# Misc / Uncategorized transactions
# ================================
@admin_categories_bp.route("/categories/misc", methods=["GET"])
def list_misc_transactions():
    """
    Returns a list of transactions that live under "misc-style" buckets.
    Query params:
      - labels: pipe- or comma-separated list of node names to treat as misc
                (default: "Miscellaneous|Uncategorized|Unknown")
      - limit:  max rows (default 300)
      - q:      optional search substring (case-insensitive) on description
      - min_abs: minimum absolute amount (float) to include (default 0)
    Response:
      { ok: true, as_of: "YYYY-MM-DD", count: N, transactions: [
          { date, desc, amount, path }
        ] }
    """
    cfg = load_cfg()

    # Params
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

    # Build latest summary using LIVE cfg
    try:
        summary_data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    except Exception:
        summary_data = {}

    # Helpers

    def _first_existing(paths):
        for p in paths:
            if p and Path(p).exists():
                return Path(p)
        return None

    def _walk(node, ancestors):
        yield node, ancestors
        for ch in (node.get("children") or []):
            yield from _walk(ch, ancestors + [node.get("name")])

    def _collect_leaf_txs_with_paths(node, ancestors):
        """Collect descendant leaf transactions; include full path for each leaf."""
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

    # Hide rules
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
            for node, ancestors in _walk(top, []):
                name = (node.get("name") or "").strip()
                if name.lower() in labels:
                    for tx, path_list in _collect_leaf_txs_with_paths(node, []):
                        # normalize
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

    # Sort newest → oldest and cap
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
@admin_categories_bp.route("/categories/cascade_preview", methods=["GET"])
def cascade_preview():
    """
    Returns counts of descendant nodes, total keywords to be removed, and total
    transactions appearing under this node across months (using live summary).
      Query: level, cat, sub, ssub, sss
      Resp: { ok: true, data: { nodes:{...}, keywords:int, transactions:int } }
    """
    cfg = load_cfg()

    level = (request.args.get("level") or "").strip()
    cat   = (request.args.get("cat")   or "").strip()
    sub   = (request.args.get("sub")   or "").strip()
    ssub  = (request.args.get("ssub")  or "").strip()
    sss   = (request.args.get("sss")   or "").strip()

    if level not in {"category","subcategory","subsubcategory","subsubsubcategory"} or not cat:
        return jsonify({"ok": False, "error": "Invalid request"}), 400

    # Count nodes & keywords from cfg
    node_counts = _count_descendants_in_cfg(cfg, level, cat, sub or None, ssub or None, sss or None)
    total_nodes = sum([node_counts["categories"], node_counts["subcategories"], node_counts["subsubcategories"], node_counts["subsubsubcategories"]])

    # Count transactions by reusing inspect logic (no limit)
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
            "nodes": {
                **node_counts,
                "total_nodes": total_nodes
            },
            "keywords": node_counts["keywords"],
            "transactions": tx_count
        }
    })

# =========================================================
# NEW: Bulk manage endpoint for Category Builder “Manage”
# (now with optional cascade)
# =========================================================
@admin_categories_bp.route("/api/manage_category", methods=["POST"])
def manage_category_bulk():
    """
    Accepts:
      {
        "level": "category"|"subcategory"|"subsubcategory"|"subsubsubcategory",
        "ctx": {"cat": "...", "sub": "...", "ssub": "..."},
        "edits": [{"old":"Old","new":"New"}, ...],
        "deletes": ["NameA","NameB", ...],
        "cascade": true|false
      }
    Applies renames and deletes, with optional cascade, then persists.
    """
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

    # --- Renames ---
    try:
        if level == "category":
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "category", cat=old, new_label=new)

        elif level == "subcategory":
            if not cat:
                return jsonify({"ok": False, "error": "Missing category context"}), 400
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "subcategory", cat=cat, sub=old, new_label=new)

        elif level == "subsubcategory":
            if not (cat and sub):
                return jsonify({"ok": False, "error": "Missing category/subcategory context"}), 400
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "subsubcategory", cat=cat, sub=sub, ssub=old, new_label=new)

        else:  # subsubsubcategory
            if not (cat and sub and ssub):
                return jsonify({"ok": False, "error": "Missing category/sub/ssub context"}), 400
            for e in edits:
                old, new = (e.get("old") or "").strip(), (e.get("new") or "").strip()
                if old and new and old != new:
                    rename_path_in_cfg(cfg, "subsubsubcategory", cat=cat, sub=sub, ssub=ssub, sss=old, new_label=new)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Rename failed: {e}"}), 400

    # --- Deletes ---
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
            else:  # subsubsubcategory
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

# =========================================================
# NEW: Drag-and-drop Tree APIs (read + move)
# =========================================================

def _cfg_to_tree(cfg):
    """Return nested tree for UI: [{name, level, children:[...]}, ...]"""
    out = []
    for cat in sorted(cfg["CATEGORY_KEYWORDS"].keys() | cfg["SUBCATEGORY_MAPS"].keys()):
        node = {"name": cat, "level": "category", "children": []}
        submap = cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {}
        for sub in sorted(submap.keys()):
            sn = {"name": sub, "level": "subcategory", "children": []}
            ssubmap = (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}) or {}).get(sub, {}) or {}
            for ssub in sorted(ssubmap.keys()):
                ssn = {"name": ssub, "level": "subsubcategory", "children": []}
                sssmap = (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}) or {}).get(sub, {}).get(ssub, {}) or {}
                for sss in sorted(sssmap.keys()):
                    sssn = {"name": sss, "level": "subsubsubcategory", "children": []}
                    ssn["children"].append(sssn)
                sn["children"].append(ssn)
            node["children"].append(sn)
        out.append(node)
    return out

def _cleanup_empty_cat(cfg, cat):
    """Drop empty containers for a given category across all maps."""
    if cat in cfg["SUBCATEGORY_MAPS"] and cfg["SUBCATEGORY_MAPS"][cat] == {}:
        cfg["SUBCATEGORY_MAPS"].pop(cat, None)
    if cat in cfg["SUBSUBCATEGORY_MAPS"] and cfg["SUBSUBCATEGORY_MAPS"][cat] == {}:
        cfg["SUBSUBCATEGORY_MAPS"].pop(cat, None)
    if cat in cfg["SUBSUBSUBCATEGORY_MAPS"] and cfg["SUBSUBSUBCATEGORY_MAPS"][cat] == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"].pop(cat, None)

def _move_path_in_cfg(cfg, src, dest, new_label=None):
    """
    Move a node (and its descendants) to a new parent.
    src = {"level": "...", "cat": "...", "sub": "...", "ssub": "...", "sss": "..."}
    dest = {"cat": "...", "sub": "...", "ssub": "..."}  # parent path at the appropriate depth
    new_label optional to rename during move.
    """
    lvl  = (src.get("level") or "").strip()
    scat = (src.get("cat")   or "").strip()
    ssub = (src.get("sub")   or "").strip()
    sssu = (src.get("ssub")  or "").strip()
    ssss = (src.get("sss")   or "").strip()

    dcat = (dest.get("cat")  or "").strip()
    dsub = (dest.get("sub")  or "").strip()
    dssu = (dest.get("ssub") or "").strip()

    if lvl not in {"category","subcategory","subsubcategory","subsubsubcategory"}:
        raise ValueError("Invalid src.level")

    # Ensure destination base containers
    if dcat:
        cfg["CATEGORY_KEYWORDS"].setdefault(dcat, cfg["CATEGORY_KEYWORDS"].get(dcat, []))
        cfg["SUBCATEGORY_MAPS"].setdefault(dcat, {})
        cfg["SUBSUBCATEGORY_MAPS"].setdefault(dcat, {})
        cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(dcat, {})

    if lvl == "category":
        # Moving a category == rename
        new_name = (new_label or scat or "").strip()
        if not new_name:
            raise ValueError("New category name required")
        if new_name == scat:
            return
        rename_path_in_cfg(cfg, "category", scat, new_label=new_name)
        return

    if lvl == "subcategory":
        if not (scat and ssub and dcat):
            raise ValueError("Subcategory move requires src.cat/src.sub and dest.cat")
        new_sub = (new_label or ssub).strip()

        # ----- keywords (merge) -----
        src_subs = cfg["SUBCATEGORY_MAPS"].setdefault(scat, {})
        kw_list = src_subs.pop(ssub, [])
        dst_subs = cfg["SUBCATEGORY_MAPS"].setdefault(dcat, {})
        dst_kw = dst_subs.get(new_sub, [])
        dst_subs[new_sub] = _merge_list_unique(dst_kw, kw_list)

        # ----- sub-sub maps (merge) -----
        src_ssub_map = cfg["SUBSUBCATEGORY_MAPS"].setdefault(scat, {})
        ssub_dict = src_ssub_map.pop(ssub, {})
        dst_ssub_map = cfg["SUBSUBCATEGORY_MAPS"].setdefault(dcat, {})
        dst_ssub = dst_ssub_map.get(new_sub, {})
        dst_ssub_map[new_sub] = _merge_nested_dict(dst_ssub or {}, ssub_dict or {})

        # ----- sub-sub-sub mirrors (merge) -----
        src_sss_map = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(scat, {})
        sss_dict = src_sss_map.pop(ssub, {})
        dst_sss_map = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(dcat, {})
        dst_sss = dst_sss_map.get(new_sub, {})
        dst_sss_map[new_sub] = _merge_nested_dict(dst_sss or {}, sss_dict or {})

        _cleanup_empty_cat(cfg, scat)
        return

    if lvl == "subsubcategory":
        if not (scat and ssub and sssu and dcat and dsub):
            raise ValueError("Sub-subcategory move requires src.cat/src.sub/src.ssub and dest.cat/dest.sub")
        new_ssub = (new_label or sssu).strip()

        # ----- keyword list (merge) -----
        src_map = cfg["SUBSUBCATEGORY_MAPS"].setdefault(scat, {}).setdefault(ssub, {})
        kw_list = src_map.pop(sssu, [])
        dst_map = cfg["SUBSUBCATEGORY_MAPS"].setdefault(dcat, {}).setdefault(dsub, {})
        dst_kw = dst_map.get(new_ssub, [])
        dst_map[new_ssub] = _merge_list_unique(dst_kw, kw_list)

        # ----- sss children (merge) -----
        src_mirror = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(scat, {}).setdefault(ssub, {})
        sss_dict = src_mirror.pop(sssu, {})
        dst_mirror = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(dcat, {}).setdefault(dsub, {})
        dst_sss = dst_mirror.get(new_ssub, {})
        dst_mirror[new_ssub] = _merge_nested_dict(dst_sss or {}, sss_dict or {})

        # cleanup empties in old branch
        if cfg["SUBSUBCATEGORY_MAPS"].get(scat, {}).get(ssub) == {}:
            cfg["SUBSUBCATEGORY_MAPS"][scat].pop(ssub, None)
        if cfg["SUBSUBCATEGORY_MAPS"].get(scat) == {}:
            cfg["SUBSUBCATEGORY_MAPS"].pop(scat, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(scat, {}).get(ssub) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"][scat].pop(ssub, None)
        if cfg["SUBSUBSUBCATEGORY_MAPS"].get(scat) == {}:
            cfg["SUBSUBSUBCATEGORY_MAPS"].pop(scat, None)
        return

    # lvl == "subsubsubcategory"
    if not (scat and ssub and sssu and ssss and dcat and dsub and dssu):
        raise ValueError("Sub-sub-subcategory move requires full src and dest.cat/dest.sub/dest.ssub")
    new_sss = (new_label or ssss).strip()

    src_map = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(scat, {}).setdefault(ssub, {}).setdefault(sssu, {})
    kw_list = src_map.pop(ssss, [])
    dst_leaf_parent = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(dcat, {}).setdefault(dsub, {}).setdefault(dssu, {})
    dst_kw = dst_leaf_parent.get(new_sss, [])
    dst_leaf_parent[new_sss] = _merge_list_unique(dst_kw, kw_list)

    # cleanup empties in old branch
    if cfg["SUBSUBSUBCATEGORY_MAPS"].get(scat, {}).get(ssub, {}).get(sssu) == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"][scat][ssub].pop(sssu, None)
    if cfg["SUBSUBSUBCATEGORY_MAPS"].get(scat, {}).get(ssub) == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"][scat].pop(ssub, None)
    if cfg["SUBSUBSUBCATEGORY_MAPS"].get(scat) == {}:
        cfg["SUBSUBSUBCATEGORY_MAPS"].pop(scat, None)
    return


@admin_categories_bp.get("/api/tree")
def api_tree_read():
    """Return the nested tree for drag-and-drop UIs."""
    cfg = load_cfg()
    return jsonify({"ok": True, "tree": _cfg_to_tree(cfg)})

@admin_categories_bp.post("/api/tree/move")
def api_tree_move():
    """
    Move a node (and its descendants) to a new parent.
    JSON:
      {
        "src":  {"level": "...", "cat":"...", "sub":"...", "ssub":"...", "sss":"..."},
        "dest": {"cat":"...", "sub":"...", "ssub":"..."},
        "new_label": "Optional new name"
      }
    """
    data = request.get_json(force=True) or {}
    src = data.get("src") or {}
    dest = data.get("dest") or {}
    new_label = (data.get("new_label") or "").strip()

    cfg = load_cfg()
    try:
        _move_path_in_cfg(cfg, src, dest, new_label=new_label or None)
        save_cfg(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True})
