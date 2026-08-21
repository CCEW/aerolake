@echo off
setlocal enabledelayedexpansion
rem ============================================================================
rem AeroLake GUI stopper (Windows) - shut down the background Streamlit GUI
rem ----------------------------------------------------------------------------
rem Double-click me (or stop-gui.vbs, the hidden-console version) when you no
rem longer need the GUI. The GUI runs detached in WSL, so there is no window to
rem close; this stops the background process. Mirrors launch-gui.bat: no
rem hardcoded paths (derives the WSL path via wslpath), distro overridable via
rem AEROLAKE_WSL_DISTRO (defaults to Ubuntu).
rem ============================================================================

if "%AEROLAKE_WSL_DISTRO%"=="" set "AEROLAKE_WSL_DISTRO=Ubuntu"

rem Convert this script's folder to a WSL path. Forward-slash the path first:
rem backslashes are eaten crossing into WSL (see launch-gui.bat for the detail).
set "WINDIR_SELF=%~dp0"
set "WINDIR_SELF=%WINDIR_SELF:\=/%"
for /f "usebackq delims=" %%p in (`wsl -d %AEROLAKE_WSL_DISTRO% wslpath "%WINDIR_SELF%" 2^>nul`) do set "WSLDIR=%%p"

if "%WSLDIR%"=="" (
  echo [error] Could not translate this folder to a WSL path.
  echo         Is WSL distro "%AEROLAKE_WSL_DISTRO%" installed and running?
  pause
  exit /b 1
)

wsl -d %AEROLAKE_WSL_DISTRO% -- bash -lc "'%WSLDIR%/stop-gui.sh'"
