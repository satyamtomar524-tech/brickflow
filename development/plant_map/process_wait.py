"""A separate fictional readiness-to-loading lesson; never posts to the ledger."""
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

EXAMPLES_PATH = Path(__file__).with_name('process_examples.json')


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError('Observation times must include a UTC offset.')
    return parsed.astimezone(timezone.utc)


def wait_minutes(ready, loading):
    """Elapsed time from recorded readiness to loading START, not drying or loading duration."""
    for event, expected in ((ready, 'ready_for_loading'), (loading, 'loading_started')):
        if event is None:
            continue
        if event['kind'] != expected or event['evidence_status'] != 'fictional_observation':
            raise ValueError('Use fictional observations of the expected event, not plans or estimates.')
        if not all(event.get(k) for k in ('event_id', 'lot_id', 'source_ref', 'recorded_by')):
            raise ValueError('Event identity and source are required.')
        if timestamp(event['recorded_at']) < timestamp(event['occurred_at']):
            raise ValueError('Entry time cannot precede the reported observation.')
    if ready is None or loading is None:
        return None
    if ready['lot_id'] != loading['lot_id'] or ready['event_id'] == loading['event_id']:
        raise ValueError('Use distinct events for the same lot.')
    seconds = (timestamp(loading['occurred_at']) - timestamp(ready['occurred_at'])).total_seconds()
    if seconds < 0:
        raise ValueError('Loading cannot precede readiness in this metric.')
    return seconds / 60


def example_event(identifier, lot, kind, observed, entered):
    return dict(event_id=identifier, lot_id=lot, kind=kind, occurred_at=observed,
                recorded_at=entered, recorded_by='Demo observer',
                source_ref='separate-process-lesson/' + identifier,
                evidence_status='fictional_observation')


def examples():
    return [(case['ready'], case['loading']) for case in load_examples()['cases']]


def reason_status(ready, loading, reason, review=None):
    """Link a reported explanation without turning elapsed time into proven causation."""
    wait_minutes(ready, loading)
    if reason is None:
        if review is not None:
            raise ValueError('A review must reference an existing explanation.')
        return 'No reason recorded'
    required = ('reason_id', 'reported_by', 'reported_at', 'source_ref', 'explanation')
    if not all(reason.get(k) for k in required) or reason.get('evidence_status') != 'fictional':
        raise ValueError('A fictional reason needs its author, time, source and explanation.')
    if reason.get('ready_event_id') != ready['event_id'] or reason.get('lot_id') != ready['lot_id']:
        raise ValueError('Reason must reference this lot and readiness event.')
    if timestamp(reason['reported_at']) < timestamp(ready['occurred_at']):
        raise ValueError('Reason cannot be reported before readiness.')
    if review is None:
        return 'Reported — awaiting review'
    if (review.get('reason_id') != reason['reason_id'] or
            review.get('decision') not in ('accepted', 'rejected') or
            not all(review.get(k) for k in ('review_id', 'reviewed_by', 'reviewed_at', 'note', 'source_ref'))):
        raise ValueError('A separate review needs a matching reason and supporting details.')
    if timestamp(review['reviewed_at']) < timestamp(reason['reported_at']):
        raise ValueError('Review cannot precede the report.')
    return 'Reviewed — ' + review['decision'] + ' as an explanation (not proof of cause)'


def example_reason():
    return load_examples()['reason']


def load_examples(path=EXAMPLES_PATH):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('evidence_status') != 'fictional':
        raise ValueError('Only version 1 fictional process examples are accepted.')
    cases = data.get('cases')
    if not isinstance(cases, list) or len(cases) < 2:
        raise ValueError('This lesson requires at least two process cases.')
    ids, lots = set(), set()
    for case in cases:
        ready, loading = case['ready'], case['loading']
        if ready is None:
            raise ValueError('A lesson case needs a readiness observation.')
        wait_minutes(ready, loading)
        if ready['lot_id'] in lots:
            raise ValueError('Each case must represent a distinct lesson lot.')
        lots.add(ready['lot_id'])
        for event in (ready, loading):
            if event is not None:
                if event['event_id'] in ids:
                    raise ValueError('Duplicate process event ID.')
                ids.add(event['event_id'])
    reason_status(cases[0]['ready'], cases[0]['loading'], data['reason'])
    return data


def reason_view():
    ready, loading = examples()[0]
    reason = example_reason()
    status = reason_status(ready, loading, reason)
    missing_ready, missing_loading = examples()[1]
    missing_status = reason_status(missing_ready, missing_loading, None)
    return f'''<h3>Why might the lot have waited?</h3>
    <p><b>{escape(status)}</b> · {escape(reason['reason_id'])} → {escape(reason['ready_event_id'])}</p>
    <p>{escape(reason['explanation'])}</p>
    <p>Reported by {escape(reason['reported_by'])} at {escape(reason['reported_at'])}.
    Source: {escape(reason['source_ref'])}. This note is entirely fictional.</p>
    <p>{escape(missing_ready['lot_id'])}: {escape(missing_status)}.</p>
    <p>No review has occurred in this example. A review would be a separate record, preserving the original note.
    We have not attributed the whole interval to this explanation, confirmed a bottleneck or calculated savings.</p>'''


