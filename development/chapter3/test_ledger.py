"""Challenge the fictional workflow, transaction boundaries and restore results."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy

import shutil
import sqlite3
import subprocess
import sys
import unittest
from uuid import uuid4
from unittest.mock import patch

import demo
import ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = (ledger.HERE/'data/tests').resolve()
        self.temp = self.root/uuid4().hex
        self.temp.mkdir(parents=True)
        self.addCleanup(self.cleanup)
        self.path = self.temp/'test.sqlite3'
        self.setup, self.events = demo.example()
        self.db = ledger.open_db(self.path,self.setup)
        self.addCleanup(lambda: self.db.close())

    def cleanup(self):
        target = self.temp.resolve()
        if target.parent != self.root or len(target.name) != 32:
            raise ValueError('Refuse cleanup outside disposable tests')
        shutil.rmtree(target)

    def add(self, eid, kind, d, date='2026-03-15', owner=False):
        event = {'event_id':eid,'request_id':'REQ-'+eid,'kind':kind,'business_date':date,
                 'actor_id':'DEMO-OWNER' if owner else 'DEMO-ACCOUNTANT',
                 'source_ref':'test/'+eid,'evidence_status':'fictional','data':d}
        return ledger.record_event(self.db,event)

    def fill(self, case='baseline'):
        _, events = demo.example(case)
        for event in events:
            ledger.record_event(self.db,event)
        return ledger.report(self.db)

    def first_work(self):
        ledger.record_event(self.db,self.events[0])

    def test_rejected_correction_can_be_revised_without_erasing_history(self):
        self.fill('rejected')
        original = ledger.snapshot(self.db)['tables']
        request = {'bill_id':'BILL-001', 'old_amount_paise':20000, 'new_amount_paise':24000,
                   'reason':'Revised fictional evidence', 'evidence_ref':'REVISED-SLIP'}
        self.assertEqual(self.add('CORR-2','correction_request',request), 'recorded')
        self.add('REVIEW-2','correction_review',
                 {'correction_id':'CORR-2','decision':'approved','reason':'Reviewed revised evidence'},owner=True)
        report = ledger.report(self.db)
        self.assertEqual((report['cash_paise'],report['bill_outstanding_paise']),(350000,4000))
        self.assertEqual({c['correction_id']:c['decision'] for c in report['corrections']},
                         {'CORR-001':'rejected','CORR-2':'approved'})
        self.assertEqual(report['bills'][0]['original_amount_paise'],20000)
        after = ledger.snapshot(self.db)['tables']
        for table, rows in original.items():
            self.assertEqual(after[table][:len(rows)], rows)
        payment = {'bill_id':'BILL-001','correction_id':'CORR-2','amount_paise':4000}
        self.assertEqual(self.add('SETTLE-2','bill_payment',payment),'recorded')
        self.assertEqual(self.add('SETTLE-2','bill_payment',payment),'already_recorded')
        with self.assertRaisesRegex(ValueError,'overpayment'):
            self.add('DUPLICATE-SETTLE','bill_payment',payment)
        before = ledger.snapshot(self.db)
        with self.assertRaisesRegex(ValueError,'approved'):
            self.add('CORR-3','correction_request',dict(request,old_amount_paise=24000,new_amount_paise=26000))
        self.assertEqual(before,ledger.snapshot(self.db))

    def test_pending_correction_blocks_second_proposal_atomically(self):
        self.fill('pending')
        before = ledger.snapshot(self.db)
        with self.assertRaisesRegex(ValueError,'pending'):
            self.add('CORR-2','correction_request',
                     {'bill_id':'BILL-001','old_amount_paise':20000,'new_amount_paise':26000,
                      'reason':'Second pending proposal','evidence_ref':'SLIP'})
        self.assertEqual(before,ledger.snapshot(self.db))

    def test_return_inspection_is_owner_reviewed_quantity_conserving_and_cash_neutral(self):
        self.fill()
        self.add('RETURN-A','return',{'sale_id':'SALE-001','quantity_bricks':10,'inspection_note':'Counted return'})
        before = ledger.report(self.db)
        data = {'return_id':'RETURN-A','quantity_bricks':5,'grades':{'first':3,'lal_petti':2},
                'inspection_note':'Owner inspected five returned pieces'}
        self.assertEqual(self.add('INSPECT-A','return_inspection',data,owner=True),'recorded')
        self.assertEqual(self.add('INSPECT-A','return_inspection',data,owner=True),'already_recorded')
        after = ledger.report(self.db)
        self.assertEqual((after['stock_bricks'],after['cash_paise'],after['sales_face_paise']),
                         (before['stock_bricks'],before['cash_paise'],before['sales_face_paise']))
        self.assertEqual(ledger.quantity_available(self.db,'LOT-DEMO-001','unclassified'),65)
        self.assertEqual(after['returns'][0]['remaining_bricks'],5)
        self.assertTrue(after['sales'][0]['return_financial_review_needed'])
        self.assertEqual(len(ledger.snapshot(self.db)['tables']['return_inspections']),1)

    def test_return_inspection_cannot_borrow_other_unclassified_stock_or_skip_owner(self):
        self.fill()
        self.add('RETURN-A','return',{'sale_id':'SALE-001','quantity_bricks':2,'inspection_note':'Two returned'})
        data = {'return_id':'RETURN-A','quantity_bricks':3,'grades':{'first':3},'inspection_note':'Too many'}
        for change, owner in [(data,True),(dict(data,quantity_bricks=2,grades={'first':2}),False),
                              (dict(data,quantity_bricks=2,grades={'first':1}),True),
                              (dict(data,quantity_bricks=2,grades={'unclassified':2}),True),
                              (dict(data,quantity_bricks=2,grades={'first':2},inspection_note=''),True)]:
            before = ledger.snapshot(self.db)
            with self.assertRaises(ValueError):
                self.add('INSPECT-A','return_inspection',change,owner=owner)
            self.assertEqual(before,ledger.snapshot(self.db))
        self.add('INSPECT-A','return_inspection',dict(data,quantity_bricks=2,grades={'first':2}),owner=True)
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('INSPECT-B','return_inspection',dict(data,quantity_bricks=1,grades={'first':1}),owner=True)
        self.assertEqual(before,ledger.snapshot(self.db))

    def make_legacy(self):
        path = self.temp/'legacy.sqlite3'
        old = sqlite3.connect(path)
        try:
            old.executescript((ledger.HERE/'schema_v1.sql').read_text(encoding='utf-8'))
            names = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in names:
                for row in self.db.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                    columns = ','.join(row.keys())
                    marks = ','.join('?' for _ in row)
                    old.execute(f'INSERT INTO {table}({columns}) VALUES({marks})',tuple(row))
                for verb in ('UPDATE','DELETE'):
                    old.execute(f"CREATE TRIGGER keep_{table}_{verb.lower()} BEFORE {verb} ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'Preserve original records; append a supported event'); END")
            old.execute(f'PRAGMA application_id={ledger.APP_ID}')
            old.execute('PRAGMA user_version=1')
            old.commit()
        finally:
            old.close()
        return path

    def test_concurrent_correction_proposals_allow_only_one_pending(self):
        self.fill('rejected')
        def submit(suffix):
            with closing(ledger.open_db(self.path)) as db:
                event = dict(event_id='R-'+suffix,request_id='R-'+suffix,source_ref='test/race/'+suffix,
                             actor_id='DEMO-ACCOUNTANT',business_date='2026-03-15',kind='correction_request',
                             evidence_status='fictional',data={'bill_id':'BILL-001','old_amount_paise':20000,
                             'new_amount_paise':24000,'reason':'Concurrent candidate','evidence_ref':'SLIP-'+suffix})
                try:
                    return ledger.record_event(db,event)
                except ValueError as exc:
                    return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit,('A','B')))
        self.assertEqual(results.count('recorded'),1)
        self.assertEqual(sum('pending' in r for r in results),1)
        self.assertEqual(sum(c['decision'] is None for c in ledger.report(self.db)['corrections']),1)

    def test_payment_between_request_and_approval_cannot_create_overpayment(self):
        self.add('BILL','bill',{'supplier_id':'DEMO-SUPPLIER','description':'Unpaid bill','amount_paise':20000})
        self.add('CORR','correction_request',{'bill_id':'BILL','old_amount_paise':20000,
                 'new_amount_paise':18000,'reason':'Candidate reduction','evidence_ref':'SLIP'})
        self.add('PAY','bill_payment',{'bill_id':'BILL','amount_paise':19000})
        before = ledger.snapshot(self.db)
        with self.assertRaisesRegex(ValueError,'overpayment'):
            self.add('APPROVE','correction_review',
                     {'correction_id':'CORR','decision':'approved','reason':'Stale approval'},owner=True)
        self.assertEqual(before,ledger.snapshot(self.db))

    def test_return_inspection_rolls_back_if_stock_write_fails(self):
        self.fill()
        self.add('RETURN','return',{'sale_id':'SALE-001','quantity_bricks':10,'inspection_note':'Ten pieces'})
        before = ledger.snapshot(self.db)
        self.db.execute("CREATE TRIGGER fail_inspection BEFORE INSERT ON stock_lines "
                        "BEGIN SELECT RAISE(ABORT,'inspection write interrupted'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add('INSPECT','return_inspection',{'return_id':'RETURN','quantity_bricks':10,
                     'grades':{'first':10},'inspection_note':'Reviewed pieces'},owner=True)
        self.assertEqual(before,ledger.snapshot(self.db))

    def test_return_inspections_stay_with_each_return_and_survive_backup(self):
        self.fill()
        for name in ('A','B'):
            self.add('RETURN-'+name,'return',{'sale_id':'SALE-001','quantity_bricks':5,'inspection_note':'Five pieces'})
        self.add('INSPECT-A','return_inspection',{'return_id':'RETURN-A','quantity_bricks':5,
                 'grades':{'pila_petti':5},'inspection_note':'All five inspected'},owner=True)
        self.assertEqual({r['return_id']:r['remaining_bricks'] for r in ledger.report(self.db)['returns']},
                         {'RETURN-A':0,'RETURN-B':5})
        before = ledger.snapshot(self.db)
        with self.assertRaises(ValueError):
            self.add('STEAL','loss',{'lot_id':'LOT-DEMO-001','from_bucket':'unclassified',
                     'quantity_bricks':61,'reason':'Must not consume pending return'},owner=True)
        self.assertEqual(before,ledger.snapshot(self.db))
        expected = ledger.backup_and_verify(self.db,self.temp/'inspected.backup.sqlite3')
        with closing(ledger.open_db(self.path,readonly=True)) as reopened:
            self.assertEqual(ledger.snapshot(reopened),expected)

    def test_upgrade_copy_preserves_legacy_and_enables_new_flows(self):
        self.fill('rejected')
        old_path = self.make_legacy()
        original = old_path.read_bytes()
        with closing(ledger.open_db(old_path,readonly=True)) as old:
            expected = ledger.snapshot(old)
        upgraded = self.temp/'upgraded.sqlite3'
        ledger.upgrade_copy(old_path,upgraded)
        self.assertEqual(old_path.read_bytes(),original)
        with closing(ledger.open_db(upgraded)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],ledger.VERSION)
            saved = ledger.snapshot(db)
            for table,rows in expected['tables'].items():
                self.assertEqual(saved['tables'][table],rows)
            self.assertEqual(saved['tables']['return_inspections'],[])
            event = dict(event_id='REVISED',request_id='REVISED',source_ref='test/revised',
                         actor_id='DEMO-ACCOUNTANT',business_date='2026-03-15',kind='correction_request',
                         evidence_status='fictional',data={'bill_id':'BILL-001','old_amount_paise':20000,
                         'new_amount_paise':24000,'reason':'Revised','evidence_ref':'SLIP'})
            ledger.record_event(db,event)
        with self.assertRaises(ValueError):
            ledger.upgrade_copy(old_path,upgraded)
        self.assertEqual(old_path.read_bytes(),original)

    def test_upgrade_failure_rolls_back_copied_schema(self):
        self.fill()
        old_path = self.make_legacy()
        original = old_path.read_bytes()
        target = self.temp/'failed-upgrade.sqlite3'
        with patch.object(ledger,'preserve_guards',side_effect=RuntimeError('Injected migration interruption')):
            with self.assertRaisesRegex(RuntimeError,'migration interruption'):
                ledger.upgrade_copy(old_path,target)
        self.assertEqual(old_path.read_bytes(),original)
        with closing(ledger.open_db(target,readonly=True)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(ledger.report(db)['cash_paise'],350000)

    def test_legacy_remains_readable_and_requires_copy_for_new_flows(self):
        self.fill('rejected')
        old_path = self.make_legacy()
        original = old_path.read_bytes()
        with closing(ledger.open_db(old_path,readonly=True)) as old:
            self.assertEqual(ledger.report(old)['schema_version'],1)
            self.assertEqual(ledger.snapshot(old)['report']['cash_paise'],350000)
        with closing(ledger.open_db(old_path)) as old:
            before = ledger.snapshot(old)
            event = dict(event_id='LEGACY-REVISION',request_id='LEGACY-REVISION',source_ref='test/legacy-revision',
                         actor_id='DEMO-ACCOUNTANT',business_date='2026-03-15',kind='correction_request',
                         evidence_status='fictional',data={'bill_id':'BILL-001','old_amount_paise':20000,
                         'new_amount_paise':24000,'reason':'Revision','evidence_ref':'SLIP'})
            with self.assertRaisesRegex(ValueError,'upgrade a copy'):
                ledger.record_event(old,event)
            self.assertEqual(ledger.snapshot(old),before)
        self.assertEqual(old_path.read_bytes(),original)

    def test_upgrade_refuses_legacy_consumed_return_stock_and_preserves_source(self):
        self.fill()
        source = self.make_legacy()
        with closing(ledger.open_db(source)) as db:
            for identifier,kind,data in [
                ('OLD-RETURN','return',{'sale_id':'SALE-001','quantity_bricks':10,'inspection_note':'Legacy return'}),
                ('OLD-LOSS','loss',{'lot_id':'LOT-DEMO-001','from_bucket':'unclassified',
                 'quantity_bricks':65,'reason':'Legacy pooled loss without return attribution'})]:
                ledger.record_event(db,dict(event_id=identifier,request_id=identifier,source_ref='test/'+identifier,
                    actor_id='DEMO-OWNER',business_date='2026-03-15',kind=kind,evidence_status='fictional',data=data))
            expected = ledger.snapshot(db)
        original = source.read_bytes()
        destination = self.temp/'needs-review.sqlite3'
        with self.assertRaisesRegex(ValueError,'Uninspected returns'):
            ledger.upgrade_copy(source,destination)
        self.assertEqual(source.read_bytes(),original)
        with closing(ledger.open_db(destination,readonly=True)) as db:
            self.assertEqual(ledger.snapshot(db),expected)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)

    def test_initial_checkpoint_and_separate_approval(self):
        self.first_work()
        r = ledger.report(self.db)
        self.assertEqual((r['stock_bricks'],r['earned_paise'],r['worker_paid_paise'],r['worker_outstanding_paise']), (1000,80000,0,80000))
        before = r['cash_paise']
        self.add('APP','work_approval',{'work_id':'WORK-001'},date='2026-02-06',owner=True)
        r = ledger.report(self.db)
        self.assertEqual((r['approved_paise'],r['worker_outstanding_paise'],r['cash_paise']), (80000,80000,before))

    def test_all_original_correction_results_and_reopen(self):
        for case in demo.CASES:
            with self.subTest(case=case):
                folder = self.temp/case
                folder.mkdir()
                result = demo.run_case(folder,case)
                demo.assert_case(result['report'],case)
                self.assertTrue(result['reopen_and_backup_match'])
                retry = demo.run_case(folder,case)
                self.assertEqual(retry['new_events'],0)
                self.assertEqual(retry['report'],result['report'])

    def test_pending_and_rejected_correction_do_not_change_cash_or_bill(self):
        for case in ('pending','rejected'):
            setup,events = demo.example(case)
            db = ledger.open_db(self.temp/f'{case}.sqlite3',setup)
            try:
                for event in events:
                    ledger.record_event(db,event)
                r = ledger.report(db)
                self.assertEqual((r['cash_paise'],r['bills'][0]['current_amount_paise']), (350000,20000))
            finally:
                db.close()

    def test_approval_changes_only_obligation_and_settlement_changes_cash(self):
        r = self.fill('approved')
        self.assertEqual((r['cash_paise'],r['bill_outstanding_paise']), (350000,5000))
        self.add('EXTRA-PAY','bill_payment',{'bill_id':'BILL-001','correction_id':'CORR-001','amount_paise':5000})
        after = ledger.report(self.db)
        self.assertEqual((after['cash_paise'],after['bill_outstanding_paise']), (345000,0))
        self.assertEqual(after['bills'][0]['original_amount_paise'],20000)

    def test_six_rupees_fifty_paise_sale_is_exact(self):
        for event in self.events:
            event = copy.deepcopy(event)
            if event['kind']=='customer_receipt':
                event['data']['amount_paise']=325000
            elif event['kind']=='dispatch_sale':
                event['data']['unit_price_paise']=650
            elif event['kind']=='allocation':
                event['data']['amount_paise']=325000
            ledger.record_event(self.db,event)
        r = ledger.report(self.db)
        self.assertEqual((r['sales_face_paise'],r['cash_paise']), (325000,425000))
        self.assertIsInstance(r['sales'][0]['amount_paise'],int)

    def test_payment_requires_approval_then_partial_payments_reconcile(self):
        self.first_work()
        before = ledger.snapshot(self.db)
        with self.assertRaisesRegex(ValueError,'approval is required'):
            self.add('PAY','worker_payment',{'work_id':'WORK-001','amount_paise':30000},date='2026-02-02')
        self.assertEqual(ledger.snapshot(self.db),before)
        self.add('APP','work_approval',{'work_id':'WORK-001'},date='2026-02-02',owner=True)
        self.add('PAY1','worker_payment',{'work_id':'WORK-001','amount_paise':30000},date='2026-02-03')
        self.assertEqual(ledger.report(self.db)['worker_outstanding_paise'],50000)
        self.add('PAY2','worker_payment',{'work_id':'WORK-001','amount_paise':50000},date='2026-02-03')
        self.assertEqual(ledger.report(self.db)['worker_outstanding_paise'],0)
        with self.assertRaisesRegex(ValueError,'overpayment'):
            self.add('PAY3','worker_payment',{'work_id':'WORK-001','amount_paise':1})

    def test_insufficient_cash_rolls_back_payment(self):
        self.add('BIG-BILL','bill',{'supplier_id':'DEMO-SUPPLIER','description':'Fictional large bill','amount_paise':300000})
        before = ledger.snapshot(self.db)
        with self.assertRaisesRegex(ValueError,'Cash would become negative'):
            self.add('PAY','bill_payment',{'bill_id':'BIG-BILL','amount_paise':300000})
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_stock_cannot_be_negative_or_skip_stages(self):
        self.first_work()
        before = ledger.snapshot(self.db)
        for data in [
            {'lot_id':'LOT-DEMO-001','from_bucket':'moulded','to_bucket':'loaded','quantity_bricks':1000},
            {'lot_id':'LOT-DEMO-001','from_bucket':'moulded','to_bucket':'dry_green','quantity_bricks':1001}]:
            with self.assertRaises(ValueError):
                self.add('BAD','transfer',data)
            self.assertEqual(ledger.snapshot(self.db),before)

    def test_grading_preserves_unclassified_count_and_rejects_imbalance(self):
        for event in self.events:
            if event['kind']=='grading':
                bad=copy.deepcopy(event)
                bad['data']['grades']['first']+=1
                before=ledger.snapshot(self.db)
                with self.assertRaisesRegex(ValueError,'must reconcile'):
                    ledger.record_event(self.db,bad)
                self.assertEqual(ledger.snapshot(self.db),before)
                ledger.record_event(self.db,event)
                break
            ledger.record_event(self.db,event)
        self.assertEqual(ledger.quantity_available(self.db,'LOT-DEMO-001','unclassified'),60)
        self.assertEqual(ledger.report(self.db)['stock_bricks'],1000)

    def test_dispatch_and_sale_are_atomic(self):
        for event in self.events:
            if event['kind']=='dispatch_sale':
                before=ledger.snapshot(self.db)
                self.db.execute("CREATE TRIGGER fail_sale BEFORE INSERT ON sales BEGIN SELECT RAISE(ABORT,'test sale failure'); END")
                with self.assertRaisesRegex(sqlite3.IntegrityError,'test sale failure'):
                    ledger.record_event(self.db,event)
                self.assertEqual(ledger.snapshot(self.db),before)
                break
            ledger.record_event(self.db,event)

    def test_work_and_receipt_roll_back_together(self):
        before=ledger.snapshot(self.db)
        self.db.execute("CREATE TRIGGER fail_stock BEFORE INSERT ON stock_lines BEGIN SELECT RAISE(ABORT,'test receipt failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.first_work()
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_process_interruption_keeps_no_partial_work(self):
        expected=ledger.snapshot(self.db)
        self.db.close()
        child="""
