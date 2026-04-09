#!/bin/sh
set -eu

URL="${FFMPEG_RTMP_URL:-rtmp://nginx-rtmp:1935/live/logger-1}"
HOLD_SEC="${ANALOG_HOLD_SEC:-8}"
FRAMES_DIR="${ANALOG_FRAMES_DIR:-/frames}"
PATTERN="${FRAMES_DIR}/frame_%02d.jpg"
LOOP_FILE="/tmp/analog_loop.mp4"

if [ ! -f "${FRAMES_DIR}/frame_01.jpg" ]; then
  echo "ffmpeg-analog: missing ${FRAMES_DIR}/frame_01.jpg"
  echo "Mount test_images/analog_sequence to ${FRAMES_DIR}"
  exit 1
fi

echo "ffmpeg-analog: build local loop file from ${PATTERN} (hold=${HOLD_SEC}s)"
ffmpeg -hide_banner -nostdin -loglevel warning -y \
  -framerate "1/${HOLD_SEC}" \
  -start_number 1 \
  -i "${PATTERN}" \
  -vf "scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,format=yuv420p" \
  -r 25 \
  -c:v libx264 -preset veryfast \
  -pix_fmt yuv420p \
  -movflags +faststart \
  "${LOOP_FILE}"

echo "ffmpeg-analog: loop publish ${LOOP_FILE} -> ${URL}"
while true; do
  ffmpeg -hide_banner -nostdin -loglevel warning \
    -re \
    -stream_loop -1 \
    -i "${LOOP_FILE}" \
    -vf "format=yuv420p" \
    -c:v libx264 -preset ultrafast -tune zerolatency \
    -g 25 -keyint_min 25 \
    -b:v 500k \
    -f flv "${URL}?timeout=10000000" || true
  echo "ffmpeg-analog: ffmpeg exited, sleep 3s and retry"
  sleep 3
done
