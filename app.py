# ── House Notes ──────────────────────────────────────────────────────
st.markdown('<p class="section-title">House Notes</p>', unsafe_allow_html=True)

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
      |> limit(n: 10)
    """
    try:
        df = query_api.query_data_frame(flux)
    except Exception:
        return []
    finally:
        client.close()
    if df.empty:
        return []
    df["_time"] = pd.to_datetime(df["_time"]).dt.tz_localize(None)
    return list(zip(df["_time"], df["_value"]))

notes = fetch_notes(device_tag)

for ts, text in notes:
    st.markdown(f"""
    <div style="background:white;border:1px solid #e5e7eb;border-radius:8px;
                padding:0.6rem 0.75rem;margin-bottom:0.5rem;">
      <div style="font-size:0.65rem;color:#9ca3af;margin-bottom:3px;">
        {ts.strftime("%-I:%M %p · %b %-d, %Y")}
      </div>
      <div style="font-size:0.85rem;color:#111827;">{text}</div>
    </div>
    """, unsafe_allow_html=True)

with st.form("note_form", clear_on_submit=True):
    new_note = st.text_area("Add a note", placeholder="e.g. Checked crawlspace — looked dry.", height=80, label_visibility="collapsed")
    if st.form_submit_button("Save Note"):
        if new_note.strip():
            client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = client.write_api(write_options=SYNCHRONOUS)
            point = Point("notes").tag("device", device_tag).field("text", new_note.strip())
            write_api.write(bucket=INFLUX_BUCKET, record=point)
            client.close()
            st.cache_data.clear()
            st.rerun()
