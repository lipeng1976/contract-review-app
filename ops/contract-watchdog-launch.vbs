' Contract Review App - hidden supervisor launcher.
' Called by contract-watchdog.cmd (manual run) and copied into the Startup
' folder (logon run). WScript.Shell.Run with window style 0 creates NO console
' window at all - unlike `start /min`, which leaves a permanent black box on
' the taskbar and reappears every time the supervisor restarts.
Option Explicit
Dim shell, root, node, script, commandLine
Set shell = CreateObject("WScript.Shell")
root = shell.ExpandEnvironmentStrings("%CONTRACT_ROOT%")
If root = "%CONTRACT_ROOT%" Or root = "" Then
  root = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
  root = Left(root, InStrRev(root, "\") - 1)
End If
node = shell.ExpandEnvironmentStrings("%CONTRACT_NODE%")
If node = "%CONTRACT_NODE%" Or node = "" Then node = "node.exe"
script = root & "\ops\watchdog-supervisor.mjs"
commandLine = """" & node & """ """ & script & """"
shell.Run commandLine, 0, False
