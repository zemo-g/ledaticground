#!/bin/bash
# ============================================================================
# install_autocap.sh — operator deploy for the DECOUPLED capture system.
# ----------------------------------------------------------------------------
# ledaticground "autocap": decouple satellite CAPTURE from data TRANSFER so the
# Pi never misses a pass when the roof WiFi is down. Replaces the Mini-driven
# pull-at-AOS model (com.ledatic.ledaticground-iq) with:
#
#   PI  (always-on, WiFi-independent at capture time):
#     pi_iq_capture.sh           -> /usr/local/bin/pi_iq_capture.sh   (separate component)
#     ledaticground-iqcap.service-> /etc/systemd/system/ledaticground-iqcap.service
#     iqcap-retention            -> /etc/cron.d/iqcap-retention
#     roofmon-deadman            -> /etc/cron.d/roofmon-deadman
#   The Pi reads a pushed ~/.iq/schedule.tsv, captures each pass on its OWN
#   clock (preempting roofmon ONLY for the window, always restarting it),
#   and KEEPS captures in ~/.iq/captures/ until the Mini pulls them.
#
#   MINI (orbital brain + janitor; tolerates flaky link):
#     com.ledatic.ledaticground-iqsched.plist -> ~/Library/LaunchAgents/  (writes+pushes schedule.tsv)
#     com.ledatic.ledaticground-iqpull.plist  -> ~/Library/LaunchAgents/  (pulls+decodes new captures)
#
# This script is IDEMPOTENT (safe to re-run) and SAFE BY DEFAULT:
#   * Without --go it INSTALLS files + enables the Pi unit, but does NOT start
#     ledaticground-iqcap and does NOT bootstrap the Mini agents. It then PRINTS
#     the cutover steps for you to run deliberately. See CUTOVER.md.
#   * It NEVER stops the live com.ledatic.ledaticground-iq on its own. The two
#     systems must never both run (they would fight the single SDR) — the
#     cutover that disables the old agent is a human decision (CUTOVER.md).
#   * With --go it performs the Mini-side bootstrap (old-agent disable + new
#     agents up) but STILL leaves the Pi-side start to you over SSH, because
#     starting ledaticground-iqcap is the moment of contention (it grabs the
#     single SDR) and you want eyes on it.
#
# Usage:
#   ./install_autocap.sh            # install + enable(no-start); print cutover
#   ./install_autocap.sh --go       # install + Mini cutover (disable old / up new); print Pi start
#   ./install_autocap.sh --dry-run  # show every action, change nothing
#   ./install_autocap.sh --uninstall-mini   # remove the two Mini plists (Pi untouched)
#
# Env overrides (match pass_scheduler.sh): PI_HOST, PI_USER.
# ============================================================================
set -u

# ---- config -----------------------------------------------------------------
GD=/Users/ledaticempire/projects/ledaticground
STAGE="$GD/scripts/autocap"
PI_HOST="${PI_HOST:-100.115.30.12}"
PI_USER="${PI_USER:-ledatic}"
PI="${PI_USER}@${PI_HOST}"
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"
LAUNCH="$HOME/Library/LaunchAgents"

# Sibling artifacts (produced by the other autocap components, staged here).
PI_CAP_SRC="$STAGE/pi_iq_capture.sh"            # pi_capture component (SEPARATE — may not be staged yet)
PI_UNIT_SRC="$STAGE/ledaticground-iqcap.service"  # pi_capture component (systemd unit)
PI_RETENTION_SRC="$STAGE/iqcap-retention"       # THIS component (cron snippet)
PI_DEADMAN_SRC="$STAGE/roofmon-deadman"         # THIS component (cron snippet; roofmon resurrection w/ SDR guards)
MINI_SCHED_SRC="$STAGE/com.ledatic.ledaticground-iqsched.plist"   # THIS component (wraps push_iq_schedule.sh)
MINI_PULL_SRC="$STAGE/com.ledatic.ledaticground-iqpull.plist"     # THIS component (wraps pull_iq.sh)

# systemd unit + Pi service NAME (note: ledaticground- prefixed, NOT bare iqcap).
PI_UNIT_NAME="ledaticground-iqcap.service"
PI_CAP_PRESENT=0   # set by require_files(); 1 iff pi_iq_capture.sh is staged

# Old (live) Mini agent we are eventually replacing — NEVER auto-stopped here.
OLD_LABEL="com.ledatic.ledaticground-iq"
OLD_PLIST="$LAUNCH/${OLD_LABEL}.plist"

