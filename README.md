# polymer_indent

Main controller + station workers for the PEGDA UV-cure / indentation workcell.
Replaces `denos`, built on the cleaned-up CubOS YAML interfaces.

```
                bear-den-keeper  (10.210.29.11, win10)
        ┌──────────────────────────────────────────────┐
        │  polymer_indent  (main.py / `polymer-indent`) │
        │   experiment loop · arm transfers · Opentrons │
        │   (placeholder) · SQLite bookkeeping          │
        └───┬──────────────┬───────────────┬────────────┘
       HTTP │  (placeholder)│         HTTP  │  HTTP
            │     ┌─────────▼──┐    ┌───────▼─────────┐   ┌───────────────┐
            │     │ Opentrons  │    │ bear-den-scale  │   │ bear-den-asmi │
            │     │  Flex      │    │ station_worker  │   │ station_worker│
            │     └────────────┘    │  + cubos@stg    │   │  + cubos@stg  │
            │                       │  uv_curing      │   │  asmi         │
       ┌────▼─────┐                 │ user: sartorius-│   │ user: asmi    │
       │ xArm +   │                 │       scale     │   │               │
       │ Vention  │  arm_worker     └─────────────────┘   └───────────────┘
       │ rail     │  on keeper :5004  10.210.29.12:8000   10.210.29.17:8000
       └──────────┘  → TCP to .16/.15
```

| Role | Device | IP | OS | Login user | What runs there |
|------|--------|----|----|------------|-----------------|
| Controller | `bear-den-keeper` | 10.210.29.11 | win10 | Kab Lab | `polymer_indent` (`main.py` / `polymer-indent`) |
| UV-curing station ("sharc") | `bear-den-scale` | 10.210.29.12 | debian | `sartorius-scale` | `station_worker --config configs/stations/sharc.yaml` + cubos@staging |
| ASMI station | `bear-den-asmi` | 10.210.29.17 | debian | `asmi` | `station_worker --config configs/stations/asmi.yaml` + cubos@staging |
| Arm + rail | `bear-den-arm1` (xArm) 10.210.29.16 / `bear-den-vention` 10.210.29.15 | arm worker on the controller, `localhost:5004` | — | — | `python -m arm_worker` (in this repo; talks TCP to .16 / .15) |
| Opentrons | Flex 10.210.29.218 | shim `:5003` | — | — | placeholder client only |

## Launch the workcell

Four processes need to be up before you run an experiment, in this order:

1. SHARC station worker (on `bear-den-scale`, the UV-cure Pi)
2. ASMI station worker (on `bear-den-asmi`)
3. Arm worker (on the controller box itself, `bear-den-keeper`)
4. The driver script (also on the controller box)

The Opentrons Flex needs to be powered on and reachable at
`10.210.29.218:31950`, but it has no worker process to start — the controller
hits its built-in HTTP API directly.

Open four terminals: two SSH'd into the Pis, two on the controller.

### 1. SHARC Pi — UV-curing station worker

```bash
# from the controller, ssh in:
ssh sartorius-scale@10.210.29.12
cd ~/polymer_indent
git pull                                                          # pick up new coords/protocols
.venv/bin/python -m station_worker --config configs/stations/sharc.yaml
```

Should print `station sharc listening on 0.0.0.0:8000 …`. Leave open.
Confirm from the controller:

```bash
curl -s http://10.210.29.12:8000/health
```

### 2. ASMI Pi — indentation station worker

```bash
ssh asmi@10.210.29.17
cd ~/polymer_indent
git pull
.venv/bin/python -m station_worker --config configs/stations/asmi.yaml
```

Confirm:

```bash
curl -s http://10.210.29.17:8000/health
```

### 3. Controller box — arm worker

The arm worker drives the xArm (`10.210.29.16`) and Vention rail
(`10.210.29.15`) via TCP. It runs on the **controller box**, listening on
`localhost:5004`, because the orchestration code and the Python 3.10
SDK env both live here.

