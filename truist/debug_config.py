# truist/debug_config.py
from flask import Blueprint, jsonify, request
from pathlib import Path
import os, shutil, zipfile, io, json, re
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

def _clean_desc(s: str) -> str:
    """Mirror parser cleaning: uppercase, strip hyphens, collapse spaces."""
    s = (s or "").strip().upper().replace("-", "")
    return re.sub(r"\s+", " ", s)

def _load_overrides_and_categories():
    """Load both JSON files from CONFIG_DIR; return (overrides, categories)."""
    cfg_dir = _config_dir()
    ovrd_path = cfg_dir / "filter_overrides.json"
    cats_path = cfg_dir / "categories.json"
    overrides = {}
    categories = {}
    try:
        if ovrd_path.exists():
            overrides = json.loads(ovrd_path.read_text(encoding="utf-8")) or {}
    except Exception:
        overrides = {}
    try:
        if cats_path.exists():
            categories = json.loads(cats_path.read_text(encoding="utf-8")) or {}
    except Exception:
        categories = {}
    return overrides, categories

def _should_omit_debug(desc: str, amt: float, overrides: dict, categories: dict) -> bool:
    """
    Lightweight copy of the parser's omit logic:
      - substring match against OMIT_KEYWORDS (from overrides first, then categories)
      - amount-based rules in overrides: [{"contains":"AMZNCOMBILL","min":300, "max":optional}]
    Uses ABS(amount) for threshold checks, same as parser.
    """
    U = _clean_desc(desc)
    omit_keywords = list(overrides.get("OMIT_KEYWORDS") or []) or list(categories.get("OMIT_KEYWORDS") or [])
    if any((k or "").upper() in U for k in omit_keywords):
        return True

    rules = list(overrides.get("AMOUNT_OMIT_RULES") or [])
    try:
        value = abs(float(amt))
    except Exception:
        value = 0.0
    for r in rules:
        contains = _clean_desc(r.get("contains") or "")
        if not contains:
            continue
        min_v = float(r.get("min", 0))
        max_raw = r.get("max", None)
        max_v = float(max_raw) if max_raw is not None else None
        if contains in U:
            if value >= min_v and (max_v is None or value <= max_v):
                return True
    return False

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

@debug_bp.route("/debug/effective_config", methods=["GET"])
def effective_config():
    """
    Show sizes and small samples from merged/overlay data to sanity check config impact.
    (Reads raw JSON files; your runtime merge still happens in code.)
    """
    overrides, categories = _load_overrides_and_categories()

    def _size_and_sample(val):
        if isinstance(val, dict):
            keys = sorted(list(val.keys()))[:10]
            return {"size": len(val), "sample_keys": keys}
        if isinstance(val, list):
            return {"size": len(val), "sample_first_10": val[:10]}
        return {"size": 0, "sample": None}

    out = {
        "overrides": {
            "CATEGORY_KEYWORDS": _size_and_sample(overrides.get("CATEGORY_KEYWORDS")),
            "SUBCATEGORY_MAPS": _size_and_sample(overrides.get("SUBCATEGORY_MAPS")),
            "SUBSUBCATEGORY_MAPS": _size_and_sample(overrides.get("SUBSUBCATEGORY_MAPS")),
            "SUBSUBSUBCATEGORY_MAPS": _size_and_sample(overrides.get("SUBSUBSUBCATEGORY_MAPS")),
            "OMIT_KEYWORDS": _size_and_sample(overrides.get("OMIT_KEYWORDS")),
            "AMOUNT_OMIT_RULES": _size_and_sample(overrides.get("AMOUNT_OMIT_RULES")),
        },
        "categories_json": {
            "CATEGORY_KEYWORDS": _size_and_sample(categories.get("CATEGORY_KEYWORDS")),
            "SUBCATEGORY_MAPS": _size_and_sample(categories.get("SUBCATEGORY_MAPS")),
            "SUBSUBCATEGORY_MAPS": _size_and_sample(categories.get("SUBSUBCATEGORY_MAPS")),
            "SUBSUBSUBCATEGORY_MAPS": _size_and_sample(categories.get("SUBSUBSUBCATEGORY_MAPS")),
            "OMIT_KEYWORDS": _size_and_sample(categories.get("OMIT_KEYWORDS")),
        }
    }
    return jsonify({"ok": True, **out})

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

# ---- NEW: amount-based omit rule add ----
@debug_bp.route("/debug/add_amount_omit", methods=["GET", "POST"])
def add_amount_omit():
    """
    Add an amount-based omit rule into $CONFIG_DIR/filter_overrides.json.
    Params (query or form):
      contains (str) - substring to search (case-insensitive; cleaned like parser)
      min (float)    - minimum ABS(amount) inclusive (default 0)
      max (float)    - optional maximum ABS(amount) inclusive
    Example:
      /debug/add_amount_omit?contains=AMZNCOMBILL&min=300.01
    """
    cfg_dir = _config_dir()
    ovrd = cfg_dir / "filter_overrides.json"
    ovrd.parent.mkdir(parents=True, exist_ok=True)

    # read current
    try:
        cur = json.loads(ovrd.read_text(encoding="utf-8")) if ovrd.exists() else {}
    except Exception:
        cur = {}

    contains = (request.values.get("contains") or "").strip()
    if not contains:
        return jsonify({"ok": False, "error": "missing 'contains'"}), 400

    try:
        min_v = float(request.values.get("min", 0))
    except Exception:
        min_v = 0.0
    max_raw = request.values.get("max", None)
    max_v = float(max_raw) if max_raw is not None else None

    rules = list(cur.get("AMOUNT_OMIT_RULES", []))
    rule = {"contains": contains, "min": min_v}
    if max_v is not None:
        rule["max"] = max_v
    rules.append(rule)
    cur["AMOUNT_OMIT_RULES"] = rules

    ovrd.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "added": rule, "total_rules": len(rules), "overrides_path": str(ovrd)})

# ---- NEW: test whether a description/amount would be omitted ----
@debug_bp.route("/debug/test_omit", methods=["GET"])
def test_omit():
    """
    Test omit logic quickly.
      /debug/test_omit?desc=YOUR+DESC&amount=2138.93
    Returns omit_hit = true/false using overrides + categories.
    """
    desc = request.args.get("desc", "")
    amount = request.args.get("amount", "0")
    try:
        amt = float(amount)
    except Exception:
        amt = 0.0

    overrides, categories = _load_overrides_and_categories()
    hit = _should_omit_debug(desc, amt, overrides, categories)
    return jsonify({
        "ok": True,
        "desc_clean": _clean_desc(desc),
        "amount_abs": abs(amt),
        "omit_hit": bool(hit),
        "rules_count": len(overrides.get("AMOUNT_OMIT_RULES", [])),
        "omit_keywords_count": len(overrides.get("OMIT_KEYWORDS", []) or categories.get("OMIT_KEYWORDS", []) or []),
    })
