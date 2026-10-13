"""Local-first Flask API for Smart Plant monitoring and irrigation.

Safety model: this server queues requests; ESP32 firmware owns pump interlocks and timeouts.
Bind to 127.0.0.1 by default. Do not expose to the public internet.
"""
from __future__ import annotations
import json, os, sqlite3, threading, time
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("SMART_PLANT_DB", ROOT / "data" / "smart_plant.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
_lock = threading.RLock()
_state = {"mode": "auto", "pump": False, "reservoir": None, "lastWatered": None, "updatedAt": None}
_command = {"id": 0, "state": "off", "created_at": 0}
_DEFAULTS = {"threshold": 35, "duration": 8, "wait": 300, "maxManual": 15, "overLimit": 85, "reservoirWarn": 20}


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS readings (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
          moisture REAL NOT NULL, temperature REAL, humidity REAL,
          pump INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'auto',
          reservoir REAL, raw_moisture INTEGER, raw_water INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
        CREATE TABLE IF NOT EXISTS waterings (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
          dur INTEGER NOT NULL, src TEXT NOT NULL DEFAULT 'Auto'
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL NOT NULL);
        ''')
        for k, v in _DEFAULTS.items(): c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (k,v))


def now_iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat().replace('+00:00','Z')


def settings():
    with db() as c:
        d = dict(_DEFAULTS)
        d.update({r['key']: r['value'] for r in c.execute('SELECT key,value FROM settings')})
    for k in ('threshold','duration','wait','maxManual','overLimit','reservoirWarn'): d[k] = int(d[k])
    return d


def latest_row():
    with db() as c: r = c.execute('SELECT * FROM readings ORDER BY ts DESC LIMIT 1').fetchone()
    return dict(r) if r else None

@app.after_request
def no_cache(resp):
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp

@app.get('/')
def dashboard():
    # Serve the dashboard from the same origin as the API to avoid file:// CORS issues.
    return send_from_directory(ROOT, 'dashboard.html')

@app.get('/api/health')
def health():
    r = latest_row()
    fresh = bool(r and time.time() - r['ts'] <= 30)
    return jsonify(ok=True, service='smart-plant-api', database=str(DB_PATH), device_seen=fresh, device_reading_age_seconds=round(time.time()-r['ts'], 1) if r else None, server_time=now_iso())

@app.get('/api/readings/latest')
def api_latest():
    r = latest_row()
    if not r: return jsonify(error='No ESP32 readings received yet. Check ESP32 power, Wi-Fi, and API_BASE.'), 404
    # Do not present stale telemetry as a live device connection.
    if time.time() - r['ts'] > 30:
        return jsonify(error='ESP32 telemetry is stale. Live readings are unavailable until the board reconnects.'), 503
    updated = r['ts'] * 1000
    with _lock:
        _state.update(pump=bool(r['pump']), mode=r['mode'], reservoir=r['reservoir'], updatedAt=updated)
    return jsonify(moisture=r['moisture'], temperature=r['temperature'], humidity=r['humidity'], pump=bool(r['pump']), mode=r['mode'], reservoir=r['reservoir'], lastWatered=now_iso(_state['lastWatered']) if _state['lastWatered'] else None, updatedAt=updated)

@app.get('/api/history')
def api_history():
    try: hours = max(1, min(168, int(request.args.get('hours', 24))))
    except ValueError: return jsonify(error='hours must be an integer from 1 to 168'), 400
    cutoff = time.time() - hours * 3600
    with db() as c:
        rows = c.execute('''SELECT CAST((ts / 300) AS INTEGER)*300 AS bucket,
          AVG(moisture) moisture, AVG(temperature) temperature, AVG(humidity) humidity
          FROM readings WHERE ts >= ? GROUP BY bucket ORDER BY bucket''', (cutoff,)).fetchall()
    return jsonify([{'t': int(r['bucket']*1000), 'moisture': round(r['moisture'],2), 'temperature': round(r['temperature'],2) if r['temperature'] is not None else None, 'humidity': round(r['humidity'],2) if r['humidity'] is not None else None} for r in rows])

@app.get('/api/waterings')
def api_waterings():
    try: hours = max(1, min(168, int(request.args.get('hours',24))))
    except ValueError: return jsonify(error='hours must be an integer from 1 to 168'), 400
    with db() as c: rows = c.execute('SELECT ts,dur,src FROM waterings WHERE ts >= ? ORDER BY ts DESC', (time.time()-hours*3600,)).fetchall()
    return jsonify([{'t': int(r['ts']*1000), 'dur': r['dur'], 'src': r['src']} for r in rows])

@app.route('/api/settings', methods=['GET','POST'])
def api_settings():
    if request.method == 'GET': return jsonify(settings())
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error='Expected a JSON object'), 400
    current = settings(); proposed = dict(current)
    for k in _DEFAULTS:
        if k in body:
            try: proposed[k] = int(body[k])
            except (ValueError, TypeError): return jsonify(error=f'{k} must be an integer'), 400
    limits = {'threshold':(5,90), 'duration':(1,30), 'wait':(30,86400), 'maxManual':(1,30), 'overLimit':(20,100), 'reservoirWarn':(5,80)}
    for k,(lo,hi) in limits.items():
        if not lo <= proposed[k] <= hi: return jsonify(error=f'{k} must be between {lo} and {hi}'), 400
    if proposed['overLimit'] < proposed['threshold'] + 10: return jsonify(error='overLimit must be at least 10 percentage points above threshold'), 400
    with db() as c:
        c.executemany('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', [(k,proposed[k]) for k in _DEFAULTS])
    return jsonify(proposed)

@app.post('/api/mode')
def api_mode():
    body = request.get_json(silent=True) or {}; mode = body.get('mode')
    if mode not in ('auto','manual'): return jsonify(error="mode must be 'auto' or 'manual'"), 400
    r = latest_row()
    if not r or time.time() - r['ts'] > 30: return jsonify(error='Fresh ESP32 telemetry is required to change irrigation mode'), 409
    with _lock: _state['mode'] = mode
    queue_command('off') # mode changes always request a safe pump-off transition
    return jsonify(mode=mode, queued=True)

@app.post('/api/pump')
def api_pump():
    body = request.get_json(silent=True) or {}; state = body.get('state')
    if state not in ('on','off'): return jsonify(error="state must be 'on' or 'off'"), 400
    with _lock: mode = _state['mode']
    r = latest_row()
    if state == 'on':
        if mode != 'manual': return jsonify(error='Switch to Manual mode before starting the pump'), 409
        if not r: return jsonify(error='No ESP32 reading available; pump start blocked'), 409
        if time.time() - r['ts'] > 30: return jsonify(error='ESP32 connection is stale; pump start blocked'), 409
        if r['reservoir'] is not None and r['reservoir'] <= 5: return jsonify(error='Low reservoir: pump start blocked'), 409
        if r['moisture'] >= settings()['overLimit']: return jsonify(error='Soil is above the overwatering limit'), 409
    cmd = queue_command(state)
    return jsonify(queued=True, command_id=cmd['id'], state=state)

def queue_command(state):
    global _command
    with _lock:
        _command = {'id': _command['id']+1, 'state': state, 'created_at': time.time()}
        return dict(_command)

@app.get('/api/device/command')
def device_command():
    with _lock: cmd = dict(_command)
    return jsonify(id=cmd['id'], state=cmd['state'], mode=_state['mode'], settings=settings())

@app.post('/api/device/reading')
def device_reading():
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error='Expected JSON object'), 400
    try:
        moisture = float(body['moisture']); pump = bool(body.get('pump',False)); mode = body.get('mode','auto')
        temp = None if body.get('temperature') is None else float(body['temperature'])
        hum = None if body.get('humidity') is None else float(body['humidity'])
        reservoir = None if body.get('reservoir') is None else float(body['reservoir'])
        raw_m = None if body.get('raw_moisture') is None else int(body['raw_moisture'])
        raw_w = None if body.get('raw_water') is None else int(body['raw_water'])
    except (KeyError, ValueError, TypeError): return jsonify(error='moisture is required and numeric; optional sensor fields must be numeric'), 400
    if not 0 <= moisture <= 100: return jsonify(error='moisture must be 0..100'), 400
    if mode not in ('auto','manual'): return jsonify(error='mode must be auto or manual'), 400
    if temp is not None and not -20 <= temp <= 80: return jsonify(error='temperature outside plausible range'), 400
    if hum is not None and not 0 <= hum <= 100: return jsonify(error='humidity must be 0..100'), 400
    if reservoir is not None and not 0 <= reservoir <= 100: return jsonify(error='reservoir must be 0..100'), 400
    ts = time.time()
    with _lock:
        was_pump = _state['pump']; _state.update(pump=pump, mode=mode, reservoir=reservoir, updatedAt=ts*1000)
        if was_pump and not pump: _state['lastWatered'] = ts
    with db() as c:
        c.execute('INSERT INTO readings(ts,moisture,temperature,humidity,pump,mode,reservoir,raw_moisture,raw_water) VALUES(?,?,?,?,?,?,?,?,?)', (ts,moisture,temp,hum,int(pump),mode,reservoir,raw_m,raw_w))
        if was_pump and not pump: c.execute('INSERT INTO waterings(ts,dur,src) VALUES(?,?,?)', (ts, max(1,int(body.get('last_run_seconds',1))), body.get('pump_source','Auto')))
    return jsonify(ok=True, server_time=now_iso(ts), command_id=_command['id'])

@app.get('/api/ai/insights')
def ai_insights():
    try:
        from ai.predict import get_insights
        return jsonify(get_insights(DB_PATH, settings()['threshold']))
    except Exception as e:
        app.logger.exception('AI insights unavailable')
        return jsonify(available=False, message='AI insight model needs enough valid historical readings.', detail=str(e)), 200

init_db()
if __name__ == '__main__':
    host = os.environ.get('SMART_PLANT_HOST','0.0.0.0')
    port = int(os.environ.get('SMART_PLANT_PORT','5000'))
    app.run(host=host, port=port, debug=False, threaded=True)
