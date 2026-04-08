#!/bin/sh
# Бесконечный publisher. Публикует black background + white number в nginx-rtmp по Docker bridge-сети.
# - network_mode: service:nginx-rtmp не используется (не работает на Docker Desktop Windows).
# - libx264 + SPS/PPS sequence header: nginx-rtmp кэширует заголовок и сразу отдаёт
#   подписчикам — они декодируют с первого кадра без ожидания keyframe.
# - -tune zerolatency + -g 25: keyframe каждую секунду, нет lookahead-буферизации.
# - ?timeout=10000000: RTMP-URL timeout (мкс) — ffmpeg прерывает зависший handshake
#   через 10 секунд и shell-цикл переподключает. Без него при зависании nginx worker'а
#   ffmpeg мог висеть бесконечно в ожидании RTMP _result.
set -eu
URL="${FFMPEG_RTMP_URL:-rtmp://nginx-rtmp:1935/live/logger-1}"
echo "ffmpeg-test: loop publish -> $URL"
while true; do
  ffmpeg -hide_banner -nostdin -loglevel warning \
    -re \
    -f lavfi -i "color=c=black:s=640x480:r=25" \
    -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='%{eif\\:mod(t\\,1000)\\:d}':fontcolor=white:fontsize=120:x=(w-text_w)/2:y=(h-text_h)/2" \
    -pix_fmt yuv420p \
    -c:v libx264 -preset ultrafast -tune zerolatency \
    -g 25 -keyint_min 25 \
    -b:v 500k \
    -f flv "${URL}?timeout=10000000" || true
  echo "ffmpeg-test: ffmpeg exited, sleep 3s and retry"
  sleep 3
done
