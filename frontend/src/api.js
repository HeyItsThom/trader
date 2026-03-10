/**
 * Boston Temp Tracker — browser-side data fetching + prediction engine
 * All NWS/METAR calls made directly from the browser (NWS supports CORS).
 */

const STATION_ID     = "KBOS";
const NWS_OBS_URL    = `https://api.weather.gov/stations/${STATION_ID}/observations`;
const NWS_POINTS     = "https://api.weather.gov/points/42.3601,-71.0589";
const AVWX_METAR_URL = "https://aviationweather.gov/api/data/metar";
const HEADERS        = { "User-Agent": "BostonTempTracker/1.0 (personal trading tool)" };

const PEAK_HOUR   = {1:13,2:13,3:14,4:14,5:14,6:15,7:15,8:15,9:14,10:14,11:13,12:13};
const PEAK_WINDOW = [11, 18];
const SIGMA_FACTOR = 1.5;

// ── Helpers ──────────────────────────────────────────────────────────────────
function cToF(c) { return c * 9 / 5 + 32; }

function normCdf(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.7814779 + t * (-1.8212560 + t * 1.3302744))));
  return x > 0 ? 1 - p : p;
}

export function probExceed(threshold, pred, curHigh) {
  if (curHigh >= threshold) return 1.0;
  const sigma = pred.spread / SIGMA_FACTOR;
  if (sigma <= 0) return pred.point >= threshold ? 1.0 : 0.0;
  const z = (threshold - pred.point) / sigma;
  return 1 - normCdf(z);
}

function velocity(times, temps, windowHr = 1.0) {
  if (times.length < 2) return null;
  const cutoff = times[times.length - 1] - windowHr * 3600000;
  const pairs = times.map((t, i) => [t, temps[i]]).filter(([t]) => t >= cutoff);
  if (pairs.length < 2) return null;
  const dtHr = (pairs[pairs.length - 1][0] - pairs[0][0]) / 3600000;
  if (dtHr < 0.05) return null;
  return (pairs[pairs.length - 1][1] - pairs[0][1]) / dtHr;
}

// Trend confidence: compare velocity across multiple windows to gauge consistency
function trendConfidence(times, temps) {
  if (times.length < 4) return { conf: "Low", reason: "few obs" };

  const v30  = velocity(times, temps, 0.5);
  const v60  = velocity(times, temps, 1.0);
  const v120 = velocity(times, temps, 2.0);

  const vels = [v30, v60, v120].filter(v => v != null);
  if (vels.length < 2) return { conf: "Low", reason: "few obs" };

  // Classify each velocity direction (ignore near-zero as neutral)
  const dirs = vels.map(v => (v > 0.3 ? 1 : v < -0.3 ? -1 : 0));
  const nonFlat = dirs.filter(d => d !== 0);
  const uniqueDirs = new Set(nonFlat);

  if (uniqueDirs.size > 1) return { conf: "Low", reason: "conflicting" };
  if (uniqueDirs.size === 0) return { conf: "Medium", reason: "near-flat" };

  // Magnitude consistency: how close are the rates?
  const magnitudes = vels.map(Math.abs);
  const maxV = Math.max(...magnitudes);
  const minV = Math.min(...magnitudes);
  const consistency = maxV > 0.1 ? minV / maxV : 0;

  if (consistency >= 0.55) return { conf: "High", reason: "consistent" };
  if (consistency >= 0.25) return { conf: "Medium", reason: "moderate" };
  return { conf: "Low", reason: "variable rate" };
}

function peakStatus(nowEt) {
  const h = nowEt.getHours() + nowEt.getMinutes() / 60;
  if (h < PEAK_WINDOW[0]) return { open: false, label: `Opens in ${(PEAK_WINDOW[0]-h).toFixed(1)}h` };
  if (h < PEAK_WINDOW[1]) return { open: true,  label: `OPEN  ${(PEAK_WINDOW[1]-h).toFixed(1)}h left` };
  return { open: false, label: "Closed" };
}

