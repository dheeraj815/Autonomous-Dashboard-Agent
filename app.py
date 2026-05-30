
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import sqlite3
import time
import threading
import logging
import os
import random
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG  — must be FIRST Streamlit call
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="NEXUS · Autonomous Intelligence Dashboard",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
DB_PATH = "nexus_data.db"
LOG_PATH = "nexus_agent.log"
VERSION = "3.0.0"
REFRESH_SEC = 15   # auto-refresh interval
MAX_LOG_LINES = 300

# ─── Palette ──────────────────────────────────────────────────────────────────
C_BG = "#03060F"
C_PANEL = "#070D1C"
C_BORDER = "#0E1E3A"
C_ACCENT = "#00C8FF"
C_ACCENT2 = "#7B2FFF"
C_GREEN = "#00E5A0"
C_YELLOW = "#FFD23F"
C_RED = "#FF3E6C"
C_MUTED = "#4A5568"
C_TEXT = "#CDD6F4"
C_WHITE = "#EEF2FF"

SECTORS = ["Technology", "Finance", "Healthcare", "Energy", "Retail"]

KPIS = {
    "Revenue ($M)":       {"target": 12.0,  "unit": "$M",  "fmt": ".2f",  "good": "high", "icon": "◈"},
    "Active Users":       {"target": 85000, "unit": "",    "fmt": ",.0f", "good": "high", "icon": "◉"},
    "Conversion Rate":    {"target": 4.5,   "unit": "%",   "fmt": ".2f",  "good": "high", "icon": "◐"},
    "Avg Response (ms)":  {"target": 120,   "unit": "ms",  "fmt": ".0f",  "good": "low",  "icon": "◑"},
    "Error Rate":         {"target": 0.5,   "unit": "%",   "fmt": ".2f",  "good": "low",  "icon": "◒"},
    "CPU Usage":          {"target": 60,    "unit": "%",   "fmt": ".1f",  "good": "low",  "icon": "◓"},
}

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="'DM Mono', 'Fira Code', monospace",
              color=C_TEXT, size=11),
    margin=dict(l=8, r=8, t=40, b=8),
    xaxis=dict(showgrid=True, gridcolor="rgba(0,200,255,0.05)", zeroline=False,
               linecolor=C_BORDER, tickfont=dict(size=10)),
    yaxis=dict(showgrid=True, gridcolor="rgba(0,200,255,0.05)", zeroline=False,
               linecolor=C_BORDER, tickfont=dict(size=10)),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(
        size=10), bordercolor=C_BORDER, borderwidth=1),
    hoverlabel=dict(bgcolor=C_PANEL, bordercolor=C_ACCENT,
                    font=dict(color=C_TEXT)),
)

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("NEXUS")

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE LAYER
# ══════════════════════════════════════════════════════════════════════════════
_db_lock = threading.Lock()


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)


def init_db():
    with _db_lock:
        conn = get_conn()
        c = conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS metrics (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                metric  TEXT NOT NULL,
                value   REAL NOT NULL,
                sector  TEXT DEFAULT 'Global'
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
            CREATE INDEX IF NOT EXISTS idx_metrics_metric ON metrics(metric);

            CREATE TABLE IF NOT EXISTS alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           TEXT NOT NULL,
                severity     TEXT NOT NULL,
                metric       TEXT NOT NULL,
                message      TEXT NOT NULL,
                acknowledged INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                run_type      TEXT NOT NULL,
                records       INTEGER DEFAULT 0,
                alerts_fired  INTEGER DEFAULT 0,
                anomalies     INTEGER DEFAULT 0,
                duration_ms   REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS anomalies (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                metric   TEXT NOT NULL,
                value    REAL NOT NULL,
                zscore   REAL NOT NULL,
                severity TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_health (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                component TEXT NOT NULL,
                status    TEXT NOT NULL,
                latency   REAL DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()
    logger.info("Database initialized v3")


def seed_historical_data():
    with _db_lock:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM metrics")
        count = c.fetchone()[0]
        conn.close()
    if count > 0:
        return

    logger.info("Seeding 30-day historical dataset…")
    rows = []
    now = datetime.now()
    base = {
        "Revenue ($M)":      (10.5, 1.2),
        "Active Users":      (82000, 5000),
        "Conversion Rate":   (4.2, 0.4),
        "Avg Response (ms)": (115, 18),
        "Error Rate":        (0.45, 0.12),
        "CPU Usage":         (58, 8),
    }
    trend_factors = {k: np.linspace(0.9, 1.1, 30 * 8) for k in base}

    for day in range(30, 0, -1):
        for hour in range(0, 24, 3):
            step = (30 - day) * 8 + hour // 3
            ts = (now - timedelta(days=day, hours=hour)
                  ).strftime("%Y-%m-%d %H:%M:%S")
            for metric, (mu, sigma) in base.items():
                tf = trend_factors[metric][min(
                    step, len(trend_factors[metric])-1)]
                for sector in SECTORS:
                    val = max(0, random.gauss(mu * tf, sigma))
                    if random.random() < 0.025:
                        val *= random.uniform(1.9, 2.8)
                    rows.append((ts, metric, val, sector))

    with _db_lock:
        conn = get_conn()
        conn.executemany(
            "INSERT INTO metrics (ts, metric, value, sector) VALUES (?,?,?,?)", rows)
        conn.commit()
        conn.close()
    logger.info(f"Seeded {len(rows):,} metric records")


# ══════════════════════════════════════════════════════════════════════════════
# DATA ENGINE
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=REFRESH_SEC, show_spinner=False)
def fetch_metrics(days: int = 7, sector: str = "All") -> pd.DataFrame:
    since = (datetime.now() - timedelta(days=days)
             ).strftime("%Y-%m-%d %H:%M:%S")
    q = "SELECT ts, metric, value, sector FROM metrics WHERE ts >= ?"
    params = [since]
    if sector != "All":
        q += " AND sector = ?"
        params.append(sector)
    with _db_lock:
        conn = get_conn()
        df = pd.read_sql_query(q, conn, params=params, parse_dates=["ts"])
        conn.close()
    return df


@st.cache_data(ttl=8, show_spinner=False)
def fetch_alerts(limit: int = 100) -> pd.DataFrame:
    with _db_lock:
        conn = get_conn()
        df = pd.read_sql_query(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?",
            conn, params=[limit], parse_dates=["ts"])
        conn.close()
    return df


@st.cache_data(ttl=12, show_spinner=False)
def fetch_agent_runs() -> pd.DataFrame:
    with _db_lock:
        conn = get_conn()
        df = pd.read_sql_query(
            "SELECT * FROM agent_runs ORDER BY ts DESC LIMIT 200",
            conn, parse_dates=["ts"])
        conn.close()
    return df


@st.cache_data(ttl=12, show_spinner=False)
def fetch_anomalies() -> pd.DataFrame:
    with _db_lock:
        conn = get_conn()
        df = pd.read_sql_query(
            "SELECT * FROM anomalies ORDER BY ts DESC LIMIT 500",
            conn, parse_dates=["ts"])
        conn.close()
    return df


def get_latest_values(sector: str = "All") -> dict:
    result = {}
    with _db_lock:
        conn = get_conn()
        c = conn.cursor()
        for metric in KPIS:
            if sector == "All":
                row = c.execute(
                    "SELECT value FROM metrics WHERE metric=? ORDER BY ts DESC LIMIT 1",
                    [metric]).fetchone()
            else:
                row = c.execute(
                    "SELECT value FROM metrics WHERE metric=? AND sector=? ORDER BY ts DESC LIMIT 1",
                    [metric, sector]).fetchone()
            result[metric] = row[0] if row else 0.0
        conn.close()
    return result


def get_previous_values(sector: str = "All") -> dict:
    """Get value from ~1 hour ago for delta calculation."""
    result = {}
    since = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    until = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_conn()
        c = conn.cursor()
        for metric in KPIS:
            if sector == "All":
                row = c.execute(
                    "SELECT AVG(value) FROM metrics WHERE metric=? AND ts BETWEEN ? AND ?",
                    [metric, since, until]).fetchone()
            else:
                row = c.execute(
                    "SELECT AVG(value) FROM metrics WHERE metric=? AND sector=? AND ts BETWEEN ? AND ?",
                    [metric, sector, since, until]).fetchone()
            result[metric] = row[0] if row and row[0] else 0.0
        conn.close()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CORE
# ══════════════════════════════════════════════════════════════════════════════
def detect_anomalies(df: pd.DataFrame) -> list:
    anomalies = []
    for metric in KPIS:
        series = df[df["metric"] == metric]["value"].dropna()
        if len(series) < 10:
            continue
        mu, sigma = series.mean(), series.std()
        if sigma == 0:
            continue
        latest = series.iloc[-1]
        z = abs((latest - mu) / sigma)
        if z > 2.0:
            severity = "CRITICAL" if z > 3.5 else "WARNING"
            anomalies.append({
                "metric": metric, "value": latest,
                "zscore": round(z, 3), "severity": severity,
                "mu": round(mu, 3), "sigma": round(sigma, 3),
            })
    return anomalies


def check_kpi_thresholds(latest: dict) -> list:
    alerts = []
    for metric, cfg in KPIS.items():
        val = latest.get(metric, 0)
        target = cfg["target"]
        if target == 0:
            continue
        deviation = abs(val - target) / target * 100
        breached = False
        if cfg["good"] == "high" and val < target * 0.85:
            breached = True
        elif cfg["good"] == "low" and val > target * 1.35:
            breached = True
        if breached:
            alerts.append({
                "metric": metric, "value": val, "target": target,
                "deviation": round(deviation, 1),
                "severity": "CRITICAL" if deviation > 30 else "WARNING",
            })
    return alerts


def generate_live_tick():
    t0 = time.time()
    rows = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hour = datetime.now().hour
    # Diurnal variation
    day_factor = 0.8 + 0.4 * abs(np.sin(hour * np.pi / 12))

    base = {
        "Revenue ($M)":      (10.5 * day_factor, 0.9),
        "Active Users":      (82000 * day_factor, 3500),
        "Conversion Rate":   (4.2, 0.35),
        "Avg Response (ms)": (115 / day_factor, 16),
        "Error Rate":        (0.45, 0.1),
        "CPU Usage":         (58 * day_factor, 7),
    }
    for metric, (mu, sigma) in base.items():
        for sector in SECTORS:
            val = max(0, random.gauss(mu, sigma))
            if random.random() < 0.035:
                val *= random.uniform(2.1, 3.2)
            rows.append((ts, metric, val, sector))

    with _db_lock:
        conn = get_conn()
        conn.executemany(
            "INSERT INTO metrics (ts, metric, value, sector) VALUES (?,?,?,?)", rows)
        # Keep DB lean: delete records older than 60 days
        conn.execute("DELETE FROM metrics WHERE ts < ?",
                     [(datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")])
        conn.commit()
        conn.close()

    duration_ms = (time.time() - t0) * 1000
    logger.info(f"Tick: {len(rows)} records in {duration_ms:.1f}ms")
    return rows, duration_ms


def persist_alert(severity, metric, message):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO alerts (ts, severity, metric, message) VALUES (?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), severity, metric, message))
        conn.commit()
        conn.close()


def persist_anomaly(metric, value, zscore, severity):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO anomalies (ts, metric, value, zscore, severity) VALUES (?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), metric, value, zscore, severity))
        conn.commit()
        conn.close()


def persist_agent_run(run_type, records, alerts_fired, anomalies, duration_ms):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO agent_runs (ts, run_type, records, alerts_fired, anomalies, duration_ms) VALUES (?,?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), run_type, records, alerts_fired, anomalies, duration_ms))
        conn.commit()
        conn.close()


