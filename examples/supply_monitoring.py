"""Monitor ink/toner levels for all discovered printers.

Polls GET /opp/v1/printers/{id}/supplies on an interval and renders
a simple ASCII bar chart.  Logs a WARNING when any supply drops below
15 % and a CRITICAL alert below 10 %.

Usage:
    python examples/supply_monitoring.py [--interval N]

    --interval N   Seconds between polls (default: 30)
"""

import argparse
import json
import sys
import time
import urllib.request

BASE_URL = "http://localhost:631"  # change to your OPP server

WARN_THRESHOLD = 15   # % — print a warning
CRIT_THRESHOLD = 10   # % — print a critical alert

BAR_WIDTH = 30


def _get(url: str) -> object:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _bar(level: int) -> str:
    """Return an ASCII bar like [####......................] 14%."""
    filled = max(0, min(BAR_WIDTH, round(BAR_WIDTH * level / 100)))
    empty = BAR_WIDTH - filled
    return f"[{'#' * filled}{'.' * empty}] {level:3d}%"


def check_supplies(printer: dict) -> None:
    pid = printer["id"]
    name = printer["name"]

    try:
        supplies: list[dict] = _get(f"{BASE_URL}/opp/v1/printers/{pid}/supplies")  # type: ignore[assignment]
    except Exception as exc:
        print(f"  {name}: could not fetch supplies — {exc}")
        return

    if not supplies:
        print(f"  {name}: no supply data reported.")
        return

    print(f"  {name}:")
    for supply in supplies:
        label: str = supply.get("name", "unknown")
        level: int = int(supply.get("level", -1))
        color: str = supply.get("color", "")
        supply_type: str = supply.get("type", "ink")

        display_label = f"{label} ({color})" if color else label

        if level < 0:
            print(f"    {display_label:30s}  [level unknown]")
            continue

        bar = _bar(level)

        if level <= CRIT_THRESHOLD:
            severity = "CRITICAL"
        elif level <= WARN_THRESHOLD:
            severity = "WARNING "
        else:
            severity = "       "

        print(f"    {display_label:30s}  {bar}  {severity}")


def monitor(interval: int) -> None:
    print(f"Supply monitor — polling every {interval}s.  Press Ctrl-C to stop.\n")

    while True:
        try:
            printers: list[dict] = _get(f"{BASE_URL}/opp/v1/printers")  # type: ignore[assignment]
        except Exception as exc:
            print(f"Could not reach OPP server: {exc}")
            time.sleep(interval)
            continue

        if not printers:
            print("No printers found.")
        else:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}]")
            for printer in printers:
                check_supplies(printer)
            print()

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor OpenPrint ink/toner levels.")
    parser.add_argument("--interval", type=int, default=30, metavar="N",
                        help="Seconds between polls (default: 30)")
    args = parser.parse_args()
    monitor(args.interval)


if __name__ == "__main__":
    main()
