#!/usr/bin/env bash
set -euo pipefail

RC_FILE="${RC_FILE:-${HOME}/.zshrc}"
START_MARK="# >>> goose_runtime_map alias >>>"
END_MARK="# <<< goose_runtime_map alias <<<"

if [[ ! -f "$RC_FILE" ]]; then
  echo "Nothing to uninstall: ${RC_FILE} not found"
  exit 0
fi

TMP_FILE="$(mktemp)"
awk -v start="$START_MARK" -v end="$END_MARK" '
  $0 == start { skip=1; next }
  $0 == end { skip=0; next }
  !skip { print }
' "$RC_FILE" > "$TMP_FILE"
mv "$TMP_FILE" "$RC_FILE"

echo "Removed goose_runtime_map alias block from ${RC_FILE}"
