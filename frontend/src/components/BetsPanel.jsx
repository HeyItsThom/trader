import { useState } from "react";

const C = {
  grn:  "#3fb950",
  red:  "#ff7b72",
  gold: "#d29922",
  purp: "#bc8cff",
  sub:  "#8b949e",
  wht:  "#e6edf3",
  card: "#21262d",
  panel:"#161b22",
};

const TINT_GRN  = "#0f2a14";
const TINT_GOLD = "#211c00";
const TINT_RED  = "#200f0f";

const SIGMA_FACTOR = 1.5;

function normCdf(x) {
  // Abramowitz & Stegun approximation
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.7814779 + t * (-1.8212560 + t * 1.3302744))));
  return x > 0 ? 1 - p : p;
}

function probExceed(threshold, pred, curHigh) {
  if (curHigh >= threshold) return 1.0;
  const sigma = pred.spread / SIGMA_FACTOR;
  if (sigma <= 0) return pred.point >= threshold ? 1.0 : 0.0;
  const z = (threshold - pred.point) / sigma;
  return 1 - normCdf(z);
}

function BetBar({ thresh, curHigh, pred, color }) {
  const HALF = 6.0;
  const toPercent = (t) => Math.max(0, Math.min(100, ((t - (thresh - HALF)) / (2 * HALF)) * 100));

  const threshPct = toPercent(thresh);
  const curPct    = toPercent(curHigh ?? (thresh - HALF));
  const predLoPct = pred ? toPercent(pred.low)  : null;
  const predHiPct = pred ? toPercent(pred.high) : null;

  const fill = curHigh != null && curHigh >= thresh ? color : "#5a2020";

  return (
    <div className="bet-bar-wrap" style={{ background: C.panel }}>
      {/* Left zone (below thresh) */}
      <div style={{
        position: "absolute", left: 0, top: 4, bottom: 4,
        width: `${threshPct}%`, background: "#3a1515", borderRadius: "2px 0 0 2px"
      }} />
      {/* Right zone (above thresh) */}
      <div style={{
        position: "absolute", left: `${threshPct}%`, top: 4, bottom: 4, right: 0,
        background: "#0f2a14", borderRadius: "0 2px 2px 0"
      }} />
      {/* Prediction band */}
      {pred && predLoPct != null && (
        <div style={{
          position: "absolute",
          left: `${predLoPct}%`,
          width: `${predHiPct - predLoPct}%`,
          top: "33%", bottom: "33%",
          background: "#bc8cff55",
        }} />
      )}
      {/* Filled bar */}
      {curHigh != null && (
        <div style={{
          position: "absolute", left: 0, top: 6, bottom: 6,
          width: `${curPct}%`, background: fill, borderRadius: "3px",
        }} />
      )}
      {/* Threshold line */}
      <div style={{
        position: "absolute", left: `${threshPct}%`, top: 0, bottom: 0,
        width: 2, background: C.wht,
      }} />
      {/* Current high line */}
      {curHigh != null && (
        <div style={{
          position: "absolute", left: `${curPct}%`, top: 0, bottom: 0,
          width: 2, background: C.acc,
        }} />
      )}
      {/* Edge labels */}
      <span style={{ position: "absolute", left: 4, top: "50%", transform: "translateY(-50%)", fontSize: 9, color: C.sub }}>
        {(thresh - HALF).toFixed(0)}°
      </span>
      <span style={{ position: "absolute", right: 4, top: "50%", transform: "translateY(-50%)", fontSize: 9, color: C.sub }}>
        {(thresh + HALF).toFixed(0)}°
      </span>
      <span style={{
        position: "absolute", left: `${threshPct}%`, top: 1,
        transform: "translateX(-50%)", fontSize: 9, color: C.wht, fontWeight: 700,
      }}>
        {thresh.toFixed(0)}°
      </span>
    </div>
  );
}