Pre-flight:

- xArm controller powered on, e-stop released
- Arm parked roughly near `ARM_SAFE_POSITION = [0, 150, 200, 180, 0, 0]` so
  the worker's first linear move can reach it (a far-off pose causes
  `set_position code=-9`, "trajectory planning failed")

Launch (from this repo on the controller):

```bash
cd /Users/charl/Programming/panda/polymer_indent
mkdir -p .run
/opt/homebrew/Caskroom/miniforge/base/envs/armworker/bin/python \
  -m arm_worker --port 5004 > .run/arm_worker.log 2>&1 &
```

`armworker` is a Python 3.10 conda env with `pip install -e ".[arm]"` — that
pulls in `xarm-python-sdk` + `machine-logic-sdk`. The Vention driver is
vendored at `arm_worker/vention_railway.py`; there is **no** PYTHONPATH
dependency on the keeper_pc repo. Confirm:

```bash
curl -s http://localhost:5004/health
```

For a dry run with no hardware: append `--mock` (logging-only stand-ins, no
SDK imports). Per-request override: include `"mock_mode": true` in the
`/run` body.

If a previous run failed with `xArm is not connect` after Studio was
connected, **restart the worker** — it caches the broken `XArmAPI` object:

```bash
pgrep -f 'python -m arm_worker' | xargs kill   # then relaunch as above
```

### 4. Controller box — driver script

From a fresh terminal on the controller:

```bash
cd /Users/charl/Programming/panda/polymer_indent
python main_bioadhesives_workcell.py
```

Before running, edit the `SETTINGS` block at the top of the script —
specifically:

- `TRANSFERS` — list of `(source_tube_well, target_plate_well, uv_exposure_s)` tuples
- `SKIP_OPENTRONS_FILL` — `True` skips the OT step entirely (placeholder)
- `MOCK_STATIONS` — `True` runs SHARC + ASMI in cubos `mock_mode` (no UV, no indent, no Pi gantry motion); the arm still moves for real

Safe first-run recipe after any coordinate or protocol change:
`SKIP_OPENTRONS_FILL=True`, `MOCK_STATIONS=True` — exercises the arm path
end-to-end without firing UV or pushing the ASMI probe.

Other entrypoints in the same shape:

```bash
python main.py                            # single-well legacy driver
python main_asmi_with_curetime.py         # ASMI + per-well cure-time sweep
polymer-indent run examples/pegda_screen.yaml --mock    # yaml-driven experiments via the CLI
```

### One-shot bring-up via the CLI

If the controller has SSH keys installed for both Pis (`ssh-copy-id` first;
no passwords anywhere), `polymer-indent workers` can start/stop all three
without you SSH'ing in by hand:

```bash
polymer-indent workers status                 # /health for sharc, asmi, arm
polymer-indent workers up                     # start all (idempotent)
polymer-indent workers up sharc asmi          # just the stations
polymer-indent workers restart arm
polymer-indent workers logs asmi --lines 80
polymer-indent workers down                   # stop all
```

The arm worker is launched as a detached local process tracked by a pidfile
under `<repo>/.run/`. Stations are started over SSH as a detached
`setsid python -m station_worker --config …` writing to `worker.log` in the
repo dir on the Pi. All paths/users live in `configs/controller.yaml`.

### Pre-flight checks

```bash
polymer-indent health                                           # ping every device
polymer-indent validate examples/pegda_screen.yaml              # offline setup_protocol on each Pi
polymer-indent run examples/pegda_screen.yaml --mock            # full loop, no hardware
```

## The clean split

- **Main controller** (`polymer_indent/`, this machine): runs the per-well
  experiment loop, calls Opentrons (placeholder), drives the arm, and does
  result bookkeeping. **No cubos dependency** — it just reads frozen YAMLs,
  swaps a well id into a base protocol, and POSTs `{gantry, deck, protocol}` to
  the station Pi.
