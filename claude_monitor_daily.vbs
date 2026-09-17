Option Explicit

Const MONITOR_LAUNCHER = "C:\Users\ricky9101\Test Code\claude_monitor\claude_monitor.vbs"
Const MONITOR_MARKER = "\claude_monitor\claude_monitor.py"
Const CHECK_INTERVAL_MS = 30000

Dim shell, wmi
Set shell = CreateObject("WScript.Shell")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

Do
    If Hour(Now) >= 7 And Hour(Now) < 19 Then
        If Not MonitorIsRunning() Then
            shell.Run """" & MONITOR_LAUNCHER & """", 0, False
        End If
    Else
        StopMonitor
    End If

    WScript.Sleep CHECK_INTERVAL_MS
Loop

Function MonitorIsRunning()
    Dim processes, process, commandLine
    MonitorIsRunning = False
    Set processes = wmi.ExecQuery( _
        "SELECT CommandLine FROM Win32_Process WHERE Name='pythonw.exe'")

    For Each process In processes
        If Not IsNull(process.CommandLine) Then
            commandLine = LCase(CStr(process.CommandLine))
            If InStr(commandLine, MONITOR_MARKER) > 0 Then
                MonitorIsRunning = True
                Exit Function
            End If
        End If
    Next
End Function

Sub StopMonitor()
    Dim processes, process, commandLine
    Set processes = wmi.ExecQuery( _
        "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='pythonw.exe'")

    For Each process In processes
        If Not IsNull(process.CommandLine) Then
            commandLine = LCase(CStr(process.CommandLine))
            If InStr(commandLine, MONITOR_MARKER) > 0 Then
                process.Terminate
            End If
        End If
    Next
End Sub
