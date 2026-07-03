@echo off
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
rem Logs (if something goes wrong): \\wsl.localhost\ubuntu\tmp\aerolake-gui.log
rem ============================================================================

rem --- 1. start the GUI in WSL if it is not already running ------------------
rem The trailing "sleep 1" matters: without it wsl.exe exits so fast that WSL
rem tears the session down before the detached child is fully spawned.
wsl -d ubuntu -- bash -lc "cd ~/code/lassena/aerolake && (ss -ltn 2>/dev/null | grep -q ':8501 ') || { nohup uv run --extra gui streamlit run src/aerolake/gui/app.py --server.headless true --server.address 0.0.0.0 --server.port 8501 >/tmp/aerolake-gui.log 2>&1 & disown; sleep 1; }"

rem --- 2. wait for the health endpoint (60 x 1 s) -----------------------------
set /a tries=0
:wait
curl -s -o NUL --max-time 2 http://localhost:8501/_stcore/health && goto open
set /a tries+=1
if %tries% geq 60 goto open
timeout /t 1 /nobreak >nul
goto wait

rem --- 3. open the browser ----------------------------------------------------
:open
start "" http://localhost:8501
