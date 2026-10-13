# AI-Driven Adaptive Plant Health & Intelligent Irrigation System

A local-first IoT project using an ESP32, capacitive soil-moisture sensor, DHT22, water-level sensor, relay-controlled pump, Flask/SQLite API, browser dashboard, and optional machine-learning analytics.

**Project status:** structured, testable project bundle / engineering starter. It is not a certified or physically validated product. Before operating a real pump, verify wiring, sensor type, relay logic, power budget, calibration, and firmware interlocks on your exact hardware.

## What is included

- `dashboard.html` — supplied HTML/CSS/JavaScript dashboard configured for ESP32 hardware only. Simulation mode and seeded demo telemetry are disabled.
- `backend/app.py` — Flask REST API matching the dashboard's `/api/readings/latest`, `/api/history`, `/api/waterings`, `/api/settings`, `/api/pump`, and `/api/mode` contract; SQLite persistence and device endpoints.
- `firmware/smart_plant/smart_plant.ino` — ESP32 Arduino firmware starter, sensor polling, Wi-Fi API reporting, command polling, local pump timeout and safety checks.
- `ai/predict.py` — optional 30-minute soil-moisture forecast, chronological holdout MAE, and Isolation Forest anomaly scoring. AI is advisory and does not control the pump.
- `tests/test_api.py` — API contract smoke tests.
- `docs/` — project presentations provided with the project materials.

## System architecture

`Sensors → ESP32 local safety logic → Wi-Fi/HTTP JSON → Flask API → SQLite → Dashboard + optional AI insights`

The ESP32 owns all real-time pump interlocks. The backend and AI can request actions but must never be the only safety layer. Do not expose the Flask server to the public internet.

## Hardware and power: read before wiring

Your listed pump is **6 V**, while your adapter is **5 V** and the LM2596 is a buck (step-down) converter. A buck converter cannot raise 5 V to 6 V. Do not assume this combination will run the pump reliably. Check the pump's rated voltage and startup/stall current, adapter current rating, relay DC contact rating, and wiring. Do not power the pump from an ESP32 pin or the ESP32 3.3 V rail. Use a suitable separate pump supply; grounds and relay/module wiring must follow the exact module documentation. If the available supply cannot safely meet the pump rating, leave the pump disconnected until the power issue is resolved.

- ESP32 GPIO is **not 5 V tolerant**. Ensure every sensor signal reaching an ESP32 input stays within 0–3.3 V.
- A water-level sensor may be analog or digital. The firmware currently assumes an analog-output sensor connected to GPIO35; if your module is digital, change the code accordingly.
- Verify relay polarity (`RELAY_ACTIVE_LOW`) and relay contact wiring with the pump disconnected first.
- Never handle wiring with the pump supply energized; keep electronics and connections dry.

## Quick start — dashboard and backend

### Windows

1. Install Python 3.10+.
2. Extract the ZIP into a writable folder.
3. Double-click `scripts/run_windows.bat` (or run it from Command Prompt). It creates `.venv`, installs dependencies, and starts Flask on `127.0.0.1:5000`.
4. Open `http://127.0.0.1:5000/` in a modern browser (the backend serves the dashboard and API from one origin).
5. The dashboard is hardware-only: there is no simulation/demo mode. Configure the ESP32 firmware `API_BASE` to the computer's LAN address (for example `http://192.168.1.20:5000`), and keep the dashboard API address on the same origin or set it in Settings. Until fresh ESP32 readings arrive, sensor values remain unavailable and pump controls are disabled. If the ESP32 stops posting for more than 30 seconds, the API treats its readings as stale and blocks pump-start commands.

### macOS / Linux

Run `bash scripts/run_mac_linux.sh`, then open `http://127.0.0.1:5000/` and set the API URL as above.

Health check: open `http://127.0.0.1:5000/api/health`. The launch scripts bind the API to the LAN interface so the ESP32 can reach it. Find the computer's private Wi-Fi/LAN IP and set that address in `API_BASE` in the firmware; never use `localhost` on the ESP32. Keep the computer and board on a trusted private network, allow port 5000 through the local firewall only as needed, and do not expose this unauthenticated API to the public internet. The dashboard itself should be opened on the same computer at `http://127.0.0.1:5000/`.

