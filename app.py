# ═══════════════════════════════════════════════════════════════════
# WK Home Platform — Homeowner Dashboard
# Mobile-first Streamlit app. Each house gets a unique URL:
#   https://yourapp.streamlit.app/?house=8-edgewood-st
# No login required.
# ═══════════════════════════════════════════════════════════════════

import os
import requests
import streamlit as st
# import anthropic  # ← uncomment when you have a Claude API key
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

# ── Home Recommendation ─────────────────────────────────────────────
st.markdown('<p class="section-title" style="margin-top:1.25rem">💡 Recommendation</p>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────
# CLAUDE AI RECOMMENDATION (commented out until API key is ready)
# ─────────────────────────────────────────────────────────────────────
# To enable:
#   1. Get an API key at console.anthropic.com
#   2. Add  ANTHROPIC_API_KEY = "sk-ant-..."  to Streamlit Cloud Secrets
#   3. Run:  pip install anthropic  (add "anthropic" to requirements.txt)
#   4. Uncomment everything below marked  # AI:
#
# AI: @st.cache_data(ttl=1800)
# AI: def ai_recommendation(moisture, temp_f, humidity, trend_7d, rain_coming, weather_desc):
# AI:     """Call Claude Haiku to generate a plain-English home recommendation."""
# AI:     ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# AI:     if not ANTHROPIC_API_KEY:
# AI:         return None
# AI:     client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
# AI:     prompt = f"""You are a home structural health advisor. Given these sensor readings,
# AI: write ONE short paragraph (2-3 sentences) recommending what the homeowner should do.
# AI: Be specific and practical. No fluff.
# AI:
# AI: - Wood moisture: {moisture:.1f}% (healthy = below 19%, concerning = above 25%)
# AI: - Moisture trend (7-day): {trend_7d:+.1f}% change
# AI: - Indoor temperature: {temp_f:.0f}°F
# AI: - Indoor humidity: {humidity:.0f}%
# AI: - Current outside weather: {weather_desc}
# AI: - Rain in next 48h: {"Yes" if rain_coming else "No"}"""
# AI:     message = client.messages.create(
# AI:         model="claude-haiku-4-5",
# AI:         max_tokens=200,
# AI:         messages=[{"role": "user", "content": prompt}]
# AI:     )
# AI:     return message.content[0].text
#
# AI: # Compute 7-day moisture trend
# AI: trend_7d = 0.0
# AI: if len(df) > 1:
# AI:     week_ago = df[df["time"] >= df["time"].max() - pd.Timedelta(days=7)]
# AI:     if len(week_ago) > 1:
# AI:         trend_7d = week_ago["moisture"].iloc[-1] - week_ago["moisture"].iloc[0]
#
# AI: cur_weather_desc = WMO.get(weather.get("current", {}).get("weathercode", 0), "Unknown") if weather else "Unknown"
# AI: ai_rec = ai_recommendation(moisture, temp_f, humidity, trend_7d, rain_coming, cur_weather_desc)
# AI: if ai_rec:
# AI:     st.markdown(f"""
# AI: <div style="background:#f0f9ff;border:1px solid #bae6fd;border-left:4px solid #0ea5e9;
# AI:      border-radius:10px;padding:0.85rem 1rem;margin-bottom:1rem;display:flex;gap:10px;align-items:flex-start;">
# AI:   <span style="font-size:1.3rem">🤖</span>
# AI:   <div>
# AI:     <div style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:700;
# AI:          font-size:0.9rem;color:#0369a1;margin-bottom:3px">AI Advisor</div>
# AI:     <div style="font-size:0.8rem;color:#374151;line-height:1.4">{ai_rec}</div>
# AI:   </div>
# AI: </div>
# AI: """, unsafe_allow_html=True)
# ─────────────────────────────────────────────────────────────────────

# Build a smart recommendation from moisture + weather
rain_coming = False
if weather:
    hourly   = weather.get("hourly", {})
    times    = hourly.get("time", [])
    precip_p = hourly.get("precipitation_probability", [])
    from datetime import datetime, timedelta
    tomorrow = (datetime.now().date() + timedelta(days=1))
    next48 = [precip_p[i] for i, t in enumerate(times)
              if i < len(precip_p) and
              (t.startswith(str(datetime.now().date())) or t.startswith(str(tomorrow)))]
    rain_coming = any(p > 50 for p in next48)

if moisture >= ALERT:
    rec_icon = "🚨"
    rec_color = "#ef4444"
    rec_bg = "#fef2f2"
    rec_title = "Action Needed"
    rec_text = "Wood moisture is critically high. Check for leaks, standing water, or poor drainage near the foundation. Consider a dehumidifier."
elif moisture >= WARN and rain_coming:
    rec_icon = "⚠️"
    rec_color = "#f59e0b"
    rec_bg = "#fffbeb"
    rec_title = "Monitor Closely"
    rec_text = "Moisture is elevated and rain is in the forecast. Keep an eye on readings over the next 48 hours."
elif moisture >= WARN:
    rec_icon = "⚠️"
    rec_color = "#f59e0b"
    rec_bg = "#fffbeb"
    rec_title = "Elevated Moisture"
    rec_text = "Moisture is above normal. Ensure crawlspace vents are open and check for any new water intrusion."
elif rain_coming and moisture > 14:
    rec_icon = "🌧"
    rec_color = "#3b82f6"
    rec_bg = "#eff6ff"
    rec_title = "Rain Expected"
    rec_text = "Conditions are good now, but rain is coming. Check back after the storm to see if moisture levels rise."
else:
    rec_icon = "✅"
    rec_color = "#16a34a"
    rec_bg = "#f0fdf4"
    rec_title = "All Good"
    rec_text = "Moisture is in a healthy range and no major weather concerns. No action needed."

st.markdown(f"""
<div style="background:{rec_bg};border:1px solid {rec_color}44;border-left:4px solid {rec_color};
     border-radius:10px;padding:0.85rem 1rem;margin-bottom:1rem;display:flex;gap:10px;align-items:flex-start;">
  <span style="font-size:1.3rem;line-height:1.2">{rec_icon}</span>
  <div>
    <div style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:700;
         font-size:0.9rem;color:{rec_color};margin-bottom:3px">{rec_title}</div>
    <div style="font-size:0.8rem;color:#374151;line-height:1.4">{rec_text}</div>
  </div>
</div>
""", unsafe_allow_html=True)

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
