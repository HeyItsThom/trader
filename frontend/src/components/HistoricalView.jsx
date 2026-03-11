import { useState, useEffect } from "react";
import {
  ComposedChart, Line, Area,
  XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
  ReferenceLine, ReferenceArea,
} from "recharts";
import { loadHistoricalData } from "../api";

const C = {
  acc:  "#f0883e",
  red:  "#ff7b72",
  grn:  "#3fb950",
  blue: "#79c0ff",
  purp: "#bc8cff",
  gold: "#d29922",
  sub:  "#8b949e",
  wht:  "#e6edf3",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTick(ts) {
  const h = new Date(ts).getHours();
  if (h === 0)  return "12a";
  if (h < 12)   return `${h}a`;
  if (h === 12) return "12p";
  return `${h - 12}p`;
}

function todayEt() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

function offsetDate(dateStr, days) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d + days);
  return dt.toLocaleDateString("en-CA");
}

function displayDate(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
}

// ── Mini chart for a past day ─────────────────────────────────────────────────

function DayTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  const timeStr = new Date(row.time).toLocaleTimeString("en-US", {
    hour: "numeric", minute: "2-digit", timeZone: "America/New_York",
  });
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-time">{timeStr} ET</div>
      {row.obs != null && (
        <div className="chart-tooltip-row">
          <span className="chart-tooltip-label">Observed</span>
          <span className="chart-tooltip-value" style={{ color: C.acc }}>
            {row.obs.toFixed(1)}°F
          </span>
        </div>
      )}
    </div>
  );
}

