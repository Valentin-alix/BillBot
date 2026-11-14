#!/usr/bin/env bash
# run with Git Bash on Windows; requires .NET 10.
set -euo pipefail

case "$(uname -s)" in
    MINGW*|MSYS*) ;;
    *) echo "This script requires Git Bash on Windows." >&2; exit 1 ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
command -v dotnet >/dev/null
dotnet build DBDofusUnity/Il2CppInspectorRedux/Il2CppInspector.CLI/Il2CppInspector.CLI.csproj \
    --configuration Debug -p:UseSharedCompilation=false --verbosity minimal
dotnet build DBDofusUnity/protodec/src/protodec/protodec.csproj \
    --configuration Debug -p:UseSharedCompilation=false --verbosity minimal

for executable in \
    DBDofusUnity/Il2CppInspectorRedux/Il2CppInspector.CLI/bin/Debug/net10.0/win-x64/Il2CppInspector.exe \
    DBDofusUnity/protodec/bin/protodec/Debug/net10.0/protodec.exe; do
    [[ -s "$executable" ]] || { echo "Missing build output: $executable" >&2; exit 1; }
done
echo 'Il2CppInspector and protodec built successfully.'
