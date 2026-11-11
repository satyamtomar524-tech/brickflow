"""Reviewed fictional openings and account movements, separate from period trade."""
import json

TABLES = ('payment_accounts','account_openings','party_openings','cash_entry_accounts',
          'opening_settlements','opening_advance_allocations')
FIELDS = {
    'account_register': {'account_id','name','account_type','review_note'},
    'account_opening': {'account_id','amount_paise','opening_as_of','review_note','evidence_ref'},
    'party_opening': {'party_id','balance_kind','amount_paise','opening_as_of','review_note','evidence_ref'},
    'openings_lock': {'review_note'},
    'opening_settlement': {'opening_id','account_id','amount_paise'},
    'opening_advance_allocation': {'opening_id','sale_id','amount_paise'},
}
OPENING_KINDS = {'worker_payable':'worker','supplier_payable':'supplier',
                 'customer_receivable':'buyer','customer_advance':'buyer'}
SETUP_KINDS = {'account_register','account_opening','party_opening','openings_lock'}
LEGACY_ACCOUNT = 'LEGACY-CASH'


def reviewed_mode(db):
    setup = json.loads(db.execute('SELECT setup_json FROM context').fetchone()[0])
    return setup.get('finance_mode') == 'reviewed_accounts'


def locked(db, excluding=''):
    return not reviewed_mode(db) or db.execute(
        "SELECT 1 FROM events WHERE kind='openings_lock' AND event_id<>?",(excluding,)).fetchone() is not None


def initialize_legacy(db):
    """Carry original fictional declarations; never fabricate an owner review."""
    import ledger as L
    context = db.execute('SELECT * FROM context').fetchone()
    L.insert(db,'payment_accounts',dict(account_id=LEGACY_ACCOUNT,name='Legacy pooled cash',
             account_type='cash',register_event_id=None,source_basis='legacy_carried'))
    L.insert(db,'account_openings',dict(account_id=LEGACY_ACCOUNT,amount_paise=context['opening_cash_paise'],
             opening_as_of=context['period_start'],review_event_id=None,source_basis='legacy_carried'))
    for party in db.execute('SELECT * FROM parties'):
        for kind, role in OPENING_KINDS.items():
            if party['role'] == role:
                L.insert(db,'party_openings',dict(opening_id='LEGACY-'+kind+'-'+party['party_id'],
                    party_id=party['party_id'],balance_kind=kind,amount_paise=0,
                    opening_as_of=context['period_start'],review_event_id=None,source_basis='legacy_carried'))
    for row in db.execute('SELECT cash_id FROM cash_entries'):
        L.insert(db,'cash_entry_accounts',dict(cash_id=row['cash_id'],account_id=LEGACY_ACCOUNT))


def opening_rows(db):
    import ledger as L
    if L.schema_version(db) < 3:
        context = db.execute('SELECT * FROM context').fetchone()
        rows = []
        for party in db.execute('SELECT * FROM parties'):
            for kind, role in OPENING_KINDS.items():
                if party['role'] == role:
                    rows.append(dict(opening_id='LEGACY-'+kind+'-'+party['party_id'],party_id=party['party_id'],
                        balance_kind=kind,amount_paise=0,opening_as_of=context['period_start'],
                        review_event_id=None,source_basis='legacy_carried',settled_paise=0,allocated_paise=0,remaining_paise=0))
        return rows
    rows = [dict(r) for r in db.execute('SELECT * FROM party_openings ORDER BY rowid')]
    for row in rows:
        row['settled_paise'] = sum(r[0] for r in db.execute(
            'SELECT amount_paise FROM opening_settlements WHERE opening_id=?',(row['opening_id'],)))
        row['allocated_paise'] = sum(r[0] for r in db.execute(
            'SELECT amount_paise FROM opening_advance_allocations WHERE opening_id=?',(row['opening_id'],)))
        row['remaining_paise'] = row['amount_paise']-row['settled_paise']-row['allocated_paise']
        L.require(row['remaining_paise'] >= 0,'Opening balance over-settled or over-allocated')
    return rows


