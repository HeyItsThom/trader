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

export default function StatCards({ data }) {
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
  if (prediction) {
    const confColors = { High: C.grn, Medium: C.acc, Low: C.red, Locked: C.grn };
    predColor = confColors[prediction.confidence] ?? C.purp;
    predText = `${prediction.point.toFixed(1)}°F ±${prediction.spread.toFixed(1)}°\n${prediction.confidence} confidence`;
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
      </div>
    </div>
  );
}
