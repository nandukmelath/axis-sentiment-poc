"""Seed a SYNTHETIC bank/CRM master so the join + RM cockpit run end-to-end today,
before any real Axis CRM extract exists. In production these tables are pointed at a
read-only CRM extract; the handle->customer bridge is populated from verified handles.

Maps a few seed/thread authors to customers with deliberate product GAPS so the
cross-sell engine has something to recommend.

Run:  python sample_data/seed_crm.py   (after python db.py)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from warehouse.build import (ensure_tables, DIM_CUSTOMER_COLS, DIM_RM_COLS, DIM_PRODUCT_COLS, BRIDGE_COLS)

RMS = [
    ("R01", "Priya Nair", "Koramangala", "South"),
    ("R02", "Arjun Mehta", "Andheri", "West"),
]

PRODUCTS = [
    ("SAV", "Savings Account", "deposits"),
    ("SAL", "Salary Account", "deposits"),
    ("FD", "Fixed Deposit", "deposits"),
    ("CC_ACE", "ACE Credit Card", "cards"),
    ("CC_MAGNUS", "Magnus Credit Card", "cards"),
    ("PL", "Personal Loan", "loans"),
    ("HL", "Home Loan", "loans"),
    ("INS", "Max Life Insurance", "insurance"),
    ("DMT", "Axis Direct Demat", "invest"),
]

# customer_key, name, segment, rm_id, city, clv, risk, products_held
CUSTOMERS = [
    ("C001", "Ravi Kumar",       "Priority",  "R01", "Bengaluru", 850000,  0, "savings account,debit card"),
    ("C002", "Fedup Sharma",     "Salaried",  "R01", "Bengaluru", 320000,  1, "salary account,credit card"),
    ("C003", "Tara Vittal",      "Burgundy",  "R02", "Mumbai",    4200000, 0, "credit card,savings account"),
    ("C004", "Deepak Deals",     "Priority",  "R02", "Pune",      610000,  0, "credit card"),
    ("C005", "S. Chandran",      "Burgundy",  "R01", "Bengaluru", 5300000, 0, "current account,savings account"),
    ("C006", "Nikhil Newbie",    "Standard",  "R02", "Delhi",     140000,  0, "savings account"),
]

# social handle (must match raw_posts.author exactly) -> customer_key
BRIDGE = [
    ("@ravi_k",       "C001"),
    ("@fedup",        "C002"),
    ("@traveller",    "C003"),
    ("@deals",        "C004"),   # note: reddit-style also possible; kept simple for the demo
    ("@startup_ceo",  "C005"),
    ("u/newbie",      "C006"),
]


def main():
    ensure_tables()
    ts = db.now()

    db.upsert_rows("dim_rm", [dict(zip(DIM_RM_COLS, r)) for r in RMS], "rm_id", DIM_RM_COLS)
    db.upsert_rows("dim_product", [dict(zip(DIM_PRODUCT_COLS, p)) for p in PRODUCTS], "product_code", DIM_PRODUCT_COLS)

    cust = [dict(customer_key=c[0], customer_name=c[1], segment=c[2], rm_id=c[3], city=c[4],
                 clv=c[5], risk_flag=c[6], products_held=c[7], updated_at=ts) for c in CUSTOMERS]
    db.upsert_rows("dim_customer", cust, "customer_key", DIM_CUSTOMER_COLS)

    bridge = [dict(author=a, customer_key=ck, match_method="crm_verified", confidence=1.0,
                   verified_by="crm_extract", effective_from=ts, effective_to=None) for a, ck in BRIDGE]
    db.upsert_rows("bridge_handle_customer", bridge, "author", BRIDGE_COLS)

    print(f"seeded CRM: {len(RMS)} RMs, {len(CUSTOMERS)} customers, {len(PRODUCTS)} products, "
          f"{len(BRIDGE)} handle links.")


if __name__ == "__main__":
    main()