def run_agent_pipeline(trigger="scheduled"):
    logger.info(f"══ Pipeline [{trigger}] start ══")
    t0 = time.time()

    rows, _ = generate_live_tick()

    since = (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_conn()
        df = pd.read_sql_query(
            "SELECT ts, metric, value FROM metrics WHERE ts >= ?",
            conn, params=[since], parse_dates=["ts"])
        conn.close()

    anom_list = detect_anomalies(df)
    for a in anom_list:
        persist_anomaly(a["metric"], a["value"], a["zscore"], a["severity"])
        persist_alert(a["severity"], a["metric"],
                      f"Anomaly: val={a['value']:.2f} z={a['zscore']:.2f} μ={a['mu']:.2f} σ={a['sigma']:.2f}")
        logger.warning(
            f"[ANOMALY] {a['metric']} z={a['zscore']:.2f} {a['severity']}")

    latest = {m: df[df["metric"] == m]["value"].iloc[-1]
              for m in KPIS if not df[df["metric"] == m].empty}
    thresh_alerts = check_kpi_thresholds(latest)
    for ta in thresh_alerts:
        persist_alert(ta["severity"], ta["metric"],
                      f"KPI breach: {ta['value']:.2f} vs target {ta['target']:.2f} (Δ{ta['deviation']}%)")
        logger.warning(f"[THRESHOLD] {ta['metric']} dev={ta['deviation']}%")

    total_alerts = len(anom_list) + len(thresh_alerts)
    duration_ms = (time.time() - t0) * 1000
    persist_agent_run(trigger, len(rows), total_alerts,
                      len(anom_list), duration_ms)
    logger.info(
        f"══ Pipeline done: {total_alerts} alerts in {duration_ms:.1f}ms ══")
    return total_alerts, len(anom_list)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════════════════
def init_scheduler():
    if not SCHEDULER_AVAILABLE:
        logger.warning(
            "APScheduler not available — using Streamlit auto-rerun only")
        return None
    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    sched.add_job(
        run_agent_pipeline,
        trigger=IntervalTrigger(seconds=REFRESH_SEC),
        id="nexus_pipeline",
        replace_existing=True,
        max_instances=1,
        kwargs={"trigger": "auto"},
    )
    sched.start()
    logger.info(f"APScheduler running — interval {REFRESH_SEC}s")
    return sched


# ══════════════════════════════════════════════════════════════════════════════
# CHART BUILDERS
# ══════════════════════════════════════════════════════════════════════════════
SECTOR_COLORS = {
    "Technology": C_ACCENT,
    "Finance":    C_ACCENT2,
    "Healthcare": C_GREEN,
    "Energy":     C_YELLOW,
    "Retail":     C_RED,
}


def chart_timeseries(df: pd.DataFrame, metric: str) -> go.Figure:
    fig = go.Figure()
    for sector in SECTORS:
        sub = df[(df["metric"] == metric) & (
            df["sector"] == sector)].sort_values("ts")
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["ts"], y=sub["value"],
            mode="lines", name=sector,
            line=dict(width=1.8, color=SECTOR_COLORS.get(sector, C_TEXT)),
            hovertemplate=f"<b>{sector}</b><br>%{{x|%H:%M %d %b}}<br><b>%{{y:.2f}}</b><extra></extra>",
        ))
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text=f"<b>{metric}</b>  <span style='font-size:11px;color:{C_MUTED}'>· all sectors</span>",
                                 font=dict(size=14, color=C_WHITE)))
    return fig