export default function BetsPanel({ data, thresholds, setThresholds }) {
  const [editing, setEditing] = useState(false);
  const [editVal, setEditVal] = useState("");

  const { day_high, prediction } = data ?? {};

  function openEdit() {
    setEditVal(thresholds.join(", "));
    setEditing(true);
  }

  function saveEdit() {
    try {
      const nums = [...new Set(
        editVal.split(",").map(s => parseFloat(s.trim())).filter(n => !isNaN(n))
      )].sort((a, b) => a - b);
      if (!nums.length) return;
      setThresholds(nums);
      setEditing(false);
    } catch {}
  }

  return (
    <div>
      <div className="bets-header" style={{ display: "flex", alignItems: "center", gap: 8, margin: "12px 0 4px" }}>
        <h2 style={{ fontSize: 13, fontWeight: 700, color: C.wht }}>YOUR ROBINHOOD BETS</h2>
        <span style={{ fontSize: 11, color: C.sub }}>&gt; X°F thresholds</span>
        <button className="btn" style={{ marginLeft: "auto" }} onClick={openEdit}>
          Edit Thresholds
        </button>
      </div>

      {editing && (
        <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
          <input
            value={editVal}
            onChange={e => setEditVal(e.target.value)}
            style={{
              background: "#21262d", border: "1px solid #30363d", borderRadius: 6,
              color: "#e6edf3", padding: "4px 10px", fontSize: 13, flex: 1,
            }}
            placeholder="41, 42, 45"
            autoFocus
            onKeyDown={e => e.key === "Enter" && saveEdit()}
          />
          <button className="btn" onClick={saveEdit} style={{ color: "#3fb950" }}>Save</button>
          <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      )}

      {thresholds.length === 0 && (
        <p style={{ color: C.sub, fontSize: 12, padding: "8px 0" }}>
          No thresholds set. Click "Edit Thresholds" to add your bets.
        </p>
      )}

      {thresholds.map(thresh => {
        const cur = day_high ?? 0;
        const pct = prediction ? probExceed(thresh, prediction, cur) : null;
        const pctStr = pct != null ? `${Math.round(pct * 100)}%` : "--";

        let status, statusColor, tint, detail;
        if (cur >= thresh) {
          status = `✅  CLEARED  (100%)`;
          statusColor = C.grn; tint = TINT_GRN;
          detail = `+${(cur - thresh).toFixed(1)}°  above  (locked in)`;
        } else if (prediction && pct != null && pct >= 0.70) {
          status = `📈  LIKELY  (${pctStr})`;
          statusColor = C.grn; tint = TINT_GRN;
          detail = `pred ${prediction.point.toFixed(1)}°  (+${(prediction.point - thresh).toFixed(1)}°)  range ${prediction.low.toFixed(1)}–${prediction.high.toFixed(1)}°  [${prediction.confidence} conf]`;
        } else if (prediction && pct != null && pct >= 0.35) {
          status = `⚠  CLOSE  (${pctStr})`;
          statusColor = C.gold; tint = TINT_GOLD;
          detail = `pred ${prediction.point.toFixed(1)}°  (${(prediction.point - thresh) >= 0 ? "+" : ""}${(prediction.point - thresh).toFixed(1)}°)  range ${prediction.low.toFixed(1)}–${prediction.high.toFixed(1)}°  [${prediction.confidence} conf]`;
        } else {
          status = `❌  UNLIKELY  (${pctStr})`;
          statusColor = C.red; tint = TINT_RED;
          detail = prediction
            ? `pred ${prediction.point.toFixed(1)}°  (${(prediction.point - thresh).toFixed(1)}°)  [${prediction.confidence} conf]`
            : "no prediction";
        }

        return (
          <div key={thresh} className="bet-row" style={{ background: tint }}>
            <div className="bet-thresh" style={{ color: statusColor }}>
              &gt; {thresh.toFixed(0)}°F
            </div>
            <BetBar thresh={thresh} curHigh={day_high} pred={prediction} color={statusColor} />
            <div className="bet-status" style={{ color: statusColor }}>{status}</div>
            <div className="bet-detail">{detail}</div>
          </div>
        );
      })}
    </div>
  );
}
