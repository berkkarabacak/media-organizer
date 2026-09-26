@echo off
set ProgramData=C:\ProgramData
echo ProgramData=[%ProgramData%]
"C:\Program Files\dotnet\dotnet.exe" msbuild -version
set | findstr /I "programdata"