function DayChart({ times, temps, dayHigh, calibEntry, dateStr }) {
  if (!times.length) {
    return <div className="hist-empty">No observation data for this date.</div>;
  }

  const rows  = times.map((t, i) => ({ time: t, obs: temps[i] }));
  const yMin  = Math.floor(Math.min(...temps) - 3);
  const yMax  = Math.ceil(Math.max(...temps)  + 4);

  // Use local midnight so the chart aligns to ET day when browser is in ET
  const [y, m, d] = dateStr.split("-").map(Number);
  const dayStart  = new Date(y, m - 1, d, 0, 0, 0, 0).getTime();
  const dayEnd    = new Date(y, m - 1, d, 23, 59, 59, 999).getTime();
  const peakStart = new Date(y, m - 1, d, 11, 0, 0, 0).getTime();
  const peakEnd   = new Date(y, m - 1, d, 18, 0, 0, 0).getTime();

  const hourTicks = [];
  for (let h = 0; h <= 23; h += 2) {
    hourTicks.push(new Date(y, m - 1, d, h, 0, 0, 0).getTime());
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <ComposedChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#30363d" opacity={0.4} />

        <XAxis
          dataKey="time"
          type="number"
          scale="time"
          domain={[dayStart, dayEnd]}
          ticks={hourTicks}
          tickFormatter={fmtTick}
          tick={{ fill: C.sub, fontSize: 10 }}
          tickLine={false}
          axisLine={{ stroke: "#30363d" }}
        />
        <YAxis
          domain={[yMin, yMax]}
          tick={{ fill: C.sub, fontSize: 10 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={v => `${v}°`}
          width={36}
        />

        {/* Peak window shading */}
        <ReferenceArea
          x1={peakStart} x2={peakEnd}
          fill={C.gold} fillOpacity={0.05} strokeOpacity={0}
        />

        {/* Day high */}
        {dayHigh != null && (
          <ReferenceLine
            y={dayHigh}
            stroke={C.red} strokeWidth={1} strokeDasharray="4 3" opacity={0.7}
            label={{
              value: `High ${dayHigh.toFixed(1)}°F`,
              position: "insideBottomRight",
              fill: C.red, fontSize: 10, fontWeight: 700,
            }}
          />
        )}

        {/* Model's early prediction (from calibration store) */}
        {calibEntry && (
          <ReferenceLine
            y={calibEntry.earlyPred}
            stroke={C.purp} strokeWidth={1} strokeDasharray="4 3" opacity={0.65}
            label={{
              value: `Pred ${calibEntry.earlyPred.toFixed(1)}°F`,
              position: "insideTopRight",
              fill: C.purp, fontSize: 9, fontWeight: 600,
            }}
          />
        )}

        <Area
          type="monotone"
          dataKey="obs"
          stroke="none"
          fill={C.acc}
          fillOpacity={0.07}
          isAnimationActive={false}
          legendType="none"
        />
        <Line
          type="monotone"
          dataKey="obs"
          stroke={C.acc}
          strokeWidth={2.5}
          dot={{ r: 2.5, fill: C.acc, strokeWidth: 0 }}
          activeDot={{ r: 4, fill: C.acc, stroke: C.wht, strokeWidth: 1.5 }}
          isAnimationActive={false}
        />

        <Tooltip
          content={<DayTooltip />}
          cursor={{ stroke: C.sub, strokeWidth: 1, strokeDasharray: "3 3" }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function HistoricalView({ calibHistory = [] }) {
  const [show,     setShow]     = useState(false);
  const [dateStr,  setDateStr]  = useState(() => offsetDate(todayEt(), -1));
  const [histData, setHistData] = useState(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState(null);

  useEffect(() => {
    if (!show) return;
    setLoading(true);
    setError(null);
    setHistData(null);
    loadHistoricalData(dateStr)
      .then(d  => { setHistData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [dateStr, show]);

  const calibEntry  = calibHistory.find(c => c.date === dateStr) ?? null;
  const canGoFwd    = offsetDate(dateStr, 1) < todayEt();

  const errSign  = calibEntry ? (calibEntry.error >= 0 ? "+" : "") : "";
  const errColor = !calibEntry ? C.sub
    : Math.abs(calibEntry.error) < 1 ? C.grn
    : Math.abs(calibEntry.error) < 2 ? C.gold
    : C.red;

  return (
    <div className="hist-wrap">
      <button className="hist-toggle" onClick={() => setShow(s => !s)}>
        <span>Past Days</span>
        <span className="hist-toggle-arrow">{show ? "▲" : "▼"}</span>
      </button>

      {show && (
        <div className="hist-body">
          {/* Date navigation */}
          <div className="hist-nav">
            <button className="btn" onClick={() => setDateStr(d => offsetDate(d, -1))}>
              ← Prev
            </button>
            <span className="hist-date">{displayDate(dateStr)}</span>
            <button
              className="btn"
              onClick={() => setDateStr(d => offsetDate(d, 1))}
              disabled={!canGoFwd}
            >
              Next →
            </button>
          </div>

          {loading && <div className="hist-status">Loading…</div>}
          {error   && <div className="hist-status hist-error">{error}</div>}

          {histData && !loading && (
            <>
              <DayChart
                times={histData.times}
                temps={histData.temps}
                dayHigh={histData.day_high}
                calibEntry={calibEntry}
                dateStr={dateStr}
              />

              {/* Stats row */}
              <div className="hist-stats">
                <div className="hist-stat">
                  <div className="hist-stat-label">Actual High</div>
                  <div className="hist-stat-value" style={{ color: C.red }}>
                    {histData.day_high != null ? `${histData.day_high.toFixed(1)}°F` : "--"}
                  </div>
                </div>

                <div className="hist-stat">
                  <div className="hist-stat-label">Model Pred (early)</div>
                  <div className="hist-stat-value" style={{ color: calibEntry ? C.purp : C.sub }}>
                    {calibEntry ? `${calibEntry.earlyPred.toFixed(1)}°F` : "not stored"}
                  </div>
                </div>

                <div className="hist-stat">
                  <div className="hist-stat-label">Pred Error</div>
                  <div className="hist-stat-value" style={{ color: errColor }}>
                    {calibEntry
                      ? `${errSign}${calibEntry.error.toFixed(1)}°F`
                      : "--"}
                  </div>
                </div>

                <div className="hist-stat">
                  <div className="hist-stat-label">Obs Count</div>
                  <div className="hist-stat-value" style={{ color: C.sub }}>
                    {histData.times.length}
                  </div>
                </div>
              </div>

              {!calibEntry && (
                <div className="hist-no-calib">
                  No prediction data stored for this day — the model only stores entries
                  after the peak window closes (after 6 PM ET).
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
