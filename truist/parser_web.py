import json
import re
import os
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import truist.filter_config as fc

# Expose the effective JSON path for visibility/imports elsewhere (e.g., app/admin UI)
JSON_PATH = None  # set by _load_category_config()

def _debug(msg: str):
    if os.environ.get("CL_DEBUG"):
        try:
            print(f"[ClarityLedger] {msg}")
        except Exception:
            pass

# === Load category config (JSON + overrides from CONFIG_DIR) ===
def _load_category_config():
    """
    Merge order:
      1) Python defaults in filter_config.py
      2) categories.json (repo root or truist/)
      3) CONFIG_DIR/filter_overrides.json (live overrides)
    Returns:
      (CATEGORY_KEYWORDS, SUBCATEGORY_MAPS, SUBSUBCATEGORY_MAPS, SUBSUBSUBCATEGORY_MAPS,
       CUSTOM_TRANSACTION_KEYWORDS, OMIT_KEYWORDS, AMOUNT_OMIT_RULES, source_str)
    """
    global JSON_PATH

    base_dir = Path(__file__).resolve().parent   # .../truist
    project_root = base_dir.parents[1]           # .../<repo_root>

    # Defaults from code
    cfg = {
        "CATEGORY_KEYWORDS": getattr(fc, "CATEGORY_KEYWORDS", {}),
        "SUBCATEGORY_MAPS": getattr(fc, "SUBCATEGORY_MAPS", {}),
        "SUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBCATEGORY_MAPS", {}),
        "SUBSUBSUBCATEGORY_MAPS": getattr(fc, "SUBSUBSUBCATEGORY_MAPS", {}),
        "CUSTOM_TRANSACTION_KEYWORDS": getattr(fc, "CUSTOM_TRANSACTION_KEYWORDS", {}),
        "OMIT_KEYWORDS": getattr(fc, "OMIT_KEYWORDS", []),
        "AMOUNT_OMIT_RULES": getattr(fc, "AMOUNT_OMIT_RULES", []),  # NEW
    }

    # Prefer the same JSON the Category Builder uses (project root), else local.
    json_candidates = [project_root / "categories.json", base_dir / "categories.json"]
    json_path = next((p for p in json_candidates if p.exists()), None)
    JSON_PATH = json_path
    source = "filter_config.py"  # will be updated below

    def _merge_dict(a, b):
        out = dict(a or {})
        out.update(b or {})
        return out

    def _merge_list(a, b):
        # preserve order, remove dups
        return list(dict.fromkeys((a or []) + (b or [])))

    # Merge categories.json (if present)
    if json_path:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                jcfg = json.load(f) or {}
            for k, v in jcfg.items():
                if v is None:
                    continue
                if isinstance(v, dict):
                    cfg[k] = _merge_dict(cfg.get(k, {}), v)
                elif isinstance(v, list):
                    cfg[k] = _merge_list(cfg.get(k, []), v)
                else:
                    cfg[k] = v
            source = f"categories.json ({json_path})"
        except Exception:
            pass  # fall back silently

    # Merge filter_overrides.json from CONFIG_DIR (if present)
    cfg_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    ovrd_path = cfg_dir / "filter_overrides.json"
    if ovrd_path.exists():
        try:
            with open(ovrd_path, "r", encoding="utf-8") as f:
                ocfg = json.load(f) or {}
            for k, v in ocfg.items():
                if v is None:
                    continue
                if isinstance(v, dict):
                    cfg[k] = _merge_dict(cfg.get(k, {}), v)
                elif isinstance(v, list):
                    cfg[k] = _merge_list(cfg.get(k, []), v)
                else:
                    cfg[k] = v
            source += f" + overrides ({ovrd_path})"
        except Exception:
            pass

    return (
        cfg["CATEGORY_KEYWORDS"],
        cfg["SUBCATEGORY_MAPS"],
        cfg["SUBSUBCATEGORY_MAPS"],
        cfg["SUBSUBSUBCATEGORY_MAPS"],
        cfg["CUSTOM_TRANSACTION_KEYWORDS"],
        cfg["OMIT_KEYWORDS"],
        cfg["AMOUNT_OMIT_RULES"],   # NEW
        source,
    )


(
    category_keywords,
    subcategory_maps,
    subsubcategory_maps,
    subsubsubcategory_maps,
    custom_tx_keywords,
    omit_keywords,
    amount_omit_rules,   # NEW
    CONFIG_SOURCE,
) = _load_category_config()

# Helpful trace in your Flask console
print(f"[ClarityLedger] Category config source: {CONFIG_SOURCE}")
if JSON_PATH:
    print(f"[ClarityLedger] JSON_PATH = {JSON_PATH}")


# === Date helpers ===
def _parse_any_date(s: str):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _safe_date_key(s):
    dt = _parse_any_date(s or "")
    return dt if dt else datetime.min


# === Utility Functions ===
def clean_description(desc: str) -> str:
    desc = (desc or "").strip().upper()
    desc = desc.replace("-", "")
    return re.sub(r"\s+", " ", desc)


def deduplicate(transactions):
    seen = set()
    unique = []
    for tx in transactions:
        key = (tx.get("date"), round(float(tx.get("amount", 0.0)), 2), tx.get("category"))
        if key not in seen:
            seen.add(key)
            unique.append(tx)
    return unique


def is_interest_income(desc, amt):
    return "INTEREST PAYMENT" in (desc or "").upper() and amt > 0


# --- Keyword hit helper (STRICT only) ---
def _kw_hits(desc: str, kw: str) -> bool:
    """
    Keep partial substring behavior by default, but enforce whole-word matching
    for a small curated set of 'troublemaker' keywords from fc.STRICT_BOUNDARY_KEYWORDS.
    """
    desc = (desc or "").upper()
    kw = (kw or "").upper()
    strict = set(getattr(fc, "STRICT_BOUNDARY_KEYWORDS", []))
    if kw in strict:
        return re.search(rf"\b{re.escape(kw)}\b", desc) is not None
    return kw in desc  # default: partials keep working