# New Mini labels.
SCHED_LABEL="com.ledatic.ledaticground-iqsched"
PULL_LABEL="com.ledatic.ledaticground-iqpull"

GO=0; DRY=0; UNINST=0
for a in "$@"; do
  case "$a" in
    --go)            GO=1 ;;
    --dry-run|-n)    DRY=1 ;;
    --uninstall-mini) UNINST=1 ;;
    -h|--help)       sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a (try --help)"; exit 2 ;;
  esac
done

c_ok="";  c_warn=""; c_step=""; c_off=""
if [ -t 1 ]; then c_ok="$(printf '\033[32m')"; c_warn="$(printf '\033[33m')"; c_step="$(printf '\033[36m')"; c_off="$(printf '\033[0m')"; fi
say(){  echo "${c_ok}==>${c_off} $*"; }
warn(){ echo "${c_warn}!! ${c_off} $*" >&2; }
step(){ echo "${c_step}  \$${c_off} $*"; }
die(){  echo "ERROR: $*" >&2; exit 1; }

# run / show a command depending on --dry-run
run(){
  if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; return 0; fi
  "$@"
}
# same, but for a remote (ssh) command string
runssh(){
  if [ "$DRY" = 1 ]; then echo "[dry-run] $SSH $PI \"$*\""; return 0; fi
  $SSH "$PI" "$*"
}

# Hard requirement: the unit, the retention cron, and the two Mini plists must
# all be present or we refuse to deploy a half-system.
require_files(){
  local missing=0 f
  for f in "$PI_UNIT_SRC" "$PI_RETENTION_SRC" "$PI_DEADMAN_SRC" "$MINI_SCHED_SRC" "$MINI_PULL_SRC"; do
    if [ ! -f "$f" ]; then warn "missing staged artifact: $f"; missing=1; fi
  done
  [ "$missing" = 0 ] || die "stage incomplete — these autocap components must be present in $STAGE before deploy."
  # Also need the two Mini-side scripts the plists invoke (they live in staging too).
  for f in "$STAGE/push_iq_schedule.sh" "$STAGE/pull_iq.sh"; do
    [ -f "$f" ] || warn "Mini script not staged: $f (the plist points at it; install it before cutover)"
  done
  # pi_iq_capture.sh is a SEPARATE component. If it isn't staged yet we still
  # install everything else; the unit will simply fail-to-start (cleanly) until
  # the capture script lands at /usr/local/bin. Warn loudly, don't abort.
  if [ ! -f "$PI_CAP_SRC" ]; then
    PI_CAP_PRESENT=0
    warn "pi_iq_capture.sh NOT staged ($PI_CAP_SRC) — the Pi unit's ExecStart."
    warn "  -> Installing the unit + cron + Mini agents anyway, but $PI_UNIT_NAME will"
    warn "     NOT start successfully until that capture script is installed to"
    warn "     /usr/local/bin/pi_iq_capture.sh. Stage it, then re-run this installer."
  else
    PI_CAP_PRESENT=1
  fi
}

# ---------------------------------------------------------------------------
# UNINSTALL (Mini only): bootout the new agents + remove their plists. Pi left
# alone. Does NOT re-enable the old agent (that is a manual rollback decision).
# ---------------------------------------------------------------------------
if [ "$UNINST" = 1 ]; then
  say "Uninstalling Mini autocap agents (Pi untouched)."
  for L in "$SCHED_LABEL" "$PULL_LABEL"; do
    run launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
    run rm -f "$LAUNCH/${L}.plist"
    say "removed $L"
  done
  warn "Pi $PI_UNIT_NAME is still installed/enabled. To fully revert see CUTOVER.md (ROLLBACK)."
  exit 0
fi

# ---------------------------------------------------------------------------
# PRE-FLIGHT
# ---------------------------------------------------------------------------
require_files
[ "$DRY" = 1 ] && say "DRY-RUN: no changes will be made."

say "Pi target: $PI   (override with PI_HOST/PI_USER)"
if [ "$DRY" != 1 ]; then
  if $SSH "$PI" "true" 2>/dev/null; then
    say "Pi reachable over SSH."
  else
    warn "Pi NOT reachable over SSH right now ($PI)."
    warn "The Pi half will be skipped; re-run when the Pi is online. Mini half can still proceed."
  fi
fi

PI_UP=0
[ "$DRY" = 1 ] && PI_UP=1
if [ "$DRY" != 1 ] && $SSH "$PI" "true" 2>/dev/null; then PI_UP=1; fi

