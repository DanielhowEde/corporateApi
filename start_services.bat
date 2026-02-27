@echo off
setlocal

set REPO_DIR=%~dp0

echo ========================================
echo  DMZ API - Starting Test Services
echo ========================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

REM Check uvicorn is available
python -m uvicorn --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: uvicorn not found. Run install_deps.bat first.
    pause
    exit /b 1
)

echo Starting Mock Gateway on port 8000...
start "Mock Gateway :8000" cmd /k "cd /d "%REPO_DIR%mock_gateway" && python -m uvicorn main:app --reload --port 8000"

timeout /t 1 /nobreak >nul

echo Starting Corporate API on port 8001...
start "Corporate API :8001" cmd /k "cd /d "%REPO_DIR%corporate" && python -m uvicorn app.main:app --reload --port 8001"

timeout /t 1 /nobreak >nul

echo Starting Low-Side API on port 8002...
start "Low-Side API :8002" cmd /k "cd /d "%REPO_DIR%low_side" && python -m uvicorn app.main:app --reload --port 8002"

echo.
echo ========================================
echo  All services starting...
echo ========================================
echo.
echo  Mock Gateway:     http://localhost:8000
echo  Corporate Admin:  http://localhost:8001/admin/
echo  Corporate User:   http://localhost:8001/user/
echo  Corporate Docs:   http://localhost:8001/docs
echo  Low-Side User:    http://localhost:8002/user/
echo  Low-Side Docs:    http://localhost:8002/docs
echo.
echo  Default admin login: admin / admin123
echo.
echo  Close the individual service windows to stop each service.
echo ========================================
echo.
pause
