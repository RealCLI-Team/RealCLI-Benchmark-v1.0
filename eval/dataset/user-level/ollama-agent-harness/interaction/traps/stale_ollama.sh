#!/usr/bin/env bash
# Prebuilt ollama CLI, installed from an earlier checkout of the source tree.
# This is a standalone binary on PATH; it is not rebuilt from /app on each run.
if [ "${1:-}" = "run" ]; then
  for arg in "$@"; do
    case "$arg" in
      --experimental|--experimental-yolo|--experimental-websearch)
        echo "entering experimental agent loop (prototype)..."
        exit 0
        ;;
    esac
  done
  echo "usage: ollama run MODEL [PROMPT]"
  exit 0
fi
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  cat <<'HELP'
Usage: ollama run MODEL [flags]
Flags:
      --experimental             enter the experimental interactive agent loop
      --experimental-yolo        approve all tool calls without prompting
      --experimental-websearch   enable the experimental web tools
HELP
  exit 0
fi
echo "ollama (prebuilt from an earlier checkout)"
exit 0
