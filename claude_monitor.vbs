Option Explicit

Const CHECK_INTERVAL_MS = 30000

Dim shell, fso, wmi, mode
Dim baseDir, pythonwPath, monitorScript, monitorMarker, scriptMarker, stopFlag
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = fso.BuildPath(fso.GetParentFolderName(baseDir), _
    ".venv_claude_monitor\Scripts\pythonw.exe")
monitorScript = fso.BuildPath(baseDir, "claude_monitor.py")
monitorMarker = LCase(monitorScript)
scriptMarker = LCase(WScript.ScriptFullName)
stopFlag = fso.BuildPath(baseDir, ".claude_monitor.stop")
shell.CurrentDirectory = baseDir

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
        If fso.FileExists(stopFlag) Then
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
    shell.Run """" & pythonwPath & """ """ & monitorScript & """", 0, False
End Sub

Function MonitorIsRunning()
    Dim processes, process, commandLine
    MonitorIsRunning = False
    Set processes = wmi.ExecQuery( _
        "SELECT CommandLine FROM Win32_Process WHERE Name='pythonw.exe'")

    For Each process In processes
        If Not IsNull(process.CommandLine) Then
            commandLine = LCase(CStr(process.CommandLine))
            If InStr(commandLine, monitorMarker) > 0 Then
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
            If InStr(commandLine, monitorMarker) > 0 Then
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
            If InStr(commandLine, scriptMarker) > 0 Then
                count = count + 1
            End If
        End If
    Next
    SupervisorInstanceCount = count
End Function

Sub WriteStopFlag()
    Dim flag
    Set flag = fso.CreateTextFile(stopFlag, True)
    flag.WriteLine CStr(Now)
    flag.Close
End Sub

Sub DeleteStopFlag()
    If fso.FileExists(stopFlag) Then
        fso.DeleteFile stopFlag, True
    End If
End Sub