def chart_rolling_avg(df: pd.DataFrame, metric: str) -> go.Figure:
    sub = df[df["metric"] == metric].sort_values("ts")
    if sub.empty:
        return go.Figure()
    agg = sub.groupby("ts")["value"].mean().reset_index()
    agg["ma10"] = agg["value"].rolling(10, min_periods=1).mean()
    agg["ma30"] = agg["value"].rolling(30, min_periods=1).mean()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=agg["ts"], y=agg["value"], mode="lines",
                             name="Raw", line=dict(color="rgba(200,214,244,0.2)", width=1)))
    fig.add_trace(go.Scatter(x=agg["ts"], y=agg["ma10"], mode="lines",
                             name="MA-10", line=dict(color=C_ACCENT, width=2)))
    fig.add_trace(go.Scatter(x=agg["ts"], y=agg["ma30"], mode="lines",
                             name="MA-30", line=dict(color=C_GREEN, width=2, dash="dot")))
    # Confidence band
    agg["upper"] = agg["ma10"] + agg["value"].rolling(10, min_periods=1).std()
    agg["lower"] = agg["ma10"] - agg["value"].rolling(10, min_periods=1).std()
    fig.add_trace(go.Scatter(
        x=pd.concat([agg["ts"], agg["ts"][::-1]]),
        y=pd.concat([agg["upper"], agg["lower"][::-1]]),
        fill="toself", fillcolor=f"rgba(0,200,255,0.06)",
        line=dict(color="rgba(0,0,0,0)"), showlegend=False, name="1σ band",
    ))
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text=f"<b>{metric}</b>  <span style='font-size:11px;color:{C_MUTED}'>· moving averages + 1σ band</span>",
                                 font=dict(size=14, color=C_WHITE)))
    return fig


def chart_gauge(metric: str, value: float, target: float, cfg: dict) -> go.Figure:
    if cfg["good"] == "high":
        ratio = value / target if target else 1
        c = C_GREEN if ratio >= 0.9 else (C_YELLOW if ratio >= 0.75 else C_RED)
    else:
        ratio = target / value if value > 0 else 1
        c = C_GREEN if ratio >= 0.9 else (C_YELLOW if ratio >= 0.75 else C_RED)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=value,
        delta=dict(reference=target, valueformat=cfg["fmt"],
                   increasing=dict(
                       color=C_GREEN if cfg["good"] == "high" else C_RED),
                   decreasing=dict(color=C_RED if cfg["good"] == "high" else C_GREEN)),
        number=dict(valueformat=cfg["fmt"], suffix=cfg["unit"],
                    font=dict(size=24, color=c, family="'DM Mono', monospace")),
        gauge=dict(
            axis=dict(range=[0, target * 2], tickfont=dict(size=9, color=C_MUTED),
                      tickcolor=C_MUTED, nticks=5),
            bar=dict(color=c, thickness=0.28),
            bgcolor="rgba(255,255,255,0.02)",
            bordercolor=C_BORDER, borderwidth=1,
            steps=[
                dict(range=[0, target * 0.75],
                     color="rgba(255,62,108,0.1)"),
                dict(range=[target * 0.75, target],
                     color="rgba(255,210,63,0.1)"),
                dict(range=[target, target * 2],
                     color="rgba(0,229,160,0.08)"),
            ],
            threshold=dict(line=dict(color=C_WHITE, width=2),
                           thickness=0.8, value=target),
        ),
        title=dict(text=f"{cfg['icon']} {metric}", font=dict(size=11, color=C_MUTED,
                   family="'DM Mono', monospace")),
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT, family="'DM Mono', monospace"),
        margin=dict(l=10, r=10, t=20, b=10),
        height=210,
    )
    return fig


def chart_heatmap(df: pd.DataFrame) -> go.Figure:
    pivot = df.groupby(["sector", "metric"])[
        "value"].mean().unstack(fill_value=0)
    if pivot.empty:
        return go.Figure()
    norm = pivot.copy()
    for col in norm.columns:
        rng = norm[col].max() - norm[col].min()
        if rng > 0:
            norm[col] = (norm[col] - norm[col].min()) / rng
    fig = go.Figure(go.Heatmap(
        z=norm.values,
        x=[c[:20] for c in norm.columns],
        y=norm.index.tolist(),
        colorscale=[[0, C_BG], [0.4, "#0A2040"],
                    [0.7, "#004D8C"], [1, C_ACCENT]],
        hoverongaps=False,
        hovertemplate="<b>%{y}</b> · %{x}<br>Normalised: <b>%{z:.3f}</b><extra></extra>",
        showscale=True,
        colorbar=dict(tickfont=dict(size=9, color=C_MUTED), thickness=12,
                      outlinecolor=C_BORDER, outlinewidth=1),
    ))
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text="<b>Sector × Metric Heatmap</b>  <span style='font-size:11px;color:#4A5568'>· normalised intensities</span>",
                                 font=dict(size=14, color=C_WHITE)))
    return fig


def chart_alert_dist(alerts_df: pd.DataFrame) -> go.Figure:
    if alerts_df.empty:
        return go.Figure()
    counts = alerts_df.groupby(
        ["metric", "severity"]).size().reset_index(name="count")
    fig = px.bar(counts, x="metric", y="count", color="severity",
                 color_discrete_map={"CRITICAL": C_RED, "WARNING": C_YELLOW},
                 barmode="stack", text="count")
    fig.update_traces(textfont_size=10, textposition="outside")
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text="<b>Alerts by Metric & Severity</b>",
                                 font=dict(size=14, color=C_WHITE)),
                      xaxis_tickangle=-25, bargap=0.35)
    return fig


def chart_anomaly_scatter(anom_df: pd.DataFrame) -> go.Figure:
    if anom_df.empty:
        return go.Figure()
    colors = anom_df["severity"].map(
        {"CRITICAL": C_RED, "WARNING": C_YELLOW}).fillna(C_MUTED)
    sizes = anom_df["zscore"].clip(2, 6) * 4
    fig = go.Figure(go.Scatter(
        x=anom_df["ts"], y=anom_df["zscore"],
        mode="markers",
        marker=dict(color=colors, size=sizes, opacity=0.85,
                    line=dict(width=1, color=C_WHITE)),
        text=anom_df["metric"],
        customdata=anom_df[["metric", "value", "severity"]],
        hovertemplate="<b>%{customdata[0]}</b><br>z-score: <b>%{y:.2f}</b><br>value: %{customdata[1]:.2f}<br>%{x}<extra></extra>",
    ))
    fig.add_hline(y=2.0, line_dash="dot", line_color=C_MUTED,
                  annotation_text="Threshold 2.0σ", annotation_font_color=C_MUTED, annotation_font_size=9)
    fig.add_hline(y=2.5, line_dash="dash", line_color=C_YELLOW,
                  annotation_text="Warning 2.5σ", annotation_font_color=C_YELLOW, annotation_font_size=10)
    fig.add_hline(y=3.5, line_dash="dash", line_color=C_RED,
                  annotation_text="Critical 3.5σ", annotation_font_color=C_RED, annotation_font_size=10)
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text="<b>Anomaly Z-Score Timeline</b>  <span style='font-size:11px;color:#4A5568'>· statistical deviation detection</span>",
                                 font=dict(size=14, color=C_WHITE)))
    return fig


