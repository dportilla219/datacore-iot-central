"""
DataCore UNAB - Dev 07 - Deteccion de agua bajo piso tecnico
Origen de envio: CLIENTE MQTT EXPLICITO (paho-mqtt), SIN el SDK de Azure.
    El script arma a mano todo el protocolo: token SAS (HMAC-SHA256), registro en DPS por MQTT
    ($dps/registrations/...) y publicacion en devices/{id}/messages/events/ sobre TLS 8883.
Intervalo: latido cada 60 s + envio inmediato cuando cambia el estado de fuga (evento)
Telemetria: leakDetected (bool), floorHumidity (%)
Sensor de referencia: cable detector de fugas RLE SeaHawk (LD310) + sonda de humedad de piso

Demo: presione ENTER en la consola para simular inicio/fin de una fuga.
Credenciales SOLO por variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY
Version del script: 1.0.0
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import ssl
import sys
import threading
import time
import urllib.parse

import paho.mqtt.client as mqtt

SCRIPT_VERSION = "1.0.0"
HEARTBEAT_S = int(os.getenv("SEND_INTERVAL", "60"))
DPS_HOST = "global.azure-devices-provisioning.net"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("fuga_agua_sesion.log", encoding="utf-8")],
)
log = logging.getLogger("fuga-agua")


def sas_token(uri: str, key_b64: str, ttl_s: int = 3600, key_name: str | None = None) -> str:
    expiry = int(time.time()) + ttl_s
    enc_uri = urllib.parse.quote(uri, safe="")
    to_sign = f"{enc_uri}\n{expiry}".encode()
    sig = base64.b64encode(hmac.new(base64.b64decode(key_b64), to_sign, hashlib.sha256).digest()).decode()
    tok = f"SharedAccessSignature sr={enc_uri}&sig={urllib.parse.quote(sig, safe='')}&se={expiry}"
    return tok + (f"&skn={key_name}" if key_name else "")


def new_client(client_id: str) -> mqtt.Client:
    try:  # paho-mqtt 2.x
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id, protocol=mqtt.MQTTv311)
    except AttributeError:  # paho-mqtt 1.x
        c = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
    c.tls_set(cert_reqs=ssl.CERT_REQUIRED)  # usa los certificados raiz del sistema operativo
    return c


def provision(scope: str, device_id: str, key: str) -> str:
    """Registro en DPS usando MQTT puro."""
    log.info("DPS: registrando '%s' (scope %s) con paho-mqtt...", device_id, scope)
    result = {}
    got = threading.Event()

    def on_message(_c, _u, msg):
        result["topic"], result["body"] = msg.topic, json.loads(msg.payload or b"{}")
        got.set()

    c = new_client(device_id)
    reg_uri = f"{scope}/registrations/{device_id}"
    c.username_pw_set(f"{reg_uri}/api-version=2019-03-31", sas_token(reg_uri, key, key_name="registration"))
    c.on_message = on_message
    c.connect(DPS_HOST, 8883)
    c.loop_start()
    c.subscribe("$dps/registrations/res/#", qos=1)
    time.sleep(1)
    c.publish("$dps/registrations/PUT/iotdps-register/?$rid=1", json.dumps({"registrationId": device_id}), qos=1)

    try:
        for rid in range(2, 30):
            if not got.wait(30):
                raise RuntimeError("DPS no respondio")
            got.clear()
            body = result["body"]
            log.info("DPS <- %s %s", result["topic"], body.get("status"))
            if body.get("status") == "assigned":
                return body["registrationState"]["assignedHub"]
            if body.get("status") == "assigning":
                time.sleep(3)
                c.publish(f"$dps/registrations/GET/iotdps-get-operationstatus/?$rid={rid}"
                          f"&operationId={body['operationId']}", "", qos=1)
                continue
            raise RuntimeError(f"DPS error: {body}")
        raise RuntimeError("DPS: demasiados intentos")
    finally:
        c.loop_stop()
        c.disconnect()


def main():
    scope = os.getenv("IOTC_ID_SCOPE")
    device_id = os.getenv("IOTC_DEVICE_ID", "dc-agua")
    key = os.getenv("IOTC_DEVICE_KEY")
    if not scope or not key:
        sys.exit("Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY")

    hub = provision(scope, device_id, key)
    log.info("Asignado al hub %s", hub)

    c = new_client(device_id)
    c.username_pw_set(f"{hub}/{device_id}/?api-version=2021-04-12",
                      sas_token(f"{hub}/devices/{device_id}", key, ttl_s=24 * 3600))
    c.on_connect = lambda *_a: log.info("Estado de conexion: CONNECTED (paho-mqtt, TLS 8883)")
    c.on_disconnect = lambda *_a: log.info("Estado de conexion: DISCONNECTED")
    c.connect(hub, 8883, keepalive=120)
    c.loop_start()
    topic = f"devices/{device_id}/messages/events/"

    state = {"leak": False, "changed": threading.Event()}

    def keyboard():
        for _ in sys.stdin:
            state["leak"] = not state["leak"]
            log.info(">>> Simulacion: fuga %s", "INICIADA" if state["leak"] else "RESUELTA")
            state["changed"].set()

    threading.Thread(target=keyboard, daemon=True).start()
    log.info("Latido cada %d s (v%s). ENTER = simular fuga. Ctrl+C = detener.", HEARTBEAT_S, SCRIPT_VERSION)

    try:
        while True:
            hum = random.uniform(78, 95) if state["leak"] else random.uniform(35, 45)
            data = {"leakDetected": state["leak"], "floorHumidity": round(hum, 1)}
            c.publish(topic, json.dumps(data), qos=1)
            log.info("TX %s%s", data, "  ALARMA FUGA" if state["leak"] else "")
            state["changed"].wait(HEARTBEAT_S)  # despierta antes si hay cambio (evento)
            state["changed"].clear()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Desconexion controlada")
        c.loop_stop()
        c.disconnect()


if __name__ == "__main__":
    main()
