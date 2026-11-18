#!/bin/bash
# Installa il job launchd che aggiorna The Space ogni giorno (07:40 e 13:10,
# o al risveglio se il Mac dormiva). Rieseguibile: sostituisce il job esistente.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="it.ivanzanolla.insala.thespace"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/insala-thespace.log"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
chmod +x "$REPO/local/run.sh"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$REPO/local/run.sh</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>10</integer></dict>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$HOME/.homebrew/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PL

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Job installato: $PLIST (log: $LOG)"
echo "Primo giro di prova:"
bash "$REPO/local/run.sh"