# --- Central transfer detector (uses fc.TRANSFER_KEYWORDS) ---
def _looks_like_transfer(desc: str) -> bool:
    U = (desc or "").upper()
    for kw in getattr(fc, "TRANSFER_KEYWORDS", []):
        if kw.upper() in U:
            return True
    return False


# --- NEW: Return detection & normalized expense math ---
def _is_return(desc: str) -> bool:
    U = (desc or "").upper()
    kws = getattr(fc, "RETURN_KEYWORDS", None)
    if not kws:
        kws = ("RETURN", "REFUND", "REVERSAL")
    return any(k.upper() in U for k in kws)


def _expense_amount(raw_amount: float, is_return: bool) -> float:
    """
    Convert a raw signed amount into 'expense space':
      - Purchases: +abs(amount)
      - Returns:   -abs(amount)
    We DO NOT infer from bank sign; we use keywords to flip.
    """
    base = abs(float(raw_amount))
    return -base if is_return else base


# --- Simple omit helper for substring-based OMIT_KEYWORDS ---
def _should_omit(desc: str, omit_list) -> bool:
    if not omit_list:
        return False
    U = clean_description(desc)
    return any((k or "").upper() in U for k in omit_list)


# --- Omit helper (supports substring + amount rules) ---
def _should_omit_tx(desc: str, amt_signed: float, omit_list, amount_rules) -> bool:
    """
    omit_list: list of substrings (case-insensitive)
    amount_rules: list of {"contains": "TEXT", "min": float?, "max": float?}
    Match is on UPPER(desc). Amount check uses abs(amt_signed).
    """
    if _should_omit(desc, omit_list):
        return True

    U = clean_description(desc)
    try:
        val = abs(float(amt_signed or 0.0))
    except Exception:
        val = 0.0

    for rule in (amount_rules or []):
        try:
            contains = clean_description(rule.get("contains") or "")
            if contains and contains not in U:
                continue
            mn = rule.get("min", None)
            mx = rule.get("max", None)
            if (mn is None or val >= float(mn)) and (mx is None or val <= float(mx)):
                return True
        except Exception:
            continue
    return False


# === Statements discovery (disk first, then repo fallbacks) ===
def _candidate_statement_dirs():
    """Ordered search paths for statements (disk first, then repo fallbacks)."""
    here = Path(__file__).resolve()
    project_root = here.parents[1]
    env_dir = os.environ.get("STATEMENTS_DIR")
    plaid_dir = os.environ.get("PLAID_DIR")
    dirs = []
    if env_dir:
        dirs.append(Path(env_dir))
    # Common on Render/Docker
    dirs.append(Path("/var/data/statements"))
    # NEW: also look in a dedicated Plaid area
    if plaid_dir:
        dirs.append(Path(plaid_dir))
    dirs.append(Path("/var/data/plaid"))
    # Project fallbacks
    dirs.append(project_root / "statements")
    dirs.append(project_root / "plaid")  # NEW
    dirs.append(here.parent / "statements")   # truist/statements
    dirs.append(here.parent / "plaid")        # truist/plaid
    dirs.append(Path.cwd() / "statements")
    dirs.append(Path.cwd() / "plaid")         # local dev
    # De-dup while preserving order
    out = []
    seen = set()
    for d in dirs:
        if str(d) not in seen:
            out.append(d)
            seen.add(str(d))
    _debug(f"scan roots: {[str(x) for x in out]}")
    return out


def get_statements_base_dir() -> Path:
    """First existing candidate; falls back to /var/data/statements."""
    for d in _candidate_statement_dirs():
        if d.exists():
            return d
    return Path("/var/data/statements")


def discover_statement_files():
    """Find CSV/JSON statements across candidate dirs (flat or nested)."""
    exts = {".csv", ".json"}
    files = []
    for base in _candidate_statement_dirs():
        if base.exists():
            files.extend(
                p for p in base.rglob("*")
                if p.is_file() and p.suffix.lower() in exts
            )
    _debug(f"found files: {len(files)}")
    return files


# === File loaders ===
def _parse_money(s: str) -> float:
    if s is None:
        return 0.0
    t = str(s).strip()
    if t == "":
        return 0.0
    # Handle parentheses for negatives and $/commas
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1]
    t = t.replace("$", "").replace(",", "")
    try:
        v = float(t)
    except Exception:
        # Sometimes "1,234.56-" or "-1,234.56" or "CR"/"DR"
        t2 = t.replace("-", "")
        try:
            v = float(t2)
            if "-" in t:
                neg = True
        except Exception:
            return 0.0
    return -v if neg else v


def _extract_tx_from_json_payload(obj):
    """Return a list of tx dicts from a parsed JSON object, or [] if not a tx payload."""
    try:
        # Plaid /transactions/get or general dumps
        if isinstance(obj, dict):
            if isinstance(obj.get("transactions"), list):
                return obj.get("transactions") or []
            # Plaid /transactions/sync style
            out = []
            if isinstance(obj.get("added"), list):
                out.extend(obj.get("added"))
            if isinstance(obj.get("modified"), list):
                out.extend(obj.get("modified"))
            # (removed are just ids)
            if out:
                return out
            # Some wrappers use a data field
            if isinstance(obj.get("data"), dict):
                if isinstance(obj["data"].get("transactions"), list):
                    return obj["data"].get("transactions") or []
        elif isinstance(obj, list):
            # Raw list of txs
            if obj and isinstance(obj[0], dict) and ("date" in obj[0] or "amount" in obj[0] or "name" in obj[0] or "description" in obj[0]):
                return obj
    except Exception:
        return []
    return []


