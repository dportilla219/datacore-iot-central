"""
DataCore UNAB - Dev 04 - Pasillo frio / contencion
Origen de envio: segundo script Python, protocolo DISTINTO al Rack B:
    MQTT sobre WebSockets (wss, puerto 443) en lugar de MQTT/TLS 8883.
    Justificacion: en muchos centros de datos el firewall corporativo solo deja salir 443.
Intervalo: 60 s
Telemetria: temperature (degC), diffPressure (Pa), humidity (%)
Sensores de referencia: Sensirion SDP810-125Pa (presion diferencial), SHT31 (T/HR)

Credenciales SOLO por variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY
Version del script: 1.0.0
"""
import asyncio
import json
import logging
import os
import random
import sys

from azure.iot.device import Message
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient

SCRIPT_VERSION = "1.0.0"
INTERVAL_S = int(os.getenv("SEND_INTERVAL", "60"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("pasillo_frio_sesion.log", encoding="utf-8")],
)
log = logging.getLogger("pasillo-frio")
logging.getLogger("azure").setLevel(logging.WARNING)

state = {"temp": 20.5, "dp": 8.0}


def read_sensors() -> dict:
    # Pasillo frio: ASHRAE A1 recomienda 18-27 degC; dP positivo tipico 5-15 Pa hacia el pasillo caliente
    state["temp"] = min(max(state["temp"] + random.uniform(-0.25, 0.25), 17.5), 26.0)
    state["dp"] = min(max(state["dp"] + random.uniform(-1.0, 1.0), 2.0), 20.0)
    return {
        "temperature": round(state["temp"], 2),
        "diffPressure": round(state["dp"], 1),
        "humidity": round(random.uniform(42.0, 52.0), 1),
    }


async def main():
    scope = os.getenv("IOTC_ID_SCOPE")
    device_id = os.getenv("IOTC_DEVICE_ID", "dc-pasillo-frio")
    key = os.getenv("IOTC_DEVICE_KEY")
    if not scope or not key:
        sys.exit("Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY")

    log.info("Provisionando '%s' por DPS usando MQTT sobre WebSockets (443)...", device_id)
    prov = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host="global.azure-devices-provisioning.net",
        registration_id=device_id, id_scope=scope, symmetric_key=key, websockets=True)
    reg = await prov.register()
    if reg.status != "assigned":
        sys.exit(f"Provisionamiento fallido: {reg.status}")
    hub = reg.registration_state.assigned_hub
    log.info("Asignado al hub %s", hub)

    client = IoTHubDeviceClient.create_from_symmetric_key(
        symmetric_key=key, hostname=hub, device_id=device_id, websockets=True)
    client.on_connection_state_change = lambda: log.info(
        "Estado de conexion: %s", "CONNECTED" if client.connected else "DISCONNECTED")
    await client.connect()
    log.info("Conectado (wss:443). Enviando cada %d s (script v%s). Ctrl+C para detener.",
             INTERVAL_S, SCRIPT_VERSION)

    try:
        while True:
            data = read_sensors()
            msg = Message(json.dumps(data), content_encoding="utf-8", content_type="application/json")
            await client.send_message(msg)
            log.info("TX %s", data)
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
