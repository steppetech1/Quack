#!/usr/bin/env bash
# deploy.sh <sha> — roll out (or roll back to) one build on this server.
# ТЗ Фазы 4 WP9.4, Д-§6.1 (steps 1-8), Д-§6.3 (rollback).
#
# Run as the `deploy` user in the directory that holds compose.yaml, deploy/ and `.env`
# (`/srv/quack`). The deploy job renders `.env` first. The script keeps two more files there:
#   release.env  QUACK_VERSION and both image names of the running release (read by compose)
#   deploy.log   one line per rollout step result, the journal of Д-§6.1
#
# A <sha> that was deployed before is a rollback: no dump, no migrations (Д-§6.3).
# Caddy and the databases are not restarted by a rollout (Д-§6.1), only started when missing.
set -euo pipefail

SHA="${1:?usage: deploy.sh <sha>}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE=.env
RELEASE_FILE=release.env
JOURNAL=deploy.log
# Starting values, `Д-§6.1` step 8 waits this long for the API and the projector.
READY_TIMEOUT_S="${READY_TIMEOUT_S:-120}"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$JOURNAL"; }
die() { log "FAIL sha=$SHA $*"; exit 1; }

[[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE (rendered by the deploy job)"
# Only the two non-secret values are read here; the rest of `.env` goes to compose as is.
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; }
QUACK_REGISTRY="$(env_value QUACK_REGISTRY)"
[[ -n "$QUACK_REGISTRY" ]] || die "set QUACK_REGISTRY in $ENV_FILE"
API_DOMAIN="$(env_value API_DOMAIN)"; API_DOMAIN="${API_DOMAIN:-api.qvack.online}"

dc() { docker compose --env-file "$ENV_FILE" --env-file "$RELEASE_FILE" "$@"; }

current_sha() { [[ -f "$RELEASE_FILE" ]] && sed -n 's/^QUACK_VERSION=//p' "$RELEASE_FILE" || true; }
backend_image() { echo "$QUACK_REGISTRY/quack-backend:$1"; }
compute_image() { echo "$QUACK_REGISTRY/quack-compute:$1"; }

write_release() {
  local sha="$1"
  cat > "$RELEASE_FILE.tmp" <<EOF
QUACK_VERSION=$sha
QUACK_BACKEND_IMAGE=$(backend_image "$sha")
QUACK_COMPUTE_IMAGE=$(compute_image "$sha")
EOF
  mv "$RELEASE_FILE.tmp" "$RELEASE_FILE"
}

registry_of() { docker run --rm --entrypoint cat "$(backend_image "$1")" /app/event_registry.json; }
migrations_of() { docker run --rm --entrypoint ls "$(backend_image "$1")" /app/migrations/versions | grep -v __pycache__ | sort; }

# Prints "events_changed=<0|1> rules_changed=<0|1>" for old -> new registries (Д-§6.1 step 2).
compare_registries() {
  python3 - "$1" "$2" <<'PY'
import json, sys
old, new = (json.loads(open(p, encoding="utf-8").read()) for p in sys.argv[1:3])
pairs = lambda r: {(e["type"], int(e["schema_version"])) for e in r["events"]}
print(f"events_changed={int(bool(pairs(new) - pairs(old)))} "
      f"rules_changed={int(old.get('rules_version') != new.get('rules_version'))}")
PY
}

wait_for() { # wait_for <description> <command...>
  local what="$1"; shift
  local deadline=$((SECONDS + READY_TIMEOUT_S))
  until "$@" >/dev/null 2>&1; do
    ((SECONDS < deadline)) || return 1
    sleep 3
  done
  log "ok $what"
}

check_release() { # Д-§6.1 step 8
  wait_for "api healthy" bash -c "[[ \"\$(docker compose --env-file '$ENV_FILE' --env-file '$RELEASE_FILE' ps --format '{{.Health}}' api)\" == healthy ]]" || return 1
  wait_for "projector running" bash -c "[[ -n \"\$(docker compose --env-file '$ENV_FILE' --env-file '$RELEASE_FILE' ps --status running --quiet projector)\" ]]" || return 1
  # `ops status` exits non-zero when a dependency or the projector lag is not ok.
  wait_for "ops status" dc exec -T api python -m quack.cli ops status || return 1
  # A request through Caddy, as a learner's browser would make it.
  wait_for "healthz through caddy" curl -fsS --max-time 5 "https://$API_DOMAIN/healthz" || return 1
}

rollout() { # steps 5-7 of Д-§6.1; databases and Caddy are only started when missing
  dc up -d --no-recreate postgres neo4j redis
  dc up -d --no-deps projector
  dc up -d --no-deps compute workers
  dc up -d --no-deps api
  dc up -d --no-recreate --no-deps caddy
}

PREVIOUS="$(current_sha)"
[[ "$PREVIOUS" != "$SHA" ]] || { log "skip sha=$SHA already running"; exit 0; }
touch "$JOURNAL"
ROLLBACK=0
if grep -q " OK sha=$SHA\b" "$JOURNAL"; then ROLLBACK=1; fi

# 1. Version.
log "start sha=$SHA previous=${PREVIOUS:-none} rollback=$ROLLBACK"
docker pull "$(backend_image "$SHA")" >/dev/null
docker pull "$(compute_image "$SHA")" >/dev/null

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
registry_of "$SHA" > "$TMP/new.json"
EVENTS_CHANGED=0; RULES_CHANGED=0; NEW_MIGRATIONS=0

if [[ -n "$PREVIOUS" ]]; then
  registry_of "$PREVIOUS" > "$TMP/old.json"
  # 2. New events or rules? Recorded in the journal, the rollback decision depends on it.
  eval "$(compare_registries "$TMP/old.json" "$TMP/new.json" | tr ' ' '\n' | tr a-z A-Z)"
  migrations_of "$PREVIOUS" > "$TMP/old.mig"; migrations_of "$SHA" > "$TMP/new.mig"
  if [[ -n "$(comm -13 "$TMP/old.mig" "$TMP/new.mig")" ]]; then NEW_MIGRATIONS=1; fi
else
  NEW_MIGRATIONS=1  # first rollout: the schema does not exist yet
fi
log "registry sha=$SHA events_changed=$EVENTS_CHANGED rules_changed=$RULES_CHANGED new_migrations=$NEW_MIGRATIONS"

# From here on compose sees the new release; the old file is kept to step back on a failure.
OLD_RELEASE="$(cat "$RELEASE_FILE" 2>/dev/null || true)"
restore_release() { if [[ -n "$OLD_RELEASE" ]]; then printf '%s
' "$OLD_RELEASE" > "$RELEASE_FILE"; else rm -f "$RELEASE_FILE"; fi; }
write_release "$SHA"

if ((ROLLBACK)); then
  # Д-§6.3: a rollback is allowed only when no new event pair was written and rules@N did not
  # change. The check runs in the running (newer) image against the registry of the target.
  if ! dc exec -T api python -m quack.cli ops can-rollback --to-registry /dev/stdin < "$TMP/new.json" >> "$JOURNAL"; then
    restore_release
    die "rollback not safe, fix forward (ops can-rollback refused)"
  fi
else
  # Roles before the dump (the dump reads as quack_reader) and before the migration (Д-§7).
  dc run --rm migrate ops db-roles || { restore_release; die "ops db-roles failed; nothing was changed"; }
fi
if ((!ROLLBACK && NEW_MIGRATIONS)); then
  # 3. Dump before the migration (Д-§8.1). Needs `ops backup now` (WP9.5). The very first
  # rollout has no data to lose and may skip it with ALLOW_NO_DUMP=1.
  if [[ -z "$PREVIOUS" && "${ALLOW_NO_DUMP:-0}" == 1 ]]; then
    log "skip dump sha=$SHA first rollout, ALLOW_NO_DUMP=1"
  elif ! dc run --rm backup ops backup now; then
    restore_release
    die "pre-migration dump failed; nothing was changed"
  fi
  # 4. Migrations. A failed migration leaves the services on the old version.
  if ! dc run --rm migrate; then
    restore_release
    die "migration failed; services stay on ${PREVIOUS:-nothing}"
  fi
  log "ok migrate sha=$SHA"
fi

# 5-7. projector, compute and workers, api.
rollout

# 8. Check; a failed check rolls back automatically only when it is safe (Д-§6.3).
if check_release; then
  log "OK sha=$SHA events_changed=$EVENTS_CHANGED rules_changed=$RULES_CHANGED"
  exit 0
fi

log "check failed sha=$SHA"
if [[ -n "$PREVIOUS" && "$EVENTS_CHANGED" == 0 && "$RULES_CHANGED" == 0 && "$NEW_MIGRATIONS" == 0 ]]; then
  write_release "$PREVIOUS"
  rollout
  check_release && log "ROLLED_BACK sha=$SHA to=$PREVIOUS" || log "ROLLBACK_FAILED sha=$SHA to=$PREVIOUS"
  exit 1
fi
die "release changed events, rules or schema; automatic rollback is not safe. Check \`ops can-rollback\` and fix forward or roll back by hand (Д-§6.3)"
