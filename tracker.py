#!/usr/bin/env python3
"""
Boston Temperature Tracker — Trading Edition
=============================================
Designed for Robinhood daily high-temperature prediction markets.

Data sources (all free, no API key):
  • NWS KBOS observations  — same ASOS data Weather Underground shows for Boston
  • tgftp.weather.gov METAR — raw METAR WU uses for current conditions
  • NWS hourly forecast     — projected temps overlaid on graph

Predictor blends NWS forecast + trend extrapolation, weighted by time-of-day.
Refreshes every 2 minutes so you catch KBOS METAR updates (~:20 and :50 past hour).

Run:  python tracker.py   (or: bash run.sh)
Requires: pip install requests matplotlib
"""

import re
import sys
import traceback as _tb

# ── Dependency checks (print a clear message before the window even opens) ───
def _die(msg: str) -> None:
    print(f"\n❌  {msg}", file=sys.stderr)
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import simpledialog, messagebox
    # Smoke-test: make sure Tcl/Tk is actually linked (not just importable)
    _r = tk.Tk()
    _r.withdraw()
    _r.destroy()
    del _r
except ImportError:
    _die(
        "tkinter is not available in this Python installation.\n\n"
        "  macOS fix A — python.org build (recommended):\n"
        "    Download Python from https://www.python.org/downloads/\n"
        "    It bundles Tcl/Tk automatically.\n\n"
        "  macOS fix B — Homebrew:\n"
        "    brew install python-tk@3.12   (replace 3.12 with your version)\n"
        "    Then re-run:  bash run.sh"
    )
except Exception as _e:
    _die(
        f"tkinter failed to initialise: {_e}\n"
        "  This usually means no display is available or Tcl/Tk is broken.\n"
        "  Try running the script directly in a Terminal window (not via SSH)."
    )

try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError as _e:
    _die(
        f"matplotlib not found: {_e}\n"
        "  Fix:  pip install matplotlib\n"
        "  Or:   bash run.sh   (installs everything automatically)"
    )

try:
    import requests
except ImportError:
    _die(
        "requests not found.\n"
        "  Fix:  pip install requests\n"
        "  Or:   bash run.sh   (installs everything automatically)"
    )

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import threading
import webbrowser
import zoneinfo

# ── Constants ────────────────────────────────────────────────────────────────
STATION_ID    = "KBOS"
NWS_OBS_URL   = f"https://api.weather.gov/stations/{STATION_ID}/observations"
NWS_POINTS    = "https://api.weather.gov/points/42.3601,-71.0589"
METAR_URL     = f"https://tgftp.weather.gov/data/observations/metar/stations/{STATION_ID}.TXT"
WU_URL        = f"https://www.wunderground.com/history/daily/us/ma/boston/{STATION_ID}"
HEADERS       = {"User-Agent": "BostonTempTracker/1.0 (personal trading tool)"}

REFRESH_MS     = 60 * 1000   # 60 s — catch METAR updates as soon as they land
FCST_REFRESH_M = 30          # re-fetch NWS hourly forecast every 30 min
EASTERN_TZ     = zoneinfo.ZoneInfo("America/New_York")

# Typical Boston daily high hour by month (NOAA)
PEAK_HOUR    = {1:13,2:13,3:14,4:14,5:14,6:15,7:15,8:15,9:14,10:14,11:13,12:13}
PEAK_WINDOW  = (11, 18)

# Boston NOAA Climate Normals 1991–2020 avg daily max (°F)
MONTHLY_AVG_HIGH = {1:36.4,2:38.9,3:46.6,4:56.5,5:66.7,6:76.1,
                    7:81.8,8:79.6,9:72.4,10:62.0,11:52.6,12:40.8}

# Default Robinhood ">X°F" bet thresholds — edit in-app or change here
DEFAULT_THRESHOLDS: list[float] = [41, 42]

# ── Colour palette ────────────────────────────────────────────────────────────
BG    = "#0d1117"
PANEL = "#161b22"
CARD  = "#21262d"
ACC   = "#f0883e"   # orange  — observed line
RED   = "#ff7b72"   # red     — day high / unlikely bets
GRN   = "#3fb950"   # green   — cleared bets
BLUE  = "#79c0ff"   # blue    — NWS forecast
PURP  = "#bc8cff"   # purple  — prediction
GOLD  = "#d29922"   # gold    — borderline / METAR
SUB   = "#8b949e"   # grey    — secondary text
WHT   = "#e6edf3"   # white   — primary text

# Row bg tints used in bet rows
TINT_GRN  = "#0f2a14"
TINT_GOLD = "#211c00"
TINT_RED  = "#200f0f"


# ── Data helpers ──────────────────────────────────────────────────────────────
def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def peak_status(now_et: datetime) -> tuple[bool, str]:
    h = now_et.hour + now_et.minute / 60
    if h < PEAK_WINDOW[0]:
        return False, f"Opens in {PEAK_WINDOW[0]-h:.1f}h"
    if h < PEAK_WINDOW[1]:
        return True, f"OPEN  {PEAK_WINDOW[1]-h:.1f}h left"
    return False, "Closed"


