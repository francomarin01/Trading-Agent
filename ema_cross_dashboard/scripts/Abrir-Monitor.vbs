Option Explicit

' Double-click launcher: starts the local server hidden and opens the dashboard.
Dim shell, fso, scriptsFolder, root, python, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptsFolder = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptsFolder)
python = "C:\Python314\python.exe"
If Not fso.FileExists(python) Then python = "python"
shell.CurrentDirectory = root
command = """" & python & """ -c ""from app.server import serve; serve(open_browser=False)"""
shell.Run command, 0, False
WScript.Sleep 900
shell.Run "http://127.0.0.1:8765/", 1, False
