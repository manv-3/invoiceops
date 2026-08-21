#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

.venv/bin/pytest -q
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m app.services.notion_schema_diagnostic
