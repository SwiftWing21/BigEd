@echo off
title BigEd CC - Bootstrap Launcher
color 0A

cd /d "%~dp0"

echo ===================================================
echo Starting BigEd CC (Developer Mode)
echo ===================================================
echo.

REM echo [1/2] Checking for updates (git pull)...
REM git pull origin main

echo.
echo [2/2] Launching BigEd CC directly...
start /b python launcher.py

exit