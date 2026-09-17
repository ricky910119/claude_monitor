Option Explicit

Dim wmi, processes, process, commandLine
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set processes = wmi.ExecQuery( _
    "SELECT ProcessId, CommandLine FROM Win32_Process " & _
    "WHERE Name='pythonw.exe'")

For Each process In processes
    If Not IsNull(process.CommandLine) Then
        commandLine = LCase(CStr(process.CommandLine))

        If InStr(commandLine, "\claude_monitor\claude_monitor.py") > 0 Then
            process.Terminate
        End If
    End If
Next