"""Run all five fictional one-lot cases locally; never touch earlier lesson DBs."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

import ledger

PROJECT = Path(__file__).resolve().parents[2]
CASES = ('baseline', 'pending', 'rejected', 'approved', 'settled')


def example(case='baseline'):
    ledger.require(case in CASES, 'Unknown example case')
    source_path = PROJECT / 'learning/02_linked_records.json'
    source = json.loads(source_path.read_text(encoding='utf-8'))
    journal = json.loads((PROJECT / 'learning/03_correction_journal.json').read_text(encoding='utf-8'))
    ledger.require(hashlib.sha256(source_path.read_bytes()).hexdigest() == journal['source_sha256'],
                   'The original example differs from its preserved fingerprint; review before importing')
    ledger.require(source['status'] == journal['status'] == 'fictional_learning_example', 'Only fictional sources are supported')
    money = lambda value: ledger.integer(value) * 100
    setup = {
        'evidence_status': 'fictional', 'currency': 'INR', 'plant_id': source['plant_id'],
        'period_start': source['period_start'], 'period_end': source['period_end'],
        'opening_cash_paise': money(source['opening_cash']),
        'opening_worker_outstanding_paise': 0, 'opening_supplier_outstanding_paise': 0,
        'opening_customer_balance_paise': 0,
        'parties': [
            {'party_id': 'DEMO-OWNER', 'name': 'Demo Owner', 'role': 'owner'},
            {'party_id': 'DEMO-ACCOUNTANT', 'name': 'Demo Accountant', 'role': 'accountant'},
            {'party_id': 'WORKER-001', 'name': source['workers'][0]['name'], 'role': 'worker'},
            {'party_id': 'DEMO-BUYER', 'name': 'Demo Buyer', 'role': 'buyer'},
            {'party_id': 'DEMO-SUPPLIER', 'name': 'Demo Supplier', 'role': 'supplier'},
        ],
        'lots': [{'lot_id': 'LOT-DEMO-001', 'opening_stock': {b: source['opening_stock'][b] for b in ledger.ACTIVE}}],
    }
    events = []

    def add(eid, kind, date, data, *, owner=False, source_ref=None):
        events.append({'event_id': eid, 'request_id': 'REQUEST-' + eid, 'kind': kind,
                       'business_date': date, 'actor_id': 'DEMO-OWNER' if owner else 'DEMO-ACCOUNTANT',
                       'source_ref': source_ref or source['id'] + '/' + eid,
                       'evidence_status': 'fictional', 'data': data})

    work = source['work_credits'][0]
    add(work['id'], 'moulding', work['date'], {
        'worker_id': work['worker_id'], 'lot_id': 'LOT-DEMO-001', 'quantity_bricks': work['quantity'],
        'rate_per_1000_paise': money(work['rate_per_1000']), 'receipt_id': 'MOVE-001'})
    add('APPROVAL-001', 'work_approval', work['approved_on'], {'work_id': work['id']}, owner=True,
        source_ref=source['id'] + '/WORK-001/approved_on')
    for move in source['stock_movements'][1:-1]:
        d = {'lot_id': 'LOT-DEMO-001', 'from_bucket': move['from'], 'quantity_bricks': move['quantity']}
        if move['from'] == 'cooling':
            d['grades'] = {line['bucket']: line['quantity'] for line in move['to']}
            kind = 'grading'
        else:
            d['to_bucket'] = move['to'][0]['bucket']
            kind = 'transfer'
        add(move['id'], kind, move['date'], d)
    for bill in source['bills']:
        add(bill['id'], 'bill', bill['date'], {'supplier_id': bill['supplier_id'],
            'description': bill['description'], 'amount_paise': money(bill['amount'])})
    for cash in source['cash_entries']:
        kind = {'wage_payment': 'worker_payment'}.get(cash['kind'], cash['kind'])
        d = {'amount_paise': money(cash['amount'])}
        d[{'worker_payment': 'work_id', 'bill_payment': 'bill_id', 'customer_receipt': 'buyer_id'}[kind]] = cash.get('ref', cash.get('customer_id'))
        add(cash['id'], kind, cash['date'], d)
    for sale in source['sales']:
        add(sale['id'], 'dispatch_sale', sale['date'], {'dispatch_ref': sale['dispatch_ref'],
            'lot_id': 'LOT-DEMO-001', 'buyer_id': sale['customer_id'], 'grade': sale['grade'],
            'quantity_bricks': sale['quantity'], 'unit_price_paise': money(sale['price_per_brick'])})
    for allocation in source['receipt_allocations']:
        add(allocation['id'], 'allocation', allocation['date'], {'receipt_id': allocation['receipt_ref'],
            'sale_id': allocation['sale_ref'], 'amount_paise': money(allocation['amount'])})
    if case != 'baseline':
        correction = journal['requests'][0]
        add(correction['id'], 'correction_request', correction['requested_on'], {
            'bill_id': correction['target_id'], 'old_amount_paise': money(correction['old_value']),
            'new_amount_paise': money(correction['new_value']), 'reason': correction['reason'],
            'evidence_ref': correction['evidence_ref']}, source_ref=journal['id'] + '/' + correction['id'])
    if case in {'rejected', 'approved', 'settled'}:
        review = journal['reviews'][0]
        add(review['id'], 'correction_review', review['reviewed_on'], {
            'correction_id': review['correction_ref'], 'decision': 'rejected' if case == 'rejected' else 'approved',
            'reason': 'Fictional rejection alternative.' if case == 'rejected' else review['reason']},
            owner=True, source_ref=journal['id'] + '/' + review['id'])
    if case == 'settled':
        cash = journal['settlements'][0]
        add(cash['id'], 'bill_payment', cash['date'], {'bill_id': cash['ref'],
            'correction_id': cash['correction_ref'], 'amount_paise': money(cash['amount'])},
            source_ref=journal['id'] + '/' + cash['id'])
    # Stable sort preserves dependency order for same-day dispatch and allocation.
    events.sort(key=lambda event: event['business_date'])
    return setup, events


def assert_case(report, case):
    expected_cash = 345000 if case == 'settled' else 350000
    expected_due = 5000 if case == 'approved' else 0
    ledger.require((report['stock_bricks'], report['cash_paise'], report['bill_outstanding_paise']) ==
                   (500, expected_cash, expected_due), 'Original one-lot totals do not reconcile')
    ledger.require((report['earned_paise'], report['worker_paid_paise'], report['worker_outstanding_paise']) ==
                   (80000,80000,0), 'Worker settlement does not reconcile')
    ledger.require(report['sales_face_paise'] == 250000 and report['unallocated_customer_receipts_paise'] == 0,
                   'Sales/advance allocation does not reconcile')
    ledger.require(report['profit_paise'] is None and report['tax_breakdown_paise'] is None, 'Unknown profit/tax must remain unknown')


def report_markdown(result, case):
    work_ids = ', '.join(w['work_id'] for w in result['work']) or 'No work entries'
    worker_payments = ', '.join(c['cash_id'] for c in result['cash_entries'] if c['kind']=='worker_payment') or 'No worker payments'
    sales_links = ', '.join(s['sale_id']+' -> '+s['dispatch_ref'] for s in result['sales']) or 'No sales'
    customer_cash = sum(c['amount_paise'] for c in result['cash_entries'] if c['kind']=='customer_receipt')
    receipt_ids = ', '.join(c['cash_id'] for c in result['cash_entries'] if c['kind']=='customer_receipt') or 'No customer receipts'
    lines = [f'# BrickFlow owner report: {case}', '',
        f"**Fictional demonstration only.** Reporting period: {result['period_start']} to {result['period_end']}. The five demonstration cases are alternative correction states at the same cutoff.", '',
        '| Total | Value | Trace to saved records |', '| --- | ---: | --- |',
        f"| Bricks still on hand | {result['stock_bricks']:,} bricks | stock_openings + stock_movements + stock_lines |",
        f"| Recorded work earnings | {ledger.rupees(result['earned_paise'])} | {work_ids} |",
        f"| Worker payments | {ledger.rupees(result['worker_paid_paise'])} | {worker_payments}; each links to its approved work |",
        f"| Worker outstanding | {ledger.rupees(result['worker_outstanding_paise'])} | Earnings minus allocated worker payments |",
        f"| Recorded sale face amount | {ledger.rupees(result['sales_face_paise'])} | {sales_links} |",
        f"| Customer money received | {ledger.rupees(customer_cash)} | {receipt_ids}; receipt_allocations links money to sales without receiving it twice |",
        f"| Closing cash | {ledger.rupees(result['cash_paise'])} | Opening {ledger.rupees(result['opening_cash_paise'])} + receipts - actual payments below |",
        f"| Unpaid bill amount | {ledger.rupees(result['bill_outstanding_paise'])} | Original bills, approved corrections and linked payments below |",
        '| Final profit and tax breakdown | Unknown | Complete costs, stock valuation and tax treatment are absent |', '',
        '## Remaining stock', '', '| Lot | Bucket | Bricks |', '| --- | --- | ---: |']
    for row in result['stock']:
        if row['bucket'] in ledger.ACTIVE and row['quantity_bricks']:
            lines.append(f"| {row['lot_id']} | {row['bucket']} | {row['quantity_bricks']:,} |")
    lines += ['', '## Cash trace', '', '| ID | Date | Kind | Amount | Work/bill/buyer link |', '| --- | --- | --- | ---: | --- |']
    for row in result['cash_entries']:
        link = row['work_id'] or row['bill_id'] or row['buyer_id']
        lines.append(f"| {row['cash_id']} | {row['business_date']} | {row['kind']} | {ledger.rupees(row['amount_paise'])} | {link} |")
    lines += ['', '## Bill history', '']
    for bill in result['bills']:
        lines.append(f"- {bill['bill_id']}: original {ledger.rupees(bill['original_amount_paise'])}; current obligation {ledger.rupees(bill['current_amount_paise'])}; paid {ledger.rupees(bill['paid_paise'])}.")
    for correction in result['corrections']:
        lines.append(f"- {correction['correction_id']}: {ledger.rupees(correction['old_amount_paise'])} -> {ledger.rupees(correction['new_amount_paise'])}; decision {correction['decision'] or 'pending'}; evidence {correction['evidence_ref']}.")
    lines += ['', 'The companion records JSON contains every table, original source reference, business date and entry timestamp. Approval changes the obligation; a separate cash entry records settlement. No transport account, real private record or achieved-profit claim is included.', '']
    return '\n'.join(lines)


def run_case(directory, case):
    setup, events = example(case)
    path = Path(directory) / f'{case}.sqlite3'
    db = ledger.open_db(path, setup)
    try:
        statuses = [ledger.record_event(db, event) for event in events]
        before = ledger.snapshot(db)
    finally:
        db.close()
    reopened = ledger.open_db(path, readonly=True)
    try:
        after = ledger.snapshot(reopened)
        ledger.require(before == after, 'Records changed after reopening')
        ledger.require(reopened.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Database integrity check failed')
        assert_case(after['report'], case)
        backup = Path(directory) / f'{case}.backup.sqlite3'
        if backup.exists():
            restored = ledger.open_db(backup, readonly=True)
            try:
                ledger.require(ledger.snapshot(restored) == after, 'Existing backup differs; choose a new demo directory')
            finally:
                restored.close()
        else:
            ledger.backup_and_verify(reopened, backup)
    finally:
        reopened.close()
    (Path(directory) / f'{case}.records.json').write_text(json.dumps(after,indent=2)+'\n',encoding='utf-8')
    (Path(directory) / f'{case}.owner-report.md').write_text(report_markdown(after['report'],case),encoding='utf-8')
    return {'case':case, 'report':after['report'], 'new_events':statuses.count('recorded'),
            'retries':statuses.count('already_recorded'), 'reopen_and_backup_match':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ledger.HERE/'data')
    parser.add_argument('--case', choices=CASES+('all',), default='all')
    parser.add_argument('--show', action='store_true', help='Read saved reports without creating or changing files')
    args = parser.parse_args()
    cases = CASES if args.case == 'all' else (args.case,)
    results = []
    try:
        if not args.show:
            args.data_dir.mkdir(parents=True, exist_ok=True)
        for case in cases:
            if args.show:
                db = ledger.open_db(args.data_dir/f'{case}.sqlite3', readonly=True)
                try:
                    result = {'case':case, 'report':ledger.report(db)}
                finally:
                    db.close()
            else:
                result = run_case(args.data_dir,case)
            r = result['report']
            print(f"{case}: {r['stock_bricks']:,} bricks remain | cash {ledger.rupees(r['cash_paise'])} | bill owed {ledger.rupees(r['bill_outstanding_paise'])}")
            results.append(result)
    except (ValueError, KeyError, sqlite3.Error, OSError) as error:
        parser.exit(2, f'Cannot complete fictional demonstration: {error}\n')
    if not args.show:
        (args.data_dir/'run-results.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
        print('Verified close/reopen and backup restore for every selected case. Repeating is safe.')
        print('Owner reports and records:', args.data_dir.resolve())
    print('Fictional examples. Cash is not profit. No private data was imported or published.')


if __name__ == '__main__':
    main()
