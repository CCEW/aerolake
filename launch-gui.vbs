' AeroLake GUI - silent launcher.
' Runs launch-gui.bat with the console window HIDDEN (0), so starting the GUI
' shows nothing but the browser. Double-click me, or put a shortcut to me in
' shell:startup to auto-start the GUI when the PC boots.
Dim fso, shell, batPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Wscript.Shell")
' The .bat sits next to this .vbs, wherever the pair lives.
batPath = fso.GetParentFolderName(WScript.ScriptFullName) & "\launch-gui.bat"
shell.Run """" & batPath & """", 0, False
