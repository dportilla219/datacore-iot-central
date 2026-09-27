"""
Registra un dispositivo en DPS UNA sola vez y muestra el hub asignado.
Uso (PowerShell):
  $env:IOTC_ID_SCOPE="0ne00..."; $env:IOTC_DEVICE_ID="dc-rack-a"; $env:IOTC_DEVICE_KEY="..."
  python provision_once.py
Luego copie el hub en secrets.h:  #define IOT_HUB_HOST "iotc-xxxx.azure-devices.net"
"""
import asyncio
import os
import sys

from azure.iot.device.aio import ProvisioningDeviceClient


async def main():
    scope, dev, key = (os.getenv(k) for k in ("IOTC_ID_SCOPE", "IOTC_DEVICE_ID", "IOTC_DEVICE_KEY"))
    if not all((scope, dev, key)):
        sys.exit("Faltan IOTC_ID_SCOPE, IOTC_DEVICE_ID o IOTC_DEVICE_KEY")
    prov = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host="global.azure-devices-provisioning.net",
        registration_id=dev, id_scope=scope, symmetric_key=key)
    r = await prov.register()
    print("Estado:", r.status)
    if r.status == "assigned":
        print(f'\nCopia esto en secrets.h:\n#define IOT_HUB_HOST "{r.registration_state.assigned_hub}"')


asyncio.run(main())
