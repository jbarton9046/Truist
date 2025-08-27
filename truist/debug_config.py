# truist/debug_config.py
from flask import Blueprint, jsonify, request
from pathlib import Path
import os, shutil, zipfile, io, json
from werkzeug.utils import secure_filename

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

def _first_existing(paths):
    for p in paths:
        pp = Path(p)
        if pp.exists():
            return pp
    return None

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
    Seed $CONFIG_DIR/categories.json from the repo.
    Supports both repo layouts:
      - <repo_root>/categories.json
      - <repo_root>/truist/categories.json
    Add ?force=1 to overwrite if it already exists.
    """
    p = _config_dir()
    p.mkdir(parents=True, exist_ok=True)

    here = Path(__file__).resolve()
    project_root = here.parents[1]
    seed_candidates = [
        project_root / "categories.json",   # repo root
        here.with_name("categories.json"),  # truist/categories.json
    ]
    src = _first_existing(seed_candidates)
    if not src:
        return jsonify({"ok": False, "error": "No seed categories.json found in repo root or truist/"}), 500

    dst = p / "categories.json"
    force = str(request.args.get("force", "0")).lower() in {"1", "true", "yes", "on"}

    if dst.exists() and not force:
        return jsonify({
            "ok": False,
            "message": "categories.json already exists; add ?force=1 to overwrite",
            "path": str(dst)
        }), 200

    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return jsonify({"ok": True, "seeded": True, "from": str(src), "to": str(dst)})

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

# ---- NEW: migrate keywords from categories.json -> filter_overrides.json ----
@debug_bp.route("/debug/migrate_keywords", methods=["GET", "POST"])
def migrate_keywords():
    """
    One-time migration: copy CATEGORY_KEYWORDS / SUB*MAPS / OMIT_KEYWORDS found in
    $CONFIG_DIR/categories.json into $CONFIG_DIR/filter_overrides.json (merge).
    Dicts are shallow-merged (overrides win). Lists are unioned (unique).
    """
    cfg_dir = _config_dir()
    cats_path = cfg_dir / "categories.json"
    ovrd_path = cfg_dir / "filter_overrides.json"

    if not cats_path.exists():
        return jsonify({"ok": False, "error": f"categories.json not found at {cats_path}"}), 404

    try:
        source = json.loads(cats_path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to read categories.json: {e}"}), 500

    keys = [
        "CATEGORY_KEYWORDS",
        "SUBCATEGORY_MAPS",
        "SUBSUBCATEGORY_MAPS",
        "SUBSUBSUBCATEGORY_MAPS",
        "OMIT_KEYWORDS",
    ]
    picked = {k: source.get(k) for k in keys if source.get(k) is not None}

    # existing overrides or empty
    current = {}
    if ovrd_path.exists():
        try:
            current = json.loads(ovrd_path.read_text(encoding="utf-8"))
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to read filter_overrides.json: {e}"}), 500

    def merge(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            out = dict(a)
            out.update(b)
            return out
        if isinstance(a, list) and isinstance(b, list):
            return list(dict.fromkeys(a + b))
        return b if b is not None else a

    merged = dict(current)
    for k, v in picked.items():
        base = current.get(k, {} if isinstance(v, dict) else [])
        merged[k] = merge(base, v)

    try:
        ovrd_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to write overrides: {e}"}), 500

    sizes = {}
    for k in keys:
        val = merged.get(k)
        if isinstance(val, dict):
            sizes[k] = len(val)
        elif isinstance(val, list):
            sizes[k] = len(val)
        else:
            sizes[k] = 0

    return jsonify({
        "ok": True,
        "categories_json": str(cats_path),
        "overrides_json": str(ovrd_path),
        "migrated_keys": list(picked.keys()),
        "overrides_sizes": sizes
    })

@debug_bp.route("/debug/seed_statements", methods=["GET"])
def seed_statements():
    """
    Copy any repo statements to the persistent disk at /var/data/statements.
    Searches these repo locations:
      - <repo_root>/statements
      - <repo_root>/truist/statements
    """
    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    candidates = [
        repo_root / "statements",
        here.parent / "statements",  # truist/statements
    ]
    src = None
    for c in candidates:
        if c.exists() and any(c.glob("**/*")):
            src = c
            break
    if not src:
        return jsonify({"ok": False, "error": "No statements folder found in repo_root or truist/"}), 404

    dst = Path("/var/data/statements")
    dst.mkdir(parents=True, exist_ok=True)

    copied = 0
    for f in src.rglob("*"):
        if f.is_file():
            rel = f.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            copied += 1

    return jsonify({"ok": True, "from": str(src), "to": str(dst), "files_copied": copied})

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

# ---- TEMPORARY upload routes ----
@debug_bp.route("/debug/upload", methods=["GET"])
def upload_form():
    return (
        """
        <h1>Upload statements</h1>
        <form action="/debug/upload" method="post" enctype="multipart/form-data">
          <p><input type="file" name="files" multiple></p>
          <p><button type="submit">Upload</button></p>
        </form>
        <p>Tip: you can upload individual CSV/JSON files or a single .zip.</p>
        """,
        200,
        {"Content-Type": "text/html"},
    )

@debug_bp.route("/debug/upload", methods=["POST"])
def upload_files():
    """
    Upload CSV/JSON or a .zip of them to /var/data/statements.
    """
    dst_base = Path("/var/data/statements")
    dst_base.mkdir(parents=True, exist_ok=True)
    saved = []
    extracted = 0

    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "no files provided"}), 400

    for f in files:
        filename = secure_filename(f.filename or "")
        if not filename:
            continue

        # If it's a ZIP, extract statement-like files
        if filename.lower().endswith(".zip"):
            data = f.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for member in z.infolist():
                    if member.is_dir():
                        continue
                    if not (member.filename.lower().endswith(".csv") or member.filename.lower().endswith(".json")):
                        continue
                    target = dst_base / Path(member.filename).name
                    with z.open(member, "r") as src, open(target, "wb") as out:
                        out.write(src.read())
                        extracted += 1
            continue

        # Otherwise save file directly
        target = dst_base / filename
        f.save(target)
        saved.append(str(target))

    return jsonify({"ok": True, "saved": saved, "extracted_from_zip": extracted, "dest": str(dst_base)})

# ---- EXTRA: effective config & omit tester (handy while tuning)
@debug_bp.route("/debug/effective_config", methods=["GET"])
def effective_config():
    """
    Show sizes and a few sample keys of the live merged configuration (load_cfg()).
    """
    if not load_cfg:
        return jsonify({"ok": False, "error": "load_cfg not available"}), 500
    try:
        cfg = load_cfg()
        out = {
            "ok": True,
            "paths": cfg.get("_PATHS", {}),
            "sizes": {
                "CATEGORY_KEYWORDS": len(cfg.get("CATEGORY_KEYWORDS", {})),
                "SUBCATEGORY_MAPS": len(cfg.get("SUBCATEGORY_MAPS", {})),
                "OMIT_KEYWORDS": len(cfg.get("OMIT_KEYWORDS", [])),
                "CATEGORIES(top-level)": len((cfg.get("CATEGORIES") or {}).get("CATEGORY_KEYWORDS", {})) if isinstance(cfg.get("CATEGORIES"), dict) else 0,
            },
            "samples": {
                "CATEGORY_KEYWORDS": list(cfg.get("CATEGORY_KEYWORDS", {}).keys())[:5],
                "SUBCATEGORY_MAPS": list(cfg.get("SUBCATEGORY_MAPS", {}).keys())[:5],
                "OMIT_KEYWORDS": cfg.get("OMIT_KEYWORDS", [])[:10],
            }
        }
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@debug_bp.route("/debug/test_omit", methods=["GET"])
def test_omit():
    """
    Quick check: does a given description match current omit keywords?
    Usage: /debug/test_omit?desc=Some+merchant+name
    """
    desc = request.args.get("desc", "")
    if not desc:
        return jsonify({"ok": False, "error": "pass ?desc=..."}), 400
    if not load_cfg:
        return jsonify({"ok": False, "error": "load_cfg not available"}), 500
    try:
        cfg = load_cfg()
        omits = [str(x).upper() for x in (cfg.get("OMIT_KEYWORDS") or [])]
        hit = any(x in desc.upper() for x in omits)
        return jsonify({"ok": True, "desc": desc, "omit_hit": hit, "omit_keywords_size": len(omits)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
