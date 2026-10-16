#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INPUT="$ROOT/local_data/input/RevAnvil_Scout_1000_Scored.csv"
OUTPUT="$ROOT/local_data/output/RevAnvil_Scout_1000_Researched.csv"

if [ ! -f "$INPUT" ]; then
  echo "Missing input file:"
  echo "  $INPUT"
  echo "Drop the scored CSV into local_data/input and run again."
  exit 1
fi

source .venv/bin/activate

echo "Running RevAnvil Scout research on priority leads..."
python -m harvey.revanvil_research "$INPUT" -o "$OUTPUT" --concurrency 6

echo "\nDone. Researched CSV:"
echo "  $OUTPUT"
