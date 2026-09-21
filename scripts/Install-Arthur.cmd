@echo off
setlocal
cd /d "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-arthur.ps1"
set "ArthurExitCode=%ERRORLEVEL%"
if not "%ArthurExitCode%"=="0" echo Arthur setup failed. Review the error above before trying again.
pause
exit /b %ArthurExitCode%
