-- Chapter 3 fictional ledger. Money = integer INR paise; stock = whole bricks.
CREATE TABLE context (
    id INTEGER PRIMARY KEY CHECK(id=1), plant_id TEXT NOT NULL,
    period_start TEXT NOT NULL, period_end TEXT NOT NULL CHECK(period_end>=period_start),
    opening_cash_paise INTEGER NOT NULL CHECK(opening_cash_paise>=0),
    evidence_status TEXT NOT NULL CHECK(evidence_status='fictional'), setup_json TEXT NOT NULL
) STRICT;
CREATE TABLE parties (
    party_id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner','accountant','worker','supplier','buyer'))
) STRICT;
CREATE TABLE lots (lot_id TEXT PRIMARY KEY NOT NULL) STRICT;
CREATE TABLE buckets (
    bucket TEXT PRIMARY KEY NOT NULL, on_hand INTEGER NOT NULL CHECK(on_hand IN (0,1))
) STRICT;
CREATE TABLE stock_openings (
    lot_id TEXT NOT NULL REFERENCES lots(lot_id), bucket TEXT NOT NULL REFERENCES buckets(bucket),
    quantity_bricks INTEGER NOT NULL CHECK(quantity_bricks>=0), PRIMARY KEY(lot_id,bucket)
) STRICT;
CREATE TABLE events (
    event_id TEXT PRIMARY KEY NOT NULL, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
    business_date TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES parties(party_id),
    source_ref TEXT NOT NULL UNIQUE, evidence_status TEXT NOT NULL CHECK(evidence_status='fictional'),
    payload_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE TABLE work_credits (
    work_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    worker_id TEXT NOT NULL REFERENCES parties(party_id), lot_id TEXT NOT NULL REFERENCES lots(lot_id),
    quantity_bricks INTEGER NOT NULL CHECK(quantity_bricks>0),
    rate_per_1000_paise INTEGER NOT NULL CHECK(rate_per_1000_paise>0),
    earned_paise INTEGER NOT NULL CHECK(earned_paise>=0),
    CHECK(rate_per_1000_paise<=9223372036854775307/quantity_bricks),
    CHECK(earned_paise=(quantity_bricks*rate_per_1000_paise+500)/1000)
) STRICT;
CREATE TABLE work_approvals (
    approval_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    work_id TEXT NOT NULL UNIQUE REFERENCES work_credits(work_id)
) STRICT;
CREATE TABLE stock_movements (
    movement_id TEXT PRIMARY KEY NOT NULL, event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
    lot_id TEXT NOT NULL REFERENCES lots(lot_id), work_id TEXT UNIQUE REFERENCES work_credits(work_id),
    purpose TEXT NOT NULL
) STRICT;
CREATE TABLE stock_lines (
    movement_id TEXT NOT NULL REFERENCES stock_movements(movement_id),
    bucket TEXT NOT NULL REFERENCES buckets(bucket), delta_bricks INTEGER NOT NULL CHECK(delta_bricks<>0),
    PRIMARY KEY(movement_id,bucket)
) STRICT;
CREATE TABLE bills (
    bill_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    supplier_id TEXT NOT NULL REFERENCES parties(party_id), description TEXT NOT NULL,
    original_amount_paise INTEGER NOT NULL CHECK(original_amount_paise>0)
) STRICT;
CREATE TABLE bill_corrections (
    correction_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    bill_id TEXT NOT NULL UNIQUE REFERENCES bills(bill_id),
    old_amount_paise INTEGER NOT NULL CHECK(old_amount_paise>0),
    new_amount_paise INTEGER NOT NULL CHECK(new_amount_paise>0), reason TEXT NOT NULL, evidence_ref TEXT NOT NULL,
    CHECK(old_amount_paise<>new_amount_paise)
) STRICT;
CREATE TABLE correction_reviews (
    review_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    correction_id TEXT NOT NULL UNIQUE REFERENCES bill_corrections(correction_id),
    decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')), reason TEXT NOT NULL
) STRICT;
CREATE TABLE cash_entries (
    cash_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    kind TEXT NOT NULL CHECK(kind IN ('worker_payment','bill_payment','customer_receipt')),
    amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
    work_id TEXT REFERENCES work_credits(work_id), bill_id TEXT REFERENCES bills(bill_id),
    buyer_id TEXT REFERENCES parties(party_id), correction_id TEXT REFERENCES bill_corrections(correction_id),
    CHECK((kind='worker_payment' AND work_id IS NOT NULL AND bill_id IS NULL AND buyer_id IS NULL AND correction_id IS NULL)
       OR (kind='bill_payment' AND bill_id IS NOT NULL AND work_id IS NULL AND buyer_id IS NULL)
       OR (kind='customer_receipt' AND buyer_id IS NOT NULL AND bill_id IS NULL AND work_id IS NULL AND correction_id IS NULL))
) STRICT;
CREATE TABLE sales (
    sale_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    dispatch_ref TEXT NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
    buyer_id TEXT NOT NULL REFERENCES parties(party_id), grade TEXT NOT NULL REFERENCES buckets(bucket),
    quantity_bricks INTEGER NOT NULL CHECK(quantity_bricks>0),
    unit_price_paise INTEGER NOT NULL CHECK(unit_price_paise>0), amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
    CHECK(unit_price_paise<=9223372036854775807/quantity_bricks),
    CHECK(amount_paise=quantity_bricks*unit_price_paise)
) STRICT;
CREATE TABLE receipt_allocations (
    allocation_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id),
    receipt_id TEXT NOT NULL REFERENCES cash_entries(cash_id), sale_id TEXT NOT NULL REFERENCES sales(sale_id),
    amount_paise INTEGER NOT NULL CHECK(amount_paise>0)
) STRICT;
CREATE TABLE stock_returns (
    return_id TEXT PRIMARY KEY NOT NULL REFERENCES events(event_id), sale_id TEXT NOT NULL REFERENCES sales(sale_id),
    quantity_bricks INTEGER NOT NULL CHECK(quantity_bricks>0), inspection_note TEXT NOT NULL
) STRICT;
CREATE INDEX event_dates ON events(business_date,event_id);
CREATE INDEX payments_work ON cash_entries(work_id);
CREATE INDEX payments_bill ON cash_entries(bill_id);
CREATE INDEX allocations_receipt ON receipt_allocations(receipt_id);
CREATE INDEX allocations_sale ON receipt_allocations(sale_id);
CREATE INDEX movements_lot ON stock_movements(lot_id);
CREATE INDEX returns_sale ON stock_returns(sale_id);
