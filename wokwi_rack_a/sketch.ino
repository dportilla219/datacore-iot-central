/*
  DataCore UNAB - Dev 01 - Rack A
  Origen de envio: Wokwi (ESP32 virtual) -> DPS -> IoT Central, MQTT sobre TLS (8883)
  Intervalo: 15 s
  Sensores: 2x DS18B20 (intake / exhaust) + DHT22 (humedad)
  Actuadores: LED azul = ventilador (comando setFan), LED rojo = alarma de temperatura
  Boton: simula caida de red (desconexion controlada / reconexion)
  Property escribible: alarmThreshold (degC)
  Version del firmware: 1.0.5
  Historial: 1.0.1 diagnostico DPS · 1.0.2 conexion directa al hub (IOT_HUB_HOST) · 1.0.3 reintento de Wi-Fi
             1.0.4 modelo del ventilador · 1.0.5 recuperacion automatica: resincroniza NTP cada 10 min y
             antes de reconectar (el token SAS depende del reloj), reinicia el Wi-Fi tras 1 min sin hub y
             reinicia el ESP32 tras 5 min sin hub.
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <DHT.h>
#include <time.h>
#include "esp_sntp.h"
#include "mbedtls/md.h"
#include "mbedtls/base64.h"
#include "secrets.h"
#include "azure_ca.h"

#define FW_VERSION   "1.0.5"
#define INTERVAL_MS  15000UL
#define SAS_TTL_S    3600UL
#define NTP_EVERY_MS    600000UL   // resincronizar la hora cada 10 min
#define WIFI_RESET_MS    60000UL   // sin hub 1 min  -> reiniciar Wi-Fi
#define REBOOT_MS       300000UL   // sin hub 5 min  -> reiniciar el ESP32

// Pines
#define PIN_INTAKE   4
#define PIN_EXHAUST  5
#define PIN_DHT      15
#define PIN_FAN_LED  26
#define PIN_ALARM    27
#define PIN_BUTTON   14

const char* DPS_HOST = "global.azure-devices-provisioning.net";

OneWire owIntake(PIN_INTAKE), owExhaust(PIN_EXHAUST);
DallasTemperature sIntake(&owIntake), sExhaust(&owExhaust);
DHT dht(PIN_DHT, DHT22);

WiFiClientSecure hubTls;
PubSubClient hub(hubTls);

String assignedHub;
unsigned long lastSend = 0, lastRetry = 0, lastNtp = 0, offlineSince = 0;
bool wifiReset = false;
time_t sasExpiry = 0;
bool fanOn = false;
// Modelo simulado del efecto del ventilador (v1.0.4): Wokwi no simula flujo de aire, asi que
// con el ventilador encendido la temperatura reportada baja 0.5 C por envio (max 8 C) y al
// apagarlo se recupera al mismo ritmo. El valor del sensor se conserva en el log.
#define FAN_STEP_C   0.5
#define FAN_MAX_C    8.0
float fanCooling = 0.0;
bool offlineMode = false;      // activado con el boton
float alarmThreshold = 27.0;
int ridCounter = 0;

// ---------- utilidades ----------
void logf(const char* fmt, ...) {
  char buf[256];
  va_list args; va_start(args, fmt); vsnprintf(buf, sizeof(buf), fmt, args); va_end(args);
  time_t now = time(nullptr);
  struct tm t; gmtime_r(&now, &t);
  Serial.printf("%04d-%02d-%02d %02d:%02d:%02dZ | %s\n",
                t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, buf);
}

String urlEncode(const String& s) {
  String o; char hex[4];
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (isalnum((unsigned char)c) || c == '-' || c == '_' || c == '.' || c == '~') o += c;
    else { snprintf(hex, sizeof(hex), "%%%02X", (uint8_t)c); o += hex; }
  }
  return o;
}

// Token SAS: HMAC-SHA256(clave del dispositivo, urlencode(uri) + "\n" + expiracion)
String sasToken(const String& uri, time_t expiry, const char* keyName) {
  uint8_t key[64]; size_t keyLen = 0;
  mbedtls_base64_decode(key, sizeof(key), &keyLen, (const uint8_t*)DEVICE_KEY, strlen(DEVICE_KEY));
  String encUri = urlEncode(uri);
  String toSign = encUri + "\n" + String((unsigned long)expiry);

  uint8_t hmac[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, key, keyLen);
  mbedtls_md_hmac_update(&ctx, (const uint8_t*)toSign.c_str(), toSign.length());
  mbedtls_md_hmac_finish(&ctx, hmac);
  mbedtls_md_free(&ctx);

  uint8_t b64[64]; size_t b64Len = 0;
  mbedtls_base64_encode(b64, sizeof(b64), &b64Len, hmac, sizeof(hmac));
  String sig; for (size_t i = 0; i < b64Len; i++) sig += (char)b64[i];

  String tok = "SharedAccessSignature sr=" + encUri + "&sig=" + urlEncode(sig) + "&se=" + String((unsigned long)expiry);
  if (keyName) tok += "&skn=" + String(keyName);
  return tok;
}

String payloadToString(byte* payload, unsigned int len) {
  String p; p.reserve(len);
  for (unsigned int i = 0; i < len; i++) p += (char)payload[i];
  return p;
}

// ---------- WiFi y hora ----------
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  for (int intento = 1; WiFi.status() != WL_CONNECTED; intento++) {
    Serial.printf("Conectando a Wokwi-GUEST (intento %d)", intento);
    WiFi.disconnect(true);
    delay(100);
    WiFi.begin("Wokwi-GUEST", "", 6);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { delay(250); Serial.print("."); }
    Serial.println(WiFi.status() == WL_CONNECTED ? " OK" : " sin respuesta, reintentando");
  }
  Serial.print("IP: "); Serial.println(WiFi.localIP());
}

void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("Sincronizando hora NTP");
  while (time(nullptr) < 1700000000) { delay(250); Serial.print("."); }
  Serial.println(" OK");
  lastNtp = millis();
}

// v1.0.5: en Wokwi la simulacion puede ir mas lenta que el tiempo real y el reloj del ESP32 se atrasa.
// El token SAS se firma con ese reloj, asi que un reloj atrasado produce tokens vencidos (estado 5).
void resyncTime(const char* motivo) {
  time_t before = time(nullptr);
  sntp_set_sync_status(SNTP_SYNC_STATUS_RESET);
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  unsigned long t0 = millis();
  while (sntp_get_sync_status() != SNTP_SYNC_STATUS_COMPLETED && millis() - t0 < 10000) delay(100);
  lastNtp = millis();
  bool ok = sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED;
  logf("Hora NTP %s (%s): ajuste %+ld s", ok ? "resincronizada" : "SIN respuesta", motivo, (long)(time(nullptr) - before));
}

// ---------- DPS (aprovisionamiento) ----------
String dpsTopic, dpsPayload;
volatile bool dpsGot = false;

void dpsCallback(char* topic, byte* payload, unsigned int len) {
  dpsTopic = String(topic);
  dpsPayload = payloadToString(payload, len);
  dpsGot = true;
}

bool waitDps(PubSubClient& c, unsigned long timeoutMs) {
  unsigned long t0 = millis();
  while (!dpsGot && millis() - t0 < timeoutMs) {
    if (!c.loop()) { logf("DPS: la conexion se cerro (estado %d)", c.state()); return false; }
    delay(10);
  }
  bool got = dpsGot; dpsGot = false;
  if (got) logf("DPS <- %s %s", dpsTopic.c_str(), dpsPayload.c_str());
  return got;
}

bool provision() {
  logf("Provisionando '%s' en DPS (scope %s)...", DEVICE_ID, ID_SCOPE);
  WiFiClientSecure tls;
  tls.setCACert(AZURE_ROOT_CA);
  PubSubClient dps(tls);          // tiempos amplios: la simulacion TLS de Wokwi es lenta
  dps.setServer(DPS_HOST, 8883);
  dps.setBufferSize(1024);
  dps.setKeepAlive(120);
  dps.setSocketTimeout(30);
  dps.setCallback(dpsCallback);

  String regUri = String(ID_SCOPE) + "/registrations/" + DEVICE_ID;
  String user = regUri + "/api-version=2019-03-31";
  String pass = sasToken(regUri, time(nullptr) + SAS_TTL_S, "registration");
  if (!dps.connect(DEVICE_ID, user.c_str(), pass.c_str())) {
    char err[128] = "";
    tls.lastError(err, sizeof(err));
    logf("DPS: fallo de conexion MQTT (estado %d) TLS: %s", dps.state(), err);
    return false;
  }
  logf("DPS: conectado por MQTT, enviando registro");
  bool sub = dps.subscribe("$dps/registrations/res/#", 1);
  for (int i = 0; i < 100; i++) { dps.loop(); delay(10); }   // dar tiempo al SUBACK
  String body = String("{\"registrationId\":\"") + DEVICE_ID + "\"}";
  bool pub = dps.publish("$dps/registrations/PUT/iotdps-register/?$rid=1", body.c_str());
  logf("DPS: subscribe=%s publish=%s estado=%d", sub ? "ok" : "FALLO", pub ? "ok" : "FALLO", dps.state());

  for (int i = 0; i < 20; i++) {
    if (!waitDps(dps, 45000)) { logf("DPS: sin respuesta (estado %d)", dps.state()); dps.disconnect(); return false; }
    JsonDocument doc;
    deserializeJson(doc, dpsPayload);
    String status = doc["status"] | "";
    if (status == "assigned") {
      assignedHub = doc["registrationState"]["assignedHub"].as<String>();
      logf("Asignado al hub %s", assignedHub.c_str());
      dps.disconnect();
      return true;
    }
    if (status == "assigning" || dpsTopic.indexOf("/202/") >= 0) {
      String op = doc["operationId"].as<String>();
      delay(3000);
      String t = "$dps/registrations/GET/iotdps-get-operationstatus/?$rid=2&operationId=" + op;
      dps.publish(t.c_str(), "");
      continue;
    }
    logf("DPS: error %s -> %s", dpsTopic.c_str(), dpsPayload.c_str());
    dps.disconnect();
    return false;
  }
  dps.disconnect();
  return false;
}

// ---------- IoT Hub (IoT Central) ----------
void reportThreshold(int version) {
  JsonDocument doc;
  JsonObject p = doc["alarmThreshold"].to<JsonObject>();
  p["value"] = alarmThreshold; p["ac"] = 200; p["ad"] = "aplicado"; p["av"] = version;
  String out; serializeJson(doc, out);
  String t = "$iothub/twin/PATCH/properties/reported/?$rid=rp" + String(++ridCounter);
  hub.publish(t.c_str(), out.c_str());
}

void applyDesired(const String& json, bool fullTwin) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) return;
  JsonVariant d = fullTwin ? doc["desired"].as<JsonVariant>() : doc.as<JsonVariant>();
  if (!d["alarmThreshold"].isNull()) {
    alarmThreshold = d["alarmThreshold"].as<float>();
    logf("PROPERTY alarmThreshold actualizada -> %.1f C", alarmThreshold);
    reportThreshold(d["$version"] | 1);
  }
}

void hubCallback(char* topic, byte* payload, unsigned int len) {
  // copiar antes de publicar: topic/payload apuntan al buffer interno de PubSubClient
  String t(topic);
  String p = payloadToString(payload, len);

  if (t.startsWith("$iothub/methods/POST/")) {
    int s = strlen("$iothub/methods/POST/");
    String method = t.substring(s, t.indexOf('/', s));
    String rid = t.substring(t.indexOf("$rid=") + 5);
    if (method == "setFan") {
      p.trim();
      fanOn = (p == "true");
      digitalWrite(PIN_FAN_LED, fanOn ? HIGH : LOW);
      logf("COMANDO setFan recibido -> ventilador %s", fanOn ? "ENCENDIDO" : "APAGADO");
      String rt = "$iothub/methods/res/200/?$rid=" + rid;
      hub.publish(rt.c_str(), fanOn ? "true" : "false");
    } else {
      String rt = "$iothub/methods/res/404/?$rid=" + rid;
      hub.publish(rt.c_str(), "{}");
    }
  } else if (t.startsWith("$iothub/twin/PATCH/properties/desired/")) {
    applyDesired(p, false);
  } else if (t.startsWith("$iothub/twin/res/200/") && t.indexOf("$rid=get") >= 0) {
    applyDesired(p, true);
  }
}

bool connectHub() {
  hubTls.setCACert(AZURE_ROOT_CA);
  hub.setServer(assignedHub.c_str(), 8883);
  hub.setBufferSize(2048);
  hub.setKeepAlive(120);
  hub.setSocketTimeout(30);
  hub.setCallback(hubCallback);

  String user = assignedHub + "/" + DEVICE_ID + "/?api-version=2021-04-12";
  sasExpiry = time(nullptr) + SAS_TTL_S;
  String pass = sasToken(assignedHub + "/devices/" + DEVICE_ID, sasExpiry, nullptr);
  if (!hub.connect(DEVICE_ID, user.c_str(), pass.c_str())) {
    char err[128] = "";
    hubTls.lastError(err, sizeof(err));
    int st = hub.state();
    logf("Hub: fallo de conexion (estado %d) TLS: %s", st, err);
    if (st == 5) resyncTime("token rechazado");   // 5 = no autorizado: token SAS vencido
    return false;
  }
  hub.subscribe("$iothub/methods/POST/#");
  hub.subscribe("$iothub/twin/res/#");
  hub.subscribe("$iothub/twin/PATCH/properties/desired/#");
  hub.publish("$iothub/twin/GET/?$rid=get", "");
  logf("Estado de conexion: CONNECTED (firmware v%s, envio cada %lu s)", FW_VERSION, INTERVAL_MS / 1000);
  return true;
}

void sendTelemetry() {
  sIntake.requestTemperatures();
  sExhaust.requestTemperatures();
  float rawIntake = sIntake.getTempCByIndex(0);
  float rawExhaust = sExhaust.getTempCByIndex(0);
  float hum = dht.readHumidity();

  // efecto simulado del ventilador (ver FAN_STEP_C / FAN_MAX_C)
  if (fanOn) fanCooling = min(fanCooling + (float)FAN_STEP_C, (float)FAN_MAX_C);
  else       fanCooling = max(fanCooling - (float)FAN_STEP_C, 0.0f);
  float intake = rawIntake - fanCooling;
  float exhaust = rawExhaust - fanCooling * 0.7f;

  JsonDocument doc;
  doc["tempIntake"] = roundf(intake * 100) / 100.0;
  doc["tempExhaust"] = roundf(exhaust * 100) / 100.0;
  if (!isnan(hum)) doc["humidity"] = roundf(hum * 10) / 10.0;
  String out; serializeJson(doc, out);

  String topic = String("devices/") + DEVICE_ID + "/messages/events/";
  bool ok = hub.publish(topic.c_str(), out.c_str());

  bool alarm = intake > alarmThreshold;
  digitalWrite(PIN_ALARM, alarm ? HIGH : LOW);
  if (fanCooling > 0)
    logf("TX %s %s(sensor %.1f C, ventilador -%.1f C)%s", out.c_str(), ok ? "" : "(FALLO) ",
         rawIntake, fanCooling, alarm ? " ALARMA: intake sobre umbral" : "");
  else
    logf("TX %s %s%s", out.c_str(), ok ? "" : "(FALLO)", alarm ? " ALARMA: intake sobre umbral" : "");
}

// ---------- setup / loop ----------
void setup() {
  Serial.begin(115200);
  pinMode(PIN_FAN_LED, OUTPUT);
  pinMode(PIN_ALARM, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  sIntake.begin(); sExhaust.begin(); dht.begin();

  connectWiFi();
  syncTime();
#ifdef IOT_HUB_HOST
  // Atajo: el dispositivo ya fue registrado en DPS una vez (provision_once.py),
  // asi que se conecta directo al hub y evita el paso DPS, lento en Wokwi.
  assignedHub = IOT_HUB_HOST;
  logf("Usando hub ya asignado: %s", assignedHub.c_str());
#else
  int intento = 1;
  while (!provision()) {
    logf("Reintentando DPS en 10 s (intento %d)", ++intento);
    delay(10000);
  }
#endif
  while (!connectHub()) { logf("Reintentando hub en 10 s"); delay(10000); }
}

void loop() {
  // Boton: alterna caida de red simulada (desconexion controlada)
  static bool lastBtn = HIGH;
  bool btn = digitalRead(PIN_BUTTON);
  if (lastBtn == HIGH && btn == LOW) {
    offlineMode = !offlineMode;
    if (offlineMode) { hub.disconnect(); logf("Estado de conexion: DISCONNECTED (desconexion controlada por boton)"); }
    else { logf("Reconectando..."); }
    delay(200);
  }
  lastBtn = btn;
  if (offlineMode) { delay(20); return; }

  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (millis() - lastNtp > NTP_EVERY_MS) resyncTime("periodica");

  // renovar token SAS 5 min antes de expirar
  if (hub.connected() && time(nullptr) > sasExpiry - 300) {
    logf("Renovando token SAS");
    hub.disconnect();
  }

  if (!hub.connected()) {
    if (offlineSince == 0) { offlineSince = millis(); wifiReset = false; resyncTime("antes de reconectar"); }
    unsigned long off = millis() - offlineSince;
    if (off > REBOOT_MS) {
      logf("Sin hub por %lu s: reiniciando el ESP32 (recuperacion automatica)", off / 1000);
      delay(200);
      ESP.restart();
    }
    if (off > WIFI_RESET_MS && !wifiReset) {
      logf("Sin hub por %lu s: reiniciando Wi-Fi", off / 1000);
      WiFi.disconnect(true); delay(500); connectWiFi(); resyncTime("tras reiniciar Wi-Fi");
      wifiReset = true;
    }
    if (millis() - lastRetry > 5000) {
      lastRetry = millis();
      if (!connectHub()) logf("Estado de conexion: DISCONNECTED (reintento en 5 s)");
    }
    return;
  }
  if (offlineSince != 0) {
    logf("Reconectado tras %lu s sin hub", (millis() - offlineSince) / 1000);
    offlineSince = 0;
  }

  hub.loop();
  if (millis() - lastSend >= INTERVAL_MS || lastSend == 0) {
    lastSend = millis();
    sendTelemetry();
  }
}
