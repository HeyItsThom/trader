const C = {
  red:  "#ff7b72",
  grn:  "#3fb950",
  blue: "#79c0ff",
  gold: "#d29922",
  sub:  "#8b949e",
  wht:  "#e6edf3",
  purp: "#bc8cff",
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
        <div className="sig-cell sig-cell--trend">
          <div className="sig-label">Trend</div>
          <div className="sig-trend-icon">─</div>
          <div className="sig-trend-conf" style={{ color: C.sub }}>-- confidence</div>
        </div>
        <Sig label="Rate of Change" value="--" />
        <Sig label="Peak Window"    value="--" />
        <Sig label="High Set At"    value="--" />
        <Sig label="METAR / WU Now" value="--" />
      </div>
    );
  }

  const { velocity, trend, trend_conf, trend_conf_reason, peak, hi_time, metar_temp, metar_match, metar_time, metar_source } = data;

  const velColor = velocity == null ? C.sub
    : velocity > 0.5 ? C.red
    : velocity < -0.5 ? C.blue
    : C.sub;

  const velText = velocity != null
    ? `${velocity >= 0 ? "+" : ""}${velocity.toFixed(1)}°F / hr`
    : "--";

  const trendColor = trend === "Rising" ? C.red : trend === "Falling" ? C.blue : C.sub;
  const trendIcon  = trend === "Rising" ? "▲" : trend === "Falling" ? "▼" : "─";

  const confColor = trend_conf === "High" ? C.grn
    : trend_conf === "Medium" ? C.gold
    : C.sub;

  const peakColor = peak?.open ? C.grn : C.sub;

  const metarVal = metar_temp != null
    ? `${metar_temp.toFixed(1)}°F  ${metar_match ?? ""}`
    : "--";
  const metarSub = [metar_time, metar_source].filter(Boolean).join(" · ");
  const metarColor = metar_match === "matches" ? C.grn
    : metar_match?.startsWith("Δ") ? C.gold
    : C.sub;

  return (
    <div className="signals">
      {/* Trend — enlarged cell spanning 2 columns */}
      <div className="sig-cell sig-cell--trend">
        <div className="sig-label">Trend</div>
        <div className="sig-trend-icon" style={{ color: trendColor }}>
          {trendIcon} {trend}
        </div>
        <div className="sig-trend-conf" style={{ color: confColor }}>
          {trend_conf ?? "Low"} confidence
          {trend_conf_reason ? <span style={{ color: C.sub }}> · {trend_conf_reason}</span> : null}
        </div>
      </div>

      <Sig label="Rate of Change" value={velText}              color={velColor}   />
      <Sig label="Peak Window"    value={peak?.label ?? "--"}  color={peakColor}  />
      <Sig label="High Set At"    value={hi_time ?? "--"}                         />
      <div className="sig-cell">
        <div className="sig-label">METAR / WU Now</div>
        <div className="sig-value" style={{ color: metarColor }}>{metarVal}</div>
        {metarSub && <div style={{ fontSize: "11px", color: C.sub, marginTop: "2px" }}>{metarSub}</div>}
      </div>
    </div>
  );
}