def load_json_transactions(file_path: Path):
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return []
    return _extract_tx_from_json_payload(data)


def load_csv_transactions(file_path: Path):
    """Robust CSV reader for common bank exports (Amount OR Debit/Credit forms)."""
    rows = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().upper() for h in (reader.fieldnames or [])]

        # Candidate columns
        date_cols = ["DATE", "POSTED DATE", "TRANSACTION DATE", "DATE POSTED"]
        desc_cols = ["DESCRIPTION", "MEMO", "NAME", "TRANSACTION DESCRIPTION", "DETAILS"]
        amt_cols  = ["AMOUNT", "TRANSACTION AMOUNT", "AMT"]
        debit_cols = ["DEBIT", "WITHDRAWAL"]
        credit_cols = ["CREDIT", "DEPOSIT"]

        def pick(colnames):
            for c in colnames:
                if c in headers:
                    return c
            return None

        DATE = pick(date_cols)
        DESC = pick(desc_cols)

        # Amount logic
        AMOUNT = pick(amt_cols)
        DEBIT  = pick(debit_cols)
        CREDIT = pick(credit_cols)

        for raw in reader:
            # Normalize keys to upper for safe access
            row = { (k or "").strip().upper(): v for k, v in raw.items() }

            # Date
            ds = row.get(DATE or "", "") if DATE else ""
            dt = _parse_any_date(ds)
            if not dt:
                # try alternative date columns ad-hoc
                for alt in date_cols:
                    ds = row.get(alt, "")
                    dt = _parse_any_date(ds)
                    if dt:
                        break
            if not dt:
                continue  # skip if we can't parse a date

            # Description
            desc = clean_description(row.get(DESC or "", "")) if DESC else ""
            if not desc:
                for alt in desc_cols:
                    v = row.get(alt, "")
                    if v:
                        desc = clean_description(v)
                        break

            # Amount
            if AMOUNT:
                raw_amt = _parse_money(row.get(AMOUNT, "0"))
            elif DEBIT or CREDIT:
                d = _parse_money(row.get(DEBIT or "", "0"))
                c = _parse_money(row.get(CREDIT or "", "0"))
                # Some exports use positive numbers; treat debit as negative, credit as positive
                if d and c:
                    raw_amt = c - d
                elif d:
                    raw_amt = -abs(d)
                else:
                    raw_amt = abs(c)
            else:
                # Fallback: look for any numeric-looking field named like *AMOUNT*
                candidates = [k for k in row.keys() if "AMOUNT" in k]
                raw_amt = _parse_money(row.get(candidates[0], "0")) if candidates else 0.0

            rows.append({
                "date": dt.strftime("%Y-%m-%d"),  # normalized later
                "amount": raw_amt,
                "description": desc,
                "pending": False,
            })
    return rows


def load_manual_transactions(file_path: Path):
    """Read newline-delimited JSON; normalize date to MM/DD/YYYY and clean description."""
    transactions = []
    if not file_path.exists():
        return transactions
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            tx = json.loads(line)
            tx["amount"] = float(tx["amount"])
            tx["description"] = clean_description(tx.get("description", ""))
            dt = _parse_any_date(tx.get("date", ""))
            if dt:
                tx["date"] = dt.strftime("%m/%d/%Y")
            # mark return + compute expense_amount for manual rows too
            tx["is_return"] = _is_return(tx["description"])
            tx["expense_amount"] = _expense_amount(tx["amount"], tx["is_return"])
            transactions.append(tx)
    return transactions


def categorize_transaction(desc, amount, category_keywords):
    amt_rounded = round(amount, 2)
    desc = (desc or "").upper()

    # Transfers FIRST so they never fall into Misc/Uncategorized
    if _looks_like_transfer(desc):
        return "Transfers"

    # Hard-coded exceptions
    if "CHECK" in desc:
        if amt_rounded == 264.00:
            return "Fees"
        if amt_rounded == 2500.00:
            return "Rent/Utilities"

    if "TRANSFER" in desc:
        return "Transfers"

    if "COSTCO" in desc and amt_rounded == 65.00:
        return "Subscriptions"

    if "WALMART" in desc and amt_rounded == 212.93:
        return "Phone"

    if "SARASOTA COUNTY PU" in desc:
        return "Rent/Utilities"

    if "HARD ROCK" in desc and "CREDIT" in desc:
        return "Income"

    # Prioritize Income over other categories
    priority_order = ["Income"] + [cat for cat in category_keywords if cat != "Income"]
    for category in priority_order:
        for keyword in category_keywords.get(category, []):
            if _kw_hits(desc, keyword):
                return category

    return "Miscellaneous"


def _iter_all_raw_transactions():
    """Yield raw transaction dicts from discovered CSV and JSON files (skipping non-tx JSON)."""
    files = discover_statement_files()
    processed = 0
    for file in files:
        ext = file.suffix.lower()

        # Skip clearly non-transaction JSON to avoid parsing secrets/config
        if ext == ".json":
            nm = file.name.lower()
            if nm in {"access_token.json", "token.json", "client_secret.json", "secrets.json"}:
                continue
            if nm.endswith("categories.json"):
                continue

        try:
            if ext == ".json":
                data = load_json_transactions(file)
                if data:
                    for tx in data:
                        yield tx
                    processed += 1
                else:
                    # not a tx JSON payload
                    continue
            elif ext == ".csv":
                data = load_csv_transactions(file)
                for tx in data:
                    yield tx
                processed += 1
        except Exception as e:
            _debug(f"failed parsing {file}: {e}")

    _debug(f"parsed files: {processed}")


