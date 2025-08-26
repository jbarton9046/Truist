import os
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

# Load environment variables
load_dotenv()
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")
PLAID_ENV = os.getenv("PLAID_ENV")

# Paths
BASE_DIR = Path(__file__).resolve().parent
STATEMENTS_DIR = BASE_DIR / "statements"
ACCESS_TOKEN_PATH = STATEMENTS_DIR / "access_token.json"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Logging
logging.basicConfig(
    filename=LOGS_DIR / "plaid_fetch.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

# Validate env vars
if not all([PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV]):
    raise EnvironmentError("❌ One or more Plaid environment variables are missing.")

# Setup Plaid client
PLAID_ENV_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com"
}
if PLAID_ENV not in PLAID_ENV_HOSTS:
    raise ValueError(f"Invalid PLAID_ENV value: {PLAID_ENV}")

configuration = Configuration(
    host=PLAID_ENV_HOSTS[PLAID_ENV],
    api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET}
)
api_client = ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

# Load or create access token
def load_access_token():
    if ACCESS_TOKEN_PATH.exists():
        with open(ACCESS_TOKEN_PATH, "r") as f:
            return json.load(f).get("access_token")
    public_token = input("Enter public_token from Plaid Link: ").strip()
    exch = client.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token=public_token))
    token = exch.access_token
    with open(ACCESS_TOKEN_PATH, "w") as f:
        json.dump({"access_token": token}, f)
    return token

# Create link token if needed
def create_link_token():
    request = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="Barton Tech",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="unique_user_id_123")
    )
    response = client.link_token_create(request)
    return response.link_token

def main():
    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Number of days back to fetch", default=2)
    args = parser.parse_args()

    today = date.today()
    start_date = (
        datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else
        today - timedelta(days=args.days)
    )
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today

    print(f"📆 Fetching transactions from {start_date} to {end_date}...")

    access_token = load_access_token()
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
                options=options
            )
            response = client.transactions_get(request)
            transactions = []
            for t in response.transactions:
                tx = t.to_dict()
                tx["description"] = tx.get("name") or tx.get("merchant_name") or ""
                transactions.append(tx)

            all_fetched.extend(transactions)
            print(f"📦 Fetched {len(transactions)} (Total so far: {len(all_fetched)})")

            if len(all_fetched) >= response.total_transactions:
                break
            offset += len(transactions)

    except ApiException as e:
        error_msg = f"❌ API Error: {e}"
        print(error_msg)
        logging.error(error_msg)
        return

    # Save all transactions as a fresh Plaid JSON dump
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = STATEMENTS_DIR / f"plaid_{timestamp}.json"
    with open(filename, "w") as f:
        json.dump(all_fetched, f, indent=2, default=str)


    print(f"✅ Saved {len(all_fetched)} transactions to {filename.name}")
    logging.info(f"Saved {len(all_fetched)} transactions to {filename.name}")
    
    # ✅ Update all_transactions.json (master transaction file)
    master_file = STATEMENTS_DIR / "all_transactions.json"

    # Load existing master transactions
    if master_file.exists():
        with open(master_file, "r") as f:
            master_data = json.load(f)
    else:
        master_data = []

    # Filter out pending transactions from fetched data
    cleared_transactions = [
        txn for txn in all_fetched if not txn.get("pending")
    ]


    # Deduplicate: compare by (name, amount, date)
    existing_keys = {
        (txn["name"], txn["amount"], txn["date"]) for txn in master_data
    }
    new_txns = [
        txn for txn in cleared_transactions
        if (txn["name"], txn["amount"], txn["date"]) not in existing_keys
    ]

    # Append new transactions to master and save
    updated_data = master_data + new_txns
    with open(master_file, "w") as f:
        json.dump(updated_data, f, indent=2, default=str)


    print(f"✅ Master file updated: {len(new_txns)} new transactions added to all_transactions.json")
    logging.info(f"{len(new_txns)} new transactions added to all_transactions.json")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "link":
        print(f"✅ Your new link_token:\n{create_link_token()}")
    else:
        main()
