@echo off
setlocal enabledelayedexpansion

:: Set defaults
set AEROLAKE_IQENGINE_URL=http://localhost:3000
set AEROLAKE_IQENGINE_ACCOUNT=aerolake
set AEROLAKE_IQENGINE_CONTAINER=aerolake-captures

:: Override with .env if it exists
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (.env) do (
        if not "%%A"=="" set "%%A=%%~B"
    )
)

set BASE_URL=%AEROLAKE_IQENGINE_URL%/api/v1/integration
set ACCOUNT=%AEROLAKE_IQENGINE_ACCOUNT%
set CONTAINER=%AEROLAKE_IQENGINE_CONTAINER%

echo Triggering initial catalog sync for %ACCOUNT%/%CONTAINER%...

for /f "tokens=*" %%i in ('curl -s -X POST "%BASE_URL%/datasources/%ACCOUNT%/%CONTAINER%/sync" ^| python -c "import sys, json; print(json.load(sys.stdin).get('job_id', ''))"') do set JOB_ID=%%i

if "%JOB_ID%"=="" (
    echo Failed to start sync.
    exit /b 1
)

echo Job %JOB_ID% queued. Waiting for indexing to finish...

:poll
for /f "tokens=*" %%i in ('curl -s "%BASE_URL%/sync/%JOB_ID%" ^| python -c "import sys, json; print(json.load(sys.stdin).get('status', ''))"') do set STATUS=%%i

if "%STATUS%"=="completed" (
    echo.
    echo Sync completed successfully!
    echo.
    uv run aerolake-list --catalog iqengine --signal-type iridium
    exit /b 0
)
if "%STATUS%"=="failed" (
    echo.
    echo Sync failed.
    exit /b 1
)

<nul set /p="."
timeout /t 2 /nobreak >nul
goto :poll