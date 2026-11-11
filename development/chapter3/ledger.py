"""Local, fictional Chapter 3 ledger. This is not a production accounting ERP."""
from contextlib import closing, contextmanager
from datetime import date
import json
from pathlib import Path
import sqlite3
import accounts

HERE = Path(__file__).resolve().parent
APP_ID = 0x42464C33
VERSION = 3
MAX_INT = (1 << 63) - 1
ACTIVE = ('moulded', 'dry_green', 'loaded', 'cooling', 'first', 'lal_petti', 'pila_petti', 'unclassified')
GRADES = ('first', 'lal_petti', 'pila_petti', 'unclassified')
BUCKETS = ACTIVE + ('dispatched', 'loss', 'internal_use')
TABLES = ('context', 'parties', 'lots', 'buckets', 'stock_openings', 'events',
          'work_credits', 'work_approvals', 'stock_movements', 'stock_lines', 'bills',
          'bill_corrections', 'correction_reviews', 'cash_entries', 'sales',
          'receipt_allocations', 'stock_returns', 'return_inspections')
V2_TABLES = TABLES
LEGACY_TABLES = V2_TABLES[:-1]
TABLES += accounts.TABLES
KINDS = {'moulding', 'work_approval', 'transfer', 'grading', 'bill', 'worker_payment',
         'bill_payment', 'customer_receipt', 'dispatch_sale', 'allocation',
         'correction_request', 'correction_review', 'loss', 'internal_use', 'return', 'return_inspection'}
DATA_FIELDS = {
    'moulding': {'worker_id','lot_id','quantity_bricks','rate_per_1000_paise','receipt_id'},
    'work_approval': {'work_id'},
    'transfer': {'lot_id','from_bucket','to_bucket','quantity_bricks'},
    'grading': {'lot_id','from_bucket','quantity_bricks','grades'},
    'bill': {'supplier_id','description','amount_paise'},
    'worker_payment': {'work_id','amount_paise'},
    'bill_payment': {'bill_id','amount_paise'},
    'customer_receipt': {'buyer_id','amount_paise'},
    'dispatch_sale': {'lot_id','dispatch_ref','buyer_id','grade','quantity_bricks','unit_price_paise'},
    'allocation': {'receipt_id','sale_id','amount_paise'},
    'correction_request': {'bill_id','old_amount_paise','new_amount_paise','reason','evidence_ref'},
    'correction_review': {'correction_id','decision','reason'},
    'loss': {'lot_id','from_bucket','quantity_bricks','reason'},
    'internal_use': {'lot_id','from_bucket','quantity_bricks','reason'},
    'return': {'sale_id','quantity_bricks','inspection_note'},
    'return_inspection': {'return_id','quantity_bricks','grades','inspection_note'},
}
KINDS.update(accounts.FIELDS)
DATA_FIELDS.update(accounts.FIELDS)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    require(isinstance(value, str) and value == value.strip() and bool(value), 'A nonempty text reference is required')
    return value


def integer(value, *, positive=False):
    require(type(value) is int and (value > 0 if positive else value >= 0) and value <= MAX_INT,
            'Use a bounded integer; missing values, floats and booleans are not amounts or counts')
    return value


def day(value):
    text(value)
    require(date.fromisoformat(value).isoformat() == value, 'Use YYYY-MM-DD dates')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def earnings(quantity, rate):
    integer(quantity, positive=True)
    integer(rate, positive=True)
    require(quantity * rate <= MAX_INT - 500, 'Amount exceeds safe integer arithmetic')
    return (quantity * rate + 500) // 1000


@contextmanager
def transaction(db, *, write=True):
    require(not db.in_transaction, 'Nested transaction is not supported')
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


def insert(db, table, values):
    # Table/column names are internal constants, never supplied as executable SQL.
    require(table in TABLES, 'Unknown table')
    names = ','.join(values)
    db.execute(f'INSERT INTO {table}({names}) VALUES({",".join("?" for _ in values)})', tuple(values.values()))


