#!/usr/bin/env bash
# Install the Mac queue runner (ADR-148): launchd runs `snowagent lab mac-queue tick` every 5 minutes (so the disk guard of ADR-158 acts quickly), which starts
# the heavy lab jobs the cloud queues on the mac-queue branch and pushes their results to the mac-results branch.
#   bash scripts/mac_runner/install.sh        # once, from the repository root, in Terminal
#   bash scripts/mac_runner/uninstall.sh      # to remove it
# Gentle on the Mac: one job at a time, nice 10, performance cores minus two as workers (SNOWAGENT_MAC_WORKERS in
# the environment of this script overrides). Jobs run on battery too (ADR-151). Run it again after moving the checkout.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$(pwd)
LABEL=ca.benfirth.snowpack-mac-queue
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$REPO/.venv/bin/python"
LOGDIR="$REPO/data/lab/outputs/mac_queue"

[ "$(uname)" = Darwin ] || { echo "This is for macOS."; exit 1; }
[ -x "$PY" ] || { echo "No .venv here: run bash scripts/setup_env.sh first."; exit 1; }
"$PY" -c "import snowagent.lab.services.mac_queue" || { echo "This checkout lacks the Mac queue: git pull first."; exit 1; }
mkdir -p "$LOGDIR/tmp" "$HOME/Library/LaunchAgents"
launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true   # undo a launchctl disable

echo "Checking that this Mac can push results to GitHub..."
if GIT_TERMINAL_PROMPT=0 git push --dry-run -q origin "HEAD:refs/heads/mac-results-push-check" 2>/dev/null; then
  echo "  ok"
else
  echo "  This Mac cannot push to GitHub yet, so results will wait in $LOGDIR/outbox."
  echo "  To fix it once: brew install gh && gh auth login   (choose GitHub.com, HTTPS, log in with a browser),"
  echo "  then: gh auth setup-git"
fi

WORKERS_ENV=""
if [ -n "${SNOWAGENT_MAC_WORKERS:-}" ]; then
  WORKERS_ENV="<key>SNOWAGENT_MAC_WORKERS</key><string>${SNOWAGENT_MAC_WORKERS}</string>"
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string><string>-m</string><string>snowagent.cli</string>
    <string>lab</string><string>mac-queue</string><string>tick</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>GIT_TERMINAL_PROMPT</key><string>0</string>
    <key>TMPDIR</key><string>$LOGDIR/tmp/</string>
    $WORKERS_ENV
  </dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>$LOGDIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/launchd.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. It checks the queue now and every 30 minutes."
echo "See what it is doing:  .venv/bin/snowagent lab mac-queue status"
echo "Keep the Mac on power with the lid open on nights a job is queued."
