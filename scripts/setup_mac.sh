#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "\nRevAnvil Scout — Mac setup\n"

if ! command -v python3.13 >/dev/null 2>&1; then
  echo "Python 3.13 is not installed."
  if command -v brew >/dev/null 2>&1; then
    echo "Installing Python 3.13 with Homebrew..."
    brew install python@3.13
  else
    echo "Homebrew is not installed. Install Homebrew first from https://brew.sh, then rerun this script."
    exit 1
  fi
fi

if [ ! -d .venv ]; then
  echo "Creating Python virtual environment..."
  python3.13 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .

mkdir -p local_data/input local_data/output

echo "\nSetup complete."
echo "Put your scored lead CSV here:"
echo "  $ROOT/local_data/input/RevAnvil_Scout_1000_Scored.csv"
echo "\nThen run:"
echo "  ./scripts/run_research_mac.sh"
echo "\nNo sending is enabled by this setup. Research/export only."
