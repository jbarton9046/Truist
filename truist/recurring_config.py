# truist/recurring_config.py
# Central knobs for what counts as “recurring”.

# --------------------------
# ALWAYS treat these top-level categories as recurring (do NOT include Income here)
# --------------------------
RECURRING_CATEGORIES = [
    "Subscriptions",
    "Rent/Utilities",
    "Utilities",
    "Insurance",
    "Auto Insurance",
    "Phone",
    "Internet",
    "Mortgage",
    "Loan Payments",
    "Vehicles",
]

# --------------------------
# Vendor-priority recurring merchants (UPPERCASE; punctuation/digits don’t matter)
# --------------------------
RECURRING_MERCHANTS = [
    # Truck / auto loan
    "BRIDGECREST", "TUNDRA",

    # Electric (FPL)
    "FPL", "FLORIDA POWER AND LIGHT", "FLORIDA POWER & LIGHT",

    # Water (Sarasota via Paymentus — keep these)
    "SARASOTA CO UTILIT",
    "SARASOTA COUNTY UTILIT",
    "SARASOTA COUNTY UTILITIES",
    "PAYMENTUS",
    "Sarasota Water",  # ← canonical key so vendor_priority triggers

    # Phone (ONLY the device financing — DO NOT add generic “VERIZON” here)
    "VERIZON FINANCIA",   # $20.25 device financing

    # Internet
    "FRONTIER",

    # Subscriptions you want
    "PELOTON", "SPOTIFY",

    # Amazon Kids+/FreeTime
    "AMZNFREETIME",

    # Creative tools / AI subs
    "ADOBE",
    "OPENAI", "OPENAI CHATGPT", "OPENAI CHATGPT SU", "CHATGPT",

    # Xbox / Microsoft game subscriptions
    "MICROSOFT ULTIMATE",
    "MICROSOFT XBOX",
    "XBOX GAME PASS",
    "XBOX",

    # Straight Talk (split by amount below)
    "STRAIGHT TALK", "STRAIGHTTALK",
]

# --------------------------
# Description keywords that hint something is recurring
# --------------------------
RECURRING_KEYWORDS = [
    "AUTOPAY", "AUTO PAY", "AUTO-PAY",
    "MONTHLY",
    "SUBSCRIPTION", "MEMBERSHIP",
    "PREMIUM", "INSURANCE",
    "RENT",
    "INTERNET", "PHONE",
    "UTILITY", "UTILITIES",
    "RECURRING",
    "BILL PAY", "BILL PAYMENT",
]

# --------------------------
# Treat INCOME as recurring only if one of these appears
# --------------------------
RECURRING_INCOME_KEYWORDS = [
    "PARALON",
    "PR PAYMENT FINANCIAL SERVIC",
]

# --------------------------
# NEVER show these merchants
# --------------------------
DENY_MERCHANTS = [
    "YOUTUBE TV", "NETFLIX", "PRIME VIDEO",
    "BURTS GAS", "BURT'S GAS", "BURT S GAS",
    "ADVANCE AUTO", "VIOC", "VALVOLINE",
    "HARD ROCK BET", "HARDROCKBET", "HARDROCK",
    "VIATRUSTLY", "VIA TRUSTLY", "TRUSTLY",
    "WALMART", "WAL-MART", "WM SUPERCENTER", "WMT.COM",
    "ARLYN ROSS CARWASH",
    "SARASOTA PARK METE",

    # Never include these “PU” variants
    "SARASOTA COUNTY PU FL", "SARASOTA COUNTY PU", "SARASOTA CO PU",

    "MOBILE DEPOSIT", "MOBILE CHECK DEPOSIT", "MOB DEPOSIT",
    "VERIZON WRLS", "VERIZON WIRELESS",
    "AMERICAN EXPRESS", "AMEX", "AMEX EPAYMENT",
    "CAPITAL ONE", "CAPITALONE",
    "CITI", "CITICARD", "CITICARDS", "CITI CARD",
    "DISCOVER",
    "CHASE", "CHASE CREDIT", "CHASE CARD",
    "BARCLAYS", "BARCLAYCARD",
    "SYNCHRONY", "SYNCB",
    "BANKCARD", "CREDIT CARD",
]

