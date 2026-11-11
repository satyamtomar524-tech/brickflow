"""BrickFlow local review workspace. Fictional transactions; no public hosting."""
import argparse
from contextlib import closing
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for folder in ('chapter3', 'chapter6', 'plant_map'):
    sys.path.insert(0, str(HERE.parent / folder))
sys.path.insert(0, str(ROOT / 'prototype'))
import ledger
import demo
import owner_report
import process_wait
from planning_engine import plan


def money(value):
    ledger.require(isinstance(value, str), 'Enter money as decimal text in rupees.')
    try:
        n = Decimal(value)
        ledger.require(n.is_finite() and n >= 0 and n * 100 == (n * 100).to_integral_value(),
                       'Money must be nonnegative, with at most two decimal places.')
        return ledger.integer(int(n * 100))
    except InvalidOperation as exc:
        raise ValueError('Invalid money amount.') from exc


def event_from_form(body):
    ledger.require(set(body) == {'kind', 'date', 'token', 'data'}, 'Unexpected entry fields.')
    kind, token = body['kind'], body['token']
    ledger.require(kind in ledger.KINDS, 'Unknown record type.')
    ledger.require(isinstance(token, str) and len(token) == 32 and all(c in '0123456789abcdef' for c in token), 'Invalid submission token.')
    data = dict(body['data'])
    for key in list(data):
        if key.endswith('_paise'):
            data[key] = money(data[key])
    return dict(event_id='WEB-' + token, request_id='WEB-REQUEST-' + token,
                kind=kind, business_date=body['date'],
                actor_id='DEMO-OWNER' if kind in ('work_approval', 'correction_review', 'loss', 'internal_use', 'return_inspection',
                                                'account_register','account_opening','party_opening','openings_lock') else 'DEMO-ACCOUNTANT',
                source_ref='review-workspace/' + token, evidence_status='fictional', data=data)


def create_server(path, port=0, *, reviewed=False):
    path = Path(path).resolve()
    ledger.require(not (reviewed and path.exists()),'Reviewed setup requires a new database path; no reset or overwrite')
    path.parent.mkdir(parents=True, exist_ok=True)
    setup, events = demo.example()
    setup.update(plant_id='REVIEW-FICTIONAL-PLANT', period_end='2026-12-31')
    if reviewed:
        setup['finance_mode'] = 'reviewed_accounts'
        for field in ('opening_cash_paise','opening_worker_outstanding_paise',
                      'opening_supplier_outstanding_paise','opening_customer_balance_paise'):
            setup[field] = None
        events = []
    elif path.exists():
        with closing(ledger.open_db(path,readonly=True)) as saved:
            original_setup = json.loads(saved.execute('SELECT setup_json FROM context').fetchone()[0])
        if original_setup.get('finance_mode') == 'reviewed_accounts':
            setup, events = original_setup, []
    with closing(ledger.open_db(path, setup)) as db:
        # Every seed event is idempotent; restarting completes an interrupted seed.
        for event in events:
            ledger.record_event(db, event)
    csrf = secrets.token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            origin = 'http://127.0.0.1:' + str(self.server.server_port)
            return self.headers.get('Host') == origin[7:] and self.headers.get('Origin', origin) == origin and self.headers.get('Sec-Fetch-Site', 'same-origin') in ('same-origin', 'none')

        def send(self, value, status=200, content_type='application/json; charset=utf-8'):
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            for k, v in {'Content-Type': content_type, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                         'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.allowed():
                self.send({'error': 'Local same-origin access only.'}, 403)
                return
            if self.path in ('/', '/app.js', '/style.css'):
                name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[self.path]
                mime = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}[self.path]
                self.send((HERE / name).read_bytes(), content_type=mime + '; charset=utf-8')
            elif self.path == '/api/state':
                with closing(ledger.open_db(path, readonly=True)) as db:
                    saved = ledger.snapshot(db)
                self.send(dict(csrf=csrf, setup=setup, snapshot=saved, report=owner_report.build(path)))
            elif self.path == '/api/sources':
                source = HERE / 'source_review.json'
                self.send(json.loads(source.read_text(encoding='utf-8')) if source.exists() else dict(note='No private source register is installed in this copy.', documents=[]))
            elif self.path == '/api/process':
                examples = process_wait.load_examples()
                self.send(dict(cases=[dict(lot=c['ready']['lot_id'],
                    ready=c['ready']['occurred_at'], loading=c['loading']['occurred_at'] if c['loading'] else None,
                    minutes=process_wait.wait_minutes(c['ready'], c['loading'])) for c in examples['cases']],
                    reason=examples['reason']))
            elif self.path == '/api/export':
                self.send(owner_report.build(path))
            else:
                self.send({'error': 'Page not found.'}, 404)

        def do_POST(self):
            try:
                self.connection.settimeout(5)
                length = int(self.headers.get('Content-Length', '0'))
                ledger.require(0 < length <= 32768, 'Request size is invalid.')
                raw = self.rfile.read(length)
                if not self.allowed() or self.headers.get('X-CSRF-Token') != csrf:
                    self.send({'error': 'Refresh the page; local request verification failed.'}, 403)
                    return
                ledger.require(self.headers.get_content_type() == 'application/json', 'JSON request required.')
                def unique(pairs):
                    obj = {}
                    for key, val in pairs:
                        ledger.require(key not in obj, 'Repeated JSON field.')
                        obj[key] = val
                    return obj
                body = json.loads(raw, object_pairs_hook=unique)
                ledger.require(isinstance(body, dict), 'An object is required.')
                if self.path == '/api/event':
                    event = event_from_form(body)
                    with closing(ledger.open_db(path, setup)) as db:
                        result = ledger.record_event(db, event)
                    self.send(dict(result=result, reference=event['event_id']))
                elif self.path == '/api/plan':
                    ledger.require(set(body) == {'delay', 'capacity'}, 'Unexpected planning fields.')
                    ledger.require(type(body['delay']) is int and 0 <= body['delay'] <= 14, 'Delay must be 0–14 days.')
                    ledger.require(type(body['capacity']) is int and 1 <= body['capacity'] <= 4000, 'Capacity must be 1–4,000 for this fictional experiment.')
                    source = json.loads((ROOT / 'prototype/demo-input.json').read_text(encoding='utf-8'))
                    source['daily_firing_capacity_bricks'] = body['capacity']
                    source['scenarios'] = [{'name': 'Baseline', 'drying_delay_days': 0}, {'name': 'Your scenario', 'drying_delay_days': body['delay']}]
                    self.send(plan(source))
                elif self.path == '/api/backup':
                    ledger.require(body == {}, 'Backup takes no fields.')
                    destination = path.parent / ('verified-backup-' + uuid.uuid4().hex + '.sqlite3')
                    with closing(ledger.open_db(path, readonly=True)) as db:
                        ledger.backup_and_verify(db, destination)
                    self.send(dict(result='Backup restored and compared successfully.', filename=destination.name))
                else:
                    self.send({'error': 'Action not found.'}, 404)
            except (ValueError, TypeError, KeyError, UnicodeError, sqlite3.IntegrityError) as exc:
                self.send({'error': str(exc)}, 400)
            except (sqlite3.OperationalError, OSError):
                self.send({'error': 'Storage is unavailable. No success is confirmed; retry the same submission after checking local storage.'}, 503)

    return HTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=HERE / 'data/review.sqlite3')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--reviewed', action='store_true', help='Start a NEW fictional workspace for reviewed account openings')
    args = parser.parse_args()
    server = create_server(args.db, args.port, reviewed=args.reviewed)
    print(f'BrickFlow review: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
