#!/bin/bash
# railrun <path-to-.rail> [timeout_s]
# Serialized compile+run wrapper. Many swarm agents build in parallel and the Rail
# compiler writes a SHARED /tmp/rail_out — concurrent compiles clobber each other.
# An flock serializes the compile+run so each agent gets a clean build. Call as:
#   bash scripts/railrun.sh /abs/path/src/module.rail
# (no +x needed). Compiles from the rail repo root so `import "stdlib/..."` resolves.
T=${2:-180}

# Resolve the source to an ABSOLUTE path against the CALLER's cwd BEFORE we cd into the rail
# repo, then REFUSE to proceed if it does not exist. Otherwise a relative or mistyped path
# resolves to a nonexistent file after the cd, and `rail_native run` silently executes a STALE
# /tmp/rail_out -- e.g. a previously-compiled signer binary, which then appends to a live
# ledger. (That exact trap once polluted the AIS ledger.) Fail loud instead of running stale.
SRC="$1"
case "$SRC" in
    /*) : ;;
    *)  SRC="$(pwd)/$SRC" ;;
esac
if [ ! -f "$SRC" ]; then
    echo "RAILRUN_ERR: source '$1' not found (resolved: $SRC) -- refusing to run a stale /tmp/rail_out" >&2
    exit 4
fi

# Serialize the shared-/tmp/rail_out compile. A bare `flock` is a silent no-op under launchd's
# minimal PATH (Homebrew, where flock lives, is off it); resolve the absolute path. If flock is
# genuinely unavailable, proceed unserialized (prior behaviour) rather than abort the run.
exec 9>/tmp/railrun.lock
FLOCK_BIN="$(command -v flock 2>/dev/null || true)"
[ -x "$FLOCK_BIN" ] || FLOCK_BIN="/opt/homebrew/bin/flock"
[ -x "$FLOCK_BIN" ] && "$FLOCK_BIN" 9

cd /Users/ledaticempire/projects/rail || { echo "RAILRUN_ERR: no rail repo"; exit 3; }
perl -e 'alarm shift; exec @ARGV' "$T" ./rail_native run "$SRC"
