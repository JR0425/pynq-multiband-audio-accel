# SEND commands to the PYNQ-Z2 serial console and print what comes back.
# Windows PowerShell, .NET SerialPort.
#
# Companion to pynq_serial_console.ps1 (which only reads).
# Useful when the board has no network yet, or when Jupyter will not open.
#
# Commands come from a text file, one per line (blank lines and lines starting
# with '#' are skipped). A file avoids PowerShell argument-parsing trouble with
# pipes, semicolons and quotes -- and lets you keep a command you ran.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File pynq_serial_send.ps1 -CommandFile cmds.txt
#   powershell -NoProfile -ExecutionPolicy Bypass -File pynq_serial_send.ps1 -CommandFile cmds.txt -WaitPerCmd 30000
#
# Remember the board needs root for anything that touches MMIO (PYNQ overlays,
# GPIO, DMA). Jupyter runs as root, a plain serial shell does not:
#   echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 script.py

param(
    [string]$CommandFile,
    [string]$PortName = "COM6",
    [int]$Baud = 115200,
    [int]$WaitPerCmd = 3000,
    [int]$Settle = 900
)

$port = New-Object System.IO.Ports.SerialPort
$port.PortName = $PortName
$port.BaudRate = $Baud
$port.Parity = [System.IO.Ports.Parity]::None
$port.DataBits = 8
$port.StopBits = [System.IO.Ports.StopBits]::One
$port.ReadTimeout = 500

# Three encoding defaults to override. All three are on the PC side; the board
# itself is fine (LANG=en_US.UTF-8, Python stdout is utf-8). Measured 2026-09-27:
#   1. SerialPort.Encoding = ASCII        -> non-ASCII becomes '?' both ways
#   2. [Console]::OutputEncoding = OEM    -> text re-encoded again on the way out
#   3. Get-Content defaults to ANSI       -> a UTF-8 command file reads as mojibake
# 1 is the one that bites. 3 is invisible while every command is pure ASCII.
$utf8 = New-Object System.Text.UTF8Encoding $false
$port.Encoding = $utf8
[Console]::OutputEncoding = $utf8

if (-not (Test-Path $CommandFile)) {
    Write-Host "Command file not found: $CommandFile"
    exit 1
}
$lines = Get-Content -LiteralPath $CommandFile -Encoding UTF8 |
    Where-Object { $_.Trim() -ne "" -and -not $_.Trim().StartsWith("#") }

# NOTE: DtrEnable / RtsEnable are deliberately NOT set (see pynq_serial_console.ps1).

try {
    $port.Open()
} catch {
    Write-Host "FAILED to open $PortName : $($_.Exception.Message)"
    exit 1
}

# A bare CR wakes the getty; throw away whatever was already buffered.
$port.Write("`r")
Start-Sleep -Milliseconds $Settle
$null = $port.ReadExisting()

foreach ($cmd in $lines) {
    Write-Host "===== $cmd"
    $port.Write($cmd + "`r")
    $deadline = (Get-Date).AddMilliseconds($WaitPerCmd)
    while ((Get-Date) -lt $deadline) {
        try {
            $chunk = $port.ReadExisting()
            if ($chunk.Length -gt 0) { Write-Host -NoNewline $chunk }
        } catch { }
        Start-Sleep -Milliseconds 120
    }
    Write-Host ""
}

$port.Close()
