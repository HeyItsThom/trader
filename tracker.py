#!/usr/bin/env python3
"""
Boston Temperature Tracker
===========================
Tracks real-time temperature data for Boston (KBOS - Logan Airport).
Uses NWS (National Weather Service) API — the same official station
data that Weather Underground displays for Boston.

Useful for monitoring daily high temperatures on Robinhood prediction markets.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import requests
from datetime import datetime, timezone, timedelta
import threading
import zoneinfo

# ── Config ──────────────────────────────────────────────────────────────────
STATION_ID   = "KBOS"
NWS_OBS_URL  = f"https://api.weather.gov/stations/{STATION_ID}/observations"
REFRESH_MS   = 5 * 60 * 1000   # 5 minutes
EASTERN_TZ   = zoneinfo.ZoneInfo("America/New_York")
HEADERS      = {"User-Agent": "BostonTempTracker/1.0 (personal weather monitoring)"}

# ── Color palette ────────────────────────────────────────────────────────────
BG_DARK   = "#0d1117"
BG_PANEL  = "#161b22"
BG_CARD   = "#21262d"
ACCENT    = "#f0883e"
RED       = "#ff7b72"
GREEN     = "#3fb950"
BLUE      = "#79c0ff"
SUBTLE    = "#8b949e"
WHITE     = "#e6edf3"
GOLD      = "#d29922"
PLOT_LINE = "#f0883e"


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def trend_arrow(temps: list[float]) -> tuple[str, str]:
    """Return (arrow symbol, color) based on last 3 readings."""
    if len(temps) < 2:
        return "─", SUBTLE
    recent = temps[-min(3, len(temps)):]
    delta = recent[-1] - recent[0]
    if delta > 0.5:
        return "▲", RED
    elif delta < -0.5:
        return "▼", BLUE
    return "─", SUBTLE


# ── Main application ─────────────────────────────────────────────────────────
class BostonTempTracker:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Boston Temp Tracker  •  KBOS Logan Airport")
        self.root.configure(bg=BG_DARK)
        self.root.minsize(900, 620)

        self.times: list[datetime] = []
        self.temps_f: list[float]  = []
        self.day_high: float | None = None
        self.current_temp: float | None = None
        self._refresh_job = None

        self._build_ui()
        self._schedule_refresh(delay_ms=100)   # immediate first load

    # ── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        top = tk.Frame(self.root, bg=BG_DARK)
        top.pack(fill="x", padx=14, pady=(12, 4))

        tk.Label(top, text="Boston Temperature Tracker",
                 font=("Helvetica", 17, "bold"), fg=WHITE, bg=BG_DARK).pack(side="left")

        tk.Label(top, text="KBOS • Logan Airport  |  Same data as Weather Underground",
                 font=("Helvetica", 10), fg=SUBTLE, bg=BG_DARK).pack(side="left", padx=12)

        self.status_lbl = tk.Label(top, text="Loading…",
                                   font=("Helvetica", 10), fg=SUBTLE, bg=BG_DARK)
        self.status_lbl.pack(side="right")

        # ── Stats cards ──────────────────────────────────────────────────────
        cards = tk.Frame(self.root, bg=BG_DARK)
        cards.pack(fill="x", padx=14, pady=6)

        self.current_lbl, self.current_unit = self._card(
            cards, "Current Temp", "--", "°F", ACCENT, col=0)
        self.high_lbl, self.high_unit = self._card(
            cards, "Today's High", "--", "°F", RED, col=1)
        self.trend_lbl, _ = self._card(
            cards, "Trend (last 3 obs)", "─", "", SUBTLE, col=2)
        self.wu_high_lbl, self.wu_high_unit = self._card(
            cards, "WU-Equivalent High", "--", "°F", GOLD, col=3)

        for c in range(4):
            cards.columnconfigure(c, weight=1, uniform="card")

        # ── WU note ──────────────────────────────────────────────────────────
        note = tk.Frame(self.root, bg=BG_DARK)
        note.pack(fill="x", padx=14)
        tk.Label(note,
                 text="ℹ  'WU-Equivalent High' = max °F recorded at KBOS today (same value Weather Underground reports).",
                 font=("Helvetica", 9), fg=SUBTLE, bg=BG_DARK).pack(side="left")

        # ── Graph ────────────────────────────────────────────────────────────
        self.fig = plt.Figure(figsize=(10, 4), facecolor=BG_PANEL)
        self.ax  = self.fig.add_subplot(111)
        self.ax.set_facecolor(BG_CARD)

        graph_frame = tk.Frame(self.root, bg=BG_DARK)
        graph_frame.pack(fill="both", expand=True, padx=14, pady=8)

        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # ── Bottom bar ───────────────────────────────────────────────────────
        bot = tk.Frame(self.root, bg=BG_DARK)
        bot.pack(fill="x", padx=14, pady=(0, 10))

        tk.Button(bot, text="⟳  Refresh Now",
                  command=self._manual_refresh,
                  bg=BG_CARD, fg=WHITE, font=("Helvetica", 11),
                  relief="flat", padx=14, pady=4,
                  activebackground=ACCENT, activeforeground=BG_DARK,
                  cursor="hand2").pack(side="left")

        self.next_refresh_lbl = tk.Label(bot, text="",
                                         font=("Helvetica", 10), fg=SUBTLE, bg=BG_DARK)
        self.next_refresh_lbl.pack(side="right")

        # Countdown timer
        self._seconds_to_next = 0
        self._update_countdown()

    def _card(self, parent, label: str, value: str, unit: str, color: str, col: int):
        frame = tk.Frame(parent, bg=BG_CARD, padx=14, pady=10)
        frame.grid(row=0, column=col, padx=5, pady=4, sticky="nsew")

        tk.Label(frame, text=label, font=("Helvetica", 10),
                 fg=SUBTLE, bg=BG_CARD).pack(anchor="w")

        row = tk.Frame(frame, bg=BG_CARD)
        row.pack(anchor="w")

        val_lbl = tk.Label(row, text=value, font=("Helvetica", 32, "bold"),
                           fg=color, bg=BG_CARD)
        val_lbl.pack(side="left")

        unit_lbl = tk.Label(row, text=unit, font=("Helvetica", 14),
                            fg=color, bg=BG_CARD)
        unit_lbl.pack(side="left", padx=(2, 0), pady=(8, 0))

        return val_lbl, unit_lbl

    # ── Data fetching ────────────────────────────────────────────────────────

    def _fetch(self):
        """Pull observations from NWS, filter to Eastern-time today, return (times, temps_f)."""
        resp = requests.get(NWS_OBS_URL, headers=HEADERS,
                            params={"limit": 150}, timeout=20)
        resp.raise_for_status()
        features = resp.json().get("features", [])

        now_eastern = datetime.now(EASTERN_TZ)
        today_eastern = now_eastern.date()

        times, temps = [], []
        for feat in reversed(features):          # oldest → newest
            props = feat.get("properties", {})
            raw_c = props.get("temperature", {}).get("value")
            ts    = props.get("timestamp")
            if raw_c is None or ts is None:
                continue

            dt_utc    = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            dt_eastern = dt_utc.astimezone(EASTERN_TZ)

            if dt_eastern.date() == today_eastern:
                times.append(dt_eastern)
                temps.append(c_to_f(raw_c))

        return times, temps

    def _do_refresh(self):
        """Run in background thread."""
        try:
            times, temps = self._fetch()
            self.root.after(0, lambda: self._apply_data(times, temps))
        except Exception as exc:
            self.root.after(0, lambda: self._show_error(exc))
        finally:
            # Schedule the next auto-refresh
            self._refresh_job = self.root.after(REFRESH_MS, self._auto_refresh)
            self._seconds_to_next = REFRESH_MS // 1000

    def _apply_data(self, times, temps):
        if not temps:
            self._show_error("No observations returned for today yet.")
            return

        self.times    = times
        self.temps_f  = temps
        self.current_temp = temps[-1]
        self.day_high     = max(temps)

        arrow, arrow_color = trend_arrow(temps)
        self.current_lbl.config(text=f"{self.current_temp:.1f}", fg=ACCENT)
        self.high_lbl.config(text=f"{self.day_high:.1f}", fg=RED)
        self.wu_high_lbl.config(text=f"{self.day_high:.1f}", fg=GOLD)
        self.trend_lbl.config(text=arrow, fg=arrow_color)

        now_str = datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p ET")
        self.status_lbl.config(
            text=f"Updated {now_str}  •  {len(temps)} observations today", fg=GREEN)

        self._plot()

    def _plot(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor(BG_CARD)

        times = self.times
        temps = self.temps_f

        # Main line + fill
        ax.plot(times, temps, color=PLOT_LINE, linewidth=2.5,
                marker="o", markersize=4, markerfacecolor=PLOT_LINE, zorder=3)
        ax.fill_between(times, temps, min(temps) - 2,
                        alpha=0.15, color=PLOT_LINE)

        # Annotate current value
        ax.annotate(f"  {temps[-1]:.1f}°F",
                    xy=(times[-1], temps[-1]),
                    color=ACCENT, fontsize=10, fontweight="bold",
                    va="center")

        # Annotate day high
        hi_idx = temps.index(self.day_high)
        ax.annotate(f"High {self.day_high:.1f}°F",
                    xy=(times[hi_idx], self.day_high),
                    xytext=(0, 14), textcoords="offset points",
                    ha="center", color=RED, fontsize=10, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=RED, lw=1.5))

        # Horizontal high line
        ax.axhline(y=self.day_high, color=RED, linewidth=1,
                   linestyle="--", alpha=0.4, zorder=2)

        # Axes formatting
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%-I:%M %p", tz=EASTERN_TZ))
        ax.xaxis.set_major_locator(mdates.HourLocator(tz=EASTERN_TZ))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=35,
                 ha="right", color=SUBTLE, fontsize=9)
        plt.setp(ax.yaxis.get_majorticklabels(), color=SUBTLE, fontsize=9)

        ax.set_xlabel("Time (Eastern)", color=SUBTLE, fontsize=10)
        ax.set_ylabel("Temperature (°F)", color=SUBTLE, fontsize=10)
        date_str = datetime.now(EASTERN_TZ).strftime("%A, %B %-d, %Y")
        ax.set_title(f"KBOS  •  {date_str}", color=WHITE, fontsize=12, pad=10)

        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        for spine in ["bottom", "left"]:
            ax.spines[spine].set_color(SUBTLE)

        ax.tick_params(colors=SUBTLE)
        ax.grid(True, alpha=0.15, color=SUBTLE, linestyle="--")

        self.fig.tight_layout(pad=1.5)
        self.canvas.draw()

    # ── Scheduling helpers ───────────────────────────────────────────────────

    def _schedule_refresh(self, delay_ms: int = REFRESH_MS):
        t = threading.Thread(target=self._do_refresh, daemon=True)
        self.root.after(delay_ms, t.start)

    def _auto_refresh(self):
        t = threading.Thread(target=self._do_refresh, daemon=True)
        t.start()

    def _manual_refresh(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        self.status_lbl.config(text="Refreshing…", fg=SUBTLE)
        t = threading.Thread(target=self._do_refresh, daemon=True)
        t.start()

    def _update_countdown(self):
        if self._seconds_to_next > 0:
            self._seconds_to_next -= 1
            m, s = divmod(self._seconds_to_next, 60)
            self.next_refresh_lbl.config(
                text=f"Next auto-refresh in {m}:{s:02d}", fg=SUBTLE)
        else:
            self.next_refresh_lbl.config(text="", fg=SUBTLE)
        self.root.after(1000, self._update_countdown)

    def _show_error(self, exc):
        msg = str(exc)[:80]
        self.status_lbl.config(text=f"Error: {msg}", fg=RED)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("1000x680")
    app = BostonTempTracker(root)
    root.mainloop()
