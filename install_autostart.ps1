# Set up how watch.py runs. Pick one mode; switching removes the other.
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1                          # at logon, always on
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -WithTriOS <TriOS.exe>   # desktop shortcut that starts TriOS
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall               # remove both
param(
    [string]$ModsDir = (Join-Path $PSScriptRoot "..\mods"),
    [string]$WithTriOS,
    [switch]$Uninstall
)

$logonLink = Join-Path ([Environment]::GetFolderPath("Startup")) "fossic-version-bridge.lnk"
$triosLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "TriOS (fossic).lnk"
$reportLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "fossic 更新列表.lnk"

function Stop-Watcher {
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*watch.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId }
}

# Any mode change starts from a clean slate.
foreach ($l in $logonLink, $triosLink, $reportLink) {
    if (Test-Path -LiteralPath $l) { Remove-Item -LiteralPath $l }
}
Stop-Watcher
if ($Uninstall) {
    Write-Output "Removed autostart, TriOS and update-list shortcuts; watcher stopped."
    return
}

$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$ModsDir = (Resolve-Path -LiteralPath $ModsDir).Path
$watch = Join-Path $PSScriptRoot "watch.py"
$shell = New-Object -ComObject WScript.Shell

# The update list (updates.html), refreshed by the watcher and inject.py.
$sc = $shell.CreateShortcut($reportLink)
$sc.TargetPath = Join-Path $PSScriptRoot "updates.html"
$sc.Description = "fossic mod update list"
$sc.Save()
Write-Output "Update list shortcut: $reportLink"

if ($WithTriOS) {
    $trios = (Resolve-Path -LiteralPath $WithTriOS).Path
    $sc = $shell.CreateShortcut($triosLink)
    $sc.TargetPath = $pythonw
    $sc.Arguments = "`"$watch`" --mods `"$ModsDir`" --launch `"$trios`""
    $sc.WorkingDirectory = $PSScriptRoot
    $sc.IconLocation = "$trios,0"
    $sc.Description = "Start TriOS with fossic mods linked (fossic-version-bridge)"
    $sc.Save()
    Write-Output "Desktop shortcut created: $triosLink"
    Write-Output "Start TriOS from it; the watcher runs while TriOS is open."
    return
}

$sc = $shell.CreateShortcut($logonLink)
$sc.TargetPath = $pythonw
$sc.Arguments = "`"$watch`" --mods `"$ModsDir`""
$sc.WorkingDirectory = $PSScriptRoot
$sc.Description = "fossic-version-bridge: keep fossic mods linked for TriOS"
$sc.Save()

# Start it now too, so no logoff is needed.
Start-Process -FilePath $pythonw -ArgumentList "`"$watch`" --mods `"$ModsDir`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Output "Autostart installed: $logonLink"
Write-Output "Watcher started; log: $(Join-Path $PSScriptRoot 'watch.log')"