# ===========================================================================
# (a) PI SIDE — capture script + systemd unit + retention cron.
# ===========================================================================
if [ "$PI_UP" = 1 ]; then
  say "[Pi] installing capture script, unit, and retention cron."

  # Ensure the capture store + state dir exist (idempotent).
  runssh "mkdir -p ~/.iq/captures && touch ~/.iq/manifest.tsv && chmod 700 ~/.iq"

  # 1) capture script -> /usr/local/bin (root, 0755). scp to a temp the user can
  #    write, then sudo-install into place atomically. SKIPPED if the separate
  #    pi_capture component isn't staged yet (unit will fail-to-start until it is).
  if [ "$PI_CAP_PRESENT" = 1 ]; then
    say "[Pi] -> /usr/local/bin/pi_iq_capture.sh"
    run scp -q "$PI_CAP_SRC" "$PI:/tmp/pi_iq_capture.sh.new"
    runssh "sudo install -m 0755 -o root -g root /tmp/pi_iq_capture.sh.new /usr/local/bin/pi_iq_capture.sh && rm -f /tmp/pi_iq_capture.sh.new"
  else
    warn "[Pi] pi_iq_capture.sh not staged — NOT installing the ExecStart script."
    warn "[Pi] $PI_UNIT_NAME will fail to start until /usr/local/bin/pi_iq_capture.sh exists."
  fi

  # 2) systemd unit -> /etc/systemd/system (root, 0644).
  say "[Pi] -> /etc/systemd/system/$PI_UNIT_NAME"
  run scp -q "$PI_UNIT_SRC" "$PI:/tmp/${PI_UNIT_NAME}.new"
  runssh "sudo install -m 0644 -o root -g root /tmp/${PI_UNIT_NAME}.new /etc/systemd/system/$PI_UNIT_NAME && rm -f /tmp/${PI_UNIT_NAME}.new"

  # 3) retention cron -> /etc/cron.d (root, 0644). cron.d ignores files with a
  #    dot or wrong perms, so the name must stay 'iqcap-retention' and mode 0644.
  say "[Pi] -> /etc/cron.d/iqcap-retention"
  run scp -q "$PI_RETENTION_SRC" "$PI:/tmp/iqcap-retention.new"
  runssh "sudo install -m 0644 -o root -g root /tmp/iqcap-retention.new /etc/cron.d/iqcap-retention && rm -f /tmp/iqcap-retention.new"

  # 3b) roofmon deadman cron -> /etc/cron.d (root, 0644). Resurrects roofmon if it
  #     dies, but NEVER while any rtl_* holds the dongle (an iqcap pass window
  #     legitimately stops roofmon — the pgrep guards keep the deadman from
  #     stealing the SDR back mid-capture). Was Pi-local-only (8bab1fb); folded
  #     into the repo so a re-flash keeps it. Same cron.d naming rules as above.
  say "[Pi] -> /etc/cron.d/roofmon-deadman"
  run scp -q "$PI_DEADMAN_SRC" "$PI:/tmp/roofmon-deadman.new"
  runssh "sudo install -m 0644 -o root -g root /tmp/roofmon-deadman.new /etc/cron.d/roofmon-deadman && rm -f /tmp/roofmon-deadman.new"

  # 4) reload systemd + ENABLE (so it auto-starts after the nightly power-cycle)
  #    but DO NOT start now — starting is the contention moment, done at cutover.
  #    NOTE: the unit's own deploy comment suggests `enable --now`; we deliberately
  #    override to plain `enable` (no --now) to honor the no-start-without---go rule.
  say "[Pi] systemctl daemon-reload + enable $PI_UNIT_NAME (NOT starting)."
  runssh "sudo systemctl daemon-reload && sudo systemctl enable $PI_UNIT_NAME"

  # Sanity: unit file parses?
  if [ "$DRY" != 1 ]; then
    if $SSH "$PI" "systemctl cat $PI_UNIT_NAME >/dev/null 2>&1"; then
      say "[Pi] $PI_UNIT_NAME installed + enabled (inactive)."
    else
      warn "[Pi] $PI_UNIT_NAME did not parse — check $PI_UNIT_SRC."
    fi
  fi
else
  warn "[Pi] SKIPPED (Pi offline). Re-run this installer when the Pi is up to finish the Pi half."
fi

# ===========================================================================
# (b) MINI SIDE — install the two plists. Bootstrap ONLY with --go.
# ===========================================================================
say "[Mini] installing LaunchAgent plists -> $LAUNCH"
run mkdir -p "$LAUNCH"
run cp "$MINI_SCHED_SRC" "$LAUNCH/${SCHED_LABEL}.plist"
run cp "$MINI_PULL_SRC"  "$LAUNCH/${PULL_LABEL}.plist"
say "[Mini] plists in place: ${SCHED_LABEL}, ${PULL_LABEL}"

