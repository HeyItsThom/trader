#!/usr/bin/env python3
"""
Boston Temperature Tracker — Flask API backend
Exposes all data-fetching logic as JSON endpoints for the React frontend.
"""

import re
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional
import zoneinfo

from flask import Flask, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# ── Constants ──────────────────────────────────────────────────────────────────
STATION_ID     = "KBOS"
NWS_OBS_URL    = f"https://api.weather.gov/stations/{STATION_ID}/observations"
NWS_POINTS     = "https://api.weather.gov/points/42.3601,-71.0589"
METAR_URL      = f"https://tgftp.weather.gov/data/observations/metar/stations/{STATION_ID}.TXT"
AVWX_METAR_URL = "https://aviationweather.gov/api/data/metar"
HEADERS        = {"User-Agent": "BostonTempTracker/1.0 (personal trading tool)"}
EASTERN_TZ     = zoneinfo.ZoneInfo("America/New_York")

PEAK_HOUR   = {1:13,2:13,3:14,4:14,5:14,6:15,7:15,8:15,9:14,10:14,11:13,12:13}
PEAK_WINDOW = (11, 18)

MONTHLY_AVG_HIGH = {1:36.4,2:38.9,3:46.6,4:56.5,5:66.7,6:76.1,
                    7:81.8,8:79.6,9:72.4,10:62.0,11:52.6,12:40.8}

_SIGMA_FACTOR = 1.5


# ── Helpers ────────────────────────────────────────────────────────────────────
def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_exceed(threshold: float, pred, current_high: float) -> float:
    if current_high >= threshold:
        return 1.0
    sigma = pred["spread"] / _SIGMA_FACTOR
    if sigma <= 0:
        return 1.0 if pred["point"] >= threshold else 0.0
    z = (threshold - pred["point"]) / sigma
    return 1.0 - norm_cdf(z)


def trend_confidence(times: list, temps: list):
    if len(times) < 4:
        return "Low", "few obs"
    v30  = velocity(times, temps, 0.5)
    v60  = velocity(times, temps, 1.0)
    v120 = velocity(times, temps, 2.0)
    v180 = velocity(times, temps, 3.0)  # extra window when hourly data is sparse
    vels = [v for v in [v30, v60, v120, v180] if v is not None]
    if len(vels) < 2:
        return "Low", "few obs"
    dirs     = [1 if v > 0.3 else -1 if v < -0.3 else 0 for v in vels]
    non_flat = [d for d in dirs if d != 0]
    unique   = set(non_flat)
    if len(unique) > 1:  return "Low",    "conflicting"
    if len(unique) == 0: return "Medium", "near-flat"
    magnitudes   = [abs(v) for v in vels]
    consistency  = min(magnitudes) / max(magnitudes) if max(magnitudes) > 0.1 else 0
    if consistency >= 0.55: return "High",   "consistent"
    if consistency >= 0.25: return "Medium", "moderate"
    return "Low", "variable rate"


def velocity(times: list, temps: list, window_hr: float = 1.0) -> Optional[float]:
    if len(times) < 2:
        return None
    cutoff = times[-1] - timedelta(hours=window_hr)
    pairs  = [(t, v) for t, v in zip(times, temps) if t >= cutoff]
    if len(pairs) < 2:
        return None
    dt_hr = (pairs[-1][0] - pairs[0][0]).total_seconds() / 3600
    return None if dt_hr < 0.05 else (pairs[-1][1] - pairs[0][1]) / dt_hr


def peak_status(now_et: datetime) -> dict:
    h = now_et.hour + now_et.minute / 60
    if h < PEAK_WINDOW[0]:
        return {"open": False, "label": f"Opens in {PEAK_WINDOW[0]-h:.1f}h"}
    if h < PEAK_WINDOW[1]:
        return {"open": True,  "label": f"OPEN  {PEAK_WINDOW[1]-h:.1f}h left"}
    return {"open": False, "label": "Closed"}


