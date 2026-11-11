"""Synthetic account/opening checks; never inspect or change saved plant data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import demo
import ledger


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ledger.HERE)
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,ledger.HERE.resolve())
        self.addCleanup(self.temp.cleanup)
        self.path = self.root/'accounts.sqlite3'
        self.setup, _ = demo.example()
        self.setup['finance_mode'] = 'reviewed_accounts'
        for key in ('opening_cash_paise','opening_worker_outstanding_paise',
                    'opening_supplier_outstanding_paise','opening_customer_balance_paise'):
            self.setup[key] = None
        self.setup['parties'].append({'party_id':'BUYER-2','name':'Fictional second buyer','role':'buyer'})
        self.setup['lots'][0]['opening_stock']['first'] = 1000
        self.db = ledger.open_db(self.path,self.setup)
        self.addCleanup(self.db.close)

    def event(self, identifier, kind, data, owner=False, date='2026-03-01'):
        return dict(event_id=identifier,request_id='REQUEST-'+identifier,kind=kind,
                    business_date=date,actor_id='DEMO-OWNER' if owner else 'DEMO-ACCOUNTANT',
                    source_ref='fictional-finance/'+identifier,evidence_status='fictional',data=data)

    def add(self, identifier, kind, data, owner=False, date='2026-03-01'):
        return ledger.record_event(self.db,self.event(identifier,kind,data,owner,date))

    def account(self, identifier='CASH', amount=100000, kind='cash'):
        self.add('REGISTER-'+identifier,'account_register',
                 dict(account_id=identifier,name='Fictional '+identifier,account_type=kind,
                      review_note='Internal account label only'),owner=True)
        if amount is not None:
            self.add('OPEN-'+identifier,'account_opening',
                     dict(account_id=identifier,amount_paise=amount,opening_as_of=self.setup['period_start'],
                          review_note='Fictional owner count',evidence_ref='FICTIONAL-OPEN-'+identifier),owner=True)

    def opening(self, identifier, party, kind, amount):
        self.add(identifier,'party_opening',
                 dict(party_id=party,balance_kind=kind,amount_paise=amount,
                      opening_as_of=self.setup['period_start'],review_note='Fictional reviewed carryforward',
                      evidence_ref='FICTIONAL-'+identifier),owner=True)

    def lock(self):
        self.add('LOCK','openings_lock',dict(review_note='Owner starts the fictional posting period'),owner=True)

    def test_unknown_and_confirmed_zero_openings_remain_distinct(self):
        self.account(amount=None)
        report = ledger.report(self.db)
        self.assertIsNone(report['account_balances'][0]['closing_paise'])
        self.assertIsNone(report['cash_paise'])
        self.assertTrue(all(row['total_balance_paise'] is None for row in report['party_balances']))
        with self.assertRaisesRegex(ValueError,'opening'):
            self.lock()
        self.add('OPEN-CASH','account_opening',dict(account_id='CASH',amount_paise=0,
            opening_as_of=self.setup['period_start'],review_note='Fictional zero verified',evidence_ref='ZERO'),owner=True)
        self.assertEqual(ledger.report(self.db)['cash_paise'],0)
        self.assertEqual(ledger.report(self.db)['account_balances'][0]['source_basis'],'reviewed')

    def test_fresh_empty_catalogue_and_partial_account_openings_stay_unknown(self):
        report = ledger.report(self.db)
        self.assertEqual(report['account_balances'],[])
        self.assertIsNone(report['cash_paise'])
        self.assertIsNone(report['funds_paise'])
        self.assertFalse(report['openings_locked'])
        self.account('CASH',100000)
        self.account('BANK',None,'bank')
        report = ledger.report(self.db)
        self.assertEqual(report['cash_paise'],100000)
        self.assertIsNone(report['bank_paise'])
        self.assertIsNone(report['funds_paise'])
        self.assertEqual(report['known_funds_subtotal_paise'],100000)
        with self.assertRaisesRegex(ValueError,'opening'):
            self.lock()

    def test_reviewed_zero_openings_cannot_be_replaced(self):
        self.account(amount=0)
        self.opening('ZERO-OWED','WORKER-001','worker_payable',0)
        before = ledger.snapshot(self.db)
        with self.assertRaises((ValueError,sqlite3.IntegrityError)):
            self.add('REPLACE-CASH','account_opening',dict(account_id='CASH',amount_paise=100,
                opening_as_of=self.setup['period_start'],review_note='Unsupported replacement',evidence_ref='OTHER'),owner=True)
        with self.assertRaises((ValueError,sqlite3.IntegrityError)):
            self.opening('REPLACE-OWED','WORKER-001','worker_payable',100)
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_current_receipt_then_opening_advance_cannot_overallocate_sale(self):
        self.account()
        self.opening('ADVANCE','DEMO-BUYER','customer_advance',50000)
        self.lock()
        self.sale()
        self.add('RECEIPT','customer_receipt',dict(buyer_id='DEMO-BUYER',account_id='CASH',amount_paise=30000))
        self.add('CURRENT-MATCH','allocation',dict(receipt_id='RECEIPT',sale_id='SALE',amount_paise=30000))
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('OVER','opening_advance_allocation',dict(opening_id='ADVANCE',sale_id='SALE',amount_paise=20001))
        self.assertEqual(ledger.snapshot(self.db),before)
        self.add('OPENING-MATCH','opening_advance_allocation',dict(opening_id='ADVANCE',sale_id='SALE',amount_paise=20000))
        self.assertEqual(ledger.report(self.db)['sales'][0]['unsettled_paise'],0)

    def test_opening_and_registration_need_owner_evidence_and_valid_date(self):
        self.account(amount=None)
        data = dict(account_id='CASH',amount_paise=0,opening_as_of=self.setup['period_start'],
                    review_note='Fictional count',evidence_ref='SLIP')
        for change, owner in [(data,False),(dict(data,evidence_ref=''),True),
                              (dict(data,amount_paise=None),True),(dict(data,amount_paise=True),True),
                              (dict(data,opening_as_of='2026-02-02'),True)]:
            before = ledger.snapshot(self.db)
            with self.assertRaises(ValueError):
                self.add('BAD','account_opening',change,owner=owner)
            self.assertEqual(ledger.snapshot(self.db),before)

    def test_accounts_require_explicit_selection_and_separate_funds(self):
        self.account('CASH',100000)
        self.account('BANK',0,'bank')
        self.lock()
        self.add('BILL','bill',dict(supplier_id='DEMO-SUPPLIER',description='Fictional current bill',amount_paise=20000))
        before = ledger.snapshot(self.db)
        for data in [dict(bill_id='BILL',amount_paise=1000),
                     dict(bill_id='BILL',amount_paise=1000,account_id='BANK')]:
            with self.assertRaises(ValueError):
                self.add('BAD-PAY','bill_payment',data)
            self.assertEqual(ledger.snapshot(self.db),before)
        self.add('PAY','bill_payment',dict(bill_id='BILL',amount_paise=10000,account_id='CASH'))
        self.add('RECEIPT','customer_receipt',dict(buyer_id='DEMO-BUYER',amount_paise=50000,account_id='BANK'))
        report = ledger.report(self.db)
        self.assertEqual((report['cash_paise'],report['bank_paise'],report['funds_paise']),(90000,50000,140000))
        self.assertEqual(len(ledger.snapshot(self.db)['tables']['cash_entry_accounts']),2)

    def test_setup_lock_does_not_default_unknown_party_balances_to_zero(self):
        self.account()
        with self.assertRaisesRegex(ValueError,'lock'):
            self.add('BILL','bill',dict(supplier_id='DEMO-SUPPLIER',description='Before lock',amount_paise=2000))
        self.lock()
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.account('LATE-BANK',0,'bank')
        self.assertEqual(ledger.snapshot(self.db),before)
        self.opening('SUPPLIER-OPEN','DEMO-SUPPLIER','supplier_payable',30000)
        row = next(r for r in ledger.report(self.db)['party_balances'] if r['opening_id']=='SUPPLIER-OPEN')
        self.assertEqual(row['total_balance_paise'],30000)
        with self.assertRaises((ValueError,sqlite3.IntegrityError)):
            self.opening('REWRITE','DEMO-SUPPLIER','supplier_payable',40000)

    def test_opening_payable_settlement_is_not_a_current_bill_or_expense(self):
        self.account('CASH',100000)
        self.opening('SUPPLIER-OPEN','DEMO-SUPPLIER','supplier_payable',30000)
        self.lock()
        data = dict(opening_id='SUPPLIER-OPEN',account_id='CASH',amount_paise=10000)
        self.assertEqual(self.add('SETTLE','opening_settlement',data),'recorded')
        self.assertEqual(self.add('SETTLE','opening_settlement',data),'already_recorded')
        report = ledger.report(self.db)
        row = next(r for r in report['party_balances'] if r['opening_id']=='SUPPLIER-OPEN')
        self.assertEqual((report['cash_paise'],row['opening_remaining_paise'],report['bill_outstanding_paise']),
                         (90000,20000,0))
        self.assertEqual(report['bills'],[])
        self.assertEqual(report['sales_face_paise'],0)
        self.assertIsNone(report['profit_paise'])
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('OVERPAY','opening_settlement',dict(data,amount_paise=20001))
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_worker_opening_is_separate_from_new_work_earnings(self):
        self.account()
        self.opening('WORKER-OPEN','WORKER-001','worker_payable',25000)
        self.lock()
        self.add('OLD-WAGE-PAY','opening_settlement',
                 dict(opening_id='WORKER-OPEN',account_id='CASH',amount_paise=10000))
        report = ledger.report(self.db)
        row = next(r for r in report['party_balances'] if r['opening_id']=='WORKER-OPEN')
        self.assertEqual((report['earned_paise'],report['worker_paid_paise'],row['total_balance_paise']),(0,0,15000))

    def test_opening_receivable_collection_is_not_new_sale_or_reusable_receipt(self):
        self.account('BANK',0,'bank')
        self.opening('AR-OPEN','DEMO-BUYER','customer_receivable',50000)
        self.lock()
        self.add('COLLECT','opening_settlement',dict(opening_id='AR-OPEN',account_id='BANK',amount_paise=20000))
        report = ledger.report(self.db)
        row = next(r for r in report['party_balances'] if r['opening_id']=='AR-OPEN')
        self.assertEqual((report['bank_paise'],row['opening_remaining_paise'],report['sales_face_paise']),(20000,30000,0))
        self.assertEqual(report['customer_receipts'],[])
        self.assertEqual(report['unallocated_customer_receipts_paise'],0)

    def sale(self):
        self.add('SALE','dispatch_sale',dict(lot_id='LOT-DEMO-001',buyer_id='DEMO-BUYER',grade='first',
                 quantity_bricks=100,unit_price_paise=500,dispatch_ref='DISPATCH'))

    def test_opening_advance_and_current_receipt_share_sale_allocation_cap(self):
        self.account('BANK',100000,'bank')
        self.opening('ADVANCE-OPEN','DEMO-BUYER','customer_advance',50000)
        self.opening('AR-OPEN','DEMO-BUYER','customer_receivable',10000)
        self.lock()
        self.sale()
        self.add('ADVANCE-MATCH','opening_advance_allocation',
                 dict(opening_id='ADVANCE-OPEN',sale_id='SALE',amount_paise=30000))
        self.add('RECEIPT','customer_receipt',dict(buyer_id='DEMO-BUYER',account_id='BANK',amount_paise=30000))
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('OVER-MATCH','allocation',dict(receipt_id='RECEIPT',sale_id='SALE',amount_paise=20001))
        self.assertEqual(ledger.snapshot(self.db),before)
        self.add('MATCH','allocation',dict(receipt_id='RECEIPT',sale_id='SALE',amount_paise=20000))
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('OVER-ADVANCE','opening_advance_allocation',
                     dict(opening_id='ADVANCE-OPEN',sale_id='SALE',amount_paise=1))
        self.assertEqual(ledger.snapshot(self.db),before)
        report = ledger.report(self.db)
        self.assertEqual(report['sales'][0]['unsettled_paise'],0)
        self.assertEqual(report['bank_paise'],130000)
        advance = next(r for r in report['party_balances'] if r['opening_id']=='ADVANCE-OPEN')
        receivable = next(r for r in report['party_balances'] if r['opening_id']=='AR-OPEN')
        self.assertEqual((advance['opening_remaining_paise'],receivable['opening_remaining_paise']),(20000,10000))

    def test_opening_kind_role_and_same_buyer_are_checked(self):
        self.account()
        with self.assertRaises(ValueError):
            self.opening('BAD','WORKER-001','customer_advance',10000)
        self.opening('OTHER-ADVANCE','BUYER-2','customer_advance',10000)
        self.lock()
        self.sale()
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('WRONG-BUYER','opening_advance_allocation',
                     dict(opening_id='OTHER-ADVANCE',sale_id='SALE',amount_paise=10000))
        with self.assertRaises(ValueError):
            self.add('REFUND','opening_settlement',
                     dict(opening_id='OTHER-ADVANCE',account_id='CASH',amount_paise=10000))
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_account_link_and_money_save_together(self):
        self.account()
        self.lock()
        before = ledger.snapshot(self.db)
        self.db.execute("CREATE TRIGGER fail_account_link BEFORE INSERT ON cash_entry_accounts "
                        "BEGIN SELECT RAISE(ABORT,'Injected account-link failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add('RECEIPT','customer_receipt',dict(buyer_id='DEMO-BUYER',account_id='CASH',amount_paise=1000))
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_opening_settlement_concurrency_cannot_overdraw_account_or_obligation(self):
        self.account(amount=10000)
        self.opening('SUPPLIER-OPEN','DEMO-SUPPLIER','supplier_payable',15000)
        self.lock()
        def pay(identifier):
            with closing(ledger.open_db(self.path)) as db:
                event = self.event(identifier,'opening_settlement',
                                   dict(opening_id='SUPPLIER-OPEN',account_id='CASH',amount_paise=10000))
                try:
                    return ledger.record_event(db,event)
                except ValueError:
                    return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(pay,('PAY-A','PAY-B')))
        self.assertEqual(sorted(results),['recorded','rejected'])
        self.assertEqual(ledger.report(self.db)['cash_paise'],0)

    def test_unknown_party_opening_may_be_reviewed_once_after_posting_with_actual_review_date(self):
        self.account()
        self.lock()
        self.add('BILL','bill',dict(supplier_id='DEMO-SUPPLIER',description='Current bill',amount_paise=10000))
        before = ledger.report(self.db)
        row = next(r for r in before['party_balances'] if r['party_id']=='DEMO-SUPPLIER')
        self.assertIsNone(row['total_balance_paise'])
        self.add('LATE-OPEN','party_opening',dict(party_id='DEMO-SUPPLIER',balance_kind='supplier_payable',
                 amount_paise=20000,opening_as_of=self.setup['period_start'],
                 review_note='Previously unknown amount explicitly confirmed',evidence_ref='FICTIONAL-BOOK'),
                 owner=True,date='2026-03-02')
        report = ledger.report(self.db)
        row = next(r for r in report['party_balances'] if r['opening_id']=='LATE-OPEN')
        self.assertEqual((row['current_balance_paise'],row['total_balance_paise']),(10000,30000))
        self.assertEqual(report['cash_paise'],before['cash_paise'])
        event = next(e for e in ledger.snapshot(self.db)['tables']['events'] if e['event_id']=='LATE-OPEN')
        self.assertEqual(event['business_date'],'2026-03-02')

    def test_reviewed_finance_reopens_and_backup_restores_all_links(self):
        self.account('BANK',50000,'bank')
        self.opening('SUPPLIER-OPEN','DEMO-SUPPLIER','supplier_payable',10000)
        self.lock()
        self.add('SETTLE','opening_settlement',dict(opening_id='SUPPLIER-OPEN',account_id='BANK',amount_paise=10000))
        expected = ledger.backup_and_verify(self.db,self.root/'backup.sqlite3')
        with closing(ledger.open_db(self.path,readonly=True)) as db:
            self.assertEqual(ledger.snapshot(db),expected)
        self.assertEqual(expected['report']['bank_paise'],40000)

    def legacy_v2(self):
        setup, events = demo.example('settled')
        current_path = self.root/'legacy-fixture-source.sqlite3'
        path = self.root/'legacy-v2.sqlite3'
        with closing(ledger.open_db(current_path,setup)) as current, closing(sqlite3.connect(path)) as old:
            for event in events:
                ledger.record_event(current,event)
            old.executescript((ledger.HERE/'schema_v2.sql').read_text(encoding='utf-8'))
            names = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for name in names:
                for row in current.execute(f'SELECT * FROM {name} ORDER BY rowid'):
                    old.execute(f'INSERT INTO {name} VALUES({",".join("?" for _ in row)})',tuple(row))
            ledger.preserve_guards(old)
            old.execute(f'PRAGMA application_id={ledger.APP_ID}')
            old.execute('PRAGMA user_version=2')
            old.commit()
        return path

    def test_v2_copy_preserves_payloads_and_does_not_forge_owner_review(self):
        source = self.legacy_v2()
        original = source.read_bytes()
        with closing(ledger.open_db(source,readonly=True)) as db:
            expected = ledger.snapshot(db)
        target = self.root/'v3-copy.sqlite3'
        ledger.upgrade_copy(source,target)
        self.assertEqual(source.read_bytes(),original)
        with closing(ledger.open_db(target,readonly=True)) as db:
            result = ledger.snapshot(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            for name,rows in expected['tables'].items():
                self.assertEqual(result['tables'][name],rows)
            self.assertEqual(result['report']['cash_paise'],345000)
            self.assertTrue(all(r['review_event_id'] is None and r['source_basis']=='legacy_carried'
                                for r in result['tables']['party_openings']))
            self.assertEqual(len(result['tables']['cash_entry_accounts']),len(result['tables']['cash_entries']))

    def test_v2_upgrade_interruption_preserves_complete_legacy_copy(self):
        source = self.legacy_v2()
        original = source.read_bytes()
        target = self.root/'interrupted.sqlite3'
        with patch.object(ledger,'preserve_guards',side_effect=RuntimeError('Injected interruption')):
            with self.assertRaises(RuntimeError):
                ledger.upgrade_copy(source,target)
        self.assertEqual(source.read_bytes(),original)
        with closing(ledger.open_db(target,readonly=True)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(ledger.report(db)['cash_paise'],345000)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='payment_accounts'").fetchone())


if __name__ == '__main__':
    unittest.main()