if [ "$GO" = 1 ]; then
  say "[Mini] --go: performing Mini-side cutover."

  # Stop the OLD Mini-driven agent FIRST so the new schedule push + the Pi
  # capture never coincide with a Mini-initiated capture (single-SDR safety).
  # This is the ONE place we touch the old agent, and ONLY under explicit --go.
  if [ -f "$OLD_PLIST" ]; then
    say "[Mini] disabling OLD agent $OLD_LABEL (bootout + disable + rename so it can't reload on reboot)."
    run launchctl bootout "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
    # CRITICAL cutover-safety: bootout alone is NOT enough — the old plist has
    # RunAtLoad=true, so a Mini REBOOT would auto-start the Mini-driven scheduler
    # and it would fight the Pi for the single SDR (the exact failure we prevent).
    # Persistently disable the label AND move the plist aside (project _disabled.*
    # convention). Rollback (CUTOVER.md) renames it back + enable + bootstrap.
    run launchctl disable "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
    run mv -f "$OLD_PLIST" "$LAUNCH/_disabled.${OLD_LABEL}.plist"
    warn "[Mini] $OLD_LABEL disabled + moved to _disabled.${OLD_LABEL}.plist (rollback: see CUTOVER.md)."
  else
    say "[Mini] old agent plist not found ($OLD_PLIST)."
    [ -f "$LAUNCH/_disabled.${OLD_LABEL}.plist" ] && say "[Mini] already disabled (_disabled.${OLD_LABEL}.plist present)."
  fi

  # Bootstrap the two new agents (re-bootstrap-safe: bootout first).
  for L in "$SCHED_LABEL" "$PULL_LABEL"; do
    run launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
    run launchctl bootstrap "gui/$(id -u)" "$LAUNCH/${L}.plist" || warn "[Mini] bootstrap $L failed"
    say "[Mini] bootstrapped $L"
  done

  if [ "$PI_UP" = 1 ]; then
    say "[Mini] kick the scheduler once so a fresh schedule.tsv reaches the Pi now."
    # iqsched runs on its own interval; a manual nudge avoids waiting for the
    # first tick. Done off-radio (no SDR contact), safe any time.
    run launchctl kickstart -k "gui/$(id -u)/$SCHED_LABEL" 2>/dev/null || true
  fi

  echo
  say "Mini cutover done. FINAL manual step — START the Pi capture loop with eyes on it:"
  if [ "$PI_CAP_PRESENT" != 1 ]; then
    warn "(!) pi_iq_capture.sh was not staged — installing/starting the unit now will FAIL"
    warn "    until that script is at /usr/local/bin/pi_iq_capture.sh. Stage it first."
  fi
  step "$SSH $PI 'sudo systemctl start $PI_UNIT_NAME && systemctl status $PI_UNIT_NAME --no-pager'"
  step "# then watch a window land:  $SSH $PI 'journalctl -u $PI_UNIT_NAME -f'"
  echo
  say "Verify + ROLLBACK details: $STAGE/CUTOVER.md"
else
  # ---- NOT --go: print the cutover, perform nothing destructive. ----
  echo
  say "Files installed. NO cutover performed (no --go)."
  warn "The OLD agent $OLD_LABEL is still LIVE. The new agents are installed but NOT started."
  warn "Do NOT start the Pi iqcap while the old agent runs — they would fight the single SDR."
  echo
  say "To CUT OVER deliberately, either re-run with --go, or do it by hand:"
  echo  "  ---------------------------------------------------------------------"
  step "launchctl bootout gui/\$(id -u)/$OLD_LABEL        # 1. stop Mini-driven capture"
  step "launchctl bootstrap gui/\$(id -u) $LAUNCH/${SCHED_LABEL}.plist   # 2. schedule pusher"
  step "launchctl bootstrap gui/\$(id -u) $LAUNCH/${PULL_LABEL}.plist    # 3. capture puller"
  step "launchctl kickstart -k gui/\$(id -u)/$SCHED_LABEL   # 4. push first schedule now"
  step "$SSH $PI 'sudo systemctl start $PI_UNIT_NAME'   # 5. LAST: start Pi capture"
  echo  "  ---------------------------------------------------------------------"
  say "Full procedure, verification, and ROLLBACK: $STAGE/CUTOVER.md"
fi

echo
if [ "$DRY" = 1 ]; then say "Done (dry-run)."; else say "Done."; fi
