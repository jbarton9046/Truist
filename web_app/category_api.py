# web_app/category_api.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from flask import Blueprint, jsonify, request, abort
import json
import os

category_api = Blueprint("category_api", __name__)

# --------- PROJECT-ROOT file (Option A) ----------
# __file__ = ...\Truist\web_app\category_api.py
# dirname(dirname(__file__)) == ...\Truist
CATEGORIES_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "categories.json")

# For compatibility, we keep the Node type for return shape,
# but the backing storage is the mapping-based categories.json used by admin.
@dataclass
class Node:
    id: str
    name: str
    keywords: List[str] = field(default_factory=list)
    children: List["Node"] = field(default_factory=list)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "keywords": list(self.keywords),
            "children": [{"id": c.id, "name": c.name} for c in self.children],
        }

# ---- mapping JSON helpers (mirror admin_categories.py schema) ----
EMPTY_CFG = {
    "CATEGORY_KEYWORDS": {},
    "SUBCATEGORY_MAPS": {},
    "SUBSUBCATEGORY_MAPS": {},
    "SUBSUBSUBCATEGORY_MAPS": {},
    "CUSTOM_TRANSACTION_KEYWORDS": {},
    "OMIT_KEYWORDS": []
}

def _load_cfg() -> Dict[str, Any]:
    if not os.path.exists(CATEGORIES_JSON_PATH):
        # seed empty file
        with open(CATEGORIES_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(EMPTY_CFG, f, indent=2)
    with open(CATEGORIES_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f) or {}
    cfg = EMPTY_CFG.copy()
    cfg.update({k: v for k, v in data.items() if k in cfg})
    # normalize missing parents
    for k in EMPTY_CFG.keys():
        cfg.setdefault(k, EMPTY_CFG[k])
    return cfg

def _save_cfg(cfg: Dict[str, Any]) -> None:
    with open(CATEGORIES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False, sort_keys=True)

def _resolve_path(parts: List[str]) -> Dict[str, Any]:
    """
    Given ['Income','JL Pay','Cash Tips','Ringling Tips'] return:
      level: 'category'|'subcategory'|'subsubcategory'|'subsubsubcategory'
      cat, sub, ssub, sss (strings or '')
    """
    cat = parts[0] if len(parts) >= 1 else ""
    sub = parts[1] if len(parts) >= 2 else ""
    ssub = parts[2] if len(parts) >= 3 else ""
    sss = parts[3] if len(parts) >= 4 else ""
    level = "category"
    if sss:
        level = "subsubsubcategory"
    elif ssub:
        level = "subsubcategory"
    elif sub:
        level = "subcategory"
    return {"level": level, "cat": cat, "sub": sub, "ssub": ssub, "sss": sss}

def _exists(cfg: Dict[str, Any], level: str, cat: str, sub: str, ssub: str, sss: str) -> bool:
    if not cat:
        return False
    if level == "category":
        return cat in cfg["CATEGORY_KEYWORDS"] or cat in cfg["SUBCATEGORY_MAPS"]
    if level == "subcategory":
        return sub in (cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {})
    if level == "subsubcategory":
        return ssub in (cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}) or {})
    if level == "subsubsubcategory":
        return sss in (cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}) or {})
    return False

def _node_from_cfg(cfg: Dict[str, Any], level: str, cat: str, sub: str, ssub: str, sss: str) -> Node:
    # Determine name/id for this node
    name = sss or ssub or sub or cat or ""
    nid = name

    # Keywords at this level
    if level == "category":
        keywords = list(cfg["CATEGORY_KEYWORDS"].get(cat, []))
        children = sorted(list((cfg["SUBCATEGORY_MAPS"].get(cat, {}) or {}).keys()))
        return Node(id=nid, name=name, keywords=keywords,
                    children=[Node(id=c, name=c) for c in children])

    if level == "subcategory":
        keywords = list(cfg["SUBCATEGORY_MAPS"].get(cat, {}).get(sub, []) or [])
        children = sorted(list((cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}) or {}).keys()))
        return Node(id=nid, name=name, keywords=keywords,
                    children=[Node(id=c, name=c) for c in children])

    if level == "subsubcategory":
        keywords = list(cfg["SUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, []) or [])
        children = sorted(list((cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}) or {}).keys()))
        return Node(id=nid, name=name, keywords=keywords,
                    children=[Node(id=c, name=c) for c in children])

    # subsubsubcategory
    keywords = list(cfg["SUBSUBSUBCATEGORY_MAPS"].get(cat, {}).get(sub, {}).get(ssub, {}).get(sss, []) or [])
    return Node(id=nid, name=name, keywords=keywords, children=[])

