/*
  DataCore UNAB - Dev 10 - Puerta / control de acceso a la sala
  Origen de envio: dispositivo IoT PLUG AND PLAY en Node.js (MQTT/TLS 8883)
    El dispositivo anuncia su modelo DTDL (model ID) al registrarse en DPS y al conectarse.
    IoT Central reconoce el modelo y le asigna la plantilla DC-Puerta y Acceso AUTOMATICAMENTE.
  Intervalo: por evento (cada acceso) + latido de estado cada 5 min
  Telemetria: doorOpen (bool), accessEvent (string)
  Sensores de referencia: contacto magnetico MC-38 + lector RFID RC522 (13.56 MHz)

  Demo por teclado (escriba la letra y presione ENTER):
    a = acceso autorizado   d = acceso denegado   f = puerta forzada   (vacio) = estado
  Ademas genera accesos automaticos cada 2-6 min para poblar la ventana de captura.

  Variables de entorno: IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY, (opcional) MODEL_ID
  Version: 1.0.0
*/
const { ProvisioningDeviceClient } = require('azure-iot-provisioning-device');
const { Mqtt: ProvMqtt } = require('azure-iot-provisioning-device-mqtt');
const { SymmetricKeySecurityClient } = require('azure-iot-security-symmetric-key');
const { Client, Message } = require('azure-iot-device');
const { Mqtt } = require('azure-iot-device-mqtt');
const readline = require('readline');
const fs = require('fs');

const VERSION = '1.0.0';
const SCOPE = process.env.IOTC_ID_SCOPE;
const DEVICE_ID = process.env.IOTC_DEVICE_ID || 'dc-acceso';
const KEY = process.env.IOTC_DEVICE_KEY;
const MODEL_ID = process.env.MODEL_ID || 'dtmi:datacoreUnab:acceso;1';   // debe coincidir con la plantilla
const HEARTBEAT_S = 300;
const LOG_FILE = 'acceso_sesion.log';

const TARJETAS_OK = ['0457', '1182', '2290', '3315'];   // personal autorizado (ficticio)
let doorOpen = false;
let client;

function log(msg) {
  const line = `${new Date().toISOString()} | ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + '\n');
}

async function enviar(evento) {
  const data = { doorOpen, accessEvent: evento };
  const msg = new Message(JSON.stringify(data));
  msg.contentType = 'application/json';
  msg.contentEncoding = 'utf-8';
  try {
    await client.sendEvent(msg);
    log(`TX ${JSON.stringify(data)}${evento === 'PUERTA_FORZADA' ? '  ALARMA ACCESO' : ''}`);
  } catch (e) {
    log(`ERROR enviando: ${e.message}`);
  }
}

async function acceso(tipo) {
  const tarjeta = tipo === 'a'
    ? TARJETAS_OK[Math.floor(Math.random() * TARJETAS_OK.length)]
    : String(Math.floor(5000 + Math.random() * 4999));
  if (tipo === 'a') {
    doorOpen = true;
    await enviar(`ACCESO_AUTORIZADO:tarjeta-${tarjeta}`);
    setTimeout(async () => { doorOpen = false; await enviar('PUERTA_CERRADA'); },
      (5 + Math.random() * 15) * 1000);                // la puerta se cierra sola en 5-20 s
  } else if (tipo === 'd') {
    await enviar(`ACCESO_DENEGADO:tarjeta-${tarjeta}`);
  } else if (tipo === 'f') {
    doorOpen = true;
    await enviar('PUERTA_FORZADA');
    setTimeout(async () => { doorOpen = false; await enviar('PUERTA_CERRADA'); }, 30000);
  } else {
    await enviar(doorOpen ? 'ESTADO:ABIERTA' : 'ESTADO:CERRADA');
  }
}

function programarAutomatico() {
  const espera = (120 + Math.random() * 240) * 1000;   // 2 a 6 minutos
  setTimeout(async () => {
    await acceso(Math.random() < 0.85 ? 'a' : 'd');     // 85 % autorizados, 15 % denegados
    programarAutomatico();
  }, espera);
}

async function main() {
  if (!SCOPE || !KEY) {
    console.error('Faltan variables de entorno IOTC_ID_SCOPE y/o IOTC_DEVICE_KEY');
    process.exit(1);
  }

  log(`Provisionando '${DEVICE_ID}' como dispositivo Plug and Play (modelo ${MODEL_ID})...`);
  const prov = ProvisioningDeviceClient.create('global.azure-devices-provisioning.net', SCOPE,
    new ProvMqtt(), new SymmetricKeySecurityClient(DEVICE_ID, KEY));
  prov.setProvisioningPayload({ modelId: MODEL_ID });      // anuncia el modelo a DPS / IoT Central
  const reg = await prov.register();
  log(`Asignado al hub ${reg.assignedHub}`);

  client = Client.fromConnectionString(
    `HostName=${reg.assignedHub};DeviceId=${reg.deviceId};SharedAccessKey=${KEY}`, Mqtt);
  await client.setOptions({ modelId: MODEL_ID });          // anuncia el modelo al conectarse (PnP)
  client.on('disconnect', () => log('Estado de conexion: DISCONNECTED'));
  client.on('error', (e) => log(`Error de cliente: ${e.message}`));
  await client.open();
  log(`Estado de conexion: CONNECTED (Plug and Play, MQTT 8883, v${VERSION})`);
  log('Teclado: a=autorizado  d=denegado  f=forzada  ENTER=estado  | Ctrl+C=detener');

  await acceso('');                                         // estado inicial
  setInterval(() => acceso(''), HEARTBEAT_S * 1000);        // latido de estado
  programarAutomatico();

  readline.createInterface({ input: process.stdin }).on('line', (l) => acceso(l.trim().toLowerCase()));

  process.on('SIGINT', async () => {
    log('Desconexion controlada');
    await client.close().catch(() => {});
    process.exit(0);
  });
}

main().catch((e) => {
  log(`Fallo: ${e.message}`);
  process.exit(1);
});
