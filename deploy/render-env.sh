#!/usr/bin/env bash
# render-env.sh <env.example> — prints the server's .env on stdout (ТЗ Фазы 4 WP9.4, Д-§6.1, Д-§7).
#
# The names and their order come from the template (deploy/env.example); a value is the
# environment variable of the same name (the deploy job maps secrets and variables of the GitHub
# Environment `production` into the environment of this script), else the default written in the
# template. Fails, printing only NAMES, when a required value is empty, a placeholder like <owner>
# is left, or a value holds a newline. Values never go to stderr.
set -euo pipefail

template="${1:?usage: render-env.sh <env.example>}"
# Without these the stack cannot start or would lock itself out of its own databases.
required=(
  QUACK_REGISTRY POSTGRES_PASSWORD NEO4J_PASSWORD META_API_KEY
  APP_DB_PASSWORD READER_DB_PASSWORD MIGRATOR_DB_PASSWORD
)

declare -A seen=()
failed=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"  # the template may have been checked out with CRLF
  [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || continue
  name="${BASH_REMATCH[1]}"
  value="${!name-}"
  [[ -n "$value" ]] || value="${BASH_REMATCH[2]}"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "render-env: $name contains a newline" >&2; failed=1; continue
  fi
  if [[ "$value" =~ \<[^\>]*\> ]]; then
    echo "render-env: $name still holds a placeholder, set it" >&2; failed=1; continue
  fi
  seen[$name]="$value"
  printf '%s=%s\n' "$name" "$value"
done < "$template"

for name in "${required[@]}"; do
  if [[ -z "${seen[$name]-}" ]]; then
    echo "render-env: required $name is empty" >&2; failed=1
  fi
done
exit "$failed"
