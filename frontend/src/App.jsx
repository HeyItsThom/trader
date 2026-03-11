import { useState, useEffect, useCallback, useRef } from "react";
import "./App.css";
import { loadData }      from "./api";
import StatCards         from "./components/StatCards";
import SignalStrip       from "./components/SignalStrip";
import TempChart         from "./components/TempChart";
import BetsPanel         from "./components/BetsPanel";
import HistoricalView    from "./components/HistoricalView";

const REFRESH_MS = 60_000;

// ── Prediction history (intra-day snapshots) ──────────────────────────────────
const TODAY_KEY = `predHistory_${new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" })}`;

function loadHistory() {
  try {
    const raw = localStorage.getItem(TODAY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveHistory(history) {
  try { localStorage.setItem(TODAY_KEY, JSON.stringify(history)); } catch {}
}

// ── Calibration (multi-day learning) ─────────────────────────────────────────
// Each entry: { date, earlyPred, actual, error }
// "earlyPred" = median of pre-noon predictions that day
// "actual"    = day_high once prediction is Locked (peak window closed)
// "error"     = actual − earlyPred  (positive → we under-predicted)
const CALIB_KEY  = "predCalibration_v1";
const CALIB_DAYS = 30; // rolling window

function loadCalib() {
  try {
    const raw = localStorage.getItem(CALIB_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveCalib(c) {
  try { localStorage.setItem(CALIB_KEY, JSON.stringify(c)); } catch {}
}

// Returns the rolling mean error to subtract from future predictions.
// Capped at ±3 °F to prevent overcorrection from unusual days.
export function computeBias(calibHistory) {
  if (!calibHistory.length) return 0;
  const recent = calibHistory.slice(-CALIB_DAYS);
  const mean   = recent.reduce((s, d) => s + d.error, 0) / recent.length;
  return Math.max(-3, Math.min(3, mean));
}

function loadThresholds() {
  try {
    const raw = localStorage.getItem("thresholds");
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function fmtClock(d) {
  let h = d.getHours(), m = d.getMinutes(), s = d.getSeconds();
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")} ${ampm} ET`;
}

export default function App() {
  const [data,        setData]        = useState(null);
  const [status,      setStatus]      = useState({ text: "Loading…", color: "#8b949e" });
  const [countdown,   setCountdown]   = useState(REFRESH_MS / 1000);
  const [thresholds,  setThresholds]  = useState(loadThresholds);
  const [predHistory, setPredHistory] = useState(loadHistory);
  const [calibHistory, setCalibHistory] = useState(loadCalib);
  const [error,       setError]       = useState(null);
  const timerRef = useRef(null);
  const countRef = useRef(null);
  const clock    = useClock();

  const fetchData = useCallback(async () => {
    try {
      setStatus({ text: "Refreshing…", color: "#8b949e" });
      const json = await loadData();

      // ── Apply learned bias correction to the prediction ───────────────────
      // bias = rolling mean of (actual − earlyPred) over the last CALIB_DAYS days
      // Positive bias → we've been under-predicting → nudge point upward.
      if (json.prediction && json.prediction.confidence !== "Locked") {
        const bias = computeBias(calibHistory);
        if (bias !== 0) {
          json.prediction = {
            ...json.prediction,
            point: json.prediction.point + bias,
            low:   json.prediction.low   + bias,
            high:  json.prediction.high  + bias,
          };
        }
      }

      setData(json);
      setError(null);

      // ── Accumulate intra-day prediction snapshots ─────────────────────────
      if (json.prediction && json.prediction.confidence !== "Locked") {
        const snap = {
          time:       new Date(json.now).getTime(),
          point:      json.prediction.point,
          low:        json.prediction.low,
          high:       json.prediction.high,
          confidence: json.prediction.confidence,
        };
        setPredHistory(prev => {
          if (prev.length && snap.time - prev[prev.length - 1].time < 3 * 60_000) return prev;
          const updated = [...prev, snap];
          saveHistory(updated);
          return updated;
        });
      }

      // ── Save calibration point when day locks ─────────────────────────────
      // Once the peak window closes the prediction is "Locked" and day_high is
      // the final observed high — use it to measure how far off we were.
      if (json.prediction?.confidence === "Locked" && json.day_high != null) {
        const dateStr = new Date(json.now).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
        setCalibHistory(prev => {
          if (prev.find(c => c.date === dateStr)) return prev; // already saved today

          // Use pre-noon snapshots as the "early prediction" baseline
          // (morning predictions are the most informative for calibration)
          const nowEt   = new Date(json.now);
          const noonMs  = new Date(json.now);
          noonMs.setHours(12, 0, 0, 0);
          const earlySnaps = predHistory.filter(s => s.time < noonMs.getTime());
          if (!earlySnaps.length) return prev; // no pre-noon data to calibrate on

          const earlyPred = earlySnaps.reduce((s, p) => s + p.point, 0) / earlySnaps.length;
          const entry = {
            date:      dateStr,
            earlyPred: parseFloat(earlyPred.toFixed(2)),
            actual:    json.day_high,
            error:     parseFloat((json.day_high - earlyPred).toFixed(2)),
          };
          const updated = [...prev, entry].slice(-CALIB_DAYS);
          saveCalib(updated);
          return updated;
        });
      }

      const ts = new Date().toLocaleTimeString("en-US", {
        hour: "numeric", minute: "2-digit", second: "2-digit",
        hour12: true, timeZone: "America/New_York",
      });
      const lastObs = json.last_obs_time ? `  ·  last obs ${json.last_obs_time}` : "";
      setStatus({ text: `Updated ${ts} ET${lastObs}  ·  ${json.obs_count} obs today`, color: "#3fb950" });
    } catch (e) {
      setError(e.message);
      setStatus({ text: `⚠  ${e.message}`, color: "#ff7b72" });
    }
    setCountdown(REFRESH_MS / 1000);
  }, [calibHistory, predHistory]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Persist thresholds whenever they change
  useEffect(() => {
    localStorage.setItem("thresholds", JSON.stringify(thresholds));
  }, [thresholds]);

  useEffect(() => {
    timerRef.current = setInterval(fetchData, REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchData]);

  useEffect(() => {
    countRef.current = setInterval(() => setCountdown(c => Math.max(0, c - 1)), 1000);
    return () => clearInterval(countRef.current);
  }, []);

  function manualRefresh() {
    clearInterval(timerRef.current);
    timerRef.current = setInterval(fetchData, REFRESH_MS);
    fetchData();
  }

  const m = Math.floor(countdown / 60);
  const s = countdown % 60;
  const countdownText = `refresh in ${m}:${String(s).padStart(2,"0")}`;

  const bias = computeBias(calibHistory);

  return (
    <div className="app">
      <div className="header">
        <span className="header-title">Boston Temp Tracker</span>
        <span className="header-sub">KBOS · Logan Airport</span>
        <span className="header-clock">{fmtClock(clock)}</span>
        <div className="header-right">
          <span className="header-countdown">{countdownText}</span>
          <span className="header-status" style={{ color: status.color }}>{status.text}</span>
          <button className="btn" onClick={manualRefresh}>⟳ Refresh</button>
          <a href="https://www.wunderground.com/history/daily/us/ma/boston/KBOS"
             target="_blank" rel="noreferrer">
            <button className="btn btn-blue">Open WU ↗</button>
          </a>
        </div>
      </div>

      <StatCards data={data} bias={bias} calibDays={calibHistory.length} />
      <SignalStrip data={data} />

      {error && !data && (
        <div className="error">{error}</div>
      )}
      {data && <TempChart data={data} thresholds={thresholds} predHistory={predHistory} />}

      <BetsPanel data={data} thresholds={thresholds} setThresholds={setThresholds} />

      <HistoricalView calibHistory={calibHistory} />

<div className="footer">
        <span>Data: NWS KBOS = WU · METAR = WU current conditions · Refreshes every 60 s</span>
        <span>Designed &amp; developed by Thom Brabant // Claude</span>
      </div>
    </div>
  );
}
