' Deep Research - one-click launcher (no console window).
' Ensures the Deep Research server (dr_server.py on :5006) is running, then exits.
' The Special Projects hub is the front door; it handles opening the tool. This just
' makes sure the server is up (the hub's "launch" runs this on demand).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
pyw = "C:\Users\crouchingyeti\AppData\Local\Python\bin\pythonw.exe"
sh.Run """" & pyw & """ """ & base & "\launch.py""", 0, False
