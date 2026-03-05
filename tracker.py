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

from dataclasses import dataclass, field
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

REFRESH_MS      = 2 * 60 * 1000    # 2 minutes — catches every METAR update
FCST_REFRESH_M  = 30               # re-fetch NWS forecast every 30 min
EASTERN_TZ      = zoneinfo.ZoneInfo("America/New_York")

# Typical Boston daily high hour by season (used for trend projection)
# Based on NOAA climate records for KBOS
PEAK_HOUR = {1:13, 2:13, 3:14, 4:14, 5:14, 6:15, 7:15, 8:15, 9:14, 10:14, 11:13, 12:13}
PEAK_WINDOW = (11, 18)   # 11 AM – 6 PM: when high almost always occurs

# Boston NOAA Climate Normals 1991–2020 (avg daily max °F)
MONTHLY_AVG_HIGH = {1:36.4,2:38.9,3:46.6,4:56.5,5:66.7,6:76.1,
                    7:81.8,8:79.6,9:72.4,10:62.0,11:52.6,12:40.8}

# Default Robinhood-style brackets (lo, hi, label)  None = open-ended
DEFAULT_BRACKETS = [
    (None, 30,  "< 30"),
    (30,   40,  "30–39"),
    (40,   50,  "40–49"),
    (50,   55,  "50–54"),
    (55,   60,  "55–59"),
    (60,   65,  "60–64"),
    (65,   70,  "65–69"),
    (70,   75,  "70–74"),
    (75,   80,  "75–79"),
    (80,   85,  "80–84"),
    (85,   90,  "85–89"),
    (90,  None, "≥ 90"),
]

# ── Colors ───────────────────────────────────────────────────────────────────
BG    = "#0d1117"
PANEL = "#161b22"
CARD  = "#21262d"
ACC   = "#f0883e"   # orange — observed data
RED   = "#ff7b72"
GRN   = "#3fb950"
BLUE  = "#79c0ff"   # blue — NWS forecast
PURP  = "#bc8cff"   # purple — prediction
GOLD  = "#d29922"
SUB   = "#8b949e"
WHT   = "#e6edf3"


# ── Data helpers ─────────────────────────────────────────────────────────────
def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def bracket_for(temp_f: float, brackets: list) -> tuple[int, str]:
    """Return (index, label) of the bracket containing temp_f."""
    for i, (lo, hi, label) in enumerate(brackets):
        if (lo is None or temp_f >= lo) and (hi is None or temp_f < hi):
            return i, label
    return -1, "?"


def peak_status(now_et: datetime) -> tuple[bool, str]:
    h = now_et.hour + now_et.minute / 60
    if h < PEAK_WINDOW[0]:
        return False, f"Opens in {PEAK_WINDOW[0]-h:.1f}h"
    if h < PEAK_WINDOW[1]:
        return True, f"OPEN  {PEAK_WINDOW[1]-h:.1f}h left"
    return False, "Closed for today"


def velocity(times: list, temps: list, window_hr: float = 1.0) -> Optional[float]:
    """°F per hour over the last `window_hr` hours. None if insufficient data."""
    if len(times) < 2:
        return None
    cutoff = times[-1] - timedelta(hours=window_hr)
    pairs  = [(t, v) for t, v in zip(times, temps) if t >= cutoff]
    if len(pairs) < 2:
        return None
    dt_hr = (pairs[-1][0] - pairs[0][0]).total_seconds() / 3600
    if dt_hr < 0.05:
        return None
    return (pairs[-1][1] - pairs[0][1]) / dt_hr


# ── Prediction engine ─────────────────────────────────────────────────────────
@dataclass
class Prediction:
    low:        float           # lower bound
    high:       float           # upper bound
    point:      float           # best-guess
    confidence: str             # "High" / "Medium" / "Low"
    method:     str             # description of how it was made
    brackets:   list[str] = field(default_factory=list)  # likely bracket(s)


