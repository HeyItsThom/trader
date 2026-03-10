import { useState, useEffect, useCallback, useRef } from "react";
import "./App.css";
import { loadData }      from "./api";
import StatCards         from "./components/StatCards";
import SignalStrip       from "./components/SignalStrip";
import TempChart         from "./components/TempChart";
import BetsPanel         from "./components/BetsPanel";
import ProbabilityLadder from "./components/ProbabilityLadder";

const REFRESH_MS = 60_000;

// Prediction history keyed by today's date in ET — auto-clears each new day
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
  const [error,       setError]       = useState(null);
  const timerRef = useRef(null);
  const countRef = useRef(null);
  const clock    = useClock();

  const fetchData = useCallback(async () => {
    try {
      setStatus({ text: "Refreshing…", color: "#8b949e" });
      const json = await loadData();
      setData(json);
      setError(null);

      // Accumulate prediction history throughout the day (one snapshot per refresh)
      if (json.prediction && json.prediction.confidence !== "Locked") {
        const snap = {
          time:       new Date(json.now).getTime(),
          point:      json.prediction.point,
          low:        json.prediction.low,
          high:       json.prediction.high,
          confidence: json.prediction.confidence,
        };
        setPredHistory(prev => {
          // If last snapshot was within 3 min, skip (handles rapid manual refreshes)
          if (prev.length && snap.time - prev[prev.length - 1].time < 3 * 60_000) return prev;
          const updated = [...prev, snap];
          saveHistory(updated);
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
  }, []);

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

      <StatCards data={data} />
      <SignalStrip data={data} />

      {error && !data && (
        <div className="error">{error}</div>
      )}
      {data && <TempChart data={data} thresholds={thresholds} predHistory={predHistory} />}

      <BetsPanel data={data} thresholds={thresholds} setThresholds={setThresholds} />

      {data && <ProbabilityLadder data={data} thresholds={thresholds} />}

      <div className="footer">
        <span>Data: NWS KBOS = WU · METAR = WU current conditions · Refreshes every 60 s</span>
        <span>Designed &amp; developed by Thom Brabant // Claude</span>
      </div>
    </div>
  );
}
