#!/bin/sh
# Own test infrastructure of one session or subagent K (1..9): containers qa-K-pg, qa-K-neo4j,
# qa-K-redis on ports 56K01 (Postgres), 56K02 (Neo4j bolt), 56K03 (Redis). Two test runs must never
# share one set of databases (they truncate each other's tables).
#   scripts/dev/test_infra.sh up K     start (idempotent), waits for Neo4j
#   scripts/dev/test_infra.sh down K   remove the containers
set -eu
cmd=${1:?up|down}
k=${2:?K 1..9}
case "$k" in [1-9]) ;; *) echo "K must be 1..9" >&2; exit 2 ;; esac

if [ "$cmd" = down ]; then
  docker rm -f "qa-$k-pg" "qa-$k-neo4j" "qa-$k-redis" >/dev/null 2>&1 || true
  echo "qa-$k removed"
  exit 0
fi

start() {
  name=$1; shift
  if docker ps --format '{{.Names}}' | grep -qx "$name"; then return; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" "$@" >/dev/null
}
start "qa-$k-pg" -e POSTGRES_USER=quack -e POSTGRES_PASSWORD=quack -e POSTGRES_DB=quack_test \
  -p "56${k}01:5432" postgres:16
start "qa-$k-neo4j" -e NEO4J_AUTH=neo4j/quacktest123 \
  -e NEO4J_server_memory_heap_max__size=512m -e NEO4J_server_memory_pagecache_size=256m \
  -p "56${k}02:7687" neo4j:2026.06-community
start "qa-$k-redis" -p "56${k}03:6379" redis:7

i=0
until docker exec "qa-$k-neo4j" cypher-shell -u neo4j -p quacktest123 'RETURN 1' >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && { echo "Neo4j of qa-$k did not start" >&2; exit 1; }
  sleep 2
done
echo "qa-$k up: postgres 56${k}01, neo4j 56${k}02, redis 56${k}03"
