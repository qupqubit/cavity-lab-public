@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
if not exist "scripts\bootstrap.py" goto incomplete
if not exist "cavity_fem\api.py" goto incomplete
if not exist "requirements.txt" goto incomplete
if defined CAVITY_FEM_PYTHON goto override_python
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
set "cavity_python="
for /f "delims=" %%P in ('where python3 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined cavity_python set "cavity_python=%%P"
for /f "delims=" %%P in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined cavity_python set "cavity_python=%%P"
if defined cavity_python goto found_python
goto missing_python
:override_python
"%CAVITY_FEM_PYTHON%" -X utf8 "scripts\bootstrap.py" %*
goto finished
:python_launcher
py -3 -X utf8 "scripts\bootstrap.py" %*
goto finished
:found_python
"%cavity_python%" -X utf8 "scripts\bootstrap.py" %*
goto finished
:finished
set "cavity_exit=%errorlevel%"
if "%cavity_exit%"=="0" exit /b 0
echo.
echo Startup failed. See the explanation above.
pause
exit /b %cavity_exit%
:missing_python
echo Install Python 3.9-3.12 from https://www.python.org/downloads/windows/ then open this file again.
echo Python 3.12 is recommended. Include the Python launcher during installation.
pause
exit /b 1
:incomplete
echo Extract the WHOLE source ZIP first, then open launch.bat inside the cavity-lab-public folder.
pause
exit /b 1