def predict_high(obs_times: list, obs_temps: list,
                 fcst_times: list, fcst_temps: list,
                 brackets: list,
                 now_et: datetime) -> Optional[Prediction]:
    """
    Blend three signals:
      1. NWS hourly forecast max remaining today
      2. Trend extrapolation from current rate of change
      3. Time-of-day confidence weighting

    Returns None if there is not enough data.
    """
    if not obs_temps:
        return None

    cur_temp  = obs_temps[-1]
    cur_high  = max(obs_temps)
    month     = now_et.month
    hour_frac = now_et.hour + now_et.minute / 60
    peak_hr   = PEAK_HOUR[month]

    # ── Signal 1: NWS forecast max for rest of today ──────────────────────────
    today = now_et.date()
    fcst_today = [t for t, v in zip(fcst_times, fcst_temps)
                  if t.date() == today]
    fcst_vals  = [v for t, v in zip(fcst_times, fcst_temps)
                  if t.date() == today]
    fcst_high  = max(fcst_vals) if fcst_vals else None

    # ── Signal 2: trend extrapolation ────────────────────────────────────────
    vel = velocity(obs_times, obs_temps, window_hr=1.0)
    if vel is None:
        vel = velocity(obs_times, obs_temps, window_hr=2.0) or 0.0

    hours_to_peak = max(0.0, peak_hr - hour_frac)
    # If we're past the peak hour or temp is falling, project 0 more rise
    if vel is not None and vel < 0 and hour_frac > peak_hr:
        hours_to_peak = 0.0

    trend_projected = cur_temp + vel * hours_to_peak
    trend_high      = max(cur_high, trend_projected)

    # ── Blending weights based on time of day ────────────────────────────────
    #  Early (before 10 AM): trust forecast more, little observed data
    #  Mid-day (10 AM–peak): equal weight
    #  Post-peak: trust observed trajectory, forecast less useful
    #  After 6 PM: observed high IS the final high with high confidence
    if hour_frac < 10:
        w_fcst, w_trend = 0.75, 0.25
    elif hour_frac < peak_hr:
        w_fcst, w_trend = 0.55, 0.45
    elif hour_frac < PEAK_WINDOW[1]:
        w_fcst, w_trend = 0.35, 0.65
    else:
        # Past peak window — observed high is almost certainly final
        point      = cur_high
        spread     = 1.0
        conf       = "High"
        _, bl      = bracket_for(point, brackets)
        return Prediction(
            low=point - spread, high=point + spread,
            point=point, confidence=conf,
            method="Peak window closed — observed high is final",
            brackets=[bl],
        )

    if fcst_high is not None:
        point = w_fcst * fcst_high + w_trend * trend_high
    else:
        point = trend_high

    point = max(point, cur_high)  # can't go below current observed high

    # ── Confidence & spread ───────────────────────────────────────────────────
    if fcst_high is not None:
        disagreement = abs(fcst_high - trend_high)
        if disagreement <= 2:
            conf, spread = "High",   1.5
        elif disagreement <= 5:
            conf, spread = "Medium", 3.0
        else:
            conf, spread = "Low",    5.0
    else:
        conf, spread = "Low", 4.0

    lo_b  = point - spread
    hi_b  = point + spread

    # Which brackets does the prediction range touch?
    touched = []
    for lo_t, hi_t, lbl in brackets:
        lo_t = lo_t if lo_t is not None else -999
        hi_t = hi_t if hi_t is not None else  999
        if lo_b < hi_t and hi_b >= lo_t:
            touched.append(lbl)

    methods = []
    if fcst_high is not None:
        methods.append(f"NWS fcst {fcst_high:.0f}°F (×{w_fcst:.0f})")
    methods.append(f"trend proj {trend_high:.1f}°F (×{w_trend:.0f}), vel {vel:+.1f}°F/hr")
    method_str = "  |  ".join(methods)

    return Prediction(
        low=lo_b, high=hi_b,
        point=point, confidence=conf,
        method=method_str,
        brackets=touched,
    )


