#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$PLUGIN_ROOT/src"
OUTPUT="$PLUGIN_ROOT/zotero-paper-agent.xpi"

rm -f "$OUTPUT"
cd "$SRC_DIR"
zip -r "$OUTPUT" . -x ".*" -x "__MACOSX/*" -x "*.DS_Store"

echo "Built: $OUTPUT"