def account_rows(db):
    import ledger as L
    context = db.execute('SELECT * FROM context').fetchone()
    if L.schema_version(db) < 3:
        incoming = sum(r[0] for r in db.execute("SELECT amount_paise FROM cash_entries WHERE kind='customer_receipt'"))
        outgoing = sum(r[0] for r in db.execute("SELECT amount_paise FROM cash_entries WHERE kind!='customer_receipt'"))
        closing = context['opening_cash_paise']+incoming-outgoing
        L.require(closing >= 0,'Cash would become negative; no borrowing is assumed')
        return [dict(account_id=LEGACY_ACCOUNT,name='Legacy pooled cash',account_type='cash',
                     opening_paise=context['opening_cash_paise'],receipts_paise=incoming,payments_paise=outgoing,
                     closing_paise=closing,source_basis='legacy_carried')]
    rows = [dict(r) for r in db.execute('''SELECT a.*,o.amount_paise AS opening_paise,
        o.review_event_id,o.opening_as_of,o.source_basis AS opening_basis
        FROM payment_accounts a LEFT JOIN account_openings o ON o.account_id=a.account_id ORDER BY a.rowid''')]
    missing_links = db.execute('''SELECT 1 FROM cash_entries c LEFT JOIN cash_entry_accounts a
        ON a.cash_id=c.cash_id WHERE a.cash_id IS NULL''').fetchone()
    L.require(missing_links is None,'Every money entry needs a named account')
    for row in rows:
        entries = db.execute('''SELECT c.kind,c.amount_paise FROM cash_entries c JOIN cash_entry_accounts a
            ON a.cash_id=c.cash_id WHERE a.account_id=?''',(row['account_id'],)).fetchall()
        settlements = db.execute('''SELECT p.balance_kind,s.amount_paise FROM opening_settlements s
            JOIN party_openings p ON p.opening_id=s.opening_id WHERE s.account_id=?''',(row['account_id'],)).fetchall()
        row['receipts_paise'] = sum(r['amount_paise'] for r in entries if r['kind']=='customer_receipt')
        row['payments_paise'] = sum(r['amount_paise'] for r in entries if r['kind']!='customer_receipt')
        row['opening_collections_paise'] = sum(r['amount_paise'] for r in settlements if r['balance_kind']=='customer_receivable')
        row['opening_payments_paise'] = sum(r['amount_paise'] for r in settlements if r['balance_kind']!='customer_receivable')
        row['source_basis'] = row.pop('opening_basis') or 'pending'
        if row['opening_paise'] is None:
            L.require(not entries and not settlements,'An unknown account opening cannot support money movements')
            row['closing_paise'] = None
        else:
            row['closing_paise'] = (row['opening_paise']+row['receipts_paise']+row['opening_collections_paise']
                                    -row['payments_paise']-row['opening_payments_paise'])
            L.require(row['closing_paise'] >= 0,
                      row['account_type'].title()+' would become negative in the selected account; no borrowing is assumed')
    return rows


def totals(rows):
    def total(kind=None):
        selected = [r for r in rows if kind is None or r['account_type']==kind]
        if not rows or any(r['closing_paise'] is None for r in selected):
            return None
        return sum(r['closing_paise'] for r in selected)
    return dict(cash_paise=total('cash'),bank_paise=total('bank'),funds_paise=total(),
                known_funds_subtotal_paise=sum(r['closing_paise'] for r in rows if r['closing_paise'] is not None) if rows else None)


def select_account(db, data, *, payment=False):
    import ledger as L
    if L.schema_version(db) < 3:
        L.require('account_id' not in data,'Named accounts require an explicitly upgraded copy')
        return None
    identifier = data.get('account_id')
    if identifier is None and not reviewed_mode(db):
        identifier = LEGACY_ACCOUNT
    L.text(identifier)
    row = next((r for r in account_rows(db) if r['account_id']==identifier),None)
    L.require(row is not None,'Unknown payment account')
    L.require(row['closing_paise'] is not None,'Review the account opening before recording money')
    if payment:
        L.require(L.integer(data['amount_paise'],positive=True) <= row['closing_paise'],
                  row['account_type'].title()+' would become negative in the selected account; no borrowing is assumed')
    return identifier


def sale_allocated(db, sale_id):
    import ledger as L
    amount = sum(r[0] for r in db.execute('SELECT amount_paise FROM receipt_allocations WHERE sale_id=?',(sale_id,)))
    if L.schema_version(db) >= 3:
        amount += sum(r[0] for r in db.execute('SELECT amount_paise FROM opening_advance_allocations WHERE sale_id=?',(sale_id,)))
    return amount


