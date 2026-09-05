@echo off
rem Build the app and hand it to the server, so a tablet can install it from
rem the same address it already reads music from.
rem
rem The version comes from the commit count, so every build is newer than the
rem last without anybody remembering to bump anything -- and Android refuses to
rem install an APK that is not newer than the one already installed.
setlocal
call "%~dp0_env.cmd"

call "%~dp0build.cmd"
if errorlevel 1 exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" ^
  -Apk "%~dp0..\app\build\outputs\apk\debug\app-debug.apk" ^
  -Aapt "%ANDROID_HOME%\build-tools\34.0.0\aapt.exe"
endlocal
