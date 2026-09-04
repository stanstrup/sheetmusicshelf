@echo off
rem Build the debug APK.
call "%~dp0_env.cmd"
pushd "%~dp0.."
call "%GRADLE%" assembleDebug --console=plain
set RESULT=%ERRORLEVEL%
popd
if %RESULT% NEQ 0 ( echo. & echo Build failed. & exit /b %RESULT% )
echo.
echo APK: %~dp0..\app\build\outputs\apk\debug\app-debug.apk
