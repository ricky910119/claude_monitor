Option Explicit

Const BASE_DIR = "C:\Users\ricky9101\Test Code\claude_monitor"
Const PYTHONW_PATH = "C:\Users\ricky9101\Test Code\.venv_claude_monitor\Scripts\pythonw.exe"
Const MONITOR_SCRIPT = "C:\Users\ricky9101\Test Code\claude_monitor\claude_monitor.py"
Const MONITOR_MARKER = "\claude_monitor\claude_monitor.py"
Const SCRIPT_MARKER = "\claude_monitor\claude_monitor.vbs"
Const STOP_FLAG = "C:\Users\ricky9101\Test Code\claude_monitor\.claude_monitor.stop"
Const CHECK_INTERVAL_MS = 30000

Dim shell, fso, wmi, mode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
shell.CurrentDirectory = BASE_DIR

mode = "daily"
If WScript.Arguments.Count > 0 Then
    mode = LCase(Trim(CStr(WScript.Arguments(0))))
End If

Select Case mode
    Case "daily"
        RunDaily
    Case "start"
        DeleteStopFlag
        LaunchMonitor
    Case "stop"
        WriteStopFlag
        StopMonitor
    Case Else
        MsgBox "Usage: claude_monitor.vbs [daily|start|stop]", vbExclamation, "Claude Monitor"
        WScript.Quit 2
End Select

Sub RunDaily()
    If SupervisorInstanceCount() > 1 Then
        WScript.Quit 0
    End If

    DeleteStopFlag
    Do
        If fso.FileExists(STOP_FLAG) Then
            StopMonitor
            WScript.Quit 0
        End If

        If Hour(Now) >= 7 And Hour(Now) < 19 Then
            If Not MonitorIsRunning() Then
                LaunchMonitor
            End If
        Else
            StopMonitor
        End If

        WScript.Sleep CHECK_INTERVAL_MS
    Loop
End Sub

Sub LaunchMonitor()
    If MonitorIsRunning() Then Exit Sub
    shell.Run """" & PYTHONW_PATH & """ """ & MONITOR_SCRIPT & """", 0, False
End Sub

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

Function SupervisorInstanceCount()
    Dim processes, process, commandLine, count
    count = 0
    Set processes = wmi.ExecQuery( _
        "SELECT CommandLine FROM Win32_Process " & _
        "WHERE Name='wscript.exe' OR Name='cscript.exe'")

    For Each process In processes
        If Not IsNull(process.CommandLine) Then
            commandLine = LCase(CStr(process.CommandLine))
            If InStr(commandLine, SCRIPT_MARKER) > 0 Then
                count = count + 1
            End If
        End If
    Next
    SupervisorInstanceCount = count
End Function

Sub WriteStopFlag()
    Dim flag
    Set flag = fso.CreateTextFile(STOP_FLAG, True)
    flag.WriteLine CStr(Now)
    flag.Close
End Sub

Sub DeleteStopFlag()
    If fso.FileExists(STOP_FLAG) Then
        fso.DeleteFile STOP_FLAG, True
    End If
End Sub
