@echo off
"C:\Program Files\dotnet\dotnet.exe" nuget locals all --list
echo EXIT=%ERRORLEVEL%
