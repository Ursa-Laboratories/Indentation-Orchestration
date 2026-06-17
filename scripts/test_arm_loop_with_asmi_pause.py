#!/usr/bin/env python3
"""Loop-test arm transfers with an operator pause before ASMI insertion.

Run from the controller box:

    python scripts/test_arm_loop_with_asmi_pause.py --mock
    python scripts/test_arm_loop_with_asmi_pause.py
    python scripts/test_arm_loop_with_asmi_pause.py --cycles 1 -y

Each cycle starts with the plate at Opentrons and runs:
    opentrons -> uv_station      # SHARC/UV
    uv_station -> asmi_pre_push  # ASMI slide-out position, before insertion

At ASMI_SLIDE_OUT_POSITION, the script prompts:
    y -> insert/release at ASMI, then pick from ASMI and return to Opentrons
    n -> keep gripping the plate and return directly to Opentrons

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


OT = "opentrons"
SHARC = "uv_station"
ASMI_PRE_PUSH = "asmi_pre_push"
ASMI = "asmi"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _run_transfer(
    client: ArmRailClient,
    *,
    src: str,
    dst: str,
    run_id: str,
    mock_mode: bool,
    skip_safe_prelude: bool = False,
) -> None:
    print(f"  transfer: {src} -> {dst}  ({run_id})")
    try:
        response = client.transfer(
            from_location=src,
            to_location=dst,
            run_id=run_id,
            mock_mode=mock_mode,
            skip_safe_prelude=skip_safe_prelude,
        )
    except ArmTransferError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise
    except HttpError as exc:
        print(f"\nHTTP ERROR: {exc}", file=sys.stderr)
        raise
    print(f"    ok: {response}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Loop-test OT -> SHARC -> ASMI with a pause before ASMI insertion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--cycles", type=_positive_int, default=3, help="number of OT -> SHARC -> ASMI/OT cycles")
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "controller.yaml"))
    parser.add_argument("--url", default=None, help="arm worker base URL, overrides --config")
    parser.add_argument("--timeout", type=float, default=None, help="per-transfer HTTP timeout seconds")
    parser.add_argument("--pause-s", type=float, default=1.0, help="pause between route segments")
    parser.add_argument("--mock", action="store_true", help="ask the worker to use logging-only stand-ins")
    parser.add_argument("-y", "--yes", action="store_true", help="do not prompt before starting real hardware motion")
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

    required_routes = {
        f"{OT}->{SHARC}",
        f"{SHARC}->{ASMI_PRE_PUSH}",
        f"{ASMI_PRE_PUSH}->{ASMI}",
        f"{ASMI}->{OT}",
        f"{ASMI_PRE_PUSH}->{OT}",
    }
    available_routes = set(health.get("routes") or [])
    missing = sorted(required_routes - available_routes) if available_routes else []
    if missing:
        print(f"ERROR: arm worker is missing required route(s): {', '.join(missing)}", file=sys.stderr)
        print("Restart the arm worker so it loads the updated ASMI pause routes.", file=sys.stderr)
        print(f"Worker reported routes: {sorted(available_routes)}", file=sys.stderr)
        return 4

    print(f"arm worker: {health}")
    print("route per cycle: OT -> SHARC -> ASMI slide-out -> prompt")
    print("  y: insert/release at ASMI, then ASMI -> OT")
    print("  n: return directly to OT from ASMI slide-out")
    print(f"cycles: {args.cycles}")
    print(f"mock_mode sent to worker: {args.mock}")

    if not args.mock and not args.yes:
        print("\n!! REAL xArm + Vention rail loop. The arm and rail WILL move.")
        print("!! Confirm the plate starts at Opentrons, fixtures are clear, and the E-stop is reachable.")
        if input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("aborted.")
            return 130

    stamp = int(time.time())
    try:
        for cycle in range(1, args.cycles + 1):
            print(f"\ncycle {cycle}/{args.cycles}")
            _run_transfer(
                client,
                src=OT,
                dst=SHARC,
                run_id=f"arm-loop-pause:{stamp}:cycle-{cycle}:opentrons-to-sharc",
                mock_mode=args.mock,
            )
            if args.pause_s > 0:
                time.sleep(args.pause_s)

            _run_transfer(
                client,
                src=SHARC,
                dst=ASMI_PRE_PUSH,
                run_id=f"arm-loop-pause:{stamp}:cycle-{cycle}:sharc-to-asmi-slide-out",
                mock_mode=args.mock,
            )

            print("\nAt ASMI_SLIDE_OUT_POSITION with gripper closed.")
            decision = input("Press y to insert at ASMI; n returns directly to Opentrons [y/N]: ").strip().lower()
            if decision == "y":
                _run_transfer(
                    client,
                    src=ASMI_PRE_PUSH,
                    dst=ASMI,
                    run_id=f"arm-loop-pause:{stamp}:cycle-{cycle}:asmi-insert",
                    mock_mode=args.mock,
                    skip_safe_prelude=True,
                )
                if args.pause_s > 0:
                    time.sleep(args.pause_s)
                _run_transfer(
                    client,
                    src=ASMI,
                    dst=OT,
                    run_id=f"arm-loop-pause:{stamp}:cycle-{cycle}:asmi-to-opentrons",
                    mock_mode=args.mock,
                )
            else:
                print("Returning directly to Opentrons from ASMI slide-out.")
                _run_transfer(
                    client,
                    src=ASMI_PRE_PUSH,
                    dst=OT,
                    run_id=f"arm-loop-pause:{stamp}:cycle-{cycle}:asmi-slide-out-to-opentrons",
                    mock_mode=args.mock,
                    skip_safe_prelude=True,
                )

            if args.pause_s > 0:
                time.sleep(args.pause_s)
    except (ArmTransferError, HttpError):
        return 1

    print("\narm + rail paused-ASMI loop complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