// ── Prediction engine ─────────────────────────────────────────────────────────
function predictHigh(obsTimes, obsTemps, fcstTimes, fcstTemps, nowEt) {
  if (!obsTemps.length) return null;

  const curHigh  = Math.max(...obsTemps);
  const month    = nowEt.getMonth() + 1;
  const hourFrac = nowEt.getHours() + nowEt.getMinutes() / 60;
  const peakHr   = PEAK_HOUR[month];
  const today    = nowEt.toDateString();

  if (hourFrac >= PEAK_WINDOW[1]) {
    return { point: curHigh, low: curHigh-0.3, high: curHigh+0.3, spread: 0.3,
             confidence: "Locked", detail: `Peak window closed — ${curHigh.toFixed(1)}°F is the day's high` };
  }

  const winDur     = PEAK_WINDOW[1] - PEAK_WINDOW[0];
  const winElapsed = Math.max(0, Math.min(1, (hourFrac - PEAK_WINDOW[0]) / winDur));

  const fcstVals = fcstTemps.filter((_, i) => new Date(fcstTimes[i]).toDateString() === today);
  const fcstHigh = fcstVals.length ? Math.max(...fcstVals) : null;

  const vel = velocity(obsTimes, obsTemps, 1.0) ?? velocity(obsTimes, obsTemps, 2.0) ?? 0;
  let hoursToPeak = Math.max(0, peakHr - hourFrac);
  if (vel < 0 && hourFrac > peakHr) hoursToPeak = 0;
  const trendHigh = Math.max(curHigh, obsTemps[obsTemps.length-1] + vel * hoursToPeak);

  let wf, wt;
  if (hourFrac < PEAK_WINDOW[0]) {
    wf = 0.80; wt = 0.20;
  } else if (hourFrac < peakHr) {
    const preFrac = (hourFrac - PEAK_WINDOW[0]) / Math.max(peakHr - PEAK_WINDOW[0], 1);
    wf = 0.75 - 0.30 * preFrac; wt = 1 - wf;
  } else {
    const postFrac = (hourFrac - peakHr) / Math.max(PEAK_WINDOW[1] - peakHr, 1);
    wf = Math.max(0.10, 0.45 - 0.35 * postFrac); wt = 1 - wf;
  }

  let point = fcstHigh != null ? wf * fcstHigh + wt * trendHigh : trendHigh;
  point = Math.max(point, curHigh);

  let conf, spread;
  if (fcstHigh != null) {
    const disagree = Math.abs(fcstHigh - trendHigh);
    if (disagree <= 1.5)      { conf = "High";   spread = 1.5; }
    else if (disagree <= 3.5) { conf = "Medium"; spread = 2.5; }
    else                      { conf = "Low";    spread = 4.0; }
  } else {
    conf = "Low"; spread = 4.0;
  }

  const tighten = 1.0 - 0.45 * winElapsed;
  spread = Math.round(spread * tighten * 2) / 2;
  if (conf === "High" && Math.abs(hourFrac - peakHr) < 1.0 && fcstHigh) spread = Math.min(spread, 1.0);
  spread = Math.max(spread, 0.5);

  const parts = [];
  if (fcstHigh) parts.push(`NWS ${fcstHigh.toFixed(1)}°  ×${wf.toFixed(2)}`);
  parts.push(`trend ${trendHigh.toFixed(1)}°  vel ${vel >= 0 ? "+" : ""}${vel.toFixed(1)}°/hr  ×${wt.toFixed(2)}`);
  if (winElapsed > 0) parts.push(`window ${(winElapsed*100).toFixed(0)}% elapsed`);

  return { point, low: point-spread, high: point+spread, spread, confidence: conf, trendHigh, vel, wf, wt, detail: parts.join("   ·   ") };
}

// ── Fetchers ──────────────────────────────────────────────────────────────────

