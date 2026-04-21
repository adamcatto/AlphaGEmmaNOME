#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="$(python3 -c "import yaml; print(yaml.safe_load(open('$ROOT/config/ollama.yaml'))['model'])")"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Error: ollama CLI not found. Install from https://ollama.com/download" >&2
  exit 1
fi

echo "Pulling $MODEL..."
ollama pull "$MODEL"
echo "Done. Start the server with: ollama serve"
