# truist/debug_config.py
from flask import Blueprint, jsonify, request
from pathlib import Path
import os

debug_bp = Blueprint("debug", __name__)

def _read_first(path: Path, n: int = 500):
    try:
        if path.exists():
            txt = path.read_text(encoding="utf-8")
            return txt[:n] + ("… (truncated)" if len(txt) > n else "")
        return "<missing>"
    except Exception as e:
        return f"<error: {e}>"

@debug_bp.route("/debug/config", methods=["GET"])
def debug_config():
    """
    Shows where CONFIG_DIR points and previews live config files.
    Safe to keep temporarily while debugging on Render (remove later).
    """
    p = Path(os.environ.get("CONFIG_DIR", "config"))
    cats_path = p / "categories.json"
    ovrd_path = p / "filter_overrides.json"

    return jsonify({
        "CONFIG_DIR": str(p),
        "exists": {
            "categories.json": cats_path.exists(),
            "filter_overrides.json": ovrd_path.exists(),
        },
        "preview": {
            "categories.json": _read_first(cats_path),
            "filter_overrides.json": _read_first(ovrd_path),
        }
    })

@debug_bp.route("/debug/seed_categories", methods=["GET", "POST"])
def seed_categories():
    """
    Seed $CONFIG_DIR/categories.json from truist/categories.json.
    Add ?force=1 to overwrite if it already exists.
    """
    p = Path(os.environ.get("CONFIG_DIR", "config"))
    p.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).with_name("categories.json")   # truist/categories.json
    dst = p / "categories.json"
    force = str(request.args.get("force", "0")).lower() in {"1", "true", "yes", "on"}

    if not src.exists():
        return jsonify({"ok": False, "error": f"Seed file not found at {src}"}), 500

    if dst.exists() and not force:
        return jsonify({
            "ok": False,
            "message": "categories.json already exists; add ?force=1 to overwrite",
            "path": str(dst)
        }), 200

    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return jsonify({"ok": True, "seeded": True, "path": str(dst)})

@debug_bp.route("/debug/init_overrides", methods=["GET", "POST"])
def init_overrides():
    """
    Ensure $CONFIG_DIR/filter_overrides.json exists (create empty {} if missing).
    """
    p = Path(os.environ.get("CONFIG_DIR", "config"))
    p.mkdir(parents=True, exist_ok=True)
    dst = p / "filter_overrides.json"
    created = False
    if not dst.exists():
        dst.write_text("{}\n", encoding="utf-8")
        created = True
    return jsonify({"ok": True, "path": str(dst), "created": created})
