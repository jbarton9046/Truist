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
PLAID_SECRET = os.getenv("PLAID_SECRET")
PLAID_ENV = os.getenv("PLAID_ENV")

# Output locations
# Prefer a production-friendly directory first, then fall back for local dev.
OUT_DIR = Path(os.environ.get("PLAID_DIR") or "/var/data/plaid")
if not OUT_DIR.exists():
    # local fallback inside repo if /var/data/plaid is not available
    OUT_DIR = Path(__file__).resolve().parent / "statements"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Where to store/read a token on disk (legacy path kept for compat)
ACCESS_TOKEN_PATH = OUT_DIR / "access_token.json"

LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOGS_DIR / "plaid_fetch.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

PLAID_ENV_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}

def _die(msg: str, code: int = 1):
    print(f"❌ {msg}", file=sys.stderr)
    logging.error(msg)
    sys.exit(code)

# Validate env vars early
if not all([PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV]):
    _die("One or more Plaid environment variables are missing (PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV).")

if PLAID_ENV not in PLAID_ENV_HOSTS:
    _die(f"Invalid PLAID_ENV value: {PLAID_ENV}")

configuration = Configuration(
    host=PLAID_ENV_HOSTS[PLAID_ENV],
    api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET}
)
api_client = ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

# --------------------
# Token utilities
# --------------------
def _exchange_public_for_access(public_token: str) -> str:
    """Exchange a Plaid Link public_token for a long-lived access_token."""
    resp = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    token = resp.access_token
    # Persist where our parser & future runs can find it
    try:
        ACCESS_TOKEN_PATH.write_text(json.dumps({"access_token": token}), encoding="utf-8")
    except Exception as e:
        logging.warning(f"Could not write {ACCESS_TOKEN_PATH}: {e}")
    return token

def load_access_token(noninteractive: bool | None = None) -> str:
    """
    Load Plaid access token without interactive prompts in server environments.

    Resolution order:
      1) env PLAID_ACCESS_TOKEN
      2) JSON files under CONFIG_DIR or /var/data/config:
         - plaid_access_token.json
         - access_token.json
      3) local file OUT_DIR/access_token.json (legacy compat)
      4) interactive prompt (only if noninteractive=False)
    """
    if noninteractive is None:
        # Default to headless on servers (no TTY) or when NONINTERACTIVE=1.
        noninteractive = (os.environ.get("NONINTERACTIVE", "1") == "1") or (not sys.stdin.isatty())

    # 1) Environment
    tok = os.environ.get("PLAID_ACCESS_TOKEN")
    if tok:
        return tok.strip()

    # 2) Config dir files
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

    # 3) Legacy file in OUT_DIR
    try:
        if ACCESS_TOKEN_PATH.exists():
            j = json.loads(ACCESS_TOKEN_PATH.read_text(encoding="utf-8"))
            tok = (j.get("access_token") or "").strip()
            if tok:
                return tok
    except Exception:
        pass

    # 4) Interactive (local dev only)
    if noninteractive:
        raise RuntimeError(
            "Missing Plaid access token. Provide PLAID_ACCESS_TOKEN env var or "
            "place {'access_token':'...'} in CONFIG_DIR/plaid_access_token.json "
            "or in /var/data/config/plaid_access_token.json."
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
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today

    print(f"📆 Fetching transactions from {start_date} to {end_date}...")
    logging.info(f"Fetching transactions from {start_date} to {end_date}")

    try:
        # Enforce headless on servers
        ni_default = (os.environ.get("NONINTERACTIVE", "1") == "1") or (not sys.stdin.isatty())
        access_token = load_access_token(noninteractive=args.noninteractive or ni_default)
    except Exception as e:
        _die(str(e))

    all_fetched = []
    offset = 0
    count = 100

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

            transactions = []
            for t in response.transactions:
                tx = t.to_dict()
                tx["description"] = tx.get("name") or tx.get("merchant_name") or ""
                transactions.append(tx)

            all_fetched.extend(transactions)
            print(f"📦 Fetched {len(transactions)} (Total: {len(all_fetched)}/{response.total_transactions})")

            if len(all_fetched) >= response.total_transactions:
                break
            offset += len(transactions)
    except ApiException as e:
        _die(f"API Error: {e}")

    # Save fresh dump
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dump_path = OUT_DIR / f"plaid_{timestamp}.json"
    dump_path.write_text(json.dumps(all_fetched, indent=2, default=str), encoding="utf-8")
    print(f"✅ Saved {len(all_fetched)} transactions to {dump_path.name}")
    logging.info(f"Saved {len(all_fetched)} transactions to {dump_path}")

    # Update or create master file (cleared-only, dedup by (name, amount, date))
    master_file = OUT_DIR / "all_transactions.json"
    if master_file.exists():
        master_data = json.loads(master_file.read_text(encoding="utf-8"))
    else:
        master_data = []

    cleared = [txn for txn in all_fetched if not txn.get("pending")]
    existing_keys = {(txn.get("name"), txn.get("amount"), txn.get("date")) for txn in master_data}
    new_txns = [txn for txn in cleared if (txn.get("name"), txn.get("amount"), txn.get("date")) not in existing_keys]
    updated = master_data + new_txns
    master_file.write_text(json.dumps(updated, indent=2, default=str), encoding="utf-8")

    print(f"✅ Master file updated: {len(new_txns)} new cleared transactions added to all_transactions.json")
    logging.info(f"{len(new_txns)} new transactions added to {master_file}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "link":
        print(f"✅ Your new link_token:\n{create_link_token()}")
    else:
        main()
