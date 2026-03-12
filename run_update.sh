#!/usr/bin/env bash
set -euo pipefail

LAMBDA_DIR="$(cd "$(dirname "$0")" && pwd)"
SITE_DIR="/Users/troboukis/Code/troboukis.github.io"
SITE_TARGET="$SITE_DIR/elliniko"

cd "$LAMBDA_DIR"

if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "=== Saving local changes before pull ==="
  git add -A
  git commit -m "chore: save local changes $(date +%Y-%m-%d-%H%M%S)"
fi

echo "=== Pulling latest changes ==="
git pull --rebase

echo ""
echo "=== Running data update ==="
python update_data.py

echo ""
echo "=== Committing changes (lambda) ==="
git add ellhniko_all.csv oikodomikes_adeies.csv oikopeda.csv permits_ellhniko.csv last_search.json map.html

if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "data: manual update $(date +%Y-%m-%d)"
  echo ""
  echo "=== Pushing to remote (lambda) ==="
  git push
  echo "Done."
fi

echo ""
echo "=== Updating site repo (pull) ==="
cd "$SITE_DIR"
git pull

echo ""
echo "=== Copying map.html to site ==="
mkdir -p "$SITE_TARGET"
cp "$LAMBDA_DIR/map.html" "$SITE_TARGET/map.html"
echo "  Copied to $SITE_TARGET/map.html"

git add elliniko/map.html

if git diff --cached --quiet; then
  echo "No changes to commit in site repo."
else
  git commit -m "elliniko: update map $(date +%Y-%m-%d)"
  echo ""
  echo "=== Pushing site ==="
  git push
  echo "Done."
fi