# ── METAR parser ─────────────────────────────────────────────────────────────
def parse_metar_temp(raw: str) -> tuple[Optional[float], Optional[str]]:
    """
    Extract temperature from a KBOS METAR string.
    Returns (temp_f, obs_time_str) or (None, None).

    Tries the T-group remark first (e.g. T03110172) for 0.1°C precision,
    then falls back to the standard SS/DD field (whole °C).
    """
    lines = raw.strip().splitlines()
    metar_line = lines[-1] if len(lines) >= 2 else raw.strip()

    # Parse observation time from METAR header line (KBOS YYYYMMDD_HHMM)
    obs_time_str = None
    if len(lines) >= 2:
        try:
            dt = datetime.strptime(lines[0].strip(), "%Y/%m/%d %H:%M")
            dt_et = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(EASTERN_TZ)
            obs_time_str = dt_et.strftime("%I:%M %p ET").lstrip("0")
        except Exception:
            pass

    # Try T-group remark: T0SSS0DDD or T0SSS1DDD
    t_match = re.search(r"\bT([01])(\d{3})([01]\d{3})\b", metar_line)
    if t_match:
        sign = -1 if t_match.group(1) == "1" else 1
        temp_c = sign * int(t_match.group(2)) / 10.0
        return c_to_f(temp_c), obs_time_str

    # Fallback: standard SS/DD group (e.g.  31/17  or  M05/M10)
    td_match = re.search(r"\b(M?\d{2})/(M?\d{2})\b", metar_line)
    if td_match:
        raw_t = td_match.group(1)
        sign  = -1 if raw_t.startswith("M") else 1
        temp_c = sign * int(raw_t.replace("M", ""))
        return c_to_f(temp_c), obs_time_str

    return None, obs_time_str


# ── Fetchers ──────────────────────────────────────────────────────────────────
def fetch_observations() -> tuple[list, list]:
    """NWS KBOS ASOS hourly observations for today (Eastern time)."""
    resp = requests.get(NWS_OBS_URL, headers=HEADERS,
                        params={"limit": 150}, timeout=20)
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
    """NWS hourly forecast for Boston. Returns (times, temps_f, today_fcst_high)."""
    try:
        r1 = requests.get(NWS_POINTS, headers=HEADERS, timeout=15)
        r1.raise_for_status()
        fcst_url = r1.json()["properties"]["forecastHourly"]
        r2 = requests.get(fcst_url, headers=HEADERS, timeout=15)
        r2.raise_for_status()
        today = datetime.now(EASTERN_TZ).date()
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
        hi = max(temps) if temps else None
        return times, temps, hi
    except Exception:
        return [], [], None


