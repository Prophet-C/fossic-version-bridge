# Start watch.py silently at every logon, by placing a shortcut in the
# current user's Startup folder. Run with -Uninstall to remove it.
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 [-ModsDir <path>] [-Uninstall]
param(
    [string]$ModsDir = (Join-Path $PSScriptRoot "..\mods"),
    [switch]$Uninstall
)

$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "fossic-version-bridge.lnk"

if ($Uninstall) {
    if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link }
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*watch.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId }
    Write-Output "Autostart removed and watcher stopped."
    return
}

$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$ModsDir = (Resolve-Path -LiteralPath $ModsDir).Path
$watch = Join-Path $PSScriptRoot "watch.py"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $pythonw
$sc.Arguments = "`"$watch`" --mods `"$ModsDir`""
$sc.WorkingDirectory = $PSScriptRoot
$sc.Description = "fossic-version-bridge: keep fossic mods linked for TriOS"
$sc.Save()

# Start it now too, so no logoff is needed.
Start-Process -FilePath $pythonw -ArgumentList "`"$watch`" --mods `"$ModsDir`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Output "Autostart installed: $link"
Write-Output "Watcher started; log: $(Join-Path $PSScriptRoot 'watch.log')"