## ESP32 firmware setup

1. Install Arduino IDE and ESP32 board support.
2. Install libraries: **DHT sensor library** (Adafruit), **Adafruit Unified Sensor**, and **ArduinoJson**.
3. Open `firmware/smart_plant/smart_plant.ino`.
4. Set `WIFI_SSID`, `WIFI_PASSWORD`, and `API_BASE` to your actual Wi-Fi and server LAN address. `localhost` is wrong for the ESP32; it means the ESP32 itself.
5. Verify pin assignments and relay input polarity against your actual boards.
6. Calibrate moisture raw values: view `analogRead()` values in Serial Monitor with the probe in dry soil and in thoroughly wet soil, then set `MOISTURE_DRY_RAW` and `MOISTURE_WET_RAW`. Confirm the displayed percentage moves in the expected direction.
7. Calibrate the water-level sensor's empty/full readings. If your sensor output is digital, adapt the firmware instead of using the analog mapping.
8. First test with the pump disconnected. Confirm readings and mode. Then test the relay with a safe dummy load, verify OFF at boot, and only then connect the pump with an appropriate supply.
9. Test low-water, sensor-disconnected, Wi-Fi-loss, overwatering cutoff, and maximum-run-time scenarios. Do not leave unattended until all have passed.

## Operating behavior and limitations

- The backend stores real device readings and exposes 5-minute grouped history. It does not invent a device reading if none has arrived.
- ESP32 auto mode uses a moisture threshold and a minimum interval; the firmware applies a hard runtime cap and local safety checks.
- The firmware currently uses `maxManualSeconds` as a hard timeout for all pump runs, with `duration` as an additional cap. Tune only after testing.
- DHT22 failures yield invalid values; firmware should not initiate automatic watering without a valid temperature reading. Review this behavior before deployment.
- Simulation mode and seeded example telemetry have been disabled. The dashboard only displays actual readings submitted by the ESP32. The backend process must still run so the ESP32 has an API endpoint to contact; the dashboard does not operate as a simulated system when the board is disconnected.
- `GET /api/ai/insights` is available for optional analytics. It needs at least 100 usable observations and adequate future labels. It is not wired into the supplied dashboard UI yet; use the endpoint directly or integrate it as a separate UI card.
- The model's reported MAE is a chronological holdout estimate, not a guarantee of future accuracy. Report baseline comparisons and real measured results in your evaluation.
- This is a local student project starter, not a hardened commercial deployment: API authentication, HTTPS, user management, firmware OTA, formal threat modeling, and power-failure testing are not included.

## API summary

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Backend health |
| GET | `/api/readings/latest` | Latest reading (404 before first device report) |
| GET | `/api/history?hours=24` | 5-minute grouped sensor history |
| GET | `/api/waterings?hours=24` | Watering events |
| GET/POST | `/api/settings` | Read/update validated settings |
| POST | `/api/mode` | Set `auto` or `manual`; requests pump OFF on mode transition |
| POST | `/api/pump` | Queue `on`/`off`; ON requires manual mode and safety checks |
| GET | `/api/device/command` | ESP32 command/config polling |
| POST | `/api/device/reading` | ESP32 sensor telemetry ingestion |
| GET | `/api/ai/insights` | Optional forecast and anomaly analysis |

Example device telemetry JSON:

```json
{
  "moisture": 42.5,
  "temperature": 26.1,
  "humidity": 57.0,
  "pump": false,
  "mode": "auto",
  "reservoir": 78.0,
  "raw_moisture": 2110,
  "raw_water": 1540
}
```

## Run API tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Evaluation plan for a strong project demonstration

1. Collect real sensor readings for several days at a consistent interval.
2. Compare threshold-only control against adaptive/forecast-assisted recommendations under similar soil and plant conditions.
3. Report moisture forecast MAE, watering duration/estimated water use, time below the target moisture band, false anomaly alerts, and pump cutoff tests.
4. Show at least one fault-injection test: empty reservoir, disconnected sensor, network loss, or stuck-low moisture reading.
5. Evaluate only real ESP32 readings and avoid claiming model accuracy without a held-out real-data evaluation.