def _tx_from_raw(raw, category_keywords, custom_tx_keywords_live):
    """Normalize one raw row into our internal tx dict."""
    # Skip pending
    if isinstance(raw, dict) and raw.get("pending", False):
        return None

    # Extract date
    date_str = None
    if isinstance(raw, dict):
        date_str = raw.get("date") or raw.get("DATE") or raw.get("posted") or raw.get("POSTED")
        # Plaid sometimes uses posting_date/authorized_date
        if not date_str:
            date_str = raw.get("posting_date") or raw.get("authorized_date")
    dt = _parse_any_date(date_str)
    if not dt:
        return None

    # Extract amount
    raw_amt = None
    if isinstance(raw, dict):
        for key in ("amount", "AMOUNT", "transaction_amount", "TRANSACTION AMOUNT", "authorized_amount"):
            if key in raw:
                try:
                    raw_amt = float(raw[key])
                    break
                except Exception:
                    pass
    if raw_amt is None:
        raw_amt = 0.0

    # Description
    desc = clean_description(
        raw.get("description")
        or raw.get("name", "")
        or raw.get("merchant_name", "")
        or raw.get("desc", "")
        or raw.get("original_description", "")
        or ""
    )

    # Custom exact-key override
    custom_key = f"{desc} - ${round(raw_amt, 2)}"
    if custom_tx_keywords_live and custom_key in custom_tx_keywords_live:
        category = custom_tx_keywords_live[custom_key]["category"]
        subcategory = custom_tx_keywords_live[custom_key].get("subcategory")
    else:
        category = categorize_transaction(desc, raw_amt, category_keywords)
        subcategory = None  # matched later

    # Return/expense math
    is_return = _is_return(desc)
    expense_amount = _expense_amount(raw_amt, is_return)

    # UI sign logic
    if category == "Income":
        norm_amount = abs(raw_amt)
    else:
        norm_amount = abs(raw_amt) if is_return else -abs(raw_amt)

    tx = {
        "date": dt.strftime("%m/%d/%Y"),
        "amount": norm_amount,            # UI/sign logic
        "category": category,
        "description": desc,
        "is_return": is_return,
        "expense_amount": expense_amount  # category math
    }
    if subcategory:
        tx["subcategory"] = subcategory
    if category == "Transfers":
        tx["is_transfer"] = True
    return tx


def _build_tree_from_categories(categories_dict):
    """
    Builds a generic tree with up to 4 levels for the recursive UI.
    """
    tree = []

    def make_node(name, total, txs, subcats, subsubs, subsubsubs):
        node = {"name": name, "total": round(total, 2), "transactions": txs, "children": []}

        for sub_name, sub_total in sorted((subcats or {}).items(), key=lambda x: -x[1]):
            sub_txs = [t for t in txs if t.get("subcategory") == sub_name]
            child = {"name": sub_name, "total": round(sub_total, 2), "transactions": sub_txs, "children": []}

            if subsubs and sub_name in subsubs:
                for ssub_name, ssub_total in sorted(subsubs[sub_name].items(), key=lambda x: -x[1]):
                    ssub_txs = [t for t in sub_txs if t.get("subsubcategory") == ssub_name]
                    grandchild = {"name": ssub_name, "total": round(ssub_total, 2), "transactions": ssub_txs, "children": []}

                    if subsubsubs and sub_name in subsubsubs and ssub_name in subsubsubs[sub_name]:
                        for sss_name, sss_total in sorted(subsubsubs[sub_name][ssub_name].items(), key=lambda x: -x[1]):
                            sss_txs = [t for t in ssub_txs if t.get("subsubsubcategory") == sss_name]
                            great = {"name": sss_name, "total": round(sss_total, 2), "transactions": sss_txs, "children": []}
                            grandchild["children"].append(great)

                    child["children"].append(grandchild)

            node["children"].append(child)

        node["children"].sort(key=lambda n: -n["total"])
        return node

    for cat_name, data in categories_dict.items():
        node = make_node(
            cat_name,
            data.get("total", 0.0),
            data.get("transactions", []),
            data.get("subcategories", {}),
            data.get("subsubcategories", {}),
            data.get("subsubsubcategories", {}),
        )
        tree.append(node)

    tree.sort(key=lambda n: -n["total"])
    return tree


def _hidden_categories():
    """Hidden categories from code (fc.HIDDEN_CATEGORIES) + JSON overrides in CONFIG_DIR/filter_overrides.json."""
    hidden = set()
    try:
        from truist import filter_config as _fc
        hidden |= set(getattr(_fc, "HIDDEN_CATEGORIES", []) or [])
    except Exception:
        pass
    try:
        import os as _os, json as _json
        from pathlib import Path as _Path
        cfg_dir = _Path(_os.environ.get("CONFIG_DIR", "config"))
        ov_path = cfg_dir / "filter_overrides.json"
        if ov_path.exists():
            j = _json.loads(ov_path.read_text(encoding="utf-8"))
            for c in (j.get("HIDDEN_CATEGORIES") or []):
                hidden.add(c)
    except Exception:
        pass
    return hidden


def list_hidden_categories():
    """Small helper to introspect effective hidden categories for debugging."""
    return sorted(_hidden_categories())


