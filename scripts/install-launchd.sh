#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${LABEL:-com.marketanalyzeragents.brief}"
HOUR="${MARKET_ANALYZER_AGENTS_HOUR:-6}"
MINUTE="${MARKET_ANALYZER_AGENTS_MINUTE:-0}"
BACKEND="${MARKET_ANALYZER_AGENTS_BACKEND:-zhipu}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/runs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/scripts/run-daily-brief.sh</string>
    <string>--backend</string>
    <string>$BACKEND</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$ROOT/runs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/runs/launchd.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"

echo "Installed $LABEL at $PLIST"
echo "Schedule: daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
echo "Backend: $BACKEND"