def validate_setup(setup):
    require(setup['evidence_status'] == 'fictional', 'Only fictional setup is accepted')
    text(setup['plant_id'])
    require(day(setup['period_start']) <= day(setup['period_end']), 'Invalid reporting period')
    require(setup.get('finance_mode') in (None,'reviewed_accounts'),'Unknown finance setup mode')
    reviewed = setup.get('finance_mode') == 'reviewed_accounts'
    if reviewed:
        require(setup.get('opening_cash_paise') is None,'Review named account openings; pooled cash must stay unknown')
    else:
        integer(setup['opening_cash_paise'])
    require(setup['currency'] == 'INR', 'Only INR is supported')
    for field in ('opening_worker_outstanding_paise', 'opening_supplier_outstanding_paise', 'opening_customer_balance_paise'):
        require((setup.get(field) is None) if reviewed else (type(setup[field]) is int and setup[field] == 0),
                'This example requires explicit zero opening obligations and customer balance')
    require(bool(setup['parties']) and bool(setup['lots']), 'Parties and lots are required')
    require(len({p['party_id'] for p in setup['parties']}) == len(setup['parties']), 'Duplicate party ID')
    require(len({lot['lot_id'] for lot in setup['lots']}) == len(setup['lots']), 'Duplicate lot ID')
    for party in setup['parties']:
        text(party['party_id'])
        text(party['name'])
        require(party['role'] in {'owner', 'accountant', 'worker', 'buyer', 'supplier'}, 'Unknown role')
    for lot in setup['lots']:
        text(lot['lot_id'])
        require(set(lot['opening_stock']) == set(ACTIVE), 'Opening stock must explicitly cover every active bucket')
        for count in lot['opening_stock'].values():
            integer(count)


def preserve_guards(db):
    """Prevent ordinary row edits, including on tables added by a copy upgrade."""
    existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in TABLES:
        if table not in existing:
            continue
        for verb in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS keep_{table}_{verb.lower()} BEFORE {verb} ON {table} "
                       "BEGIN SELECT RAISE(ABORT, 'Preserve original records; append a supported event'); END")


def schema_version(db):
    return db.execute('PRAGMA user_version').fetchone()[0]


def saved_tables(db):
    return TABLES if schema_version(db) >= 3 else V2_TABLES if schema_version(db) == 2 else LEGACY_TABLES


