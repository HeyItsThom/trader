import {
  ComposedChart, Line, Area, Scatter, ReferenceLine, ReferenceArea,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { format, parseISO } from "date-fns";

const C = {
  acc:  "#f0883e",
  red:  "#ff7b72",
  grn:  "#3fb950",
  blue: "#79c0ff",
  purp: "#bc8cff",
  gold: "#d29922",
  sub:  "#8b949e",
  wht:  "#e6edf3",
  card: "#21262d",
};

// Formats a tick timestamp (ms) to hour label
function fmtTick(ts) {
  const d = new Date(ts);
  const h = d.getHours();
  if (h === 0)  return "12 AM";
  if (h < 12)   return `${h} AM`;
  if (h === 12) return "12 PM";
  return `${h - 12} PM`;
}

// Custom tooltip — shows historically correct data for each point
function ChartTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;

  // Pull the underlying data point; fall back to forecast entry for future-only rows
  const obsEntry  = payload.find(p => p.dataKey === "obs");
  const fcstEntry = payload.find(p => p.dataKey === "fcst");
  const predEntry = payload.find(p => p.dataKey === "point");
  const entry = obsEntry ?? fcstEntry ?? predEntry;
  if (!entry) return null;

  const d = entry.payload; // the full data row
  const timeStr = format(new Date(d.time), "h:mm a 'ET'");

  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-time">{timeStr}</div>
      {d.obs != null && (
        <div className="chart-tooltip-row">
          <span className="chart-tooltip-label">Observed</span>
          <span className="chart-tooltip-value" style={{ color: C.acc }}>
            {d.obs.toFixed(1)}°F
          </span>
        </div>
      )}
      {d.fcst != null && (
        <div className="chart-tooltip-row">
          <span className="chart-tooltip-label">NWS Forecast</span>
          <span className="chart-tooltip-value" style={{ color: C.blue }}>
            {d.fcst.toFixed(1)}°F
          </span>
        </div>
      )}
      {/* Prediction snapshot (from history or current) */}
      {d.point != null && (
        <>
          <div className="chart-tooltip-row">
            <span className="chart-tooltip-label">Pred at this time</span>
            <span className="chart-tooltip-value" style={{ color: C.purp }}>
              {d.point.toFixed(1)}°F ±{((d.high - d.low) / 2).toFixed(1)}°
            </span>
          </div>
          {d.confidence && (
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">Confidence</span>
              <span className="chart-tooltip-value" style={{ color: C.purp }}>
                {d.confidence}
              </span>
            </div>
          )}
        </>
      )}
      {/* Current prediction shown on latest obs point */}
      {d.isLatest && d.predPoint != null && !d.point && (
        <>
          <div className="chart-tooltip-row">
            <span className="chart-tooltip-label">Pred High</span>
            <span className="chart-tooltip-value" style={{ color: C.purp }}>
              {d.predPoint.toFixed(1)}°F ±{d.predSpread?.toFixed(1)}°
            </span>
          </div>
          <div className="chart-tooltip-row">
            <span className="chart-tooltip-label">Confidence</span>
            <span className="chart-tooltip-value" style={{ color: C.purp }}>
              {d.predConf}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

export default function TempChart({ data, thresholds = [], predHistory = [] }) {
  if (!data) return null;

  const { observed = [], forecast = [], prediction, day_high, fcst_high, now } = data;
  if (!observed.length) return null;

  // ── Build merged chart series ──────────────────────────────────────────────
  // Key: ISO timestamp string → value
  const fcstMap = {};
  for (const f of forecast) {
    const ms = new Date(f.time).getTime();
    // round to nearest hour for matching
    const hourMs = Math.round(ms / 3600000) * 3600000;
    fcstMap[hourMs] = f.temp;
  }

  const nowMs = now ? new Date(now).getTime() : Date.now();

  const obsRows = observed.map((o, i) => {
    const ms = new Date(o.time).getTime();
    const hourMs = Math.round(ms / 3600000) * 3600000;
    // match nearest NWS forecast within 30 min
    let fcst = null;
    let minDiff = Infinity;
    for (const f of forecast) {
      const diff = Math.abs(new Date(f.time).getTime() - ms);
      if (diff < minDiff) { minDiff = diff; fcst = f.temp; }
    }
    if (minDiff > 90 * 60 * 1000) fcst = null; // >90 min → don't show

    return {
      time: ms,
      obs: o.temp,
      fcst,
      isLatest: i === observed.length - 1,
      predPoint: i === observed.length - 1 ? prediction?.point : null,
      predSpread: i === observed.length - 1 ? prediction?.spread : null,
      predConf:  i === observed.length - 1 ? prediction?.confidence : null,
    };
  });

  // Forecast-only rows (future hours not yet in observations)
  const lastObsMs = obsRows.length ? obsRows[obsRows.length - 1].time : nowMs;
  const fcstRows = forecast
    .filter(f => new Date(f.time).getTime() > lastObsMs + 30 * 60 * 1000)
    .map(f => ({ time: new Date(f.time).getTime(), fcst: f.temp }));

  const allRows = [...obsRows, ...fcstRows].sort((a, b) => a.time - b.time);

  // ── Y-axis domain ─────────────────────────────────────────────────────────
  const allTemps = [
    ...observed.map(o => o.temp),
    ...(prediction ? [prediction.low - 1, prediction.high + 1] : []),
    ...(fcst_high ? [fcst_high] : []),
    ...thresholds.filter(t => Math.abs(t - (day_high ?? 50)) <= 12).flatMap(t => [t - 1, t + 1]),
  ].filter(v => v != null);

  const yMin = Math.floor(Math.min(...allTemps) - 3);
  const yMax = Math.ceil(Math.max(...allTemps) + 4);

  // ── X-axis: full day (midnight to midnight) ───────────────────────────────
  const todayStart = new Date(now || Date.now());
  todayStart.setHours(0, 0, 0, 0);
  const todayEnd = new Date(todayStart);
  todayEnd.setHours(23, 59, 59, 999);

  // Peak window: 11 AM – 6 PM ET
  const peakStart = new Date(todayStart); peakStart.setHours(11, 0, 0, 0);
  const peakEnd   = new Date(todayStart); peakEnd.setHours(18, 0, 0, 0);

  // Hour ticks
  const hourTicks = [];
  for (let h = 0; h <= 23; h++) {
    const t = new Date(todayStart); t.setHours(h, 0, 0, 0);
    hourTicks.push(t.getTime());
  }

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={allRows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" opacity={0.4} />

          <XAxis
            dataKey="time"
            type="number"
            scale="time"
            domain={[todayStart.getTime(), todayEnd.getTime()]}
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
            x1={peakStart.getTime()} x2={peakEnd.getTime()}
            fill={C.gold} fillOpacity={0.05}
            strokeOpacity={0}
          />

          {/* Prediction band */}
          {prediction && (
            <ReferenceArea
              y1={prediction.low} y2={prediction.high}
              fill={C.purp} fillOpacity={0.08}
              strokeOpacity={0}
            />
          )}

          {/* Prediction point line */}
          {prediction && (
            <ReferenceLine
              y={prediction.point}
              stroke={C.purp} strokeWidth={1} strokeDasharray="4 3" opacity={0.7}
              label={{
                value: `Pred ${prediction.point.toFixed(1)}° ±${prediction.spread.toFixed(1)}`,
                position: "insideTopRight",
                fill: C.purp, fontSize: 9, fontWeight: 600,
              }}
            />
          )}

          {/* Day high reference */}
          {day_high != null && (
            <ReferenceLine
              y={day_high}
              stroke={C.red} strokeWidth={1} strokeDasharray="4 3" opacity={0.6}
              label={{
                value: `High ${day_high.toFixed(1)}°F`,
                position: "insideBottomRight",
                fill: C.red, fontSize: 10, fontWeight: 700,
              }}
            />
          )}

          {/* NWS forecast high */}
          {fcst_high && fcst_high >= yMin && fcst_high <= yMax && (
            <ReferenceLine
              y={fcst_high}
              stroke={C.blue} strokeWidth={0.8} opacity={0.35}
            />
          )}

          {/* Bet threshold lines */}
          {thresholds.map(t => {
            if (t < yMin || t > yMax) return null;
            const cleared = day_high != null && day_high >= t;
            return (
              <ReferenceLine
                key={t}
                y={t}
                stroke={cleared ? C.grn : C.gold}
                strokeWidth={1.2} strokeDasharray="5 3" opacity={0.7}
                label={{
                  value: `> ${t}°`,
                  position: "insideTopLeft",
                  fill: cleared ? C.grn : C.gold,
                  fontSize: 9, fontWeight: 700,
                }}
              />
            );
          })}

          {/* Now line */}
          <ReferenceLine
            x={nowMs}
            stroke={C.wht} strokeWidth={0.7} opacity={0.25} strokeDasharray="3 4"
          />

          {/* Fill under observed temps */}
          <Area
            data={obsRows}
            type="monotone"
            dataKey="obs"
            stroke="none"
            fill={C.acc}
            fillOpacity={0.07}
            isAnimationActive={false}
            legendType="none"
          />

          {/* NWS Forecast line */}
          <Line
            data={allRows.filter(r => r.fcst != null)}
            type="monotone"
            dataKey="fcst"
            stroke={C.blue}
            strokeWidth={1.5}
            strokeDasharray="6 3"
            dot={false}
            isAnimationActive={false}
            name="NWS Forecast"
          />

          {/* Prediction tracking line (how the prediction evolved throughout the day) */}
          {predHistory.length > 1 && (
            <Line
              data={predHistory}
              type="monotone"
              dataKey="point"
              stroke={C.purp}
              strokeWidth={2}
              dot={{ r: 3, fill: C.purp, strokeWidth: 0 }}
              activeDot={{ r: 5, fill: C.purp, stroke: C.wht, strokeWidth: 1.5 }}
              isAnimationActive={false}
              name="Pred Track"
              opacity={0.85}
            />
          )}

          {/* Observed temperature line */}
          <Line
            data={obsRows}
            type="monotone"
            dataKey="obs"
            stroke={C.acc}
            strokeWidth={2.5}
            dot={{ r: 3, fill: C.acc, strokeWidth: 0 }}
            activeDot={{ r: 5, fill: C.acc, stroke: C.wht, strokeWidth: 1.5 }}
            isAnimationActive={false}
            name="Observed (KBOS)"
          />

          <Tooltip
            content={<ChartTooltip />}
            cursor={{ stroke: C.sub, strokeWidth: 1, strokeDasharray: "3 3" }}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="chart-legend" style={{ marginTop: 6 }}>
        <div className="legend-item">
          <div className="legend-dot" style={{ background: C.acc }} />
          Observed (KBOS)
        </div>
        <div className="legend-item">
          <div className="legend-dot" style={{ background: C.blue, borderRadius: 2 }} />
          NWS Forecast
        </div>
        {prediction && (
          <div className="legend-item">
            <div className="legend-dot" style={{ background: C.purp }} />
            Pred High {prediction.point.toFixed(1)}° ±{prediction.spread.toFixed(1)}° ({prediction.confidence})
          </div>
        )}
        {predHistory.length > 1 && (
          <div className="legend-item">
            <div className="legend-dot" style={{ background: C.purp, opacity: 0.5 }} />
            Pred Track ({predHistory.length} pts)
          </div>
        )}
        <div className="legend-item" style={{ marginLeft: "auto", color: C.gold, fontSize: 10 }}>
          ■ Peak window 11 AM – 6 PM
        </div>
      </div>
    </div>
  );
}
