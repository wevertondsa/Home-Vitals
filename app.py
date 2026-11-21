# ═══════════════════════════════════════════════════════════════════
# WK Home Platform — Homeowner Dashboard
# Mobile-first Streamlit app. Each house gets a unique URL:
#   https://yourapp.streamlit.app/?house=8-edgewood-st
# No login required.
# ═══════════════════════════════════════════════════════════════════

import os
import requests
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

st.set_page_config(
    page_title="HomeVitals",
    page_icon="🏠",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── House registry ───────────────────────────────────────────────────
HOUSES = {
    "8-edgewood-st": ("8 Edgewood St, Stafford Springs CT", "moisture-sensor-01", (41.9609, -72.2954)),
}

# ── InfluxDB connection ──────────────────────────────────────────────
INFLUX_URL    = "https://us-east-1-1.aws.cloud2.influxdata.com"
INFLUX_TOKEN  = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG    = "87f00952168d1b5d"
INFLUX_BUCKET = "Sensors V1"

# ── Moisture thresholds (%) ──────────────────────────────────────────
WARN  = 19
ALERT = 25

# ── WMO weather code descriptions ───────────────────────────────────
WMO = {
    0:"Clear sky", 1:"Mainly clear", 2:"Partly cloudy", 3:"Overcast",
    45:"Foggy", 48:"Icy fog", 51:"Light drizzle", 53:"Drizzle",
    55:"Heavy drizzle", 61:"Light rain", 63:"Rain", 65:"Heavy rain",
    71:"Light snow", 73:"Snow", 75:"Heavy snow", 77:"Snow grains",
    80:"Rain showers", 81:"Heavy showers", 82:"Violent showers",
    85:"Snow showers", 86:"Heavy snow showers",
    95:"Thunderstorm", 96:"Thunderstorm + hail", 99:"Severe thunderstorm",
}
SEVERE = {65, 75, 80, 81, 82, 85, 86, 95, 96, 99}

# ════════════════════════════════════════════════════════════════════
# Routing
# ════════════════════════════════════════════════════════════════════
house_id = st.query_params.get("house", "")

if not house_id:
    st.title("WK Home Platform")
    st.info("Your link should look like: `?house=8-edgewood-st`\nContact us if you think something is wrong.")
    st.stop()

if house_id not in HOUSES:
    st.error("House not found. Check the link in your text message.")
    st.stop()

address, device_tag, (lat, lon) = HOUSES[house_id]

# ════════════════════════════════════════════════════════════════════
# Data fetching
# ════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800)
def fetch_data(device: str, days: int = 30) -> pd.DataFrame:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    flux = f"""
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "sensors")
      |> filter(fn: (r) => r.device == "{device}")
      |> filter(fn: (r) =>
            r._field == "moisture" or
            r._field == "temperature" or
            r._field == "humidity")
      |> aggregateWindow(every: 30m, fn: mean, createEmpty: false)
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    """

    try:
        df = query_api.query_data_frame(flux)
    except Exception as e:
        st.error(f"Could not reach database: {e}")
        st.stop()
    finally:
        client.close()

    if df.empty:
        return pd.DataFrame()

    df = df[["_time", "moisture", "temperature", "humidity"]].copy()
    df.rename(columns={"_time": "time"}, inplace=True)
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df["temperature"] = df["temperature"] * 9 / 5 + 32
    return df.sort_values("time").reset_index(drop=True)


@st.cache_data(ttl=60)
def fetch_notes(device: str):
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()
    flux = f"""
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -365d)
      |> filter(fn: (r) => r._measurement == "notes")
      |> filter(fn: (r) => r.device == "{device}")
      |> filter(fn: (r) => r._field == "text")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: 20)
    """
    try:
        df = query_api.query_data_frame(flux)
        client.close()
        if df.empty:
            return []
        return list(zip(
            pd.to_datetime(df["_time"]).dt.tz_localize(None),
            df["_value"].astype(str)
        ))
    except Exception:
        client.close()
        return []


@st.cache_data(ttl=3600)
def fetch_weather(lat: float, lon: float):
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,weathercode,windspeed_10m,precipitation"
        f"&hourly=precipitation_probability,weathercode"
        f"&forecast_days=3&temperature_unit=fahrenheit&windspeed_unit=mph"
    )
    try:
        r = requests.get(url, timeout=5)
        return r.json()
    except Exception:
        return None


