"""Plan service helper tests."""

from datetime import datetime, time, timedelta, timezone
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "mazda_6e" / "helpers" / "charge_plan.py"
SPEC = importlib.util.spec_from_file_location("charge_plan_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_next_departure_rolls_over_to_tomorrow():
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone(timedelta(hours=1)))

    assert MODULE.next_departure(now, time(13, 30)) == now.replace(hour=13, minute=30)
    assert MODULE.next_departure(now, time(7, 30)) == datetime(2026, 10, 6, 7, 30, tzinfo=now.tzinfo)
    assert MODULE.next_departure(now, time(12, 0)) == datetime(2026, 10, 6, 12, 0, tzinfo=now.tzinfo)
    assert MODULE.upcoming_end_data(now, time(7, 30)) == "20261006073000"
