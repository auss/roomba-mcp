#!/usr/bin/env bash
# Fail if a tracked source or docs file contains an em dash (U+2014).
#
# The steering rule bans the em dash in prose and code. This turns that rule
# into a deterministic check instead of leaving it to memory. Use a plain
# hyphen "-" where an em dash would otherwise appear.
set -euo pipefail

if git grep -n -P -I '\x{2014}' -- \
    '*.py' '*.md' '*.toml' '*.yaml' '*.yml' '*.json' '*.rst'; then
  echo "em dash (U+2014) found in the files above" >&2
  echo "use a plain hyphen '-' instead" >&2
  exit 1
fi