def generate_summary(category_keywords, subcategory_maps):
    """
    Build monthly summaries using the *latest* config every call (so renames & deeper maps stay in sync).
    Returns offset spending within the same category via tx['expense_amount'].
    """
    # Pull fresh config for deeper maps & omit/custom rules
    (
        _ck,  # unused here (we take category_keywords from the arg)
        _sm,  # unused here (we take subcategory_maps from the arg)
        subsubcategory_maps_live,
        subsubsubcategory_maps_live,
        custom_tx_keywords_live,
        omit_keywords_live,
        amount_omit_rules_live,   # NEW
        _src,
    ) = _load_category_config()

    hidden_cats = _hidden_categories()  # <-- NEW

    # Use discovered base dir for manual entries
    statements_base = get_statements_base_dir()
    manual_file = statements_base / "manual_transactions.json"

    all_tx = []

    # Load from both JSON and CSV sources discovered on disk/repo
    for raw in _iter_all_raw_transactions():
        tx = _tx_from_raw(raw, category_keywords, custom_tx_keywords_live)
        if tx:
            all_tx.append(tx)

    # Load manual entries (already normalized above)
    all_tx.extend(load_manual_transactions(manual_file))

    # --- Legacy cleanup + categorize missing manual entries ---
    for tx in all_tx:
        # 1) Nuke old hardcoded Paychecks so config can take over
        if tx.get("category") == "Income" and tx.get("subcategory") == "Paychecks":
            tx.pop("subcategory", None)
            tx.pop("sub_subcategory", None)

        # 2) Categorize any transaction missing a category (manual entries after we stopped hardcoding)
        if not tx.get("category"):
            cat = categorize_transaction(tx.get("description", ""), float(tx.get("amount", 0.0)), category_keywords)
            tx["category"] = cat
            if cat == "Transfers":
                tx["is_transfer"] = True

        # Ensure is_return / expense_amount exist
        if "expense_amount" not in tx:
            tx["is_return"] = _is_return(tx.get("description", ""))
            tx["expense_amount"] = _expense_amount(tx.get("amount", 0.0), tx["is_return"])

        # Ensure UI sign convention for ALL rows (including manual):
        # Income => +; Expense purchase => -; Expense return => +
        cat = tx.get("category", "")
        if cat == "Income":
            tx["amount"] = abs(float(tx.get("amount", 0.0)))
        else:
            if tx.get("is_return"):
                tx["amount"] = abs(float(tx.get("amount", 0.0)))
            else:
                tx["amount"] = -abs(float(tx.get("amount", 0.0)))

    # Deduplicate (keeps one per (date, signed_amount, category))
    all_tx = deduplicate(all_tx)

    # Sort safely regardless of input date format
    all_tx.sort(key=lambda t: _parse_any_date(t.get("date") or "") or datetime.min)

    # Group by month
    months = defaultdict(list)
    for tx in all_tx:
        dt = _parse_any_date(tx.get("date", ""))
        if not dt:
            continue
        month_key = dt.strftime("%Y-%m")
        months[month_key].append(tx)

    monthly_summaries = {}

    # Build monthly summaries
    for month_key in sorted(months.keys()):
        month_tx = months[month_key]

        income_total = 0.0
        expense_net = 0.0  # net of purchases minus returns

        categorized = defaultdict(list)
        categorized_data = defaultdict(lambda: {"total": 0.0, "transactions": [], "subcategories": defaultdict(float)})

        for tx in month_tx:
            desc = tx["description"]
            cat = tx["category"]
            amt_signed = float(tx["amount"])               # UI sign (+income, -purchase, +return)
            exp_amt = float(tx.get("expense_amount", 0.0)) # +spend, -return

            # Global omit/skip rules (now includes amount rules)
            if _should_omit_tx(desc, amt_signed, omit_keywords_live, amount_omit_rules_live):
                continue
            if cat == "Transfers" or (cat == "Venmo" and round(abs(amt_signed), 2) != 200.00) or (cat == "Credit Card" and abs(amt_signed) > 300) or cat in hidden_cats:
                continue

            # Withdrawals owner tagging
            if cat == "Withdrawals":
                if "6466" in desc:
                    tx["owner"] = "Rachel"
                elif "3453" in desc or "8842" in desc:
                    tx["owner"] = "JL"
                else:
                    tx["owner"] = "Unknown"

            # Totals
            if cat == "Income":
                income_total += abs(amt_signed)
            else:
                expense_net += exp_amt  # purchases add, returns subtract

            categorized[cat].append(tx)
            cd = categorized_data[cat]

            # Category totals: Income uses income amounts; others use expense_amount
            if cat == "Income":
                cd["total"] += abs(amt_signed)
            else:
                cd["total"] += exp_amt

            cd["transactions"].append(tx)

            # Respect manual subcats first
            matched = False
            forced_subcat = tx.get("subcategory")
            forced_subsub = tx.get("sub_subcategory")

            if forced_subcat:
                # Subcategory totals: use expense_amount for expenses, income amount for income
                if cat == "Income":
                    cd["subcategories"][forced_subcat] += abs(amt_signed)
                else:
                    cd["subcategories"][forced_subcat] += exp_amt
                tx["subcategory"] = forced_subcat
                matched = True

                if forced_subsub:
                    if "subsubcategories" not in cd:
                        cd["subsubcategories"] = defaultdict(lambda: defaultdict(float))
                    # Sub-sub also uses exp/income logic
                    if cat == "Income":
                        cd["subsubcategories"][forced_subcat][forced_subsub] += abs(amt_signed)
                    else:
                        cd["subsubcategories"][forced_subcat][forced_subsub] += exp_amt
                    tx["subsubcategory"] = forced_subsub

            # Keyword matching
            sub_map = subcategory_maps.get(cat, {})
            if not matched and sub_map:
                for subcat_label, keywords in sub_map.items():
                    if any(_kw_hits(desc, k) for k in keywords):
                        if cat == "Income":
                            cd["subcategories"][subcat_label] += abs(amt_signed)
                        else:
                            cd["subcategories"][subcat_label] += exp_amt
                        tx["subcategory"] = subcat_label
                        matched = True

                        # Sub-subcategory match
                        subsub_map = subsubcategory_maps_live.get(cat, {}).get(subcat_label, {})
                        subsub_matched = None
                        for subsub_label, subsub_keywords in subsub_map.items():
                            if any(_kw_hits(desc, ssub_kw) for ssub_kw in subsub_keywords):
                                subsub_matched = subsub_label
                                tx["subsubcategory"] = subsub_label
                                if "subsubcategories" not in cd:
                                    cd["subsubcategories"] = defaultdict(lambda: defaultdict(float))
                                if cat == "Income":
                                    cd["subsubcategories"][subcat_label][subsub_label] += abs(amt_signed)
                                else:
                                    cd["subsubcategories"][subcat_label][subsub_label] += exp_amt
                                break

                        # Sub-sub-subcategory match (requires a sub-sub match)
                        if subsub_matched:
                            subsubsub_map = (
                                subsubsubcategory_maps_live
                                .get(cat, {})
                                .get(subcat_label, {})
                                .get(subsub_matched, {})
                            )
                            for subsubsub_label, subsubsub_keywords in subsubsub_map.items():
                                if any(_kw_hits(desc, k) for k in subsubsub_keywords):
                                    tx["subsubsubcategory"] = subsubsub_label
                                    if "subsubsubcategories" not in cd:
                                        cd["subsubsubcategories"] = defaultdict(
                                            lambda: defaultdict(lambda: defaultdict(float))
                                        )
                                    if cat == "Income":
                                        cd["subsubsubcategories"][subcat_label][subsub_matched][subsubsub_label] += abs(amt_signed)
                                    else:
                                        cd["subsubsubcategories"][subcat_label][subsub_matched][subsubsub_label] += exp_amt
                                    break
                        break

            if not matched and sub_map:
                # Uncategorized bucket uses same exp/income logic
                if cat == "Income":
                    cd["subcategories"]["🟡 Other/Uncategorized"] += abs(amt_signed)
                else:
                    cd["subcategories"]["🟡 Other/Uncategorized"] += exp_amt
                tx["subcategory"] = "🟡 Other/Uncategorized"

        # Compose month summary
        # Keep expense_total non-negative for UI sanity; returns reduce it.
        expense_total = round(max(0.0, expense_net), 2)

        month_summary = {
            "income_total": round(income_total, 2),
            "expense_total": expense_total,
            "net_cash_flow": round(income_total - expense_total, 2),
            "categories": {},
            "all_transactions": month_tx,
            "config_source": CONFIG_SOURCE,  # visible for debugging
        }

        for cat, data in sorted(categorized_data.items(), key=lambda x: -x[1]["total"]):
            subcats = data["subcategories"]
            sub_map = subcategory_maps.get(cat, {})
            has_defined_subcats = bool(sub_map)

            if has_defined_subcats:
                # For display, drop the 'Other/Uncategorized' line if you prefer (kept behavior)
                filtered_subcats = {k: v for k, v in subcats.items() if k != "🟡 Other/Uncategorized"}
                subcat_output = {k: round(v, 2) for k, v in sorted(filtered_subcats.items(), key=lambda x: -x[1])}
            else:
                subcat_output = {}

            month_summary["categories"][cat] = {
                "total": round(data["total"], 2),
                "subcategories": subcat_output,
                "transactions": data["transactions"],
            }

            # Sub-subcategory breakdown if present
            if "subsubcategories" in data:
                month_summary["categories"][cat]["subsubcategories"] = {
                    subcat: {
                        subsub: round(amount, 2)
                        for subsub, amount in sorted(subsubs.items(), key=lambda x: -x[1])
                    }
                    for subcat, subsubs in data["subsubcategories"].items()
                }

            # Sub-sub-subcategory breakdown if present
            if "subsubsubcategories" in data:
                month_summary["categories"][cat]["subsubsubcategories"] = {
                    subcat: {
                        subsub: {
                            subsubsub: round(amount, 2)
                            for subsubsub, amount in sorted(subsubsubs.items(), key=lambda x: -x[1])
                        }
                        for subsub, subsubsubs in subsubs.items()
                    }
                    for subcat, subsubs in data["subsubsubcategories"].items()
                }

        # Build tree for recursive UI (supports 4 levels)
        month_summary["tree"] = _build_tree_from_categories(month_summary["categories"])

        monthly_summaries[month_key] = month_summary

    return monthly_summaries


