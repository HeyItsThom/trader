const C = {
  acc:  "#f0883e",
  red:  "#ff7b72",
  grn:  "#3fb950",
  blue: "#79c0ff",
  purp: "#bc8cff",
  sub:  "#8b949e",
};

function Card({ label, value, color }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color }}>{value}</div>
    </div>
  );
}

export default function StatCards({ data, bias = 0, calibDays = 0 }) {
  if (!data) {
    return (
      <div className="cards">
        <Card label="Current Temp"        value="--°F" color={C.acc}  />
        <Card label="Today's High (= WU)" value="--°F" color={C.red}  />
        <Card label="NWS Forecast High"   value="--°F" color={C.blue} />
        <Card label="Predicted High"      value="--"   color={C.purp} />
      </div>
    );
  }

  const { cur_temp, day_high, fcst_high, prediction } = data;

  let predText = "--";
  let predColor = C.purp;
  let biasLine = null;
  if (prediction) {
    const confColors = { High: C.grn, Medium: C.acc, Low: C.red, Locked: C.grn };
    predColor = confColors[prediction.confidence] ?? C.purp;
    predText  = `${prediction.point.toFixed(1)}°F ±${prediction.spread.toFixed(1)}°\n${prediction.confidence} confidence`;

    // Show calibration bias when it's non-trivial (≥0.1°)
    if (calibDays > 0 && Math.abs(bias) >= 0.1) {
      const sign      = bias > 0 ? "+" : "";
      const biasColor = Math.abs(bias) < 1 ? C.sub : bias > 0 ? C.grn : C.red;
      biasLine = (
        <div style={{ fontSize: "10px", color: biasColor, marginTop: "3px" }}>
          model bias {sign}{bias.toFixed(1)}° · {calibDays}d
        </div>
      );
    } else if (calibDays > 0) {
      biasLine = (
        <div style={{ fontSize: "10px", color: C.sub, marginTop: "3px" }}>
          calibrated · {calibDays}d
        </div>
      );
    } else {
      biasLine = (
        <div style={{ fontSize: "10px", color: C.sub, marginTop: "3px" }}>
          learning…
        </div>
      );
    }
  }

  return (
    <div className="cards">
      <Card label="Current Temp"        value={cur_temp != null ? `${cur_temp.toFixed(1)}°F` : "--"} color={C.acc}  />
      <Card label="Today's High (= WU)" value={day_high != null ? `${day_high.toFixed(1)}°F` : "--"} color={C.red}  />
      <Card label="NWS Forecast High"   value={fcst_high != null ? `${fcst_high.toFixed(0)}°F` : "--"} color={C.blue} />
      <div className="stat-card">
        <div className="stat-label">Predicted High</div>
        <div className="stat-value" style={{ color: predColor, fontSize: prediction ? "18px" : "26px", whiteSpace: "pre-line" }}>
          {predText}
        </div>
        {biasLine}
      </div>
    </div>
  );
}