# --------------------------
# Merchants allowed up to TWO charges per month (biweekly)
# --------------------------
TWO_PER_MONTH_MERCHANTS = ["STRAIGHT TALK", "STRAIGHTTALK"]

# --------------------------
# Any tx with one of these SUBCATEGORY names will NEVER appear
# --------------------------
DENY_SUBCATEGORIES = ["Gas"]

# --------------------------
# Split a vendor into separate streams by AMOUNT
# --------------------------
SPLIT_VENDOR_BY_AMOUNT = [
    "STRAIGHT TALK", "STRAIGHTTALK",
    "VERIZON FINANCIA",
]

# --------------------------
# Pretty labels for specific (merchant, amount) combos
# --------------------------
AMOUNT_LABELS = {
    "STRAIGHT TALK": {
        47.84: "STRAIGHT TALK (JL Line)",
        50.16: "STRAIGHT TALK (Rachel Line)",
    },
    "STRAIGHTTALK": {
        47.84: "STRAIGHT TALK (JL Line)",
        50.16: "STRAIGHT TALK (Rachel Line)",
    },
    "VERIZON FINANCIA": {
        20.25: "VERIZON DEVICE FINANCE",
    },
}

# --------------------------
# Variance tolerance
# --------------------------
VARIANCE_TOLERANCE = {
    "PR PAYMENT FINANCIAL SERVIC": 0.10,
}

# --------------------------
# Biweekly max per month
# --------------------------
BIWEEKLY_MAX_PER_MONTH = {
    "STRAIGHT TALK": 2,
    "STRAIGHTTALK": 2,
    "PR PAYMENT FINANCIAL SERVIC": 3,
    "PARALON": 3,
}

# --------------------------
# Allow single occurrences
# --------------------------
ALLOW_SINGLE_OCCURRENCES = [
    "VERIZON FINANCIA",
    "ADOBE",
    "OPENAI", "OPENAI CHATGPT", "OPENAI CHATGPT SU", "CHATGPT",
    "MICROSOFT", "MICROSOFT ULTIMATE",
    "XBOX", "XBOX GAME PASS",
    "STRAIGHT TALK", "STRAIGHTTALK",

    # >>> Water bill (single sighting still allowed)
    "SARASOTA CO UTILIT",
    "SARASOTA COUNTY UTILIT",
    "SARASOTA COUNTY UTILITIES",
    "PAYMENTUS",
    "Sarasota Water",   # canonical name used after alias collapsing
]

# --------------------------
# Canonical vendor aliases (collapse variants into one stream label)
# --------------------------
CANONICAL_VENDOR_ALIASES = {
    "Sarasota Water": [
        "SARASOTA CO UTILIT",
        "SARASOTA COUNTY UTILIT",
        "SARASOTA COUNTY UTILITIES",
        "PAYMENTUS",
    ]
}

# --------------------------
# Grace period for missed recurrences
# --------------------------
MISSED_GRACE_DAYS = 7

# --------------------------
# Variable income config
# --------------------------
VARIABLE_INCOME = {
    "ENABLED": True,
    "WINDOW_DAYS": 120,
    "MIN_WEEKS": 3,
    "TRIM_PCT": 0.20,
    "INCLUDE_MERCHANTS": ["MOBILE DEPOSIT", "MOBILE CHECK DEPOSIT", "MOB DEPOSIT"],
    "INCLUDE_KEYWORDS": ["TIP", "TIPS", "CASH TIP", "CASH TIPS"],
    "INCLUDE_SUBCATEGORIES": ["TIPS", "CASH TIPS", "CASH"],
    "EXCLUDE_MERCHANTS": [],
}