def velocity(times: list, temps: list, window_hr: float = 1.0) -> Optional[float]:
    if len(times) < 2:
        return None
    cutoff = times[-1] - timedelta(hours=window_hr)
    pairs  = [(t, v) for t, v in zip(times, temps) if t >= cutoff]
    if len(pairs) < 2:
        return None
    dt_hr = (pairs[-1][0] - pairs[0][0]).total_seconds() / 3600
    return None if dt_hr < 0.05 else (pairs[-1][1] - pairs[0][1]) / dt_hr


# ── Prediction engine ─────────────────────────────────────────────────────────
@dataclass
class Prediction:
    point:      float   # best single-value estimate
    low:        float   # lower bound of range
    high:       float   # upper bound of range
    spread:     float   # half-width of range (= (high-low)/2)
    confidence: str     # "High" / "Medium" / "Low" / "Locked"
    detail:     str     # human-readable explanation


def predict_high(obs_times: list, obs_temps: list,
                 fcst_times: list, fcst_temps: list,
                 now_et: datetime) -> Optional[Prediction]:
    """
    Blend NWS forecast + trend extrapolation, weighted by time of day.
    Confidence range targets ±1–2°F for per-degree Robinhood betting.
    """
    if not obs_temps:
        return None

    cur_high  = max(obs_temps)
    month     = now_et.month
    hour_frac = now_et.hour + now_et.minute / 60
    peak_hr   = PEAK_HOUR[month]

    # ── Past peak window: observed high is final ──────────────────────────────
    if hour_frac >= PEAK_WINDOW[1]:
        return Prediction(
            point=cur_high, low=cur_high - 0.3, high=cur_high + 0.3,
            spread=0.3, confidence="Locked",
            detail=f"Peak window closed — {cur_high:.1f}°F is the day's high",
        )

    # ── Signal 1: NWS forecast max for today ─────────────────────────────────
    today     = now_et.date()
    fcst_vals = [v for t, v in zip(fcst_times, fcst_temps) if t.date() == today]
    fcst_high = max(fcst_vals) if fcst_vals else None

    # ── Signal 2: trend extrapolation ────────────────────────────────────────
    vel = velocity(obs_times, obs_temps, 1.0) or velocity(obs_times, obs_temps, 2.0) or 0.0
    # Don't project further rise if we're past typical peak and already falling
    hours_to_peak = max(0.0, peak_hr - hour_frac)
    if vel < 0 and hour_frac > peak_hr:
        hours_to_peak = 0.0
    trend_high = max(cur_high, obs_temps[-1] + vel * hours_to_peak)

    # ── Blend weights by time of day ─────────────────────────────────────────
    if hour_frac < 10:
        wf, wt = 0.80, 0.20          # early: lean on forecast
    elif hour_frac < peak_hr:
        wf, wt = 0.55, 0.45
    else:
        wf, wt = 0.30, 0.70          # post-peak: lean on observed trend

    point = (wf * fcst_high + wt * trend_high) if fcst_high else trend_high
    point = max(point, cur_high)     # prediction can't be below current high

    # ── Confidence & spread ───────────────────────────────────────────────────
    # Tight ranges (±1–2°F) to be useful for per-degree bets
    if fcst_high is not None:
        disagree = abs(fcst_high - trend_high)
        if disagree <= 1.5:
            conf, spread = "High",   1.5
        elif disagree <= 3.5:
            conf, spread = "Medium", 2.5
        else:
            conf, spread = "Low",    4.0
    else:
        conf, spread = "Low", 4.0

    # Near the peak hour with good velocity data → tighten further
    if conf == "High" and abs(hour_frac - peak_hr) < 1.0 and fcst_high:
        spread = 1.0

    detail_parts = []
    if fcst_high:
        detail_parts.append(f"NWS {fcst_high:.0f}°F (×{wf:.2f})")
    detail_parts.append(
        f"trend {trend_high:.1f}°F (vel {vel:+.1f}°/hr, ×{wt:.2f})"
    )
    detail = "  +  ".join(detail_parts)

    return Prediction(
        point=point,
        low=point - spread,
        high=point + spread,
        spread=spread,
        confidence=conf,
        detail=detail,
    )


