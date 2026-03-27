#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TARGET_SCRIPT="${SCRIPT_DIR}/grm.py"
RC_FILE="${RC_FILE:-${HOME}/.zshrc}"
START_MARK="# >>> goose_runtime_map alias >>>"
END_MARK="# <<< goose_runtime_map alias <<<"

mkdir -p "$(dirname "$RC_FILE")"
touch "$RC_FILE"

TMP_FILE="$(mktemp)"
awk -v start="$START_MARK" -v end="$END_MARK" '
  $0 == start { skip=1; next }
  $0 == end { skip=0; next }
  !skip { print }
' "$RC_FILE" > "$TMP_FILE"
mv "$TMP_FILE" "$RC_FILE"

{
  echo
  echo "$START_MARK"
  echo "alias grm='${PYTHON_BIN} ${TARGET_SCRIPT}'"
  echo "$END_MARK"
} >> "$RC_FILE"

echo "Installed alias 'grm' into ${RC_FILE}"
echo "Run: source ${RC_FILE}"
