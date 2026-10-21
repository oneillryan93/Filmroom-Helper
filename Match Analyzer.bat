@echo off
rem Match Analyzer launcher (Windows). No questions asked: if uv isn't available it's installed into .tools\uv
rem inside this folder (no admin, nothing added to PATH), then uv sets up Python and the libraries in
rem pyproject.toml and starts the app, which opens in your browser. Only the first start downloads anything.
setlocal EnableExtensions
title Match Analyzer
cd /d "%~dp0"
set "LOCAL_UV=%~dp0.tools\uv"

call :finduv
if defined UV goto :start

echo First start: setting up Match Analyzer. This only happens once and takes a minute or two...
set "UV_INSTALL_DIR=%LOCAL_UV%"
set "UV_NO_MODIFY_PATH=1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
call :finduv
if defined UV goto :start

echo.
echo Setup couldn't finish: check your internet connection and double-click this file again.
pause
exit /b 1

:start
rem keep the AI add-on once it has been turned on in the app
set "EXTRA="
if exist "%~dp0.models\ai-enabled" set "EXTRA=--extra ai"
rem The app checks GitHub for a newer version on start and, after updating itself (this file too), exits with 75
rem to be started again. It all runs as one block because cmd reads a whole block before running it, so the file
rem changing underneath can't confuse it; the new copy of this file then takes over.
(
  "%UV%" run %EXTRA% app.py %*
  if errorlevel 75 if not errorlevel 76 (
    call "%~f0" %*
    exit /b
  )
  if errorlevel 1 pause
  exit /b
)

:finduv
rem This folder's own copy first, then one already on the computer
set "UV="
if exist "%LOCAL_UV%\uv.exe" set "UV=%LOCAL_UV%\uv.exe"
if not defined UV if exist "%LOCAL_UV%\bin\uv.exe" set "UV=%LOCAL_UV%\bin\uv.exe"
if not defined UV for /f "delims=" %%U in ('where uv 2^>nul') do if not defined UV set "UV=%%U"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
exit /b
