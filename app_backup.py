import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'truist')))

import filter_config as fc
from parser_web import generate_summary
from flask import Flask, render_template, request, redirect, url_for
import json
from datetime import datetime

category_keywords = fc.CATEGORY_KEYWORDS
subcategory_maps = fc.SUBCATEGORY_MAPS

app = Flask(__name__)

# Load manual cash payments
def load_manual_transactions():
    try:
        with open("manual_transactions.json") as f:
            return [json.loads(line) for line in f.readlines()]
    except FileNotFoundError:
        return []

# Save a new manual transaction
def save_manual_transaction(tx):
    with open("manual_transactions.json", "a") as f:
        f.write(json.dumps(tx) + "\n")

@app.route("/")
def index():
    summary_data = generate_summary(category_keywords, subcategory_maps)
    transactions = load_manual_transactions()

    # Optional: Totals from manual entries
    income_total = sum(t["amount"] for t in transactions if t["amount"] > 0)
    expense_total = sum(-t["amount"] for t in transactions if t["amount"] < 0)

    return render_template("index.html",
                           summary_data=summary_data,
                           transactions=transactions,
                           income=income_total,
                           expense=expense_total)
    
@app.route("/add-income", methods=["GET"])
def add_income():
    summary_data = generate_summary(category_keywords, subcategory_maps)
    return render_template("add_income.html", summary_data=summary_data)


@app.route("/add", methods=["GET", "POST"])
def add_cash():
    if request.method == "POST":
        raw_amount = abs(float(request.form["amount"]))
        tx_type = request.form["type"]
        amount = raw_amount if tx_type == "income" else -raw_amount

        tx = {
            "date": request.form["date"],
            "description": request.form["description"].upper(),
            "amount": amount,
            "category": request.form["category"]
        }

        save_manual_transaction(tx)
        return redirect(url_for("index"))

    summary_data = generate_summary(category_keywords, subcategory_maps)
    return render_template("add_cash.html", summary_data=summary_data)

if __name__ == "__main__":
    app.run(debug=True)