# ── METAR parser ──────────────────────────────────────────────────────────────
def parse_metar_temp(raw: str) -> tuple[Optional[float], Optional[str]]:
    lines      = raw.strip().splitlines()
    metar_line = lines[-1] if len(lines) >= 2 else raw.strip()

    obs_time_str = None
    if len(lines) >= 2:
        try:
            dt = datetime.strptime(lines[0].strip(), "%Y/%m/%d %H:%M")
            dt_et = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(EASTERN_TZ)
            obs_time_str = dt_et.strftime("%I:%M %p ET").lstrip("0")
        except Exception:
            pass

    # T-group remark (0.1°C precision)
    t_match = re.search(r"\bT([01])(\d{3})([01]\d{3})\b", metar_line)
    if t_match:
        sign   = -1 if t_match.group(1) == "1" else 1
        temp_c = sign * int(t_match.group(2)) / 10.0
        return c_to_f(temp_c), obs_time_str

    # Standard SS/DD field
    td_match = re.search(r"\b(M?\d{2})/(M?\d{2})\b", metar_line)
    if td_match:
        raw_t  = td_match.group(1)
        sign   = -1 if raw_t.startswith("M") else 1
        temp_c = sign * int(raw_t.replace("M", ""))
        return c_to_f(temp_c), obs_time_str

    return None, obs_time_str


# ── Data fetchers ─────────────────────────────────────────────────────────────
def fetch_observations() -> tuple[list, list]:
    resp  = requests.get(NWS_OBS_URL, headers=HEADERS, params={"limit": 150}, timeout=20)
    resp.raise_for_status()
    today = datetime.now(EASTERN_TZ).date()
    times, temps = [], []
    for feat in reversed(resp.json().get("features", [])):
        props  = feat.get("properties", {})
        temp_c = props.get("temperature", {}).get("value")
        ts     = props.get("timestamp")
        if temp_c is None or ts is None:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(EASTERN_TZ)
        if dt.date() == today:
            times.append(dt)
            temps.append(c_to_f(temp_c))
    return times, temps


def fetch_forecast() -> tuple[list, list, Optional[float]]:
    try:
        r1       = requests.get(NWS_POINTS, headers=HEADERS, timeout=15)
        r1.raise_for_status()
        fcst_url = r1.json()["properties"]["forecastHourly"]
        r2       = requests.get(fcst_url, headers=HEADERS, timeout=15)
        r2.raise_for_status()
        today    = datetime.now(EASTERN_TZ).date()
        times, temps = [], []
        for p in r2.json()["properties"]["periods"]:
            dt = datetime.fromisoformat(p["startTime"]).astimezone(EASTERN_TZ)
            if dt.date() < today:
                continue
            if dt.date() > today:
                break
            tf = p["temperature"]
            if p["temperatureUnit"] == "C":
                tf = c_to_f(tf)
            times.append(dt)
            temps.append(tf)
        return times, temps, (max(temps) if temps else None)
    except Exception:
        return [], [], None


