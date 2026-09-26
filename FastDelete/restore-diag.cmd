@echo off
set ROOT=C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer\FastDelete
set ProgramData=C:\ProgramData
set NUGET_PACKAGES=%ROOT%\.nuget\packages
"C:\Program Files\dotnet\dotnet.exe" restore %ROOT%\src\FastDelete.Core\FastDelete.Core.csproj -p:RestorePackagesPath="%NUGET_PACKAGES%" -v diag > %ROOT%\restore-diag.log 2>&1
echo EXIT=%ERRORLEVEL%
