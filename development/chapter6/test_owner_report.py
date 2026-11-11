from contextlib import closing
import hashlib
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
import owner_report as owner
import demo


class OwnerReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=owner.HERE)
        self.root=Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,owner.HERE.resolve())
    def tearDown(self):
        self.temp.cleanup()
    def test_five_cases_metrics_and_source_links_without_writes(self):
        for case in demo.CASES:
            directory=self.root/case
            directory.mkdir()
            demo.run_case(directory,case)
            path=directory/(case+'.sqlite3')
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            result=owner.build(path)
            m=result['metrics']
            self.assertEqual(result['stock_reconciliation'][0]['difference'],0)
            self.assertEqual(result['stock_reconciliation'][0]['dispatched'],500)
            expected={'stock_on_hand':500,'classified_finished_stock':440,'unclassified_stock':60,'work_in_progress':0,
                      'wages_earned':80000,'wages_approved':80000,'wages_paid':80000,'wages_outstanding':0,
                      'sales_face_amount':250000,'customer_receipts':250000,'unsettled_sale_face_amount':0,'unallocated_receipts':0,
                      'closing_cash':345000 if case=='settled' else 350000,
                      'supplier_outstanding':5000 if case=='approved' else 0,
                      'recorded_obligations':105000 if case in ('approved','settled') else 100000}
            for name,value in expected.items():
                self.assertEqual(m[name]['value'],value,(case,name))
            for name in ('profit','profit_margin','forecast_sales','weather_impact'):
                self.assertIsNone(m[name]['value'])
            keys={'context':'id','stock_movements':'movement_id','work_credits':'work_id','work_approvals':'approval_id',
                  'cash_entries':'cash_id','bills':'bill_id','bill_corrections':'correction_id','correction_reviews':'review_id',
                      'sales':'sale_id','receipt_allocations':'allocation_id',
                      'account_openings':'account_id','payment_accounts':'account_id','cash_entry_accounts':'cash_id',
                      'party_openings':'opening_id','opening_settlements':'settlement_id','opening_advance_allocations':'allocation_id'}
            valid={t+'/'+str(r[k]) for t,k in keys.items() for r in result['source_records'][t]}
            valid.update('stock_openings/'+r['lot_id']+'/'+r['bucket'] for r in result['source_records']['stock_openings'])
            self.assertTrue(all(ref in valid for metric in m.values() for ref in metric['sources']))
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
            kinds={a['kind'] for a in result['attention']}
            self.assertEqual('review_correction' in kinds,case=='pending')
            self.assertEqual('bill_outstanding' in kinds,case=='approved')
    def test_supplier_total_traces_approved_bill_correction(self):
        setup, events = demo.example('approved')
        path = self.root/'approved-supplier-total.sqlite3'
        with closing(owner.ledger.open_db(path,setup)) as db:
            for event in events:
                owner.ledger.record_event(db,event)
        report = owner.build(path)
        metric = report['metrics']['supplier_payable_total']
        self.assertEqual(metric['value'],5000)
        self.assertIn('bill_corrections/CORR-001',metric['sources'])
        self.assertIn('correction_reviews/REVIEW-001',metric['sources'])
        self.assertEqual(report['metrics']['closing_cash']['value'],350000)

    def test_initial_work_is_not_finished_stock_or_paid_work(self):
        setup,events=demo.example()
        path=self.root/'work.sqlite3'
        with closing(owner.ledger.open_db(path,setup)) as db:
            owner.ledger.record_event(db,events[0])
        r=owner.build(path)
        m=r['metrics']
        self.assertEqual((m['work_in_progress']['value'],m['classified_finished_stock']['value'],m['wages_outstanding']['value']),(1000,0,80000))
        self.assertEqual({a['kind'] for a in r['attention']},{'review_work','wages_outstanding'})

    def test_opening_advance_allocation_has_report_lineage_without_cash_receipt(self):
        setup, _ = demo.example()
        setup['finance_mode'] = 'reviewed_accounts'
        for field in ('opening_cash_paise','opening_worker_outstanding_paise',
                      'opening_supplier_outstanding_paise','opening_customer_balance_paise'):
            setup[field] = None
        setup['lots'][0]['opening_stock']['first'] = 100
        path = self.root/'reviewed-advance.sqlite3'
        with closing(owner.ledger.open_db(path,setup)) as db:
            def add(identifier,kind,data,owner_role=True):
                owner.ledger.record_event(db,dict(event_id=identifier,request_id=identifier,
                    source_ref='fictional-opening/'+identifier,actor_id='DEMO-OWNER' if owner_role else 'DEMO-ACCOUNTANT',
                    business_date='2026-03-01',kind=kind,evidence_status='fictional',data=data))
            add('REGISTER','account_register',dict(account_id='BANK',name='Fictional bank',account_type='bank',review_note='Label'))
            add('ACCOUNT-OPEN','account_opening',dict(account_id='BANK',amount_paise=0,opening_as_of='2026-02-01',
                review_note='Zero reviewed',evidence_ref='BOOK'))
            add('ADVANCE-OPEN','party_opening',dict(party_id='DEMO-BUYER',balance_kind='customer_advance',
                amount_paise=10000,opening_as_of='2026-02-01',review_note='Prior advance',evidence_ref='BOOK'))
            add('LOCK','openings_lock',dict(review_note='Start'))
            add('SALE','dispatch_sale',dict(lot_id='LOT-DEMO-001',buyer_id='DEMO-BUYER',grade='first',
                quantity_bricks=100,unit_price_paise=500,dispatch_ref='DISPATCH'),False)
            add('MATCH','opening_advance_allocation',dict(opening_id='ADVANCE-OPEN',sale_id='SALE',amount_paise=10000),False)
        report = owner.build(path)
        metric = report['metrics']['unsettled_sale_face_amount']
        self.assertEqual(metric['value'],40000)
        self.assertIn('opening_advance_allocations/MATCH',metric['sources'])
        self.assertEqual(report['metrics']['closing_bank']['value'],0)
        self.assertEqual(report['metrics']['customer_receipts']['value'],0)
    def test_return_is_flagged_not_silently_refunded(self):
        demo.run_case(self.root,'baseline')
        path=self.root/'baseline.sqlite3'
        with closing(owner.ledger.open_db(path)) as db:
            owner.ledger.record_event(db,dict(event_id='RETURN',request_id='RETURN',kind='return',business_date='2026-03-15',actor_id='DEMO-OWNER',source_ref='fictional-return',evidence_status='fictional',data=dict(sale_id='SALE-001',quantity_bricks=10,inspection_note='Fictional inspection')))
        r=owner.build(path)
        self.assertEqual(r['metrics']['stock_on_hand']['value'],510)
        check=r['stock_reconciliation'][0]
        self.assertEqual((check['moulded'],check['returned'],check['dispatched'],check['on_hand'],check['difference']), (1000,10,500,510,0))
        self.assertEqual(r['metrics']['sales_face_amount']['value'],250000)
        self.assertEqual(r['metrics']['closing_cash']['value'],350000)
        self.assertIn('review_return',{a['kind'] for a in r['attention']})

    def test_return_inspection_lineage_and_financial_review_remain_separate(self):
        demo.run_case(self.root,'baseline')
        path = self.root/'baseline.sqlite3'
        with closing(owner.ledger.open_db(path)) as db:
            def add(identifier, kind, data):
                owner.ledger.record_event(db,dict(event_id=identifier,request_id=identifier,
                    source_ref='fictional-inspection/'+identifier,actor_id='DEMO-OWNER',
                    business_date='2026-03-15',kind=kind,evidence_status='fictional',data=data))
            add('RETURN','return',dict(sale_id='SALE-001',quantity_bricks=10,inspection_note='Counted ten'))
            add('INSPECT','return_inspection',dict(return_id='RETURN',quantity_bricks=10,
                grades={'first':10},inspection_note='Owner inspected all ten'))
        report = owner.build(path)
        self.assertEqual(report['returned_stock'][0]['remaining_bricks'],0)
        self.assertEqual(report['source_records']['return_inspections'][0]['return_id'],'RETURN')
        self.assertEqual(report['metrics']['closing_cash']['value'],350000)
        self.assertEqual(report['metrics']['sales_face_amount']['value'],250000)
        self.assertTrue(report['stock_reconciliation'][0]['balanced'])
        self.assertIn('review_return',{a['kind'] for a in report['attention']})
        self.assertNotIn('review_return_stock',{a['kind'] for a in report['attention']})
    def test_missing_database_is_not_created(self):
        path=self.root/'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            owner.build(path)
        self.assertFalse(path.exists())

    def test_worker_approval_partial_payment_and_retry(self):
        setup,events=demo.example()
        path=self.root/'worker.sqlite3'
        work=next(e for e in events if e['kind']=='moulding')
        approval=next(e for e in events if e['kind']=='work_approval')
        payment=next(e for e in events if e['kind']=='worker_payment')
        payment['data']['amount_paise']=30000
        with closing(owner.ledger.open_db(path,setup)) as db:
            owner.ledger.record_event(db,work)
        before=owner.build(path)
        with closing(owner.ledger.open_db(path)) as db:
            with self.assertRaisesRegex(ValueError,'approval'):
                owner.ledger.record_event(db,payment)
        self.assertEqual(owner.build(path),before)
        with closing(owner.ledger.open_db(path)) as db:
            owner.ledger.record_event(db,approval)
        approved=owner.build(path)['metrics']
        self.assertEqual((approved['wages_approved']['value'],approved['closing_cash']['value']), (80000,200000))
        with closing(owner.ledger.open_db(path)) as db:
            owner.ledger.record_event(db,payment)
        paid=owner.build(path)
        m=paid['metrics']
        self.assertEqual((m['stock_on_hand']['value'],m['wages_earned']['value'],m['wages_paid']['value'],
                          m['wages_outstanding']['value'],m['closing_cash']['value']), (1000,80000,30000,50000,170000))
        self.assertEqual({a['kind'] for a in paid['attention']},{'wages_outstanding'})
        with closing(owner.ledger.open_db(path)) as db:
            self.assertEqual(owner.ledger.record_event(db,payment),'already_recorded')
        self.assertEqual(owner.build(path),paid)

    def test_supplier_partial_payment_then_correction_and_settlement(self):
        setup,events=demo.example('settled')
        path=self.root/'supplier.sqlite3'
        selected=[e for e in events if e['kind'] in ('bill','bill_payment','correction_request','correction_review')]
        first_payment=next(e for e in selected if e['kind']=='bill_payment')
        first_payment['data']['amount_paise']=10000
        final_payment=selected[-1]
        self.assertEqual(final_payment['kind'],'bill_payment')
        final_payment['data']['amount_paise']=15000
        expected=[(20000,200000,20000),(10000,190000,20000),(10000,190000,20000),
                  (15000,190000,25000),(0,175000,25000)]
        with closing(owner.ledger.open_db(path,setup)):
            pass
        for event,totals in zip(selected,expected):
            with closing(owner.ledger.open_db(path)) as db:
                owner.ledger.record_event(db,event)
            report=owner.build(path)
            m=report['metrics']
            self.assertEqual(tuple(m[k]['value'] for k in ('supplier_outstanding','closing_cash','recorded_obligations')),totals)
            self.assertEqual(m['stock_on_hand']['value'],0)
            self.assertIsNone(m['profit']['value'])
        self.assertEqual(len(selected),len(expected))
        self.assertEqual(report['source_records']['bills'][0]['original_amount_paise'],20000)
        self.assertEqual(report['attention'],[])
        with closing(owner.ledger.open_db(path)) as db:
            self.assertEqual(owner.ledger.record_event(db,final_payment),'already_recorded')
        self.assertEqual(owner.build(path),report)

    def test_loss_and_internal_use_reduce_stock_without_sales_or_cash(self):
        demo.run_case(self.root,'baseline')
        path=self.root/'baseline.sqlite3'
        before=owner.build(path)
        for eid,kind,quantity in [('LOSS','loss',10),('REPAIR','internal_use',20)]:
            event=dict(event_id=eid,request_id=eid,kind=kind,business_date='2026-03-15',
                actor_id='DEMO-OWNER',source_ref='fictional-stock-check/'+eid,evidence_status='fictional',
                data=dict(lot_id='LOT-DEMO-001',from_bucket='first',quantity_bricks=quantity,
                          reason='Fictional confirmed removal for '+kind))
            with closing(owner.ledger.open_db(path)) as db:
                owner.ledger.record_event(db,event)
                self.assertEqual(owner.ledger.record_event(db,event),'already_recorded')
        after=owner.build(path)
        m=after['metrics']
        self.assertTrue(after['stock_reconciliation'][0]['balanced'])
        self.assertEqual((m['stock_on_hand']['value'],m['classified_finished_stock']['value'],
                          m['recorded_loss_bricks']['value'],m['internal_use_bricks']['value']), (470,410,10,20))
        self.assertEqual(m['recorded_loss_bricks']['sources'],['stock_movements/LOSS'])
        self.assertEqual(m['internal_use_bricks']['sources'],['stock_movements/REPAIR'])
        self.assertEqual(m['stock_on_hand']['value']+10+20,before['metrics']['stock_on_hand']['value'])
        for name in ('closing_cash','sales_face_amount','customer_receipts','wages_earned','wages_paid','recorded_obligations'):
            self.assertEqual(m[name]['value'],before['metrics'][name]['value'])
        self.assertIsNone(m['profit']['value'])

    def test_invalid_stock_removals_leave_report_unchanged(self):
        setup,events=demo.example()
        path=self.root/'removal.sqlite3'
        with closing(owner.ledger.open_db(path,setup)) as db:
            owner.ledger.record_event(db,events[0])
        before=owner.build(path)
        for kind in ('loss','internal_use'):
            for actor,count in [('DEMO-ACCOUNTANT',10),('DEMO-OWNER',1001)]:
                event=dict(event_id='INVALID',request_id='INVALID',kind=kind,business_date='2026-03-15',
                    actor_id=actor,source_ref='fictional-invalid-removal',evidence_status='fictional',
                    data=dict(lot_id='LOT-DEMO-001',from_bucket='moulded',quantity_bricks=count,reason='Test rejection'))
                with closing(owner.ledger.open_db(path)) as db:
                    with self.assertRaises(ValueError):
                        owner.ledger.record_event(db,event)
                self.assertEqual(owner.build(path),before)

    def commercial_case(self, received, sell=False, allocate=0):
        # Opening finished stock is explicit carryover, not newly earned work.
        setup,_=demo.example()
        setup['lots'][0]['opening_stock']['first']=1000
        path=self.root/'commercial.sqlite3'
        with closing(owner.ledger.open_db(path,setup)) as db:
            def add(eid,kind,data):
                owner.ledger.record_event(db,dict(event_id=eid,request_id=eid,kind=kind,
                    business_date='2026-03-01',actor_id='DEMO-ACCOUNTANT',source_ref='fictional-report-check/'+eid,
                    evidence_status='fictional',data=data))
            add('RECEIPT','customer_receipt',dict(buyer_id='DEMO-BUYER',amount_paise=received))
            if sell:
                add('SALE','dispatch_sale',dict(dispatch_ref='DISPATCH',lot_id='LOT-DEMO-001',buyer_id='DEMO-BUYER',
                    grade='first',quantity_bricks=500,unit_price_paise=500))
            if allocate:
                add('MATCH','allocation',dict(receipt_id='RECEIPT',sale_id='SALE',amount_paise=allocate))
        return path

    def test_opening_stock_reconciles_without_new_production(self):
        r=owner.build(self.commercial_case(100000,sell=True))
        c=r['stock_reconciliation'][0]
        self.assertEqual((c['opening'],c['moulded'],c['dispatched'],c['on_hand'],c['difference']), (1000,0,500,500,0))

    def test_stock_gap_is_flagged_without_repairing_source(self):
        demo.run_case(self.root,'baseline')
        path=self.root/'baseline.sqlite3'
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        with closing(owner.ledger.open_db(path,readonly=True)) as db:
            snapshot=owner.ledger.snapshot(db)
        # Inject a reporting fault in memory, never into the preserved database.
        row=next(r for r in snapshot['report']['stock'] if r['bucket']=='first')
        row['quantity_bricks']-=7
        snapshot['report']['stock_bricks']-=7
        with patch.object(owner.ledger,'snapshot',return_value=snapshot):
            result=owner.build(path)
        self.assertEqual(result['stock_reconciliation'][0]['difference'],7)
        self.assertFalse(result['stock_reconciliation'][0]['balanced'])
        self.assertIn('stock_balance_mismatch',{a['kind'] for a in result['attention']})
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_lot_gaps_cannot_cancel_each_other(self):
        setup,_=demo.example()
        path=self.root/'two-lots.sqlite3'
        setup['lots'][0]['opening_stock']['first']=1000
        setup['lots'].append(dict(lot_id='LOT-SECOND',opening_stock={b:100 if b=='first' else 0 for b in owner.ledger.ACTIVE}))
        with closing(owner.ledger.open_db(path,setup)) as db:
            saved=owner.ledger.snapshot(db)
        normal=owner.build(path)
        self.assertTrue(all(c['balanced'] for c in normal['stock_reconciliation']))
        for row in saved['report']['stock']:
            if row['bucket']=='first':
                row['quantity_bricks']+=7 if row['lot_id']=='LOT-SECOND' else -7
        with patch.object(owner.ledger,'snapshot',return_value=saved):
            altered=owner.build(path)
        # The plant total still agrees; each lot must nevertheless be flagged.
        self.assertEqual(altered['metrics']['stock_on_hand']['value'],1100)
        self.assertEqual({c['lot_id']:c['difference'] for c in altered['stock_reconciliation']},
                         {'LOT-DEMO-001':7,'LOT-SECOND':-7})
        self.assertEqual({a['reference'] for a in altered['attention'] if a['kind']=='stock_balance_mismatch'},
                         {'LOT-DEMO-001','LOT-SECOND'})
        self.assertEqual(owner.build(path),normal)

    def test_advance_before_collection_is_not_a_sale(self):
        report=owner.build(self.commercial_case(100000))
        m=report['metrics']
        self.assertEqual((m['stock_on_hand']['value'],m['sales_face_amount']['value'],m['customer_receipts']['value'],
                          m['unallocated_receipts']['value'],m['closing_cash']['value']), (1000,0,100000,100000,300000))
        self.assertEqual(m['wages_earned']['value'],0)
        self.assertEqual(report['attention'],[dict(kind='review_receipt_allocation',reference='RECEIPT',reason='Customer money remains unmatched; it may be an advance, not extra sales income.')])

    def test_partial_allocation_changes_neither_cash_nor_stock(self):
        path=self.commercial_case(100000,sell=True)
        before=owner.build(path)
        with closing(owner.ledger.open_db(path)) as db:
            owner.ledger.record_event(db,dict(event_id='MATCH',request_id='MATCH',kind='allocation',business_date='2026-03-01',
                actor_id='DEMO-ACCOUNTANT',source_ref='fictional-report-check/MATCH',evidence_status='fictional',
                data=dict(receipt_id='RECEIPT',sale_id='SALE',amount_paise=100000)))
        report=owner.build(path)
        m=report['metrics']
        self.assertEqual(m['unsettled_sale_face_amount']['value'],150000)
        self.assertEqual(m['unallocated_receipts']['value'],0)
        for name in ('closing_cash','stock_on_hand','sales_face_amount','customer_receipts'):
            self.assertEqual(m[name]['value'],before['metrics'][name]['value'])
        self.assertEqual({a['kind'] for a in report['attention']},{'review_sale_balance'})
        self.assertIsNone(m['profit']['value'])

    def test_excess_receipt_is_not_extra_revenue(self):
        report=owner.build(self.commercial_case(300000,sell=True,allocate=250000))
        m=report['metrics']
        self.assertEqual((m['sales_face_amount']['value'],m['customer_receipts']['value'],m['unallocated_receipts']['value'],
                          m['unsettled_sale_face_amount']['value'],m['closing_cash']['value']), (250000,300000,50000,0,500000))
        self.assertEqual({a['kind'] for a in report['attention']},{'review_receipt_allocation'})


if __name__=='__main__':
    unittest.main()
