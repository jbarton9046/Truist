import json
import re
import os
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

from truist import filter_config as fc

# =========================================================
# Helpers for config + paths
# =========================================================

def get_statements_base_dir() -> Path:
    """
    Discover the base folder that contains your bank statements + manual entries.
    Looks for ./statements or ./data/statements, else falls back to repo root / statements.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here / "statements",
        here / "data" / "statements",
        here.parent / "statements",
        Path.cwd() / "statements",
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    return candidates[0]

def _read_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

def _iter_all_raw_transactions():
    """
    Yield raw dict rows from all known sources under the statements base dir:
      - combined.json
      - any CSVs under ./statements (Chase/BoA/etc)
    """
    base = get_statements_base_dir()
    combined = base / "combined.json"
    if combined.exists():
        data = _read_json(combined, fallback=[])
        if isinstance(data, list):
            for r in data:
                if isinstance(r, dict):
                    yield r

    # CSVs
    for p in base.glob("**/*.csv"):
        try:
            with p.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if isinstance(row, dict):
                        yield row
        except Exception:
            continue

# =========================================================
# Normalization + categorization
# =========================================================

DATE_PATTERNS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%m/%d/%y",
    "%b %d, %Y",
]

def _parse_any_date(s):
    s = (s or "").strip()
    if not s:
        return None
    # Try substring YYYY-MM first
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            pass
    for pat in DATE_PATTERNS:
        try:
            return datetime.strptime(s, pat)
        except Exception:
            continue
    # Sometimes CSVs include time - strip time
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except Exception:
            pass
    return None

def _safe_date_key(s):
    dt = _parse_any_date(s)
    return dt or datetime.min

def clean_description(s):
    s = (s or "").strip()
    # Normalization rules
    s = re.sub(r"\s+", " ", s)
    s = s.replace("\u00A0", " ").strip()
    return s

def _kw_hits(desc, kw):
    """
    Case-insensitive simple substring match against cleaned desc.
    """
    if not kw:
        return False
    d = clean_description(desc).upper()
    return (kw or "").upper() in d

def _is_return(desc: str) -> bool:
    d = clean_description(desc).upper()
    # Basic return markers
    for token in ["REFUND", "REVERSAL", "RETURN", "CREDIT", "ADJ"]:
        if token in d:
            return True
    return False

def _expense_amount(amount: float, is_return: bool) -> float:
    """
    Convert UI-signed amount to a positive "spend" measure.
      - purchases => positive spend
      - returns   => negative spend
    """
    try:
        amt = float(amount or 0.0)
    except Exception:
        amt = 0.0
    if amt >= 0:  # income or return
        return -abs(amt) if is_return else 0.0  # income isn't "spend"
    # amt < 0 means purchase in UI sign
    return abs(amt)

def _should_omit_tx(desc: str, signed_amount: float, omit_keywords, amount_rules) -> bool:
    """
    Global omit rules by keyword and by amount windows.
    signed_amount is UI-signed (+income, -purchase, +return)
    """
    d = clean_description(desc)
    # Keyword rules
    for kw in (omit_keywords or []):
        if _kw_hits(d, kw):
            return True

    # Amount omit rules: list of dicts {min_abs, max_abs, sign}
    for rule in (amount_rules or []):
        try:
            min_abs = float(rule.get("min_abs", 0) or 0)
            max_abs = float(rule.get("max_abs", 0) or 0)
            sign    = str(rule.get("sign", "") or "").lower()   # 'pos' | 'neg' | ''
        except Exception:
            continue
        a = float(signed_amount or 0.0)
        ok_sign = (
            (sign == "pos" and a > 0) or
            (sign == "neg" and a < 0) or
            (sign == "")
        )
        if ok_sign and (abs(a) >= min_abs) and (max_abs <= 0 or abs(a) <= max_abs):
            return True

    return False

# =========================================================
# Config + hidden categories
# =========================================================

def _load_category_config():
    """
    Return tuple:
      (CATEGORY_KEYWORDS, SUBCATEGORY_MAPS, SUBSUBCATEGORY_MAPS, SUBSUBSUBCATEGORY_MAPS,
       CUSTOM_TRANSACTION_KEYWORDS, OMIT_KEYWORDS, AMOUNT_OMIT_RULES, CONFIG_SOURCE)
    """
    # defaults from code
    CATEGORY_KEYWORDS      = getattr(fc, "CATEGORY_KEYWORDS", {})
    SUBCATEGORY_MAPS       = getattr(fc, "SUBCATEGORY_MAPS", {})
    SUBSUBCATEGORY_MAPS    = getattr(fc, "SUBSUBCATEGORY_MAPS", {})
    SUBSUBSUBCATEGORY_MAPS = getattr(fc, "SUBSUBSUBCATEGORY_MAPS", {})
    CUSTOM_TX              = getattr(fc, "CUSTOM_TRANSACTION_KEYWORDS", {})
    OMIT_KEYWORDS          = getattr(fc, "OMIT_KEYWORDS", [])
    AMOUNT_OMIT_RULES      = getattr(fc, "AMOUNT_OMIT_RULES", [])
    src = "code"

    # overrides from CONFIG_DIR/filter_overrides.json if present
    cfg_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    ov_path = cfg_dir / "filter_overrides.json"
    if ov_path.exists():
        try:
            j = _read_json(ov_path, {})
            CATEGORY_KEYWORDS      = j.get("CATEGORY_KEYWORDS", CATEGORY_KEYWORDS)
            SUBCATEGORY_MAPS       = j.get("SUBCATEGORY_MAPS", SUBCATEGORY_MAPS)
            SUBSUBCATEGORY_MAPS    = j.get("SUBSUBCATEGORY_MAPS", SUBSUBCATEGORY_MAPS)
            SUBSUBSUBCATEGORY_MAPS = j.get("SUBSUBSUBCATEGORY_MAPS", SUBSUBSUBCATEGORY_MAPS)
            CUSTOM_TX              = j.get("CUSTOM_TRANSACTION_KEYWORDS", CUSTOM_TX)
            OMIT_KEYWORDS          = j.get("OMIT_KEYWORDS", OMIT_KEYWORDS)
            AMOUNT_OMIT_RULES      = j.get("AMOUNT_OMIT_RULES", AMOUNT_OMIT_RULES)
            src = "overrides"
        except Exception:
            pass

    return (
        CATEGORY_KEYWORDS,
        SUBCATEGORY_MAPS,
        SUBSUBCATEGORY_MAPS,
        SUBSUBSUBCATEGORY_MAPS,
        CUSTOM_TX,
        OMIT_KEYWORDS,
        AMOUNT_OMIT_RULES,
        src
    )

def _hidden_categories():
    """
    Union of default hidden categories + overrides (if any).
    We also ALWAYS include "Camera" as a sentinel hidden category so it never
    contributes to top-line expense totals (but it's still available in the
    drawer/inspector which requests allow_hidden=1).
    """
    defaults = set(getattr(fc, "HIDDEN_CATEGORIES", []) or [])
    cfg_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    ov_path = cfg_dir / "filter_overrides.json"
    hidden = set(defaults)
    if ov_path.exists():
        try:
            j = _read_json(ov_path, {})
            hidden.update(j.get("HIDDEN_CATEGORIES", []) or [])
        except Exception:
            pass
    return hidden.union({"Camera"})

def _is_path_hidden(cat, sub=None, ssub=None, sss=None):
    """
    Optional: allow hiding deeper paths by specifying exact labels in HIDDEN_PATHS override:
      ["Rent/Utilities / Water", "Groceries / Costco", ...]
    """
    cfg_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    ov_path = cfg_dir / "filter_overrides.json"
    paths = set()
    if ov_path.exists():
        try:
            j = _read_json(ov_path, {})
            for p in j.get("HIDDEN_PATHS", []) or []:
                if isinstance(p, str) and p.strip():
                    paths.add(p.strip().lower())
        except Exception:
            pass
    label = " / ".join([x for x in [cat, sub, ssub, sss] if x]).strip().lower()
    return (label in paths)

# =========================================================
# Transaction normalization from raw rows
# =========================================================

def _tx_from_raw(raw, category_keywords, custom_tx_keywords):
    """
    Normalize a raw row (json/csv row) into our app transaction format.
    Returns dict with:
      date, description, amount (UI-signed), category, subcategory?, is_return, expense_amount
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("pending", False):
        return None

    # Date
    dt = _parse_any_date(raw.get("date") or raw.get("DATE") or raw.get("posted") or raw.get("POSTED"))
    if not dt:
        return None

    # Amount
    try:
        raw_amt = float(raw.get("amount"))
    except Exception:
        raw_amt = 0.0

    # Desc
    desc = clean_description(
        raw.get("description") or raw.get("name", "") or raw.get("merchant_name", "") or raw.get("desc", "") or ""
    )

    # Categorize
    category = categorize_transaction(desc, raw_amt, category_keywords)

    # Custom overrides?
    for label, kws in (custom_tx_keywords or {}).items():
        if any(_kw_hits(desc, k) for k in (kws or [])):
            category = label
            break

    # Return?
    is_ret = _is_return(desc)

    # UI sign:
    #  - income => +abs
    #  - expense purchase => -abs
    #  - expense return   => +abs
    if category == "Income":
        amount = abs(raw_amt)
    else:
        amount = abs(raw_amt) if is_ret else -abs(raw_amt)

    tx = {
        "date": dt.strftime("%Y-%m-%d"),
        "description": desc,
        "amount": amount,
        "category": category,
        "is_return": is_ret,
        "expense_amount": _expense_amount(amount, is_ret),
    }
    return tx

