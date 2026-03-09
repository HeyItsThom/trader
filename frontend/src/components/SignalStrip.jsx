const C = {
  red:  "#ff7b72",
  grn:  "#3fb950",
  blue: "#79c0ff",
  gold: "#d29922",
  sub:  "#8b949e",
  wht:  "#e6edf3",
};

function Sig({ label, value, color }) {
  return (
    <div className="sig-cell">
      <div className="sig-label">{label}</div>
      <div className="sig-value" style={{ color: color ?? C.wht }}>{value}</div>
    </div>
  );
}

export default function SignalStrip({ data }) {
  if (!data) {
    return (
      <div className="signals">
        <Sig label="Rate of Change" value="--" />
        <Sig label="Trend"          value="─"  />
        <Sig label="Peak Window"    value="--" />
        <Sig label="High Set At"    value="--" />
        <Sig label="METAR / WU Now" value="--" />
      </div>
    );
  }

  const { velocity, trend, peak, hi_time, metar_temp, metar_match } = data;

  const velColor = velocity == null ? C.sub
    : velocity > 0.5 ? C.red
    : velocity < -0.5 ? C.blue
    : C.sub;

  const velText = velocity != null
    ? `${velocity >= 0 ? "+" : ""}${velocity.toFixed(1)}°F / hr`
    : "--";

  const trendColor = trend === "Rising" ? C.red : trend === "Falling" ? C.blue : C.sub;
  const trendIcon  = trend === "Rising" ? "▲" : trend === "Falling" ? "▼" : "─";

  const peakColor = peak?.open ? C.grn : C.sub;

  const metatText = metar_temp != null
    ? `${metar_temp.toFixed(1)}°F  ${metar_match ?? ""}`
    : "--";
  const metarColor = metar_match === "matches" ? C.grn
    : metar_match?.startsWith("Δ") ? C.gold
    : C.sub;

  return (
    <div className="signals">
      <Sig label="Rate of Change" value={velText}                color={velColor}  />
      <Sig label="Trend"          value={`${trend}  ${trendIcon}`} color={trendColor} />
      <Sig label="Peak Window"    value={peak?.label ?? "--"}     color={peakColor}  />
      <Sig label="High Set At"    value={hi_time ?? "--"}         />
      <Sig label="METAR / WU Now" value={metatText}               color={metarColor} />
    </div>
  );
}