def chart_agent_perf(runs_df: pd.DataFrame) -> go.Figure:
    if runs_df.empty:
        return go.Figure()
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Records Ingested per Run", "Pipeline Duration (ms)"),
        horizontal_spacing=0.08,
    )
    fig.add_trace(go.Bar(
        x=runs_df["ts"], y=runs_df["records"],
        name="Records", marker_color=f"rgba(0,200,255,0.55)",
        hovertemplate="%{y:,} records<br>%{x}<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=runs_df["ts"], y=runs_df["alerts_fired"],
        name="Alerts", mode="lines+markers",
        line=dict(color=C_YELLOW, width=1.5), marker=dict(size=5),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=runs_df["ts"], y=runs_df["duration_ms"],
        name="Duration ms", mode="lines+markers",
        line=dict(color=C_GREEN, width=1.8), marker=dict(size=5),
        fill="tozeroy", fillcolor="rgba(0,229,160,0.08)",
    ), row=1, col=2)
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text="<b>Agent Performance Monitor</b>",
                                 font=dict(size=14, color=C_WHITE)),
                      showlegend=True)
    for ann in fig.layout.annotations:
        ann.font.color = C_MUTED
        ann.font.size = 11
    return fig


def chart_sector_trend(df: pd.DataFrame, metric: str) -> go.Figure:
    """Sector-level area chart for the focused metric."""
    fig = go.Figure()
    for sector in SECTORS:
        sub = df[(df["metric"] == metric) & (
            df["sector"] == sector)].sort_values("ts")
        if sub.empty:
            continue
        c = SECTOR_COLORS.get(sector, C_TEXT)
        fig.add_trace(go.Scatter(
            x=sub["ts"], y=sub["value"],
            mode="lines", name=sector,
            line=dict(width=0), stackgroup="one",
            fillcolor=c.replace(")", ",0.18)").replace("rgb", "rgba") if "rgb" in c
            else f"rgba(0,200,255,0.14)",
            hovertemplate=f"<b>{sector}</b> %{{y:.2f}}<extra></extra>",
        ))
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text=f"<b>{metric}</b>  <span style='font-size:11px;color:{C_MUTED}'>· stacked sector areas</span>",
                                 font=dict(size=14, color=C_WHITE)))
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════════════════
def inject_css():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Syne:wght@400;600;700;800&family=Instrument+Serif:ital@0;1&display=swap');

    *, *::before, *::after {{ box-sizing: border-box; }}

    html, body, [class*="css"] {{
        font-family: 'DM Mono', 'Fira Code', monospace !important;
        -webkit-font-smoothing: antialiased;
    }}

    .stApp {{
        background: {C_BG};
        background-image:
            radial-gradient(ellipse 80% 50% at 20% -20%, rgba(0,200,255,0.06) 0%, transparent 60%),
            radial-gradient(ellipse 60% 40% at 80% 110%, rgba(123,47,255,0.06) 0%, transparent 60%);
        min-height: 100vh;
    }}

    /* ─── Sidebar ──────────────────────────────────── */
    section[data-testid="stSidebar"] {{
        background: rgba(4,8,20,0.97) !important;
        border-right: 1px solid {C_BORDER} !important;
    }}
    section[data-testid="stSidebar"] * {{ color: {C_TEXT}; }}

    /* ─── KPI Cards ────────────────────────────────── */
    .nexus-kpi {{
        background: linear-gradient(145deg, rgba(0,200,255,0.05) 0%, rgba(7,13,28,0.95) 100%);
        border: 1px solid {C_BORDER};
        border-radius: 14px;
        padding: 20px 18px 16px;
        position: relative;
        overflow: hidden;
        transition: border-color 0.25s ease, transform 0.2s ease;
        cursor: default;
    }}
    .nexus-kpi::after {{
        content: '';
        position: absolute; top: 0; left: 0;
        width: 100%; height: 2px;
        background: linear-gradient(90deg, {C_ACCENT}, {C_ACCENT2});
        opacity: 0;
        transition: opacity 0.25s ease;
    }}
    .nexus-kpi:hover {{ border-color: rgba(0,200,255,0.4); transform: translateY(-2px); }}
    .nexus-kpi:hover::after {{ opacity: 1; }}

    .kpi-icon  {{ font-size: 11px; color: {C_MUTED}; letter-spacing: 2px; margin-bottom: 6px; }}
    .kpi-val   {{ font-family: 'Syne', sans-serif; font-size: 26px; font-weight: 700; color: {C_WHITE}; line-height: 1.1; }}
    .kpi-unit  {{ font-size: 12px; color: {C_MUTED}; margin-left: 3px; font-weight: 400; }}
    .kpi-meta  {{ font-size: 10px; margin-top: 5px; letter-spacing: 0.5px; }}
    .kpi-up    {{ color: {C_GREEN}; }}
    .kpi-down  {{ color: {C_RED}; }}
    .kpi-warn  {{ color: {C_YELLOW}; }}

    /* ─── Stat Blocks ──────────────────────────────── */
    .stat-block {{
        background: rgba(0,0,0,0.35);
        border: 1px solid {C_BORDER};
        border-radius: 10px;
        padding: 16px 12px;
        text-align: center;
        transition: border-color 0.2s;
    }}
    .stat-block:hover {{ border-color: rgba(0,200,255,0.25); }}
    .stat-val   {{ font-family: 'Syne', sans-serif; font-size: 26px; font-weight: 700; line-height: 1; }}
    .stat-label {{ font-size: 9px; letter-spacing: 2.5px; color: {C_MUTED}; margin-top: 5px; text-transform: uppercase; }}

    /* ─── Alert rows ───────────────────────────────── */
    .alert-row {{
        display: flex; align-items: flex-start; gap: 14px;
        padding: 10px 0;
        border-bottom: 1px solid rgba(255,255,255,0.04);
        transition: background 0.15s;
    }}
    .alert-row:hover {{ background: rgba(0,200,255,0.03); border-radius: 6px; }}

    .badge {{
        display: inline-flex; align-items: center; justify-content: center;
        padding: 2px 10px; border-radius: 20px;
        font-size: 9px; font-weight: 700; letter-spacing: 1.5px;
        white-space: nowrap; min-width: 72px;
    }}
    .badge-crit {{ background: rgba(255,62,108,0.18); color: {C_RED};    border: 1px solid rgba(255,62,108,0.35); }}
    .badge-warn {{ background: rgba(255,210,63,0.18); color: {C_YELLOW}; border: 1px solid rgba(255,210,63,0.35); }}
    .badge-ok   {{ background: rgba(0,229,160,0.15);  color: {C_GREEN};  border: 1px solid rgba(0,229,160,0.3);  }}

    /* ─── Section headers ──────────────────────────── */
    .sec-hdr {{
        font-family: 'Syne', sans-serif; font-size: 18px; font-weight: 700;
        color: {C_WHITE}; letter-spacing: 1px;
        border-bottom: 1px solid {C_BORDER}; padding-bottom: 8px; margin: 0 0 4px;
    }}
    .sec-sub {{ font-size: 10px; color: {C_MUTED}; letter-spacing: 2.5px; text-transform: uppercase; margin-bottom: 20px; }}

    /* ─── Sidebar labels ───────────────────────────── */
    .sb-logo {{
        font-family: 'Syne', sans-serif; font-size: 24px; font-weight: 800;
        background: linear-gradient(135deg, {C_ACCENT} 0%, {C_ACCENT2} 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        text-align: center; letter-spacing: 3px; padding: 8px 0 2px;
    }}
    .sb-sub {{
        font-size: 9px; color: {C_MUTED}; text-align: center;
        letter-spacing: 3.5px; text-transform: uppercase; margin-bottom: 18px;
    }}

    /* ─── Status pulse ─────────────────────────────── */
    .pulse-dot {{
        display: inline-block; width: 8px; height: 8px;
        border-radius: 50%; margin-right: 7px;
        animation: pulse 1.8s ease-in-out infinite;
    }}
    .pulse-green {{ background: {C_GREEN}; box-shadow: 0 0 10px {C_GREEN}; }}
    .pulse-red   {{ background: {C_RED};   box-shadow: 0 0 10px {C_RED};   }}
    @keyframes pulse {{
        0%, 100% {{ opacity:1; transform:scale(1); }}
        50% {{ opacity:0.5; transform:scale(0.75); }}
    }}

    /* ─── Log box ──────────────────────────────────── */
    .log-box {{
        background: rgba(0,0,0,0.65);
        border: 1px solid {C_BORDER};
        border-radius: 8px; padding: 12px 14px;
        font-size: 11px; font-family: 'DM Mono', monospace; color: {C_MUTED};
        max-height: 340px; overflow-y: auto; white-space: pre-wrap;
        line-height: 1.6;
    }}
    .log-box::-webkit-scrollbar {{ width: 4px; }}
    .log-box::-webkit-scrollbar-track {{ background: transparent; }}
    .log-box::-webkit-scrollbar-thumb {{ background: {C_BORDER}; border-radius: 2px; }}

    /* ─── Streamlit overrides ──────────────────────── */
    .stMetric label {{ color: {C_MUTED} !important; font-size: 10px !important; letter-spacing: 1.5px !important; }}
    div[data-testid="stSelectbox"] label,
    div[data-testid="stSlider"] label,
    div[data-testid="stRadio"] label,
    div[data-testid="stToggle"] label {{
        color: {C_MUTED} !important; font-size: 10px !important; letter-spacing: 1.5px !important;
    }}
    .stButton > button {{
        background: rgba(0,200,255,0.07) !important;
        border: 1px solid rgba(0,200,255,0.28) !important;
        color: {C_ACCENT} !important;
        border-radius: 8px !important;
        font-family: 'DM Mono', monospace !important;
        font-size: 11px !important; letter-spacing: 1px !important;
        transition: all 0.2s;
    }}
    .stButton > button:hover {{
        background: rgba(0,200,255,0.16) !important;
        border-color: {C_ACCENT} !important;
        transform: translateY(-1px);
        box-shadow: 0 4px 20px rgba(0,200,255,0.15) !important;
    }}
    div[data-testid="stTabs"] button {{
        color: {C_MUTED} !important;
        font-family: 'DM Mono', monospace !important;
        font-size: 11px !important; letter-spacing: 1.5px !important;
    }}
    div[data-testid="stTabs"] button[aria-selected="true"] {{
        color: {C_ACCENT} !important;
        border-bottom: 2px solid {C_ACCENT} !important;
    }}
    div[data-testid="stDataFrame"] {{
        border: 1px solid {C_BORDER} !important; border-radius: 8px !important;
    }}
    hr {{ border-color: {C_BORDER} !important; }}
    .stAlert {{ border-radius: 8px !important; }}
    div[data-testid="column"] {{ gap: 0; }}
    </style>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# UI COMPONENTS
# ══════════════════════════════════════════════════════════════════════════════
def kpi_card(metric: str, value: float, prev: float, cfg: dict):
    target = cfg["target"]
    pct_vs_target = (value - target) / target * 100 if target else 0
    pct_delta = (value - prev) / prev * 100 if prev else 0

    if cfg["good"] == "high":
        status_cls = "kpi-up" if value >= target * \
            0.9 else ("kpi-warn" if value >= target * 0.75 else "kpi-down")
    else:
        status_cls = "kpi-up" if value <= target * \
            1.1 else ("kpi-warn" if value <= target * 1.35 else "kpi-down")

    delta_arrow = "↑" if pct_delta >= 0 else "↓"
    target_arrow = "▲" if pct_vs_target >= 0 else "▼"
    fmt_val = format(value, cfg["fmt"])

    st.markdown(f"""
    <div class="nexus-kpi">
        <div class="kpi-icon">{cfg['icon']} {metric.upper()}</div>
        <div class="kpi-val">{fmt_val}<span class="kpi-unit">{cfg['unit']}</span></div>
        <div class="kpi-meta {status_cls}">
            {target_arrow} {abs(pct_vs_target):.1f}% vs target
            <span style="color:{C_MUTED};margin:0 6px">·</span>
            <span style="color:{'#00E5A0' if pct_delta>=0 else '#FF3E6C'}">{delta_arrow} {abs(pct_delta):.1f}% 1h</span>
        </div>
    </div>""", unsafe_allow_html=True)


def stat_block(label: str, value: str, color: str):
    st.markdown(f"""
    <div class="stat-block">
        <div class="stat-val" style="color:{color}">{value}</div>
        <div class="stat-label">{label}</div>
    </div>""", unsafe_allow_html=True)


def alert_row(row):
    sev = row.get("severity", "")
    badge_c = "badge-crit" if sev == "CRITICAL" else "badge-warn"
    ts = str(row.get("ts", ""))[:16]
    metric = str(row.get("metric", ""))[:22]
    msg = str(row.get("message", ""))[:90]
    st.markdown(f"""
    <div class="alert-row">
        <span class="badge {badge_c}">{sev}</span>
        <span style="color:{C_MUTED};font-size:10px;min-width:120px;padding-top:1px">{ts}</span>
        <span style="color:{C_WHITE};font-size:11px;min-width:150px;padding-top:1px">{metric}</span>
        <span style="color:{C_MUTED};font-size:11px;padding-top:1px">{msg}</span>
    </div>""", unsafe_allow_html=True)


def render_sidebar(sched_running: bool) -> dict:
    st.sidebar.markdown('<div class="sb-logo">◈ NEXUS</div>',
                        unsafe_allow_html=True)
    st.sidebar.markdown(
        '<div class="sb-sub">Autonomous Intelligence · v3.0</div>', unsafe_allow_html=True)

    dot = "pulse-green" if sched_running else "pulse-red"
    lbl = "AGENT ONLINE" if sched_running else "AGENT OFFLINE"
    lc = C_GREEN if sched_running else C_RED
    st.sidebar.markdown(
        f'<div style="text-align:center;margin-bottom:14px">'
        f'<span class="pulse-dot {dot}"></span>'
        f'<span style="font-size:10px;letter-spacing:2px;color:{lc}">{lbl}</span>'
        f'</div>', unsafe_allow_html=True)

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div style="font-size:9px;letter-spacing:2.5px;color:#4A5568;margin-bottom:8px">◈ DATA FILTERS</div>',
                        unsafe_allow_html=True)
    sector = st.sidebar.selectbox("Sector", ["All"] + SECTORS, key="sb_sector")
    days = st.sidebar.slider("History window (days)", 1, 30, 7, key="sb_days")
    metric = st.sidebar.selectbox(
        "Focus Metric", list(KPIS.keys()), key="sb_metric")

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div style="font-size:9px;letter-spacing:2.5px;color:#4A5568;margin-bottom:8px">◈ LIVE ENGINE</div>',
                        unsafe_allow_html=True)
    auto_refresh = st.sidebar.toggle(
        "Auto-Refresh UI", value=True, key="sb_refresh")
    refresh_rate = st.sidebar.slider(
        "Interval (sec)", 5, 60, REFRESH_SEC, key="sb_rate")

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div style="font-size:9px;letter-spacing:2.5px;color:#4A5568;margin-bottom:8px">◈ AGENT CONTROLS</div>',
                        unsafe_allow_html=True)
    run_now = st.sidebar.button(
        "▶  Run Pipeline Now", use_container_width=True)
    if run_now:
        with st.spinner("Executing agent pipeline…"):
            al, an = run_agent_pipeline("manual")
        st.sidebar.success(f"✓ Done · {al} alerts · {an} anomalies")
        st.cache_data.clear()

    gen_data = st.sidebar.button(
        "⟳  Inject Live Tick", use_container_width=True)
    if gen_data:
        generate_live_tick()
        st.sidebar.success("✓ Data tick injected")
        st.cache_data.clear()

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div style="font-size:9px;letter-spacing:2.5px;color:#4A5568;margin-bottom:6px">◈ EXPORTS</div>',
                        unsafe_allow_html=True)
    return dict(sector=sector, days=days, metric=metric,
                auto_refresh=auto_refresh, refresh_rate=refresh_rate)