def recent_activity_summary(
    days=30,
    large_threshold=500,   # kept for compatibility
    max_items=5,
    include_income=False,
    max_recent=20
):
    """
    Dashboard-friendly activity snapshot:
      - recent_txs: most recent transactions (filtered), newest first
      - movers_abs: category movers (latest vs prev) by absolute $ delta
      - latest_totals: income/expense/net for latest month + deltas vs prev
    """
    # Always build from latest config
    (
        ck, sm, _ss, _sss, _custom, omit_live, amount_rules, _src
    ) = _load_category_config()

    hidden_cats = _hidden_categories()  # <-- NEW

    try:
        monthly = generate_summary(ck, sm)
    except Exception:
        monthly = {}

    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "window_days": int(days),
        "latest_month": None,
        "prev_month": None,
        "latest_totals": {
            "income": 0.0, "expense": 0.0, "net": 0.0,
            "prev_income": 0.0, "prev_expense": 0.0, "prev_net": 0.0,
            "delta_income": 0.0, "delta_expense": 0.0, "delta_net": 0.0,
            "pct_income": None, "pct_expense": None, "pct_net": None,
        },
        "movers_abs": [],
        "top_ups": [],
        "top_downs": [],
        "recent_txs": [],
        "recent_windows": {
            "last_7_expense": 0.0, "last_7_income": 0.0,
            "last_30_expense": 0.0, "last_30_income": 0.0
        }
    }
    if not monthly:
        return out

    months_sorted = sorted(monthly.keys())
    latest = months_sorted[-1]
    prev   = months_sorted[-2] if len(monthly) > 1 else None
    out["latest_month"] = latest
    out["prev_month"]   = prev

    latest_blob = monthly.get(latest, {}) or {}
    prev_blob   = monthly.get(prev,   {}) or {}

    # ---- Latest totals & deltas
    Li = float(latest_blob.get("income_total")  or 0.0)
    Le = float(latest_blob.get("expense_total") or 0.0)
    Ln = float(latest_blob.get("net_cash_flow") or (Li - Le))

    Pi = float(prev_blob.get("income_total")  or 0.0)
    Pe = float(prev_blob.get("expense_total") or 0.0)
    Pn = float(prev_blob.get("net_cash_flow") or (Pi - Pe))

    def _pct(cur, old):
        if prev is None: return None
        if abs(old) < 1e-9: return 1.0 if abs(cur) > 0 else 0.0
        return (cur - old) / old

    out["latest_totals"] = {
        "income": Li, "expense": Le, "net": Ln,
        "prev_income": Pi, "prev_expense": Pe, "prev_net": Pn,
        "delta_income": Li-Pi, "delta_expense": Le-Pe, "delta_net": Ln-Pn,
        "pct_income": _pct(Li, Pi), "pct_expense": _pct(Le, Pe), "pct_net": _pct(Ln, Pn),
    }

    # ---- Category movers vs prev (expenses by default; include_income toggles it)
    latest_cats = (latest_blob.get("categories") or {})
    prev_cats   = (prev_blob.get("categories") or {})

    rows = []
    for name, data in latest_cats.items():
        if not include_income and name == "Income":
            continue
        latest_total = float(data.get("total") or 0.0)
        prev_total   = float((prev_cats.get(name, {}) or {}).get("total") or 0.0)
        pct = _pct(latest_total, prev_total)
        rows.append({
            "name": name,
            "latest": latest_total,
            "prev": prev_total,
            "delta": latest_total - prev_total,
            "pct": pct
        })

    movers_abs = sorted(rows, key=lambda r: abs(r["delta"]), reverse=True)[: max_items * 2]
    out["movers_abs"] = movers_abs

    ups   = [r for r in rows if r["pct"] is not None and r["pct"] > 0]
    downs = [r for r in rows if r["pct"] is not None and r["pct"] < 0]
    out["top_ups"]   = sorted(ups,   key=lambda r: (r["pct"], r["delta"]), reverse=True)[:max_items]
    out["top_downs"] = sorted(downs, key=lambda r: (r["pct"], r["delta"]))[:max_items]

    # ---- Filters / hide sentinels
    HIDE_AMOUNTS = [10002.02, -10002.02]
    EPS = 0.005
    def _is_hidden_amount(x: float) -> bool:
        try:
            xv = float(x)
        except Exception:
            return False
        return any(abs(xv - h) < EPS for h in HIDE_AMOUNTS)

    def _include_for_recent(t):
        desc = clean_description(
            t.get("description") or t.get("desc") or t.get("merchant_name") or t.get("name") or ""
        )
        cat = t.get("category", "")
        try:
            amt = float(t.get("amount") or 0.0)
        except Exception:
            amt = 0.0

        if _should_omit_tx(desc, amt, omit_live, amount_rules):
            return False
        if cat == "Transfers" or (cat == "Venmo" and round(abs(amt), 2) != 200.00) or (cat == "Credit Card" and abs(amt) > 300) or cat in hidden_cats:
            return False
        if _is_hidden_amount(amt):
            return False
        return True

    # ---- Most recent transactions (newest first)
    recent = []
    for mk in months_sorted[::-1]:  # newest first
        txs = (monthly.get(mk, {}) or {}).get("all_transactions") or []
        for t in txs:
            if not _include_for_recent(t):
                continue
            recent.append({
                "date": t.get("date", ""),
                "desc": (t.get("description") or t.get("desc") or ""),
                "amount": float(t.get("amount") or 0.0),  # UI sign (+income, -purchase, +return)
                "category": t.get("category", ""),
                "subcategory": t.get("subcategory", "")
            })

    recent.sort(key=lambda x: (_safe_date_key(x["date"]), abs(x["amount"])), reverse=True)
    out["recent_txs"] = recent[: max_recent]

    # ---- Quick 7/30-day windows (use UI-signed amount)
    now = datetime.now()
    cut7 = now - timedelta(days=7)
    cut30 = now - timedelta(days=30)
    l7e = l7i = l30e = l30i = 0.0
    for x in recent:
        dt = _parse_any_date(x["date"])
        if not dt:
            continue
        amt = float(x["amount"])
        if dt >= cut7:
            if amt < 0: l7e  += abs(amt)
            else:       l7i  += abs(amt)
        if dt >= cut30:
            if amt < 0: l30e += abs(amt)
            else:       l30i += abs(amt)
    out["recent_windows"] = {
        "last_7_expense": round(l7e, 2), "last_7_income": round(l7i, 2),
        "last_30_expense": round(l30e, 2), "last_30_income": round(l30i, 2),
    }

    return out


