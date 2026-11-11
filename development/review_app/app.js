'use strict';
let state, token = crypto.randomUUID().replaceAll('-', '');
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = v => String(v).replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const inr = p => p == null ? 'Not established' : new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:2}).format(p / 100);
const num = n => Number(n).toLocaleString('en-IN');
const pages = {overview:'Overview',entry:'Record an activity',finance:'Money setup',reports:'Owner’s report',records:'Record trail',map:'Plant map',planning:'What-if planning',process:'Process insights',sources:'Book review',guide:'Guide & next steps'};
const hints = {
 moulding:'Count the individual’s work. This creates earnings and a linked moulded-stock receipt together. It does not pay the worker.',
 work_approval:'Owner review only. Approval changes the approval record, never the cash balance.',
 worker_payment:'Record money actually paid against approved work. Earnings are not charged a second time.',
 transfer:'Move an observed quantity to its next production stage. Dates alone do not prove bricks are ready.',
 grading:'Record inspected output after cooling. All grade quantities must add up to the quantity being classified.',
 bill:'Record a supplier’s bill. Cash changes only when you separately record its payment.',
 bill_payment:'Record an actual payment against an existing bill. Select an approved correction when settling its additional amount.',
 customer_receipt:'Record money actually received from the buyer. It can remain an advance until allocated to a sale.',
 dispatch_sale:'Record bricks leaving stock and their sale value together. This does not create a cash receipt. External delivery costs remain outside scope.',
 allocation:'Match existing customer money to an existing sale. This does not move cash again.',
 correction_request:'Keep the original bill. Propose its replacement amount with a reason and evidence reference.',
 correction_review:'Owner approves or rejects a request. An approval changes what is owed, not what has been paid.',
 loss:'Remove counted damaged pieces from stock with a reason. Lower grades are not automatically losses.',
 internal_use:'Record pieces used for plant repairs. This is neither a customer sale nor a cash payment.',
 return:'Record a physical return into unclassified stock. Refunds and financial credit notes remain outside this prototype.',
 return_inspection:'Owner reviews a specified return and assigns inspected pieces to saleable grades. Uninspected or damaged pieces remain on hold. Cash and sale value do not change.'};
const specs = {
 moulding:['worker_id','lot_id','quantity_bricks','rate_per_1000_paise'], work_approval:['work_id'],
 worker_payment:['work_id','amount_paise'], transfer:['lot_id','from_bucket','to_bucket','quantity_bricks'],
 grading:['lot_id','quantity_bricks','first','lal_petti','pila_petti','unclassified'],
 bill:['supplier_id','description','amount_paise'], bill_payment:['bill_id','amount_paise','correction_id_optional'],
 customer_receipt:['buyer_id','amount_paise'], dispatch_sale:['lot_id','buyer_id','grade','quantity_bricks','unit_price_paise'],
 allocation:['receipt_id','sale_id','amount_paise'], correction_request:['bill_id','old_amount_paise','new_amount_paise','reason','evidence_ref'],
 correction_review:['correction_id','decision','reason'], loss:['lot_id','from_bucket','quantity_bricks','reason'],
 internal_use:['lot_id','from_bucket','quantity_bricks','reason'], return:['sale_id','quantity_bricks','inspection_note'],
 return_inspection:['return_id','quantity_bricks','first','lal_petti','pila_petti','inspection_note']};
