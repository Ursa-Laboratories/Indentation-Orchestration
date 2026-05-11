"""polymer-indent command-line interface.

    polymer-indent run      <experiment.yaml> [--config controller.yaml] [--mock]
                            [--resume] [--only-well A1[,B2]] [--db PATH] [--continue-on-error]
    polymer-indent validate <experiment.yaml> [--config controller.yaml]
    polymer-indent health   [--config controller.yaml]

``validate`` and ``health`` never touch hardware. ``validate`` builds each
well's SHARC/ASMI protocol and asks each Pi to run cubos' offline
``setup_protocol`` on it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_controller_config
from .experiment import load_experiment
from .loop import run_experiment
from .protocol_render import render_protocol

_DEFAULT_CONFIG = "configs/controller.yaml"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _add_config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=_DEFAULT_CONFIG, help=f"controller config YAML (default: {_DEFAULT_CONFIG})")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polymer-indent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run an experiment end to end")
    p_run.add_argument("experiment", help="experiment YAML")
    _add_config_arg(p_run)
    p_run.add_argument("--mock", action="store_true", help="dry run: stations skip all hardware")
    p_run.add_argument("--resume", action="store_true", help="skip wells already marked done")
    p_run.add_argument("--only-well", default=None, help="comma-separated wells to run (in declared order)")
    p_run.add_argument("--db", default=None, help="override results DB path")
    p_run.add_argument("--continue-on-error", action="store_true", help="keep going after a well fails")

    p_val = sub.add_parser("validate", help="offline-validate every well's protocols on the Pis")
    p_val.add_argument("experiment", help="experiment YAML")
    _add_config_arg(p_val)

    p_health = sub.add_parser("health", help="ping every device")
    _add_config_arg(p_health)

    return parser


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_controller_config(args.config)
    experiment = load_experiment(args.experiment)
    only_wells = [w for w in args.only_well.split(",")] if args.only_well else None

    sharc = cfg.station_bundle("sharc", mock_mode=args.mock)
    asmi = cfg.station_bundle("asmi", mock_mode=args.mock)
    arm = cfg.arm_client()
    opentrons = cfg.opentrons_client()

    with cfg.result_store(args.db) as results:
        failed = run_experiment(
            experiment,
            opentrons=opentrons,
            arm=arm,
            sharc=sharc,
            asmi=asmi,
            results=results,
            mock_mode=args.mock,
            resume=args.resume,
            only_wells=only_wells,
            continue_on_error=args.continue_on_error,
        )
    return 1 if failed else 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_controller_config(args.config)
    experiment = load_experiment(args.experiment)
    sharc = cfg.station_bundle("sharc")
    asmi = cfg.station_bundle("asmi")

    ok = True
    for well in experiment.wells:
        for name, bundle in (("sharc", sharc), ("asmi", asmi)):
            proto = render_protocol(bundle.base_protocol_yaml, well)
            try:
                resp = bundle.client.validate_protocol(proto)
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"  {well:>4} {name:<6} ERROR  {exc}")
                continue
            valid = bool(resp.get("valid"))
            ok = ok and valid
            detail = f"steps={resp.get('steps')}" if valid else resp.get("error", "")
            print(f"  {well:>4} {name:<6} {'OK ' if valid else 'FAIL'}   {detail}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_health(args: argparse.Namespace) -> int:
    cfg = load_controller_config(args.config)
    targets = [
        ("sharc", lambda: cfg.station_bundle("sharc").client.health()),
        ("asmi", lambda: cfg.station_bundle("asmi").client.health()),
        ("arm", lambda: cfg.arm_client().health()),
        ("opentrons", lambda: cfg.opentrons_client().health()),
    ]
    all_ok = True
    for name, call in targets:
        try:
            info = call()
            print(f"  {name:<10} OK    {info}")
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            print(f"  {name:<10} DOWN  {exc}")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    # Resolve a relative --config against CWD then the repo root (one level up
    # from this package) so `polymer-indent` works from anywhere in the repo.
    if not Path(args.config).exists():
        repo_root = Path(__file__).resolve().parent.parent
        candidate = repo_root / args.config
        if candidate.exists():
            args.config = str(candidate)
    return {"run": cmd_run, "validate": cmd_validate, "health": cmd_health}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
