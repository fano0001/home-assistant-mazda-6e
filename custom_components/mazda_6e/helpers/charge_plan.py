"""Helpers for the charging schedule plans reported in charge.chargePlanList."""

from datetime import datetime, time, timedelta

END_DATA_FORMAT = "%Y%m%d%H%M%S"


def charge_plans(vehicle_data: dict | None) -> list[dict] | None:
    """Return the vehicle's charge plans, or None when the status omits them."""
    try:
        plans = vehicle_data["status"]["charge"]["chargePlanList"]
    except (KeyError, TypeError):
        return None
    if not isinstance(plans, list):
        return None
    return [plan for plan in plans if isinstance(plan, dict)]


def first_charge_plan(vehicle_data: dict | None) -> dict | None:
    """Return the first charge plan, which the official app edits."""
    plans = charge_plans(vehicle_data)
    return plans[0] if plans else None


def parse_plan_time(value) -> time | None:
    """Parse Mazda's HHmm plan time."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%H%M").time()
    except ValueError:
        return None


def format_plan_time(value: time) -> str:
    """Format a time as Mazda's HHmm plan time."""
    return value.strftime("%H%M")


def plan_end_enabled(plan: dict) -> bool:
    """Whether the plan stops charging at its end time."""
    return plan.get("endSwitch") == 1 and parse_plan_time(plan.get("endTime")) is not None


def plan_time_zone(now: datetime) -> str:
    """Format the UTC offset of an aware datetime as Mazda's GMT+HH:MM label."""
    minutes = int(now.utcoffset().total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, minutes = divmod(abs(minutes), 60)
    return f"GMT{sign}{hours:02d}:{minutes:02d}"


def describe_plan(plan: dict) -> dict:
    """Return a plan's fields with readable times for state attributes."""
    start = parse_plan_time(plan.get("startTime"))
    end = parse_plan_time(plan.get("endTime"))
    return {
        # planId exceeds JavaScript's safe integer range, so keep it as text.
        "plan_id": str(plan.get("planId")),
        "enabled": plan.get("isValid") == 1,
        "start_time": start.strftime("%H:%M") if start else None,
        "end_time": end.strftime("%H:%M") if end and plan_end_enabled(plan) else None,
    }


def next_departure(now: datetime, departure: time) -> datetime:
    """Return the next local occurrence of a departure time."""
    candidate = now.replace(hour=departure.hour, minute=departure.minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def upcoming_end_data(now: datetime, departure: time) -> str:
    """Format the next occurrence of a departure time; Mazda rejects plans dated in the past."""
    return next_departure(now, departure).strftime(END_DATA_FORMAT)
