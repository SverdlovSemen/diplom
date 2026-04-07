#!/usr/bin/env bash
set -euo pipefail

STREAM_KEY="${1:-logger-1}"
RTMP_HOST="${2:-${RTMP_HOST:-localhost}}"

command -v ffmpeg >/dev/null 2>&1 || { echo >&2 "ERROR: ffmpeg is not installed or not in PATH"; exit 1; }

echo "Starting test stream to rtmp://${RTMP_HOST}:1935/live/${STREAM_KEY}"
ffmpeg -re -f lavfi -i "testsrc=size=640x480:rate=30" \
  -pix_fmt yuv420p -c:v libx264 -preset ultrafast -tune zerolatency \
  -f flv "rtmp://${RTMP_HOST}:1935/live/${STREAM_KEY}"

echo "Stream stopped."