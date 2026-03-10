#!/usr/bin/env python3
"""
Boston Temperature Tracker — Flask API backend
Exposes all data-fetching logic as JSON endpoints for the React frontend.
"""

import re
import math
import threading
from concurrent.futures import ThreadPoolExecutor
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

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

PEAK_HOUR   = {1:13,2:13,3:14,4:14,5:14,6:15,7:15,8:15,9:14,10:14,11:13,12:13}
PEAK_WINDOW = (11, 18)

MONTHLY_AVG_HIGH = {1:36.4,2:38.9,3:46.6,4:56.5,5:66.7,6:76.1,
                    7:81.8,8:79.6,9:72.4,10:62.0,11:52.6,12:40.8}

_SIGMA_FACTOR = 1.5


# ── In-memory observation cache ────────────────────────────────────────────────
# Accumulates all successfully-fetched (dt, temp) pairs keyed by date string.
# Prevents chart gaps when upstream APIs (AVWX, NWS) are temporarily unavailable.
# Two stores: _chart_cache (all sources) and _metar_cache (METAR-only, for day_high).
_cache_lock   = threading.Lock()
_chart_cache: dict = {}   # date_str -> sorted list of (dt, temp)
_metar_cache: dict = {}   # date_str -> sorted list of (dt, temp), METAR precision only


def _cache_add(store: dict, date_str: str, points: list):
    """Merge new points into store[date_str], keeping sorted + 10-min deduped."""
    if not points:
        return
    with _cache_lock:
        combined = sorted(store.get(date_str, []) + points, key=lambda x: x[0])
        deduped = []
        for t, v in combined:
            if deduped and (t - deduped[-1][0]).total_seconds() < 600:
                deduped[-1] = (t, v)   # keep the later reading
            else:
                deduped.append((t, v))
        store[date_str] = deduped


def _cache_get(store: dict, date_str: str) -> list:
    with _cache_lock:
        return list(store.get(date_str, []))


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
    vels = [v for v in [v30, v60, v120] if v is not None]
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
    except Exception:
        return [], []


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


