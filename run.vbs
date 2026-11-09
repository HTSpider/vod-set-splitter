' Opens VOD Set Splitter without a console window.
Set shell = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & here & "\.venv\Scripts\pythonw.exe"" """ & here & "\launch.pyw""", 0, False
