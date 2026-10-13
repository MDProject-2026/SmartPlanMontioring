"""Offline soil-moisture forecast and anomaly scoring from SQLite history.
AI output is advisory only; ESP32 firmware remains responsible for pump safety.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sklearn.metrics import mean_absolute_error

FEATURES = ['moisture','temperature','humidity','hour_sin','hour_cos','moisture_delta_15m']

def load_data(db_path):
    with sqlite3.connect(str(db_path)) as con:
        df = pd.read_sql_query('SELECT ts, moisture, temperature, humidity FROM readings ORDER BY ts', con)
    if df.empty: return df
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    df = df.drop_duplicates('ts').sort_values('ts').set_index('ts')
    df = df.resample('5min').mean(numeric_only=True).interpolate(limit=2).dropna(subset=['moisture'])
    hours = df.index.hour + df.index.minute/60
    df['hour_sin'] = np.sin(2*np.pi*hours/24); df['hour_cos'] = np.cos(2*np.pi*hours/24)
    df['moisture_delta_15m'] = df['moisture'].diff(3)
    # A label is moisture 30 minutes later; no leakage from future values into features.
    df['target_30m'] = df['moisture'].shift(-6)
    return df.dropna(subset=['moisture_delta_15m'])

def get_insights(db_path, threshold=35):
    df = load_data(db_path)
    if len(df) < 100:
        return {'available': False, 'message': f'Collect at least 100 usable 5-minute observations; currently {len(df)}.'}
    labeled = df.dropna(subset=['target_30m'])
    if len(labeled) < 50:
        return {'available': False, 'message': 'Not enough future-labelled observations for a reliable 30-minute forecast.'}
    X = labeled[FEATURES].replace([np.inf,-np.inf],np.nan).dropna()
    y = labeled.loc[X.index,'target_30m']
    if len(X) < 50: return {'available':False,'message':'Not enough complete sensor records.'}
    # Chronological holdout avoids reporting an optimistic random-split score.
    split = max(40, int(len(X)*0.8))
    if len(X)-split < 5: split = len(X)-5
    model = RandomForestRegressor(n_estimators=120, min_samples_leaf=2, random_state=42, n_jobs=-1)
    model.fit(X.iloc[:split], y.iloc[:split])
    mae = float(mean_absolute_error(y.iloc[split:], model.predict(X.iloc[split:])))
    final_model = RandomForestRegressor(n_estimators=120, min_samples_leaf=2, random_state=42, n_jobs=-1).fit(X,y)
    latest = df[FEATURES].replace([np.inf,-np.inf],np.nan).dropna().iloc[[-1]]
    forecast = float(np.clip(final_model.predict(latest)[0],0,100))
    anomaly_model = IsolationForest(contamination=0.04, random_state=42)
    scored = df[FEATURES].replace([np.inf,-np.inf],np.nan).dropna()
    anomaly_model.fit(scored)
    anomaly = bool(anomaly_model.predict(latest)[0] == -1)
    current = float(df['moisture'].dropna().iloc[-1])
    return {'available':True,'forecast_horizon_minutes':30,'current_moisture':round(current,1),
      'predicted_moisture_30m':round(forecast,1),'holdout_mae_percentage_points':round(mae,2),
      'anomaly_flag':anomaly,'threshold':int(threshold),
      'recommendation': ('Check sensors for unusual readings.' if anomaly else
       'Prepare to irrigate soon; forecast is below threshold.' if forecast < threshold else
       'No irrigation suggested by the current forecast.'),
      'training_observations':int(len(X)),'disclaimer':'Advisory analytics only. Validate locally; firmware interlocks always take priority.'}