- **SHARC Pi** (`station_worker/` + `cubos@staging`): fixed CubOS gantry/deck
  for the UV station; receives one protocol YAML per well, runs it, returns
  results.
- **ASMI Pi** (`station_worker/` + `cubos@staging`): fixed CubOS gantry/deck for
  the ASMI station; same.
- **Protocol YAMLs** are the frozen cubos base protocols with the well id
  rewritten in memory by the main loop (a one-line text edit — see
  `polymer_indent/protocol_render.py`). The gantry and deck YAMLs are sent
  byte-for-byte every iteration.

## The loop (per well)

```
opentrons.run_fill(well, volume_ul, formulation)            # PLACEHOLDER
arm.transfer(opentrons -> uv_station)
sharc.run_protocol(render_protocol(sharc_base, well))       # cubos on the Pi
arm.transfer(uv_station -> asmi)
asmi.run_protocol(render_protocol(asmi_base, well))         # cubos on the Pi
results.store(experiment_id, well, sharc, asmi, <both protocol YAMLs>)
arm.transfer(asmi -> storage_end if last well else opentrons)
```

## Layout

```
configs/
  controller.yaml                  device URLs, per-station file bundles, db path
  gantry/sharc_gantry.yaml         verbatim copy of cubos@staging configs/gantry/cub_sharc.yaml (with `offline:` stripped from uv_curing)
  gantry/asmi_gantry.yaml          verbatim copy of ASMI_new   configs/gantry/new_asmi_gantry_calibration.yaml
  deck/sharc_deck.yaml             verbatim copy of cubos@staging configs/deck/sharc_uv_deck.yaml
  deck/asmi_deck.yaml              verbatim copy of ASMI_new   configs/deck/asmi_deck.yaml
  protocol/sharc_uv_one_well.yaml  one-well UV `measure` (cubos format; cubos ships only a 96-well scan)
  protocol/asmi_indentation_test.yaml  verbatim copy of ASMI_new (one-well `measure`)
  protocol/asmi_indentation.yaml   verbatim copy of ASMI_new (full-plate scan; reference only)
  protocol/sharc_uv_curing_scan.yaml   verbatim copy of cubos@staging (full-plate scan; reference only)
  stations/{sharc,asmi}.yaml       station-worker server config (port, run dir, allow-list)
polymer_indent/                    the controller package (no cubos dep)
  cli.py  experiment.py  protocol_render.py  results.py  loop.py  config.py
  workers.py                                  start/stop/inspect device workers (SSH for stations, local for arm)
  clients/{cubos_station,arm_rail,opentrons}.py
station_worker/                    the Flask worker run on each station Pi (imports cubos)
  app.py  worker.py  config.py  runs.py  allow.py  jsonify.py  __main__.py
arm_worker/                        xArm + Vention-rail transfer worker (runs on the controller box)
  app.py  positions.py  __main__.py            (extracted from denos; --mock = no hardware)
deploy/                            systemd units + install_station.sh
examples/pegda_screen.yaml         sample experiment
scripts/test_asmi.py  test_uv.py  test_arm.py   per-device test runners
main.py                            controller entrypoint
tests/                             pytest suite
```

## Test one device at a time

Standalone runners you launch **from the controller box** — they drive the
machine over HTTP (the cpu never runs cubos / the arm SDK; the protocol runs on
the Pi / the arm worker). Use `--mock` first to confirm the YAMLs + the Flask API
without moving anything:

```bash
# ASMI station — run one full indentation protocol on one well
python scripts/test_asmi.py --well E5 --mock              # Pi loads gantry+deck+protocol, runs cubos in mock (no hardware)
python scripts/test_asmi.py --well E5                     # real run on the ASMI Pi (prompts first)
python scripts/test_asmi.py --well B3 --force-limit 5 --indentation-limit-height -3
python scripts/test_asmi.py --validate-only --well E5     # just the Pi's offline cubos setup_protocol

# SHARC / UV-curing station
python scripts/test_uv.py --well A1 --mock
python scripts/test_uv.py --well A1                        # real (prompts first)
python scripts/test_uv.py --well C7 --intensity 20 --exposure-time 300

# Arm + Vention rail — run one plate transfer
python scripts/test_arm.py --from opentrons --to uv_station --mock   # arm worker uses logging-only stand-ins
python scripts/test_arm.py --from opentrons --to uv_station          # real (prompts first)
python scripts/test_arm.py --from asmi --to storage_end
python scripts/test_arm.py --health
```