def _fetch_metar_tgftp():
    """tgftp.weather.gov — NWS public METAR feed. Returns (temp_f, time_str, dt, 'tgftp')."""
    resp = requests.get(METAR_URL, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    temp_f, time_str, dt = parse_metar_temp(resp.text)
    if temp_f is None:
        return None
    return temp_f, time_str, dt, "tgftp"


def _fetch_metar_avwx():
    """aviationweather.gov — FAA operational METAR feed. Returns (temp_f, time_str, dt, 'avwx')."""
    resp = requests.get(
        AVWX_METAR_URL,
        params={"ids": STATION_ID, "format": "json", "hours": 1},
        headers=HEADERS, timeout=10,
    )
    resp.raise_for_status()
    obs_list = resp.json()
    if not obs_list:
        return None
    obs    = max(obs_list, key=lambda x: x.get("obsTime", 0))
    temp_c = obs.get("temp")
    obs_ts = obs.get("obsTime")
    if temp_c is None or obs_ts is None:
        return None
    dt           = datetime.fromtimestamp(obs_ts, tz=zoneinfo.ZoneInfo("UTC")).astimezone(EASTERN_TZ)
    obs_time_str = dt.strftime("%-I:%M %p ET")
    return c_to_f(temp_c), obs_time_str, dt, "avwx"


def fetch_metar():
    """Fetch current METAR from both sources in parallel; return whichever is newer."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_tgftp = ex.submit(_fetch_metar_tgftp)
        f_avwx  = ex.submit(_fetch_metar_avwx)
        candidates = []
        for f in (f_tgftp, f_avwx):
            try:
                result = f.result()
                if result is not None:
                    candidates.append(result)
            except Exception:
                pass

    if not candidates:
        return None, None, None, None
    # Pick the reading with the most recent observation timestamp
    best = max(candidates, key=lambda x: x[2])
    return best[0], best[1], best[2], best[3]   # temp_f, time_str, dt, source


def fetch_open_meteo():
    """15-minute model temperatures for the KBOS grid point — today up to now.
    Free, no API key. Used to densify trend/velocity data between hourly ASOS obs."""
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": 42.3601,
                "longitude": -71.0589,
                "minutely_15": "temperature_2m",
                "temperature_unit": "fahrenheit",
                "timezone": "America/New_York",
                "forecast_days": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        m15    = resp.json().get("minutely_15", {})
        now_et = datetime.now(EASTERN_TZ)
        today  = now_et.date()
        times, temps = [], []
        for t_str, temp in zip(m15.get("time", []), m15.get("temperature_2m", [])):
            if temp is None:
                continue
            dt = datetime.fromisoformat(t_str).replace(tzinfo=EASTERN_TZ)
            if dt.date() == today and dt <= now_et:
                times.append(dt)
                temps.append(temp)
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
        now_et     = datetime.now(EASTERN_TZ)
        today_str  = now_et.strftime("%Y-%m-%d")

        # Run all fetches in parallel — cuts worst-case latency from ~85s to ~20s.
        with ThreadPoolExecutor(max_workers=5) as ex:
            f_obs   = ex.submit(fetch_observations)
            f_mh    = ex.submit(fetch_metar_history)
            f_om    = ex.submit(fetch_open_meteo)
            f_metar = ex.submit(fetch_metar)
            f_fcst  = ex.submit(fetch_forecast)
            obs_times, obs_temps             = f_obs.result()
            mh_times, mh_temps               = f_mh.result()
            om_times, om_temps               = f_om.result()
            metar_temp, metar_time, metar_dt, metar_source = f_metar.result()
            fcst_times, fcst_temps, fcst_high = f_fcst.result()

        # Add every successful fetch into the persistent caches.
        # This means a later API outage can't erase readings we already saw.
        if obs_times:
            _cache_add(_chart_cache, today_str, list(zip(obs_times, obs_temps)))
        if mh_times:
            _cache_add(_chart_cache, today_str, list(zip(mh_times, mh_temps)))
            _cache_add(_metar_cache, today_str, list(zip(mh_times, mh_temps)))
        if metar_temp is not None and metar_dt is not None and metar_dt.date() == now_et.date():
            _cache_add(_chart_cache, today_str, [(metar_dt, metar_temp)])
            _cache_add(_metar_cache, today_str, [(metar_dt, metar_temp)])

        # Chart data: use the full accumulated cache (never loses old readings).
        cached_chart  = _cache_get(_chart_cache, today_str)
        merged_times  = [x[0] for x in cached_chart]
        merged_temps  = [x[1] for x in cached_chart]

        # Last resort cur_temp: fall back to most-recent cached METAR reading.
        if metar_temp is None:
            cached_metar_pts = _cache_get(_metar_cache, today_str)
            if cached_metar_pts:
                metar_dt     = cached_metar_pts[-1][0]
                metar_temp   = cached_metar_pts[-1][1]
                metar_time   = metar_dt.strftime("%-I:%M %p ET")
                metar_source = "cache"

        # Enriched trend dataset: KBOS obs + Open-Meteo 15-min model.
        # Used only for velocity/trend_confidence — not for day_high or cur_temp
        # so WU matching is preserved.
        trend_all   = sorted(
            list(zip(merged_times, merged_temps)) + list(zip(om_times, om_temps)),
            key=lambda x: x[0],
        )
        trend_times = [x[0] for x in trend_all]
        trend_temps = [x[1] for x in trend_all]

        # cur_temp and day_high must come from METAR-only sources to match WU.
        # NWS observations use integer-°C rounding and can diverge from METAR.
        # Use the accumulated METAR cache so day_high survives AVWX outages.
        cached_metar_pts = _cache_get(_metar_cache, today_str)
        wu_times = [x[0] for x in cached_metar_pts]
        wu_temps = [x[1] for x in cached_metar_pts]

        cur_temp = metar_temp if metar_temp is not None else (merged_temps[-1] if merged_temps else None)
        day_high = max(wu_temps) if wu_temps else (max(merged_temps) if merged_temps else None)

        # High set time
        hi_time = "--"
        if day_high is not None and wu_temps:
            try:
                hi_idx  = wu_temps.index(day_high)
                hi_time = wu_times[hi_idx].strftime("%I:%M %p").lstrip("0")
            except ValueError:
                pass

        prediction = predict_high(trend_times, trend_temps, fcst_times, fcst_temps, now_et)

        vel_val = velocity(trend_times, trend_temps)

        # Derive trend from 1-hour velocity so it always agrees with Rate of Change
        if vel_val is None:
            trend = "Steady"
        elif vel_val > 0.5:
            trend = "Rising"
        elif vel_val < -0.5:
            trend = "Falling"
        else:
            trend = "Steady"

        trend_conf, trend_conf_reason = trend_confidence(trend_times, trend_temps)

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
            "model_data": [
                {"time": t.isoformat(), "temp": v}
                for t, v in zip(om_times, om_temps)
            ],
            "now": now_et.isoformat(),
            "cur_temp": cur_temp,
            "day_high": day_high,
            "fcst_high": fcst_high,
            "hi_time": hi_time,
            "metar_temp": metar_temp,
            "metar_time": metar_time,
            "metar_source": metar_source,
            "metar_match": metar_match,
            "prediction": prediction,
            "velocity": vel_val,
            "trend": trend,
            "trend_conf": trend_conf,
            "trend_conf_reason": trend_conf_reason,
            "peak": pk,
            "obs_count": len(merged_temps),
            "last_obs_time": merged_times[-1].strftime("%-I:%M %p") if merged_times else None,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(port=5050, debug=True)
