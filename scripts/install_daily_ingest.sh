#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/simonclifford/projects/find-a-grant"
PLIST_NAME="com.find-a-grant.daily-ingest.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$LAUNCH_AGENTS_DIR"

chmod +x "$PROJECT_DIR/scripts/run_daily_ingest.sh"
cp "$PROJECT_DIR/scripts/$PLIST_NAME" "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

launchctl unload "$LAUNCH_AGENTS_DIR/$PLIST_NAME" 2>/dev/null || true
launchctl load "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "Installed daily Grant Finder ingest at 06:00 using $LAUNCH_AGENTS_DIR/$PLIST_NAME"