# Basic categorizer (top-level only)
def categorize_transaction(desc, raw_amount, category_keywords):
    d = clean_description(desc)
    for cat, kws in (category_keywords or {}).items():
        for k in (kws or []):
            if _kw_hits(d, k):
                return cat
    # Default fallbacks
    if float(raw_amount or 0.0) > 0:
        return "Income"
    return "Other"

# =========================================================
# Manual entries
# =========================================================

def load_manual_transactions(manual_path: Path):
    """
    Load manual entries from manual_transactions.json if present.
    Each row should already include: date, description, amount (UI-signed), category,
    optional subcategory/sub_subcategory, is_return, expense_amount
    """
    if not manual_path.exists():
        return []
    data = _read_json(manual_path, [])
    if isinstance(data, list):
        out = []
        for r in data:
            if not isinstance(r, dict):
                continue
            # Keep date normalized
            dt = _parse_any_date(r.get("date", ""))
            if not dt:
                continue
            row = dict(r)
            row["date"] = dt.strftime("%Y-%m-%d")
            # Ensure expense_amount present
            if "expense_amount" not in row:
                is_ret = row.get("is_return", _is_return(row.get("description", "")))
                row["expense_amount"] = _expense_amount(row.get("amount", 0.0), is_ret)
            out.append(row)
        return out
    return []

