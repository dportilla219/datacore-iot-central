"""
DataCore UNAB - Dev 05 - Clima exterior (condiciona el free-cooling)
Origen de envio: feed meteorologico OpenWeather (equivalente a Atlas Weather aceptado por el enunciado)
                 -> puente Python -> IoT Central (MQTT)
Intervalo: 5 min (plan gratuito: 60 consultas/min y 1.000.000/mes; OpenWeather actualiza cada ~10 min)
Telemetria: tempOutside (degC), humidity (%), windSpeed (km/h), rain1h (mm), cloudCover (%), sourceTimestamp
Estacion de referencia equivalente: Davis Vantage Pro2
Ubicacion: Bucaramanga (7.1193, -73.1227)

Variables de entorno (nunca en el codigo):
  IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY   -> dispositivo dc-clima-ext
  OPENWEATHER_KEY                                  -> API key de openweathermap.org
Version del script: 1.0.0
"""
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from azure.iot.device import Message
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient

SCRIPT_VERSION = "1.0.0"
INTERVAL_S = int(os.getenv("SEND_INTERVAL", "300"))
LAT, LON = os.getenv("LAT", "7.1193"), os.getenv("LON", "-73.1227")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("clima_sesion.log", encoding="utf-8")],
)
log = logging.getLogger("clima-ext")
logging.getLogger("azure").setLevel(logging.WARNING)


def fetch_weather(api_key: str) -> dict:
    url = (f"https://api.openweathermap.org/data/2.5/weather"
           f"?lat={LAT}&lon={LON}&units=metric&appid={api_key}")
    with urllib.request.urlopen(url, timeout=20) as r:
        w = json.load(r)
    src = datetime.fromtimestamp(w["dt"], tz=timezone.utc)
    return {
        "tempOutside": w["main"]["temp"],
        "humidity": w["main"]["humidity"],
        "windSpeed": round(w.get("wind", {}).get("speed", 0) * 3.6, 1),  # m/s -> km/h
        "rain1h": w.get("rain", {}).get("1h", 0.0),                         # sin lluvia, la API omite el campo
        "cloudCover": w.get("clouds", {}).get("all"),
        "sourceTimestamp": src.isoformat().replace("+00:00", "Z"),
    }


async def main():
    scope = os.getenv("IOTC_ID_SCOPE")
    device_id = os.getenv("IOTC_DEVICE_ID", "dc-clima-ext")
    key = os.getenv("IOTC_DEVICE_KEY")
    ow_key = os.getenv("OPENWEATHER_KEY")
    if not (scope and key and ow_key):
        sys.exit("Faltan variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_KEY y/o OPENWEATHER_KEY")

    # probar la API antes de conectar (una key recien creada tarda unas horas en activarse)
    try:
        log.info("Prueba de la API OpenWeather: %s", fetch_weather(ow_key))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("OpenWeather respondio 401: la API key es invalida o aun no se activa (espere 1-2 h).")
        raise

    prov = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host="global.azure-devices-provisioning.net",
        registration_id=device_id, id_scope=scope, symmetric_key=key)
    reg = await prov.register()
    if reg.status != "assigned":
        sys.exit(f"Provisionamiento fallido: {reg.status}")
    client = IoTHubDeviceClient.create_from_symmetric_key(
        symmetric_key=key, hostname=reg.registration_state.assigned_hub, device_id=device_id)
    client.on_connection_state_change = lambda: log.info(
        "Estado de conexion: %s", "CONNECTED" if client.connected else "DISCONNECTED")
    await client.connect()
    log.info("Puente OpenWeather -> IoT Central activo. Consulta cada %d s (v%s).", INTERVAL_S, SCRIPT_VERSION)

    try:
        while True:
            try:
                data = await asyncio.to_thread(fetch_weather, ow_key)
                ingest = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                await client.send_message(Message(json.dumps(data), content_encoding="utf-8",
                                                  content_type="application/json"))
                log.info("TX %s | fuente=%s ingesta=%s", data, data["sourceTimestamp"], ingest)
            except Exception as e:
                log.warning("No se pudo consultar/enviar: %s", e)
            await asyncio.sleep(INTERVAL_S)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log.info("Desconexion controlada")
        await client.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