def party_balances(db, work, bills, sales, receipts):
    openings = {(r['party_id'],r['balance_kind']):r for r in opening_rows(db)}
    rows = []
    for party in db.execute('SELECT * FROM parties ORDER BY rowid'):
        for kind, role in OPENING_KINDS.items():
            if party['role'] != role:
                continue
            opening = openings.get((party['party_id'],kind))
            if kind=='worker_payable':
                current = sum(w['outstanding_paise'] for w in work if w['worker_id']==party['party_id'])
            elif kind=='supplier_payable':
                current = sum(b['outstanding_paise'] for b in bills if b['supplier_id']==party['party_id'])
            elif kind=='customer_receivable':
                current = sum(s['unsettled_paise'] for s in sales if s['buyer_id']==party['party_id'])
            else:
                current = sum(r['unallocated_paise'] for r in receipts if r['buyer_id']==party['party_id'])
            remaining = opening['remaining_paise'] if opening else None
            rows.append(dict(party_id=party['party_id'],name=party['name'],balance_kind=kind,
                opening_id=opening['opening_id'] if opening else None,
                opening_amount_paise=opening['amount_paise'] if opening else None,
                opening_remaining_paise=remaining,current_balance_paise=current,
                total_balance_paise=None if remaining is None else remaining+current,
                source_basis=opening['source_basis'] if opening else 'pending'))
    return rows


def apply(db, event):
    """Called inside the ledger's existing serialized, atomic event transaction."""
    import ledger as L
    L.require(L.schema_version(db) >= 3,'Opening accounts require an explicitly upgraded copy')
    kind, identifier, data = event['kind'],event['event_id'],event['data']
    if kind in SETUP_KINDS:
        L.party_role(db,event['actor_id'],{'owner'})
        L.text(data['review_note'])
    if kind in {'account_register','account_opening','openings_lock'}:
        L.require(reviewed_mode(db) and not locked(db,identifier),'Account openings are locked for this period')
    if kind=='account_register':
        L.require(data['account_type'] in {'cash','bank'},'Use a cash or bank account')
        L.insert(db,'payment_accounts',dict(account_id=L.text(data['account_id']),name=L.text(data['name']),
                 account_type=data['account_type'],register_event_id=identifier,source_basis='reviewed'))
    elif kind in {'account_opening','party_opening'}:
        context = db.execute('SELECT period_start FROM context').fetchone()
        L.require(L.day(data['opening_as_of'])==context['period_start'],'Opening as-of must be period start')
        amount = L.integer(data['amount_paise'])
        L.text(data['evidence_ref'])
        common = dict(amount_paise=amount,opening_as_of=data['opening_as_of'],
                      review_event_id=identifier,source_basis='reviewed')
        if kind=='account_opening':
            L.referenced(db,'payment_accounts','account_id',data['account_id'])
            L.insert(db,'account_openings',dict(account_id=data['account_id'],**common))
        else:
            L.require(data['balance_kind'] in OPENING_KINDS,'Unsupported opening kind; worker/supplier advances are deferred')
            L.party_role(db,data['party_id'],{OPENING_KINDS[data['balance_kind']]})
            L.insert(db,'party_openings',dict(opening_id=identifier,party_id=data['party_id'],
                     balance_kind=data['balance_kind'],**common))
    elif kind=='openings_lock':
        rows = account_rows(db)
        L.require(bool(rows) and all(r['opening_paise'] is not None for r in rows),
                  'Review every registered account opening before locking')
    elif kind=='opening_settlement':
        opening = next((r for r in opening_rows(db) if r['opening_id']==L.text(data['opening_id'])),None)
        L.require(opening is not None,'Unknown opening reference')
        L.require(opening['balance_kind']!='customer_advance','Opening advance refunds are not implemented')
        amount = L.integer(data['amount_paise'],positive=True)
        L.require(amount <= opening['remaining_paise'],'Opening settlement exceeds remaining balance')
        account = select_account(db,data,payment=opening['balance_kind']!='customer_receivable')
        L.insert(db,'opening_settlements',dict(settlement_id=identifier,opening_id=data['opening_id'],
                 account_id=account,amount_paise=amount))
    elif kind=='opening_advance_allocation':
        opening = next((r for r in opening_rows(db) if r['opening_id']==L.text(data['opening_id'])),None)
        L.require(opening is not None and opening['balance_kind']=='customer_advance','Use a customer advance opening')
        sale = L.referenced(db,'sales','sale_id',data['sale_id'])
        L.require(opening['party_id']==sale['buyer_id'],'Opening advance and sale need the same buyer')
        amount = L.integer(data['amount_paise'],positive=True)
        L.require(amount <= opening['remaining_paise'] and amount <= sale['amount_paise']-sale_allocated(db,data['sale_id']),
                  'Allocation exceeds opening advance or unsettled sale')
        L.insert(db,'opening_advance_allocations',dict(allocation_id=identifier,opening_id=data['opening_id'],
                 sale_id=data['sale_id'],amount_paise=amount))
