@echo off
setlocal

rem argv: [stream-key] [rtmp-host]
set STREAM_KEY=%1
if "%STREAM_KEY%"=="" set STREAM_KEY=logger-1

set RTMP_HOST=%2
if "%RTMP_HOST%"=="" set RTMP_HOST=host.docker.internal

echo Checking FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ERROR: ffmpeg is not found in PATH. Install ffmpeg and try again.
  exit /b 1
)

echo Starting test stream to rtmp://%RTMP_HOST%:1935/live/%STREAM_KEY%
rem flv1 — быстрый старт потока; libx264 иногда долго не отдаёт первый кадр и backend/ffmpeg не могут сделать snapshot
ffmpeg -re -f lavfi -i "testsrc=size=640x480:rate=30" ^
  -pix_fmt yuv420p -c:v flv1 -q:v 4 ^
  -f flv "rtmp://%RTMP_HOST%:1935/live/%STREAM_KEY%"

echo Stream stopped.
pause