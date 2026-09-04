@echo off
rem Follow just this app's log. Ctrl+C to stop.
call "%~dp0_env.cmd"
for /f %%p in ('"%ADB%" shell pidof -s org.sheetmusicshelf.app') do set APPPID=%%p
if "%APPPID%"=="" (
  echo The app is not running. Start it on the device first, or run install.cmd.
  exit /b 1
)
"%ADB%" logcat --pid=%APPPID%
