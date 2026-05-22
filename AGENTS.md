# AGENTS.md

Context for AI coding agents working in this repo. For human-facing setup and
operating instructions see `README.md`.

## What this repo is

Main controller + station workers for the PEGDA UV-cure / indentation
workcell. Replaces `denos`, built on cleaned-up CubOS YAML interfaces. The
controller box is `bear-den-keeper` (10.210.29.11, win10); the UV-cure and
ASMI stations run on Raspberry Pis; the Opentrons Flex sits on the same lab
network. See `configs/controller.yaml` for the full device map.

## Self-contained — no external repo dependencies

This is a **hard rule**. The repo must run end-to-end with only its own
checkout and its declared pip dependencies. Specifically:

- Do **not** reintroduce a `PYTHONPATH=…/keeper_pc` requirement. The Vention
  rail driver (`arm_worker/vention_railway.py`) was vendored from
  `keeper_pc/device_drivers/vention_railway.py` on 2026-05-22 so polymer_indent
  no longer needs that sibling repo on PYTHONPATH. If you need a new driver
  that lives in keeper_pc (or elsewhere), vendor it here too — don't add a
  sys.path / PYTHONPATH hack.
- Do **not** import from `denos`, `keeper_pc`, `ASMI_new`, `cubos`/`Cubware`,
  or any other panda-monorepo sibling. If you need code from one of those,
  vendor it (with attribution in the file header) and prune unused pieces.
- The one exception is `cubos@staging`, which is pulled in as a real pip
  dependency in `pyproject.toml` for the station workers — that's fine
  because pip handles it on every machine.

If you find yourself adding a `sys.path.insert(...)` for a sibling repo,
stop and copy the file across instead. The Pis pull this repo and only this
repo via `git pull`; anything not in the tree won't be there at runtime.

## Layout

| Path | Role |
|------|------|
| `polymer_indent/` | Controller package (loop, clients, results, CLI) |
| `arm_worker/` | Flask worker for xArm + Vention rail. Runs on the controller box (localhost:5004). Includes vendored `vention_railway.py` |
| `station_worker/` | Flask worker for SHARC / ASMI Pis (port 8000). Runs cubos protocols |
| `configs/` | `controller.yaml` (top-level), plus `gantry/`, `deck/`, `protocol/`, `stations/` YAMLs |
| `main_bioadhesives_workcell.py` | The current end-to-end driver script (edit SETTINGS at the top) |
| `main_asmi_with_curetime.py`, `main.py` | Older driver scripts |
| `results/polymer_indent.db` | SQLite audit trail of every per-leg call |

## Per-well loop (the thing that drives a real run)

Implemented in `polymer_indent/loop.py::run_experiment`. For each well:

1. `OpentronsClient.run_fill` — POSTs a generated one-transfer Flex protocol to the Opentrons HTTP API and polls until done. With `base_url=None` it's a no-op placeholder (used when `SKIP_OPENTRONS_FILL=True`).
2a. SHARC home-only protocol (park gantry before deposit).
2. Arm: `opentrons → uv_station`.
3. SHARC UV cure (well + exposure_time swapped per iteration via `apply_overrides` + `render_protocol`).
4a. ASMI home-only protocol (park gantry before deposit).
4. Arm: `uv_station → asmi`.
5. ASMI indentation (well swapped per iteration).
6. Arm: `asmi → opentrons` (or `FINAL_RETURN_LOCATION` on the last well).

Every leg writes its own row to `results/polymer_indent.db` immediately after
the device returns — including failure rows, written before the exception
propagates. Don't add a step without persisting it.

## Mock modes

Three orthogonal mock switches that the orchestration respects:

- `MOCK_STATIONS` (workcell script) → SHARC + ASMI `/run-protocol` calls go through with `mock_mode=True` (no UV, no indent, no Pi-side gantry motion). Arm + OT still real.
- `SKIP_OPENTRONS_FILL` (workcell script) → swap in `OpentronsClient(None)`; the run_fill call becomes a logged placeholder.
- `arm_worker --mock` (or `mock_mode: true` in a `/run` POST) → arm + rail are `_MockArm` / `_MockRail` stand-ins; the worker doesn't import `xarm.wrapper` or `machinelogic`.

The mock stand-ins live alongside the real code in `arm_worker/app.py` and
`polymer_indent/clients/`. **Don't** put mocks in a separate "tests" layer —
they need to be reachable from the same code path that drives real hardware.

## Hardware control = always go through a worker

The orchestration layer never imports `xarm.wrapper`, `machinelogic`, or any
cubos motion code directly. It only POSTs JSON to:

- `localhost:5004` (arm worker — `arm_worker/app.py`)
- `http://<pi>:8000` (station workers — `station_worker/app.py`)
- `http://10.210.29.218:31950` (Opentrons Flex HTTP API directly)

Reasons: per-device Python version pinning (Vention needs 3.10),
process-wide hardware locks (one transfer at a time enforced inside the
worker), uniform timeout/retry/health/stop semantics. If you find yourself
wanting to import a hardware SDK in `polymer_indent/`, add the call to the
worker instead.

## Coordinate / calibration edits

- Arm + rail named poses: `arm_worker/positions.py`. Whenever you change a pose, leave the prior value in the comment + the date you re-measured it (e.g. `# y bumped 37 -> 39 on 2026-05-20`). The Pis pull these via `git pull`.
- ASMI gantry / deck: `configs/gantry/asmi_gantry.yaml`, `configs/deck/asmi_deck.yaml`. These are read by the **station** Pi's cubos, so a coordinate change here needs the Pi to be on the current commit.
- SHARC gantry / deck: `configs/gantry/sharc_gantry.yaml`, `configs/deck/sharc_deck.yaml`.
- Opentrons deck slots (`OPENTRONS_*_SLOT` in the workcell script) must match the arm worker's opentrons-side pickup point — they're coupled, not independent.

## Common gotchas

- xArm only allows one TCP control connection at a time. If UFactory Studio is connected, the worker can't grab the socket and `XArmAPI.connect()` fails silently into a "not connect" state. The worker then caches that broken object — restarting the worker is the fix.
- `set_position(motion_type=0)` is linear TCP motion. From an awkward joint config it can fail with `code=-9` (trajectory planning failed). The retract-to-safe move in `app.py` uses it, so if the arm is parked weirdly the first move fails. Jog to a friendly pose in Studio (then disconnect Studio) before the next run.
- `home:` in protocol YAMLs is owned by the controller via `_home_station()`, not the protocol. Don't add a leading `home:` step to `configs/protocol/*.yaml` — the controller already runs a home-only protocol on each station before the arm deposits, and a duplicate leading home doubles travel.

## Result store

`results/polymer_indent.db` is SQLite, written by `polymer_indent/results.py`.
Rows are per-call (not per-well), keyed by `run_id`. Use it as the source of
truth for "did this leg actually happen" — log lines can be lossy, the DB
is not.

## Running locally

See `README.md` for the full setup. The minimal recipe to drive one well
through real arm + mocked stations + skipped OT:

```bash
# 1. start the arm worker (Python 3.10 env)
mkdir -p .run
/opt/homebrew/Caskroom/miniforge/base/envs/armworker/bin/python -m arm_worker --port 5004 > .run/arm_worker.log 2>&1 &

# 2. confirm
curl -s http://localhost:5004/health

# 3. drive the loop (reads configs/controller.yaml from cwd)
python main_bioadhesives_workcell.py
```

With `SKIP_OPENTRONS_FILL = True` and `MOCK_STATIONS = True` in the workcell
script (the safest first run after a change). Flip to `False` only when
you're certain the stations and OT are reachable and the arm path is sound.
