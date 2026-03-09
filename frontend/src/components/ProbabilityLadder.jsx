const SIGMA_FACTOR = 1.5;

function normCdf(x) {
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

function chipStyle(pct) {
  if (pct >= 80) return { bg: "#3fb950", fg: "#0d1117" };
  if (pct >= 55) return { bg: "#5a9e3c", fg: "#0d1117" };
  if (pct >= 45) return { bg: "#d29922", fg: "#0d1117" };
  if (pct >= 20) return { bg: "#c06020", fg: "#0d1117" };
  return { bg: "#3a1515", fg: "#e6edf3" };
}

export default function ProbabilityLadder({ data, thresholds = [] }) {
  const { prediction, day_high } = data ?? {};
  if (!prediction || day_high == null) {
    return (
      <div>
        <div className="prob-header">
          <h2>PROBABILITY LADDER</h2>
          <span>P(daily high &gt; X°F) for each degree near predicted high</span>
        </div>
        <div style={{ color: "#8b949e", fontSize: 12, padding: "4px 0" }}>Waiting for data…</div>
      </div>
    );
  }

  const sigma = prediction.spread / SIGMA_FACTOR;
  const lo = Math.floor(prediction.point - 3 * sigma);
  const hi = Math.ceil( prediction.point + 3 * sigma);

  const betInts = thresholds.map(t => Math.round(t));
  const allDegrees = [...new Set([
    ...Array.from({ length: hi - lo + 1 }, (_, i) => lo + i),
    ...betInts,
  ])].sort((a, b) => a - b);

  return (
    <div>
      <div className="prob-header">
        <h2>PROBABILITY LADDER</h2>
        <span>P(daily high &gt; X°F) for each integer degree near predicted high</span>
      </div>
      <div className="prob-chips">
        {allDegrees.map(deg => {
          const pct = Math.round(probExceed(deg, prediction, day_high) * 100);
          const { bg, fg } = chipStyle(pct);
          const isBet = betInts.some(b => Math.abs(b - deg) < 0.5);
          return (
            <div
              key={deg}
              className={`prob-chip${isBet ? " is-bet" : ""}`}
              style={{ background: bg }}
            >
              <span className="prob-chip-deg" style={{ color: fg }}>&gt; {deg}°</span>
              <span className="prob-chip-pct" style={{ color: fg }}>{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
