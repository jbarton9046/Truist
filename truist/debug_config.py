# truist/debug_config.py
from flask import Blueprint, jsonify, request
from pathlib import Path
import os

# Optional deeper debug imports
try:
    from truist.admin_categories import load_cfg
    from truist.parser_web import generate_summary
except Exception:
    load_cfg = None
    generate_summary = None

debug_bp = Blueprint("debug", __name__)

# ---------- helpers ----------
def _read_first(path: Path, n: int = 500):
    try:
        if path.exists():
            txt = path.read_text(encoding="utf-8")
            return txt[:n] + ("… (truncated)" if len(txt) > n else "")
        return "<missing>"
    except Exception as e:
        return f"<error: {e}>"

def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "config"))

# ---------- routes ----------
@debug_bp.route("/debug/config", methods=["GET"])
def debug_config():
    """
    Shows where CONFIG_DIR points and previews live config files.
    Safe to keep temporarily while debugging on Render (remove later).
    """
    p = _config_dir()
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
    p = _config_dir()
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
    p = _config_dir()
    p.mkdir(parents=True, exist_ok=True)
    dst = p / "filter_overrides.json"
    created = False
    if not dst.exists():
        dst.write_text("{}\n", encoding="utf-8")
        created = True
    return jsonify({"ok": True, "path": str(dst), "created": created})

@debug_bp.route("/debug/summary", methods=["GET"])
def debug_summary():
    """
    High-level view: how many months & approx how many transactions the app sees.
    Requires load_cfg + generate_summary to be importable.
    """
    if not load_cfg or not generate_summary:
        return jsonify({"ok": False, "error": "debug_summary requires load_cfg and generate_summary"}), 500
    try:
        cfg = load_cfg()
        data = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
        months = sorted(list(data.keys()))
        total_months = len(months)

        # Approx transaction count by walking the monthly trees
        def count_leaf_txs(node):
            children = node.get("children") or []
            if children:
                return sum(count_leaf_txs(ch) for ch in children)
            return len(node.get("transactions") or [])

        tx_count = 0
        for m in data.values():
            for node in (m or {}).get("tree", []):
                tx_count += count_leaf_txs(node)

        sample = months[-6:] if total_months > 6 else months
        return jsonify({
            "ok": True,
            "months_found": total_months,
            "months_sample": sample,
            "approx_tx_count": tx_count
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@debug_bp.route("/debug/statements", methods=["GET"])
def debug_statements():
    """
    Lists likely statement directories and a small sample of files under each.
    Helps confirm your JSON/CSV sources are present on the server.
    """
    here = Path(__file__).resolve()
    project_root = here.parents[1]  # .../project_root
    candidates = [
        project_root / "statements",          # repo_root/statements
        here.parent / "statements",           # truist/statements
        Path.cwd() / "statements",            # working dir/statements
        Path("/var/data") / "statements",     # persistent disk (if you put them there)
    ]
    out = []
    for p in candidates:
        if p.exists():
            files = sorted([f.name for f in p.glob("**/*") if f.is_file()])
            out.append({"path": str(p), "exists": True, "count": len(files), "files_sample": files[:10]})
        else:
            out.append({"path": str(p), "exists": False})
    return jsonify({"ok": True, "candidates": out})