def trial_plan_view():
    ready, loading = examples()[0]
    baseline = wait_minutes(ready, loading)
    baseline_label = 'Unknown' if baseline is None else f'{baseline / 60:g} hours'
    return f'''<section><h3>What could we try?</h3>
    <p><b>Draft idea — not approved or started</b> · PLAN-DEMO-001 → REASON-001</p>
    <p>Proposed change: the foreman and accountant compare the ready-lot list with available kiln loading slots
    before scheduling the next lot. This is a coordination idea, not a change to firing or drying requirements.</p>
    <div class="flow"><span>Review the reported reason</span> → <span>Review a small trial plan</span> →
    <span>Record trial observations</span> → <span>Compare results</span></div>
    <table><caption>Evidence needed before claiming improvement</caption>
    <tr><th>Existing fictional interval</th><td>{baseline_label} · TIME-001 → TIME-002</td></tr>
    <tr><th>Baseline limitation</th><td>One lesson lot; not a representative plant baseline</td></tr>
    <tr><th>Required review</th><td>Owner and foreman review; no decision recorded</td></tr>
    <tr><th>Trial period and lots</th><td>Not selected</td></tr>
    <tr><th>Trial result</th><td>Not observed</td></tr>
    <tr><th>Time saved and financial benefit</th><td>Unknown</td></tr></table>
    <p>Record readiness and loading-start times, lot size, available kiln space, weather, staffing and closures
    for comparable lots before and during any trial. Keep observation times separate from entry times.
    Check extra handling, breakage and quality alongside waiting time.</p>
    <p>A shorter interval alone does not establish what caused it. Do not reduce required drying or cooling,
    or convert time into cash savings without cost evidence. The plan makes no changes to accounts or plant operations.</p></section>'''


def event_anchor(identifier):
    return 'process-event-' + identifier.encode('utf-8').hex()


def event_link(identifier):
    return f'<a href="#{event_anchor(identifier)}">{escape(identifier)}</a>'


def event_evidence(cases):
    rows = []
    for ready, loading in cases:
        for event in (ready, loading):
            if event is None:
                continue
            lag = (timestamp(event['recorded_at']) - timestamp(event['occurred_at'])).total_seconds() / 60
            values = (event['event_id'], event['lot_id'], event['kind'].replace('_', ' '),
                      event['occurred_at'], event['recorded_at'], f'{lag:g} minutes',
                      event['recorded_by'], event['source_ref'])
            rows.append(f'<tr id="{event_anchor(event["event_id"])}">' +
                        ''.join('<td>' + escape(v) + '</td>' for v in values) + '</tr>')
    return '''<h3>Evidence behind the process times</h3>
    <p>All entries below are fictional observations. Entry lag means time between an event and its record entry;
    it is not brick waiting time or a worker-performance score.</p>
    <div class="scroll"><table><caption>Process event records — separate from accounting entries</caption>
    <thead><tr><th>Event</th><th>Lot</th><th>Activity</th><th>Occurred at</th><th>Entered at</th>
    <th>Entry lag</th><th>Recorder</th><th>Source</th></tr></thead><tbody>''' + ''.join(rows) + '</tbody></table></div>'


def render():
    rows = []
    cases = examples()
    for ready, loading in cases:
        minutes = wait_minutes(ready, loading)
        duration = 'Unknown — loading start not recorded' if minutes is None else f'{minutes / 60:g} hours'
        values = (ready['lot_id'], ready['occurred_at'],
                  loading['occurred_at'] if loading else 'Missing', duration)
        links = event_link(ready['event_id']) + (' → ' + event_link(loading['event_id']) if loading else '')
        rows.append('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in values) + '<td>' + links + '</td></tr>')
    return '''<section><h2>Where does work wait?</h2>
    <p>Separate fictional process lesson — these lots are not the accounting lot above.</p>
    <div class="flow"><span>Readiness recorded</span> → <span>Elapsed waiting interval</span> →
    <span>Loading starts</span></div>
    <div class="scroll"><table><caption>Observation timestamps include India UTC offset +05:30</caption>
    <thead><tr><th>Lesson lot</th><th>Ready at</th><th>Loading started at</th><th>Elapsed interval</th><th>Evidence IDs</th></tr></thead>
    <tbody>''' + ''.join(rows) + '''</tbody></table></div>
    <p>Intervals use when the events occurred, not when someone entered them.
    A missing loading-start observation means unknown, not zero. No current waiting age is inferred from a missing event.</p>
    <p>This interval excludes drying before readiness and loading after its start, but includes any closures or breaks between observations.
    It is not automatically avoidable delay, paid idle time or lost profit. Readiness is an observer's statement, not a sensor-certified result.
    Ask about kiln space, access, staffing or planned scheduling before assigning a cause.</p>
    ''' + event_evidence(cases) + reason_view() + trial_plan_view() + '''<p>The next decision is whether evidence supports this draft trial.
    No cause, improvement or worker-performance score has been inferred here.</p></section>'''