// Fetch the single most-current NWS observation (fresher than the list endpoint)
async function fetchLatestObservation() {
  try {
    const res = await fetch(`${NWS_OBS_URL}/latest`, { headers: HEADERS });
    if (!res.ok) return null;
    const p     = (await res.json()).properties ?? {};
    const tempC = p.temperature?.value;
    const ts    = p.timestamp;
    if (tempC == null || ts == null) return null;
    return { time: new Date(ts).getTime(), temp: cToF(tempC) };
  } catch { return null; }
}

async function fetchObservations() {
  const res = await fetch(`${NWS_OBS_URL}?limit=150`, { headers: HEADERS });
  if (!res.ok) throw new Error(`NWS observations: HTTP ${res.status}`);
  const data  = await res.json();
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const times = [], temps = [];
  for (const feat of [...(data.features ?? [])].reverse()) {
    const p      = feat.properties ?? {};
    const tempC  = p.temperature?.value;
    const ts     = p.timestamp;
    if (tempC == null || ts == null) continue;
    const dt = new Date(ts);
    if (dt.toLocaleDateString("en-CA", { timeZone: "America/New_York" }) === today) {
      times.push(dt.getTime());
      temps.push(cToF(tempC));
    }
  }
  return { times, temps };
}

async function fetchForecast() {
  try {
    const r1  = await fetch(NWS_POINTS, { headers: HEADERS });
    if (!r1.ok) return { times: [], temps: [], high: null };
    const fcstUrl = (await r1.json()).properties?.forecastHourly;
    if (!fcstUrl) return { times: [], temps: [], high: null };
    const r2  = await fetch(fcstUrl, { headers: HEADERS });
    if (!r2.ok) return { times: [], temps: [], high: null };
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    const times = [], temps = [];
    for (const p of (await r2.json()).properties?.periods ?? []) {
      const dt  = new Date(p.startTime);
      const day = dt.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
      if (day < today) continue;
      if (day > today) break;
      temps.push(p.temperatureUnit === "C" ? cToF(p.temperature) : p.temperature);
      times.push(dt.getTime());
    }
    return { times, temps, high: temps.length ? Math.max(...temps) : null };
  } catch {
    return { times: [], temps: [], high: null };
  }
}

async function fetchMetarHistory() {
  try {
    const res = await fetch(
      `${AVWX_METAR_URL}?ids=${STATION_ID}&format=json&hours=24`,
      { headers: HEADERS }
    );
    if (!res.ok) return { times: [], temps: [] };
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    const pairs = [];
    for (const obs of await res.json()) {
      if (obs.temp == null || obs.obsTime == null) continue;
      const dt  = new Date(obs.obsTime * 1000);
      const day = dt.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
      if (day === today) pairs.push([dt.getTime(), cToF(obs.temp)]);
    }
    pairs.sort((a, b) => a[0] - b[0]);
    return { times: pairs.map(p => p[0]), temps: pairs.map(p => p[1]) };
  } catch {
    return { times: [], temps: [] };
  }
}

