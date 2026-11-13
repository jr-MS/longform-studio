#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  printf '%s\n' '请先按 README.md 完成依赖安装与前端构建。' >&2
  exit 1
fi
export STUDIO_ORIGIN="http://127.0.0.1:${PORT:-8765}"
exec .venv/bin/python -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port "${PORT:-8765}"