def open_db(path, setup=None, *, readonly=False):
    """A separate application ID keeps earlier lessons and unrelated DBs untouched."""
    require(sqlite3.sqlite_version_info >= (3, 37), 'SQLite 3.37+ is required')
    require(not (readonly and setup is not None), 'Read-only cannot initialise a database')
    if setup is not None:
        validate_setup(setup)
    mode = 'ro' if readonly else ('rwc' if setup is not None else 'rw')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=' + mode, uri=True,
                         isolation_level=None, timeout=5, autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL)
    db.row_factory = sqlite3.Row
    try:
        app = db.execute('PRAGMA application_id').fetchone()[0]
        version = db.execute('PRAGMA user_version').fetchone()[0]
        objects = db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        fresh = setup is not None and app == version == 0 and not objects
        require(fresh or (app == APP_ID and version in (1, 2, VERSION)),
                'Not a Chapter 3 database; no conversion or overwrite')
        db.execute('PRAGMA foreign_keys=ON')
        require(db.execute('PRAGMA foreign_keys').fetchone()[0] == 1, 'Foreign keys must be enabled')
        if not readonly:
            require(db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete', 'Rollback journal is required')
            db.execute('PRAGMA synchronous=FULL')
        if fresh:
            db.executescript('BEGIN IMMEDIATE;\n' + (HERE / 'schema.sql').read_text(encoding='utf-8'))
            try:
                insert(db, 'context', {'id': 1, 'plant_id': setup['plant_id'], 'period_start': setup['period_start'],
                      'period_end': setup['period_end'], 'opening_cash_paise': setup.get('opening_cash_paise'),
                      'evidence_status': 'fictional', 'setup_json': canonical(setup)})
                for party in setup['parties']:
                    insert(db, 'parties', party)
                for bucket in BUCKETS:
                    insert(db, 'buckets', {'bucket': bucket, 'on_hand': int(bucket in ACTIVE)})
                for lot in setup['lots']:
                    insert(db, 'lots', {'lot_id': lot['lot_id']})
                    for bucket in BUCKETS:
                        insert(db, 'stock_openings', {'lot_id': lot['lot_id'], 'bucket': bucket,
                              'quantity_bricks': lot['opening_stock'][bucket] if bucket in ACTIVE else 0})
                if not accounts.reviewed_mode(db):
                    accounts.initialize_legacy(db)
                # These guards prevent accidental edits, not a database administrator's tampering.
                preserve_guards(db)
                db.execute(f'PRAGMA application_id={APP_ID}')
                db.execute(f'PRAGMA user_version={VERSION}')
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
        elif setup is not None:
            require(db.execute('SELECT setup_json FROM context').fetchone()[0] == canonical(setup), 'Existing setup differs; no overwrite')
        return db
    except BaseException:
        db.close()
        raise


def upgrade_copy(source, destination):
    """Explicitly upgrade a NEW copy; never migrate or overwrite the source."""
    destination = Path(destination)
    require(not destination.exists(), 'Upgrade destination already exists; no overwrite')
    with closing(open_db(source, readonly=True)) as original:
        original_version = schema_version(original)
        require(original_version in (1,2), 'Only version-1 or version-2 copies need this upgrade')
        expected = snapshot(original)
        with destination.open('xb'):
            pass
        with closing(sqlite3.connect(destination, isolation_level=None)) as copy:
            copy.row_factory = sqlite3.Row
            original.backup(copy)
            require(copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Copied database is damaged')
            copy.execute('PRAGMA foreign_keys=OFF')
            copy.execute('PRAGMA synchronous=FULL')
            statements = [s.strip() for s in (HERE/'schema.sql').read_text(encoding='utf-8').split(';')]
            with transaction(copy):
                if original_version == 1:
                    definition = next(s for s in statements if s.startswith('CREATE TABLE bill_corrections ('))
                    copy.execute(definition.replace('CREATE TABLE bill_corrections (', 'CREATE TABLE corrections_upgrade (', 1))
                    copy.execute('INSERT INTO corrections_upgrade SELECT * FROM bill_corrections ORDER BY rowid')
                    copy.execute('DROP TABLE bill_corrections')
                    copy.execute('ALTER TABLE corrections_upgrade RENAME TO bill_corrections')
                    copy.execute(next(s for s in statements if s.startswith('CREATE TABLE return_inspections (')))
                    copy.execute('CREATE INDEX corrections_bill ON bill_corrections(bill_id)')
                    copy.execute('CREATE INDEX inspections_return ON return_inspections(return_id)')
                definition = next(s[s.index('CREATE TABLE context ('):] for s in statements if 'CREATE TABLE context (' in s)
                copy.execute(definition.replace('CREATE TABLE context (','CREATE TABLE context_upgrade (',1))
                copy.execute('INSERT INTO context_upgrade SELECT * FROM context ORDER BY rowid')
                copy.execute('DROP TABLE context')
                copy.execute('ALTER TABLE context_upgrade RENAME TO context')
                for table in accounts.TABLES:
                    copy.execute(next(s for s in statements if s.startswith('CREATE TABLE '+table+' (')))
                for name in ('account_money','opening_payments','opening_matches'):
                    copy.execute(next(s for s in statements if s.startswith('CREATE INDEX '+name+' ')))
                accounts.initialize_legacy(copy)
                preserve_guards(copy)
                copy.execute(f'PRAGMA user_version={VERSION}')
                require(not copy.execute('PRAGMA foreign_key_check').fetchall(), 'Upgrade broke record references')
                _report(copy)
                for table, rows in expected['tables'].items():
                    actual = [dict(r) for r in copy.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    require(actual == rows, 'Upgrade changed original records')
            copy.execute('PRAGMA foreign_keys=ON')
            require(copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Upgraded database is damaged')
    with closing(open_db(destination, readonly=True)) as reopened:
        require(schema_version(reopened) == VERSION, 'Upgrade did not reopen as current version')
        snapshot(reopened)
    return destination


def party_role(db, party_id, roles):
    row = db.execute('SELECT role FROM parties WHERE party_id=?', (text(party_id),)).fetchone()
    require(row is not None and row['role'] in roles, 'Unknown party or unsuitable demonstration role')


def referenced(db, table, key, value):
    require(table in TABLES, 'Unknown table')
    row = db.execute(f'SELECT * FROM {table} WHERE {key}=?', (text(value),)).fetchone()
    require(row is not None, f'Unknown {table} reference: {value}')
    return row


def stock_rows(db):
    return [dict(r) for r in db.execute('''
        SELECT lot_id,bucket,SUM(quantity_bricks) AS quantity_bricks FROM (
            SELECT lot_id,bucket,quantity_bricks FROM stock_openings
            UNION ALL
            SELECT m.lot_id,l.bucket,l.delta_bricks FROM stock_movements m
            JOIN stock_lines l ON l.movement_id=m.movement_id
        ) GROUP BY lot_id,bucket ORDER BY lot_id,bucket''')]


def quantity_available(db, lot_id, bucket):
    referenced(db, 'lots', 'lot_id', lot_id)
    require(bucket in BUCKETS, 'Unknown stock bucket')
    return next(r['quantity_bricks'] for r in stock_rows(db) if r['lot_id'] == lot_id and r['bucket'] == bucket)


def movement(db, event, movement_id, lot_id, lines, *, work_id=None):
    referenced(db, 'lots', 'lot_id', lot_id)
    insert(db, 'stock_movements', {'movement_id': text(movement_id), 'event_id': event['event_id'],
          'lot_id': lot_id, 'work_id': work_id, 'purpose': event['kind']})
    for bucket, delta in lines.items():
        require(bucket in BUCKETS and type(delta) is int and 0 < abs(delta) <= MAX_INT, 'Invalid stock line')
        insert(db, 'stock_lines', {'movement_id': movement_id, 'bucket': bucket, 'delta_bricks': delta})


def bill_state(db, bill_id):
    row = referenced(db, 'bills', 'bill_id', bill_id)
    correction = db.execute('''SELECT c.new_amount_paise FROM bill_corrections c
        JOIN correction_reviews r ON r.correction_id=c.correction_id
        WHERE c.bill_id=? AND r.decision='approved' ''', (bill_id,)).fetchone()
    amount = correction['new_amount_paise'] if correction else row['original_amount_paise']
    paid = db.execute('SELECT COALESCE(SUM(amount_paise),0) FROM cash_entries WHERE bill_id=?', (bill_id,)).fetchone()[0]
    return dict(row, current_amount_paise=amount, paid_paise=paid, outstanding_paise=amount-paid)


def return_rows(db):
    inspected = ("(SELECT COALESCE(SUM(i.quantity_bricks),0) FROM return_inspections i "
                 "WHERE i.return_id=r.return_id)") if schema_version(db) >= 2 else '0'
    rows = [dict(r) for r in db.execute(f'''SELECT r.*,m.lot_id,{inspected} AS inspected_bricks
        FROM stock_returns r JOIN sales s ON s.sale_id=r.sale_id
        JOIN stock_movements m ON m.movement_id=s.dispatch_ref ORDER BY r.rowid''')]
    for row in rows:
        row['remaining_bricks'] = row['quantity_bricks'] - row['inspected_bricks']
        require(row['remaining_bricks'] >= 0, 'Return inspected more than once')
    return rows


def _report(db):
    context = dict(db.execute('SELECT * FROM context').fetchone())
    require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'Broken record references')
    stock = stock_rows(db)
    require(all(r['quantity_bricks'] >= 0 for r in stock), 'Stock would become negative')
    work = [dict(r) for r in db.execute('''SELECT w.*,e.business_date,a.approval_id,
        (SELECT COALESCE(SUM(amount_paise),0) FROM cash_entries c WHERE c.work_id=w.work_id) AS paid_paise
        FROM work_credits w JOIN events e ON e.event_id=w.work_id
        LEFT JOIN work_approvals a ON a.work_id=w.work_id ORDER BY w.work_id''')]
    for row in work:
        row['outstanding_paise'] = row['earned_paise'] - row['paid_paise']
        require(row['outstanding_paise'] >= 0, 'Worker overpayment')
    bills = [bill_state(db, r[0]) for r in db.execute('SELECT bill_id FROM bills ORDER BY bill_id')]
    require(all(b['outstanding_paise'] >= 0 for b in bills), 'Bill overpayment')
    cash = [dict(r) for r in db.execute('''SELECT c.*,e.business_date,e.actor_id,e.source_ref
        FROM cash_entries c JOIN events e ON e.event_id=c.cash_id ORDER BY e.business_date,c.cash_id''')]
    account_balances = accounts.account_rows(db)
    fund_totals = accounts.totals(account_balances)
    if schema_version(db) >= 3:
        links = {r['cash_id']:r['account_id'] for r in db.execute('SELECT * FROM cash_entry_accounts')}
        for entry in cash:
            entry['account_id'] = links[entry['cash_id']]
    sales = [dict(r) for r in db.execute('''SELECT s.*,
        (SELECT COALESCE(SUM(amount_paise),0) FROM receipt_allocations a WHERE a.sale_id=s.sale_id) AS allocated_paise,
        (SELECT COALESCE(SUM(quantity_bricks),0) FROM stock_returns r WHERE r.sale_id=s.sale_id) AS returned_bricks
        FROM sales s ORDER BY sale_id''')]
    for sale in sales:
        sale['allocated_paise'] = accounts.sale_allocated(db,sale['sale_id'])
        sale['unsettled_paise'] = sale['amount_paise'] - sale['allocated_paise']
        require(sale['unsettled_paise'] >= 0,'Sale over-allocation')
        sale['return_financial_review_needed'] = sale['returned_bricks'] > 0
    advances = [dict(r) for r in db.execute('''SELECT c.cash_id,c.buyer_id,c.amount_paise,
        c.amount_paise-(SELECT COALESCE(SUM(amount_paise),0) FROM receipt_allocations a WHERE a.receipt_id=c.cash_id) AS unallocated_paise
        FROM cash_entries c WHERE c.kind='customer_receipt' ORDER BY c.cash_id''')]
    corrections = [dict(r) for r in db.execute('''SELECT c.*,e.actor_id AS requested_by,e.business_date AS requested_on,
        r.review_id,r.decision,re.actor_id AS reviewed_by,re.business_date AS reviewed_on
        FROM bill_corrections c JOIN events e ON e.event_id=c.correction_id
        LEFT JOIN correction_reviews r ON r.correction_id=c.correction_id
        LEFT JOIN events re ON re.event_id=r.review_id ORDER BY c.correction_id''')]
    for bill in bills:
        proposals = [c for c in corrections if c['bill_id'] == bill['bill_id']]
        approved = sum(c['decision'] == 'approved' for c in proposals)
        pending = sum(c['decision'] is None for c in proposals)
        require(approved <= 1 and pending <= 1 and not (approved and pending),
                'Conflicting live bill corrections')
    returns = return_rows(db)
    if schema_version(db) >= 2:
        for lot_id in {r['lot_id'] for r in returns}:
            reserved = sum(r['remaining_bricks'] for r in returns if r['lot_id'] == lot_id)
            require(quantity_available(db, lot_id, 'unclassified') >= reserved,
                    'Uninspected returns must remain in unclassified stock')
    return {
        'schema_version': schema_version(db),
        'evidence_status': 'fictional', 'currency': 'INR', 'money_unit': 'paise', 'stock_unit': 'brick',
        'plant_id': context['plant_id'], 'period_start': context['period_start'], 'period_end': context['period_end'],
        'opening_cash_paise': context['opening_cash_paise'], **fund_totals,
        'account_balances': account_balances,
        'party_balances': accounts.party_balances(db,work,bills,sales,advances),
        'party_openings': accounts.opening_rows(db),
        'finance_mode': 'reviewed_accounts' if accounts.reviewed_mode(db) else 'legacy_carried',
        'openings_locked': accounts.locked(db),
        'opening_settlements': [dict(r) for r in db.execute('SELECT * FROM opening_settlements ORDER BY rowid')] if schema_version(db)>=3 else [],
        'stock_bricks': sum(r['quantity_bricks'] for r in stock if r['bucket'] in ACTIVE),
        'earned_paise': sum(w['earned_paise'] for w in work),
        'approved_paise': sum(w['earned_paise'] for w in work if w['approval_id'] is not None),
        'worker_paid_paise': sum(w['paid_paise'] for w in work),
        'worker_outstanding_paise': sum(w['outstanding_paise'] for w in work),
        'bill_outstanding_paise': sum(b['outstanding_paise'] for b in bills),
        'sales_face_paise': sum(s['amount_paise'] for s in sales),
        'unallocated_customer_receipts_paise': sum(a['unallocated_paise'] for a in advances),
        'stock': stock, 'work': work, 'bills': bills, 'cash_entries': cash, 'sales': sales,
        'customer_receipts': advances, 'corrections': corrections, 'returns': returns,
        'event_count': db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
        'profit_paise': None, 'tax_breakdown_paise': None,
        'limitations': ['Fictional records only', 'Sales are recorded face amounts; tax is not decomposed',
                        'Inventory valuation and complete costs are absent', 'Returns need separate financial review'],
    }


def report(db):
    with transaction(db, write=False):
        return _report(db)


def snapshot(db):
    with transaction(db, write=False):
        return {'report': _report(db), 'tables': {
            table: [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in saved_tables(db)}}


def apply_event(db, event):
    """Internal writer; called only inside the event's single transaction."""
    kind, eid, d = event['kind'], event['event_id'], event['data']
    party_role(db, event['actor_id'], {'owner', 'accountant'})
    if kind in accounts.FIELDS:
        accounts.apply(db,event)
    elif kind == 'moulding':
        party_role(db, d['worker_id'], {'worker'})
        amount = earnings(d['quantity_bricks'], d['rate_per_1000_paise'])
        insert(db, 'work_credits', {'work_id': eid, 'worker_id': d['worker_id'], 'lot_id': d['lot_id'],
              'quantity_bricks': d['quantity_bricks'], 'rate_per_1000_paise': d['rate_per_1000_paise'], 'earned_paise': amount})
        movement(db, event, d['receipt_id'], d['lot_id'], {'moulded': d['quantity_bricks']}, work_id=eid)
    elif kind == 'work_approval':
        party_role(db, event['actor_id'], {'owner'})
        referenced(db, 'work_credits', 'work_id', d['work_id'])
        insert(db, 'work_approvals', {'approval_id': eid, 'work_id': d['work_id']})
    elif kind in {'transfer', 'grading', 'loss', 'internal_use'}:
        count = integer(d['quantity_bricks'], positive=True)
        origin = d['from_bucket']
        require(origin in ACTIVE, 'A movement needs an active source bucket')
        if kind == 'transfer':
            require((origin, d['to_bucket']) in {('moulded','dry_green'), ('dry_green','loaded'), ('loaded','cooling')}, 'Invalid stage transition')
            outputs = {d['to_bucket']: count}
        elif kind == 'grading':
            require(origin == 'cooling' and isinstance(d['grades'], dict) and bool(d['grades'])
                    and set(d['grades']) <= set(GRADES), 'Grade only cooled bricks into explicit categories')
            outputs = {g: integer(n, positive=True) for g,n in d['grades'].items()}
            require(sum(outputs.values()) == count, 'Grade counts must reconcile; keep unknown quantities unclassified')
        else:
            party_role(db, event['actor_id'], {'owner'})
            text(d['reason'])
            outputs = {kind: count}
        require(quantity_available(db, d['lot_id'], origin) >= count, 'Insufficient stock')
        movement(db, event, eid, d['lot_id'], {origin: -count, **outputs})
    elif kind == 'bill':
        party_role(db, d['supplier_id'], {'supplier'})
        insert(db, 'bills', {'bill_id': eid, 'supplier_id': d['supplier_id'], 'description': text(d['description']),
              'original_amount_paise': integer(d['amount_paise'], positive=True)})
    elif kind in {'worker_payment', 'bill_payment', 'customer_receipt'}:
        amount = integer(d['amount_paise'], positive=True)
        account_id = accounts.select_account(db,d,payment=kind!='customer_receipt')
        values = {'cash_id': eid, 'kind': kind, 'amount_paise': amount}
        if kind == 'worker_payment':
            work = referenced(db, 'work_credits', 'work_id', d['work_id'])
            require(db.execute('SELECT 1 FROM work_approvals WHERE work_id=?', (d['work_id'],)).fetchone() is not None,
                    'Owner approval is required before payment')
            paid = db.execute('SELECT COALESCE(SUM(amount_paise),0) FROM cash_entries WHERE work_id=?', (d['work_id'],)).fetchone()[0]
            require(amount <= work['earned_paise']-paid, 'Worker overpayment')
            values['work_id'] = d['work_id']
        elif kind == 'bill_payment':
            bill = bill_state(db, d['bill_id'])
            require(amount <= bill['outstanding_paise'], 'Bill overpayment')
            values['bill_id'] = d['bill_id']
            if d.get('correction_id') is not None:
                correction = referenced(db, 'bill_corrections', 'correction_id', d['correction_id'])
                review = db.execute('SELECT decision FROM correction_reviews WHERE correction_id=?', (d['correction_id'],)).fetchone()
                require(correction['bill_id'] == d['bill_id'] and review is not None and review['decision'] == 'approved', 'Correction settlement needs approval for this bill')
                values['correction_id'] = d['correction_id']
        else:
            party_role(db, d['buyer_id'], {'buyer'})
            values['buyer_id'] = d['buyer_id']
        insert(db, 'cash_entries', values)
        if schema_version(db) >= 3:
            insert(db,'cash_entry_accounts',dict(cash_id=eid,account_id=account_id))
    elif kind == 'dispatch_sale':
        party_role(db, d['buyer_id'], {'buyer'})
        count, price = integer(d['quantity_bricks'], positive=True), integer(d['unit_price_paise'], positive=True)
        require(count * price <= MAX_INT, 'Sale amount exceeds integer range')
        require(d['grade'] in GRADES and d['grade'] != 'unclassified', 'Only classified whole bricks can be sold here')
        require(quantity_available(db, d['lot_id'], d['grade']) >= count, 'Insufficient stock')
        movement(db, event, d['dispatch_ref'], d['lot_id'], {d['grade']: -count, 'dispatched': count})
        insert(db, 'sales', {'sale_id': eid, 'dispatch_ref': d['dispatch_ref'], 'buyer_id': d['buyer_id'],
              'grade': d['grade'], 'quantity_bricks': count, 'unit_price_paise': price, 'amount_paise': count*price})
    elif kind == 'allocation':
        receipt = referenced(db, 'cash_entries', 'cash_id', d['receipt_id'])
        sale = referenced(db, 'sales', 'sale_id', d['sale_id'])
        require(receipt['kind'] == 'customer_receipt' and receipt['buyer_id'] == sale['buyer_id'], 'Receipt and sale must belong to the same buyer')
        amount = integer(d['amount_paise'], positive=True)
        receipt_used = db.execute('SELECT COALESCE(SUM(amount_paise),0) FROM receipt_allocations WHERE receipt_id=?', (d['receipt_id'],)).fetchone()[0]
        sale_used = accounts.sale_allocated(db,d['sale_id'])
        require(amount <= receipt['amount_paise']-receipt_used and amount <= sale['amount_paise']-sale_used, 'Allocation exceeds unallocated receipt or unsettled sale')
        insert(db, 'receipt_allocations', {'allocation_id': eid, 'receipt_id': d['receipt_id'], 'sale_id': d['sale_id'], 'amount_paise': amount})
    elif kind == 'correction_request':
        bill = bill_state(db, d['bill_id'])
        previous = db.execute('''SELECT r.decision FROM bill_corrections c
            LEFT JOIN correction_reviews r ON r.correction_id=c.correction_id WHERE c.bill_id=?''',
            (d['bill_id'],)).fetchall()
        require(not any(r['decision'] is None for r in previous), 'A pending correction already exists for this bill')
        require(not any(r['decision'] == 'approved' for r in previous), 'This bill already has an approved correction')
        require(not previous or schema_version(db) >= 2,
                'Legacy database: upgrade a copy before revising a rejected correction')
        old = integer(d['old_amount_paise'], positive=True)
        new = integer(d['new_amount_paise'], positive=True)
        require(old == bill['current_amount_paise'], 'Stale original bill amount')
        require(old != new and new >= bill['paid_paise'], 'Correction cannot create an overpayment; use a later refund workflow')
        insert(db, 'bill_corrections', {'correction_id': eid, 'bill_id': d['bill_id'], 'old_amount_paise': old,
              'new_amount_paise': new, 'reason': text(d['reason']), 'evidence_ref': text(d['evidence_ref'])})
    elif kind == 'correction_review':
        party_role(db, event['actor_id'], {'owner'})
        correction = referenced(db, 'bill_corrections', 'correction_id', d['correction_id'])
        request = referenced(db, 'events', 'event_id', d['correction_id'])
        require(request['actor_id'] != event['actor_id'], 'Requester cannot review their own correction')
        require(d['decision'] in {'approved', 'rejected'}, 'Unknown correction decision')
        text(d['reason'])
        if d['decision'] == 'approved':
            bill = bill_state(db, correction['bill_id'])
            require(bill['current_amount_paise'] == correction['old_amount_paise'], 'Stale correction')
            require(correction['new_amount_paise'] >= bill['paid_paise'], 'Correction cannot create an overpayment')
        insert(db, 'correction_reviews', {'review_id': eid, 'correction_id': d['correction_id'], 'decision': d['decision'], 'reason': d['reason']})
    elif kind == 'return':
        sale = referenced(db, 'sales', 'sale_id', d['sale_id'])
        count = integer(d['quantity_bricks'], positive=True)
        returned = db.execute('SELECT COALESCE(SUM(quantity_bricks),0) FROM stock_returns WHERE sale_id=?', (d['sale_id'],)).fetchone()[0]
        require(count <= sale['quantity_bricks']-returned, 'Return exceeds the original dispatched quantity')
        dispatch = referenced(db, 'stock_movements', 'movement_id', sale['dispatch_ref'])
        movement(db, event, eid, dispatch['lot_id'], {'dispatched': -count, 'unclassified': count})
        insert(db, 'stock_returns', {'return_id': eid, 'sale_id': d['sale_id'], 'quantity_bricks': count, 'inspection_note': text(d['inspection_note'])})
    elif kind == 'return_inspection':
        require(schema_version(db) >= 2, 'Legacy database: upgrade a copy before inspecting returns')
        party_role(db, event['actor_id'], {'owner'})
        returned = next((r for r in return_rows(db) if r['return_id'] == text(d['return_id'])), None)
        require(returned is not None, 'Unknown return reference')
        count = integer(d['quantity_bricks'], positive=True)
        require(count <= returned['remaining_bricks'], 'Inspection exceeds this return remaining quantity')
        require(isinstance(d['grades'], dict) and bool(d['grades'])
                and set(d['grades']) <= set(GRADES)-{'unclassified'},
                'Inspect returns into explicit saleable grades')
        grades = {g:integer(n,positive=True) for g,n in d['grades'].items()}
        require(sum(grades.values()) == count, 'Inspected grade counts must reconcile')
        require(quantity_available(db, returned['lot_id'], 'unclassified') >= count, 'Insufficient unclassified stock')
        insert(db, 'return_inspections', {'inspection_id':eid, 'return_id':d['return_id'],
              'quantity_bricks':count, 'inspection_note':text(d['inspection_note'])})
        movement(db, event, eid, returned['lot_id'], {'unclassified':-count, **grades})


def record_event(db, event):
    """Save header and linked records atomically; identical retry is a no-op."""
    required = {'event_id','request_id','kind','business_date','actor_id','source_ref','evidence_status','data'}
    require(isinstance(event, dict) and set(event) == required and isinstance(event['data'], dict), 'Use the documented event fields')
    for field in required-{'data'}:
        text(event[field])
    require(event['evidence_status'] == 'fictional', 'Only fictional records are accepted')
    require(event['kind'] in KINDS, 'Unsupported event kind')
    fields = DATA_FIELDS[event['kind']]
    optional = {'account_id'} if event['kind'] in {'worker_payment','bill_payment','customer_receipt'} else set()
    if event['kind'] == 'bill_payment':
        optional.add('correction_id')
    require(fields <= set(event['data']) <= fields | optional, 'Missing or unexpected event data fields')
    day(event['business_date'])
    encoded = canonical(event)
    with transaction(db):
        existing = db.execute('SELECT payload_json FROM events WHERE event_id=? OR request_id=? OR source_ref=?',
                              (event['event_id'],event['request_id'],event['source_ref'])).fetchall()
        if existing:
            require(len(existing) == 1 and existing[0]['payload_json'] == encoded, 'Conflicting duplicate; original records preserved')
            return 'already_recorded'
        if schema_version(db) >= 3 and accounts.reviewed_mode(db) and event['kind'] not in accounts.SETUP_KINDS:
            require(accounts.locked(db),'Owner must lock the reviewed account openings before posting')
        context = db.execute('SELECT period_start,period_end FROM context').fetchone()
        require(context['period_start'] <= event['business_date'] <= context['period_end'], 'Event outside reporting period')
        latest = db.execute('SELECT MAX(business_date) FROM events').fetchone()[0]
        require(latest is None or event['business_date'] >= latest, 'Backdated insertion requires a later review workflow')
        insert(db, 'events', {**{k:event[k] for k in required-{'data'}}, 'payload_json': encoded})
        apply_event(db, event)
        _report(db)  # Validate totals before committing either header or children.
        return 'recorded'


def backup_and_verify(db, destination):
    """Create a new SQLite backup and reopen it; refuse to overwrite any file."""
    destination = Path(destination)
    require(not destination.exists(), 'Backup destination already exists; no overwrite')
    expected = snapshot(db)
    # Exclusive creation also prevents accidentally opening an unrelated existing file.
    with destination.open('xb'):
        pass
    target = sqlite3.connect(destination)
    try:
        db.backup(target)
    finally:
        target.close()
    restored = open_db(destination, readonly=True)
    try:
        require(restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Backup integrity check failed')
        require(snapshot(restored) == expected, 'Restored backup differs from the saved records')
    finally:
        restored.close()
    return expected


def rupees(paise):
    return f'INR {paise // 100:,}.{paise % 100:02d}'
