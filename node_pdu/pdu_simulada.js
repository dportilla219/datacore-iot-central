/*
  DataCore UNAB - Dev 09 - PDU de fila (equipo simulado)
  Simula la interfaz REST/JSON que traen las PDU inteligentes (APC AP8853, Raritan PX, etc.).
  Expone:  GET http://127.0.0.1:8080/api/v1/pdu/fila1
  No se conecta a Azure: el que envia a IoT Central es puente_http.js.
  Version: 1.0.0
*/
const http = require('http');

const PORT = Number(process.env.PDU_PORT || 8080);
const VOLTAGE = 208;            // V, trifasico tipico de centro de datos
let powerKw = 5.2;

function lectura() {
  // la carga TI varia lentamente entre 3.5 y 7.5 kW
  powerKw = Math.min(7.5, Math.max(3.5, powerKw + (Math.random() - 0.5) * 0.4));
  const pf = 0.92 + Math.random() * 0.06;                          // factor de potencia 0.92-0.98
  const current = (powerKw * 1000) / (Math.sqrt(3) * VOLTAGE * pf); // I = P / (raiz3 * V * FP)
  return {
    device: 'PDU-FILA1',
    model: 'APC AP8853 (simulada)',
    timestamp: new Date().toISOString(),
    power_kw: +powerKw.toFixed(2),
    current_a: +current.toFixed(2),
    power_factor: +pf.toFixed(3),
    voltage_v: VOLTAGE,
  };
}

http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/api/v1/pdu/fila1') {
    const body = JSON.stringify(lectura());
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(body);
    console.log(`${new Date().toISOString()} | GET ${req.url} -> ${body}`);
  } else {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end('{"error":"no encontrado"}');
  }
}).listen(PORT, '127.0.0.1', () =>
  console.log(`PDU simulada escuchando en http://127.0.0.1:${PORT}/api/v1/pdu/fila1  (Ctrl+C para detener)`));