# =========================================================
# Monthly summaries (top-line dashboard)
# =========================================================

CONFIG_SOURCE = "unknown"

def generate_summary(category_keywords, subcategory_maps):
    """
    Build monthly summaries using the *latest* config every call (so renames & deeper maps stay in sync).
    Returns offset spending within the same category via tx['expense_amount'].
    Hidden categories (including "Camera") do not contribute to top-line totals.
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

    hidden_cats = _hidden_categories()

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

        categorized_data = defaultdict(lambda: {"total": 0.0, "transactions": [], "subcategories": defaultdict(float)})

        for tx in month_tx:
            desc = tx["description"]
            cat = tx["category"]
            amt_signed = float(tx["amount"])               # UI sign (+income, -purchase, +return)
            exp_amt = float(tx.get("expense_amount", 0.0)) # +spend, -return

            # Global omit/skip rules (now includes amount rules)
            if _should_omit_tx(desc, amt_signed, omit_keywords_live, amount_omit_rules_live):
                continue
            # Transfers/Venmo/CreditCard sentinel filters, and **hidden categories**
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
                if cat == "Income":
                    cd["subcategories"][forced_subcat] += abs(amt_signed)
                else:
                    cd["subcategories"][forced_subcat] += exp_amt
                tx["subcategory"] = forced_subcat
                matched = True

                if forced_subsub:
                    if "subsubcategories" not in cd:
                        cd["subsubcategories"] = defaultdict(lambda: defaultdict(float))
                    if cat == "Income":
                        cd["subsubcategories"][forced_subcat][forced_subsub] += abs(amt_signed)
                    else:
                        cd["subsubcategories"][forced_subcat][forced_subsub] += exp_amt
                    tx["subsubcategory"] = forced_subsub

            # Keyword matching
            if not matched:
                sub_map = subcategory_maps.get(cat, {})
                if sub_map:
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

                            # Sub-sub-subcategory match
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
            # skip writing hidden categories to the per-category breakdown (they've
            # already been excluded from totals above, this just keeps the list tidy)
            if cat in hidden_cats:
                continue

            subcats = data["subcategories"]
            sub_map = subcategory_maps.get(cat, {})
            has_defined_subcats = bool(sub_map)

            if has_defined_subcats:
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

def _build_tree_from_categories(cat_blob):
    """
    Build a tree structure the UI expects from the per-category blob.
    """
    tree = []
    for cat, data in cat_blob.items():
        node = {
            "name": cat,
            "total": data.get("total", 0.0),
            "children": [],
        }
        for sub, v in (data.get("subcategories") or {}).items():
            node["children"].append({
                "name": sub,
                "total": v,
                "children": [],  # could add deeper if needed in UI
            })
        tree.append(node)
    return tree

def deduplicate(rows):
    """
    Drop duplicates on (date, amount, category, description) keeping the first.
    """
    seen = set()
    out = []
    for r in rows:
        key = (r.get("date"), float(r.get("amount") or 0.0), r.get("category"), r.get("description"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

# =========================================================
# Recent activity summary (dashboard cards)
# =========================================================

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
    Hidden categories are filtered from totals and recent lists.
    """
    # Always build from latest config
    (
        ck, sm, _ss, _sss, _custom, omit_live, amount_rules, _src
    ) = _load_category_config()

    hidden_cats = _hidden_categories()

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
        if (not include_income and name == "Income") or (name in hidden_cats):
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
        if cat == "Transfers" or (cat == "Venmo" and round(abs(amt), 2) != 200.00) or (cat == "Credit Card" and abs(amt) > 300) or (cat in hidden_cats):
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

