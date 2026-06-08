#!/bin/bash
# railrun <abs-path-to-.rail> [timeout_s]
# Serialized compile+run wrapper. Many swarm agents build in parallel and the Rail
# compiler writes a SHARED /tmp/rail_out — concurrent compiles clobber each other.
# An flock serializes the compile+run so each agent gets a clean build. Call as:
#   bash scripts/railrun.sh /abs/path/src/module.rail
# (no +x needed). Compiles from the rail repo root so `import "stdlib/..."` resolves.
T=${2:-180}
exec 9>/tmp/railrun.lock
flock 9
cd /Users/ledaticempire/projects/rail || { echo "RAILRUN_ERR: no rail repo"; exit 3; }
perl -e 'alarm shift; exec @ARGV' "$T" ./rail_native run "$1"
