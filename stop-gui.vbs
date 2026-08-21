' AeroLake GUI - silent stopper.
' Runs stop-gui.bat with the console window HIDDEN (0), so stopping the GUI
' shows nothing. Double-click me when you no longer need the GUI.
Dim fso, shell, batPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Wscript.Shell")
' The .bat sits next to this .vbs, wherever the pair lives.
batPath = fso.GetParentFolderName(WScript.ScriptFullName) & "\stop-gui.bat"
shell.Run """" & batPath & """", 0, False