# ---- cascade helper (add keyword to node + all ancestors) ----
def _add_keyword_cascade_up(cfg: Dict[str, Any], level: str, cat: str, sub: str, ssub: str, sss: str, kw: str) -> None:
    """Add KW to the node and all ancestors."""
    KW = (kw or "").strip().upper()
    if not KW or not cat:
        return

    cfg.setdefault("CATEGORY_KEYWORDS", {}).setdefault(cat, [])
    cfg.setdefault("SUBCATEGORY_MAPS", {}).setdefault(cat, {})
    cfg.setdefault("SUBSUBCATEGORY_MAPS", {}).setdefault(cat, {})
    cfg.setdefault("SUBSUBSUBCATEGORY_MAPS", {}).setdefault(cat, {})

    def add(arr):
        if KW not in arr: arr.append(KW)

    if level == "category":
        add(cfg["CATEGORY_KEYWORDS"].setdefault(cat, []))

    elif level == "subcategory":
        add(cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, []))
        add(cfg["CATEGORY_KEYWORDS"].setdefault(cat, []))

    elif level == "subsubcategory":
        add(cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, []))
        add(cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, []))
        add(cfg["CATEGORY_KEYWORDS"].setdefault(cat, []))

    else:  # subsubsubcategory
        add(cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, {}).setdefault(sss, []))
        add(cfg["SUBSUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, {}).setdefault(ssub, []))
        add(cfg["SUBCATEGORY_MAPS"].setdefault(cat, {}).setdefault(sub, []))
        add(cfg["CATEGORY_KEYWORDS"].setdefault(cat, []))

# ------------------ Routes ------------------

@category_api.get("/categories/<path:node_ref>/details")
def get_category_details(node_ref: str):
    """
    node_ref can be:
      - a single label (e.g., "Income")
      - or a path separated by "::" (e.g., "Income::JL Pay::Cash Tips")
    Returns { id, name, keywords[], children[] } based on mapping JSON.
    """
    parts = [p for p in node_ref.split("::") if p]
    if not parts:
        abort(404, description="Category not found")

    cfg = _load_cfg()
    ctx = _resolve_path(parts)
    if not _exists(cfg, **ctx):
        abort(404, description="Category not found")

    node = _node_from_cfg(cfg, **ctx)
    return jsonify(node.to_summary())

@category_api.post("/categories/<path:node_ref>/keywords")
def add_keyword(node_ref: str):
    """
    Add a keyword to the mapping-based node (bubbles up to all parents).
    """
    payload = request.get_json(silent=True) or {}
    kw = (payload.get("keyword") or "").strip()
    if not kw:
        abort(400, description="Missing 'keyword'")

    parts = [p for p in node_ref.split("::") if p]
    cfg = _load_cfg()
    ctx = _resolve_path(parts)
    if not _exists(cfg, **ctx):
        abort(404, description="Category not found")

    # normalize & cascade
    KW = kw.upper()
    _add_keyword_cascade_up(cfg, ctx["level"], ctx["cat"], ctx["sub"], ctx["ssub"], ctx["sss"], KW)

    _save_cfg(cfg)
    node = _node_from_cfg(cfg, **ctx)
    return jsonify({"ok": True, "keywords": node.keywords})

@category_api.delete("/categories/<path:node_ref>/keywords")
def remove_keyword(node_ref: str):
    """
    Remove a keyword from the mapping-based node (case-insensitive). Does NOT cascade upward.
    """
    payload = request.get_json(silent=True) or {}
    kw = (payload.get("keyword") or "").strip()
    if not kw:
        abort(400, description="Missing 'keyword'")

    parts = [p for p in node_ref.split("::") if p]
    cfg = _load_cfg()
    ctx = _resolve_path(parts)
    if not _exists(cfg, **ctx):
        abort(404, description="Category not found")

    lower = kw.lower()

    if ctx["level"] == "category":
        arr = cfg["CATEGORY_KEYWORDS"].setdefault(ctx["cat"], [])
        arr[:] = [k for k in arr if k.lower() != lower]

    elif ctx["level"] == "subcategory":
        arr = cfg["SUBCATEGORY_MAPS"].setdefault(ctx["cat"], {}).setdefault(ctx["sub"], [])
        arr[:] = [k for k in arr if k.lower() != lower]

    elif ctx["level"] == "subsubcategory":
        arr = cfg["SUBSUBCATEGORY_MAPS"].setdefault(ctx["cat"], {}).setdefault(ctx["sub"], {}).setdefault(ctx["ssub"], [])
        arr[:] = [k for k in arr if k.lower() != lower]

    else:  # subsubsubcategory
        arr = cfg["SUBSUBSUBCATEGORY_MAPS"].setdefault(ctx["cat"], {}).setdefault(ctx["sub"], {}).setdefault(ctx["ssub"], {}).setdefault(ctx["sss"], [])
        arr[:] = [k for k in arr if k.lower() != lower]

    _save_cfg(cfg)
    node = _node_from_cfg(cfg, **ctx)
    return jsonify({"ok": True, "keywords": node.keywords})