// ── Main data loader ──────────────────────────────────────────────────────────
export async function loadData() {
  const [obs, fcst, metar, latestObs] = await Promise.all([
    fetchObservations(),
    fetchForecast(),
    fetchMetarHistory(),
    fetchLatestObservation(),
  ]);

  const now    = new Date();
  const nowEt  = now; // JS Date is fine for local computation; displayed in ET via Intl

  // Merge NWS obs + METAR history + latest NWS observation into a unified series.
  // latestObs hits a dedicated endpoint that's fresher than the list and helps
  // close the gap with WU's near-real-time METAR display.
  const combined = [
    ...obs.times.map((t, i) => ({ t, temp: obs.temps[i] })),
    ...metar.times.map((t, i) => ({ t, temp: metar.temps[i] })),
    ...(latestObs ? [{ t: latestObs.time, temp: latestObs.temp }] : []),
  ].sort((a, b) => a.t - b.t);

  // Deduplicate: if two readings are within 10 min, keep the later one
  const deduped = [];
  for (const entry of combined) {
    const last = deduped[deduped.length - 1];
    if (last && entry.t - last.t < 10 * 60 * 1000) {
      deduped[deduped.length - 1] = entry;
    } else {
      deduped.push(entry);
    }
  }
  const mergedTimes = deduped.map(e => e.t);
  const mergedTemps = deduped.map(e => e.temp);

  const allHighs = mergedTemps;
  const dayHigh  = allHighs.length ? Math.max(...allHighs) : null;

  // High set time (search merged series)
  let hiTime = "--";
  if (dayHigh != null) {
    const idx = mergedTemps.indexOf(dayHigh);
    if (idx !== -1) {
      hiTime = new Date(mergedTimes[idx]).toLocaleTimeString("en-US",
        { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
    }
  }

  // Compute ET hour/min for prediction engine
  const etStr   = now.toLocaleString("en-US", { timeZone: "America/New_York", hour12: false,
                    year: "numeric", month: "2-digit", day: "2-digit",
                    hour: "2-digit", minute: "2-digit" });
  const [datePart, timePart] = etStr.split(", ");
  const [mo, dy, yr]   = datePart.split("/").map(Number);
  const [hr, mn]        = timePart.split(":").map(Number);
  const nowEtProxy = { getHours: () => hr, getMinutes: () => mn,
                       getMonth: () => mo - 1, toDateString: () => new Date(yr, mo-1, dy).toDateString() };

  // Use merged series for prediction (includes latest METAR readings)
  const prediction = predictHigh(mergedTimes, mergedTemps, fcst.times, fcst.temps, nowEtProxy);

  const vel = velocity(mergedTimes, mergedTemps);

  // Derive trend from 1-hour velocity so it always agrees with Rate of Change
  const trend = vel == null ? "Steady"
    : vel >  0.5 ? "Rising"
    : vel < -0.5 ? "Falling"
    : "Steady";

  const trendConf = trendConfidence(mergedTimes, mergedTemps);

  // Restore METAR match: compare last METAR reading vs latest NWS obs reading
  const lastMetarTemp = metar.temps.length ? metar.temps[metar.temps.length - 1] : null;
  const lastObsTemp   = obs.temps.length   ? obs.temps[obs.temps.length - 1]     : null;
  const metarMatch = (lastMetarTemp != null && lastObsTemp != null)
    ? (Math.abs(lastMetarTemp - lastObsTemp) < 2 ? "matches" : `Δ ${Math.abs(lastMetarTemp - lastObsTemp).toFixed(1)}°`)
    : null;

  return {
    observed:      mergedTimes.map((t, i) => ({ time: new Date(t).toISOString(), temp: mergedTemps[i] })),
    forecast:      fcst.times.map((t, i) => ({ time: new Date(t).toISOString(), temp: fcst.temps[i] })),
    metar_history: metar.times.map((t, i) => ({ time: new Date(t).toISOString(), temp: metar.temps[i] })),
    now:           now.toISOString(),
    cur_temp:      mergedTemps.length ? mergedTemps[mergedTemps.length-1] : null,
    day_high:      dayHigh,
    fcst_high:     fcst.high,
    hi_time:       hiTime,
    metar_temp:    lastMetarTemp,
    metar_match:   metarMatch,
    prediction,
    velocity:      vel,
    trend,
    trend_conf:    trendConf.conf,
    trend_conf_reason: trendConf.reason,
    peak:          peakStatus(nowEtProxy),
    obs_count:     mergedTemps.length,
    last_obs_time: mergedTimes.length
      ? new Date(mergedTimes[mergedTimes.length - 1]).toLocaleTimeString("en-US", {
          hour: "numeric", minute: "2-digit", timeZone: "America/New_York",
        })
      : null,
  };
}
