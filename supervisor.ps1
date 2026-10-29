# ==============================================================================
# AlphaVision Production Process Supervisor & Continuous Watchdog
# Headless background launcher & watchdog for AnimeVision, SillyTavern, and MateEngine
# ==============================================================================

$ErrorActionPreference = "SilentlyContinue"

# Define authoritative paths
$BaseDir          = "C:\DriveD\mate_engine"
$SillyTavernDir   = "$BaseDir\SillyTavern-Launcher\SillyTavern"
$AlphaEnvDir      = "$BaseDir\MateEngineX_Data\alpha_env"
$PythonwExe       = "$AlphaEnvDir\Scripts\pythonw.exe"
$PythonExe        = "$AlphaEnvDir\Scripts\python.exe"
$MateEngineDir    = "C:\DriveD\MateEngineWin3.2 modded"
$MateEngineExe    = "$MateEngineDir\MateEngineX.exe"

if (-not (Test-Path $PythonwExe)) {
    if (Test-Path "$AlphaEnvDir\alpha_env\Scripts\pythonw.exe") {
        $PythonwExe = "$AlphaEnvDir\alpha_env\Scripts\pythonw.exe"
    } else {
        $PythonwExe = "pythonw.exe"
    }
}

$LogsDir          = "$AlphaEnvDir\logs"
if (!(Test-Path $LogsDir)) { New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null }
$SupervisorLog    = "$LogsDir\supervisor.log"

function Log-Message([string]$msg) {
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "$timestamp [SUPERVISOR] $msg"
    Add-Content -Path $SupervisorLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

Log-Message "Starting AlphaVision Ecosystem..."

# ------------------------------------------------------------------------------
# Step 1: Cleanup any previous orphaned instances and dangling port listeners
# ------------------------------------------------------------------------------
Log-Message "Step 1/4: Cleaning up previous processes and port listeners..."

# Terminate existing MateEngineX.exe
Stop-Process -Name "MateEngineX" -Force -ErrorAction SilentlyContinue

# Terminate existing pythonw/python running AnimeVision
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' or Name = 'python.exe'" | 
    Where-Object { $_.CommandLine -like "*AnimeVision*" } | 
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Terminate existing node.exe running SillyTavern
Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | 
    Where-Object { $_.CommandLine -like "*server.js*" -or $_.CommandLine -like "*SillyTavern*" } | 
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Free up ports 5001, 8787, 8000
Get-NetTCPConnection -LocalPort 5001, 8787, 8000 -ErrorAction SilentlyContinue | 
    Select-Object -ExpandProperty OwningProcess -Unique | 
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 600

# ------------------------------------------------------------------------------
# Step 2: Launch SillyTavern silently in background (Node.js daemon)
# ------------------------------------------------------------------------------
Log-Message "Step 2/4: Spawning SillyTavern background daemon..."
$nodeCmd = "server.js"
if (Test-Path "$SillyTavernDir\server.js") {
    Start-Process -FilePath "node.exe" -ArgumentList $nodeCmd -WorkingDirectory $SillyTavernDir -WindowStyle Hidden
}

# Wait for SillyTavern port 8000 (up to 8s)
$stReady = $false
for ($i = 0; $i -lt 16; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $res = Invoke-WebRequest -Uri "http://127.0.0.1:8000" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($res.StatusCode -lt 500) { $stReady = $true; break }
    } catch { }
}
if ($stReady) {
    Log-Message "SillyTavern is online at http://127.0.0.1:8000"
} else {
    Log-Message "SillyTavern spawned (continuing initialization)..."
}

# ------------------------------------------------------------------------------
# Step 3: Launch AlphaVision daemon silently via pythonw.exe
# ------------------------------------------------------------------------------
Log-Message "Step 3/4: Spawning AlphaVision daemon via $PythonwExe..."
Start-Process -FilePath $PythonwExe -ArgumentList "AnimeVision.py" -WorkingDirectory $AlphaEnvDir -WindowStyle Hidden

# Wait for AlphaVision port 5001 (up to 8s)
$avReady = $false
for ($i = 0; $i -lt 16; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $res = Invoke-WebRequest -Uri "http://127.0.0.1:5001/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($res.StatusCode -eq 200) { $avReady = $true; break }
    } catch { }
}
if ($avReady) {
    Log-Message "AlphaVision daemon is online and healthy at http://127.0.0.1:5001"
} else {
    Log-Message "AlphaVision daemon spawned (continuing)..."
}

# ------------------------------------------------------------------------------
# Step 4: Launch MateEngine visual companion window
# ------------------------------------------------------------------------------
Log-Message "Step 4/4: Launching MateEngine foreground desktop companion..."
if (Test-Path $MateEngineExe) {
    Start-Process -FilePath $MateEngineExe -WorkingDirectory $MateEngineDir
    Log-Message "MateEngineX.exe launched successfully."
} else {
    Log-Message "MateEngineX.exe not found at $MateEngineExe (skipping GUI)"
}

Log-Message "All initial components spawned. Entering continuous watchdog monitor loop..."

# ------------------------------------------------------------------------------
# Step 5: Continuous Auto-Restart Watchdog for AnimeVision.py
# ------------------------------------------------------------------------------
$HealthUrl = "http://127.0.0.1:5001/health"
$consecutiveFailures = 0

while ($true) {
    Start-Sleep -Seconds 3

    # 1. Check if AnimeVision process is active
    $avProc = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' or Name = 'python.exe'" | 
        Where-Object { $_.CommandLine -like "*AnimeVision.py*" }

    # 2. Check if port 5001 is listening
    $portListening = $false
    try {
        $conn = Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue
        if ($conn) { $portListening = $true }
    } catch { }

    # 3. Test HTTP responsiveness
    $healthy = $false
    if ($portListening) {
        try {
            $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                $healthy = $true
            }
        } catch { }
    }

    if ($avProc -and $portListening -and $healthy) {
        $consecutiveFailures = 0
    } else {
        $consecutiveFailures++
        Log-Message "[WATCHDOG WARNING] Health check failure #$consecutiveFailures (Proc: $([bool]$avProc), Port: $portListening, Healthy: $healthy)"

        if ($consecutiveFailures -ge 2) {
            Log-Message "[WATCHDOG ACTION] AnimeVision.py is unresponsive or dead. Performing clean restart after 2-second cooldown..."

            # Terminate any hung or zombie instances on port 5001
            Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' or Name = 'python.exe'" | 
                Where-Object { $_.CommandLine -like "*AnimeVision.py*" } | 
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

            Get-NetTCPConnection -LocalPort 5001 -ErrorAction SilentlyContinue | 
                Select-Object -ExpandProperty OwningProcess -Unique | 
                ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

            # Mandatory 2-second cooldown
            Start-Sleep -Seconds 2

            # Respawn using virtual environment pythonw.exe
            Log-Message "[WATCHDOG ACTION] Restarting AnimeVision.py via $PythonwExe..."
            Start-Process -FilePath $PythonwExe -ArgumentList "AnimeVision.py" -WorkingDirectory $AlphaEnvDir -WindowStyle Hidden

            # Allow time to initialize
            Start-Sleep -Seconds 3
            $consecutiveFailures = 0
        }
    }
}
