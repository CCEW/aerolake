@echo off
setlocal enabledelayedexpansion
rem ============================================================================
rem AeroLake GUI launcher (Windows) - start the Streamlit GUI without a terminal
rem ----------------------------------------------------------------------------
rem Double-click me (or better: double-click launch-gui.vbs, which runs me with
rem the console window hidden). What happens:
rem   1. Inside WSL: if nothing listens on :8501 yet, start the GUI detached
rem      (nohup + disown -> it survives this window closing).
rem   2. Wait until the GUI answers its health endpoint (up to ~60 s: the first
rem      run may resolve dependencies).
rem   3. Open the browser on http://localhost:8501.
rem The GUI keeps running in the background afterwards; colleagues on the
rem network can reach it via http://<this-pc>:8501 (it binds 0.0.0.0).
rem
rem NO hardcoded paths: the project directory is derived from THIS script's own
rem location (%~dp0) and translated to a WSL path with `wslpath`, so it works
rem wherever the repo is cloned. The WSL distro defaults to "Ubuntu"; override it
rem by setting AEROLAKE_WSL_DISTRO before launching if your distro is named
rem differently (list yours with: wsl -l -q).
rem
rem Logs (if something goes wrong): \\wsl.localhost\Ubuntu\tmp\aerolake-gui.log
rem ============================================================================

rem --- 0. resolve the WSL distro and this project's WSL path ------------------
if "%AEROLAKE_WSL_DISTRO%"=="" set "AEROLAKE_WSL_DISTRO=Ubuntu"

rem Translate the folder THIS script lives in (%~dp0) to a WSL path. %~dp0 ends
rem with a backslash. CRITICAL: backslashes are eaten as escape characters when
rem a string crosses into WSL, so "C:\Users\..." would reach wslpath mangled as
rem "C:Users...". We convert every backslash to a forward slash first
rem (%VAR:\=/%) — forward-slash Windows paths survive the crossing intact and
rem wslpath translates them to /mnt/c/... correctly.
set "WINDIR_SELF=%~dp0"
set "WINDIR_SELF=%WINDIR_SELF:\=/%"
for /f "usebackq delims=" %%p in (`wsl -d %AEROLAKE_WSL_DISTRO% wslpath "%WINDIR_SELF%" 2^>nul`) do set "WSLDIR=%%p"

if "%WSLDIR%"=="" (
  echo [error] Could not translate this folder to a WSL path.
  echo         Is WSL distro "%AEROLAKE_WSL_DISTRO%" installed and running?
  echo         List your distros with:  wsl -l -q
  echo         If yours has a different name, set AEROLAKE_WSL_DISTRO and retry.
  pause
  exit /b 1
)

rem --- 1. start the GUI in WSL if it is not already running -------------------
rem The WSL-side logic lives in launch-gui.sh (a real script), NOT inline here:
rem cramming a multi-line bash command into `bash -lc "..."` across the
rem cmd->wsl->bash boundary mangles quotes and backslashes. We just invoke the
rem script by its WSL path. `bash -lc` gives it a login shell (PATH, conda init).
wsl -d %AEROLAKE_WSL_DISTRO% -- bash -lc "'%WSLDIR%/launch-gui.sh'"

if errorlevel 1 (
  echo [error] Failed to start the GUI in WSL. Recent log:
  wsl -d %AEROLAKE_WSL_DISTRO% -- bash -lc "tail -n 20 /tmp/aerolake-gui.log 2>/dev/null"
  pause
  exit /b 1
)

rem --- 2. wait for the health endpoint (60 x 1 s) -----------------------------
set /a tries=0
:wait
curl -s -o NUL --max-time 2 http://localhost:8501/_stcore/health && goto open
set /a tries+=1
if %tries% geq 60 goto timeout
timeout /t 1 /nobreak >nul
goto wait

rem --- health never came up: show the log so the failure is not silent --------
:timeout
echo [error] GUI did not answer on http://localhost:8501 within 60 s. Recent log:
wsl -d %AEROLAKE_WSL_DISTRO% -- bash -lc "tail -n 30 /tmp/aerolake-gui.log 2>/dev/null"
pause
exit /b 1

rem --- 3. open the browser ----------------------------------------------------
:open
start "" http://localhost:8501
