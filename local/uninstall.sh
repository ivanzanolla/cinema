#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/it.ivanzanolla.insala.thespace.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST" && echo "Job rimosso"