# =========================================================
# Manage Panel Support (drawer)
# =========================================================

def get_transactions_for_path(level, cat, sub, ssub, sss, limit=50, allow_hidden=False):
    """
    Return recent transactions that land on the given node.
    - level: 'category' | 'subcategory' | 'subsubcategory' | 'subsubsubcategory'
    - cat/sub/ssub/sss: labels; pass '' for unused deeper levels
    Output rows: [{id,date,amount,desc,merchant}]
    If allow_hidden=True, hidden categories/paths are still returned (drawer use-case).
    """
    # Fresh config
    ck, sm, ss, sss_map, _custom, omit_live, amount_rules, _src = _load_category_config()

    # Manual entries base
    statements_base = get_statements_base_dir()
    manual_file = statements_base / "manual_transactions.json"

    rows = []

    # --- Load from discovered sources (JSON + CSV) ---
    for raw in _iter_all_raw_transactions():
        if isinstance(raw, dict) and raw.get("pending", False):
            continue
        dt = _parse_any_date(raw.get("date") or raw.get("DATE") or raw.get("posted") or raw.get("POSTED"))
        if not dt:
            continue
        try:
            raw_amt = float(raw.get("amount"))
        except Exception:
            raw_amt = 0.0
        desc = clean_description(
            raw.get("description") or raw.get("name", "") or raw.get("merchant_name", "") or raw.get("desc", "") or ""
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

    hidden_cats = _hidden_categories()

    # Global omit/skip rules
    kept = []
    for r in rows:
        desc = r["description"]
        cat_r = r["category"]
        amt = float(r["amount"])

        if _should_omit_tx(desc, amt, omit_live, amount_rules):
            continue
        if not allow_hidden and (cat_r in hidden_cats):
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

    # If this exact path is hidden and allow_hidden=False, empty it (extra safety)
    if (not allow_hidden) and (_is_path_hidden(cat, sub, ssub, sss) or (cat in hidden_cats)):
        out = []

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
