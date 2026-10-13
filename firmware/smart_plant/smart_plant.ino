
/*
 * Smart Plant Health & Intelligent Irrigation System
 * Board: ESP32-WROOM-32
 * Framework: Arduino
 *
 * Features:
 * - Controlled Wi-Fi reconnection (10-second retry interval)
 * - Wi-Fi connection and IP address logging
 * - Capacitive soil moisture sensor
 * - DHT22 temperature and humidity sensor
 * - Optional analog water-level sensor
 * - Flask backend communication over HTTP + JSON
 * - Local pump safety interlocks and runtime limits
 *
 * SAFETY:
 * - Never drive the pump directly from an ESP32 GPIO.
 * - Verify relay polarity and DC current rating.
 * - Calibrate sensor readings before enabling automatic irrigation.
 * - Test first with the pump disconnected.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <math.h>

// =====================================================
// 1. WI-FI AND BACKEND CONFIGURATION
// =====================================================

// Replace with your actual 2.4 GHz Wi-Fi credentials.
// Avoid sharing real credentials publicly.
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Use the laptop's LOCAL IPv4 address, not localhost.
// Example only: replace with your actual laptop IP.
const char* API_BASE = "http://192.168.1.10:5000";

// Wi-Fi retry and HTTP intervals
constexpr unsigned long WIFI_RETRY_MS = 10000UL;
constexpr unsigned long HTTP_TIMEOUT_MS = 2500UL;
constexpr unsigned long SENSOR_INTERVAL_MS = 2000UL;
constexpr unsigned long POST_INTERVAL_MS = 10000UL;
constexpr unsigned long COMMAND_POLL_MS = 3000UL;

// =====================================================
// 2. HARDWARE PINS
// =====================================================

constexpr int PIN_MOISTURE = 34; // ADC1, compatible with Wi-Fi
constexpr int PIN_RELAY = 26;
constexpr int PIN_DHT = 27;
constexpr int PIN_WATER = 35;    // Analog sensor only

#define DHTTYPE DHT22
DHT dht(PIN_DHT, DHTTYPE);

// =====================================================
// 3. SENSOR CALIBRATION
// =====================================================

// Calibrate using readings from your actual soil and sensor.
int MOISTURE_DRY_RAW = 3000;
int MOISTURE_WET_RAW = 1400;

// Verify whether your relay input is active LOW.
bool RELAY_ACTIVE_LOW = true;

// Set false if your water-level sensor is not installed
// or if it is not an analog-output module.
bool WATER_SENSOR_ENABLED = true;

int WATER_EMPTY_RAW = 3000;
int WATER_FULL_RAW = 1200;

// =====================================================
// 4. IRRIGATION SETTINGS
// =====================================================

int thresholdPct = 35;
int overLimitPct = 85;

// Maximum automatic watering duration, seconds
int runSeconds = 8;

// Minimum delay between automatic watering cycles
int waitSeconds = 300;

// Maximum manual pump runtime, seconds
int maxManualSeconds = 15;

String mode = "auto";

bool pumpOn = false;
bool wifiWasConnected = false;

// =====================================================
// 5. TIMERS AND SENSOR VALUES
// =====================================================

unsigned long lastReconnectAttempt = 0;
unsigned long pumpStarted = 0;
unsigned long lastRead = 0;
unsigned long lastPost = 0;
unsigned long lastCommandPoll = 0;
unsigned long lastAutoWater = 0;

int lastCommandId = 0;

float moisturePct = NAN;
float tempC = NAN;
float humidityPct = NAN;
float reservoirPct = NAN;

int rawMoisture = 0;
int rawWater = 0;

// =====================================================
// 6. GENERAL HELPERS
// =====================================================

bool validFloat(float value) {
  return !isnan(value) && isfinite(value);
}

float pctFromRaw(int raw, int dryRaw, int wetRaw) {
  if (dryRaw == wetRaw) {
    return NAN;
  }

  float percentage =
      100.0f * (float)(dryRaw - raw) /
      (float)(dryRaw - wetRaw);

  return constrain(percentage, 0.0f, 100.0f);
}

bool reservoirSafe() {
  if (!WATER_SENSOR_ENABLED) {
    return true;
  }

  return validFloat(reservoirPct) &&
         reservoirPct > 5.0f;
}

// =====================================================
// 7. PUMP CONTROL
// =====================================================

void setPump(bool on) {
  // Do not start a pump without the required safety checks.
  if (on) {
    if (WiFi.status() != WL_CONNECTED ||
        !validFloat(moisturePct) ||
        moisturePct >= overLimitPct ||
        !reservoirSafe()) {
      Serial.println(
          "Pump start rejected: safety interlock.");
      return;
    }

    // Use the appropriate limit for the selected mode.
    unsigned long limitSeconds =
        (mode == "manual")
            ? (unsigned long)maxManualSeconds
            : (unsigned long)runSeconds;

    // Avoid restarting the pump if it is already running.
    if (pumpOn) {
      return;
    }

    pumpStarted = millis();

    // Drive relay to the ON state.
    digitalWrite(
        PIN_RELAY,
        RELAY_ACTIVE_LOW ? LOW : HIGH);

    pumpOn = true;

    Serial.printf(
        "Pump ON; mode=%s; maximum runtime=%lu seconds\n",
        mode.c_str(),
        limitSeconds);
  } else {
    // Drive relay to the OFF state.
    digitalWrite(
        PIN_RELAY,
        RELAY_ACTIVE_LOW ? HIGH : LOW);

    if (pumpOn) {
      Serial.println("Pump OFF");
    }

    pumpOn = false;
  }
}

// =====================================================
// 8. WIFI STATUS AND RECONNECTION
// =====================================================

void handleWiFi() {
  wl_status_t status = WiFi.status();

  if (status == WL_CONNECTED) {
    if (!wifiWasConnected) {
      wifiWasConnected = true;

      Serial.println();
      Serial.println("Wi-Fi connected!");

      Serial.print("SSID: ");
      Serial.println(WiFi.SSID());

      Serial.print("ESP32 IP address: ");
      Serial.println(WiFi.localIP());

      Serial.print("Signal strength: ");
      Serial.print(WiFi.RSSI());
      Serial.println(" dBm");

      Serial.println(
          "Check that the laptop and ESP32 "
          "can communicate over the local network.");
    }

    return;
  }

  if (wifiWasConnected) {
    Serial.printf(
        "Wi-Fi connection lost. Status code: %d\n",
        (int)status);

    wifiWasConnected = false;
  }

  // Fail-safe: switch pump off when disconnected.
  if (pumpOn) {
    setPump(false);
  }

  unsigned long now = millis();

  // Do not repeatedly call WiFi.reconnect() in every loop.
  if (now - lastReconnectAttempt >= WIFI_RETRY_MS) {
    lastReconnectAttempt = now;

    Serial.println("Retrying Wi-Fi connection...");
    WiFi.reconnect();
  }
}

// =====================================================
// 9. SEND SENSOR READINGS TO FLASK
// =====================================================

bool postReading() {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  if (!validFloat(moisturePct)) {
    return false;
  }

  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);

  String url =
      String(API_BASE) + "/api/device/reading";

  if (!http.begin(url)) {
    Serial.println("Could not initialize HTTP client.");
    return false;
  }

  http.addHeader(
      "Content-Type",
      "application/json");

  StaticJsonDocument<384> doc;

  doc["moisture"] = moisturePct;

  if (validFloat(tempC)) {
    doc["temperature"] = tempC;
  } else {
    doc["temperature"] = nullptr;
  }

  if (validFloat(humidityPct)) {
    doc["humidity"] = humidityPct;
  } else {
    doc["humidity"] = nullptr;
  }

  doc["pump"] = pumpOn;
  doc["mode"] = mode;
  doc["raw_moisture"] = rawMoisture;
  doc["raw_water"] = rawWater;

  if (WATER_SENSOR_ENABLED &&
      validFloat(reservoirPct)) {
    doc["reservoir"] = reservoirPct;
  } else {
    doc["reservoir"] = nullptr;
  }

  String payload;
  serializeJson(doc, payload);

  int code = http.POST(payload);

  if (code >= 200 && code < 300) {
    Serial.printf(
        "Reading uploaded successfully (HTTP %d)\n",
        code);
  } else {
    Serial.printf(
        "Reading upload failed (HTTP %d): %s\n",
        code,
        http.errorToString(code).c_str());
  }

  http.end();

  return code >= 200 && code < 300;
}

// =====================================================
// 10. APPLY BACKEND SETTINGS
// =====================================================

void applyServerSettings(JsonObject settings) {
  if (settings["threshold"].is<int>()) {
    thresholdPct =
        constrain(settings["threshold"].as<int>(), 5, 90);
  }

  if (settings["overLimit"].is<int>()) {
    overLimitPct =
        constrain(settings["overLimit"].as<int>(), 20, 100);
  }

  if (settings["duration"].is<int>()) {
    runSeconds =
        constrain(settings["duration"].as<int>(), 1, 30);
  }

  if (settings["wait"].is<int>()) {
    waitSeconds =
        constrain(settings["wait"].as<int>(), 30, 86400);
  }

  if (settings["maxManual"].is<int>()) {
    maxManualSeconds =
        constrain(settings["maxManual"].as<int>(), 1, 30);
  }
}

// =====================================================
// 11. POLL SERVER COMMANDS
// =====================================================

void pollCommand() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);

  String url =
      String(API_BASE) + "/api/device/command";

  if (!http.begin(url)) {
    Serial.println("Could not initialize command request.");
    return;
  }

  int code = http.GET();

  if (code == 200) {
    StaticJsonDocument<512> doc;

    DeserializationError error =
        deserializeJson(doc, http.getString());

    if (!error) {
      String receivedMode =
          doc["mode"] | "auto";

      // Accept only known modes.
      if (receivedMode == "auto" ||
          receivedMode == "manual") {
        mode = receivedMode;
      } else {
        mode = "auto";
      }

      applyServerSettings(
          doc["settings"].as<JsonObject>());

      int id = doc["id"] | 0;
      String commandState = doc["state"] | "off";

      if (id > lastCommandId) {
        lastCommandId = id;

        if (commandState == "off") {
          setPump(false);
        } else if (commandState == "on") {
          // Manual ON commands only.
          if (mode == "manual") {
            setPump(true);
          } else {
            Serial.println(
                "Manual ON rejected: system is not in manual mode.");
          }
        }
      }
    } else {
      Serial.printf(
          "Command JSON error: %s\n",
          error.c_str());
    }
  } else if (code < 0) {
    Serial.printf(
        "Command request failed: %s\n",
        http.errorToString(code).c_str());
  }

  http.end();
}

// =====================================================
// 12. SENSOR READING
// =====================================================

void readSensors() {
  rawMoisture = analogRead(PIN_MOISTURE);

  moisturePct = pctFromRaw(
      rawMoisture,
      MOISTURE_DRY_RAW,
      MOISTURE_WET_RAW);

  tempC = dht.readTemperature();
  humidityPct = dht.readHumidity();

  if (WATER_SENSOR_ENABLED) {
    rawWater = analogRead(PIN_WATER);

    reservoirPct = pctFromRaw(
        rawWater,
        WATER_EMPTY_RAW,
        WATER_FULL_RAW);
  } else {
    reservoirPct = NAN;
  }

  Serial.printf(
      "Soil raw=%d, moisture=%.1f%%\n",
      rawMoisture,
      moisturePct);

  if (validFloat(tempC) &&
      validFloat(humidityPct)) {
    Serial.printf(
        "Temperature=%.1f C, humidity=%.1f%%\n",
        tempC,
        humidityPct);
  } else {
    Serial.println("DHT22 reading unavailable.");
  }

  if (WATER_SENSOR_ENABLED) {
    Serial.printf(
        "Water raw=%d, reservoir=%.1f%%\n",
        rawWater,
        reservoirPct);
  }
}

// =====================================================
// 13. LOCAL PUMP SAFETY
// =====================================================

void enforcePumpSafety(unsigned long now) {
  if (!pumpOn) {
    return;
  }

  unsigned long elapsed =
      now - pumpStarted;

  unsigned long maximumRuntime =
      (mode == "manual")
          ? (unsigned long)maxManualSeconds * 1000UL
          : (unsigned long)runSeconds * 1000UL;

  if (elapsed >= maximumRuntime) {
    Serial.println("Pump stopped: runtime limit reached.");
    setPump(false);
    return;
  }

  if (!validFloat(moisturePct) ||
      moisturePct >= overLimitPct) {
    Serial.println("Pump stopped: moisture safety limit.");
    setPump(false);
    return;
  }

  if (!reservoirSafe()) {
    Serial.println("Pump stopped: reservoir low or invalid.");
    setPump(false);
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Pump stopped: Wi-Fi disconnected.");
    setPump(false);
  }
}

// =====================================================
// 14. AUTOMATIC IRRIGATION
// =====================================================

void handleAutomaticIrrigation(unsigned long now) {
  if (pumpOn || mode != "auto") {
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  if (!validFloat(moisturePct) ||
      !validFloat(tempC)) {
    return;
  }

  if (moisturePct >= thresholdPct) {
    return;
  }

  if (!reservoirSafe()) {
    return;
  }

  if (now - lastAutoWater <
      (unsigned long)waitSeconds * 1000UL) {
    return;
  }

  Serial.println(
      "Automatic irrigation condition met.");

  setPump(true);

  if (pumpOn) {
    lastAutoWater = now;
  }
}

// =====================================================
// 15. SETUP
// =====================================================

void setup() {
  Serial.begin(115200);
  delay(300);

  // Set a safe relay state as early as possible.
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(
      PIN_RELAY,
      RELAY_ACTIVE_LOW ? HIGH : LOW);
  pumpOn = false;

  analogReadResolution(12);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);

  Serial.println();
  Serial.println("====================================");
  Serial.println("Smart Plant System starting");
  Serial.println("Pump initialized OFF");
  Serial.println("====================================");

  Serial.print("Connecting to Wi-Fi SSID: ");
  Serial.println(WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Allow the first attempt to begin.
  lastReconnectAttempt = millis();
}

// =====================================================
// 16. MAIN LOOP
// =====================================================

void loop() {
  unsigned long now = millis();

  // Handle connection changes and spaced retries.
  handleWiFi();

  // Read sensors at the configured interval.
  if (now - lastRead >= SENSOR_INTERVAL_MS) {
    lastRead = now;
    readSensors();
  }

  // Poll server commands.
  if (now - lastCommandPoll >= COMMAND_POLL_MS) {
    lastCommandPoll = now;
    pollCommand();
  }

  // Enforce local safety independently of the server.
  enforcePumpSafety(now);

  // Automatic watering remains local to the ESP32.
  handleAutomaticIrrigation(now);

  // Send real sensor data to the backend.
  if (now - lastPost >= POST_INTERVAL_MS) {
    lastPost = now;

    if (WiFi.status() == WL_CONNECTED) {
      postReading();
    }
  }

  delay(20);
}