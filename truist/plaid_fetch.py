import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, date, timedelta

from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.configuration import Configuration
from plaid.api_client import ApiClient
from plaid.exceptions import ApiException

from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.country_code import CountryCode
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products


# --------------------
# Env & configuration
# --------------------
load_dotenv()
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET    = os.getenv("PLAID_SECRET")
PLAID_ENV       = os.getenv("PLAID_ENV")

# Prefer /var/data/plaid in prod; fall back to repo-local for dev
OUT_DIR = Path(os.environ.get("PLAID_DIR") or "/var/data/plaid")
if not OUT_DIR.exists():
    OUT_DIR = Path(__file__).resolve().parent / "statements"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Legacy/local token path
ACCESS_TOKEN_PATH = OUT_DIR / "access_token.json"

LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOGS_DIR / "plaid_fetch.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

PLAID_ENV_HOSTS = {
    "sandbox":     "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production":  "https://production.plaid.com",
}


def _die(msg: str, code: int = 1):
    print(f"❌ {msg}", file=sys.stderr)
    logging.error(msg)
    sys.exit(code)


# Validate env vars early
if not all([PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV]):
    _die("One or more Plaid env vars are missing (PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV).")
if PLAID_ENV not in PLAID_ENV_HOSTS:
    _die(f"Invalid PLAID_ENV: {PLAID_ENV}")

configuration = Configuration(
    host=PLAID_ENV_HOSTS[PLAID_ENV],
    api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
)
api_client = ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)


# --------------------
# Small helpers
# --------------------
def _to_tx_list(obj):
    """Normalize any JSON into list[dict] of transactions."""
    if isinstance(obj, dict):
        arr = obj.get("transactions") or obj.get("items") or []
    elif isinstance(obj, list):
        arr = obj
    else:
        arr = []
    return [t for t in arr if isinstance(t, dict)]


def _key(tx: dict):
    """Stable dedupe key: prefer Plaid id; else name/amount/date."""
    tid = tx.get("transaction_id")
    return ("id", tid) if tid else ("nad", tx.get("name"), tx.get("amount"), tx.get("date"))


# --------------------
# Token utilities
# --------------------
def _exchange_public_for_access(public_token: str) -> str:
    resp  = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    token = resp.access_token
    try:
        ACCESS_TOKEN_PATH.write_text(json.dumps({"access_token": token}), encoding="utf-8")
    except Exception as e:
        logging.warning(f"Could not write {ACCESS_TOKEN_PATH}: {e}")
    return token


def load_access_token(noninteractive: bool | None = None) -> str:
    """
    Resolution order:
      1) env PLAID_ACCESS_TOKEN
      2) CONFIG_DIR (/var/data/config) -> plaid_access_token.json / access_token.json
      3) STATEMENT_DIR (/var/data/statements) -> access_token.json  (legacy)
      4) OUT_DIR (/var/data/plaid or repo) -> access_token.json     (legacy)
      5) interactive prompt (only if allowed)
    """
    if noninteractive is None:
        noninteractive = (os.environ.get("NONINTERACTIVE", "1") == "1") or (not sys.stdin.isatty())

    tok = os.environ.get("PLAID_ACCESS_TOKEN")
    if tok:
        return tok.strip()

    # Config dir
    cfg_dir = Path(os.environ.get("CONFIG_DIR", "/var/data/config"))
    for cand in (cfg_dir / "plaid_access_token.json", cfg_dir / "access_token.json"):
        try:
            if cand.exists():
                j = json.loads(cand.read_text(encoding="utf-8"))
                tok = (j.get("access_token") or j.get("PLAID_ACCESS_TOKEN") or "").strip()
                if tok:
                    return tok
        except Exception:
            pass

    # Legacy: STATEMENT_DIR (your earlier token lived here)
    stmt_dir = Path(os.environ.get("STATEMENT_DIR", "/var/data/statements"))
    cand = stmt_dir / "access_token.json"
    try:
        if cand.exists():
            j = json.loads(cand.read_text(encoding="utf-8"))
            tok = (j.get("access_token") or "").strip()
            if tok:
                return tok
    except Exception:
        pass

    # Legacy: OUT_DIR
    try:
        if ACCESS_TOKEN_PATH.exists():
            j = json.loads(ACCESS_TOKEN_PATH.read_text(encoding="utf-8"))
            tok = (j.get("access_token") or "").strip()
            if tok:
                return tok
    except Exception:
        pass

    if noninteractive:
        raise RuntimeError(
            "Missing Plaid access token. Use PLAID_ACCESS_TOKEN env or "
            "put {'access_token':'...'} in /var/data/config/plaid_access_token.json "
            "or STATEMENT_DIR/access_token.json."
        )

    public_token = input("Enter public_token from Plaid Link: ").strip()
    if not public_token:
        raise RuntimeError("No public_token provided.")
    return _exchange_public_for_access(public_token)


