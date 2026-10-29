' ==============================================================================
' AlphaVision Silent Background Runner
' Executes the process supervisor completely headless (0 visible console windows)
' ==============================================================================

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
strPS1 = strScriptDir & "\supervisor.ps1"

' WindowStyle 0 = Hidden, bWaitOnReturn = False (asynchronous)
strCommand = "powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File """ & strPS1 & """"

WshShell.Run strCommand, 0, False
Set WshShell = Nothing
Set FSO = Nothing
