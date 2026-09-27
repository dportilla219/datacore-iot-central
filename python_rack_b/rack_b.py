"""
DataCore UNAB - Dev 02 - Rack B
Origen de envio: Python (azure-iot-device) -> DPS -> IoT Central (MQTT sobre TLS 8883)
Intervalo: 30 s
Telemetria: tempIntake, tempExhaust, humidity   (sensor de referencia: Sensirion SHT31)
Property escribible: alarmThreshold             (umbral de alarma, degC)
Comando: setFan(on: bool) -> fanState: bool

Credenciales SOLO por variables de entorno (nunca en el codigo ni en el repo):
  IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY
Version del script: 1.1.0 (1.0.0 tenia deriva hacia arriba en tempIntake; corregido con reversion a la media)
"""
import asyncio
import json
import logging
import os
import random
import sys
from datetime import datetime, timezone

from azure.iot.device import Message, MethodResponse
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient

SCRIPT_VERSION = "1.1.0"
INTERVAL_S = int(os.getenv("SEND_INTERVAL", "30"))
DPS_HOST = "global.azure-devices-provisioning.net"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("rack_b_sesion.log", encoding="utf-8")],
)
log = logging.getLogger("rack-b")
logging.getLogger("azure").setLevel(logging.WARNING)

state = {"fan_on": False, "alarm_threshold": 27.0, "intake": 22.0}


def read_sensors() -> dict:
    """Simula un SHT31 en la entrada y salida del rack (rango fabricante -40..125 degC, 0..100 %HR).
    Modelo con reversion a la media: la temperatura oscila alrededor del punto de consigna del CRAC
    (22 degC). Con probabilidad baja aparece un 'punto caliente' de ~10 min (objetivo 28.5 degC),
    que dispara la regla R1 de forma ocasional y realista. El ventilador baja el objetivo a 20 degC."""
    if state.get("hot_left", 0) > 0:
        state["hot_left"] -= 1
    elif random.random() < 0.004:                     # ~1 evento cada 2 h a 30 s por muestra
        state["hot_left"] = 20                         # 20 muestras x 30 s = 10 min
        log.info("Evento simulado: punto caliente en el rack (10 min)")
    target = 28.5 if state.get("hot_left", 0) > 0 else 22.0
    if state["fan_on"]:
        target -= 2.0
    state["intake"] += 0.15 * (target - state["intake"]) + random.gauss(0, 0.2)
    state["intake"] = min(max(state["intake"], 17.0), 32.0)
    intake = round(state["intake"], 2)
    exhaust = round(intake + random.uniform(10.0, 14.0), 2)  # delta-T tipico de un servidor
    humidity = round(random.uniform(40.0, 55.0), 1)
    return {"tempIntake": intake, "tempExhaust": exhaust, "humidity": humidity}


async def provision(scope: str, device_id: str, key: str) -> str:
    log.info("Provisionando '%s' en DPS (scope %s)...", device_id, scope)
    prov = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host=DPS_HOST, registration_id=device_id, id_scope=scope, symmetric_key=key
    )
    result = await prov.register()
    if result.status != "assigned":
        raise RuntimeError(f"Provisionamiento fallido: {result.status}")
    hub = result.registration_state.assigned_hub
    log.info("Asignado al hub %s", hub)
    return hub


async def main():
    scope = os.getenv("IOTC_ID_SCOPE")
    device_id = os.getenv("IOTC_DEVICE_ID", "dc-rack-b")
    key = os.getenv("IOTC_DEVICE_KEY")
    if not scope or not key:
        sys.exit("Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY")

    hub = await provision(scope, device_id, key)
    client = IoTHubDeviceClient.create_from_symmetric_key(symmetric_key=key, hostname=hub, device_id=device_id)

    # --- Comando setFan ---
    async def on_method(req):
        if req.name == "setFan":
            state["fan_on"] = bool(req.payload)
            log.info("COMANDO setFan recibido -> ventilador %s", "ENCENDIDO" if state["fan_on"] else "APAGADO")
            await client.send_method_response(MethodResponse.create_from_method_request(req, 200, state["fan_on"]))
        else:
            await client.send_method_response(MethodResponse.create_from_method_request(req, 404, None))

    # --- Property escribible alarmThreshold ---
    async def apply_desired(patch: dict):
        if "alarmThreshold" in patch:
            state["alarm_threshold"] = float(patch["alarmThreshold"])
            log.info("PROPERTY alarmThreshold actualizada -> %.1f degC", state["alarm_threshold"])
            await client.patch_twin_reported_properties({
                "alarmThreshold": {"value": state["alarm_threshold"], "ac": 200,
                                   "ad": "aplicado", "av": patch.get("$version", 1)}
            })

    client.on_method_request_received = on_method
    client.on_twin_desired_properties_patch_received = apply_desired
    client.on_connection_state_change = lambda: log.info(
        "Estado de conexion: %s", "CONNECTED" if client.connected else "DISCONNECTED")

    await client.connect()
    log.info("Conectado a IoT Central. Enviando cada %d s (script v%s). Ctrl+C para detener.", INTERVAL_S, SCRIPT_VERSION)

    twin = await client.get_twin()
    await apply_desired(twin.get("desired", {}))

    try:
        while True:
            data = read_sensors()
            msg = Message(json.dumps(data), content_encoding="utf-8", content_type="application/json")
            await client.send_message(msg)
            alarma = " ALARMA: intake sobre umbral" if data["tempIntake"] > state["alarm_threshold"] else ""
            log.info("TX %s%s", data, alarma)
            await asyncio.sleep(INTERVAL_S)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log.info("Desconexion controlada (%s UTC)", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        await client.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
