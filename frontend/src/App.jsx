import { useState, useEffect, useCallback, useRef } from "react";
import "./App.css";
import StatCards        from "./components/StatCards";
import SignalStrip      from "./components/SignalStrip";
import TempChart        from "./components/TempChart";
import BetsPanel        from "./components/BetsPanel";
import ProbabilityLadder from "./components/ProbabilityLadder";

const API_URL = "/api/data";
const REFRESH_MS   = 60_000;
const DEFAULT_THRESH = [41, 42];

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
  const [data,       setData]       = useState(null);
  const [status,     setStatus]     = useState({ text: "Loading…", color: "#8b949e" });
  const [countdown,  setCountdown]  = useState(REFRESH_MS / 1000);
  const [thresholds, setThresholds] = useState(DEFAULT_THRESH);
  const [error,      setError]      = useState(null);
  const timerRef = useRef(null);
  const countRef = useRef(null);
  const clock    = useClock();

  const fetchData = useCallback(async () => {
    try {
      setStatus({ text: "Refreshing…", color: "#8b949e" });
      const res  = await fetch(API_URL);
      let json;
      try { json = await res.json(); } catch { json = {}; }
      if (!res.ok) throw new Error(json.error ?? `HTTP ${res.status}`);
      if (json.error) throw new Error(json.error);
      setData(json);
      setError(null);
      const ts = new Date().toLocaleTimeString("en-US", {
        hour: "numeric", minute: "2-digit", second: "2-digit",
        hour12: true, timeZone: "America/New_York",
      });
      setStatus({ text: `Updated ${ts} ET  ·  ${json.obs_count} obs today`, color: "#3fb950" });
    } catch (e) {
      setError(e.message);
      setStatus({ text: `⚠  ${e.message}`, color: "#ff7b72" });
    }
    setCountdown(REFRESH_MS / 1000);
  }, []);

  // Initial fetch
  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh every 60 s
  useEffect(() => {
    timerRef.current = setInterval(fetchData, REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchData]);

  // Countdown ticker
  useEffect(() => {
    countRef.current = setInterval(() => {
      setCountdown(c => Math.max(0, c - 1));
    }, 1000);
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
      {/* Header */}
      <div className="header">
        <span className="header-title">Boston Temp Tracker</span>
        <span className="header-sub">KBOS · Logan Airport</span>
        <span className="header-clock">{fmtClock(clock)}</span>
        <div className="header-right">
          <span className="header-countdown">{countdownText}</span>
          <span className="header-status" style={{ color: status.color }}>{status.text}</span>
          <button className="btn" onClick={manualRefresh}>⟳ Refresh</button>
          <a
            href="https://www.wunderground.com/history/daily/us/ma/boston/KBOS"
            target="_blank" rel="noreferrer"
          >
            <button className="btn btn-blue">Open WU ↗</button>
          </a>
        </div>
      </div>

      {/* Stat cards */}
      <StatCards data={data} />

      {/* Signal strip */}
      <SignalStrip data={data} />

      {/* Chart */}
      {error && !data && (
        <div className="error">
          {error.includes("fetch") || error.includes("ECONNREFUSED") || error.includes("NetworkError")
            ? <>Backend not reachable — run <code>python api.py</code> in a separate terminal</>
            : <>{error}</>
          }
        </div>
      )}
      {data && (
        <TempChart data={data} thresholds={thresholds} />
      )}

      {/* Bets panel */}
      <BetsPanel
        data={data}
        thresholds={thresholds}
        setThresholds={setThresholds}
      />

      {/* Probability ladder */}
      {data && (
        <ProbabilityLadder data={data} thresholds={thresholds} />
      )}

      {/* Footer */}
      <div className="footer">
        <span>Data: NWS KBOS = WU · METAR = WU current conditions · Refreshes every 60 s</span>
        <span>Designed &amp; developed by Thom Brabant // Claude</span>
      </div>
    </div>
  );
}
