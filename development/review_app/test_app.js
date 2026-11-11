'use strict';

// Exercise the real browser event handlers without a browser, server or database.
// Run from this folder with: node --test test_app.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const startup = /refresh\(\)\.then\(render\)\.catch\(e=>message\('Could not open the workspace: '\+e\.message,true\)\);\s*$/;
assert.match(source, startup, 'Update the harness if the application startup changes.');

function harness({ post, refresh, controls = {} } = {}) {
    const requests = [];
    const forms = [];
    const messageBox = { className: '', textContent: '' };
    let sequence = 0;
    let currentForm;
    let refreshCount = 0;
    const savedState = { csrf: 'fictional-token', snapshot: { report: {} } };

    function mountForm() {
        const button = { disabled: false };
        currentForm = {
            dataset: { kind: 'moulding' },
            values: {
                date: '2026-03-15',
                worker_id: 'WORKER-001',
                lot_id: 'LOT-DEMO-001',
                quantity_bricks: '1000',
                rate_per_1000_paise: '800.00',
            },
            button,
            querySelector(selector) {
                assert.equal(selector, 'button');
                return button;
            },
        };
        forms.push(currentForm);
    }

    mountForm();
    const context = vm.createContext({
        crypto: { randomUUID: () => String(++sequence).padStart(32, '0') },
        document: {
            querySelector(selector) {
                if (selector in controls) return controls[selector];
                if (selector === '#entry-form') return currentForm;
                if (selector === '#message') return messageBox;
                return null;
            },
        },
        window: { addEventListener() {} },
        location: { hash: '#entry/moulding' },
        FormData: class {
            constructor(form) { this.values = form.values; }
            get(key) { return this.values[key]; }
        },
        async fetch(route, options) {
            if (route === '/api/event') {
                const body = JSON.parse(options.body);
                requests.push(body);
                const result = post
                    ? await post(body, requests.length)
                    : { result: 'recorded', reference: 'WEB-' + body.token };
                return { ok: true, json: async () => result };
            }
            assert.equal(route, '/api/state');
            refreshCount += 1;
            if (refresh) await refresh(refreshCount);
            return { ok: true, json: async () => savedState };
        },
        mountForm,
        savedState,
    });

    // Skip initial loading only. Keep the application's API and refresh behavior.
    vm.runInContext(source.replace(startup, ''), context, { filename: 'app.js' });
    vm.runInContext(`
        state = savedState;
        render = async () => { mountForm(); bind(); };
        bind();
    `, context);

    return {
        requests,
        forms,
        messageBox,
        context,
        get form() { return currentForm; },
        get refreshCount() { return refreshCount; },
        submit(form = currentForm) {
            return form.onsubmit({ preventDefault() {}, target: form });
        },
    };
}

test('a saved entry stays locked when refreshing the screen fails', async () => {
    const h = harness({ refresh: async () => { throw Error('Temporary refresh failure'); } });
    const submittedForm = h.form;
    await h.submit(submittedForm);

    assert.equal(h.requests.length, 1);
    assert.equal(submittedForm.button.disabled, true);
    assert.match(h.messageBox.textContent, /saved/i);
    assert.match(h.messageBox.textContent, /reload|refresh/i);
    assert.doesNotMatch(h.messageBox.textContent, /not saved/i);

    // Call the handler directly as well as checking the disabled visual control.
    await h.submit(submittedForm);
    assert.equal(h.requests.length, 1, 'A second submission must not post duplicate work.');
});

test('an uncertain POST can retry with the same token and linked receipt', async () => {
    const committedTokens = new Set();
    const h = harness({
        post: async (body, attempt) => {
            const alreadyRecorded = committedTokens.has(body.token);
            committedTokens.add(body.token);
            // Simulate a write succeeding, but its response being lost.
            if (attempt === 1) throw TypeError('Connection lost before the response arrived');
            return { result: alreadyRecorded ? 'already_recorded' : 'recorded', reference: 'WEB-' + body.token };
        },
    });
    const submittedForm = h.form;
    await h.submit(submittedForm);
    assert.equal(submittedForm.button.disabled, false, 'An uncertain response should permit a safe retry.');
    assert.equal(h.refreshCount, 0);
    assert.doesNotMatch(h.messageBox.textContent, /^saved\b/i, 'A missing response is not confirmation.');

    await h.submit(submittedForm);
    assert.equal(h.requests.length, 2);
    assert.equal(h.requests[0].token, h.requests[1].token);
    assert.deepEqual(h.requests[0].data, h.requests[1].data);
    assert.equal(committedTokens.size, 1, 'The retry still represents one committed operation.');
    assert.match(h.messageBox.textContent, /already saved|no duplicate/i);
});

test('a successful save and refresh allows a new entry with a fresh token', async () => {
    const h = harness();
    const firstForm = h.form;
    await h.submit(firstForm);

    assert.notEqual(h.form, firstForm);
    assert.equal(h.form.button.disabled, false);
    assert.equal(h.refreshCount, 1);
    await h.submit();

    assert.equal(h.requests.length, 2);
    assert.notEqual(h.requests[0].token, h.requests[1].token);
    assert.notEqual(h.requests[0].data.receipt_id, h.requests[1].data.receipt_id);
    assert.equal(h.refreshCount, 2);
});

test('a second submit during a pending POST does not create another request', async () => {
    let finishPost;
    const h = harness({
        post: body => new Promise(resolve => {
            finishPost = () => resolve({ result: 'recorded', reference: 'WEB-' + body.token });
        }),
    });
    const submittedForm = h.form;
    const pending = h.submit(submittedForm);
    assert.equal(submittedForm.button.disabled, true);
    assert.equal(h.requests.length, 1);
    await h.submit(submittedForm);
    assert.equal(h.requests.length, 1);

    finishPost();
    await pending;
    assert.equal(h.refreshCount, 1);
});

