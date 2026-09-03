' Central Industrial — LOCAL hub, one-click launcher (no console window).
' Double-click to open the local suite: starts the hub (which starts and supervises
' every local tool behind http://127.0.0.1:5050) only if it isn't already running,
' then opens the C64 landing page. Pass --startup (the Startup-folder copy does) to
' start quietly with no browser tab.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
pyw = "C:\Users\crouchingyeti\AppData\Local\Python\bin\pythonw.exe"
arg = ""
If WScript.Arguments.Count > 0 Then arg = " """ & WScript.Arguments(0) & """"
sh.Run """" & pyw & """ """ & base & "\launch_local.py""" & arg, 0, False
