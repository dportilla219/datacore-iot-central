"""
DataCore UNAB - Dev 08 - Deteccion de humo / incendio
Origen de envio: REPLAY DE CSV (historico de un simulacro registrado) -> IoT Central (MQTT)
    El feed no es un sensor en vivo ni un generador aleatorio: reproduce, fila por fila,
    el archivo simulacro_humo.csv (4 h de registro cada 30 s, con un evento de humo
    por sobrecalentamiento de UPS entre el minuto 90 y 115).
Intervalo: 30 s (el mismo del registro original). Al terminar el archivo vuelve a empezar.
Telemetria: smoke (%/ft de oscurecimiento), ceilingTemp (degC)
Sensor de referencia: System Sensor 2151 (fotoelectrico, sensibilidad 0.5-4.0 %/ft)

Opcional: START_ROW=170 arranca cerca del evento (util para la demo).
Credenciales SOLO por variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY
Version del script: 1.0.0
"""
import asyncio
import csv
import json
import logging
import os
import sys
from pathlib import Path

from azure.iot.device import Message
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient

SCRIPT_VERSION = "1.0.0"
INTERVAL_S = int(os.getenv("SEND_INTERVAL", "30"))
CSV_PATH = Path(os.getenv("CSV_PATH", Path(__file__).with_name("simulacro_humo.csv")))
START_ROW = int(os.getenv("START_ROW", "0"))
SMOKE_ALARM = 2.5  # %/ft, umbral de alarma del escenario

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("humo_sesion.log", encoding="utf-8")],
)
log = logging.getLogger("humo-replay")
logging.getLogger("azure").setLevel(logging.WARNING)


async def main():
    scope = os.getenv("IOTC_ID_SCOPE")
    device_id = os.getenv("IOTC_DEVICE_ID", "dc-humo")
    key = os.getenv("IOTC_DEVICE_KEY")
    if not scope or not key:
        sys.exit("Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY")

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    log.info("CSV cargado: %s (%d filas)", CSV_PATH.name, len(rows))

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
    log.info("Replay activo cada %d s desde la fila %d (v%s).", INTERVAL_S, START_ROW, SCRIPT_VERSION)

    i = START_ROW % len(rows)
    try:
        while True:
            r = rows[i]
            data = {"smoke": float(r["smoke"]), "ceilingTemp": float(r["ceilingTemp"])}
            await client.send_message(Message(json.dumps(data), content_encoding="utf-8",
                                              content_type="application/json"))
            alarm = "  ALARMA HUMO" if data["smoke"] >= SMOKE_ALARM else ""
            log.info("TX fila %d/%d (registro %s, %s) %s%s",
                     i + 1, len(rows), r["timestamp"], r["fase"], data, alarm)
            i = (i + 1) % len(rows)
            await asyncio.sleep(INTERVAL_S)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log.info("Desconexion controlada (ultima fila enviada: %d)", i)
        await client.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
