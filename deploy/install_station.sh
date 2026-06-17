#!/usr/bin/env bash
# Install the polymer_indent station worker on a Raspberry Pi (SHARC or ASMI).
#
#   ./deploy/install_station.sh sharc        # uses configs/stations/sharc.yaml
#   ./deploy/install_station.sh asmi
#
# Assumes you've already cloned this repo onto the Pi and `cd`'d into it.
set -euo pipefail

STATION="${1:-}"
if [[ "$STATION" != "sharc" && "$STATION" != "asmi" ]]; then
  echo "usage: $0 {sharc|asmi}" >&2
  exit 2
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo ">> creating venv at $REPO_DIR/.venv"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip

echo ">> installing polymer_indent + station extras (flask, cubos@main)"
pip install -e ".[station]"

echo ">> sanity: import cubos"
python -c "import protocol_engine, gantry; print('cubos OK')"

UNIT="deploy/polymer-indent-${STATION}-station.service"
echo
echo "Next (manual, needs sudo):"
echo "  sudo cp $UNIT /etc/systemd/system/"
echo "  # edit User= / WorkingDirectory= / ExecStart= in that file to match this path:"
echo "  #   WorkingDirectory=$REPO_DIR"
echo "  #   ExecStart=$REPO_DIR/.venv/bin/python -m station_worker --config configs/stations/${STATION}.yaml"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now polymer-indent-${STATION}-station"
echo
echo "Before any non-mock run, validate on this Pi against cubos directly, e.g.:"
echo "  python -m station_worker --config configs/stations/${STATION}.yaml &   # then from the controller:"
echo "  polymer-indent validate examples/pegda_screen.yaml"
