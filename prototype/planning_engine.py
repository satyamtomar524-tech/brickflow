"""Small, fictional BrickFlow planning experiment; this is not an ERP or forecast.

Run the bundled demo: python planning_engine.py
Or supply files: python planning_engine.py input.json --output output.json

Every event is dated. A scenario changes drying readiness only; it does not infer
weather effects. One saleable grade is modelled. Whole-brick yield is rounded down
per completed firing batch; the remainder is explicitly recorded as loss. Daily
entries are end-of-day balances, with cash events ordered as supplied within a day.
No sale, dispatch, or expense automatically creates a cash transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def decimal_string(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a decimal string, not a float")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal string") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def paise(value, name):
    amount = decimal_string(value, name)
    if amount < 0 or amount * 100 != (amount * 100).to_integral_value():
        raise ValueError(f"{name} must be nonnegative with at most two decimal places")
    return int(amount * 100)


def rupees(value):
    return format(Decimal(value) / 100, ".2f")


def day(value, name):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if result.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return result


def required(obj, keys, name):
    if not isinstance(obj, dict):
        raise ValueError(f"{name} must be an object")
    missing = set(keys) - obj.keys()
    if missing:
        raise ValueError(f"{name} missing: {', '.join(sorted(missing))}")


def validate(data):
    required(data, ["fictional_demo", "start_date", "end_date", "opening",
                    "reserve_inr", "daily_firing_capacity_bricks",
                    "plant_capacity_limit_bricks", "firing_days", "cooling_days",
                    "saleable_yield", "green_lots", "order", "cash_events",
                    "scenarios"], "input")
    if data["fictional_demo"] is not True:
        raise ValueError("This prototype accepts explicitly fictional demonstrations only")
    start, end = day(data["start_date"], "start_date"), day(data["end_date"], "end_date")
    if end < start:
        raise ValueError("end_date precedes start_date")
    opening = data["opening"]
    required(opening, ["cash_inr", "ready_green_bricks", "finished_bricks",
                       "other_work_in_progress_bricks"], "opening")
    paise(opening["cash_inr"], "opening cash")
    paise(data["reserve_inr"], "reserve")
    for field in ("ready_green_bricks", "finished_bricks", "other_work_in_progress_bricks"):
        integer(opening[field], field)
    if opening["other_work_in_progress_bricks"] != 0:
        raise ValueError("Opening drying/firing/cooling WIP is not supported; explicitly set zero")
    capacity = integer(data["daily_firing_capacity_bricks"], "daily capacity", 1)
    limit = integer(data["plant_capacity_limit_bricks"], "plant limit", 1)
    if capacity > limit:
        raise ValueError("Planned daily firing capacity exceeds the plant limit")
    integer(data["firing_days"], "firing_days", 1)
    integer(data["cooling_days"], "cooling_days", 1)
    yield_rate = decimal_string(data["saleable_yield"], "saleable_yield")
    if not 0 < yield_rate <= 1:
        raise ValueError("saleable_yield must be greater than zero and at most one")

    ids = set()

    def event_id(obj):
        value = obj["event_id"]
        if not isinstance(value, str) or not value.strip() or value in ids:
            raise ValueError("Event IDs must be unique nonempty strings")
        ids.add(value)

    def within(value, name):
        result = day(value, name)
        if not start <= result <= end:
            raise ValueError(f"{name} must be inside the input horizon")
        return result

    for field in ("green_lots", "cash_events", "scenarios"):
        if not isinstance(data[field], list):
            raise ValueError(f"{field} must be a list (use [] for no entries)")
    for lot in data["green_lots"]:
        required(lot, ["event_id", "bricks", "moulded_on", "ready_on"], "green lot")
        event_id(lot)
        integer(lot["bricks"], "lot bricks", 1)
        if within(lot["ready_on"], "ready_on") < within(lot["moulded_on"], "moulded_on"):
            raise ValueError("A lot cannot dry before it is moulded")
    order = data["order"]
    required(order, ["event_id", "bricks", "dispatch_from", "daily_dispatch_limit_bricks"], "order")
    event_id(order)
    integer(order["bricks"], "order bricks", 1)
    integer(order["daily_dispatch_limit_bricks"], "dispatch capacity", 1)
    within(order["dispatch_from"], "dispatch_from")
    for entry in data["cash_events"]:
        required(entry, ["event_id", "date", "direction", "amount_inr", "description", "linked_event_id"], "cash event")
        event_id(entry)
        within(entry["date"], "cash date")
        if entry["direction"] not in ("receipt", "payment"):
            raise ValueError("cash direction must be receipt or payment")
        if paise(entry["amount_inr"], "cash amount") == 0:
            raise ValueError("cash amount must be positive")
        if not isinstance(entry["description"], str) or not entry["description"].strip():
            raise ValueError("cash description must be explicit")
        if entry["linked_event_id"] is not None and entry["linked_event_id"] != order["event_id"]:
            raise ValueError("Only the demo order can be linked; use null for other cash entries")
    if not data["scenarios"]:
        raise ValueError("At least one scenario is required")
    names = set()
    for scenario in data["scenarios"]:
        required(scenario, ["name", "drying_delay_days"], "scenario")
        if not isinstance(scenario["name"], str) or not scenario["name"].strip() or scenario["name"] in names:
            raise ValueError("Scenario names must be unique nonempty strings")
        names.add(scenario["name"])
        integer(scenario["drying_delay_days"], "drying_delay_days")


def simulate(data, scenario):
    """Validated input is simulated without modifying the source records."""
    start, end = date.fromisoformat(data["start_date"]), date.fromisoformat(data["end_date"])
    opening = data["opening"]
    cash = paise(opening["cash_inr"], "opening cash")
    minimum_cash, minimum_date, minimum_event = cash, start.isoformat(), "opening"
    reserve = paise(data["reserve_inr"], "reserve")
    lots = [{"event_id": "opening-ready-green", "remaining": opening["ready_green_bricks"],
             "moulded_on": start, "ready_on": start, "opening": True}]
    lots += [{"event_id": lot["event_id"], "remaining": lot["bricks"],
              "moulded_on": date.fromisoformat(lot["moulded_on"]),
              "ready_on": date.fromisoformat(lot["ready_on"]) + timedelta(days=scenario["drying_delay_days"]),
              "opening": False} for lot in data["green_lots"]]
    lots.sort(key=lambda lot: (lot["ready_on"], lot["moulded_on"], lot["event_id"]))
    batches, daily = [], []
    finished, moulded, dispatched, lost = opening["finished_bricks"], 0, 0, 0
    completion_date = None
    order = data["order"]
    yield_rate = Decimal(data["saleable_yield"])
    current = start
    while current <= end:
        events, loaded, completed, today_dispatch = [], 0, 0, 0
        for lot in lots:
            if not lot["opening"] and lot["moulded_on"] == current:
                moulded += lot["remaining"]
                events.append({"event_id": lot["event_id"], "type": "moulded", "bricks": lot["remaining"]})
            if lot["ready_on"] == current and lot["remaining"]:
                events.append({"event_id": f"ready:{lot['event_id']}", "type": "drying_complete",
                               "source_event_ids": [lot["event_id"]], "bricks": lot["remaining"]})
        for batch in batches:
            if batch["cooling_on"] == current:
                events.append({"event_id": f"cooling:{batch['event_id']}", "type": "cooling_started",
                               "source_event_ids": [batch["event_id"]], "bricks": batch["bricks"]})
            if batch["finished_on"] == current:
                good = int((batch["bricks"] * yield_rate).to_integral_value(rounding=ROUND_FLOOR))
                finished += good
                completed += good
                lost += batch["bricks"] - good
                events.append({"event_id": f"finished:{batch['event_id']}", "type": "finished",
                               "source_event_ids": [batch["event_id"]], "saleable_bricks": good,
                               "loss_bricks": batch["bricks"] - good})
        sources = []
        for lot in lots:
            if lot["ready_on"] <= current:
                take = min(lot["remaining"], data["daily_firing_capacity_bricks"] - loaded)
                if take:
                    lot["remaining"] -= take
                    loaded += take
                    sources.append({"event_id": lot["event_id"], "bricks": take})
        if loaded:
            batch_id = f"firing:{scenario['name']}:{current.isoformat()}"
            cooling_on = current + timedelta(days=data["firing_days"])
            batches.append({"event_id": batch_id, "bricks": loaded, "cooling_on": cooling_on,
                            "finished_on": cooling_on + timedelta(days=data["cooling_days"])})
            events.append({"event_id": batch_id, "type": "firing_started", "bricks": loaded, "sources": sources})
        if current.isoformat() >= order["dispatch_from"]:
            today_dispatch = min(finished, order["bricks"] - dispatched, order["daily_dispatch_limit_bricks"])
            finished -= today_dispatch
            dispatched += today_dispatch
            if today_dispatch:
                events.append({"event_id": f"dispatch:{scenario['name']}:{current.isoformat()}",
                               "type": "dispatch", "source_event_ids": [order["event_id"]], "bricks": today_dispatch})
            if dispatched == order["bricks"] and completion_date is None:
                completion_date = current.isoformat()
        for entry in data["cash_events"]:
            if entry["date"] == current.isoformat():
                amount = paise(entry["amount_inr"], "cash amount")
                cash += amount if entry["direction"] == "receipt" else -amount
                events.append({**entry, "type": "cash", "cash_after_inr": rupees(cash)})
                if cash < minimum_cash:
                    minimum_cash, minimum_date, minimum_event = cash, current.isoformat(), entry["event_id"]
        drying = sum(lot["remaining"] for lot in lots if lot["moulded_on"] <= current < lot["ready_on"])
        ready = sum(lot["remaining"] for lot in lots if lot["ready_on"] <= current)
        firing = sum(batch["bricks"] for batch in batches if current < batch["cooling_on"])
        cooling = sum(batch["bricks"] for batch in batches if batch["cooling_on"] <= current < batch["finished_on"])
        counted = drying + ready + firing + cooling + finished + dispatched + lost
        supplied = opening["ready_green_bricks"] + opening["finished_bricks"] + moulded
        if counted != supplied or min(drying, ready, firing, cooling, finished) < 0:
            raise ValueError("Stock conservation failed or stock overdraft detected")
        if loaded > data["plant_capacity_limit_bricks"]:
            raise ValueError("Daily firing exceeded the plant limit")
        daily.append({"date": current.isoformat(), "drying_bricks": drying,
                      "ready_green_bricks": ready, "firing_bricks": firing, "cooling_bricks": cooling,
                      "finished_bricks": finished, "loaded_today_bricks": loaded,
                      "finished_today_bricks": completed, "dispatched_today_bricks": today_dispatch,
                      "dispatched_total_bricks": dispatched, "loss_total_bricks": lost,
                      "moulded_total_bricks": moulded, "order_remaining_bricks": order["bricks"] - dispatched,
                      "cash_inr": rupees(cash), "events": events})
        current += timedelta(days=1)
    return {"name": scenario["name"], "drying_delay_days": scenario["drying_delay_days"],
            "order_completed_on": completion_date, "closing_cash_inr": rupees(cash),
            "lowest_projected_cash_inr": rupees(minimum_cash), "lowest_cash_date": minimum_date,
            "lowest_cash_event_id": minimum_event, "reserve_inr": rupees(reserve),
            "topup_needed_to_preserve_reserve_inr": rupees(max(0, reserve - minimum_cash)),
            "daily": daily}


def plan(data):
    validate(data)
    return {"fictional_demo": True,
            "model_version": "prototype-0.1",
            "input_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "limitations": ["Invented scenario inputs, not actual business records or a weather forecast.",
                            "One saleable grade; yield rounds down per completed batch; remainder is loss.",
                            "Opening drying/firing/cooling WIP must explicitly be zero; ready and finished opening stock are supported.",
                            "Readiness delay is assumed; firing uses available feedstock up to the entered capacity.",
                            "The fictional calendar does not validate a real plant's permitted season or chamber occupancy.",
                            "Cash follows supplied dated events only, independent of dispatch; amounts are not profit.",
                            "Daily cash timing follows the input event order; the minimum includes interim cash and opening cash.",
                            "A full reserve topup is a scenario calculation, not a financing recommendation.",
                            "Unfinished pipeline stock and order quantities are carried at the horizon; no future completion is invented."],
            "scenarios": [simulate(data, scenario) for scenario in data["scenarios"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    demo_input = Path(__file__).resolve().with_name("demo-input.json")
    parser.add_argument("input", type=Path, nargs="?", default=demo_input,
                        help="Input JSON (default: demo-input.json beside this script)")
    parser.add_argument("--output", type=Path,
                        help="Output JSON (default: demo-output.json for the bundled demo, "
                             "otherwise <input-stem>-output.json beside the input)")
    args = parser.parse_args()
    try:
        input_path = args.input.resolve()
        output_path = (args.output if args.output is not None else
                       input_path.with_name("demo-output.json" if input_path == demo_input else
                                            f"{input_path.stem}-output.json")).resolve()
        if output_path == input_path or (output_path.exists() and output_path.samefile(input_path)):
            raise ValueError("Output must be different from the input file")
        result = plan(json.loads(input_path.read_text(encoding="utf-8-sig")))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print("Fictional BrickFlow planning demo")
    for scenario in result["scenarios"]:
        completion = (f"order completed {scenario['order_completed_on']}"
                      if scenario['order_completed_on'] else "order not completed within the planning horizon")
        print(f"{scenario['name']}: {completion}; "
              f"lowest cash INR {scenario['lowest_projected_cash_inr']}; "
              f"reserve topup INR {scenario['topup_needed_to_preserve_reserve_inr']}")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()
