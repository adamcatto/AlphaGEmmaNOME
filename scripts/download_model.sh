#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$(python3 "$ROOT/scripts/_paths.py" alphagenome_weights)"
TARGET_DIR="$(dirname "$TARGET")"

REPO_ID="$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/config/models.yaml')); print(d['huggingface']['alphagenome']['repo_id'])")"
FILENAME="$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/config/models.yaml')); print(d['huggingface']['alphagenome']['filename'])")"
REVISION="$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/config/models.yaml')); print(d['huggingface']['alphagenome']['revision'])")"

mkdir -p "$TARGET_DIR"

if [[ -f "$TARGET" ]]; then
  echo "AlphaGenome weights already at $TARGET"
  exit 0
fi

if ! command -v hf >/dev/null 2>&1; then
  echo "Installing huggingface_hub CLI..."
  pip install -q "huggingface_hub>=0.23"
fi

echo "Downloading $FILENAME from $REPO_ID@$REVISION..."
hf download "$REPO_ID" "$FILENAME" --revision "$REVISION" --local-dir "$TARGET_DIR"
echo "AlphaGenome weights ready: $TARGET"