import os,sys
import ledger,demo
db=ledger.open_db(sys.argv[1])
def stop(statement):
    if statement.lstrip().upper().startswith('INSERT INTO STOCK_LINES'):
        os._exit(23)
db.set_trace_callback(stop)
ledger.record_event(db,demo.example()[1][0])
sys.exit(99)
"""
        result=subprocess.run([sys.executable,'-B','-c',child,str(self.path)],cwd=ledger.HERE,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,23,result.stderr)
        self.db=ledger.open_db(self.path)
        self.assertEqual(ledger.snapshot(self.db),expected)
        self.first_work()
        self.assertEqual(ledger.report(self.db)['stock_bricks'],1000)

    def test_retries_conflicts_and_concurrent_duplicates(self):
        def writer():
            db=ledger.open_db(self.path)
            try:
                return ledger.record_event(db,self.events[0])
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:writer(),range(2)))
        self.assertEqual(sorted(results),['already_recorded','recorded'])
        original=ledger.snapshot(self.db)
        for changes in [ {'event_id':'OTHER','request_id':'OTHER'}, {'data':dict(self.events[0]['data'],quantity_bricks=2000)} ]:
            bad=dict(self.events[0],**changes)
            with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):
                ledger.record_event(self.db,bad)
        self.assertEqual(ledger.snapshot(self.db),original)

    def test_allocation_neither_adds_cash_nor_duplicates_sales(self):
        for event in self.events:
            before=ledger.report(self.db)
            ledger.record_event(self.db,event)
            if event['kind']=='allocation':
                after=ledger.report(self.db)
                self.assertEqual((before['cash_paise'],before['sales_face_paise']), (after['cash_paise'],after['sales_face_paise']))
                self.assertEqual(after['unallocated_customer_receipts_paise'],0)
        with self.assertRaisesRegex(ValueError,'Allocation exceeds'):
            self.add('ALLOC-OVER','allocation',{'receipt_id':'CASH-003','sale_id':'SALE-001','amount_paise':1})

    def test_allocation_rejects_a_different_buyer(self):
        self.db.close()
        setup=copy.deepcopy(self.setup)
        setup['parties'].append({'party_id':'BUYER-2','name':'Another fictional buyer','role':'buyer'})
        self.db=ledger.open_db(self.temp/'two-buyers.sqlite3',setup)
        for event in self.events:
            if event['kind']=='allocation':
                break
            ledger.record_event(self.db,event)
        self.add('OTHER-RECEIPT','customer_receipt',{'buyer_id':'BUYER-2','amount_paise':250000})
        with self.assertRaisesRegex(ValueError,'same buyer'):
            self.add('BAD-ALLOC','allocation',{'receipt_id':'OTHER-RECEIPT','sale_id':'SALE-001','amount_paise':250000})

    def test_nonzero_opening_stock_is_separate_from_current_production(self):
        self.db.close()
        setup=copy.deepcopy(self.setup)
        setup['lots'][0]['opening_stock']['first']=100
        self.db=ledger.open_db(self.temp/'carryover.sqlite3',setup)
        self.first_work()
        r=ledger.report(self.db)
        self.assertEqual((r['stock_bricks'],r['earned_paise']), (1100,80000))

    def test_loss_and_internal_use_are_not_sales_or_automatic_wage_deductions(self):
        self.first_work()
        for eid,kind,quantity in [('LOSS','loss',20),('REPAIR','internal_use',30)]:
            self.add(eid,kind,{'lot_id':'LOT-DEMO-001','from_bucket':'moulded','quantity_bricks':quantity,'reason':'Fictional counted disposition'},owner=True)
        r=ledger.report(self.db)
        self.assertEqual((r['stock_bricks'],r['earned_paise'],r['sales_face_paise']), (950,80000,0))
        self.assertEqual(ledger.quantity_available(self.db,'LOT-DEMO-001','loss'),20)

    def test_physical_return_is_unclassified_and_financial_review_is_explicit(self):
        before=self.fill()
        self.add('RETURN','return',{'sale_id':'SALE-001','quantity_bricks':10,'inspection_note':'Fictional returned pieces awaiting regrading'})
        after=ledger.report(self.db)
        self.assertEqual(after['stock_bricks'],510)
        self.assertEqual(ledger.quantity_available(self.db,'LOT-DEMO-001','unclassified'),70)
        self.assertEqual((after['cash_paise'],after['sales_face_paise']), (before['cash_paise'],before['sales_face_paise']))
        self.assertTrue(after['sales'][0]['return_financial_review_needed'])
        with self.assertRaisesRegex(ValueError,'Return exceeds'):
            self.add('TOO-MANY','return',{'sale_id':'SALE-001','quantity_bricks':491,'inspection_note':'Invalid quantity'})

    def test_missing_unknown_float_and_unexpected_fields_are_rejected(self):
        before=ledger.snapshot(self.db)
        for quantity in (None,True,1.5,-1,0):
            bad=copy.deepcopy(self.events[0])
            bad['data']['quantity_bricks']=quantity
            with self.assertRaises(ValueError):
                ledger.record_event(self.db,bad)
        bad=copy.deepcopy(self.events[0])
        bad['data']['made_up_cost']=12
        with self.assertRaisesRegex(ValueError,'unexpected event data'):
            ledger.record_event(self.db,bad)
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_unknown_openings_and_actuals_are_not_defaulted_to_zero(self):
        for change in ({'opening_cash_paise':None},{'opening_worker_outstanding_paise':1},{'evidence_status':'actual'}):
            with self.assertRaises(ValueError):
                ledger.open_db(self.temp/'bad.sqlite3',dict(self.setup,**change))
        self.assertFalse((self.temp/'bad.sqlite3').exists())

    def test_rounding_and_overflow_are_explicit(self):
        self.assertEqual(ledger.earnings(1,80000),80)
        self.assertEqual(ledger.earnings(1,500),1)
        self.assertEqual(ledger.earnings(1,499),0)
        with self.assertRaises(ValueError):
            ledger.earnings(ledger.MAX_INT,80000)

    def test_dates_scope_and_backdating_are_guarded(self):
        self.first_work()
        for value in ('2026-01-31','2026-02-30','2026-03-16','20260206'):
            with self.assertRaises(ValueError):
                self.add('APP','work_approval',{'work_id':'WORK-001'},date=value,owner=True)
        self.add('APP','work_approval',{'work_id':'WORK-001'},date='2026-02-06',owner=True)
        with self.assertRaisesRegex(ValueError,'Backdated'):
            self.add('PAY','worker_payment',{'work_id':'WORK-001','amount_paise':100},date='2026-02-05')

    def test_correction_needs_current_value_independent_owner_and_unique_review(self):
        self.add('BILL','bill',{'supplier_id':'DEMO-SUPPLIER','description':'Demo bill','amount_paise':20000})
        data={'bill_id':'BILL','old_amount_paise':19000,'new_amount_paise':25000,'reason':'Demo correction','evidence_ref':'DEMO-SLIP'}
        with self.assertRaisesRegex(ValueError,'Stale'):
            self.add('CORR','correction_request',data)
        data['old_amount_paise']=20000
        self.add('CORR','correction_request',data)
        review={'correction_id':'CORR','decision':'approved','reason':'Reviewed fictional slip'}
        with self.assertRaisesRegex(ValueError,'role'):
            self.add('REVIEW','correction_review',review)
        self.add('REVIEW','correction_review',review,owner=True)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add('REVIEW-2','correction_review',dict(review,decision='rejected'),owner=True)
        self.assertEqual(ledger.report(self.db)['bills'][0]['current_amount_paise'],25000)

    def test_owner_cannot_review_own_correction(self):
        self.add('BILL','bill',{'supplier_id':'DEMO-SUPPLIER','description':'Demo bill','amount_paise':20000})
        self.add('CORR','correction_request',{'bill_id':'BILL','old_amount_paise':20000,'new_amount_paise':25000,'reason':'Demo correction','evidence_ref':'SLIP'},owner=True)
        with self.assertRaisesRegex(ValueError,'own correction'):
            self.add('REVIEW','correction_review',{'correction_id':'CORR','decision':'approved','reason':'Invalid self review'},owner=True)

    def test_correction_cannot_hide_prior_payment_or_overpay_a_bill(self):
        self.fill()
        with self.assertRaisesRegex(ValueError,'overpayment'):
            self.add('PAY','bill_payment',{'bill_id':'BILL-001','amount_paise':1})
        with self.assertRaisesRegex(ValueError,'overpayment'):
            self.add('CORR','correction_request',{'bill_id':'BILL-001','old_amount_paise':20000,'new_amount_paise':10000,'reason':'Unsupported refund case','evidence_ref':'SLIP'})

    def test_original_rows_cannot_be_updated_or_deleted_through_sql(self):
        self.fill('settled')
        before=ledger.snapshot(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'Preserve original'):
            self.db.execute('UPDATE bills SET original_amount_paise=25000')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'Preserve original'):
            self.db.execute("DELETE FROM cash_entries WHERE cash_id='CASH-001'")
        self.assertEqual(ledger.snapshot(self.db),before)

    def test_backup_restores_every_table_and_refuses_overwrite(self):
        self.fill('settled')
        path=self.temp/'backup.sqlite3'
        expected=ledger.backup_and_verify(self.db,path)
        backup=ledger.open_db(path,readonly=True)
        try:
            self.assertEqual(ledger.snapshot(backup),expected)
        finally:
            backup.close()
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'already exists'):
            ledger.backup_and_verify(self.db,path)
        self.assertEqual(path.read_bytes(),before)

    def test_unrelated_database_and_setup_changes_are_refused(self):
        other=self.temp/'other.sqlite3'
        db=sqlite3.connect(other)
        db.execute('CREATE TABLE notes(value TEXT)')
        db.commit()
        db.close()
        before=other.read_bytes()
        with self.assertRaisesRegex(ValueError,'Not a Chapter 3'):
            ledger.open_db(other,self.setup)
        self.assertEqual(other.read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'setup differs'):
            ledger.open_db(self.path,dict(self.setup,opening_cash_paise=1))

    def test_read_only_cli_does_not_create_or_change_files(self):
        missing=self.temp/'missing'
        result=subprocess.run([sys.executable,'-B',str(ledger.HERE/'demo.py'),'--show','--data-dir',str(missing)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,2)
        self.assertFalse(missing.exists())
        demo.run_case(self.temp,'settled')
        path=self.temp/'settled.sqlite3'
        before=path.read_bytes()
        result=subprocess.run([sys.executable,'-B',str(ledger.HERE/'demo.py'),'--show','--case','settled','--data-dir',str(self.temp)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('INR 3,450.00',result.stdout)
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
