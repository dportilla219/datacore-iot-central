<#
  DataCore UNAB - Arranque de toda la flota con un solo comando
  Uso (desde la carpeta que contiene las carpetas de cada dispositivo):
      .\iniciar_todo.ps1                 -> inicia todo, sin limite de tiempo
      .\iniciar_todo.ps1 -Horas 4        -> inicia todo y lo detiene solo a las 4 horas
      .\iniciar_todo.ps1 -Horas 4 -WokwiUrl "https://wokwi.com/projects/XXXX"
  Busca en cada subcarpeta un archivo iniciar.ps1 y lo abre en su propia ventana.
  Si la subcarpeta tiene pdu_simulada.js, primero arranca la PDU simulada.
  Registra inicio y fin de cada captura en registro_capturas.csv
  Version: 1.2.0 (no arranca si la flota ya esta corriendo) (abre Wokwi automaticamente si existe wokwi_url.txt)
#>
param(
  [double]$Horas = 0,
  [string]$Carpeta = $PSScriptRoot,
  [string]$WokwiUrl = "",
  [string]$Nota = ""
)

$host.UI.RawUI.WindowTitle = 'Flota DataCore (control)'

# Evita lanzar la flota dos veces (por ejemplo, tarea programada + arranque manual)
$patronFlota = 'rack_b\.py|pasillo_frio\.py|clima_openweather\.py|calidad_aire_openmeteo\.py|fuga_agua_paho\.py|replay_humo\.py|puente_http\.js|acceso_pnp\.js'
$yaCorriendo = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match $patronFlota }
if ($yaCorriendo) {
  Write-Host 'La flota DataCore ya esta corriendo. No se inicia otra vez.' -ForegroundColor Yellow
  Start-Sleep -Seconds 5
  exit 0
}
$registro = Join-Path $Carpeta 'registro_capturas.csv'
$actual   = Join-Path $Carpeta 'captura_actual.txt'
if (-not (Test-Path $registro)) { 'inicio,fin,dispositivos,nota' | Out-File $registro -Encoding utf8 }

Write-Host ''
Write-Host '=== DataCore UNAB: iniciando la flota ===' -ForegroundColor Cyan

# Evita que el PC se suspenda durante la captura (se restaura al detener)
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-dc 0 | Out-Null   # tambien con bateria

$lanzados = @()
$carpetas = Get-ChildItem -Path $Carpeta -Directory | Sort-Object Name
foreach ($c in $carpetas) {
  $dir = $c.FullName
  if (-not (Test-Path (Join-Path $dir 'iniciar.ps1'))) { continue }

  if (Test-Path (Join-Path $dir 'pdu_simulada.js')) {
    Start-Process powershell -WorkingDirectory $dir -ArgumentList @(
      '-NoExit', '-Command', "`$host.UI.RawUI.WindowTitle='DataCore - PDU simulada'; node pdu_simulada.js")
    Write-Host '  [OK] PDU simulada (API REST en :8080)' -ForegroundColor Green
    Start-Sleep -Seconds 2
  }

  $titulo = 'DataCore - ' + $c.Name
  Start-Process powershell -WorkingDirectory $dir -ArgumentList @(
    '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', "`$host.UI.RawUI.WindowTitle='$titulo'; & '.\iniciar.ps1'")
  $lanzados += $c.Name
  Write-Host ('  [OK] ' + $c.Name) -ForegroundColor Green
  Start-Sleep -Seconds 3   # escalonar los registros en DPS
}

if ($lanzados.Count -eq 0) {
  Write-Host 'No se encontro ninguna subcarpeta con iniciar.ps1. Revise la ubicacion de este script.' -ForegroundColor Red
  exit 1
}

# Si no se pasa -WokwiUrl, se toma de wokwi_url.txt (una linea con la URL del proyecto)
$archivoWokwi = Join-Path $Carpeta 'wokwi_url.txt'
if ($WokwiUrl -eq '' -and (Test-Path $archivoWokwi)) { $WokwiUrl = (Get-Content $archivoWokwi -Raw).Trim() }

if ($WokwiUrl -ne '') {
  Start-Process $WokwiUrl
  Write-Host '  [!!] Wokwi abierto en el navegador: presione PLAY y deje la pestana visible' -ForegroundColor Yellow
} else {
  Write-Host '  [!!] Recuerde iniciar el Rack A en Wokwi (PLAY) y dejar la pestana visible' -ForegroundColor Yellow
}
Write-Host '  [i]  El Rack C (simulado) corre solo en IoT Central' -ForegroundColor DarkGray

$inicio = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"$inicio|$($lanzados -join ' ')|$Nota" | Out-File $actual -Encoding utf8
Write-Host ''
Write-Host ("Captura iniciada: {0}  ({1} ventanas de dispositivos)" -f $inicio, $lanzados.Count) -ForegroundColor Cyan

if ($Horas -gt 0) {
  $fin = (Get-Date).AddHours($Horas)
  Write-Host ("Se detendra automaticamente a las {0:HH:mm}. No cierre esta ventana." -f $fin) -ForegroundColor Cyan
  Start-Sleep -Seconds ([int]($Horas * 3600))
  & (Join-Path $Carpeta 'detener_todo.ps1') -Carpeta $Carpeta
} else {
  Write-Host 'Para detener todo: .\detener_todo.ps1' -ForegroundColor Cyan
}
