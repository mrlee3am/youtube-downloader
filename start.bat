@echo off
setlocal
title YouTube Downloader
cd /d "%~dp0"

echo ============================================
echo  YouTube Downloader - one-click startup
echo ============================================
echo.

:: ---------- 1. Find a REAL python.exe ----------
:: (Windows ships a fake "python" stub that just opens the Microsoft Store;
::  we detect and ignore it.)
set "PYEXE="
for /f "delims=" %%E in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%E"

if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        echo %%P | findstr /i /c:"WindowsApps" >nul
        if errorlevel 1 (
            "%%P" --version >nul 2>&1
            if not errorlevel 1 set "PYEXE=%%P"
        )
    )
)

if not defined PYEXE (
    echo [1/4] No working Python found - installing via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
    "%PYEXE%" --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERROR: Python install failed. Install it manually from python.org
        echo ^(check "Add python.exe to PATH"^) and run start.bat again.
        pause
        exit /b 1
    )
)
echo [1/4] Python OK
"%PYEXE%" --version

:: ---------- 2. yt-dlp ----------
echo [2/4] Installing/updating yt-dlp...
"%PYEXE%" -m pip install --upgrade --quiet yt-dlp
if errorlevel 1 (
    echo.
    echo ERROR: could not install yt-dlp. Check your internet connection and try again.
    pause
    exit /b 1
)

:: ---------- 3. ffmpeg ----------
where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    echo [3/4] ffmpeg not found - installing via winget...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    :: The installer refreshes PATH, but this shell can't see it yet.
    :: Find the fresh install and use it right away.
    set "FFBIN="
    for /f "delims=" %%F in ('dir /s /b "%LocalAppData%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\bin\ffmpeg.exe" 2^>nul') do set "FFBIN=%%~dpF"
    if defined FFBIN (
        set "PATH=%FFBIN%;%PATH%"
        echo         ffmpeg found, using it right away.
    ) else (
        set "PATH=%LocalAppData%\Microsoft\WinGet\Links;%PATH%"
    )
    where ffmpeg >nul 2>nul
    if %errorlevel% neq 0 (
        echo.
        echo WARNING: ffmpeg not on PATH in this window. Close it and run start.bat again.
    )
) else (
    echo [3/4] ffmpeg OK
)

:: ---------- 4. Launch ----------
echo [4/4] Starting the app...
echo.
echo  Open http://127.0.0.1:8765 in your browser.
echo  Keep this window open while you use the app. Close it to stop.
echo.
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8765
"%PYEXE%" ytdl_app.py
pause