def fetch_metar() -> tuple[Optional[float], Optional[str]]:
    try:
        resp   = requests.get(METAR_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        temp_f, obs_time = parse_metar_temp(resp.text)
        return temp_f, obs_time
    except Exception:
        return None, None


# ── Main application ──────────────────────────────────────────────────────────
class BostonTempTracker:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Boston Temp Tracker  •  KBOS")
        self.root.configure(bg=BG)
        self.root.minsize(960, 700)

        # ── State ──────────────────────────────────────────────────────────────
        self.obs_times:   list = []
        self.obs_temps:   list = []
        self.fcst_times:  list = []
        self.fcst_temps:  list = []
        self.day_high:    Optional[float] = None
        self.prev_high:   Optional[float] = None
        self.fcst_high:   Optional[float] = None
        self.metar_temp:  Optional[float] = None
        self.metar_time:  Optional[str]   = None
        self.prediction:  Optional[Prediction] = None
        self.thresholds:  list[float] = list(DEFAULT_THRESHOLDS)
        self._refresh_job    = None
        self._secs_left      = 0
        self._last_fcst_fetch: Optional[datetime] = None
        self._last_obs_time:   Optional[datetime] = None   # time of newest obs

        self._build_ui()
        self._launch_refresh(fetch_fcst=True, delay_ms=200)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill="x", padx=16, pady=(12, 4))

        tk.Label(hdr, text="Boston Temp Tracker",
                 font=("Helvetica", 20, "bold"), fg=WHT, bg=BG).pack(side="left")
        tk.Label(hdr, text="  KBOS  ·  Logan Airport",
                 font=("Helvetica", 12), fg=SUB, bg=BG).pack(side="left", pady=(3, 0))

        # Live clock — ticks every second
        self.lbl_clock = tk.Label(hdr, text="",
                                  font=("Helvetica", 12, "bold"), fg=ACC, bg=BG)
        self.lbl_clock.pack(side="left", padx=(18, 0), pady=(3, 0))

        right = tk.Frame(hdr, bg=BG)
        right.pack(side="right")

        self.lbl_status = tk.Label(right, text="Loading…",
                                   font=("Helvetica", 11), fg=SUB, bg=BG)
        self.lbl_status.pack(side="right", padx=(12, 0))

        self.lbl_countdown = tk.Label(right, text="",
                                      font=("Helvetica", 10), fg=SUB, bg=BG)
        self.lbl_countdown.pack(side="right", padx=(0, 6))

        tk.Button(right, text="Open WU ↗",
                  command=lambda: webbrowser.open(WU_URL),
                  bg=CARD, fg=BLUE, font=("Helvetica", 10), relief="flat",
                  padx=8, pady=3, cursor="hand2").pack(side="right", padx=3)

        tk.Button(right, text="⟳ Refresh",
                  command=self._manual_refresh,
                  bg=CARD, fg=WHT, font=("Helvetica", 10), relief="flat",
                  padx=8, pady=3, cursor="hand2").pack(side="right", padx=3)

        # ── Stat cards ────────────────────────────────────────────────────────
        cards = tk.Frame(self.root, bg=BG)
        cards.pack(fill="x", padx=16, pady=(4, 0))
        for i in range(4):
            cards.columnconfigure(i, weight=1, uniform="card")

        self.lbl_cur    = self._card(cards, "Current Temp",        "--°F", ACC,  0)
        self.lbl_high   = self._card(cards, "Today's High  (= WU)","--°F", RED,  1)
        self.lbl_fcst   = self._card(cards, "NWS Forecast High",   "--°F", BLUE, 2)
        self.lbl_pred   = self._card(cards, "Predicted High",      "--",   PURP, 3)

        # ── Signals strip ─────────────────────────────────────────────────────
        sig = tk.Frame(self.root, bg=PANEL)
        sig.pack(fill="x", padx=16, pady=(6, 0))
        for i in range(5):
            sig.columnconfigure(i, weight=1, uniform="sig")

        self.lbl_vel    = self._sig(sig, "Rate of Change", "--",  0)
        self.lbl_trend  = self._sig(sig, "Trend",          "─",   1)
        self.lbl_peak   = self._sig(sig, "Peak Window",    "--",  2)
        self.lbl_hi_t   = self._sig(sig, "High Set At",    "--",  3)
        self.lbl_metar  = self._sig(sig, "METAR / WU Now", "--",  4)

        # ── Graph ─────────────────────────────────────────────────────────────
        self.fig = plt.Figure(figsize=(10, 3.4), facecolor=PANEL)
        self.ax  = self.fig.add_subplot(111)
        self.ax.set_facecolor(CARD)

        gf = tk.Frame(self.root, bg=BG)
        gf.pack(fill="both", expand=True, padx=16, pady=(6, 0))
        self.canvas = FigureCanvasTkAgg(self.fig, master=gf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # ── Bets panel ────────────────────────────────────────────────────────
        bets_header = tk.Frame(self.root, bg=BG)
        bets_header.pack(fill="x", padx=16, pady=(8, 2))

        tk.Label(bets_header, text="YOUR ROBINHOOD BETS",
                 font=("Helvetica", 12, "bold"), fg=WHT, bg=BG).pack(side="left")
        tk.Label(bets_header, text="  > X°F thresholds",
                 font=("Helvetica", 10), fg=SUB, bg=BG).pack(side="left")

        tk.Button(bets_header, text="Edit Thresholds",
                  command=self._edit_thresholds,
                  bg=CARD, fg=SUB, font=("Helvetica", 10), relief="flat",
                  padx=8, pady=2, cursor="hand2").pack(side="right")

        self.bets_frame = tk.Frame(self.root, bg=BG)
        self.bets_frame.pack(fill="x", padx=16, pady=(0, 10))
        self.bet_widgets: dict = {}
        self._populate_bets()

        # ── Footer ────────────────────────────────────────────────────────────
        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=16, pady=(0, 6))
        tk.Label(foot,
                 text="Data: NWS KBOS = WU  ·  METAR = WU current conditions  ·  Refreshes every 2 min",
                 font=("Helvetica", 9), fg=SUB, bg=BG).pack(side="left")

        self._tick()

    def _card(self, parent, label, val, color, col):
        f = tk.Frame(parent, bg=CARD, padx=14, pady=10)
        f.grid(row=0, column=col, padx=3, pady=3, sticky="nsew")
        tk.Label(f, text=label, font=("Helvetica", 10), fg=SUB, bg=CARD).pack(anchor="w")
        lbl = tk.Label(f, text=val, font=("Helvetica", 26, "bold"), fg=color, bg=CARD)
        lbl.pack(anchor="w")
        return lbl

    def _sig(self, parent, label, val, col):
        f = tk.Frame(parent, bg=PANEL, padx=12, pady=6)
        f.grid(row=0, column=col, padx=1, sticky="nsew")
        tk.Label(f, text=label, font=("Helvetica", 9), fg=SUB, bg=PANEL).pack(anchor="w")
        lbl = tk.Label(f, text=val, font=("Helvetica", 12, "bold"), fg=WHT, bg=PANEL)
        lbl.pack(anchor="w")
        return lbl

    # ── Bets panel ────────────────────────────────────────────────────────────

    def _populate_bets(self):
        """Build one row per threshold. Called on init and after editing."""
        for w in self.bets_frame.winfo_children():
            w.destroy()
        self.bet_widgets = {}

        if not self.thresholds:
            tk.Label(self.bets_frame,
                     text='No thresholds set. Click "Edit Thresholds" to add your bets.',
                     font=("Helvetica", 11), fg=SUB, bg=BG).pack(anchor="w", pady=6)
            return

        for thresh in sorted(self.thresholds):
            row = tk.Frame(self.bets_frame, bg=CARD, pady=7, padx=14)
            row.pack(fill="x", pady=2)
            row.columnconfigure(1, weight=1)

            # Threshold label
            lbl_thresh = tk.Label(row, text=f"> {thresh:.0f}°F",
                                  font=("Helvetica", 16, "bold"), fg=WHT, bg=CARD,
                                  width=8, anchor="w")
            lbl_thresh.grid(row=0, column=0, padx=(0, 12))

            # Bar canvas
            bar = tk.Canvas(row, height=28, bg=PANEL, highlightthickness=0)
            bar.grid(row=0, column=1, sticky="ew", padx=(0, 12))
            row.update_idletasks()

            # Status badge
            lbl_status = tk.Label(row, text="--",
                                  font=("Helvetica", 13, "bold"), fg=SUB, bg=CARD,
                                  width=13, anchor="w")
            lbl_status.grid(row=0, column=2, padx=(0, 8))

            # Delta / detail
            lbl_detail = tk.Label(row, text="",
                                  font=("Helvetica", 11), fg=SUB, bg=CARD,
                                  anchor="w")
            lbl_detail.grid(row=0, column=3, sticky="w")

            self.bet_widgets[thresh] = {
                "row": row,
                "lbl_thresh": lbl_thresh,
                "bar": bar,
                "lbl_status": lbl_status,
                "lbl_detail": lbl_detail,
            }

    def _update_bets(self):
        """Refresh colours, bars, and status text for all bet rows."""
        if self.day_high is None:
            return
        p   = self.prediction
        cur = self.day_high

        for thresh, w in self.bet_widgets.items():
            # ── Determine status ──────────────────────────────────────────────
            if cur >= thresh:
                status, status_col, tint = "✅  CLEARED", GRN, TINT_GRN
                detail = f"+{cur - thresh:.1f}°  above  (locked in)"
            elif p and p.low >= thresh:
                status, status_col, tint = "📈  LIKELY", GRN, TINT_GRN
                detail = (f"pred {p.point:.1f}°  (+{p.point - thresh:.1f}°)  "
                          f"range {p.low:.1f}–{p.high:.1f}°  [{p.confidence} conf]")
            elif p and p.high >= thresh:
                status, status_col, tint = "⚠   CLOSE", GOLD, TINT_GOLD
                detail = (f"pred {p.point:.1f}°  ({p.point - thresh:+.1f}°)  "
                          f"range {p.low:.1f}–{p.high:.1f}°  [{p.confidence} conf]")
            else:
                status, status_col, tint = "❌  UNLIKELY", RED, TINT_RED
                pred_str = (f"pred {p.point:.1f}° ({p.point-thresh:+.1f}°)"
                            if p else "no prediction")
                detail = pred_str + (f"  [{p.confidence} conf]" if p else "")

            # ── Update widgets ────────────────────────────────────────────────
            row = w["row"]
            row.config(bg=tint)
            for child in row.winfo_children():
                if not isinstance(child, tk.Canvas):
                    child.config(bg=tint)

            w["lbl_thresh"].config(fg=status_col)
            w["lbl_status"].config(text=status,  fg=status_col)
            w["lbl_detail"].config(text=detail,  fg=SUB if tint == TINT_GRN else status_col)

            # ── Draw bar ──────────────────────────────────────────────────────
            self.root.update_idletasks()
            self._draw_bar(w["bar"], thresh, cur, p, status_col, tint)

    def _draw_bar(self, canvas: tk.Canvas, thresh: float, cur_high: float,
                  pred: Optional[Prediction], color: str, tint: str):
        """
        Horizontal bar showing cur_high position relative to thresh.
        Range: thresh ± 6°F  (12°F window centred on the threshold).
        """
        canvas.delete("all")
        canvas.update_idletasks()
        W = canvas.winfo_width()
        H = canvas.winfo_height()
        if W < 10:
            W = 300   # fallback before first layout pass

        HALF = 6.0    # °F either side of threshold

        def to_x(t: float) -> float:
            return (t - (thresh - HALF)) / (2 * HALF) * W

        thresh_x = to_x(thresh)
        cur_x    = max(0.0, min(float(W), to_x(cur_high)))

        # Background
        canvas.create_rectangle(0, 0, W, H, fill=PANEL, outline="")

        # Left half (below threshold) — always dim red
        canvas.create_rectangle(0, 4, max(0, thresh_x), H - 4,
                                 fill="#3a1515", outline="")

        # Right half (above threshold) — dim green
        canvas.create_rectangle(min(W, thresh_x), 4, W, H - 4,
                                 fill="#0f2a14", outline="")

        # Predicted range band (purple, semi-transparent look)
        if pred:
            px0 = max(0.0, min(float(W), to_x(pred.low)))
            px1 = max(0.0, min(float(W), to_x(pred.high)))
            canvas.create_rectangle(px0, H // 3, px1, 2 * H // 3,
                                     fill=PURP, outline="", stipple="gray50")

        # Filled bar from left to cur_high
        fill = color if cur_high >= thresh else "#5a2020"
        canvas.create_rectangle(0, 6, cur_x, H - 6, fill=fill, outline="")

        # Threshold line (bright white divider)
        canvas.create_line(thresh_x, 0, thresh_x, H, fill=WHT, width=2)

        # Current high marker line (orange)
        canvas.create_line(cur_x, 0, cur_x, H, fill=ACC, width=2)

        # Edge labels
        canvas.create_text(4, H // 2,
                            text=f"{thresh - HALF:.0f}°",
                            fill=SUB, font=("Helvetica", 8), anchor="w")
        canvas.create_text(W - 4, H // 2,
                            text=f"{thresh + HALF:.0f}°",
                            fill=SUB, font=("Helvetica", 8), anchor="e")

        # Threshold label just above the line
        canvas.create_text(thresh_x, 2,
                            text=f"{thresh:.0f}°",
                            fill=WHT, font=("Helvetica", 8, "bold"), anchor="n")

    def _edit_thresholds(self):
        current = ", ".join(str(int(t) if t == int(t) else t)
                            for t in sorted(self.thresholds))
        result = simpledialog.askstring(
            "Edit Bet Thresholds",
            "Enter the Robinhood '>X°F' thresholds you're betting on,\n"
            "comma-separated (e.g.  41, 42, 45):",
            initialvalue=current,
            parent=self.root,
        )
        if not result:
            return
        try:
            nums = sorted(set(float(x.strip()) for x in result.split(",")))
            if not nums:
                raise ValueError("No valid numbers entered")
            self.thresholds = nums
            self._populate_bets()
            self._update_bets()
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.root)

    # ── Refresh logic ─────────────────────────────────────────────────────────

    def _launch_refresh(self, fetch_fcst: bool = False, delay_ms: int = 0):
        def work():
            try:
                obs_t, obs_f = fetch_observations()
                metar_f, metar_ts = fetch_metar()

                if fetch_fcst:
                    fct_t, fct_f, fhi = fetch_forecast()
                    self._last_fcst_fetch = datetime.now(EASTERN_TZ)
                else:
                    fct_t, fct_f, fhi = self.fcst_times, self.fcst_temps, self.fcst_high

                self.root.after(0, lambda: self._apply(
                    obs_t, obs_f, fct_t, fct_f, fhi, metar_f, metar_ts))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._set_status(f"⚠  {msg}", RED))
            finally:
                self._refresh_job = self.root.after(REFRESH_MS, self._auto_refresh)
                self._secs_left   = REFRESH_MS // 1000

        t = threading.Thread(target=work, daemon=True)
        self.root.after(delay_ms, t.start) if delay_ms else t.start()

    def _auto_refresh(self):
        need_fcst = (self._last_fcst_fetch is None or
                     (datetime.now(EASTERN_TZ) - self._last_fcst_fetch
                      ).total_seconds() / 60 >= FCST_REFRESH_M)
        self._launch_refresh(fetch_fcst=need_fcst)

    def _manual_refresh(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._set_status("Refreshing…", SUB)
        self._launch_refresh(fetch_fcst=True)

    # ── Apply data ────────────────────────────────────────────────────────────

    def _apply(self, obs_t, obs_f, fct_t, fct_f, fhi, metar_f, metar_ts):
        if not obs_f:
            self._set_status("No observations yet for today", SUB)
            return

        self.prev_high  = self.day_high
        self.obs_times  = obs_t
        self.obs_temps  = obs_f
        self.fcst_times = fct_t
        self.fcst_temps = fct_f
        self.fcst_high  = fhi
        self.metar_temp = metar_f
        self.metar_time = metar_ts

        cur    = obs_f[-1]
        high   = max(obs_f)
        self.day_high = high
        hi_idx = obs_f.index(high)
        hi_time = obs_t[hi_idx].strftime("%I:%M %p").lstrip("0") if obs_t else "--"
        self._last_obs_time = obs_t[-1] if obs_t else None

        now_et = datetime.now(EASTERN_TZ)
        month  = now_et.month

        self.prediction = predict_high(obs_t, obs_f, fct_t, fct_f, now_et)

        # ── Stat cards ────────────────────────────────────────────────────────
        self.lbl_cur.config(text=f"{cur:.1f}°F", fg=ACC)
        self.lbl_high.config(
            text=f"{high:.1f}°F",
            fg=GRN if (self.prev_high and high > self.prev_high) else RED)
        self.lbl_fcst.config(text=f"{fhi:.0f}°F" if fhi else "--", fg=BLUE)

        if self.prediction:
            p = self.prediction
            conf_col = {
                "High": GRN, "Medium": GOLD, "Low": RED, "Locked": GRN
            }.get(p.confidence, SUB)
            self.lbl_pred.config(
                text=f"{p.point:.1f}°F  ±{p.spread:.1f}°\n"
                     f"{p.confidence} confidence",
                fg=conf_col)
        else:
            self.lbl_pred.config(text="--", fg=SUB)

        # ── Signal strip ──────────────────────────────────────────────────────
        vel_val = velocity(obs_t, obs_f)
        if vel_val is not None:
            sign = "+" if vel_val >= 0 else ""
            self.lbl_vel.config(
                text=f"{sign}{vel_val:.1f}°F / hr",
                fg=RED if vel_val > 0.5 else BLUE if vel_val < -0.5 else SUB)
        else:
            self.lbl_vel.config(text="--", fg=SUB)

        recent = obs_f[-min(4, len(obs_f)):]
        delta  = recent[-1] - recent[0]
        if delta > 1.0:
            self.lbl_trend.config(text="Rising  ▲", fg=RED)
        elif delta < -1.0:
            self.lbl_trend.config(text="Falling  ▼", fg=BLUE)
        else:
            self.lbl_trend.config(text="Steady  ─", fg=SUB)

        pk_open, pk_msg = peak_status(now_et)
        self.lbl_peak.config(text=pk_msg, fg=GRN if pk_open else SUB)
        self.lbl_hi_t.config(text=hi_time, fg=WHT)

        if metar_f is not None:
            diff = abs(metar_f - cur)
            match_str = "✓ matches" if diff < 2 else f"Δ {diff:.1f}°"
            self.lbl_metar.config(
                text=f"{metar_f:.1f}°F  {match_str}",
                fg=GRN if diff < 2 else GOLD)
        else:
            self.lbl_metar.config(text="--", fg=SUB)

        self._update_bets()
        self._plot()

        ts = now_et.strftime("%I:%M:%S %p ET").lstrip("0")
        self._set_status(f"Updated {ts}  ·  {len(obs_f)} obs today", GRN)

        if self.prev_high and high > self.prev_high:
            self._flash(self.lbl_high)

    # ── Plot ──────────────────────────────────────────────────────────────────

    def _plot(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor(CARD)

        now_et = datetime.now(EASTERN_TZ)
        today  = now_et.date()

        # ── Y-axis: zoom to predicted range + threshold context ───────────────
        candidates = []
        if self.obs_temps:
            candidates += [min(self.obs_temps), max(self.obs_temps)]
        if self.prediction:
            candidates += [self.prediction.low - 1, self.prediction.high + 1]
        if self.fcst_high:
            candidates.append(self.fcst_high)
        # Include bet thresholds within ±10°F of current range
        if self.day_high and self.thresholds:
            for t in self.thresholds:
                if abs(t - self.day_high) <= 12:
                    candidates += [t - 1, t + 1]
        if candidates:
            y_min = min(candidates) - 3
            y_max = max(candidates) + 4
        else:
            y_min, y_max = 30, 80
        ax.set_ylim(y_min, y_max)

        # ── Peak window shading ───────────────────────────────────────────────
        pk0 = datetime(today.year, today.month, today.day,
                       PEAK_WINDOW[0], 0, tzinfo=EASTERN_TZ)
        pk1 = datetime(today.year, today.month, today.day,
                       PEAK_WINDOW[1], 0, tzinfo=EASTERN_TZ)
        ax.axvspan(pk0, pk1, alpha=0.05, color=GOLD)

        # ── Bet threshold lines ───────────────────────────────────────────────
        for thresh in self.thresholds:
            if y_min <= thresh <= y_max:
                cleared = self.day_high is not None and self.day_high >= thresh
                tc = GRN if cleared else GOLD
                ax.axhline(y=thresh, color=tc, linewidth=1.3,
                           linestyle="--", alpha=0.7, zorder=1)
                ax.annotate(f"> {thresh:.0f}°",
                            xy=(1.0, thresh), xycoords=("axes fraction", "data"),
                            color=tc, fontsize=8, fontweight="bold",
                            va="center", ha="right",
                            xytext=(-4, 0), textcoords="offset points")

        # ── NWS forecast ─────────────────────────────────────────────────────
        if self.fcst_times and self.fcst_temps:
            ax.plot(self.fcst_times, self.fcst_temps,
                    color=BLUE, linewidth=1.5, linestyle="--",
                    alpha=0.7, label="NWS Forecast", zorder=2)

        # ── Prediction band ───────────────────────────────────────────────────
        if self.prediction:
            p = self.prediction
            ax.axhspan(p.low, p.high, alpha=0.08, color=PURP, zorder=1)
            ax.axhline(y=p.point, color=PURP, linewidth=1.0,
                       linestyle=":", alpha=0.8, zorder=2)
            if self.obs_times:
                ax.annotate(f" Pred  {p.point:.1f}° ±{p.spread:.1f}",
                            xy=(self.obs_times[-1], p.point),
                            color=PURP, fontsize=8, va="bottom",
                            fontweight="bold")

        # ── Observed temperatures ─────────────────────────────────────────────
        if self.obs_times and self.obs_temps:
            ax.plot(self.obs_times, self.obs_temps,
                    color=ACC, linewidth=2.5,
                    marker="o", markersize=4, zorder=4, label="Observed (KBOS)")
            ax.fill_between(self.obs_times, self.obs_temps, y_min,
                            alpha=0.10, color=ACC, zorder=1)

            # Day high dashed line
            hi_idx = self.obs_temps.index(self.day_high)
            ax.axhline(y=self.day_high, color=RED, linewidth=1.0,
                       linestyle="--", alpha=0.5, zorder=3)
            ax.annotate(f"  High  {self.day_high:.1f}°F",
                        xy=(self.obs_times[hi_idx], self.day_high),
                        color=RED, fontsize=9, fontweight="bold", va="bottom")

            # Forecast high reference line
            if self.fcst_high and y_min <= self.fcst_high <= y_max:
                ax.axhline(y=self.fcst_high, color=BLUE, linewidth=0.8,
                           linestyle=":", alpha=0.4)

            # Current value label
            ax.annotate(f"  {self.obs_temps[-1]:.1f}°F  ← now",
                        xy=(self.obs_times[-1], self.obs_temps[-1]),
                        color=ACC, fontsize=9, fontweight="bold", va="center")

        # METAR point
        if self.metar_temp is not None and y_min <= self.metar_temp <= y_max:
            ax.scatter([now_et], [self.metar_temp],
                       color=GOLD, s=55, zorder=5, label="METAR / WU", marker="D")

        # Now line
        ax.axvline(x=now_et, color=WHT, linewidth=0.6, linestyle=":", alpha=0.3)

        # ── Axes styling ──────────────────────────────────────────────────────
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%I %p", tz=EASTERN_TZ))
        ax.xaxis.set_major_locator(mdates.HourLocator(tz=EASTERN_TZ))
        plt.setp(ax.xaxis.get_majorticklabels(), color=SUB, fontsize=8)
        plt.setp(ax.yaxis.get_majorticklabels(), color=SUB, fontsize=9)
        ax.set_ylabel("°F", color=SUB, fontsize=9)

        date_str = now_et.strftime("%A  %B %-d,  %Y")
        ax.set_title(f"KBOS  ·  {date_str}",
                     color=WHT, fontsize=11, pad=6, loc="left")

        ax.legend(loc="upper right", fontsize=8,
                  facecolor=CARD, edgecolor=CARD,
                  labelcolor=WHT, framealpha=0.9)

        for s in ["top", "right"]:
            ax.spines[s].set_visible(False)
        for s in ["bottom", "left"]:
            ax.spines[s].set_color(CARD)
        ax.tick_params(colors=SUB)
        ax.grid(True, alpha=0.08, color=SUB, linestyle="--")

        self.fig.tight_layout(pad=1.0)
        self.canvas.draw()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, color: str = SUB):
        self.lbl_status.config(text=msg, fg=color)

    def _flash(self, widget):
        bg = widget.cget("bg")
        widget.config(bg=GRN, fg=BG)
        self.root.after(600,  lambda: widget.config(bg=bg, fg=GRN))
        self.root.after(1200, lambda: widget.config(bg=bg, fg=RED))

    def _tick(self):
        now_et = datetime.now(EASTERN_TZ)

        # Live clock
        self.lbl_clock.config(text=now_et.strftime("%I:%M:%S %p ET").lstrip("0"))

        # Countdown
        if self._secs_left > 0:
            self._secs_left -= 1
            m, s = divmod(self._secs_left, 60)
            # Also show age of last observation
            if self._last_obs_time:
                age_s = int((now_et - self._last_obs_time).total_seconds())
                age_m = age_s // 60
                age_txt = f"{age_m}m ago" if age_m > 0 else f"{age_s}s ago"
                self.lbl_countdown.config(
                    text=f"last obs {age_txt}  ·  refresh in {m}:{s:02d}",
                    fg=GOLD if age_m > 25 else SUB)
            else:
                self.lbl_countdown.config(text=f"refresh in {m}:{s:02d}", fg=SUB)
        else:
            self.lbl_countdown.config(text="", fg=SUB)

        self.root.after(1000, self._tick)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        root = tk.Tk()
        root.geometry("1100x780")
        BostonTempTracker(root)
        root.mainloop()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("\n── Crash report ─────────────────────────────────", file=sys.stderr)
        _tb.print_exc()
        print("─────────────────────────────────────────────────", file=sys.stderr)
        print("Press Enter to exit…", file=sys.stderr, end="", flush=True)
        try:
            input()
        except Exception:
            pass
        sys.exit(1)
