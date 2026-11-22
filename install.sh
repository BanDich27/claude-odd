#!/usr/bin/env sh
# Installs /odd into one project, linking to this repository so a `git pull`
# here updates it:
#
#   ./install.sh /path/to/project [config.json]
#
# - .claude/skills/odd and .claude/agents/odd-*.md -> links to this repository
# - .claude/odd.json            -> copied from the given config (or the example) if missing
# - .claude/settings.local.json -> hooks for the live view and the costs
# - .git/info/exclude           -> keeps all of it out of the project's git
set -eu

repo=$(cd "$(dirname "$0")" && pwd)
project=${1:?usage: install.sh /path/to/project [config.json]}
project=$(cd "$project" && pwd)
config=${2:-$repo/examples/odd.json}

test -d "$project/.git" || { echo "$project is not a git repository" >&2; exit 1; }

mkdir -p "$project/.claude/skills" "$project/.claude/agents"
ln -sfn "$repo/skill/odd" "$project/.claude/skills/odd"
for agent in "$repo"/agents/odd-*.md; do
  ln -sf "$agent" "$project/.claude/agents/$(basename "$agent")"
done
if [ ! -f "$project/.claude/odd.json" ]; then
  cp "$config" "$project/.claude/odd.json"
  echo "Created .claude/odd.json from $(basename "$config"): review it."
fi

python3 "$repo/skill/odd/scripts/install_hooks.py" "$project/.claude/settings.local.json"

exclude="$project/.git/info/exclude"
for pattern in ".claude/skills/odd" ".claude/agents/odd-*.md" ".claude/odd.json" ".claude/settings.local.json"; do
  grep -qxF "$pattern" "$exclude" 2>/dev/null || echo "$pattern" >> "$exclude"
done

echo "/odd installed in $project"
