@echo off
setlocal

rem Looped analog gauge sequence as RTMP stream.
rem Usage: test_analog_stream.bat [stream-key] [rtmp-host] [hold-sec]
set STREAM_KEY=%1
if "%STREAM_KEY%"=="" set STREAM_KEY=logger-1

set RTMP_HOST=%2
if "%RTMP_HOST%"=="" set RTMP_HOST=127.0.0.1

set HOLD_SEC=%3
if "%HOLD_SEC%"=="" set HOLD_SEC=8

set FRAMES=%~dp0..\test_images\analog_sequence
if not exist "%FRAMES%\frame_01.jpg" (
  echo ERROR: Missing analog sequence in %FRAMES%
  echo Run: py "%~dp0generate_analog_sequence.py"
  exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ERROR: ffmpeg is not found in PATH.
  exit /b 1
)

echo Streaming analog sequence to rtmp://%RTMP_HOST%:1935/live/%STREAM_KEY% (hold=%HOLD_SEC%s)
ffmpeg -re -stream_loop -1 -framerate 1/%HOLD_SEC% -start_number 1 -i "%FRAMES%\frame_%%02d.jpg" ^
  -vf "scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,format=yuv420p" ^
  -r 25 -c:v flv1 -q:v 4 ^
  -f flv "rtmp://%RTMP_HOST%:1935/live/%STREAM_KEY%"

echo Stream stopped.
pause
