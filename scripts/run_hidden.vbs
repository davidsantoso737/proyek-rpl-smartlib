Option Explicit
Dim shell, fso, batchPath, command, rc
If WScript.Arguments.Count < 1 Then WScript.Quit 2
batchPath = WScript.Arguments(0)
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(batchPath) Then WScript.Quit 2
command = "cmd.exe /d /c """ & batchPath & """"
Set shell = CreateObject("WScript.Shell")
rc = shell.Run(command, 0, True)
WScript.Quit rc
