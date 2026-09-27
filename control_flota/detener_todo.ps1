<#
  DataCore UNAB - Detiene toda la flota y registra el fin de la captura
  Uso:  .\detener_todo.ps1
  Version: 1.0.0
#>
param([string]$Carpeta = $PSScriptRoot)

$patron = 'rack_b\.py|pasillo_frio\.py|clima_openweather\.py|calidad_aire_openmeteo\.py|fuga_agua_paho\.py|replay_humo\.py|puente_http\.js|pdu_simulada\.js|acceso_pnp\.js'

Write-Host ''
Write-Host '=== DataCore UNAB: deteniendo la flota ===' -ForegroundColor Cyan
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match $patron }
foreach ($p in $procs) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  $nombre = [regex]::Match($p.CommandLine, $patron).Value
  Write-Host ('  [STOP] ' + $nombre) -ForegroundColor Yellow
}

# Cerrar las ventanas de los dispositivos (los logs *_sesion.log quedan guardados)
Get-Process powershell -ErrorAction SilentlyContinue |
  Where-Object { $_.MainWindowTitle -like 'DataCore - *' } |
  Stop-Process -Force -ErrorAction SilentlyContinue

powercfg /change standby-timeout-ac 30 | Out-Null
powercfg /change standby-timeout-dc 15 | Out-Null

$registro = Join-Path $Carpeta 'registro_capturas.csv'
$actual   = Join-Path $Carpeta 'captura_actual.txt'
$fin = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if (Test-Path $actual) {
  $partes = (Get-Content $actual -Raw).Trim().Split('|')
  ('{0},{1},"{2}","{3}"' -f $partes[0], $fin, $partes[1], $partes[2]) | Out-File $registro -Append -Encoding utf8
  Remove-Item $actual
  Write-Host ("Captura registrada: {0} -> {1}" -f $partes[0], $fin) -ForegroundColor Cyan
}
Write-Host '  [!!] Detenga tambien el Rack A en Wokwi (boton STOP)' -ForegroundColor Yellow
