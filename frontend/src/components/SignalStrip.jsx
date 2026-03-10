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
        <Sig label="Rate of Change"   value="--" />
        <Sig label="Peak Window"      value="--" />
        <Sig label="High Set At"      value="--" />
        <Sig label="NWS Latest Temp"  value="--" />
        <Sig label="Wind (NWS)"       value="--" />
        <Sig label="Humidity (NWS)"   value="--" />
        <Sig label="Conditions (NWS)" value="--" />
      </div>
    );
  }

  const {
    velocity, trend, trend_conf, trend_conf_reason, peak, hi_time,
    metar_temp, metar_match,
    latest_wind_speed_mph, latest_wind_direction, latest_humidity, latest_conditions,
  } = data;

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

  // NWS /latest primary current-conditions display
  const latestTempText = metar_temp != null
    ? `${metar_temp.toFixed(1)}°F  ${metar_match ?? ""}`
    : "--";
  const latestTempColor = metar_match === "matches" ? C.grn
    : metar_match?.startsWith("Δ") ? C.gold
    : C.sub;

  // Wind from /latest: convert degrees to cardinal direction
  function degToCard(deg) {
    const dirs = ["N","NE","E","SE","S","SW","W","NW"];
    return dirs[Math.round(deg / 45) % 8];
  }
  const windText = latest_wind_speed_mph != null
    ? `${latest_wind_speed_mph} mph${latest_wind_direction != null ? " " + degToCard(latest_wind_direction) : ""}`
    : "--";

  const condText = latest_conditions ?? "--";
  const humText  = latest_humidity != null ? `${latest_humidity}% RH` : "--";

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

      <Sig label="Rate of Change"   value={velText}              color={velColor}        />
      <Sig label="Peak Window"      value={peak?.label ?? "--"}  color={peakColor}       />
      <Sig label="High Set At"      value={hi_time ?? "--"}                              />
      <Sig label="NWS Latest Temp"  value={latestTempText}       color={latestTempColor} />
      <Sig label="Wind (NWS)"       value={windText}             color={C.blue}          />
      <Sig label="Humidity (NWS)"   value={humText}              color={C.sub}           />
      <Sig label="Conditions (NWS)" value={condText}             color={C.wht}           />
    </div>
  );
}