df = fetch_data(device_tag)

if df.empty:
    st.title(f"🏠 {address}")
    st.warning("No readings yet — sensor may still be setting up. Check back soon.")
    st.stop()

# ════════════════════════════════════════════════════════════════════
# Status from latest reading
# ════════════════════════════════════════════════════════════════════
latest   = df.iloc[-1]
moisture = latest["moisture"]
temp_f   = latest["temperature"]
humidity = latest["humidity"]
last_time = latest["time"]

if moisture >= ALERT:
    emoji, label, color, bg = "🚨", "Attention Needed", "#ef4444", "#fef2f2"
elif moisture >= WARN:
    emoji, label, color, bg = "⚠️", "Elevated — Monitor", "#f59e0b", "#fffbeb"
else:
    emoji, label, color, bg = "✅", "All Clear", "#16a34a", "#f0fdf4"

# ════════════════════════════════════════════════════════════════════
# Fetch weather (used on main page below)
# ════════════════════════════════════════════════════════════════════
weather = fetch_weather(lat, lon)

# ════════════════════════════════════════════════════════════════════
# Styles
# ════════════════════════════════════════════════════════════════════
st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@600;700&display=swap');

  .block-container {{
    padding-top: 1.5rem !important;
    max-width: 480px;
    margin: auto;
    font-family: 'Inter', system-ui, sans-serif;
  }}

  .hv-brand {{
    font-family: 'Space Grotesk', system-ui, sans-serif;
    font-size: 1rem;
    font-weight: 700;
    color: #111827;
    margin-bottom: 0.15rem;
  }}
  .hv-brand span {{ color: #3b82f6; }}

  .address-label {{
    font-size: 0.875rem;
    color: #6b7280;
    margin-bottom: 0.75rem;
  }}

  .status-card {{
    background: {bg};
    border: 1px solid {color}44;
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .status-label {{
    font-family: 'Space Grotesk', system-ui, sans-serif;
    font-size: 1rem;
    font-weight: 700;
    color: {color};
  }}
  .status-sub {{
    font-size: 0.75rem;
    color: #6b7280;
    margin-top: 2px;
  }}

  .metric-row {{
    display: flex;
    gap: 8px;
    margin-bottom: 1rem;
  }}
  .metric-card {{
    flex: 1;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 0.75rem 0.5rem;
    text-align: center;
  }}
  .metric-value {{
    font-family: 'Space Grotesk', system-ui, sans-serif;
    font-size: 1.5rem;
    font-weight: 700;
    color: #111827;
    line-height: 1.1;
  }}
  .metric-unit {{ font-size: 0.875rem; }}
  .metric-label {{
    font-size: 0.65rem;
    color: #9ca3af;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }}

  .section-title {{
    font-size: 0.7rem;
    font-weight: 600;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 0.4rem;
    margin-top: 0.75rem;
  }}

  .threshold-row {{
    display: flex;
    gap: 8px;
    margin-top: 4px;
    margin-bottom: 1rem;
  }}
  .threshold-pill {{
    font-size: 0.7rem;
    padding: 2px 8px;
    border-radius: 20px;
    font-weight: 500;
  }}
  .pill-warn  {{ background: #fffbeb; color: #d97706; border: 1px solid #fde68a; }}
  .pill-alert {{ background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }}

  .sensor-row {{
    display: flex;
    justify-content: space-between;
    font-size: 0.72rem;
    color: #9ca3af;
    padding: 0.6rem 0.75rem;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    margin-top: 0.5rem;
  }}
  .sensor-dot {{
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: #16a34a;
    margin-right: 5px;
    vertical-align: middle;
  }}

  .last-seen {{
    font-size: 0.72rem;
    color: #9ca3af;
    text-align: right;
    margin-bottom: 0.75rem;
  }}

  .note-item {{
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 0.6rem 0.75rem;
    margin-bottom: 0.4rem;
    font-size: 0.82rem;
    color: #374151;
  }}
  .note-time {{
    font-size: 0.65rem;
    color: #9ca3af;
    margin-bottom: 2px;
  }}

  footer {{ visibility: hidden; }}
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════
# Render — main content
# ════════════════════════════════════════════════════════════════════
time_str = last_time.strftime("%-I:%M %p · %b %-d")

st.markdown('<div class="hv-brand">Home<span>Vitals</span></div>', unsafe_allow_html=True)
st.markdown(f'<div class="last-seen">Updated {time_str}</div>', unsafe_allow_html=True)
st.markdown(f'<p class="address-label">📍 {address}</p>', unsafe_allow_html=True)

# Status card
st.markdown(f"""
<div class="status-card">
  <span style="font-size:1.3rem">{emoji}</span>
  <div>
    <div class="status-label">{label}</div>
    <div class="status-sub">Wood moisture within {'safe' if moisture < WARN else 'elevated'} range</div>
  </div>
</div>
""", unsafe_allow_html=True)

# Three metric tiles
st.markdown(f"""
<div class="metric-row">
  <div class="metric-card">
    <div class="metric-value">{moisture:.1f}<span class="metric-unit">%</span></div>
    <div class="metric-label">Wood Moisture</div>
  </div>
  <div class="metric-card">
    <div class="metric-value">{temp_f:.0f}<span class="metric-unit">°F</span></div>
    <div class="metric-label">Temperature</div>
  </div>
  <div class="metric-card">
    <div class="metric-value">{humidity:.0f}<span class="metric-unit">%</span></div>
    <div class="metric-label">Humidity</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── 30-day moisture chart ────────────────────────────────────────────
st.markdown('<p class="section-title">30-day moisture trend</p>', unsafe_allow_html=True)

fig_m = go.Figure()
fig_m.add_hrect(y0=WARN,  y1=ALERT, fillcolor="rgba(253,230,138,0.25)", line_width=0)
fig_m.add_hrect(y0=ALERT, y1=35,    fillcolor="rgba(252,165,165,0.25)", line_width=0)
fig_m.add_trace(go.Scatter(
    x=df["time"], y=df["moisture"],
    mode="lines",
    line=dict(color="#3b82f6", width=2),
    fill="tozeroy",
    fillcolor="rgba(59,130,246,0.08)",
    hovertemplate="%{y:.1f}%<extra></extra>",
))
fig_m.add_hline(y=WARN,  line_dash="dot", line_color="#f59e0b", line_width=1.5)
fig_m.add_hline(y=ALERT, line_dash="dot", line_color="#ef4444", line_width=1.5)
fig_m.update_layout(
    height=170,
    margin=dict(l=0, r=4, t=4, b=0),
    showlegend=False,
    plot_bgcolor="white",
    paper_bgcolor="white",
    xaxis=dict(showgrid=False, tickformat="%b %-d", tickfont=dict(size=10, color="#9ca3af")),
    yaxis=dict(range=[0, 35], ticksuffix="%", showgrid=True,
               gridcolor="#f3f4f6", tickfont=dict(size=10, color="#9ca3af")),
)
st.plotly_chart(fig_m, use_container_width=True, config={"displayModeBar": False})

st.markdown("""
<div class="threshold-row">
  <span class="threshold-pill pill-warn">⚠ Watch at 19%</span>
  <span class="threshold-pill pill-alert">🚨 Act at 25%</span>
</div>
""", unsafe_allow_html=True)

# ── Temp + Humidity chart ────────────────────────────────────────────
st.markdown('<p class="section-title">Temperature &amp; Humidity</p>', unsafe_allow_html=True)

fig_e = make_subplots(specs=[[{"secondary_y": True}]])
fig_e.add_trace(go.Scatter(
    x=df["time"], y=df["temperature"],
    name="Temp °F",
    line=dict(color="#f97316", width=2),
    hovertemplate="%{y:.0f}°F<extra>Temp</extra>",
), secondary_y=False)
fig_e.add_trace(go.Scatter(
    x=df["time"], y=df["humidity"],
    name="Humidity %",
    line=dict(color="#8b5cf6", width=2),
    hovertemplate="%{y:.0f}%<extra>Humidity</extra>",
), secondary_y=True)
fig_e.update_layout(
    height=170,
    margin=dict(l=0, r=4, t=4, b=0),
    showlegend=False,
    plot_bgcolor="white",
    paper_bgcolor="white",
    xaxis=dict(showgrid=False, tickformat="%b %-d", tickfont=dict(size=10, color="#9ca3af")),
)
fig_e.update_yaxes(ticksuffix="°", tickfont=dict(size=10, color="#f97316"),
                   showgrid=True, gridcolor="#f3f4f6", secondary_y=False)
fig_e.update_yaxes(ticksuffix="%", tickfont=dict(size=10, color="#8b5cf6"),
                   showgrid=False, secondary_y=True)
st.plotly_chart(fig_e, use_container_width=True, config={"displayModeBar": False})

# ── Sensor status row ────────────────────────────────────────────────
st.markdown(f"""
<div class="sensor-row">
  <span><span class="sensor-dot"></span>{device_tag} · online</span>
  <span>Posts every 30 min</span>
</div>
""", unsafe_allow_html=True)

# ── Weather section ─────────────────────────────────────────────────
st.markdown('<p class="section-title" style="margin-top:1.25rem">🌤 Local Weather</p>', unsafe_allow_html=True)

if weather:
    from datetime import datetime, timedelta
    cur      = weather.get("current", {})
    cur_temp = cur.get("temperature_2m", "—")
    cur_code = cur.get("weathercode", 0)
    cur_wind = cur.get("windspeed_10m", "—")
    cur_desc = WMO.get(cur_code, "Unknown")
    is_severe = cur_code in SEVERE

    if is_severe:
        st.error(f"⚠️ **Weather Alert: {cur_desc}**")

    st.markdown(f"""
<div class="metric-row">
  <div class="metric-card">
    <div class="metric-value">{cur_temp}<span class="metric-unit">°F</span></div>
    <div class="metric-label">Outside Temp</div>
  </div>
  <div class="metric-card" style="flex:2">
    <div class="metric-value" style="font-size:1rem;padding-top:4px">{cur_desc}</div>
    <div class="metric-label">Conditions · {cur_wind} mph wind</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # 3-day outlook
    hourly   = weather.get("hourly", {})
    times    = hourly.get("time", [])
    precip_p = hourly.get("precipitation_probability", [])
    wmo_h    = hourly.get("weathercode", [])
    today    = datetime.now().date()

    day_cards = ""
    for d_offset in range(3):
        day = today + timedelta(days=d_offset)
        day_label = "Today" if d_offset == 0 else day.strftime("%a %-d")
        day_probs = [precip_p[i] for i, t in enumerate(times) if t.startswith(str(day)) and i < len(precip_p)]
        day_codes = [wmo_h[i]    for i, t in enumerate(times) if t.startswith(str(day)) and i < len(wmo_h)]
        max_prob  = max(day_probs) if day_probs else 0
        day_code  = max(set(day_codes), key=day_codes.count) if day_codes else 0
        day_desc  = WMO.get(day_code, "")
        rain_icon = "🌧" if max_prob > 50 else ("🌦" if max_prob > 20 else "☀️")
        day_cards += f"""
  <div class="metric-card">
    <div style="font-size:1.1rem">{rain_icon}</div>
    <div class="metric-label" style="font-size:0.68rem;color:#374151;margin-top:4px">{day_label}</div>
    <div class="metric-label">{day_desc}</div>
    <div class="metric-label">💧 {max_prob}%</div>
  </div>"""

    st.markdown(f'<div class="metric-row">{day_cards}</div>', unsafe_allow_html=True)
else:
    st.markdown('<p style="font-size:0.8rem;color:#9ca3af;">Weather unavailable.</p>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════
# House Notes
# ════════════════════════════════════════════════════════════════════
st.markdown('<p class="section-title" style="margin-top:1.5rem">📝 House Notes</p>', unsafe_allow_html=True)

notes = fetch_notes(device_tag)
if notes:
    for note_time, note_text in notes:
        time_label = note_time.strftime("%-I:%M %p · %b %-d, %Y")
        st.markdown(f"""
<div class="note-item">
  <div class="note-time">{time_label}</div>
  {note_text}
</div>
""", unsafe_allow_html=True)
else:
    st.markdown('<p style="font-size:0.8rem;color:#9ca3af;">No notes yet.</p>', unsafe_allow_html=True)

with st.form("note_form", clear_on_submit=True):
    new_note = st.text_area("Add a note", placeholder="e.g. Plumber visited, checked crawlspace — looked dry.", height=80)
    submitted = st.form_submit_button("Save Note")
    if submitted and new_note.strip():
        try:
            client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = client.write_api(write_options=SYNCHRONOUS)
            point = (
                Point("notes")
                .tag("device", device_tag)
                .field("text", new_note.strip())
            )
            write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
            client.close()
            st.success("Note saved!")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"Could not save note: {e}")