# ========= Manage Panel Support =========
def get_transactions_for_path(level, cat, sub, ssub, sss, limit=50, allow_hidden: bool = False):
    """
    Return recent transactions that land on the given node.
    - level: 'category' | 'subcategory' | 'subsubcategory' | 'subsubsubcategory'
    - cat/sub/ssub/sss: labels; pass '' for unused deeper levels
    - allow_hidden: include rows whose category is in HIDDEN_CATEGORIES (default False)
    Output rows: [{id,date,amount,desc,merchant}]
    """
    # Fresh config
    ck, sm, ss, sss_map, _custom, omit_live, amount_rules, _src = _load_category_config()

    hidden_cats = _hidden_categories()  # <-- NEW

    # Use discovered base for manual
    statements_base = get_statements_base_dir()
    manual_file = statements_base / "manual_transactions.json"

    rows = []

    # --- Load from discovered sources (JSON + CSV) ---
    for raw in _iter_all_raw_transactions():
        # Basic normalize like generate_summary (but we don't need custom override here)
        # We still use categorize_transaction to place into categories
        if isinstance(raw, dict) and raw.get("pending", False):
            continue
        dt = _parse_any_date(raw.get("date") or raw.get("DATE") or raw.get("posted") or raw.get("POSTED") or raw.get("posting_date") or raw.get("authorized_date"))
        if not dt:
            continue
        try:
            raw_amt = float(raw.get("amount"))
        except Exception:
            raw_amt = 0.0
        desc = clean_description(
            raw.get("description") or raw.get("name", "") or raw.get("merchant_name", "") or raw.get("desc", "") or raw.get("original_description", "") or ""
        )

        category = categorize_transaction(desc, raw_amt, ck)
        is_return = _is_return(desc)
        if category == "Income":
            amt = abs(raw_amt)
        else:
            amt = abs(raw_amt) if is_return else -abs(raw_amt)

        row = {
            "date": dt.strftime("%m/%d/%Y"),
            "amount": amt,
            "category": category,
            "description": desc,
            "is_return": is_return,
        }
        rows.append(row)

    # --- Load manual entries ---
    rows.extend(load_manual_transactions(manual_file))

    # --- Legacy cleanup & fill missing category ---
    for r in rows:
        if r.get("category") == "Income" and r.get("subcategory") == "Paychecks":
            r.pop("subcategory", None)
            r.pop("sub_subcategory", None)
        if not r.get("category"):
            r["category"] = categorize_transaction(r.get("description", ""), float(r.get("amount", 0.0)), ck)
            if r["category"] == "Transfers":
                r["is_transfer"] = True

    # Deduplicate; keep rows (no offset matcher removal)
    rows = deduplicate(rows)

    # Global omit/skip rules
    kept = []
    for r in rows:
        desc = r["description"]
        cat_r = r["category"]
        amt = float(r["amount"])

        if _should_omit_tx(desc, amt, omit_live, amount_rules):
            continue
        # respect hidden categories unless explicitly allowed
        if (not allow_hidden) and (cat_r in hidden_cats):
            continue
        if cat_r == "Transfers" or (cat_r == "Venmo" and round(abs(amt), 2) != 200.00) or (cat_r == "Credit Card" and abs(amt) > 300):
            continue

        # Owner tagging (for Withdrawals)
        if cat_r == "Withdrawals":
            if "6466" in desc:
                r["owner"] = "Rachel"
            elif "3453" in desc or "8842" in desc:
                r["owner"] = "JL"
            else:
                r["owner"] = "Unknown"

        kept.append(r)

    # Sub/sub-sub/sub³ matching using keyword maps
    for r in kept:
        forced_subcat = r.get("subcategory")
        forced_subsub = r.get("sub_subcategory")
        if forced_subcat:
            r["subcategory"] = forced_subcat
            if forced_subsub:
                r["subsubcategory"] = forced_subsub
        else:
            sub_map = sm.get(r["category"], {})
            matched = False
            if sub_map:
                for sub_label, keywords in sub_map.items():
                    if any(_kw_hits(r["description"], k) for k in keywords):
                        r["subcategory"] = sub_label
                        matched = True
                        # sub-sub
                        subsub_map = ss.get(r["category"], {}).get(sub_label, {})
                        subsub_hit = None
                        for ssub_label, ssub_keys in subsub_map.items():
                            if any(_kw_hits(r["description"], k) for k in ssub_keys):
                                r["subsubcategory"] = ssub_label
                                subsub_hit = ssub_label
                                break
                        # sub³
                        if subsub_hit:
                            subsubsub_map = (
                                sss_map
                                .get(r["category"], {})
                                .get(sub_label, {})
                                .get(subsub_hit, {})
                            )
                            for sss_label, sss_keys in subsubsub_map.items():
                                if any(_kw_hits(r["description"], k) for k in sss_keys):
                                    r["subsubsubcategory"] = sss_label
                                    break
                        break
            if not matched and sub_map:
                r["subcategory"] = "🟡 Other/Uncategorized"

    # Sort newest-first
    kept.sort(key=lambda t: _parse_any_date(t.get("date") or "") or datetime.min, reverse=True)

    # --- Path filter ---
    def _norm(x):
        return (x or "").strip().lower()

    req_cat  = _norm(cat)
    req_sub  = _norm(sub)
    req_ssub = _norm(ssub)
    req_sss  = _norm(sss)

    def match_path(t):
        t_cat  = _norm(t.get("category"))
        # tolerate legacy aliases
        t_sub  = _norm(t.get("subcategory") or t.get("sub_category"))
        t_ssub = _norm(t.get("subsubcategory") or t.get("sub_subcategory"))
        t_sss  = _norm(t.get("subsubsubcategory") or t.get("sub_sub_subcategory"))

        if level == "category":
            return t_cat == req_cat
        if level == "subcategory":
            return t_cat == req_cat and t_sub == req_sub
        if level == "subsubcategory":
            return t_cat == req_cat and t_sub == req_sub and t_ssub == req_ssub
        if level == "subsubsubcategory":
            return t_cat == req_cat and t_sub == req_sub and t_ssub == req_ssub and t_sss == req_sss
        return False

    out = [t for t in kept if match_path(t)]
    out = out[: max(1, int(limit))]

    return [
        {
            "id": None,
            "date": t.get("date", ""),
            "amount": float(t.get("amount", 0.0)),  # UI sign
            "desc": t.get("description", ""),
            "merchant": None,
        }
        for t in out
    ]


if __name__ == "__main__":
    # Quick test: just print a summary count
    cfg = {
        "CATEGORY_KEYWORDS": category_keywords,
        "SUBCATEGORY_MAPS": subcategory_maps,
    }
    monthly = generate_summary(cfg["CATEGORY_KEYWORDS"], cfg["SUBCATEGORY_MAPS"])
    print(f"Built {len(monthly)} months of summaries")
    print("Hidden cats:", list_hidden_categories())