def fetch_metar() -> tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Fetch raw KBOS METAR. Returns (temp_f, obs_time_str, raw_metar_line).
    This is the exact same source Weather Underground uses for current conditions.
    """
    try:
        resp = requests.get(METAR_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        raw    = resp.text
        lines  = raw.strip().splitlines()
        metar  = lines[-1] if len(lines) >= 2 else raw.strip()
        temp_f, obs_time = parse_metar_temp(raw)
        return temp_f, obs_time, metar
    except Exception:
        return None, None, None


# ── Main application ──────────────────────────────────────────────────────────
class BostonTempTracker:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Boston Temp Tracker — Trading Edition  •  KBOS")
        self.root.configure(bg=BG)
        self.root.minsize(1050, 740)

        # State
        self.obs_times:  list = []
        self.obs_temps:  list = []
        self.fcst_times: list = []
        self.fcst_temps: list = []
        self.day_high:   Optional[float] = None
        self.prev_high:  Optional[float] = None
        self.fcst_high:  Optional[float] = None
        self.metar_temp: Optional[float] = None
        self.prediction: Optional[Prediction] = None
        self.brackets    = list(DEFAULT_BRACKETS)
        self._refresh_job = None
        self._secs_left   = 0
        self._last_fcst_fetch: Optional[datetime] = None

        self._build_ui()
        self._launch_refresh(fetch_fcst=True, delay_ms=200)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill="x", padx=14, pady=(10, 2))

        tk.Label(hdr, text="Boston Temp Tracker",
                 font=("Helvetica", 18, "bold"), fg=WHT, bg=BG).pack(side="left")
        tk.Label(hdr, text=" — Trading Edition",
                 font=("Helvetica", 14), fg=ACC, bg=BG).pack(side="left")
        tk.Label(hdr, text="   KBOS • Data ≡ Weather Underground",
                 font=("Helvetica", 10), fg=SUB, bg=BG).pack(side="left")

        btn_frame = tk.Frame(hdr, bg=BG)
        btn_frame.pack(side="right")

        tk.Button(btn_frame, text="Open WU ↗",
                  command=lambda: webbrowser.open(WU_URL),
                  bg=CARD, fg=BLUE, font=("Helvetica", 10), relief="flat",
                  padx=8, pady=3, cursor="hand2").pack(side="right", padx=3)

        tk.Button(btn_frame, text="⟳ Refresh",
                  command=self._manual_refresh,
                  bg=CARD, fg=WHT, font=("Helvetica", 10), relief="flat",
                  padx=8, pady=3, cursor="hand2").pack(side="right", padx=3)

        self.status_lbl = tk.Label(btn_frame, text="Loading…",
                                   font=("Helvetica", 10), fg=SUB, bg=BG)
        self.status_lbl.pack(side="right", padx=10)

        # ── Top stats row ─────────────────────────────────────────────────────
        row1 = tk.Frame(self.root, bg=BG)
        row1.pack(fill="x", padx=14, pady=(6, 3))
        for i in range(6):
            row1.columnconfigure(i, weight=1, uniform="top")

        self.lbl_cur      = self._card(row1, "Current Temp",       "--°F",   ACC,  0)
        self.lbl_high     = self._card(row1, "Today's High  (=WU)","--°F",   RED,  1)
        self.lbl_fcst     = self._card(row1, "NWS Forecast High",  "--°F",   BLUE, 2)
        self.lbl_pred     = self._card(row1, "Predicted High",     "--°F",   PURP, 3)
        self.lbl_metar    = self._card(row1, "METAR / WU Current", "--°F",   GOLD, 4)
        self.lbl_avg      = self._card(row1, "Avg High (climate)", "--°F",   SUB,  5)

        # ── Trading signals row ───────────────────────────────────────────────
        row2 = tk.Frame(self.root, bg=PANEL)
        row2.pack(fill="x", padx=14, pady=3)
        for i in range(6):
            row2.columnconfigure(i, weight=1, uniform="sig")

        self.lbl_vel      = self._sig(row2, "Velocity",       "--",     0)
        self.lbl_trend    = self._sig(row2, "Trend",          "─",      1)
        self.lbl_peak     = self._sig(row2, "Peak Window",    "--",     2)
        self.lbl_high_t   = self._sig(row2, "High Set At",    "--",     3)
        self.lbl_conf     = self._sig(row2, "Pred Confidence","--",     4)
        self.lbl_brkt_cur = self._sig(row2, "High in Bracket","--",     5)

        # ── Graph ─────────────────────────────────────────────────────────────
        self.fig = plt.Figure(figsize=(11, 3.8), facecolor=PANEL)
        self.ax  = self.fig.add_subplot(111)
        self.ax.set_facecolor(CARD)

        gf = tk.Frame(self.root, bg=BG)
        gf.pack(fill="both", expand=True, padx=14, pady=4)
        self.canvas = FigureCanvasTkAgg(self.fig, master=gf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # ── Prediction detail bar ─────────────────────────────────────────────
        pred_bar = tk.Frame(self.root, bg=PANEL)
        pred_bar.pack(fill="x", padx=14, pady=(0, 3))
        tk.Label(pred_bar, text="Pred method: ", font=("Helvetica", 9),
                 fg=SUB, bg=PANEL).pack(side="left", padx=(8, 0))
        self.lbl_pred_method = tk.Label(pred_bar, text="--",
                                        font=("Helvetica", 9), fg=PURP, bg=PANEL)
        self.lbl_pred_method.pack(side="left")

        # ── Brackets panel ────────────────────────────────────────────────────
        self.bkt_outer = tk.Frame(self.root, bg=BG)
        self.bkt_outer.pack(fill="x", padx=14, pady=(2, 0))
        self._populate_brackets()

        # ── Bottom bar ────────────────────────────────────────────────────────
        bot = tk.Frame(self.root, bg=BG)
        bot.pack(fill="x", padx=14, pady=(2, 8))

        tk.Label(bot, text="NWS KBOS obs = WU Today's High  •  METAR = WU Current Conditions  •  Refreshes every 2 min",
                 font=("Helvetica", 9), fg=SUB, bg=BG).pack(side="left")

        self.lbl_countdown = tk.Label(bot, text="", font=("Helvetica", 10),
                                      fg=SUB, bg=BG)
        self.lbl_countdown.pack(side="right")

        self._tick()

    def _card(self, parent, label, val, color, col):
        f = tk.Frame(parent, bg=CARD, padx=11, pady=8)
        f.grid(row=0, column=col, padx=3, pady=2, sticky="nsew")
        tk.Label(f, text=label, font=("Helvetica", 9), fg=SUB, bg=CARD).pack(anchor="w")
        lbl = tk.Label(f, text=val, font=("Helvetica", 24, "bold"), fg=color, bg=CARD)
        lbl.pack(anchor="w")
        return lbl

    def _sig(self, parent, label, val, col):
        f = tk.Frame(parent, bg=PANEL, padx=10, pady=5)
        f.grid(row=0, column=col, padx=2, pady=2, sticky="nsew")
        tk.Label(f, text=label, font=("Helvetica", 9), fg=SUB, bg=PANEL).pack(anchor="w")
        lbl = tk.Label(f, text=val, font=("Helvetica", 12, "bold"), fg=WHT, bg=PANEL)
        lbl.pack(anchor="w")
        return lbl

    def _populate_brackets(self):
        for w in self.bkt_outer.winfo_children():
            w.destroy()
        self.bkt_btns = {}

        tk.Label(self.bkt_outer, text="BRACKETS ",
                 font=("Helvetica", 10, "bold"), fg=SUB, bg=BG).pack(side="left")

        inner = tk.Frame(self.bkt_outer, bg=BG)
        inner.pack(side="left", fill="x", expand=True)

        for _, _, label in self.brackets:
            b = tk.Label(inner, text=label, font=("Helvetica", 10),
                         fg=SUB, bg=CARD, padx=7, pady=2)
            b.pack(side="left", padx=2)
            self.bkt_btns[label] = b

        tk.Button(self.bkt_outer, text="Edit Brackets",
                  font=("Helvetica", 9), fg=SUB, bg=BG, relief="flat",
                  command=self._edit_brackets, cursor="hand2").pack(side="right")

    def _refresh_brackets_colors(self):
        if self.day_high is None:
            return
        cur_idx,  cur_lbl  = bracket_for(self.day_high, self.brackets)
        pred_idxs = []
        if self.prediction:
            pred_idxs = [bracket_for(self.prediction.point, self.brackets)[0]]

        for i, (_, _, label) in enumerate(self.brackets):
            btn = self.bkt_btns.get(label)
            if not btn:
                continue
            if i == cur_idx:
                btn.config(bg=RED, fg=BG, font=("Helvetica", 10, "bold"))
            elif i in pred_idxs:
                btn.config(bg=PURP, fg=BG, font=("Helvetica", 10, "bold"))
            else:
                btn.config(bg=CARD, fg=SUB, font=("Helvetica", 10))

        self.lbl_brkt_cur.config(text=cur_lbl, fg=RED)

    def _edit_brackets(self):
        vals = [str(int(b[1])) for b in self.brackets if b[1] is not None]
        result = simpledialog.askstring(
            "Edit Brackets",
            "Enter bracket boundary temperatures (°F), comma-separated.\n"
            "Example:  30, 40, 50, 55, 60, 65, 70, 75, 80, 85, 90\n"
            "(App will build ranges: <30, 30–39, 40–49 … ≥90)",
            initialvalue=", ".join(vals),
            parent=self.root,
        )
        if not result:
            return
        try:
            nums = sorted(set(int(x.strip()) for x in result.split(",")))
            new  = [(None, nums[0], f"< {nums[0]}")]
            for a, b in zip(nums, nums[1:]):
                new.append((a, b, f"{a}–{b-1}"))
            new.append((nums[-1], None, f"≥ {nums[-1]}"))
            self.brackets = new
            self._populate_brackets()
            self._refresh_brackets_colors()
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.root)

    # ── Refresh logic ─────────────────────────────────────────────────────────

    def _launch_refresh(self, fetch_fcst: bool = False, delay_ms: int = 0):
        def work():
            try:
                obs_t, obs_f   = fetch_observations()
                metar_f, metar_ts, metar_raw = fetch_metar()

                if fetch_fcst:
                    fct_t, fct_f, fhi = fetch_forecast()
                    self._last_fcst_fetch = datetime.now(EASTERN_TZ)
                else:
                    fct_t, fct_f, fhi = self.fcst_times, self.fcst_temps, self.fcst_high

                self.root.after(0, lambda: self._apply(
                    obs_t, obs_f, fct_t, fct_f, fhi, metar_f, metar_ts, metar_raw))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._set_status(f"Error: {msg}", RED))
            finally:
                self._refresh_job = self.root.after(REFRESH_MS, self._auto_refresh)
                self._secs_left   = REFRESH_MS // 1000

        t = threading.Thread(target=work, daemon=True)
        if delay_ms:
            self.root.after(delay_ms, t.start)
        else:
            t.start()

    def _auto_refresh(self):
        # Re-fetch forecast every FCST_REFRESH_M minutes
        need_fcst = False
        if self._last_fcst_fetch is None:
            need_fcst = True
        else:
            elapsed = (datetime.now(EASTERN_TZ) - self._last_fcst_fetch).total_seconds() / 60
            need_fcst = elapsed >= FCST_REFRESH_M
        self._launch_refresh(fetch_fcst=need_fcst)

    def _manual_refresh(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._set_status("Refreshing…", SUB)
        self._launch_refresh(fetch_fcst=True)

    # ── Apply data to UI ──────────────────────────────────────────────────────

    def _apply(self, obs_t, obs_f, fct_t, fct_f, fhi,
               metar_f, metar_ts, metar_raw):
        if not obs_f:
            self._set_status("No observations yet for today", SUB)
            return

        self.prev_high   = self.day_high
        self.obs_times   = obs_t
        self.obs_temps   = obs_f
        self.fcst_times  = fct_t
        self.fcst_temps  = fct_f
        self.fcst_high   = fhi
        self.metar_temp  = metar_f

        cur      = obs_f[-1]
        high     = max(obs_f)
        self.day_high = high
        hi_idx   = obs_f.index(high)
        hi_time  = obs_t[hi_idx].strftime("%I:%M %p").lstrip("0") if obs_t else "--"

        month    = datetime.now(EASTERN_TZ).month
        now_et   = datetime.now(EASTERN_TZ)
        avg      = MONTHLY_AVG_HIGH[month]
        vel_val  = velocity(obs_t, obs_f)

        # Prediction
        self.prediction = predict_high(obs_t, obs_f, fct_t, fct_f, self.brackets, now_et)

        # Trend
        recent  = obs_f[-min(4, len(obs_f)):]
        delta   = recent[-1] - recent[0]
        if delta > 1.0:
            trend_txt, trend_col = "Rising ▲", RED
        elif delta < -1.0:
            trend_txt, trend_col = "Falling ▼", BLUE
        else:
            trend_txt, trend_col = "Steady ─", SUB

        # Peak window
        peak_open, peak_msg = peak_status(now_et)

        # Update stat cards
        self.lbl_cur.config(text=f"{cur:.1f}°F", fg=ACC)
        self.lbl_high.config(text=f"{high:.1f}°F",
                             fg=GRN if (self.prev_high and high > self.prev_high) else RED)
        self.lbl_fcst.config(text=f"{fhi:.0f}°F" if fhi else "--°F", fg=BLUE)
        self.lbl_avg.config(text=f"{avg:.1f}°F", fg=SUB)

        if metar_f is not None:
            diff     = abs(metar_f - cur)
            match    = "✓ matches" if diff < 2 else f"Δ{diff:.1f}°"
            mt_str   = f"{metar_f:.1f}°F"
            mt_color = GRN if diff < 2 else GOLD
            self.lbl_metar.config(
                text=f"{mt_str}  {match}\n{metar_ts or ''}", fg=mt_color)
        else:
            self.lbl_metar.config(text="--", fg=SUB)

        if self.prediction:
            p = self.prediction
            self.lbl_pred.config(
                text=f"{p.point:.1f}°F\n({p.low:.0f}–{p.high:.0f})", fg=PURP)
            conf_color = {
                "High": GRN, "Medium": GOLD, "Low": RED}.get(p.confidence, SUB)
            self.lbl_conf.config(text=p.confidence, fg=conf_color)
            self.lbl_pred_method.config(text=p.method[:120])
        else:
            self.lbl_pred.config(text="--", fg=SUB)

        # Signal row
        if vel_val is not None:
            sign = "+" if vel_val >= 0 else ""
            self.lbl_vel.config(text=f"{sign}{vel_val:.1f}°F/hr",
                                fg=RED if vel_val > 0.5 else BLUE if vel_val < -0.5 else SUB)
        else:
            self.lbl_vel.config(text="--")

        self.lbl_trend.config(text=trend_txt, fg=trend_col)
        self.lbl_peak.config(text=peak_msg, fg=GRN if peak_open else SUB)
        self.lbl_high_t.config(text=hi_time)

        self._refresh_brackets_colors()
        self._plot()

        ts = now_et.strftime("%I:%M:%S %p ET").lstrip("0")
        self._set_status(f"Updated {ts}  •  {len(obs_f)} obs  •  METAR ✓", GRN)

        # Flash if new high
        if self.prev_high and high > self.prev_high:
            self._flash(self.lbl_high)

    # ── Plot ──────────────────────────────────────────────────────────────────

    def _plot(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor(CARD)

        now_et = datetime.now(EASTERN_TZ)
        today  = now_et.date()

        # Peak window shading
        pk0 = datetime(today.year, today.month, today.day,
                       PEAK_WINDOW[0], 0, tzinfo=EASTERN_TZ)
        pk1 = datetime(today.year, today.month, today.day,
                       PEAK_WINDOW[1], 0, tzinfo=EASTERN_TZ)
        ax.axvspan(pk0, pk1, alpha=0.05, color=GOLD, label="_peak")

        # NWS hourly forecast (dashed blue)
        if self.fcst_times and self.fcst_temps:
            ax.plot(self.fcst_times, self.fcst_temps,
                    color=BLUE, linewidth=1.6, linestyle="--",
                    alpha=0.75, label="NWS Forecast", zorder=2)

        # Prediction band (shaded purple)
        if self.prediction and self.obs_times:
            last_obs_t = self.obs_times[-1]
            # Shade forward from last obs to end of day
            eod = datetime(today.year, today.month, today.day,
                           23, 59, tzinfo=EASTERN_TZ)
            ax.axhspan(self.prediction.low, self.prediction.high,
                       xmin=0, xmax=1,
                       alpha=0.07, color=PURP)
            ax.axhline(y=self.prediction.point, color=PURP, linewidth=1.2,
                       linestyle=":", alpha=0.7)
            ax.annotate(f" Pred {self.prediction.point:.1f}°",
                        xy=(last_obs_t, self.prediction.point),
                        color=PURP, fontsize=8, va="bottom", alpha=0.9)

        # Observed temp line
        if self.obs_times and self.obs_temps:
            ax.plot(self.obs_times, self.obs_temps,
                    color=ACC, linewidth=2.5,
                    marker="o", markersize=4, zorder=4, label="Observed (KBOS)")
            ax.fill_between(self.obs_times, self.obs_temps,
                            min(self.obs_temps) - 3,
                            alpha=0.12, color=ACC)

            # Day high dashed line + annotation
            if self.day_high:
                hi_idx = self.obs_temps.index(self.day_high)
                ax.axhline(y=self.day_high, color=RED, linewidth=1.1,
                           linestyle="--", alpha=0.5, zorder=3)
                ax.annotate(f"  High {self.day_high:.1f}°F",
                            xy=(self.obs_times[hi_idx], self.day_high),
                            color=RED, fontsize=9, fontweight="bold", va="bottom")

            # NWS forecast high dotted line
            if self.fcst_high:
                ax.axhline(y=self.fcst_high, color=BLUE, linewidth=0.9,
                           linestyle=":", alpha=0.45)
                ax.annotate(f" Fcst {self.fcst_high:.0f}°F",
                            xy=(pk0, self.fcst_high),
                            color=BLUE, fontsize=8, va="top", alpha=0.8)

            # Current temp label
            ax.annotate(f"  {self.obs_temps[-1]:.1f}°F  ← now",
                        xy=(self.obs_times[-1], self.obs_temps[-1]),
                        color=ACC, fontsize=9, fontweight="bold", va="center")

        # METAR point (gold dot at current time)
        if self.metar_temp is not None:
            ax.scatter([now_et], [self.metar_temp],
                       color=GOLD, s=60, zorder=5, label="METAR/WU",
                       marker="D")

        # Current-time vertical line
        ax.axvline(x=now_et, color=WHT, linewidth=0.7, linestyle=":", alpha=0.3)

        # Axes
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%I %p", tz=EASTERN_TZ))
        ax.xaxis.set_major_locator(mdates.HourLocator(tz=EASTERN_TZ))
        plt.setp(ax.xaxis.get_majorticklabels(), color=SUB, fontsize=8)
        plt.setp(ax.yaxis.get_majorticklabels(), color=SUB, fontsize=8)
        ax.set_ylabel("°F", color=SUB, fontsize=9)

        date_str = now_et.strftime("%A %B %-d, %Y")
        ax.set_title(f"KBOS  •  {date_str}  •  Orange shading = observed | Blue dashed = NWS forecast | Purple = predicted high",
                     color=WHT, fontsize=10, pad=6)

        for s in ["top", "right"]:
            ax.spines[s].set_visible(False)
        for s in ["bottom", "left"]:
            ax.spines[s].set_color(CARD)

        ax.tick_params(colors=SUB)
        ax.grid(True, alpha=0.1, color=SUB, linestyle="--")

        legend = ax.legend(loc="upper left", fontsize=8,
                           facecolor=CARD, edgecolor=CARD,
                           labelcolor=WHT, framealpha=0.85)

        self.fig.tight_layout(pad=1.2)
        self.canvas.draw()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, color: str = SUB):
        self.status_lbl.config(text=msg, fg=color)

    def _flash(self, widget):
        orig_bg = widget.cget("bg")
        widget.config(bg=GRN, fg=BG)
        self.root.after(700, lambda: widget.config(bg=orig_bg, fg=GRN))
        self.root.after(1400, lambda: widget.config(bg=orig_bg, fg=RED))

    def _tick(self):
        if self._secs_left > 0:
            self._secs_left -= 1
            m, s = divmod(self._secs_left, 60)
            self.lbl_countdown.config(text=f"Next refresh: {m}:{s:02d}")
        else:
            self.lbl_countdown.config(text="")
        self.root.after(1000, self._tick)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        root = tk.Tk()
        root.geometry("1150x780")
        BostonTempTracker(root)
        root.mainloop()
    except KeyboardInterrupt:
        pass
    except Exception:
        # Print the full traceback to the terminal so it's readable
        print("\n── Crash report ─────────────────────────────────", file=sys.stderr)
        _tb.print_exc()
        print("─────────────────────────────────────────────────", file=sys.stderr)
        print("Press Enter to exit…", file=sys.stderr, end="", flush=True)
        try:
            input()
        except Exception:
            pass
        sys.exit(1)
