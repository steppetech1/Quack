#!/usr/bin/env bash
# env-diff.sh <old.env> <new.env> — names of the keys that differ, never their values
# (ТЗ Фазы 4 WP9.4): the dry run of the deploy job compares the .env on the server with the one
# rendered from GitHub, so a password that differs from the one the database was created with is
# noticed before it is installed.
set -euo pipefail

old="${1:?usage: env-diff.sh <old.env> <new.env>}"
new="${2:?usage: env-diff.sh <old.env> <new.env>}"

declare -A before=() after=()
read_env() { # file array-name
  local -n target="$2"
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] && target[${BASH_REMATCH[1]}]="${BASH_REMATCH[2]}"
  done < "$1"
  return 0
}
[[ -f "$old" ]] && read_env "$old" before
read_env "$new" after

differences=0
for name in $(printf '%s\n' "${!after[@]}" | sort); do
  if [[ -z "${before[$name]+x}" ]]; then
    echo "added:   $name"; differences=1
  elif [[ "${before[$name]}" != "${after[$name]}" ]]; then
    echo "changed: $name"; differences=1
  fi
done
for name in $(printf '%s\n' "${!before[@]}" | sort); do
  if [[ -z "${after[$name]+x}" ]]; then
    echo "removed: $name"; differences=1
  fi
done
((differences)) || echo "no differences"
