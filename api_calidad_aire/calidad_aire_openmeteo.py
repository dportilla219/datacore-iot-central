"""
DataCore UNAB - Dev 06 - Calidad de aire en la toma de free-cooling
Origen de envio: API PUBLICA (Open-Meteo Air Quality API, sin clave) -> puente Python -> IoT Central (MQTT)
Intervalo: 10 min (la fuente se actualiza cada 15 min; consultar mas seguido no aporta datos nuevos)
Telemetria: pm25, pm10 (ug/m3), aqi (US AQI), sourceTimestamp (marca de tiempo de la fuente)
Sensor de referencia equivalente: Plantower PMS5003
Ubicacion: Bucaramanga (7.1193, -73.1227)

Credenciales SOLO por variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY
Version del script: 1.0.0
"""
import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone

from azure.iot.device import Message
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient

SCRIPT_VERSION = "1.0.0"
INTERVAL_S = int(os.getenv("SEND_INTERVAL", "600"))
LAT, LON = os.getenv("LAT", "7.1193"), os.getenv("LON", "-73.1227")
API_URL = ("https://air-quality-api.open-meteo.com/v1/air-quality"
           f"?latitude={LAT}&longitude={LON}&current=pm10,pm2_5,us_aqi&timezone=GMT")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("calidad_aire_sesion.log", encoding="utf-8")],
)
log = logging.getLogger("calidad-aire")
logging.getLogger("azure").setLevel(logging.WARNING)


def fetch_air_quality() -> dict:
    req = urllib.request.Request(API_URL, headers={"User-Agent": "DataCore-UNAB-IoT/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        cur = json.load(r)["current"]
    # la API entrega "2026-09-24T21:00" en GMT -> ISO 8601 con Z para el campo dateTime
    src_ts = datetime.fromisoformat(cur["time"]).replace(tzinfo=timezone.utc)
    return {
        "pm25": cur.get("pm2_5"),
        "pm10": cur.get("pm10"),
        "aqi": cur.get("us_aqi"),
        "sourceTimestamp": src_ts.isoformat().replace("+00:00", "Z"),
    }


async def main():
    scope = os.getenv("IOTC_ID_SCOPE")
    device_id = os.getenv("IOTC_DEVICE_ID", "dc-aire")
    key = os.getenv("IOTC_DEVICE_KEY")
    if not scope or not key:
        sys.exit("Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY")

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
    log.info("Puente Open-Meteo -> IoT Central activo. Consulta cada %d s (v%s).", INTERVAL_S, SCRIPT_VERSION)

    try:
        while True:
            try:
                data = await asyncio.to_thread(fetch_air_quality)
                ingest = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                await client.send_message(Message(json.dumps(data), content_encoding="utf-8",
                                                  content_type="application/json"))
                log.info("TX %s | fuente=%s ingesta=%s", data, data["sourceTimestamp"], ingest)
            except Exception as e:  # API caida o sin red: se registra y se reintenta en el proximo ciclo
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
