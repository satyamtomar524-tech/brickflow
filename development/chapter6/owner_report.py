"""Read-only owner measures from one complete fictional ledger reporting period."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'chapter3'))
import ledger  # noqa: E402 - sibling module requires the path setup above.


def reconcile_stock(tables, stock):
    """Compare recorded inputs with destinations, separately for each lot."""
    movements = {m['movement_id']: m for m in tables['stock_movements']}
    checks = []
    for lot in tables['lots']:
        lot_id = lot['lot_id']
        openings = [r for r in tables['stock_openings'] if r['lot_id']==lot_id]
        work = [r for r in tables['work_credits'] if r['lot_id']==lot_id]
        sales = [r for r in tables['sales'] if movements[r['dispatch_ref']]['lot_id']==lot_id]
        sale_ids = {r['sale_id'] for r in sales}
        returns = [r for r in tables['stock_returns'] if r['sale_id'] in sale_ids]
        balances = {r['bucket']:r['quantity_bricks'] for r in stock if r['lot_id']==lot_id}
        opening = sum(r['quantity_bricks'] for r in openings)
        moulded = sum(r['quantity_bricks'] for r in work)
        dispatched = sum(r['quantity_bricks'] for r in sales)
        returned = sum(r['quantity_bricks'] for r in returns)
        on_hand = sum(balances.get(b,0) for b in ledger.ACTIVE)
        loss, internal = balances.get('loss',0), balances.get('internal_use',0)
        difference = opening + moulded + returned - dispatched - loss - internal - on_hand
        checks.append(dict(lot_id=lot_id,unit='brick',opening=opening,moulded=moulded,
            returned=returned,dispatched=dispatched,loss=loss,internal_use=internal,on_hand=on_hand,
            difference=difference,balanced=difference==0,
            sources=['stock_openings/'+r['lot_id']+'/'+r['bucket'] for r in openings]
                + ['work_credits/'+r['work_id'] for r in work]
                + ['sales/'+r['sale_id'] for r in sales]
                + ['stock_returns/'+r['return_id'] for r in returns]
                + ['stock_movements/'+m['movement_id'] for m in movements.values() if m['lot_id']==lot_id]))
    return checks


def build(path):
    with closing(ledger.open_db(path, readonly=True)) as db:
        saved = ledger.snapshot(db)
    r, tables = saved['report'], saved['tables']
    metrics = {}

    def metric(name, value, unit, sources, meaning):
        metrics[name] = dict(value=value, unit=unit, sources=sources, meaning=meaning)

    def refs(table, key):
        return [table+'/'+row[key] for row in tables.get(table,[])]

    stock_refs = ['stock_openings/'+s['lot_id']+'/'+s['bucket'] for s in tables['stock_openings']] + refs('stock_movements','movement_id')
    metric('stock_on_hand', r['stock_bricks'], 'brick', stock_refs, 'All recorded on-hand stages; not a physical stocktake.')
    for name,bucket,meaning in [
        ('recorded_loss_bricks','loss','Pieces explicitly removed as loss; not all lower-grade or damaged material, and not a monetary loss valuation.'),
        ('internal_use_bricks','internal_use','Pieces removed for plant use; not a customer sale or a new cash payment.')]:
        movement_ids = {line['movement_id'] for line in tables['stock_lines'] if line['bucket']==bucket}
        metric(name,sum(s['quantity_bricks'] for s in r['stock'] if s['bucket']==bucket),'brick',
               ['stock_movements/'+mid for mid in sorted(movement_ids)],meaning)
    metric('classified_finished_stock',sum(s['quantity_bricks'] for s in r['stock'] if s['bucket'] in ('first','lal_petti','pila_petti')),'brick',stock_refs,'Classified stock, without assuming an order or future sale.')
    metric('unclassified_stock',sum(s['quantity_bricks'] for s in r['stock'] if s['bucket']=='unclassified'),'brick',stock_refs,'Needs classification or inspection; not automatically waste.')
    metric('work_in_progress',sum(s['quantity_bricks'] for s in r['stock'] if s['bucket'] in ('moulded','dry_green','loaded','cooling')),'brick',stock_refs,'Count each piece only in its current stage.')
    work = refs('work_credits','work_id')
    cash = refs('cash_entries','cash_id')
    funds_sources = (refs('account_openings','account_id')+refs('cash_entry_accounts','cash_id')
                     +refs('opening_settlements','settlement_id')) if r['schema_version']>=3 else ['context/1']+cash
    for name, field, sources, meaning in [
        ('wages_earned','earned_paise',work,'Recorded work earnings, whether approved or paid.'),
        ('wages_approved','approved_paise',work+refs('work_approvals','approval_id'),'Approved earnings; approval is not payment.'),
        ('wages_paid','worker_paid_paise',cash,'Payments against current-period work only; opening wage settlements are separate.'),
        ('wages_outstanding','worker_outstanding_paise',work+cash,'Current-period earned minus linked payments only; excludes opening obligations and may include unapproved work.'),
        ('supplier_outstanding','bill_outstanding_paise',refs('bills','bill_id')+refs('bill_corrections','correction_id')+refs('correction_reviews','review_id')+cash,'Current-period effective bills minus linked payments only; opening supplier obligations are separate.'),
        ('sales_face_amount','sales_face_paise',refs('sales','sale_id'),'Recorded sale face amounts; no tax or return settlement adjustment is inferred.'),
        ('unallocated_receipts','unallocated_customer_receipts_paise',cash+refs('receipt_allocations','allocation_id'),'Received money not yet matched to sales; not extra sales income.'),
        ('closing_cash','cash_paise',funds_sources,'Recorded physical cash accounts only; bank funds are separate. Unknown openings stay unavailable.')]:
        metric(name,r[field],'paise',sources,meaning)
    metric('customer_receipts',sum(c['amount_paise'] for c in r['customer_receipts']),'paise',cash,'Customer money received, separate from sales.')
    metric('unsettled_sale_face_amount',sum(s['unsettled_paise'] for s in r['sales']),'paise',
           refs('sales','sale_id')+refs('receipt_allocations','allocation_id')+refs('opening_advance_allocations','allocation_id'),
           'Current-period sale face amount less ordinary receipt and opening-advance allocations; not an overdue-debt calculation. Returns require review.')
    metric('recorded_obligations',r['earned_paise']+sum(b['current_amount_paise'] for b in r['bills']),'paise',work+refs('bills','bill_id')+refs('bill_corrections','correction_id')+refs('correction_reviews','review_id'),'Work earnings plus effective bills only; incomplete costs, not cost of goods sold. Payments are not added again.')
    metric('closing_bank',r['bank_paise'],'paise',funds_sources,'Recorded bank account balances; not physical cash or profit.')
    metric('combined_funds',r['funds_paise'],'paise',funds_sources,'Recorded cash plus bank funds; unavailable if any declared account opening is missing.')
    metric('known_funds_subtotal',r['known_funds_subtotal_paise'],'paise',funds_sources,
           'Subtotal of accounts with known openings only; a partial subtotal when other accounts are pending.')
    opening_refs = refs('party_openings','opening_id')+refs('opening_settlements','settlement_id')+refs('opening_advance_allocations','allocation_id')
    for kind in ('worker_payable','supplier_payable','customer_receivable','customer_advance'):
        rows = [p for p in r['party_balances'] if p['balance_kind']==kind]
        value = None if any(p['total_balance_paise'] is None for p in rows) else sum(p['total_balance_paise'] for p in rows)
        metric(kind+'_total',value,'paise',opening_refs+work+cash+refs('bills','bill_id')
               +refs('bill_corrections','correction_id')+refs('correction_reviews','review_id')
               +refs('sales','sale_id')+refs('receipt_allocations','allocation_id'),
               'Opening remainder plus current-period balance for registered parties; unavailable when an opening is unknown. Opposing balances are not netted.')
    opening_kinds = {p['opening_id']:p['balance_kind'] for p in r['party_openings']}
    metric('opening_receivable_collections',sum(s['amount_paise'] for s in r['opening_settlements']
           if opening_kinds[s['opening_id']]=='customer_receivable'),'paise',opening_refs,
           'Money collected against opening receivables; not a new sale or reusable current receipt.')
    metric('opening_payable_payments',sum(s['amount_paise'] for s in r['opening_settlements']
           if opening_kinds[s['opening_id']] in ('worker_payable','supplier_payable')),'paise',opening_refs,
           'Payments against opening worker/supplier obligations; not current-period earnings or bills.')
    for name,unit,reason in [('profit','paise','Complete costs, tax treatment and inventory valuation are missing.'),('profit_margin','percent','Profit is unknown.'),('forecast_sales','brick','No validated demand forecast or complete order data.'),('weather_impact','paise','No linked weather observations and evaluated comparison.')]:
        metric(name,None,unit,[],reason)

    attention=[]
    def flag(kind,reference,reason):
        attention.append(dict(kind=kind,reference=reference,reason=reason))
    if r['finance_mode']=='reviewed_accounts':
        if not r['openings_locked']:
            flag('review_account_setup',r['plant_id'],'Review declared account openings and lock setup before current-period posting.')
        for account in r['account_balances']:
            if account['closing_paise'] is None:
                flag('missing_account_opening',account['account_id'],'Opening amount is unknown; zero has not been confirmed.')
        for party in r['party_balances']:
            if party['opening_id'] is None:
                flag('missing_party_opening',party['party_id']+'/'+party['balance_kind'],
                     'Party opening is not recorded; its total balance is unavailable, not zero.')
    stock_checks = reconcile_stock(tables,r['stock'])
    for check in stock_checks:
        if not check['balanced']:
            flag('stock_balance_mismatch',check['lot_id'],'Recorded inputs and stock destinations do not balance; review source records before relying on stock totals.')
    for w in r['work']:
        if w['approval_id'] is None:
            flag('review_work',w['work_id'],'Work has no approval record.')
        if w['outstanding_paise']:
            flag('wages_outstanding',w['work_id'],'Unpaid amount exists; no due date is assumed.')
    for b in r['bills']:
        if b['outstanding_paise']:
            flag('bill_outstanding',b['bill_id'],'Unpaid bill amount exists; not automatically overdue.')
    for c in r['corrections']:
        if c['decision'] is None:
            flag('review_correction',c['correction_id'],'Correction requested but not decided.')
    for s in r['sales']:
        if s['unsettled_paise']:
            flag('review_sale_balance',s['sale_id'],'Sale face amount is not fully matched to receipts; check allocations and returns before seeking payment.')
        if s['return_financial_review_needed']:
            flag('review_return',s['sale_id'],'Physical return exists; financial resolution is not implemented.')
    for receipt in r['customer_receipts']:
        if receipt['unallocated_paise']:
            flag('review_receipt_allocation',receipt['cash_id'],'Customer money remains unmatched; it may be an advance, not extra sales income.')
    for returned in r['returns']:
        if returned['remaining_bricks']:
            flag('review_return_stock',returned['return_id'],
                 'Returned pieces await owner inspection; financial resolution is separate.')
    for s in r['stock']:
        if s['bucket']=='unclassified' and s['quantity_bricks']:
            flag('classify_stock',s['lot_id']+'/unclassified','Unclassified pieces require review.')
    return dict(evidence_status='fictional',plant_id=r['plant_id'],period_start=r['period_start'],period_end=r['period_end'],
                scope='Whole saved reporting period; not a daily filter or historical as-of query',
                metrics=metrics,stock_by_lot_and_stage=r['stock'],stock_reconciliation=stock_checks,attention=attention,
                returned_stock=r['returns'],payment_accounts=r['account_balances'],party_balances=r['party_balances'],
                openings_locked=r['openings_locked'],finance_mode=r['finance_mode'],
                source_records=tables,limitations=r['limitations'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.database),indent=2))
