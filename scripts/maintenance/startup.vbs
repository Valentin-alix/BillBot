Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")
maintenance = filesystem.GetParentFolderName(WScript.ScriptFullName)
root = filesystem.GetParentFolderName(filesystem.GetParentFolderName(maintenance))

shell.CurrentDirectory = root
shell.Run "uv run python -m scripts.maintenance.watch_maj", 0, False
