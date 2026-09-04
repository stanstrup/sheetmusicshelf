@echo off
rem What is attached right now.
call "%~dp0_env.cmd"
"%ADB%" devices -l
