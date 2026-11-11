import copy
import unittest
import process_wait
import json
from pathlib import Path
import tempfile


class ProcessWaitTests(unittest.TestCase):
    def test_occurrence_not_entry_time(self):
        ready, loading = process_wait.examples()[0]
        self.assertEqual(process_wait.wait_minutes(ready, loading), 360)

    def test_missing_is_unknown(self):
        ready, _ = process_wait.examples()[1]
        self.assertIsNone(process_wait.wait_minutes(ready, None))

    def test_equivalent_timezone(self):
        ready, loading = copy.deepcopy(process_wait.examples()[0])
        loading['occurred_at'] = '2026-03-02T08:30:00+00:00'
        self.assertEqual(process_wait.wait_minutes(ready, loading), 360)

    def test_bad_order_identity_and_plans_rejected(self):
        for key, value in [('occurred_at', '2026-03-02T07:00:00+05:30'),
                           ('occurred_at', '2026-03-02T14:00:00'),
                           ('recorded_at', '2026-03-02T07:00:00+05:30'),
                           ('lot_id', 'DIFFERENT'), ('event_id', 'TIME-001'),
                           ('evidence_status', 'planned'), ('source_ref', '')]:
            ready, loading = copy.deepcopy(process_wait.examples()[0])
            loading[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                process_wait.wait_minutes(ready, loading)

    def test_zero_is_valid_when_both_times_are_known(self):
        ready, loading = copy.deepcopy(process_wait.examples()[0])
        loading['occurred_at'] = ready['occurred_at']
        self.assertEqual(process_wait.wait_minutes(ready, loading), 0)


    def test_reason_remains_reported_until_separate_review(self):
        ready, loading = process_wait.examples()[0]
        reason = process_wait.example_reason()
        original = copy.deepcopy(reason)
        self.assertIn('awaiting review', process_wait.reason_status(ready, loading, reason))
        review = dict(review_id='REVIEW-001', reason_id=reason['reason_id'], decision='accepted',
                      reviewed_by='Demo owner', reviewed_at='2026-03-02T17:00:00+05:30',
                      note='Fictional review note', source_ref='fictional-review-001')
        self.assertIn('not proof of cause', process_wait.reason_status(ready, loading, reason, review))
        self.assertEqual(reason, original)
        review['decision'] = 'rejected'
        self.assertIn('rejected', process_wait.reason_status(ready, loading, reason, review))

    def test_reason_cannot_attach_to_wrong_lot_or_time(self):
        ready, loading = process_wait.examples()[0]
        for key, value in [('lot_id', 'OTHER'), ('ready_event_id', 'OTHER'),
                           ('reported_at', '2026-03-02T07:00:00+05:30'), ('source_ref', '')]:
            reason = process_wait.example_reason()
            reason[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                process_wait.reason_status(ready, loading, reason)

    def test_missing_reason_is_not_inferred(self):
        ready, loading = process_wait.examples()[0]
        self.assertEqual(process_wait.reason_status(ready, loading, None), 'No reason recorded')
        with self.assertRaises(ValueError):
            process_wait.reason_status(ready, loading, None, {'reason_id': 'MISSING'})
        self.assertIn('No review has occurred', process_wait.render())


    def test_saved_examples_are_reproducible_and_unchanged(self):
        before = process_wait.EXAMPLES_PATH.read_bytes()
        data = process_wait.load_examples()
        self.assertEqual(process_wait.wait_minutes(data['cases'][0]['ready'], data['cases'][0]['loading']), 360)
        self.assertIsNone(data['cases'][1]['loading'])
        process_wait.render()
        self.assertEqual(before, process_wait.EXAMPLES_PATH.read_bytes())

    def test_import_rejects_duplicates_and_nonfictional_data(self):
        with tempfile.TemporaryDirectory(dir=process_wait.EXAMPLES_PATH.parent) as tmp:
            path = Path(tmp) / 'example.json'
            for alteration in ('duplicate', 'actual', 'wrong_reason'):
                data = process_wait.load_examples()
                if alteration == 'duplicate':
                    data['cases'][1]['ready']['event_id'] = data['cases'][0]['ready']['event_id']
                elif alteration == 'actual':
                    data['evidence_status'] = 'actual'
                else:
                    data['reason']['ready_event_id'] = 'OTHER'
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                    process_wait.load_examples(path)


    def test_entry_lag_does_not_change_waiting_time(self):
        cases = process_wait.examples()
        page = process_wait.event_evidence(cases)
        self.assertIn('60 minutes', page)
        self.assertIn('120 minutes', page)
        self.assertEqual(process_wait.wait_minutes(*cases[0]), 360)
        self.assertIn('not brick waiting time', page)
        for case in cases:
            for event in case:
                if event:
                    self.assertIn(event['source_ref'], page)
                    self.assertIn(f'id="{process_wait.event_anchor(event["event_id"])}"', page)


if __name__ == '__main__':
    unittest.main()
