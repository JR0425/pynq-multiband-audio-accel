# Read the PYNQ-Z2 serial console with no extra software.
# Windows PowerShell, .NET SerialPort. ASCII only on purpose.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File pynq_serial_console.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File pynq_serial_console.ps1 -PortName COM4 -Seconds 20

param(
    [string]$PortName = "COM6",
    [int]$Baud = 115200,
    [int]$Seconds = 12,
    [switch]$NoPause
)

Write-Host "Opening $PortName at $Baud 8N1 ..."

$port = New-Object System.IO.Ports.SerialPort
$port.PortName = $PortName
$port.BaudRate = $Baud
$port.Parity = [System.IO.Ports.Parity]::None
$port.DataBits = 8
$port.StopBits = [System.IO.Ports.StopBits]::One
$port.ReadTimeout = 500

# NOTE: DtrEnable / RtsEnable are deliberately NOT set.
# Tested on PYNQ-Z2 (FTDI) 2026-09-26: output arrives either way.
# If you get nothing on a different board, try setting both to $true.

try {
    $port.Open()
} catch {
    Write-Host ""
    Write-Host "FAILED to open $PortName : $($_.Exception.Message)"
    Write-Host "Check the port number in Device Manager (look for 'USB Serial Port')."
    Write-Host "Avoid the entries named 'Standard Serial over Bluetooth link'."
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

Write-Host "Connected. Pressing Enter on the board ..."
Write-Host "------------------------------------------------------------"

# A bare CR wakes the getty and prints a fresh login prompt.
$port.Write("`r")

$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    try {
        $chunk = $port.ReadExisting()
        if ($chunk.Length -gt 0) { Write-Host -NoNewline $chunk }
    } catch { }
    Start-Sleep -Milliseconds 150
}

Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host "Expected prompt:  xilinx@pynq:~$"
Write-Host "  ( '$' = normal user, '#' = root )"
Write-Host "Nothing at all means: board is off, wrong COM port, or a charge-only cable."

$port.Close()

if (-not $NoPause) { Read-Host "Press Enter to close" }
