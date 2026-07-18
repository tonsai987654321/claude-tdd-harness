#!/usr/bin/env bash
# Wire the working repos into the harness: clone each into the projects dir (gitignored)
# if missing, else fast-forward pull. Idempotent — safe to re-run.
#
#   .claude/scripts/link_projects.sh            # clone/update all
#   .claude/scripts/link_projects.sh --status   # just show where each stands
#
# Each project stays its OWN independent repo/remote (a reviewer reads them standalone);
# this only reproduces the working layout, it does not couple histories.
#
# Owner and project names come from .claude/harness.json — nothing about which repos exist
# is hardcoded here. Requires: gh authenticated as the owner (for private repos).
set -euo pipefail

# Resolve the harness root (dir containing .claude/scripts/harness.py), from this script's path.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
config="$root/.claude/harness.json"

if [ ! -f "$config" ]; then
  echo "link_projects: no .claude/harness.json at $root — run /harness-init first." >&2
  exit 1
fi

# Project names come from the cycle files (each is one project, and they carry build_order),
# so adding a project means adding its cycle file — there is no second list to keep in sync.
read_config() {
  python3 - "$root" <<'PY'
import json, sys
from pathlib import Path

root = Path(sys.argv[1])
cfg = json.loads((root / ".claude" / "harness.json").read_text(encoding="utf-8"))
print(cfg.get("owner", ""))
print(cfg.get("projects_dir", "projects"))

def rank(p):
    try:
        return (json.loads(p.read_text(encoding="utf-8")).get("build_order", 99), p.stem)
    except (OSError, json.JSONDecodeError):
        return (99, p.stem)

for p in sorted((root / ".claude" / "cycles").glob("*.json"), key=rank):
    print(p.stem)
PY
}

# Read line-by-line rather than with `mapfile`: macOS still ships bash 3.2 as /bin/bash, which
# has no mapfile, and this script must run on whatever bash `env` finds.
OWNER=""
projects_dir="projects"
PROJECTS=()
_line_no=0
while IFS= read -r line; do
  case "$_line_no" in
    0) OWNER="$line" ;;
    1) projects_dir="$line" ;;
    *) [ -n "$line" ] && PROJECTS+=("$line") ;;
  esac
  _line_no=$((_line_no + 1))
done < <(read_config)

if [ "${#PROJECTS[@]}" -eq 0 ]; then
  echo "link_projects: no cycle files in .claude/cycles/ — nothing to link." >&2
  exit 1
fi
if [ -z "$OWNER" ]; then
  echo "link_projects: .claude/harness.json has no \"owner\" — cannot resolve clone URLs." >&2
  exit 1
fi

dest="$root/$projects_dir"
mkdir -p "$dest"

status_only=0
[ "${1:-}" = "--status" ] && status_only=1

for name in "${PROJECTS[@]}"; do
  dir="$dest/$name"
  if [ -d "$dir/.git" ]; then
    branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
    head="$(git -C "$dir" log -1 --format='%h %s' 2>/dev/null || echo '?')"
    if [ "$status_only" = 1 ]; then
      printf '%-26s present  [%s] %s\n' "$name" "$branch" "$head"
      continue
    fi
    echo ">> $name: present on '$branch' — pulling…"
    git -C "$dir" pull --ff-only 2>&1 | sed 's/^/   /' || echo "   (pull skipped — diverged or offline; leaving as-is)"
  else
    if [ "$status_only" = 1 ]; then
      printf '%-26s MISSING (run without --status to clone)\n' "$name"
      continue
    fi
    echo ">> $name: missing — cloning…"
    gh repo clone "$OWNER/$name" "$dir" 2>&1 | sed 's/^/   /'
  fi
done

echo
echo "Layout ready under: $dest"
[ "$status_only" = 1 ] || echo "Next: ./init.sh"