# ── Prediction engine ──────────────────────────────────────────────────────────
def predict_high(obs_times, obs_temps, fcst_times, fcst_temps, now_et) -> Optional[dict]:
    if not obs_temps:
        return None

    cur_high  = max(obs_temps)
    month     = now_et.month
    hour_frac = now_et.hour + now_et.minute / 60
    peak_hr   = PEAK_HOUR[month]

    if hour_frac >= PEAK_WINDOW[1]:
        return dict(point=cur_high, low=cur_high-0.3, high=cur_high+0.3,
                    spread=0.3, confidence="Locked",
                    detail=f"Peak window closed — {cur_high:.1f}°F is the day's high")

    win_dur     = PEAK_WINDOW[1] - PEAK_WINDOW[0]
    win_elapsed = max(0.0, min(1.0, (hour_frac - PEAK_WINDOW[0]) / win_dur))

    today     = now_et.date()
    fcst_vals = [v for t, v in zip(fcst_times, fcst_temps) if t.date() == today]
    fcst_high = max(fcst_vals) if fcst_vals else None

    vel = velocity(obs_times, obs_temps, 1.0) or velocity(obs_times, obs_temps, 2.0) or 0.0
    hours_to_peak = max(0.0, peak_hr - hour_frac)
    if vel < 0 and hour_frac > peak_hr:
        hours_to_peak = 0.0
    trend_high = max(cur_high, obs_temps[-1] + vel * hours_to_peak)

    if hour_frac < PEAK_WINDOW[0]:
        wf, wt = 0.80, 0.20
    elif hour_frac < peak_hr:
        pre_frac = (hour_frac - PEAK_WINDOW[0]) / max(peak_hr - PEAK_WINDOW[0], 1)
        wf = 0.75 - 0.30 * pre_frac
        wt = 1.0 - wf
    else:
        post_frac = (hour_frac - peak_hr) / max(PEAK_WINDOW[1] - peak_hr, 1)
        wf = max(0.10, 0.45 - 0.35 * post_frac)
        wt = 1.0 - wf

    point = (wf * fcst_high + wt * trend_high) if fcst_high else trend_high
    point = max(point, cur_high)

    if fcst_high is not None:
        disagree = abs(fcst_high - trend_high)
        if disagree <= 1.5:
            conf, spread = "High",   1.5
        elif disagree <= 3.5:
            conf, spread = "Medium", 2.5
        else:
            conf, spread = "Low",    4.0
    else:
        conf, spread = "Low", 4.0

    tighten = 1.0 - 0.45 * win_elapsed
    spread  = round(spread * tighten * 2) / 2
    if conf == "High" and abs(hour_frac - peak_hr) < 1.0 and fcst_high:
        spread = min(spread, 1.0)
    spread = max(spread, 0.5)

    detail_parts = []
    if fcst_high:
        detail_parts.append(f"NWS {fcst_high:.1f}°  ×{wf:.2f}")
    detail_parts.append(f"trend {trend_high:.1f}°  vel {vel:+.1f}°/hr  ×{wt:.2f}")
    if win_elapsed > 0:
        detail_parts.append(f"window {win_elapsed*100:.0f}% elapsed")

    return dict(
        point=point, low=point-spread, high=point+spread,
        spread=spread, confidence=conf, detail="   ·   ".join(detail_parts),
    )


# ── METAR parser ───────────────────────────────────────────────────────────────
def parse_metar_temp(raw: str):
    lines      = raw.strip().splitlines()
    metar_line = lines[-1] if len(lines) >= 2 else raw.strip()

    obs_dt       = None
    obs_time_str = None
    if len(lines) >= 2:
        try:
            dt    = datetime.strptime(lines[0].strip(), "%Y/%m/%d %H:%M")
            obs_dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(EASTERN_TZ)
            obs_time_str = obs_dt.strftime("%I:%M %p ET").lstrip("0")
        except Exception:
            pass

    t_match = re.search(r"\bT([01])(\d{3})([01]\d{3})\b", metar_line)
    if t_match:
        sign   = -1 if t_match.group(1) == "1" else 1
        temp_c = sign * int(t_match.group(2)) / 10.0
        return c_to_f(temp_c), obs_time_str, obs_dt

    td_match = re.search(r"\b(M?\d{2})/(M?\d{2})\b", metar_line)
    if td_match:
        raw_t  = td_match.group(1)
        sign   = -1 if raw_t.startswith("M") else 1
        temp_c = sign * int(raw_t.replace("M", ""))
        return c_to_f(temp_c), obs_time_str, obs_dt

    return None, obs_time_str, obs_dt


