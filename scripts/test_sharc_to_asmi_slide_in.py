#!/usr/bin/env python3
"""Test the arm/rail transfer from Opentrons to SHARC, then ASMI slide-out.

Run from the controller box:

    python scripts/test_sharc_to_asmi_slide_in.py --mock   # logging-only worker mode
    python scripts/test_sharc_to_asmi_slide_in.py          # real arm/rail, prompts first

This calls only the arm worker's /health and /run endpoints with:
    opentrons -> uv_station      # place the plate at SHARC/UV
    uv_station -> asmi_pre_push  # move to ASMI slide-out position before slide-in

After it reaches ASMI_SLIDE_OUT_POSITION, the script prompts:
    y -> finish the ASMI insert/release sequence
    n -> return the plate to the SHARC/UV spot

The yes branch inserts the plate, opens the gripper, seats the plate, and
retracts from ASMI. The no branch keeps the gripper closed and places the plate
back at SHARC/UV.
It does not call Opentrons, SHARC station worker, ASMI station worker, or CubOS.
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
ASMI = "asmi"
PRE_PUSH = "asmi_pre_push"
RETURN_TO_SHARC = SHARC


def _run_transfer(
    client: ArmRailClient,
    *,
    src: str,
    dst: str,
    run_id: str,
    mock_mode: bool,
    skip_safe_prelude: bool = False,
) -> None:
    print(f"\ntransfer: {src} -> {dst}  ({run_id})")
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
    print(f"ok: {response}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test Opentrons -> SHARC -> ASMI slide-out arm/rail transfer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "controller.yaml"))
    parser.add_argument("--url", default=None, help="arm worker base URL, overrides --config")
    parser.add_argument("--timeout", type=float, default=None, help="transfer HTTP timeout seconds")
    parser.add_argument("--mock", action="store_true", help="ask the worker to use logging-only stand-ins")
    parser.add_argument("-y", "--yes", action="store_true", help="do not prompt before a real transfer")
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
        f"{SHARC}->{PRE_PUSH}",
        f"{PRE_PUSH}->{ASMI}",
        f"{PRE_PUSH}->{RETURN_TO_SHARC}",
    }
    available_routes = set(health.get("routes") or [])
    if available_routes:
        missing = sorted(required_routes - available_routes)
    else:
        missing = []
    if missing:
        print(f"ERROR: arm worker is missing required route(s): {', '.join(missing)}", file=sys.stderr)
        print("Restart the arm worker so it loads the updated calibration routes.", file=sys.stderr)
        print(f"Worker reported routes: {sorted(available_routes)}", file=sys.stderr)
        return 4

    print(f"arm worker: {health}")
    print("route: OT -> SHARC -> ASMI slide-out -> y: insert/release at ASMI, n: return to SHARC")
    print(f"mock_mode sent to worker: {args.mock}")

    if not args.mock and not args.yes:
        print("\n!! REAL xArm + Vention rail transfer. The arm and rail WILL move.")
        print("!! Confirm the plate starts at Opentrons, fixtures are clear, and the E-stop is reachable.")
        print("!! The script will pause at ASMI_SLIDE_OUT_POSITION with the gripper still closed.")
        if input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("aborted.")
            return 130

    try:
        stamp = int(time.time())
        _run_transfer(
            client,
            src=OT,
            dst=SHARC,
            run_id=f"arm-test:{stamp}:opentrons-to-sharc",
            mock_mode=args.mock,
        )
        _run_transfer(
            client,
            src=SHARC,
            dst=PRE_PUSH,
            run_id=f"arm-test:{stamp}:sharc-to-asmi-pre-push",
            mock_mode=args.mock,
        )

        print("\nAt ASMI_SLIDE_OUT_POSITION with gripper closed.")
        if input("Press y to insert/release at ASMI; n returns to SHARC [y/N]: ").strip().lower() == "y":
            _run_transfer(
                client,
                src=PRE_PUSH,
                dst=ASMI,
                run_id=f"arm-test:{stamp}:asmi-pre-push-insert",
                mock_mode=args.mock,
                skip_safe_prelude=True,
            )
        else:
            print("Skipping ASMI insert; returning plate to SHARC/UV.")
            _run_transfer(
                client,
                src=PRE_PUSH,
                dst=RETURN_TO_SHARC,
                run_id=f"arm-test:{stamp}:asmi-return-to-sharc",
                mock_mode=args.mock,
                skip_safe_prelude=True,
            )
    except (ArmTransferError, HttpError):
        return 1

    print("\nOpentrons -> SHARC -> ASMI calibration cycle complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
