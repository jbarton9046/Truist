import os
import csv
import json
import glob
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
import filter_config as fc

load_dotenv()

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")
PLAID_ENV = os.getenv("PLAID_ENV")

def clean_description(desc):
    desc = desc.strip().upper()
    desc = desc.replace('-', '')
    return re.sub(r'\s+', ' ', desc)

def deduplicate(transactions):
    seen = set()
    unique = []
    for tx in transactions:
        key = (tx["date"], round(tx["amount"], 2), tx["category"])
        
        if key not in seen:
            seen.add(key)
            unique.append(tx)
    return unique

def load_plaid_json(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    transactions = []
    for tx in data:
        if tx.get("pending", False):
            continue

        try:
            date_obj = datetime.strptime(tx.get("date", ""), "%Y-%m-%d")
        except:
            continue

        raw_amt = float(tx.get("amount", 0))
        raw_desc = tx.get("name", "") or tx.get("merchant_name", "")
        desc = clean_description(raw_desc)

        category = categorize_transaction(desc, raw_amt, fc.CATEGORY_KEYWORDS)

        is_return = "RETURN" in desc and category == "Online Shopping"
        is_credit = "CREDIT" in desc and category in ["Bet", "Entertainment", "Online Shopping"]

        if category in ("Paychecks", "Cash Income") or is_return or is_credit:
            amt = abs(raw_amt)
        else:
            amt = -abs(raw_amt)

        transactions.append({
            "date": date_obj.strftime("%m/%d/%Y"),
            "amount": amt,
            "category": category,
            "description": desc
        })

    return transactions

def load_manual_transactions(file_path="manual_transactions.json"):
    transactions = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                tx = json.loads(line)
                amt = float(tx["amount"])
                tx["amount"] = amt
                tx["date"] = tx["date"]
                tx["description"] = clean_description(tx["description"])
                transactions.append(tx)
    except FileNotFoundError:
        print("ℹ️ No manual transactions found.")
    return transactions

def categorize_transaction(desc, amount, category_keywords):
    amt_rounded = round(amount, 2)

    if "CHECK" in desc:
        if amt_rounded == 264.00:
            return "Fees"
        if amt_rounded == 2500.00:
            return "Rent/Utilities"

    if "TRANSFER" in desc:
        return "Transfers"

    if "COSTCO" in desc and round(abs(amount), 2) == 65.00:
        return "Subscriptions"

    if "HARD ROCK" in desc and "CREDIT" in desc:
        return "Bet"

    if "WALMART" in desc and amt_rounded == 212.93:
        return "Phone"

    if "SARASOTA COUNTY PU" in desc:
        return "Rent/Utilities"

    for category, keywords in category_keywords.items():
        for keyword in keywords:
            if keyword in desc:
                return category

    return "Miscellaneous"

def is_interest_income(desc, amt):
    return "INTEREST PAYMENT" in desc.upper() and amt > 0

def main():
    folder = Path(__file__).resolve().parent / "statements"
    all_tx = []

    for file in folder.glob("*.json"):
        if file.name.startswith("plaid_") or file.name.startswith("transactions_") or file.name == "all_transactions.json":
            try:
                all_tx.extend(load_plaid_json(file))
            except json.JSONDecodeError as e:
                print(f"❌ Skipped invalid JSON file: {file.name} ({e})")

    manual_file = Path(__file__).resolve().parent / "web_app" / "manual_transactions.json"
    manual_tx = load_manual_transactions(manual_file)
    all_tx.extend(manual_tx)

    if not all_tx:
        print("ℹ️ No transactions found.")
        return

    all_tx = deduplicate(all_tx)
    all_tx.sort(key=lambda tx: datetime.strptime(tx["date"], "%m/%d/%Y"))

    offset_tracker = defaultdict(list)
    for tx in all_tx:
        key = (round(abs(tx['amount']), 2), clean_description(tx['description']))
        offset_tracker[key].append(tx)

    filtered_tx = []
    for group in offset_tracker.values():
        credits = [tx for tx in group if tx['amount'] > 0]
        debits = [tx for tx in group if tx['amount'] < 0]
        while credits and debits:
            credits.pop()
            debits.pop()
        filtered_tx.extend(credits + debits)

    months = defaultdict(list)
    for tx in filtered_tx:
        dt = datetime.strptime(tx["date"], "%m/%d/%Y")
        month_key = dt.strftime("%Y-%m")
        months[month_key].append(tx)

    for month in sorted(months.keys()):
        print(f"\n========================")
        print(f"🗵️ {month} Summary")
        print(f"========================\n")

        month_tx = months[month]

        income_total = 0
        expense_total = 0
        expense_by_category = defaultdict(float)
        categorized = defaultdict(list)

        subcategory_data = {
            cat: {"subtotals": defaultdict(float), "subsubtotals": defaultdict(lambda: defaultdict(float)), "unmatched": []}
            for cat in fc.SUBCATEGORY_MAPS
        }

        for tx in month_tx:
            cat = tx["category"]
            amt = tx["amount"]
            desc = tx.get("description", "")

            if any(keyword in desc for keyword in fc.OMIT_KEYWORDS):
                continue
            if cat in ["Transfers", "Camera"]:
                continue
            if cat == "Venmo" and round(abs(amt), 2) != 200.00:
                continue
            if cat == "Credit Card" and abs(amt) > 300:
                continue

            if is_interest_income(desc, amt):
                income_total += amt
                continue
            
            tx["amount"] = abs(amt) if cat == "Income" else -abs(amt)
            amt = tx["amount"]  # update amt to reflect normalized value

            if cat == "Income":
                income_total += abs(amt)
                categorized[cat].append(tx)
            else:
                expense_total += abs(amt)
                expense_by_category[cat] += abs(amt)
                categorized[cat].append(tx)

                if cat in fc.SUBCATEGORY_MAPS:
                    sub_map = fc.SUBCATEGORY_MAPS[cat]
                    matched = False
                    for subcat_label, keywords in sub_map.items():
                        if any(keyword in desc.upper() for keyword in keywords):
                            subcategory_data[cat]["subtotals"][subcat_label] += abs(amt)
                            tx["subcategory"] = subcat_label
                            matched = True
                            break
                    if not matched:
                        subcategory_data[cat]["subtotals"]["🟡 Other/Uncategorized"] += abs(amt)
                        tx["subcategory"] = "🟡 Other/Uncategorized"
                        subcategory_data[cat]["unmatched"].append(tx)

                    # Subsubcategory matching
                    if cat in fc.SUBSUBCATEGORY_MAPS and tx.get("subcategory"):
                        subsub_map = fc.SUBSUBCATEGORY_MAPS.get(cat, {}).get(tx["subcategory"], {})
                        for subsub_label, subsub_keywords in subsub_map.items():
                            if any(subsub_kw in desc.upper() for subsub_kw in subsub_keywords):
                                subcategory_data[cat]["subsubtotals"][tx["subcategory"]][subsub_label] += abs(amt)
                                tx["subsubcategory"] = subsub_label
                                break

        print("📊 Category Summary:")
        for cat, txns in sorted(categorized.items(), key=lambda x: abs(sum(t["amount"] for t in x[1])), reverse=True):
            total = sum(t["amount"] for t in txns)
            print(f"{cat}: ${total:.2f}")
            for t in txns:
                subcat = t.get("subcategory")
                subsubcat = t.get("subsubcategory")
                label = f" [{subcat}]"
                if subsubcat:
                    label += f" > {subsubcat}"
                print(f"  {t['date']}: {t.get('description', 'N/A')} - ${t['amount']:.2f}{label}")
            print()

        net = income_total - expense_total

        print("\n💸 Expense Breakdown:")
        sorted_expenses = sorted(expense_by_category.items(), key=lambda x: -x[1])
        for category, total in sorted_expenses:
            print(f"  {category}: ${total:.2f}")

        def print_breakdown(title, subtotals, subsubtotals, unmatched):
            if subtotals:
                total = sum(subtotals.values())
                print(f"\n{title} ${total:.2f}:")
                for label, subtotal in sorted(subtotals.items(), key=lambda x: -x[1]):
                    print(f"  {label}: ${subtotal:.2f}")
                if subsubtotals:
                    for subcat, subsubs in subsubtotals.items():
                        for subsub, subsub_amt in sorted(subsubs.items(), key=lambda x: -x[1]):
                            print(f"    > {subcat} > {subsub}: ${subsub_amt:.2f}")
                if unmatched:
                    print("    👀 Unmatched:")
                    for tx in unmatched:
                        print(f"     • {tx['date']}: {tx['description']} - ${abs(tx['amount']):.2f} [{tx.get('subcategory', '')}]")

        for category, _ in sorted_expenses:
            if category in fc.SUBCATEGORY_MAPS:
                subtotals = subcategory_data[category]["subtotals"]
                subsubtotals = subcategory_data[category]["subsubtotals"]
                unmatched = subcategory_data[category]["unmatched"]
                if sum(subtotals.values()) > 0:
                    print_breakdown(f"{category} Breakdown", subtotals, subsubtotals, unmatched)

        print("-" * 30)
        print(f"💰 Total Income:      ${income_total:.2f}")
        print(f"💸 Total Expenses:    ${expense_total:.2f}")
        print(f"🔢 Net Cash Flow:     ${net:.2f}")
        print("-" * 30)

if __name__ == "__main__":
    main()