# ── Data fetchers ──────────────────────────────────────────────────────────────
def fetch_latest_observation():
    """Fetch the single most-current NWS observation (fresher than the list endpoint)."""
    try:
        resp = requests.get(f"{NWS_OBS_URL}/latest", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        props  = resp.json().get("properties", {})
        temp_c = props.get("temperature", {}).get("value")
        ts     = props.get("timestamp")
        if temp_c is None or ts is None:
            return None, None
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(EASTERN_TZ)
        return dt, c_to_f(temp_c)
    except Exception:
        return None, None


def fetch_observations():
    try:
        resp = requests.get(NWS_OBS_URL, headers=HEADERS, params={"limit": 150}, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Cannot reach NWS API: {exc}") from exc
    today = datetime.now(EASTERN_TZ).date()
    times, temps = [], []
    for feat in reversed(resp.json().get("features", [])):
        props  = feat.get("properties", {})
        temp_c = props.get("temperature", {}).get("value")
        ts     = props.get("timestamp")
        if temp_c is None or ts is None:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(EASTERN_TZ)
        if dt.date() == today:
            times.append(dt)
            temps.append(c_to_f(temp_c))
    return times, temps


def fetch_forecast():
    try:
        r1       = requests.get(NWS_POINTS, headers=HEADERS, timeout=15)
        r1.raise_for_status()
        fcst_url = r1.json()["properties"]["forecastHourly"]
        r2       = requests.get(fcst_url, headers=HEADERS, timeout=15)
        r2.raise_for_status()
        today    = datetime.now(EASTERN_TZ).date()
        times, temps = [], []
        for p in r2.json()["properties"]["periods"]:
            dt = datetime.fromisoformat(p["startTime"]).astimezone(EASTERN_TZ)
            if dt.date() < today:
                continue
            if dt.date() > today:
                break
            tf = p["temperature"]
            if p["temperatureUnit"] == "C":
                tf = c_to_f(tf)
            times.append(dt)
            temps.append(tf)
        return times, temps, (max(temps) if temps else None)
    except Exception:
        return [], [], None


def fetch_metar():
    try:
        resp   = requests.get(METAR_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return parse_metar_temp(resp.text)
    except Exception:
        return None, None, None


def fetch_iem_history():
    """Fetch ASOS observations from Iowa Environmental Mesonet.

    IEM archives all ASOS reports including SPECI (special) observations
    that occur outside the routine hourly cycle when significant weather
    changes happen.  This gives sub-hourly resolution for trend calculation
    and is the richest freely available source of KBOS temperature data.
    """
    try:
        resp = requests.get(
            "https://mesonet.agron.iastate.edu/api/1/observations.json",
            params={"station": STATION_ID, "hours": 24},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        today = datetime.now(EASTERN_TZ).date()
        times, temps = [], []
        for obs in resp.json().get("data", []):
            tmpf     = obs.get("tmpf")
            valid_ts = obs.get("valid")
            if tmpf is None or valid_ts is None:
                continue
            dt = datetime.fromisoformat(valid_ts).astimezone(EASTERN_TZ)
            if dt.date() == today:
                times.append(dt)
                temps.append(float(tmpf))
        if times:
            pairs = sorted(zip(times, temps))
            times, temps = zip(*pairs)
            times, temps = list(times), list(temps)
        return times, temps
    except Exception:
        return [], []


def fetch_metar_history():
    try:
        resp = requests.get(
            AVWX_METAR_URL,
            params={"ids": STATION_ID, "format": "json", "hours": 24},
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        today = datetime.now(EASTERN_TZ).date()
        times, temps = [], []
        for obs in resp.json():
            temp_c   = obs.get("temp")
            obs_time = obs.get("obsTime")
            if temp_c is None or obs_time is None:
                continue
            dt = datetime.fromtimestamp(
                obs_time, tz=zoneinfo.ZoneInfo("UTC")
            ).astimezone(EASTERN_TZ)
            if dt.date() == today:
                times.append(dt)
                temps.append(c_to_f(temp_c))
        if times:
            pairs = sorted(zip(times, temps))
            times, temps = zip(*pairs)
            times, temps = list(times), list(temps)
        return times, temps
    except Exception:
        return [], []


# ── API routes ────────────────────────────────────────────────────────────────
@app.route("/api/data")
def get_data():
    try:
        obs_times, obs_temps              = fetch_observations()
        metar_temp, metar_time, metar_dt  = fetch_metar()
        mh_times, mh_temps               = fetch_metar_history()
        iem_times, iem_temps             = fetch_iem_history()
        fcst_times, fcst_temps, fcst_high = fetch_forecast()
        latest_obs_time, latest_obs_temp  = fetch_latest_observation()

        now_et = datetime.now(EASTERN_TZ)

        # Merge all sources into a unified series.
        # IEM ASOS includes SPECI (special) observations, giving sub-hourly
        # resolution during significant weather changes — better trend data.
        # The live METAR from tgftp.weather.gov is the same feed WU uses and is
        # the freshest available reading — inject it so day_high reflects what
        # WU will report as the historic daily high for the day.
        latest_pair = [(latest_obs_time, latest_obs_temp)] if latest_obs_time else []
        metar_pair  = (
            [(metar_dt, metar_temp)]
            if metar_temp is not None and metar_dt is not None
               and metar_dt.date() == now_et.date()
            else []
        )
        combined = sorted(
            [(t, v) for t, v in zip(obs_times, obs_temps)] +
            [(t, v) for t, v in zip(mh_times, mh_temps)] +
            [(t, v) for t, v in zip(iem_times, iem_temps)] +
            latest_pair +
            metar_pair,
            key=lambda x: x[0]
        )
        # Deduplicate: within 10 min keep the later reading
        deduped = []
        for t, v in combined:
            if deduped and (t - deduped[-1][0]).total_seconds() < 600:
                deduped[-1] = (t, v)
            else:
                deduped.append((t, v))
        merged_times = [x[0] for x in deduped]
        merged_temps = [x[1] for x in deduped]

        day_high = max(merged_temps) if merged_temps else None

        # High set time
        hi_time = "--"
        if day_high is not None and merged_temps:
            try:
                hi_idx  = merged_temps.index(day_high)
                hi_time = merged_times[hi_idx].strftime("%I:%M %p").lstrip("0")
            except ValueError:
                pass

        prediction = predict_high(merged_times, merged_temps, fcst_times, fcst_temps, now_et)

        # Prefer 1-hour velocity; fall back to 2-hour if recent NWS obs have
        # null temperatures leaving only latestObs in the short window.
        vel_val = velocity(merged_times, merged_temps, 1.0) or \
                  velocity(merged_times, merged_temps, 2.0)

        # Derive trend from 1-hour velocity so it always agrees with Rate of Change
        if vel_val is None:
            trend = "Steady"
        elif vel_val > 0.5:
            trend = "Rising"
        elif vel_val < -0.5:
            trend = "Falling"
        else:
            trend = "Steady"

        trend_conf, trend_conf_reason = trend_confidence(merged_times, merged_temps)

        pk = peak_status(now_et)

        metar_match = None
        if metar_temp is not None and obs_temps:
            diff = abs(metar_temp - obs_temps[-1])
            metar_match = "matches" if diff < 2 else f"Δ {diff:.1f}°"

        return jsonify({
            "observed": [
                {"time": t.isoformat(), "temp": v}
                for t, v in zip(merged_times, merged_temps)
            ],
            "forecast": [
                {"time": t.isoformat(), "temp": v}
                for t, v in zip(fcst_times, fcst_temps)
            ],
            "metar_history": [
                {"time": t.isoformat(), "temp": v}
                for t, v in zip(mh_times, mh_temps)
            ],
            "now": now_et.isoformat(),
            "cur_temp": merged_temps[-1] if merged_temps else None,
            "day_high": day_high,
            "fcst_high": fcst_high,
            "hi_time": hi_time,
            "metar_temp": metar_temp,
            "metar_time": metar_time,
            "metar_match": metar_match,
            "prediction": prediction,
            "velocity": vel_val,
            "trend": trend,
            "trend_conf": trend_conf,
            "trend_conf_reason": trend_conf_reason,
            "peak": pk,
            "obs_count": len(merged_temps),
            "iem_count": len(iem_temps),
            "last_obs_time": merged_times[-1].strftime("%-I:%M %p") if merged_times else None,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(port=5050, debug=True)