test('sales offer only the three grades accepted by the ledger', () => {
    const h = harness();
    const grades = vm.runInContext("options('grade', 'dispatch_sale').map(([value]) => value)", h.context);
    assert.deepEqual(Array.from(grades), ['first', 'lal_petti', 'pila_petti']);
});

test('return inspection submits positive grade counts and preserves the return reference', async () => {
    const h = harness();
    h.form.dataset.kind = 'return_inspection';
    h.form.values = { date: '2026-03-15', return_id: 'RETURN-A', quantity_bricks: '3',
        first: '2', lal_petti: '1', pila_petti: '0', inspection_note: 'Owner reviewed three' };
    await h.submit();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].kind, 'return_inspection');
    assert.deepEqual(h.requests[0].data, { return_id: 'RETURN-A', quantity_bricks: 3,
        grades: { first: 2, lal_petti: 1 }, inspection_note: 'Owner reviewed three' });
});

test('correction and return selectors expose only eligible records', () => {
    const h = harness();
    h.context.savedState.snapshot.report = { schema_version: 2,
        bills: ['BILL-REJECTED', 'BILL-PENDING', 'BILL-APPROVED'].map(bill_id =>
            ({ bill_id, description: 'Fictional bill', outstanding_paise: 0 })),
        corrections: [
            { bill_id: 'BILL-REJECTED', decision: 'rejected' },
            { bill_id: 'BILL-PENDING', decision: null },
            { bill_id: 'BILL-APPROVED', decision: 'approved' }],
        returns: [{ return_id: 'RETURN-PARTIAL', remaining_bricks: 3 },
            { return_id: 'RETURN-DONE', remaining_bricks: 0 }] };
    const bills = vm.runInContext("options('bill_id','correction_request').map(([v])=>v)", h.context);
    const returns = vm.runInContext("options('return_id','return_inspection').map(([v])=>v)", h.context);
    assert.deepEqual(Array.from(bills), ['BILL-REJECTED']);
    assert.deepEqual(Array.from(returns), ['RETURN-PARTIAL']);
});

test('reviewed payments keep the selected account and decimal money text', async () => {
    const h = harness();
    h.context.savedState.snapshot.report.schema_version = 3;
    h.form.dataset.kind = 'bill_payment';
    h.form.values = {date:'2026-03-15',bill_id:'BILL',account_id:'BANK',
        amount_paise:'25.50',correction_id_optional:''};
    await h.submit();
    assert.deepEqual(h.requests[0].data,{bill_id:'BILL',account_id:'BANK',amount_paise:'25.50'});
});

test('party opening review preserves its as-of date and actual review event date', async () => {
    const h = harness();
    h.form.dataset.kind = 'party_opening';
    h.form.values = {date:'2026-03-15',party_id:'DEMO-BUYER',balance_kind:'customer_receivable',
        amount_paise:'0',opening_as_of:'2026-02-01',review_note:'Fictional zero reviewed',evidence_ref:'BOOK'};
    await h.submit();
    assert.equal(h.requests.length,1);
    assert.equal(h.requests[0].date,'2026-03-15');
    assert.equal(h.requests[0].data.opening_as_of,'2026-02-01');
    assert.equal(h.requests[0].data.amount_paise,'0');
});

test('fresh money setup shows missing amounts without seeded demonstration figures', () => {
    const h = harness();
    h.context.savedState.snapshot.report = {schema_version:3,finance_mode:'reviewed_accounts',
        openings_locked:false,account_balances:[],party_balances:[],party_openings:[],
        cash_paise:null,bank_paise:null,funds_paise:null};
    const html = vm.runInContext('finance()',h.context);
    assert.match(html,/Money setup/);
    assert.match(html,/Not established|Not recorded/);
    assert.match(html,/unknown|missing|pending/i);
    assert.doesNotMatch(html,/3,500|800|sold 500/);
});

test('party selection refreshes only eligible missing opening kinds and keeps entered amount', () => {
    const controls = {};
    const h = harness({controls});
    h.context.savedState.setup = {parties:[
        {party_id:'SUPPLIER',role:'supplier'},{party_id:'BUYER',role:'buyer'}]};
    h.context.savedState.snapshot.report.party_balances = [
        {party_id:'SUPPLIER',balance_kind:'supplier_payable',opening_id:null},
        {party_id:'BUYER',balance_kind:'customer_receivable',opening_id:'KNOWN-ZERO'},
        {party_id:'BUYER',balance_kind:'customer_advance',opening_id:null}];
    controls['#party_id'] = {value:'SUPPLIER'};
    controls['#balance_kind'] = {innerHTML:''};
    controls['#amount_paise'] = {value:'125.50'};
    vm.runInContext('bind()',h.context);
    assert.match(controls['#balance_kind'].innerHTML,/supplier_payable/);
    assert.doesNotMatch(controls['#balance_kind'].innerHTML,/worker_payable|customer_/);
    controls['#party_id'].value = 'BUYER';
    controls['#party_id'].onchange();
    assert.match(controls['#balance_kind'].innerHTML,/customer_advance/);
    assert.doesNotMatch(controls['#balance_kind'].innerHTML,/customer_receivable|worker_payable|supplier_payable/);
    assert.equal(controls['#amount_paise'].value,'125.50');
});

test('fresh guide uses the reviewed-opening walkthrough instead of seeded balances', () => {
    const h = harness();
    h.context.savedState.snapshot.report.finance_mode = 'reviewed_accounts';
    const html = vm.runInContext('guide()',h.context);
    assert.match(html,/fresh fictional workspace/i);
    assert.match(html,/Money setup/);
    assert.doesNotMatch(html,/3,500|3,450|1,000 bricks/);
});
