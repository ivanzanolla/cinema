#!/bin/bash
# Scarica la programmazione di The Space dal Mac e la pubblica nel repo.
# Lanciato da launchd (vedi install.sh); si può eseguire anche a mano.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== $(date '+%F %T')"
git pull -q --rebase --autostash origin main
python3 scraper/thespace.py data/thespace.json
git add data/thespace.json
if git diff --cached --quiet; then
  echo "nessuna modifica"
else
  git commit -qm "The Space: aggiorna programmazione (Mac)"
  git push -q origin main
  echo "pubblicato"
fi