# --------------------
# Link token creator
# --------------------
def create_link_token() -> str:
    request = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="Barton Tech",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="unique_user_id_123"),
    )
    response = client.link_token_create(request)
    return response.link_token


# --------------------
# Main fetch logic
# --------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=2, help="Number of days back to fetch")
    parser.add_argument("--noninteractive", action="store_true", help="Force non-interactive mode")
    args = parser.parse_args()

    today = date.today()
    start_date = datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else today - timedelta(days=args.days)
    end_date   = datetime.strptime(args.end,   "%Y-%m-%d").date() if args.end   else today

    print(f"📆 Fetching transactions from {start_date} to {end_date}...")
    logging.info(f"Fetching transactions from {start_date} to {end_date}")

    try:
        ni_default   = (os.environ.get("NONINTERACTIVE", "1") == "1") or (not sys.stdin.isatty())
        access_token = load_access_token(noninteractive=args.noninteractive or ni_default)
    except Exception as e:
        _die(str(e))

    all_fetched: list[dict] = []
    offset, count = 0, 100

    try:
        while True:
            options = TransactionsGetRequestOptions(count=count, offset=offset)
            request = TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=options,
            )
            response = client.transactions_get(request)

            batch = []
            for t in response.transactions:
                tx = t.to_dict()
                tx["description"] = tx.get("name") or tx.get("merchant_name") or ""
                batch.append(tx)

            all_fetched.extend(batch)
            print(f"📦 Fetched {len(batch)} (Total: {len(all_fetched)}/{response.total_transactions})")

            if len(all_fetched) >= response.total_transactions:
                break
            offset += len(batch)
    except ApiException as e:
        _die(f"API Error: {e}")

    # Save raw dump (list is fine; readers handle both shapes)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dump_path = OUT_DIR / f"plaid_{timestamp}.json"
    dump_path.write_text(json.dumps(all_fetched, indent=2, default=str), encoding="utf-8")
    print(f"✅ Saved {len(all_fetched)} transactions to {dump_path.name}")
    logging.info(f"Saved {len(all_fetched)} transactions to {dump_path}")

    # Load existing master robustly
    master_file = OUT_DIR / "all_transactions.json"
    if master_file.exists():
        try:
            raw     = json.loads(master_file.read_text(encoding="utf-8"))
            master  = _to_tx_list(raw)
        except Exception:
            master = []
    else:
        master = []

    # Only cleared; dedupe vs existing cleared
    fetched_cleared = [t for t in all_fetched if not t.get("pending", False)]
    existing_keys   = {_key(t) for t in master if not t.get("pending", False)}
    new_txns        = [t for t in fetched_cleared if _key(t) not in existing_keys]

    updated = master + new_txns
    # newest first
    updated.sort(key=lambda t: (t.get("date") or "", str(t.get("transaction_id") or "")), reverse=True)

    # Write master back in a stable dict shape
    master_file.write_text(json.dumps({"transactions": updated}, indent=2, default=str), encoding="utf-8")
    print(f"✅ Master file updated: {len(new_txns)} new cleared transactions added to all_transactions.json")
    logging.info(f"{len(new_txns)} new transactions added to {master_file}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "link":
        print(f"✅ Your new link_token:\n{create_link_token()}")
    else:
        main()
