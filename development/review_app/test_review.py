"""Integration checks use disposable fictional databases, never the review DB."""
from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import server


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=server.HERE)
        self.path = Path(self.temp.name) / 'review.sqlite3'
        self.start()
        self.csrf = self.get('/api/state')[1]['csrf']

    def start(self, reviewed=False):
        self.app = server.create_server(self.path, reviewed=reviewed)
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.app.shutdown()
        self.app.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def request(self, method, route, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.app.server_port, timeout=5)
        h = {'Content-Type': 'application/json', 'X-CSRF-Token': getattr(self, 'csrf', '')}
        h.update(headers or {})
        c.request(method, route, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        raw = r.read()
        content = json.loads(raw) if 'application/json' in r.getheader('Content-Type','') else raw
        c.close()
        return r.status, content

    def get(self, route):
        return self.request('GET', route)

    def state(self):
        return self.get('/api/state')[1]['snapshot']

    def event(self, kind, data, token=None):
        return self.request('POST', '/api/event', dict(kind=kind, date='2026-03-15', token=token or uuid.uuid4().hex, data=data))

    def reviewed_workspace(self):
        self.stop()
        self.path = self.path.parent/'fresh-reviewed.sqlite3'
        self.start(reviewed=True)
        self.csrf = self.get('/api/state')[1]['csrf']

    def test_fresh_reviewed_workspace_supports_browser_setup_and_reopening(self):
        self.reviewed_workspace()
        self.assertIsNone(self.state()['report']['funds_paise'])
        self.assertEqual(self.state()['report']['cash_entries'],[])
        self.assertEqual(self.event('account_register',dict(account_id='BANK',name='Fictional bank',
            account_type='bank',review_note='Owner declares account'))[0],200)
        self.assertEqual(self.event('account_opening',dict(account_id='BANK',amount_paise='0',
            opening_as_of='2026-02-01',review_note='Zero confirmed in fictional evidence',evidence_ref='DEMO-ZERO'))[0],200)
        self.assertEqual(self.event('openings_lock',dict(review_note='Owner starts posting'))[0],200)
        self.assertEqual(self.event('customer_receipt',dict(buyer_id='DEMO-BUYER',amount_paise='100',account_id='BANK'))[0],200)
        before = self.state()
        self.stop()
        self.start()
        self.csrf = self.get('/api/state')[1]['csrf']
        self.assertEqual(self.state(),before)
        self.assertEqual(self.state()['report']['bank_paise'],10000)
        event = next(e for e in self.state()['tables']['events'] if e['kind']=='account_opening')
        self.assertEqual(event['actor_id'],'DEMO-OWNER')
        with self.assertRaises(ValueError):
            server.create_server(self.path,reviewed=True)

    def test_opening_collection_report_is_not_new_sale_or_current_receipt(self):
        self.reviewed_workspace()
        self.assertEqual(self.event('account_register',dict(account_id='BANK',name='Fictional bank',
            account_type='bank',review_note='Owner declares account'))[0],200)
        self.assertEqual(self.event('account_opening',dict(account_id='BANK',amount_paise='0',
            opening_as_of='2026-02-01',review_note='Verified fictional zero',evidence_ref='ZERO'))[0],200)
        status, opening = self.event('party_opening',dict(party_id='DEMO-BUYER',balance_kind='customer_receivable',
            amount_paise='500',opening_as_of='2026-02-01',review_note='Fictional carryforward',evidence_ref='BOOK'))
        self.assertEqual(status,200)
        self.assertEqual(self.event('openings_lock',dict(review_note='Ready for posting'))[0],200)
        self.assertEqual(self.event('opening_settlement',dict(opening_id=opening['reference'],account_id='BANK',amount_paise='200'))[0],200)
        report = self.get('/api/export')[1]
        self.assertEqual(report['metrics']['closing_bank']['value'],20000)
        self.assertEqual(report['metrics']['opening_receivable_collections']['value'],20000)
        self.assertEqual(report['metrics']['sales_face_amount']['value'],0)
        self.assertEqual(report['metrics']['customer_receipts']['value'],0)
        self.assertIsNone(report['metrics']['supplier_payable_total']['value'])

    def test_baseline_reopen(self):
        s = self.state()
        self.assertEqual((s['report']['stock_bricks'],s['report']['cash_paise']),(500,350000))
        self.stop()
        self.start()
        self.assertEqual(s, self.state())

    def test_moulding_duplicate_approval_payment(self):
        token=uuid.uuid4().hex
        d=dict(worker_id='WORKER-001',lot_id='LOT-DEMO-001',quantity_bricks=1000,rate_per_1000_paise='800.00',receipt_id='RECEIPT-'+token)
        self.assertEqual(self.event('moulding',d,token)[0],200)
        s=self.state()['report']
        w=next(w for w in s['work'] if w['work_id']=='WEB-'+token)
        self.assertEqual((w['earned_paise'],w['paid_paise'],w['outstanding_paise']),(80000,0,80000))
        self.assertEqual(s['stock_bricks'],1500)
        self.assertEqual(self.event('moulding',d,token)[1]['result'],'already_recorded')
        self.assertEqual(self.event('worker_payment',dict(work_id='WEB-'+token,amount_paise='800'))[0],400)
        self.assertEqual(self.event('work_approval',dict(work_id='WEB-'+token))[0],200)
        self.assertEqual(self.state()['report']['cash_paise'],350000)
        self.assertEqual(self.event('worker_payment',dict(work_id='WEB-'+token,amount_paise='800'))[0],200)
        self.assertEqual(self.state()['report']['cash_paise'],270000)

    def test_bill_correction_separate_settlement(self):
        bill=self.state()['report']['bills'][0]['bill_id']
        status,res=self.event('correction_request',dict(bill_id=bill,old_amount_paise='200',new_amount_paise='250',reason='Fictional review',evidence_ref='demo-note'))
        self.assertEqual(status,200)
        correction=res['reference']
        self.assertEqual(self.event('correction_review',dict(correction_id=correction,decision='approved',reason='Demo owner check'))[0],200)
        r=self.state()['report']
        self.assertEqual((r['cash_paise'],r['bill_outstanding_paise']),(350000,5000))
        self.assertEqual(self.event('bill_payment',dict(bill_id=bill,correction_id=correction,amount_paise='50'))[0],200)
        self.assertEqual(self.state()['report']['cash_paise'],345000)

    def test_decimal_sale_receipt_allocation(self):
        status,r=self.event('dispatch_sale',dict(lot_id='LOT-DEMO-001',buyer_id='DEMO-BUYER',grade='first',quantity_bricks=10,unit_price_paise='6.50',dispatch_ref='D-NEW'))
        self.assertEqual(status,200)
        sale=r['reference']
        self.assertEqual(self.state()['report']['cash_paise'],350000)
        status,r=self.event('customer_receipt',dict(buyer_id='DEMO-BUYER',amount_paise='65'))
        self.assertEqual(status,200)
        self.assertEqual(self.event('allocation',dict(receipt_id=r['reference'],sale_id=sale,amount_paise='65'))[0],200)
        s=next(s for s in self.state()['report']['sales'] if s['sale_id']==sale)
        self.assertEqual((s['amount_paise'],s['unsettled_paise']),(6500,0))

    def test_rejected_correction_revision_through_http(self):
        data = dict(bill_id='BILL-001',old_amount_paise='200',new_amount_paise='250',
                    reason='Fictional candidate',evidence_ref='SLIP-A')
        status, first = self.event('correction_request',data)
        self.assertEqual(status,200)
        self.assertEqual(self.event('correction_request',dict(data,evidence_ref='SLIP-B'))[0],400)
        self.assertEqual(self.event('correction_review',dict(correction_id=first['reference'],
            decision='rejected',reason='Need revised evidence'))[0],200)
        status, revised = self.event('correction_request',dict(data,new_amount_paise='240',evidence_ref='SLIP-B'))
        self.assertEqual(status,200)
        self.assertEqual(self.event('correction_review',dict(correction_id=revised['reference'],
            decision='approved',reason='Reviewed revised evidence'))[0],200)
        self.assertEqual((self.state()['report']['cash_paise'],self.state()['report']['bill_outstanding_paise']),
                         (350000,4000))

    def test_return_inspection_http_owner_role_and_report(self):
        status, returned = self.event('return',dict(sale_id='SALE-001',quantity_bricks=10,inspection_note='Ten returned'))
        self.assertEqual(status,200)
        data = dict(return_id=returned['reference'],quantity_bricks=7,grades={'first':7},
                    inspection_note='Owner reviewed seven pieces')
        status, inspection = self.event('return_inspection',data)
        self.assertEqual(status,200)
        s = self.state()
        self.assertEqual((s['report']['stock_bricks'],s['report']['cash_paise']),(510,350000))
        row = s['tables']['return_inspections'][0]
        self.assertEqual(row['inspection_id'],inspection['reference'])
        event = next(e for e in s['tables']['events'] if e['event_id']==inspection['reference'])
        self.assertEqual(event['actor_id'],'DEMO-OWNER')
        report = self.get('/api/export')[1]
        self.assertEqual(report['returned_stock'][0]['remaining_bricks'],3)
        self.assertTrue(any(a['kind']=='review_return' for a in report['attention']))
        self.assertEqual(self.event('return_inspection',dict(data,quantity_bricks=4,grades={'first':4}))[0],400)

    def test_rejected_actions_leave_no_half_records(self):
        before=self.state()
        self.assertEqual(self.event('dispatch_sale',dict(lot_id='LOT-DEMO-001',buyer_id='DEMO-BUYER',grade='first',quantity_bricks=9999,unit_price_paise='6.50',dispatch_ref='TOO-MUCH'))[0],400)
        self.assertEqual(before,self.state())
        self.assertEqual(self.event('bill',dict(supplier_id='DEMO-SUPPLIER',description='Bad amount',amount_paise='1.001'))[0],400)
        self.assertEqual(before,self.state())

    def test_backup_restores_snapshot(self):
        before=self.state()
        status,r=self.request('POST','/api/backup',{})
        self.assertEqual(status,200)
        with closing(server.ledger.open_db(self.path.parent/r['filename'],readonly=True)) as db:
            self.assertEqual(before,server.ledger.snapshot(db))
        self.assertEqual(before,self.state())

    def test_synthetic_sources_are_read_only(self):
        before = self.state()
        source_root = Path(self.temp.name) / 'sources'
        source_root.mkdir()
        fixture = {
            'note': 'Fictional source fixture; not a private plant record.',
            'documents': [{
                'id': 'FICTIONAL-SOURCE-001',
                'type': 'Synthetic dispatch slip',
                'readings': {'quantity_bricks': 25},
                'needs_confirmation': 'Fictional example only; do not post.',
            }],
        }
        (source_root / 'source_review.json').write_text(
            json.dumps(fixture), encoding='utf-8'
        )
        # Redirect only this resource read, leaving real assets and imports intact.
        with patch.object(server, 'HERE', source_root):
            status, received = self.get('/api/sources')
        self.assertEqual(status, 200)
        self.assertEqual(received, fixture)
        self.assertEqual(before, self.state())

    def test_missing_sources_are_empty_and_do_not_post(self):
        before = self.state()
        source_root = Path(self.temp.name) / 'no-sources'
        source_root.mkdir()
        with patch.object(server, 'HERE', source_root):
            status, received = self.get('/api/sources')
        self.assertEqual(status, 200)
        self.assertEqual(received, {
            'note': 'No private source register is installed in this copy.',
            'documents': [],
        })
        self.assertEqual(before, self.state())

    def test_planning_no_postings(self):
        before=self.state()
        status,p=self.request('POST','/api/plan',{'delay':3,'capacity':4000})
        self.assertEqual(status,200)
        self.assertEqual(len(p['scenarios']),2)
        self.assertEqual(p['scenarios'][0]['order_completed_on'],'2026-02-11')
        self.assertEqual(p['scenarios'][1]['order_completed_on'],'2026-02-14')
        self.assertEqual(self.request('POST','/api/plan',{'delay':99,'capacity':4000})[0],400)
        self.assertEqual(before,self.state())

    def test_request_guards_and_assets(self):
        for route in ('/','/app.js','/style.css','/api/export'):
            self.assertEqual(self.get(route)[0],200)
        self.assertEqual(self.get('/../source_review.json')[0],404)
        self.assertEqual(self.request('POST','/api/backup',{}, {'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.request('GET','/api/state',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('POST','/api/backup',{}, {'Origin':'https://evil.example'})[0],403)

    def test_production_grading_losses_returns(self):
        t=uuid.uuid4().hex
        self.assertEqual(self.event('moulding',dict(worker_id='WORKER-001',lot_id='LOT-DEMO-001',quantity_bricks=1000,rate_per_1000_paise='800',receipt_id='R-'+t))[0],200)
        for origin,destination in [('moulded','dry_green'),('dry_green','loaded'),('loaded','cooling')]:
            self.assertEqual(self.event('transfer',dict(lot_id='LOT-DEMO-001',from_bucket=origin,to_bucket=destination,quantity_bricks=1000))[0],200)
        before=self.state()
        d=dict(lot_id='LOT-DEMO-001',from_bucket='cooling',quantity_bricks=1000,grades={'first':900})
        self.assertEqual(self.event('grading',d)[0],400)
        self.assertEqual(before,self.state())
        d['grades']={'first':800,'lal_petti':80,'pila_petti':60,'unclassified':60}
        self.assertEqual(self.event('grading',d)[0],200)
        for kind in ('loss','internal_use'):
            self.assertEqual(self.event(kind,dict(lot_id='LOT-DEMO-001',from_bucket='unclassified',quantity_bricks=10,reason='Fictional observation'))[0],200)
        sale=self.state()['report']['sales'][0]['sale_id']
        self.assertEqual(self.event('return',dict(sale_id=sale,quantity_bricks=5,inspection_note='Review physical return'))[0],200)
        p=self.get('/api/export')[1]
        self.assertEqual(p['stock_reconciliation'][0]['difference'],0)
        self.assertTrue(any(a['kind']=='review_return' for a in p['attention']))

    def test_process_observation_missing_is_unknown(self):
        status,p=self.get('/api/process')
        self.assertEqual(status,200)
        self.assertEqual(p['cases'][0]['minutes'],360)
        self.assertIsNone(p['cases'][1]['minutes'])


if __name__=='__main__':
    unittest.main()