Each reads the device's `base_url` (and, for stations, the gantry/deck/base-protocol
paths) from `configs/controller.yaml`; override with `--url` (and `--gantry`,
`--deck`, `--protocol` for stations). `--help` on any of them lists every knob.
(Pointing a station's `--protocol` at a scan file runs the whole plate, but then
the station's `allow`-list must include the `scan` command.)

## Install

Controller (this machine):

```bash
pip install -e .          # pyyaml + requests
```

Each station Pi (`pip` already has cubos via the repo checkouts; the extra makes
a clean-machine install work too):

```bash
git clone <this repo> ~/polymer_indent && cd ~/polymer_indent
./deploy/install_station.sh sharc      # or:  asmi
# then follow the printed systemd steps
```

Arm worker (runs on the controller box, `bear-den-keeper`). `--mock` needs only
flask; real mode needs Python 3.10 (for `machine-logic-sdk`) and the `arm`
extra. The Vention driver (`arm_worker/vention_railway.py`) is vendored
here — no `PYTHONPATH` dance, no external repo dependency:

```bash
pip install -e ".[arm]"                # flask + xarm-python-sdk + machine-logic-sdk
```

## Run experiments via the CLI

For YAML-driven experiments (the recommended path for anything bigger than a
quick `main_*.py` script):

```bash
polymer-indent validate examples/pegda_screen.yaml              # offline pre-flight on each Pi
polymer-indent run      examples/pegda_screen.yaml --mock       # full loop, no hardware
polymer-indent run      examples/pegda_screen.yaml              # real run
polymer-indent run      examples/pegda_screen.yaml --resume     # skip wells already done
polymer-indent run      examples/pegda_screen.yaml --only-well A1,B2
polymer-indent run      examples/pegda_screen.yaml --continue-on-error
```

(`python main.py …` is equivalent to the `polymer-indent` console script.)

## Station HTTP API (`station_worker`)

| Method & path           | Body                                                              | Returns |
|-------------------------|-------------------------------------------------------------------|---------|
| `GET /health`           | —                                                                 | `{status, station_id, cubos_version, busy, current_run_id, allow}` |
| `POST /validate-protocol` | `{gantry_config, deck_config, protocol_yaml}`                   | `{valid: bool, steps?, error?}` (offline `setup_protocol`, no hardware) |
| `POST /run-protocol`    | `{run_id, gantry_config, deck_config, protocol_yaml, mock_mode?, metadata?}` | `{success, run_id, station_id, results, cubos_version, protocol_sha256, artifacts}` — or `{success:false, error, traceback}` (500); `409` if a run is in progress |
| `POST /stop`            | —                                                                 | best-effort only — cubos has no mid-`protocol.run()` abort; use a hardware kill switch |
| `GET /runs/<run_id>`    | —                                                                 | `{run_id, run_dir, protocol_yaml, result, error?}` (404 if unknown) |

On `/run-protocol` the worker: takes the process-wide station lock (one CubOS
protocol at a time per Pi), checks the protocol against the station `allow`-list
(instrument & command names), writes `gantry.yaml` / `deck.yaml` / `protocol.yaml`
+ `meta.json` into `run_dir/<sanitized run_id>/`, then — for a **real run** —
mirrors cubos' `setup/run_protocol.py`: `Gantry(config=…)` → `setup_protocol(…, gantry=gantry)`
→ `gantry.connect()` → `gantry.prepare_for_protocol_run()` → `board.connect_instruments()`
→ health check → `protocol.run(context)` → `finally` disconnect instruments + gantry.
A **mock run** (`mock_mode=true`) uses `setup_protocol(gantry=None, mock_mode=True)`
and touches no hardware. The result JSON is written next to the inputs.

## Arm-transfer HTTP API (`arm_worker`)

| Method & path | Body | Returns |
|---|---|---|
| `GET /health` | — | `{status, device:"xarm", mock_mode_default, busy, current, routes}` |
| `POST /run` | `{"from": <loc>, "to": <loc>, "run_id"?, "mock_mode"?}` | `{success, from, to, run_id, mock}` — or `{success:false, error}` (400 bad/unknown route, 409 busy, 500 transfer error) |
| `POST /stop` | — | best-effort: sets a stop flag and (real mode) `set_state(4)` / stops the gripper & rail |

Locations: `opentrons`, `uv_station`, `asmi`, `storage_end`. Routes: `opentrons→uv_station`,
`uv_station→asmi`, `asmi→uv_station`, `asmi→opentrons`, `asmi→storage_end`, `opentrons→storage_end`.
One transfer at a time (process lock). The pick/place sequences + named poses are
in `arm_worker/positions.py` (lifted verbatim from denos). `mock_mode` runs the
same sequence against logging-only stand-ins — no xArm / rail / SDK imports.

## Hardware safety

`station_worker` drives real GRBL gantries and instruments via cubos. Before any
non-mock run on a Pi:

1. Make sure the Pi's cubos is `@staging` and the deck calibration anchors are
   correct (the copied `configs/deck/asmi_deck.yaml` still carries upstream's
   "TODO re-measure" markers). The local `configs/gantry/sharc_gantry.yaml`
   already has `offline:` stripped from `uv_curing`, so a real run will fire
   the OmniCure — keep `--mock` for dry runs.
2. `python cubos/setup/validate_setup.py <gantry> <deck> <a-generated-protocol>`.
3. `python cubos/setup/hello_world.py --gantry <gantry>` jog test.
4. `polymer-indent validate <experiment.yaml>` (offline `setup_protocol` on each Pi).
5. `polymer-indent run <experiment.yaml> --mock` end-to-end dry run.

## Bookkeeping

SQLite at `results/polymer_indent.db` (`results.db_path` in `controller.yaml`):
`experiments`, `wells`, `runs` (raw protocol YAML + result JSON kept as TEXT for
replay/audit). Each Pi also keeps its own per-run directories under `run_dir`.

## Status / TODO

- **Opentrons is a placeholder** (`polymer_indent/clients/opentrons.py`): logs
  the requested fill and returns success; the real Flex REST flow is a commented
  stub.
- Per-well *protocol* overrides aren't wired yet — only the well id is swapped
  into the base protocol. Per-well params (intensity, exposure, force limit, …)
  are recorded with the results; extend `render_protocol` / the base files to
  vary them per well.
- The arm-transfer worker (`arm_worker/`, extracted from denos) runs on the
  controller box; `configs/controller.yaml` has `arm.base_url: http://localhost:5004`
  to match. Its poses (`arm_worker/positions.py`) and the Opentrons-D1 plate
  variant should be re-checked against the current rig before any new transfer
  geometry — the ones in-tree have been validated end-to-end.
- IPs are set: `bear-den-scale` 10.210.29.12 ("sharc"/UV-curing), `bear-den-asmi`
  10.210.29.17, xArm `bear-den-arm1` 10.210.29.16 (+ Vention rail 10.210.29.15),
  Opentrons Flex 10.210.29.218.
- `/stop` is best-effort only; a real emergency stop must be hardware.

## Tests

```bash
pip install -e ".[dev]"      # adds pytest; install flask too for the worker tests
pytest -q
```

Tests that need cubos installed (`protocol_engine`) skip cleanly if it's absent;
the SHARC mock-run test also skips on a cubos build that lacks the SHARC holder
labware definition.
