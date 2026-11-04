@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0weather-desk.bat" --installed-python --open
exit /b %errorlevel%
