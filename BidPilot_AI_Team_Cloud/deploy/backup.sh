#!/usr/bin/env bash
set -euo pipefail
umask 077
project_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"
backup_dir="${1:-$project_dir/backups}"
mkdir -p "$backup_dir"
temporary_path="$(mktemp "$backup_dir/bidpilot-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
backup_path="$temporary_path.dump"
trap 'rm -f -- "$temporary_path"' EXIT
docker compose exec -T db pg_dump -U bidpilot -d bidpilot --format=custom > "$temporary_path"
mv -- "$temporary_path" "$backup_path"
printf 'Database backup saved: %s\nKeep an encrypted off-server copy of this file and a separate private .env backup.\n' "$backup_path"
