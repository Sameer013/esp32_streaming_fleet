@echo off
setlocal

:: Why %~dp0? It gives the directory of the batch file, ensuring it works regardless of where it's run from.
echo Working directory: %~dp0
set "ICECAST_PATH=%~dp0Icecast" 
set "FFMPEG_PATH=%~dp0ffmpeg-8.1\bin"
set "PATH=%FFMPEG_PATH%;%PATH%"
set ICECAST_URL=icecast://source:password@127.0.0.1:8000/live


echo Starting Icecast Server...
echo Using Icecast path: %ICECAST_PATH%
start "Icecast" /D "%ICECAST_PATH%" "%ICECAST_PATH%\icecast.bat"

title Joda Service Launcher

echo Waiting 5 seconds for Icecast to initialize...
timeout /t 5 /nobreak >nul

:: echo Starting FFmpeg stream...
:: start "FFmpeg" "%FFMPEG_PATH%\ffmpeg.exe" -f dshow -audio_buffer_size 10 -i audio="Stereo Mix (Realtek(R) Audio)" -ac 1 -ar 22050 -b:a 64k -c:a libmp3lame -content_type audio/mpeg -f mp3 %ICECAST_URL%

:: uvicorn server:app --port 5000 --host 0.0.0.0

echo All services started.
endlocal
