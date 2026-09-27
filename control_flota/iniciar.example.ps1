# Plantilla del iniciar.ps1 que va en la carpeta de cada dispositivo (el real NO se sube: tiene la clave).
# Copiar como iniciar.ps1, poner los valores de IoT Central (Dispositivo -> Conectar) y ejecutar.
$Host.UI.RawUI.WindowTitle = "DataCore - <nombre del dispositivo>"
$env:IOTC_ID_SCOPE   = "0ne00XXXXXX"
$env:IOTC_DEVICE_ID  = "dc-rack-b"
$env:IOTC_DEVICE_KEY = "PEGA_AQUI_LA_CLAVE_PRINCIPAL"
# Solo para el clima exterior:
# $env:OPENWEATHER_KEY = "PEGA_AQUI_TU_API_KEY"
python .\rack_b.py          # o: node .\acceso_pnp.js, node .\puente_http.js, etc.
