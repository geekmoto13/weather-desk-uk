@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0weather-desk.bat" --open
exit /b %errorlevel%
