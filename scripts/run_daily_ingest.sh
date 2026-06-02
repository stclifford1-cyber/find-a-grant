#!/bin/zsh
set -euo pipefail

cd /Users/simonclifford/projects/find-a-grant

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

"$PYTHON" -m app.ingest_all
