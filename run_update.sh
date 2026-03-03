#!/usr/bin/env bash
set -euo pipefail

LAMBDA_DIR="$(dirname "$0")"
SITE_DIR="/Users/troboukis/Code/troboukis.github.io"
SITE_TARGET="$SITE_DIR/elliniko"

cd "$LAMBDA_DIR"

echo "=== Pulling latest changes ==="
git pull

echo ""
echo "=== Running data update ==="
python update_data.py

echo ""
echo "=== Committing changes (lambda) ==="
git add ellhniko_all.csv oikodomikes_adeies.csv oikopeda.csv permits_ellhniko.csv map.html

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
echo "=== Copying map.html to site ==="
mkdir -p "$SITE_TARGET"
cp map.html "$SITE_TARGET/map.html"
echo "  Copied to $SITE_TARGET/map.html"

echo ""
echo "=== Updating site repo ==="
cd "$SITE_DIR"
git pull

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
