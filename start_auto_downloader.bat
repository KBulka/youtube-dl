@echo off
REM YouTube Auto-Downloader Starter Script
REM This script starts the clipboard monitor in the background

cd /d "%~dp0"

echo Starting YouTube Auto-Downloader...
echo.

REM Start Python script
python youtube_auto_downloader.py

pause
