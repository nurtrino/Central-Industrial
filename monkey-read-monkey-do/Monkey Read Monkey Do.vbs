' Monkey Read Monkey Do — one-click launcher (no console window).
' Double-click this to open Monkey Read Monkey Do. Starts the server only if it isn't already
' running, then opens the page in your browser.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
pyw = base & "\.venv\Scripts\pythonw.exe"
sh.Run """" & pyw & """ """ & base & "\launch.py""", 0, False