def export_csv(df): return df.to_csv(index=False).encode("utf-8")


def export_json(df): return df.to_json(
    orient="records", date_format="iso").encode("utf-8")


def build_report(latest, prev, alerts_df):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "═"*65, "  NEXUS AUTONOMOUS INTELLIGENCE DASHBOARD",
        f"  Report generated: {now}", "  Version: 3.0.0", "═"*65, "",
        "KPI SUMMARY", "─"*50,
    ]
    for metric, cfg in KPIS.items():
        val = latest.get(metric, 0)
        p = prev.get(metric, val)
        target = cfg["target"]
        delta = (val - p) / p * 100 if p else 0
        ok = ((cfg["good"] == "high" and val >= target*0.85) or
              (cfg["good"] == "low" and val <= target*1.35))
        status = "✓ OK" if ok else "✗ BREACH"
        lines.append(f"  {metric:<25} {val:{cfg['fmt']}} {cfg['unit']:<4}  "
                     f"target={target}  Δ1h={delta:+.1f}%  [{status}]")
    lines += ["", "RECENT ALERTS (last 20)", "─"*50]
    if alerts_df.empty:
        lines.append("  No alerts.")
    else:
        for _, r in alerts_df.head(20).iterrows():
            lines.append(
                f"  [{r['severity']:<8}] {r['metric']:<25}  {r['message'][:55]}")
    lines += ["", "═"*65, "  END OF REPORT", "═"*65]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    inject_css()

    # ── Bootstrap ──────────────────────────────────────────────────────────
    init_db()
    seed_historical_data()

    # ── Scheduler singleton ────────────────────────────────────────────────
    if "scheduler" not in st.session_state:
        st.session_state.scheduler = init_scheduler()
        st.session_state.start_time = datetime.now()
        logger.info("NEXUS session started")

    scheduler = st.session_state.scheduler
    sched_running = scheduler.running if scheduler else False

    # ── Sidebar ────────────────────────────────────────────────────────────
    cfg = render_sidebar(sched_running)
    sector = cfg["sector"]
    days = cfg["days"]
    metric = cfg["metric"]

    # ── Header ─────────────────────────────────────────────────────────────
    h1, h2, h3 = st.columns([4, 2, 2])
    with h1:
        st.markdown(
            f'<h1 style="font-family:Syne,sans-serif;font-size:34px;font-weight:800;'
            f'color:{C_WHITE};margin:0;letter-spacing:1px;line-height:1.1">'
            f'<span style="background:linear-gradient(135deg,{C_ACCENT},{C_ACCENT2});'
            f'-webkit-background-clip:text;-webkit-text-fill-color:transparent">NEXUS</span>'
            f' <span style="font-weight:400;font-size:22px;color:{C_MUTED}">·</span>'
            f' AUTONOMOUS AGENT</h1>'
            f'<p style="font-size:10px;color:{C_MUTED};letter-spacing:3px;margin:4px 0 0 2px">'
            f'REAL-TIME · SELF-HEALING · INTELLIGENCE PIPELINE</p>',
            unsafe_allow_html=True)
    with h2:
        st.markdown(
            f'<div style="text-align:right;padding-top:6px">'
            f'<div style="font-size:22px;font-weight:500;color:{C_WHITE};font-family:Syne,sans-serif">'
            f'{datetime.now().strftime("%H:%M:%S")}</div>'
            f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:1px">'
            f'{datetime.now().strftime("%A · %d %b %Y")}</div></div>',
            unsafe_allow_html=True)
    with h3:
        uptime = datetime.now() - st.session_state.get("start_time", datetime.now())
        h, r = divmod(int(uptime.total_seconds()), 3600)
        m, s = divmod(r, 60)
        st.markdown(
            f'<div style="text-align:right;padding-top:6px">'
            f'<div style="font-size:22px;font-weight:500;color:{C_GREEN};font-family:Syne,sans-serif">'
            f'{h:02d}:{m:02d}:{s:02d}</div>'
            f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:1px">SESSION UPTIME</div></div>',
            unsafe_allow_html=True)

    st.markdown("---")

    # ── Fetch data ─────────────────────────────────────────────────────────
    df = fetch_metrics(days, sector)
    alerts_df = fetch_alerts()
    runs_df = fetch_agent_runs()
    anom_df = fetch_anomalies()
    latest = get_latest_values(sector)
    prev = get_previous_values(sector)

    # ── KPI Overview ───────────────────────────────────────────────────────
    st.markdown('<div class="sec-hdr">◈ KPI OVERVIEW</div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="sec-sub">Live · sector: {sector} · auto-refresh every {REFRESH_SEC}s</div>',
                unsafe_allow_html=True)

    kpi_cols = st.columns(6)
    for i, (m, kpi_cfg) in enumerate(KPIS.items()):
        with kpi_cols[i]:
            kpi_card(m, latest.get(m, 0), prev.get(m, 0), kpi_cfg)

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── Summary stats ──────────────────────────────────────────────────────
    total_recs = len(df)
    total_alerts = len(alerts_df)
    crit_count = len(alerts_df[alerts_df["severity"]
                     == "CRITICAL"]) if not alerts_df.empty else 0
    anom_count = len(anom_df)
    run_count = len(runs_df)
    avg_dur = runs_df["duration_ms"].mean() if not runs_df.empty else 0

    sc = st.columns(6)
    stat_data = [
        ("RECORDS",        f"{total_recs:,}",     C_ACCENT),
        ("TOTAL ALERTS",   f"{total_alerts:,}",
         C_YELLOW if total_alerts else C_GREEN),
        ("CRITICAL",       f"{crit_count}",
         C_RED if crit_count else C_GREEN),
        ("ANOMALIES",      f"{anom_count}",
         C_YELLOW if anom_count else C_GREEN),
        ("AGENT RUNS",     f"{run_count}",           C_ACCENT),
        ("AVG PIPELINE",   f"{avg_dur:.0f}ms",       C_GREEN),
    ]
    for col, (lbl, val, c) in zip(sc, stat_data):
        with col:
            stat_block(lbl, val, c)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Tabs ───────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📈 METRICS",
        "🎯 GAUGES",
        "🚨 ALERTS",
        "🔬 ANOMALIES",
        "🤖 AGENT",
        "📋 LOGS & EXPORT",
    ])

    # ── TAB 1: METRICS ─────────────────────────────────────────────────────
    with tab1:
        st.markdown('<div class="sec-hdr">TIME SERIES ANALYSIS</div>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="sec-sub">Sector: {sector} · Last {days} days · Focus: {metric}</div>',
                    unsafe_allow_html=True)
        if df.empty:
            st.warning(
                "No data found. Use 'Inject Live Tick' or 'Run Pipeline Now' in the sidebar.")
        else:
            st.plotly_chart(chart_timeseries(df, metric),
                            use_container_width=True)
            st.plotly_chart(chart_rolling_avg(df, metric),
                            use_container_width=True)
            st.plotly_chart(chart_sector_trend(df, metric),
                            use_container_width=True)
            st.markdown("---")
            others = [m for m in KPIS if m != metric]
            for i in range(0, len(others), 2):
                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(chart_timeseries(
                        df, others[i]), use_container_width=True)
                if i+1 < len(others):
                    with c2:
                        st.plotly_chart(chart_timeseries(
                            df, others[i+1]), use_container_width=True)
            st.markdown("---")
            st.plotly_chart(chart_heatmap(df), use_container_width=True)

    # ── TAB 2: GAUGES ──────────────────────────────────────────────────────
    with tab2:
        st.markdown('<div class="sec-hdr">KPI GAUGES</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="sec-sub">Real-time target vs actual · threshold indicators</div>',
                    unsafe_allow_html=True)
        g_cols = st.columns(3)
        for i, (m, kpi_cfg) in enumerate(KPIS.items()):
            with g_cols[i % 3]:
                st.plotly_chart(
                    chart_gauge(m, latest.get(m, 0),
                                kpi_cfg["target"], kpi_cfg),
                    use_container_width=True)

    # ── TAB 3: ALERTS ──────────────────────────────────────────────────────
    with tab3:
        st.markdown('<div class="sec-hdr">ALERT FEED</div>',
                    unsafe_allow_html=True)

        acol1, acol2, acol3 = st.columns([2, 2, 3])
        with acol1:
            sev_filter = st.selectbox(
                "Severity filter", ["All", "CRITICAL", "WARNING"], key="af_sev")
        with acol2:
            metric_filter = st.selectbox("Metric filter",
                                         ["All"] + list(KPIS.keys()), key="af_metric")
        filtered = alerts_df.copy()
        if sev_filter != "All" and not filtered.empty:
            filtered = filtered[filtered["severity"] == sev_filter]
        if metric_filter != "All" and not filtered.empty:
            filtered = filtered[filtered["metric"] == metric_filter]

        if filtered.empty:
            st.markdown(
                f'<div style="color:{C_GREEN};text-align:center;padding:50px;'
                f'border:1px solid rgba(0,229,160,0.2);border-radius:12px;'
                f'font-size:13px;letter-spacing:1px">◉ &nbsp;No alerts matching current filters</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="color:{C_MUTED};font-size:10px;letter-spacing:1px;'
                f'margin-bottom:8px">{len(filtered)} ALERT(S) FOUND</div>',
                unsafe_allow_html=True)
            for _, row in filtered.head(60).iterrows():
                alert_row(row)

        st.markdown("---")
        if not alerts_df.empty:
            st.plotly_chart(chart_alert_dist(alerts_df),
                            use_container_width=True)

    # ── TAB 4: ANOMALIES ───────────────────────────────────────────────────
    with tab4:
        st.markdown('<div class="sec-hdr">ANOMALY DETECTION</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="sec-sub">Z-score statistical detection · no ML dependencies · auto-triggered</div>',
                    unsafe_allow_html=True)

        if not df.empty:
            live_anom = detect_anomalies(df)
            if live_anom:
                st.markdown(
                    f'<div style="color:{C_RED};font-size:11px;letter-spacing:1px;'
                    f'margin-bottom:12px">⚠ &nbsp;{len(live_anom)} ACTIVE ANOMALIES IN CURRENT WINDOW</div>',
                    unsafe_allow_html=True)
                for a in live_anom:
                    badge = "badge-crit" if a["severity"] == "CRITICAL" else "badge-warn"
                    st.markdown(
                        f'<div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,62,108,0.18);'
                        f'border-radius:8px;padding:12px 16px;margin-bottom:8px;'
                        f'display:flex;gap:14px;align-items:center">'
                        f'<span class="badge {badge}">{a["severity"]}</span>'
                        f'<span style="color:{C_WHITE};font-size:12px;min-width:160px">{a["metric"]}</span>'
                        f'<span style="color:{C_MUTED};font-size:11px">'
                        f'val={a["value"]:.2f} · z={a["zscore"]:.2f} · μ={a["mu"]:.2f} · σ={a["sigma"]:.2f}'
                        f'</span></div>',
                        unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div style="color:{C_GREEN};padding:14px 18px;border:1px solid rgba(0,229,160,0.2);'
                    f'border-radius:8px;margin-bottom:16px;font-size:12px;letter-spacing:0.5px">'
                    f'✓ &nbsp;No anomalies in current data window</div>',
                    unsafe_allow_html=True)

        st.plotly_chart(chart_anomaly_scatter(
            anom_df), use_container_width=True)

        if not anom_df.empty:
            st.markdown(
                f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:2px;margin-bottom:8px">ANOMALY HISTORY</div>',
                unsafe_allow_html=True)
            st.dataframe(
                anom_df[["ts", "metric", "value",
                         "zscore", "severity"]].head(100),
                use_container_width=True, hide_index=True)

    # ── TAB 5: AGENT ───────────────────────────────────────────────────────
    with tab5:
        st.markdown('<div class="sec-hdr">AGENT INTELLIGENCE</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="sec-sub">Autonomous pipeline · APScheduler · self-monitoring</div>',
                    unsafe_allow_html=True)

        # Pipeline status card
        next_run_str = "—"
        if scheduler and sched_running:
            job = scheduler.get_job("nexus_pipeline")
            if job and job.next_run_time:
                next_run_str = job.next_run_time.strftime("%H:%M:%S")

        st.markdown(
            f'<div style="background:rgba(0,200,255,0.05);border:1px solid rgba(0,200,255,0.2);'
            f'border-radius:14px;padding:22px 24px;margin-bottom:22px">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div><div style="font-family:Syne,sans-serif;font-size:18px;font-weight:700;color:{C_WHITE}">'
            f'Pipeline Status</div>'
            f'<div style="font-size:10px;color:{C_MUTED};margin-top:3px;letter-spacing:1px">'
            f'APScheduler · Background Daemon · SQLite persistence</div></div>'
            f'<div><span class="pulse-dot {"pulse-green" if sched_running else "pulse-red"}"></span>'
            f'<span style="font-size:13px;color:{"#00E5A0" if sched_running else "#FF3E6C"};font-weight:600">'
            f'{"LIVE" if sched_running else "STOPPED"}</span></div></div>'
            f'<div style="display:flex;gap:30px;margin-top:18px;flex-wrap:wrap">'
            f'<div><div style="font-family:Syne,sans-serif;font-size:22px;font-weight:700;color:{C_ACCENT}">{REFRESH_SEC}s</div>'
            f'<div style="font-size:9px;color:{C_MUTED};letter-spacing:2px">INTERVAL</div></div>'
            f'<div><div style="font-family:Syne,sans-serif;font-size:22px;font-weight:700;color:{C_GREEN}">{run_count}</div>'
            f'<div style="font-size:9px;color:{C_MUTED};letter-spacing:2px">TOTAL RUNS</div></div>'
            f'<div><div style="font-family:Syne,sans-serif;font-size:22px;font-weight:700;color:{C_YELLOW}">{next_run_str}</div>'
            f'<div style="font-size:9px;color:{C_MUTED};letter-spacing:2px">NEXT RUN</div></div>'
            f'<div><div style="font-family:Syne,sans-serif;font-size:22px;font-weight:700;color:{C_ACCENT}">{avg_dur:.0f}ms</div>'
            f'<div style="font-size:9px;color:{C_MUTED};letter-spacing:2px">AVG DURATION</div></div>'
            f'</div></div>',
            unsafe_allow_html=True)

        # Pipeline stages
        st.markdown(f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:2.5px;margin-bottom:12px">◈ PIPELINE STAGES</div>',
                    unsafe_allow_html=True)
        stages = [
            ("01", "Data Ingestion",
             "Simulated multi-source tick with diurnal variation & spike injection",  C_GREEN),
            ("02", "Window Analysis",
             "8-hour rolling window fetched from SQLite for fresh statistics",        C_ACCENT),
            ("03", "Anomaly Detection",
             "Z-score computation (σ threshold: 2.0/2.5/3.5) on each metric",        C_ACCENT),
            ("04", "Threshold Monitor",
             "KPI vs target: good-high (85%) / good-low (135%) deviation rules",     C_ACCENT),
            ("05", "Alert Persistence",
             "Severity-tagged alerts + anomaly records written to SQLite",           C_GREEN),
            ("06", "Agent Run Logging",
             "Timing, record count, alert count logged per run for analytics",       C_GREEN),
            ("07", "DB Housekeeping",
             "Auto-prune records older than 60 days to keep DB lean",                C_YELLOW),
            ("08", "Cache Invalidation",
             "st.cache_data TTL forces fresh reads on next Streamlit render",        C_MUTED),
        ]
        for num, name, desc, color in stages:
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:18px;padding:9px 0;'
                f'border-bottom:1px solid rgba(255,255,255,0.03)">'
                f'<span style="font-size:9px;color:{color};min-width:22px;font-weight:700;letter-spacing:1px">{num}</span>'
                f'<span style="font-size:12px;color:{C_WHITE};min-width:190px;font-weight:500">{name}</span>'
                f'<span style="font-size:10px;color:{C_MUTED}">{desc}</span>'
                f'</div>', unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        st.plotly_chart(chart_agent_perf(runs_df), use_container_width=True)

        if not runs_df.empty:
            a1, a2, a3 = st.columns(3)
            total_fired = int(runs_df["alerts_fired"].sum())
            total_anom = int(runs_df["anomalies"].sum(
            )) if "anomalies" in runs_df.columns else "—"
            max_dur = runs_df["duration_ms"].max()
            for col, lbl, val, c in [
                (a1, "TOTAL ALERTS FIRED",     f"{total_fired}",   C_YELLOW),
                (a2, "TOTAL ANOMALIES FOUND",  f"{total_anom}",    C_RED),
                (a3, "MAX PIPELINE DURATION",  f"{max_dur:.0f}ms", C_ACCENT),
            ]:
                with col:
                    stat_block(lbl, val, c)

    # ── TAB 6: LOGS & EXPORT ────────────────────────────────────────────────
    with tab6:
        st.markdown('<div class="sec-hdr">LOGS & EXPORT</div>',
                    unsafe_allow_html=True)

        lc1, lc2 = st.columns([3, 2])
        with lc1:
            st.markdown(f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:2px;margin-bottom:8px">◈ LIVE AGENT LOG</div>',
                        unsafe_allow_html=True)
            if Path(LOG_PATH).exists():
                with open(LOG_PATH, "r") as f:
                    lines = f.readlines()[-MAX_LOG_LINES:]
                log_text = "".join(reversed(lines))
                # Colour WARNING / CRITICAL lines
                log_html = ""
                for ln in reversed(lines):
                    if "CRITICAL" in ln or "ERROR" in ln:
                        log_html += f'<span style="color:{C_RED}">{ln}</span>'
                    elif "WARNING" in ln or "ANOMALY" in ln or "THRESHOLD" in ln:
                        log_html += f'<span style="color:{C_YELLOW}">{ln}</span>'
                    elif "Pipeline" in ln or "Session" in ln or "Seeded" in ln:
                        log_html += f'<span style="color:{C_GREEN}">{ln}</span>'
                    else:
                        log_html += ln
                st.markdown(
                    f'<div class="log-box">{log_html}</div>', unsafe_allow_html=True)
            else:
                st.info("No log file yet. Run the agent pipeline to generate logs.")

        with lc2:
            st.markdown(f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:2px;margin-bottom:12px">◈ DATA EXPORTS</div>',
                        unsafe_allow_html=True)
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            if not df.empty:
                st.download_button("⬇  Metrics (CSV)",  export_csv(
                    df),  f"nexus_metrics_{ts_str}.csv",  "text/csv",             use_container_width=True)
                st.download_button("⬇  Metrics (JSON)", export_json(
                    df), f"nexus_metrics_{ts_str}.json", "application/json",     use_container_width=True)
            if not alerts_df.empty:
                st.download_button("⬇  Alerts (CSV)",   export_csv(
                    alerts_df), f"nexus_alerts_{ts_str}.csv", "text/csv",        use_container_width=True)
            if not anom_df.empty:
                st.download_button("⬇  Anomalies (CSV)", export_csv(
                    anom_df), f"nexus_anomalies_{ts_str}.csv", "text/csv",        use_container_width=True)
            st.markdown("<div style='height:10px'></div>",
                        unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:10px;color:{C_MUTED};letter-spacing:2px;margin-bottom:8px">◈ INTELLIGENCE REPORT</div>',
                        unsafe_allow_html=True)
            report = build_report(latest, prev, alerts_df)
            st.download_button("⬇  Download Report (.txt)", report.encode(),
                               f"nexus_report_{ts_str}.txt", "text/plain", use_container_width=True)
            with st.expander("Preview report"):
                st.code(report, language="text")

    # ── Footer ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'font-size:9px;color:{C_MUTED};letter-spacing:1px;padding:4px 0">'
        f'<span>◈ NEXUS AUTONOMOUS AGENT · v{VERSION} · Streamlit + Plotly + APScheduler + SQLite</span>'
        f'<span>DB: {DB_PATH} · refresh: {REFRESH_SEC}s · records in view: {total_recs:,}</span>'
        f'</div>',
        unsafe_allow_html=True)

    # ── Auto-rerun ──────────────────────────────────────────────────────────
    if cfg.get("auto_refresh", True):
        rate = max(5, cfg.get("refresh_rate", REFRESH_SEC))
        time.sleep(0.8)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
