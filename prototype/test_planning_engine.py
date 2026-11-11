"""Behavior checks for the fictional planning engine; run python -m unittest -v."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from planning_engine import plan


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path(__file__).with_name("demo-input.json").read_text(encoding="utf-8"))

    def test_every_day_conserves_stock_and_obeys_capacity(self):
        for scenario in plan(self.data)["scenarios"]:
            for entry in scenario["daily"]:
                stock = sum(entry[field] for field in ["drying_bricks", "ready_green_bricks", "firing_bricks",
                                                      "cooling_bricks", "finished_bricks"])
                self.assertEqual(entry["moulded_total_bricks"], stock + entry["dispatched_total_bricks"] + entry["loss_total_bricks"])
                self.assertLessEqual(entry["loaded_today_bricks"], 4000)
                self.assertGreaterEqual(entry["finished_bricks"], 0)
                self.assertGreaterEqual(entry["order_remaining_bricks"], 0)

    def test_delay_changes_availability_and_leaves_explicit_wip(self):
        baseline, delayed = plan(self.data)["scenarios"]
        self.assertEqual(baseline["order_completed_on"], "2026-02-11")
        self.assertEqual(delayed["order_completed_on"], "2026-02-14")
        self.assertEqual(baseline["daily"][-1]["finished_bricks"], 2400)
        self.assertEqual(delayed["daily"][-1]["cooling_bricks"], 2000)
        self.assertEqual(delayed["daily"][-1]["finished_bricks"], 600)
        self.assertEqual(delayed["daily"][-1]["loss_total_bricks"], 1400)
        self.assertEqual(delayed["daily"][6]["dispatched_today_bricks"], 0)

    def test_cash_is_independent_of_dispatch(self):
        baseline, delayed = plan(self.data)["scenarios"]
        self.assertEqual([day["cash_inr"] for day in baseline["daily"]], [day["cash_inr"] for day in delayed["daily"]])
        self.data["order"]["bricks"] = 100000
        changed = plan(self.data)["scenarios"][0]
        self.assertIsNone(changed["order_completed_on"])
        self.assertEqual(changed["closing_cash_inr"], baseline["closing_cash_inr"])

    def test_cash_minimum_timing_and_reserve_topup(self):
        result = plan(self.data)["scenarios"][0]
        self.assertEqual(result["lowest_projected_cash_inr"], "-5000.00")
        self.assertEqual(result["lowest_cash_date"], "2026-02-14")
        self.assertEqual(result["lowest_cash_event_id"], "demo-wage-payment-02")
        self.assertEqual(result["topup_needed_to_preserve_reserve_inr"], "8000.00")

    def test_exact_paise_and_intraday_minimum(self):
        self.data["opening"]["cash_inr"] = "0.10"
        self.data["reserve_inr"] = "0.00"
        self.data["cash_events"] = [
            {"event_id": "pay", "date": "2026-02-01", "direction": "payment", "amount_inr": "0.30", "description": "pay", "linked_event_id": None},
            {"event_id": "receive", "date": "2026-02-01", "direction": "receipt", "amount_inr": "0.20", "description": "receive", "linked_event_id": None}]
        result = plan(self.data)["scenarios"][0]
        self.assertEqual(result["closing_cash_inr"], "0.00")
        self.assertEqual(result["lowest_projected_cash_inr"], "-0.20")
        self.assertEqual(result["lowest_cash_event_id"], "pay")

    def test_opening_cash_can_be_the_minimum(self):
        self.data["cash_events"] = []
        self.data["opening"]["cash_inr"] = "1.00"
        result = plan(self.data)["scenarios"][0]
        self.assertEqual(result["lowest_cash_event_id"], "opening")
        self.assertEqual(result["topup_needed_to_preserve_reserve_inr"], "2999.00")

    def test_opening_stock_is_preserved_and_dispatch_cannot_overdraw(self):
        self.data["green_lots"] = []
        self.data["opening"]["finished_bricks"] = 17
        self.data["opening"]["ready_green_bricks"] = 9
        result = plan(self.data)["scenarios"][0]["daily"][-1]
        self.assertEqual(result["dispatched_total_bricks"], 25)
        self.assertEqual(result["loss_total_bricks"], 1)
        self.assertEqual(result["finished_bricks"], 0)

    def test_traceability_and_no_input_mutation(self):
        original = copy.deepcopy(self.data)
        result = plan(self.data)
        events = [event for entry in result["scenarios"][0]["daily"] for event in entry["events"]]
        first_firing = next(event for event in events if event["type"] == "firing_started")
        self.assertEqual(first_firing["sources"], [{"event_id": "demo-lot-01", "bricks": 4000}])
        self.assertTrue(any(event["event_id"] == "demo-advance-01" for event in events))
        self.assertEqual(self.data, original)

    def test_missing_unknown_and_negative_values_are_rejected(self):
        del self.data["opening"]["cash_inr"]
        with self.assertRaises(ValueError):
            plan(self.data)
        self.setUp()
        self.data["opening"]["cash_inr"] = None
        with self.assertRaises(ValueError):
            plan(self.data)
        self.setUp()
        self.data["green_lots"][0]["bricks"] = -1
        with self.assertRaises(ValueError):
            plan(self.data)

    def test_bad_yield_capacity_dates_and_money_are_rejected(self):
        for key, value in [("saleable_yield", "1.01"), ("saleable_yield", "NaN"),
                           ("daily_firing_capacity_bricks", 4001), ("end_date", "2026-01-01")]:
            with self.subTest(key=key, value=value):
                data = copy.deepcopy(self.data)
                data[key] = value
                with self.assertRaises(ValueError):
                    plan(data)
        for value in [1.1, "1.001", "-0.01"]:
            self.data["opening"]["cash_inr"] = value
            with self.assertRaises(ValueError):
                plan(self.data)

    def test_events_outside_horizon_duplicate_ids_and_nonzero_opening_wip_rejected(self):
        self.data["cash_events"][0]["date"] = "2026-02-15"
        with self.assertRaises(ValueError):
            plan(self.data)
        self.setUp()
        self.data["green_lots"][1]["event_id"] = "demo-lot-01"
        with self.assertRaises(ValueError):
            plan(self.data)
        self.setUp()
        self.data["opening"]["other_work_in_progress_bricks"] = 1
        with self.assertRaises(ValueError):
            plan(self.data)

    def test_delay_beyond_horizon_remains_drying_not_lost(self):
        self.data["scenarios"] = [{"name": "long-delay", "drying_delay_days": 30}]
        result = plan(self.data)["scenarios"][0]
        closing = result["daily"][-1]
        self.assertIsNone(result["order_completed_on"])
        self.assertEqual(closing["drying_bricks"], 16000)
        self.assertEqual(closing["loss_total_bricks"], 0)
        self.assertEqual(closing["order_remaining_bricks"], 12000)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script_dir = self.root / "prototype"
        self.script_dir.mkdir()
        for name in ("planning_engine.py", "demo-input.json"):
            shutil.copyfile(Path(__file__).with_name(name), self.script_dir / name)
        self.script = self.script_dir / "planning_engine.py"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.script), *map(str, args)],
                              cwd=self.root, capture_output=True, text=True, timeout=15)

    def test_no_arguments_runs_complete_demo_from_another_directory(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads((self.script_dir / "demo-output.json").read_text(encoding="utf-8"))
        source = json.loads((self.script_dir / "demo-input.json").read_text(encoding="utf-8"))
        self.assertEqual(output, plan(source))
        self.assertIn("Results saved to:", result.stdout)

    def test_custom_output_creates_missing_parent_directories(self):
        output = self.root / "new folder" / "results.json"
        result = self.run_cli(self.script_dir / "demo-input.json", "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())

    def test_custom_input_with_bom_gets_separate_default_output(self):
        source = self.root / "custom.json"
        contents = (self.script_dir / "demo-input.json").read_text(encoding="utf-8")
        source.write_text(contents, encoding="utf-8-sig")
        original = source.read_bytes()
        result = self.run_cli(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "custom-output.json").is_file())
        self.assertEqual(source.read_bytes(), original)

    def test_input_cannot_be_overwritten(self):
        source = self.script_dir / "demo-input.json"
        original = source.read_bytes()
        result = self.run_cli(source, "--output", source)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Output must be different", result.stderr)
        self.assertEqual(source.read_bytes(), original)

    def test_file_errors_are_reported_without_tracebacks(self):
        invalid = self.root / "invalid.json"
        invalid.write_text("not JSON", encoding="utf-8")
        for args in [(self.root / "missing.json",), (invalid,),
                     ("--output", self.script_dir)]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("Results saved", result.stdout)

    def test_incomplete_order_has_clear_console_summary(self):
        source = self.script_dir / "demo-input.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        data["order"]["bricks"] = 100000
        source.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("order not completed within the planning horizon", result.stdout)
        self.assertNotIn("completed None", result.stdout)


if __name__ == "__main__":
    unittest.main()
