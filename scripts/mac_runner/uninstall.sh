#!/usr/bin/env bash
# Remove the Mac queue runner (ADR-148). A job it started keeps running until it ends; stop it in the lab app.
set -euo pipefail
LABEL=ca.benfirth.snowpack-mac-queue
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed. Queue state and results stay in data/lab/outputs/mac_queue."
