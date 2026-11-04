@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0weather-desk.bat" --diagnose
exit /b %errorlevel%
