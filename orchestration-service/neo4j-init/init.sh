#!/bin/sh
# One-shot set-up for Neo4j Enterprise: test database + read-only agent user.
# Runs inside a neo4j image (which ships cypher-shell). Idempotent: safe on every bring-up.
set -eu

ADDR="bolt://neo4j:7687"

echo "waiting for neo4j..."
until cypher-shell -a "$ADDR" -u "$GRAPH_USER" -p "$GRAPH_PASSWORD"  "RETURN 1" >/dev/null 2>&1; do
  sleep 2
done

echo "creating test database and read-only user..."
cypher-shell -a "$ADDR" -u "$GRAPH_USER" -p "$GRAPH_PASSWORD" -d system <<EOF
CREATE DATABASE ${GRAPH_TEST_DATABASE} IF NOT EXISTS WAIT;
CREATE OR REPLACE USER ${GRAPH_READER_USER} SET PASSWORD '${GRAPH_READER_PASSWORD}' CHANGE NOT REQUIRED;
GRANT ROLE reader TO ${GRAPH_READER_USER};
EOF
echo "neo4j init done"
