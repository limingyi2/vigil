Set ws = CreateObject("WScript.Shell")
' 0 = hidden window. Launches the autostart bat with NO visible console at all.
ws.Run "cmd /c ""F:\Vigil\pet\launchers\autostart_zhixia.bat""", 0, False
