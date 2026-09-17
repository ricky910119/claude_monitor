Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Users\ricky9101\Test Code\claude_monitor"
shell.Run """C:\Users\ricky9101\Test Code\.venv_claude_monitor\Scripts\pythonw.exe"" ""C:\Users\ricky9101\Test Code\claude_monitor\claude_monitor.py""", 0, False
