#!/usr/bin/env bash
# Запуск бота из корня проекта (используется и вручную, и launchd).
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m grok_bridge
