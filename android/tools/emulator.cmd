@echo off
rem Start the test phone. Creates it on first run. Leave this window open.
call "%~dp0_env.cmd"

"%EMULATOR%" -list-avds | findstr /x "sms_test" >nul
if errorlevel 1 (
  echo Creating the test device...
  echo no | call "%AVDMANAGER%" create avd -n sms_test -k "system-images;android-34;google_apis;x86_64" -d pixel_5 --force
)

echo Starting the emulator. First boot takes a minute or so.
echo Leave this window open; close it to shut the phone down.
"%EMULATOR%" -avd sms_test -no-boot-anim -gpu swiftshader_indirect -no-audio
