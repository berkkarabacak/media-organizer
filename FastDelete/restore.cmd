@echo off
set ROOT=C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer\FastDelete
set ProgramData=C:\ProgramData
if not exist "C:\ProgramData\NuGet" mkdir "C:\ProgramData\NuGet"
set NUGET_PACKAGES=%ROOT%\.nuget\packages
"C:\Program Files\dotnet\dotnet.exe" restore %ROOT%\FastDelete.sln -p:RestorePackagesPath="%NUGET_PACKAGES%" -v m
echo EXIT=%ERRORLEVEL%
