@echo off
rem Build and install onto whatever is attached: the emulator, or a phone
rem plugged in with USB debugging turned on.
call "%~dp0_env.cmd"

call "%~dp0build.cmd"
if errorlevel 1 exit /b 1

echo.
echo Waiting for a device...
"%ADB%" wait-for-device
"%ADB%" install -r "%~dp0..\app\build\outputs\apk\debug\app-debug.apk"
if errorlevel 1 ( echo. & echo Install failed. Is a device attached? Run: "%ADB%" devices & exit /b 1 )

"%ADB%" shell am start -n org.sheetmusicshelf.app/.BrowseActivity
echo.
echo Installed and launched.
