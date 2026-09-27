/*
  DataCore UNAB - Dev 09 - PDU / energia de fila
  Origen de envio: PUENTE HTTP/REST en Node.js
    1) consulta por HTTP la API REST de la PDU (GET /api/v1/pdu/fila1)
    2) reenvia a IoT Central usando el transporte HTTPS (puerto 443) del SDK de Azure para Node,
       tanto para el registro en DPS como para la telemetria. Es el unico origen de la flota por HTTPS.
  Intervalo: 60 s
  Telemetria: power (kW), current (A), powerFactor
  Nota: con HTTPS no hay conexion persistente; IoT Central puede no mostrar el estado "Conectado"
        aunque los datos lleguen (limitacion documentada del protocolo).

  Variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY, (opcional) PDU_URL, SEND_INTERVAL
  Version: 1.0.0
*/
const { ProvisioningDeviceClient } = require('azure-iot-provisioning-device');
const { Http: ProvHttp } = require('azure-iot-provisioning-device-http');
const { SymmetricKeySecurityClient } = require('azure-iot-security-symmetric-key');
const { Client, Message } = require('azure-iot-device');
const { Http } = require('azure-iot-device-http');
const fs = require('fs');

const VERSION = '1.0.0';
const SCOPE = process.env.IOTC_ID_SCOPE;
const DEVICE_ID = process.env.IOTC_DEVICE_ID || 'dc-pdu';
const KEY = process.env.IOTC_DEVICE_KEY;
const PDU_URL = process.env.PDU_URL || 'http://127.0.0.1:8080/api/v1/pdu/fila1';
const INTERVAL_S = Number(process.env.SEND_INTERVAL || 60);
const LOG_FILE = 'pdu_sesion.log';

function log(msg) {
  const line = `${new Date().toISOString()} | ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + '\n');
}

async function main() {
  if (!SCOPE || !KEY) {
    console.error('Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY');
    process.exit(1);
  }

  log(`Provisionando '${DEVICE_ID}' en DPS por HTTPS...`);
  const security = new SymmetricKeySecurityClient(DEVICE_ID, KEY);
  const prov = ProvisioningDeviceClient.create('global.azure-devices-provisioning.net', SCOPE, new ProvHttp(), security);
  const reg = await prov.register();
  log(`Asignado al hub ${reg.assignedHub}`);

  const client = Client.fromConnectionString(
    `HostName=${reg.assignedHub};DeviceId=${reg.deviceId};SharedAccessKey=${KEY}`, Http);
  await client.open();
  log(`Puente HTTP activo: ${PDU_URL} -> IoT Central (HTTPS 443), cada ${INTERVAL_S} s (v${VERSION}). Ctrl+C para detener.`);

  const ciclo = async () => {
    try {
      const r = await fetch(PDU_URL);
      if (!r.ok) throw new Error(`PDU respondio HTTP ${r.status}`);
      const pdu = await r.json();
      const data = { power: pdu.power_kw, current: pdu.current_a, powerFactor: pdu.power_factor };
      const msg = new Message(JSON.stringify(data));
      msg.contentType = 'application/json';
      msg.contentEncoding = 'utf-8';
      await client.sendEvent(msg);
      log(`TX ${JSON.stringify(data)} (lectura PDU ${pdu.timestamp})`);
    } catch (e) {
      log(`ERROR en el ciclo (se reintenta en el siguiente): ${e.message}`);
    }
  };

  await ciclo();
  const timer = setInterval(ciclo, INTERVAL_S * 1000);

  process.on('SIGINT', async () => {
    clearInterval(timer);
    log('Desconexion controlada');
    await client.close().catch(() => {});
    process.exit(0);
  });
}

main().catch((e) => {
  log(`Fallo: ${e.message}`);
  process.exit(1);
});