Object.assign(hints, {
 account_register:'Name a cash or bank account used in the plant books.',
 account_opening:'Review money held in this account at period start. Enter zero only when confirmed; an empty amount is unknown.',
 party_opening:'Review a carried-forward amount separately from this period’s earnings, bills and sales. Customer debt and customer advances are separate.',
 openings_lock:'Owner starts current posting. Registered account openings become locked. Missing party openings remain pending and can receive one initial review later.',
 opening_settlement:'Pay an opening worker/supplier balance or collect an opening customer debt through the selected account. This is not a new expense or sale.',
 opening_advance_allocation:'Apply an opening customer advance to a sale. No money is received again.'
});
Object.assign(specs, {
 account_register:['new_account_id','name','account_type','review_note'],
 account_opening:['account_id','amount_paise','opening_as_of','review_note','evidence_ref'],
 party_opening:['party_id','balance_kind','amount_paise','opening_as_of','review_note','evidence_ref'],
 openings_lock:['review_note'],
 opening_settlement:['opening_id','account_id','amount_paise'],
 opening_advance_allocation:['opening_id','sale_id','amount_paise']
});
for (const kind of ['worker_payment','bill_payment','customer_receipt']) specs[kind].splice(1,0,'account_id');
const financeSetupKinds=['account_register','account_opening','party_opening','openings_lock'];
const openingRoles={worker_payable:'worker',supplier_payable:'supplier',customer_receivable:'buyer',customer_advance:'buyer'};
const openingNames={worker_payable:'Worker payable — we owe',supplier_payable:'Supplier payable — we owe',customer_receivable:'Customer receivable — customer owes us',customer_advance:'Customer advance — held for customer'};
const reviewedMode=()=>state.snapshot.report.finance_mode==='reviewed_accounts';
const stages = ['moulded','dry_green','loaded','cooling','first','lal_petti','pila_petti','unclassified'];
function message(text, error=false){$('#message').className=text?'notice '+(error?'error':''):'';$('#message').textContent=text;}
async function api(path, body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw Error(result.error || 'Request failed');return result;}
async function refresh(){state=await api('/api/state');}
function heading(title, sub){return `<h1>${title}</h1><p class="muted tagline">${sub}</p>`;}
function table(headers, rows){return rows.length?`<div class="scroll"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<p class="empty">No records yet.</p>';}
function metric(name,value,target='reports'){return `<div class="card metric"><small>${name}</small><strong>${value}</strong><a href="#${target}">See supporting records →</a></div>`;}
function overview(){const r=state.snapshot.report, c=state.report.stock_reconciliation[0];const net=c.dispatched-c.returned;const denominator=net+c.on_hand+c.loss+c.internal_use;const pct=denominator?100*net/denominator:0;
 return heading('A clear view of your plant.', 'One connected record book, from individual work to the owner’s report.')+
 `<div class="hero"><div><span class="pill">FICTIONAL REVIEW • 2026</span><h2 class="section">Follow one lot. Understand every number.</h2><p>${reviewedMode()?'Fresh fictional workspace: no operational records were preloaded. Begin with reviewed account and party openings.':'The reference example moulded 1,000 bricks, sold 500 and paid the recorded bills. New entries update this fictional workspace.'}</p><div class="flow"><span>Work</span> → <span>Production</span> → <span>Stock</span> → <span>Sales & money</span></div></div><a class="button" href="#entry">Record an activity →</a></div>`+
 `<div class="grid">${metric('Bricks on hand',num(r.stock_bricks))}${metric('Physical cash',inr(r.cash_paise),'finance')}${metric('Bank funds',inr(r.bank_paise),'finance')}${metric('Combined recorded funds',inr(r.funds_paise),'finance')}${metric('Current work unpaid',inr(r.worker_outstanding_paise))}${metric('Sales face value',inr(r.sales_face_paise))}</div>`+
 `<div class="two"><section class="card"><h2>Where the bricks are</h2><p class="muted">Current recorded stage, not a physical stocktake.</p>${stages.map(b=>{const n=r.stock.filter(s=>s.bucket===b).reduce((a,s)=>a+s.quantity_bricks,0);return `<div class="chart-row"><span>${label(b)}</span><div class="track"><div class="bar" style="width:${r.stock_bricks?100*n/r.stock_bricks:0}%"></div></div><b>${num(n)}</b></div>`;}).join('')}</section>`+
 `<section class="card"><h2>Dispatched and remaining</h2><p class="muted">Physical quantities. A dispatch is separate from payment.</p><div class="chart-flex"><div class="donut" style="background:conic-gradient(#42694e ${pct}%,#d9e4d0 0)"><div><strong>${Math.round(pct)}%</strong><small>net dispatched</small></div></div><div><span class="key"><i class="dot"></i>${num(net)} net dispatched</span><span class="key"><i class="dot light"></i>${num(c.on_hand)} on hand</span><small>${num(c.loss+c.internal_use)} loss / internal use</small></div></div><p class="section">Stock equation: <b>${c.balanced?'balanced':'needs investigation'}</b></p><a href="#reports">Check the calculation →</a></section></div>`+
 `<section class="card section"><h2>Needs attention · ${state.report.attention.length}</h2>${state.report.attention.slice(0,5).map(a=>`<p><b>${esc(label(a.kind))}</b> — ${esc(a.reason)} <a href="#records">${esc(a.reference)}</a></p>`).join('')||'<p>There are no current record alerts.</p>'}<p class="muted">These alerts come from saved records. Live weather and government monitoring are not connected.</p></section>`;
}
function options(key,kind){const r=state.snapshot.report;let choices;
 if(key==='account_type')return [['cash','Cash'],['bank','Bank']];
 if(key==='balance_kind')return Object.entries(openingNames);
 if(key==='account_id')return (r.account_balances||[]).filter(a=>kind==='account_opening'?a.opening_paise==null:a.closing_paise!=null).map(a=>[a.account_id,`${a.name} · ${label(a.account_type)} · ${inr(a.closing_paise)}`]);
 if(key==='party_id'){const pending=new Set((r.party_balances||[]).filter(p=>p.opening_id==null).map(p=>p.party_id));return state.setup.parties.filter(p=>pending.has(p.party_id)).map(p=>[p.party_id,`${p.name} · ${p.role}`]);}
 if(key==='opening_id')return (r.party_openings||[]).filter(o=>o.remaining_paise>0&&(kind==='opening_advance_allocation'?o.balance_kind==='customer_advance':o.balance_kind!=='customer_advance')).map(o=>[o.opening_id,`${o.opening_id} · ${openingNames[o.balance_kind]} · ${inr(o.remaining_paise)}`]);
 if(key.endsWith('_optional'))return [['','No correction'],...r.corrections.filter(c=>c.decision==='approved').map(c=>[c.correction_id,c.correction_id])];
 if(['worker_id','supplier_id','buyer_id'].includes(key)){const role=key.split('_')[0];return state.setup.parties.filter(p=>p.role===role).map(p=>[p.party_id,p.name]);}
 if(key==='lot_id')return state.setup.lots.map(l=>[l.lot_id,l.lot_id]);
 if(key==='from_bucket')choices=stages;
 if(key==='to_bucket')choices=['dry_green','loaded','cooling'];
 if(key==='grade')choices=['first','lal_petti','pila_petti'];
 if(key==='decision')choices=['approved','rejected'];
 if(choices)return choices.map(c=>[c,label(c)]);
 if(key==='work_id')return r.work.filter(w=>kind==='work_approval'?!w.approval_id:w.approval_id&&w.outstanding_paise>0).map(w=>[w.work_id,`${w.work_id} · ${num(w.quantity_bricks)} bricks · ${inr(w.outstanding_paise)} owed`]);
 if(key==='bill_id')return r.bills.filter(b=>kind!=='correction_request'||!r.corrections.some(c=>c.bill_id===b.bill_id&&(c.decision!=='rejected'||r.schema_version===1))).map(b=>[b.bill_id,`${b.bill_id} · ${b.description} · ${inr(b.outstanding_paise)} owed`]);
 if(key==='return_id')return (r.returns||[]).filter(r=>r.remaining_bricks>0).map(r=>[r.return_id,`${r.return_id} · ${num(r.remaining_bricks)} awaiting inspection`]);
 if(key==='sale_id')return r.sales.map(s=>[s.sale_id,`${s.sale_id} · ${num(s.quantity_bricks)} bricks`]);
 if(key==='receipt_id')return r.customer_receipts.filter(r=>r.unallocated_paise>0).map(r=>[r.cash_id,`${r.cash_id} · ${inr(r.unallocated_paise)} unmatched`]);
 if(key==='correction_id')return r.corrections.filter(c=>!c.decision).map(c=>[c.correction_id,c.correction_id]);
 return null;
}
function control(key,kind){
 const opts=options(key,kind);
 const title=key==='new_account_id'?'New account reference':key==='opening_as_of'?'Opening as-of date':key.endsWith('_paise')?label(key.replace('_paise',''))+' (₹)':label(key.replace('_optional',''));
 const integer=['quantity_bricks','first','lal_petti','pila_petti','unclassified'].includes(key), monetary=key.endsWith('_paise');
 if(key==='opening_as_of')return `<div><label for="${key}">${title}</label><input type="date" id="${key}" name="${key}" value="${state.setup.period_start}" readonly required></div>`;
 const value=key==='rate_per_1000_paise'?reviewedMode()?'':'800.00':integer?key==='quantity_bricks'?reviewedMode()?'':'1000':'0':'';
 return `<div><label for="${key}">${title}</label>`+(opts?`<select id="${key}" name="${key}" ${key.endsWith('optional')?'':'required'}>${opts.length?opts.map(([v,t])=>`<option value="${esc(v)}">${esc(t)}</option>`).join(''):'<option value="">No eligible record — complete the earlier step</option>'}</select>`:`<input id="${key}" name="${key}" required ${integer?'type="number" min="'+(key==='quantity_bricks'?1:0)+'" max="100000000" step="1"':monetary?'type="text" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?"':'type="text" maxlength="400"'} value="${value}">`)+ '</div>';
}
function entry(){
 const kind=location.hash.split('/')[1]||'moulding', r=state.snapshot.report;
 if(!specs[kind])return heading('Unknown activity','Select an activity from the navigation.');
 if(reviewedMode()&&!r.openings_locked&&!financeSetupKinds.includes(kind))return heading('Review account openings first.','Owner review prepares the account balances before current posting.')+`<section class="card"><a href="#finance">Open Money setup →</a></section>`;
 if(reviewedMode()&&r.openings_locked&&['account_register','account_opening','openings_lock'].includes(kind))return heading('Account setup is locked.','Reviewed account openings cannot be replaced after posting starts.')+`<section class="card"><a href="#finance">View Money setup →</a></section>`;
 const last=state.snapshot.tables.events.at(-1)?.business_date||state.setup.period_start;
 const fields=specs[kind].filter(k=>k!=='account_id'||r.schema_version>=3);
 const owner=['work_approval','correction_review','loss','internal_use','return_inspection',...financeSetupKinds].includes(kind);
 return heading('Record one activity.','Choose what happened. Opening balances, current activity and money movements stay distinct.')+`<div class="two"><section class="card"><label for="activity">Activity</label><select id="activity">${Object.keys(specs).map(k=>`<option value="${k}" ${k===kind?'selected':''}>${label(k)}</option>`).join('')}</select><p class="section">${hints[kind]}</p>${r.schema_version<3?'<p class="warn">Legacy database. Named accounts need an explicitly upgraded copy; see the local project guide.</p>':''}<form id="entry-form" data-kind="${kind}"><label for="date">Business / review date</label><input id="date" name="date" type="date" min="${last}" max="${state.setup.period_end}" value="${last}" required><div class="fields">${fields.map(k=>control(k,kind)).join('')}</div><button>Save fictional record</button><p class="muted">Saved locally. Repeating the same submission cannot add it twice.</p></form></section><section class="card"><h2>Before you save</h2><p><b>Current stock:</b> ${num(r.stock_bricks)} bricks</p><p><b>Physical cash:</b> ${inr(r.cash_paise)}</p><p><b>Bank funds:</b> ${inr(r.bank_paise)}</p><p><b>Entry role:</b> ${owner?'Demo owner':'Demo accountant'}</p><p>Roles demonstrate responsibilities; these are not authenticated accounts.</p><div class="warn">New event dates must be on or after ${last}. An opening as-of date is separately fixed at period start.</div><p class="section">Worker and supplier advances, loans and account transfers are outside this slice.</p><a href="#finance">Review money setup →</a><p><a href="#records">Inspect supporting records →</a></p></section></div>`;
}
function finance(){
 const r=state.snapshot.report, fresh=r.finance_mode==='reviewed_accounts';
 const phase=fresh?r.openings_locked?'Account setup locked':'Preparing openings':'Legacy declarations carried forward';
 const links=fresh?`${!r.openings_locked?'<a href="#entry/account_register">1. Name a cash or bank account →</a><p><a href="#entry/account_opening">2. Review each account opening →</a></p>':''}<a href="#entry/party_opening">Review a missing party opening →</a>${!r.openings_locked?'<p><a href="#entry/openings_lock">3. Owner locks account setup →</a></p>':'<p><a href="#entry/opening_settlement">Pay / collect an opening balance →</a></p><a href="#entry/opening_advance_allocation">Apply an opening customer advance →</a>'}`:'<p>These are preserved fictional single-cash and zero-obligation assumptions, not a newly verified owner review. Use a fresh reviewed workspace for new opening setup.</p>';
 return heading('Money setup.',phase)+`<div class="grid">${metric('Physical cash',inr(r.cash_paise),'finance')}${metric('Bank funds',inr(r.bank_paise),'finance')}${metric('Combined recorded funds',inr(r.funds_paise),'finance')}</div><section class="card section"><h2>Review before posting</h2><p>Missing openings remain unknown. Enter zero only when reviewed. Registered account openings must be known before locking; missing party openings remain pending.</p>${links}<p>Reviewed amounts stay immutable. A previously missing party opening can receive one dated review later.</p></section><section class="card section"><h2>Named accounts</h2>${table(['Account','Type','Opening','Current receipts','Current payments','Opening collections','Opening payments','Recorded closing','Evidence basis'],(r.account_balances||[]).map(a=>[a.name,label(a.account_type),inr(a.opening_paise),inr(a.receipts_paise),inr(a.payments_paise),inr(a.opening_collections_paise??0),inr(a.opening_payments_paise??0),inr(a.closing_paise),label(a.source_basis)]))}</section><section class="card section"><h2>Party balances</h2><p>Customer debt and advances stay separate. Totals with missing openings are unavailable; current balances alone are partial.</p>${table(['Party','Balance direction','Opening','Opening remaining','Current period','Total','Evidence basis'],(r.party_balances||[]).map(p=>[p.name,openingNames[p.balance_kind],inr(p.opening_amount_paise),inr(p.opening_remaining_paise),inr(p.current_balance_paise),inr(p.total_balance_paise),label(p.source_basis)]))}</section><section class="card section"><h2>Opening review references</h2>${table(['Opening reference','Party','Kind','As of','Amount','Review event','Evidence basis'],(r.party_openings||[]).map(p=>[p.opening_id,p.party_id,openingNames[p.balance_kind],p.opening_as_of,inr(p.amount_paise),p.review_event_id||'Legacy source',label(p.source_basis)]))}<a href="#records">Read source events and settlement links →</a><p>Opening balances are carried-forward stocks. They are not new sales, current costs or new receipts. Worker/supplier advances and refunds are not modeled here.</p></section>`;
}
function reports(){const p=state.report;return heading('The owner’s report.',`${p.period_start} to ${p.period_end} · Whole saved period · Fictional figures`)+
 `<div class="grid">${metric('Wages earned',inr(p.metrics.wages_earned.value),'records')}${metric('Wages paid',inr(p.metrics.wages_paid.value),'records')}${metric('Current bills unpaid',inr(p.metrics.supplier_outstanding.value),'records')}${metric('Profit','Not established','guide')}</div>`+
 `<section class="card section"><h2>Stock reconciliation</h2>${table(['Lot','Opening','Moulded','Returned','Dispatched','Loss','Internal use','On hand','Difference'],p.stock_reconciliation.map(c=>[c.lot_id,c.opening,c.moulded,c.returned,c.dispatched,c.loss,c.internal_use,c.on_hand,c.difference]))}<p class="muted">Opening + moulded + returns − dispatches − loss − internal use = on hand.</p></section>`+
 `<section class="card section"><h2>Trace each measure</h2>${Object.entries(p.metrics).map(([k,m])=>`<details><summary>${esc(label(k))} · ${m.value===null?'Not established':m.unit==='paise'?inr(m.value):num(m.value)+' '+esc(m.unit)}</summary><p>${esc(m.meaning)}</p><p>Sources: ${m.sources.map(s=>`<a href="#records">${esc(s)}</a>`).join(', ')||'No supporting records yet.'}</p></details>`).join('')}</section><div class="section"><button id="export" class="secondary">Download fictional report</button> <button id="backup">Create & verify local backup</button></div>`;}
function records(){const r=state.snapshot.report;return heading('Every total has a trail.', 'Original events stay preserved. Approval, correction and settlement are separate records.')+
 `<section class="card"><h2>Individual work</h2>${table(['Reference','Bricks','Earned','Approved','Paid','Outstanding'],r.work.map(w=>[w.work_id,w.quantity_bricks,inr(w.earned_paise),w.approval_id||'Pending',inr(w.paid_paise),inr(w.outstanding_paise)]))}</section>`+
 `<section class="card section"><h2>Bills and corrections</h2>${table(['Bill','Description','Original','Effective','Paid','Outstanding'],r.bills.map(b=>[b.bill_id,b.description,inr(b.original_amount_paise),inr(b.current_amount_paise),inr(b.paid_paise),inr(b.outstanding_paise)]))}<a href="#entry/correction_request">Request or revise a rejected correction →</a></section>`+
 `<section class="card section"><h2>Correction history</h2>${table(['Proposal','Bill','Old','Proposed','Decision','Requested by','Reviewed by','Reason'],r.corrections.map(c=>[c.correction_id,c.bill_id,inr(c.old_amount_paise),inr(c.new_amount_paise),c.decision||'Pending',c.requested_by,c.reviewed_by||'Pending',c.reason]))}<p>Rejected proposals remain visible. One pending proposal and one approved correction per bill are supported.</p></section>`+
 `<section class="card section"><h2>Returned stock review</h2>${table(['Return','Sale','Lot','Received','Inspected','Remaining'],(r.returns||[]).map(t=>[t.return_id,t.sale_id,t.lot_id,t.quantity_bricks,t.inspected_bricks,t.remaining_bricks]))}${table(['Inspection','Return','Pieces','Note'],(state.snapshot.tables.return_inspections||[]).map(i=>[i.inspection_id,i.return_id,i.quantity_bricks,i.inspection_note]))}<a href="#entry/return_inspection">Record owner inspection →</a><p>Uninspected or non-saleable pieces remain on hold. Financial review remains separate.</p></section>`+
 `<section class="card section"><h2>Sales</h2>${table(['Reference','Grade','Bricks','Unit price','Face value','Allocated','Unsettled'],r.sales.map(s=>[s.sale_id,label(s.grade),s.quantity_bricks,inr(s.unit_price_paise),inr(s.amount_paise),inr(s.allocated_paise),inr(s.unsettled_paise)]))}</section>`+
 `<section class="card section"><h2>Cash entries</h2>${table(['Reference','Date','Kind','Account','Amount'],r.cash_entries.map(c=>[c.cash_id,c.business_date,label(c.kind),c.account_id||'Legacy cash',inr(c.amount_paise)]))}</section>`+
 `<section class="card section"><h2>Saved event history</h2><p class="muted">Technical amounts below are integer paise. IDs link these events to the tables.</p>${state.snapshot.tables.events.map(e=>`<details><summary>${esc(e.business_date)} · ${esc(label(e.kind))} · ${esc(e.event_id)}</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>`).join('')}<details><summary>Inspect all linked tables</summary><pre>${esc(JSON.stringify(state.snapshot.tables,null,2))}</pre></details></section>`;}
function map(){const r=state.snapshot.report;const zones=[['Soil & clay',null,'Land acquisition and preparation: requirements only.'],['Moulding','moulded','Individual output creates the first stock receipt.'],['Drying yard','dry_green','Record observed drying completion.'],['Kiln','loaded','Loading and firing, followed by cooling.'],['Cooling','cooling','Inspect before assigning grades.'],['Finished storage',null,'First, Lal Petti, Pila Petti and unclassified.']];return heading('Follow the material through the plant.', 'Illustrative zones — not a survey, scaled layout or an optimised design.')+`<section class="map">${zones.map(([n,b,t])=>`<a class="zone" href="#${b?'entry/transfer':'guide'}"><strong>${n}</strong>${b?'<p>'+num(r.stock.filter(s=>s.bucket===b).reduce((a,s)=>a+s.quantity_bricks,0))+' bricks</p>':''}<small>${t}</small></a>`).join('')}</section><section class="card section"><h2>Movement matters</h2><p>Clay → moulding → drying → kiln → cooling → graded storage → dispatch.</p><p>Future layout comparisons need measured distances, safe access routes, land boundaries and actual handling times. This drawing does not establish safe kiln dimensions or fan settings.</p></section>`;}
function planning(){return heading('Compare a possibility, not a promise.', 'An independent fictional 16,000-brick experiment. Changing it never changes your ledger.')+`<div class="two"><section class="card"><h2>What if drying takes longer?</h2><form id="plan-form"><label for="delay">Additional drying days</label><input id="delay" name="delay" type="number" min="0" max="14" value="3" required><label for="capacity">Daily firing capacity (bricks)</label><input id="capacity" name="capacity" type="number" min="1" max="4000" value="4000" required><button>Compare scenarios</button></form></section><section class="card"><h2>What this assumes</h2><p>12,000-brick order; 90% saleable yield; two firing days; three cooling days; fixed dated cash events. These are teaching assumptions, not this plant’s capacity or permitted calendar.</p><p class="warn">No live weather, legal operating permission or validated demand forecast is inferred. A delay does not automatically change cash costs.</p></section></div><section id="plan-result" class="card section"><p>Run the comparison to see completion, stock and cash effects.</p></section>`;}
function guide(){const items=[['Working locally','Individual moulding, linked stock, approvals, payments, transfers, grading, bills, sales, receipts, allocations, corrections, loss and internal use.'],['Working locally','Owner report, record alerts, stock charts, source tracing, downloadable fictional report and verified backup.'],['Illustrative','Plant zones and drying-delay comparisons. Neither is an engineering design or validated forecast.'],['Needs owner review','Private source candidates, when installed, require owner review; uncertain readings stay outside the demo database. No complete real season has been reconciled.'],['Future connection','Ghaziabad weather alerts, government-source update checks and notifications when a source is unavailable. No monitoring service runs yet.'],['Future research','Camera/audio quality assistance, kiln sensors, fuel moisture, supplier quality and layout optimisation require observed data and expert validation.'],['Before live use','Authentication, permissions, full accounts/tax treatment, opening balances, mass-based broken-material sales, returns settlement and operational security review.'],['Future release','Multiple plants, cloud database, student eligibility, private access and annual subscriptions. No cloud account or commercial demand is claimed.']];return heading('Learn the workflow. Review the limits.', 'This is a functional local review prototype, not a finished operational or commercial ERP.')+`<section class="card"><h2>Your first five minutes</h2>${reviewedMode()?'<p>This fresh fictional workspace starts with unknown financial openings. Open Money setup, name accounts, review openings, then lock account setup before current activity. Previously missing party openings can be reviewed once later.</p>':'<ol><li>Check Overview: the untouched starting case has 500 bricks and ₹3,500 cash.</li><li>Record moulding for an individual. Earnings and stock increase together.</li><li>Approve that work, then separately record payment.</li><li>Open the owner’s report and inspect supporting event references.</li><li>Create a backup and check the verified result.</li></ol><p>Try the bill correction: ₹200 → ₹250. Approval leaves cash unchanged and creates ₹50 owed. Paying ₹50 separately reduces baseline cash to ₹3,450.</p>'}</section><section class="roadmap section">${items.map(([s,t])=>`<div class="card"><span class="status">${s}</span><p>${t}</p></div>`).join('')}</section><section class="card section"><h2>Foundation preserved</h2><p>Chapters 1–3: business context, research and the record model. Chapter 4: verification. Chapters 5–7: entry, reporting and scenarios. Chapters 8–9: validation and release methodology. Written completion does not mean live deployment or business validation.</p><p>Money is stored in paise. Wages round half up to the nearest paise. Missing information is not zero. Cash balance is not profit.</p></section>`;}
async function sources(){const s=await api('/api/sources');return heading('From paper books to reviewable records.',s.note)+`<div class="warn">Private-source candidates · not posted · not owner-verified. No original photograph, contact details or customer name is served here.</div><section class="card section">${s.documents.map(d=>`<details><summary>${esc(d.id)} · ${esc(d.type)}</summary>${table(['Field','Candidate reading'],Object.entries(d.readings).map(([k,v])=>[label(k),v]))}<p><b>Needs confirmation:</b> ${esc(d.needs_confirmation)}</p></details>`).join('')}</section>`;}
async function process(){const p=await api('/api/process');return heading('See where work waits.', 'A separate fictional observation exercise, inspired by process analysis. No savings are claimed.')+`<section class="card"><div class="flow"><span>Ready for loading</span> → <span>Waiting interval</span> → <span>Loading starts</span></div>${table(['Lot','Observed ready','Loading started','Elapsed wait'],p.cases.map(c=>[c.lot,c.ready,c.loading||'Not recorded',c.minutes===null?'Unknown':c.minutes+' minutes']))}<p class="muted">An absent observation is unknown, not zero. This interval is not the drying duration.</p></section><section class="card section"><h2>Investigate before changing the process</h2><p><b>Reported explanation, awaiting review:</b> ${esc(p.reason.explanation)}</p><p>Review the reason with the foreman → choose comparable lots → trial a coordination change → record observations → compare quality, breakage and waiting time.</p><p>No trial is approved or observed here. Do not shorten necessary drying, firing or cooling to improve this measure.</p></section>`;}
let renderVersion=0;
async function render(){const version=++renderVersion;const page=location.hash.slice(1).split('/')[0]||'overview';$('#nav').innerHTML=Object.entries(pages).map(([k,v])=>`<a href="#${k}" class="${page===k?'active':''}" ${page===k?'aria-current="page"':''}>${v}</a>`).join('');$('#crumb').textContent=pages[page]||'Not found';let html=page==='sources'?await sources():page==='process'?await process():({overview,entry,finance,reports,records,map,planning,guide}[page]||(()=>heading('Page not found','Choose a page from the navigation.')))();if(version!==renderVersion)return;$('#content').innerHTML=html;bind();}
function bind(){if($('#party_id')&&$('#balance_kind')){const update=()=>{const party=state.setup.parties.find(p=>p.party_id===$('#party_id').value);const pending=state.snapshot.report.party_balances||[];$('#balance_kind').innerHTML=Object.entries(openingNames).filter(([k])=>openingRoles[k]===party?.role&&pending.some(p=>p.party_id===party.party_id&&p.balance_kind===k&&p.opening_id==null)).map(([k,n])=>`<option value="${k}">${n}</option>`).join('');};$('#party_id').onchange=update;update();}if($('#activity'))$('#activity').onchange=e=>{token=crypto.randomUUID().replaceAll('-','');location.hash='entry/'+e.target.value;};
 const form = $('#entry-form');
 if (form) {
  // Keep this form's identity when retrying a request with an uncertain result.
  const submissionToken = token;
  let saving = false;
  let saved = false;

  form.onsubmit = async e => {
   e.preventDefault();
   if (saving || saved) return;
   const button = form.querySelector('button');
   saving = true;
   button.disabled = true;

   try {
    const fields = new FormData(form);
    const kind = form.dataset.kind;
    const data = {};
    for (const key of specs[kind]) {
     let value = fields.get(key);
     if (['quantity_bricks', 'first', 'lal_petti', 'pila_petti', 'unclassified'].includes(key)) {
      value = Number(value);
     }
     if (key === 'account_id' && state.snapshot.report.schema_version < 3) continue;
     if (key === 'new_account_id') {
      data.account_id = value;
     } else if (key === 'correction_id_optional') {
      if (value) data.correction_id = value;
     } else {
      data[key] = value;
     }
    }
    if (kind === 'moulding') data.receipt_id = 'RECEIPT-' + submissionToken;
    if (kind === 'dispatch_sale') data.dispatch_ref = 'DISPATCH-' + submissionToken;
    if (kind === 'grading' || kind === 'return_inspection') {
     if (kind === 'grading') data.from_bucket = 'cooling';
     data.grades = {};
     for (const grade of ['first', 'lal_petti', 'pila_petti', 'unclassified']) {
      if (data[grade] > 0) data.grades[grade] = data[grade];
      delete data[grade];
     }
    }

    const result = await api('/api/event', {
     kind, date: fields.get('date'), token: submissionToken, data
    });
    // A successful save stays successful even if refreshing the screen fails.
    saved = true;
    button.textContent = 'Saved';
    token = crypto.randomUUID().replaceAll('-', '');
    const confirmation = `${result.result === 'already_recorded' ? 'Already saved; no duplicate' : 'Saved'} · ${result.reference}`;
    message(confirmation);

    try {
     await refresh();
     await render();
    } catch (err) {
     message(`${confirmation}. The screen could not refresh. Reload the page to see the saved record. ${err.message}`, true);
    }
   } catch (err) {
    // A lost response does not prove that the server failed to save.
    message('Save not confirmed: ' + err.message + '. Retry this form with the same values to avoid a duplicate.', true);
   } finally {
    saving = false;
    button.disabled = saved;
   }
  };
 }
 if($('#plan-form'))$('#plan-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const data=await api('/api/plan',{delay:Number($('#delay').value),capacity:Number($('#capacity').value)});$('#plan-result').innerHTML='<h2>Scenario comparison</h2>'+table(['Scenario','Order complete','Finished stock','Order remaining','Lowest projected cash','Reserve top-up'],data.scenarios.map(s=>[s.name,s.order_completed_on||'Not completed within horizon',s.daily.at(-1).finished_bricks,s.daily.at(-1).order_remaining_bricks,'₹'+s.lowest_projected_cash_inr,'₹'+s.topup_needed_to_preserve_reserve_inr]))+'<p class="muted">Projection only. No ledger entries were created.</p>';}catch(err){message(err.message,true);}finally{button.disabled=false;}};
 if($('#backup'))$('#backup').onclick=async e=>{e.target.disabled=true;try{const r=await api('/api/backup',{});message(r.result+' '+r.filename);}catch(err){message(err.message,true);}finally{e.target.disabled=false;}};
 if($('#export'))$('#export').onclick=async()=>{try{const data=await api('/api/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='brickflow-fictional-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(err){message(err.message,true);}};
}
window.addEventListener('hashchange',()=>{if(location.hash==='#content'){$('#content').focus();return;}render().catch(e=>message(e.message,true));});
refresh().then(render).catch(e=>message('Could not open the workspace: '+e.message,true));
