@echo off
setlocal

rem Looped static image (test_images\gauge.jpg) as RTMP, like a fixed camera on a meter.
rem Usage: test_with_gauge.bat [stream-key] [rtmp-host]
set STREAM_KEY=%1
if "%STREAM_KEY%"=="" set STREAM_KEY=logger-1

set RTMP_HOST=%2
if "%RTMP_HOST%"=="" set RTMP_HOST=127.0.0.1

set IMG=%~dp0..\test_images\gauge.jpg
if not exist "%IMG%" (
  echo ERROR: Missing %IMG% — run from repo or generate with ffmpeg (see README in test_images).
  exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ERROR: ffmpeg not in PATH.
  exit /b 1
)

echo Streaming %IMG% to rtmp://%RTMP_HOST%:1935/live/%STREAM_KEY%
ffmpeg -re -stream_loop -1 -i "%IMG%" ^
  -vf "scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,format=yuv420p" ^
  -c:v flv1 -q:v 4 ^
  -f flv "rtmp://%RTMP_HOST%:1935/live/%STREAM_KEY%"

echo Stream stopped.
pause
