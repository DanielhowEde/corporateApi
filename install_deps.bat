@echo off
setlocal

set REPO_DIR=%~dp0

echo ========================================
echo  DMZ API - Installing Dependencies
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

echo Installing Corporate API dependencies...
python -m pip install -r "%REPO_DIR%corporate\requirements.txt" python-multipart
if errorlevel 1 goto :error

echo.
echo Installing Low-Side API dependencies...
python -m pip install -r "%REPO_DIR%low_side\requirements.txt"
if errorlevel 1 goto :error

echo.
echo Installing Mock Gateway dependencies...
python -m pip install fastapi uvicorn httpx
if errorlevel 1 goto :error

echo.
echo ========================================
echo  All dependencies installed successfully
echo ========================================
echo.
echo  Run start_services.bat to start the services.
echo.
pause
exit /b 0

:error
echo.
echo ERROR: Installation failed. Check the output above.
pause
exit /b 1
