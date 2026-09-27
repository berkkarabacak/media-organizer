#!/usr/bin/env bash
# This Git Bash environment is missing standard Windows variables; dotnet/MSBuild
# crash with "Value cannot be null (Parameter 'path1')" without them.
export SystemRoot="${SystemRoot:-C:\\Windows}"
export windir="${windir:-C:\\Windows}"
export ProgramData="${ProgramData:-C:\\ProgramData}"
export ProgramFiles="${ProgramFiles:-C:\\Program Files}"
export "ProgramFiles(x86)"="${ProgramFiles(x86):-C:\\Program Files (x86)}"
export CommonProgramFiles="${CommonProgramFiles:-C:\\Program Files\\Common Files}"
export 'CommonProgramFiles(x86)'="${CommonProgramFiles(x86):-C:\\Program Files (x86)\\Common Files}"
export PUBLIC="${PUBLIC:-C:\\Users\\Public}"
export ALLUSERSPROFILE="${ALLUSERSPROFILE:-C:\\ProgramData}"
export USERPROFILE="${USERPROFILE:-C:\\Users\\OdinLocal}"
export APPDATA="${APPDATA:-C:\\Users\\OdinLocal\\AppData\\Roaming}"
export LOCALAPPDATA="${LOCALAPPDATA:-C:\\Users\\OdinLocal\\AppData\\Local}"
export TEMP="${TEMP:-C:\\Users\\OdinLocal\\AppData\\Local\\Temp}"
export TMP="$TEMP"
exec dotnet "$@"
