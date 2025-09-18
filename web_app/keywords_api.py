# keywords_api.py
from __future__ import annotations
from flask import Blueprint, request, jsonify, current_app
from pathlib import Path
import json
from typing import Dict, List

# Use your existing helper if available; otherwise fall back to CWD
try:
    from truist.parser_web import get_statements_base_dir  # type: ignore
except Exception:
    def get_statements_base_dir() -> Path:
        return Path(".").resolve()

bp = Blueprint("keywords_api", __name__, url_prefix="/api/keywords")

def _store_path() -> Path:
    base = get_statements_base_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "keyword_overrides.json"

def _load_store() -> Dict[str, List[str]]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        current_app.logger.exception("Failed to load keyword_overrides.json: %s", e)
        return {}

def _save_store(data: Dict[str, List[str]]) -> None:
    p = _store_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def _norm_path(raw: str) -> str:
    return "/".join(seg.strip() for seg in (raw or "").split("/") if seg.strip())

@bp.get("")
def list_keywords():
    path = _norm_path(request.args.get("path", ""))
    if not path:
        return jsonify({"error": "Missing 'path'"}), 400
    store = _load_store()
    return jsonify({"path": path, "keywords": store.get(path, [])})

@bp.delete("")
def delete_keyword():
    path = _norm_path(request.args.get("path", ""))
    kw = (request.args.get("keyword") or "").strip()
    if not path or not kw:
        return jsonify({"error": "Missing 'path' or 'keyword'"}), 400
    store = _load_store()
    changed = False
    if kw in store.get(path, []):
        arr = [k for k in store[path] if k != kw]
        if arr:
            store[path] = arr
        else:
            store.pop(path, None)
        changed = True
    if changed:
        _save_store(store)
    return jsonify({"ok": True, "path": path, "removed": kw, "changed": changed})
