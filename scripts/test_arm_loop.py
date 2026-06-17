#!/usr/bin/env python3
"""Loop-test only the xArm + Vention-rail transfer worker.

Run from the controller box:

    python scripts/test_arm_loop.py --mock           # logging-only worker mode
    python scripts/test_arm_loop.py                  # real arm/rail, prompts first
    python scripts/test_arm_loop.py --cycles 1 -y    # one real cycle, no prompt

Each cycle runs:
    opentrons -> uv_station  (SHARC position)
    uv_station -> asmi
    asmi -> opentrons

This talks only to the arm worker's /health and /run endpoints. It does not call
the Opentrons, SHARC station worker, ASMI station worker, or CubOS.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from polymer_indent.clients import ArmRailClient  # noqa: E402
from polymer_indent.clients._http import HttpError  # noqa: E402
from polymer_indent.clients.arm_rail import ArmTransferError  # noqa: E402
from polymer_indent.config import load_controller_config  # noqa: E402


ROUTE = (
    ("opentrons", "uv_station"),
    ("uv_station", "asmi"),
    ("asmi", "opentrons"),
)

LABELS = {
    "opentrons": "OT",
    "uv_station": "SHARC",
    "asmi": "ASMI",
}


def _label(location: str) -> str:
    return LABELS.get(location, location)


def _route_text() -> str:
    return " -> ".join(_label(src) for src, _dst in ROUTE) + f" -> {_label(ROUTE[-1][1])}"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Loop-test the xArm + Vention rail only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--cycles", type=_positive_int, default=3, help="number of OT -> SHARC -> ASMI -> OT cycles")
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "controller.yaml"))
    parser.add_argument("--url", default=None, help="arm worker base URL, overrides --config")
    parser.add_argument("--timeout", type=float, default=None, help="per-transfer HTTP timeout seconds")
    parser.add_argument("--pause-s", type=float, default=1.0, help="pause between transfers")
    parser.add_argument("--mock", action="store_true", help="ask the worker to use logging-only stand-ins")
    parser.add_argument("-y", "--yes", action="store_true", help="do not prompt before a real arm/rail loop")
    args = parser.parse_args(argv)

    cfg = load_controller_config(args.config)
    arm_cfg = cfg.raw.get("arm", {}) or {}
    base_url = args.url or arm_cfg.get("base_url")
    if not base_url:
        print(f"ERROR: no arm base_url in {args.config}; pass --url", file=sys.stderr)
        return 2
    timeout_s = args.timeout if args.timeout is not None else float(arm_cfg.get("timeout_s", 300.0))
    client = ArmRailClient(base_url, timeout_s=timeout_s)

    try:
        health = client.health()
    except HttpError as exc:
        print(f"ERROR: arm worker unreachable at {base_url}: {exc}", file=sys.stderr)
        return 3

    required_routes = {f"{src}->{dst}" for src, dst in ROUTE}
    available_routes = set(health.get("routes") or [])
    if available_routes:
        missing = sorted(required_routes - available_routes)
        if missing:
            print(f"ERROR: arm worker is missing required route(s): {', '.join(missing)}", file=sys.stderr)
            print(f"Worker reported routes: {sorted(available_routes)}", file=sys.stderr)
            return 4

    total_transfers = args.cycles * len(ROUTE)
    print(f"arm worker: {health}")
    print(f"route: {_route_text()}")
    print(f"cycles: {args.cycles} ({total_transfers} transfers)")
    print(f"mock_mode sent to worker: {args.mock}")

    if not args.mock and not args.yes:
        print("\n!! REAL xArm + Vention rail loop. The arm and rail WILL move.")
        print("!! Confirm the plate starts at OT, fixtures are clear, and the E-stop is reachable.")
        if input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("aborted.")
            return 130

    stamp = int(time.time())
    for cycle in range(1, args.cycles + 1):
        print(f"\ncycle {cycle}/{args.cycles}")
        for step, (src, dst) in enumerate(ROUTE, start=1):
            run_id = f"arm-loop:{stamp}:cycle-{cycle}:step-{step}:{src}->{dst}"
            print(f"  transfer {step}/3: {_label(src)} -> {_label(dst)}  ({run_id})")
            try:
                response = client.transfer(
                    from_location=src,
                    to_location=dst,
                    run_id=run_id,
                    mock_mode=args.mock,
                )
            except ArmTransferError as exc:
                print(f"\nFAILED: {exc}", file=sys.stderr)
                return 1
            except HttpError as exc:
                print(f"\nHTTP ERROR: {exc}", file=sys.stderr)
                return 1
            print(f"    ok: {response}")
            if args.pause_s > 0:
                time.sleep(args.pause_s)

    print("\narm + rail loop complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
