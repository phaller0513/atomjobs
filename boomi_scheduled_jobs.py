import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
import re
from collections import defaultdict
import pytz
import plotly.graph_objects as go

# ── helpers ──────────────────────────────────────────────────────────────────

def clear_cache():
    st.cache_data.clear()


def parse_cron_time_range(time_str):
    times = []
    if time_str == '*':
        return list(range(24))
    if '/' in time_str:
        base, step = time_str.split('/')
        step = int(step)
        if '-' in base:
            start, end = map(int, base.split('-'))
            times.extend(range(start, end + 1, step))
        else:
            times.append(int(base))
    elif '-' in time_str:
        start, end = map(int, time_str.split('-'))
        times.extend(range(start, end + 1))
    elif ',' in time_str:
        times.extend(map(int, time_str.split(',')))
    else:
        times.append(int(time_str))
    return times


def parse_job_schedule(job):
    try:
        hours_str = str(job.get('hours', '*'))
        minutes_str = str(job.get('minutes', '0'))
        hours = parse_cron_time_range(hours_str)
        minutes = parse_cron_time_range(minutes_str)
        return [(h, m) for h in hours for m in minutes]
    except Exception:
        return [(0, 0)]


def convert_utc_to_mst(hour, minute):
    utc_time = datetime(2025, 1, 1, hour, minute, tzinfo=pytz.UTC)
    mst_tz = pytz.timezone('US/Mountain')
    mst_time = utc_time.astimezone(mst_tz)
    return mst_time.hour, mst_time.minute


def format_time_12hour(hour, minute):
    period = "AM" if hour < 12 else "PM"
    display_hour = hour if hour <= 12 else hour - 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{minute:02d} {period}"


def is_job_enabled(job):
    val = job.get('enabled', False)
    if isinstance(val, str):
        return val.lower() == 'true'
    return bool(val)


def categorize_jobs(df):
    from collections import Counter
    name_counts = Counter(df['Name'])
    multi_instance_names = {name for name, count in name_counts.items() if count > 1}

    recurring, scheduled = [], []
    for _, job in df.iterrows():
        times = parse_job_schedule(job)
        hours_str = str(job.get('hours', '*'))
        minutes_str = str(job.get('minutes', '0'))
        is_freq = len(times) > 8 or '/' in hours_str or '/' in minutes_str
        is_multi = job['Name'] in multi_instance_names
        if is_freq or is_multi:
            recurring.append(job)
        else:
            scheduled.append(job)
    return recurring, scheduled


# ── statistics strip ─────────────────────────────────────────────────────────

def show_job_statistics(df):
    total = len(df)
    enabled = sum(1 for _, j in df.iterrows() if is_job_enabled(j))
    disabled = total - enabled
    recurring, scheduled = categorize_jobs(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Jobs", total)
    c2.metric("Enabled", enabled, delta=f"{enabled/total*100:.1f}%")
    c3.metric("Disabled", disabled, delta=f"{disabled/total*100:.1f}%")
    c4.metric("Recurring", len(recurring))
    c5.metric("Scheduled", len(scheduled))


# ── timeline tab ──────────────────────────────────────────────────────────────

def create_timeline_tab(scheduled_jobs):
    if not scheduled_jobs:
        st.info("No scheduled jobs found")
        return

    st.caption("All times in Mountain Standard Time (MST)")

    # Build per-hour buckets (MST)
    hour_buckets = defaultdict(list)   # hour → list of (minute, job)
    for job in scheduled_jobs:
        for utc_h, utc_m in parse_job_schedule(job):
            mst_h, mst_m = convert_utc_to_mst(utc_h, utc_m)
            hour_buckets[mst_h].append((mst_m, job))

    # ── density bar chart ────────────────────────────────────────────────────
    hours_24 = list(range(24))
    counts = [len(hour_buckets.get(h, [])) for h in hours_24]

    # Build hover text: list job names per hour
    hover_texts = []
    for h in hours_24:
        items = hour_buckets.get(h, [])
        if items:
            names = sorted(set(j['Name'] for _, j in items))
            label = f"<b>{format_time_12hour(h, 0).split(':')[0] + ('AM' if h < 12 else 'PM')}</b><br>"
            label += "<br>".join(f"{'●' if is_job_enabled(j) else '○'} {j['Name']}" for _, j in items)
        else:
            label = f"<b>{format_time_12hour(h, 0).split(':')[0] + ('AM' if h < 12 else 'PM')}</b><br>No jobs"
        hover_texts.append(label)

    hour_labels = [format_time_12hour(h, 0).replace(":00 ", "") for h in hours_24]

    fig = go.Figure(go.Bar(
        x=hour_labels,
        y=counts,
        marker_color=[
            "#2a78d6" if c > 0 else "#e2e8f0" for c in counts
        ],
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_texts,
    ))
    fig.update_layout(
        title="Jobs per Hour",
        xaxis_title=None,
        yaxis_title="# Jobs",
        height=260,
        margin=dict(l=40, r=20, t=40, b=40),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        bargap=0.15,
        xaxis=dict(tickfont=dict(size=11, color="#1a1a1a")),
        yaxis=dict(gridcolor="#f0f0f0", zeroline=False),
        hoverlabel=dict(bgcolor="white", font_size=12, font_color="#1a1a1a"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── hour-bucket expanders ────────────────────────────────────────────────
    st.markdown("#### Jobs by Hour")

    active_hours = sorted(h for h in hour_buckets if hour_buckets[h])

    for h in active_hours:
        items = sorted(hour_buckets[h], key=lambda x: x[0])  # sort by minute
        label = format_time_12hour(h, 0).replace(":00 ", " ") + "xx"
        n_enabled = sum(1 for _, j in items if is_job_enabled(j))
        n_disabled = len(items) - n_enabled

        status_pill = f"{n_enabled} enabled" if n_disabled == 0 else f"{n_enabled} on / {n_disabled} off"

        with st.expander(f"**{format_time_12hour(h, 0).replace(':00', '')}** — {len(items)} job{'s' if len(items) != 1 else ''}   {status_pill}"):
            # Group by exact minute within the hour
            minute_groups = defaultdict(list)
            for m, job in items:
                minute_groups[m].append(job)

            for m in sorted(minute_groups):
                time_label = format_time_12hour(h, m)
                jobs_at_minute = minute_groups[m]
                cols = st.columns([1] + [3] * min(len(jobs_at_minute), 4))
                cols[0].markdown(f"<span style='color:#888;font-size:0.85em'>{time_label}</span>", unsafe_allow_html=True)
                for idx, job in enumerate(jobs_at_minute):
                    color = "#22c55e" if is_job_enabled(job) else "#ef4444"
                    dot = f"<span style='display:inline-block;width:7px;height:7px;border-radius:50%;background:{color};vertical-align:middle;margin-right:4px'></span>"
                    name = job['Name']
                    col_idx = (idx % 4) + 1
                    cols[col_idx].markdown(
                        f"<span title='{name}' style='font-size:0.9em'>{dot}{name[:45]}{'…' if len(name) > 45 else ''}</span>",
                        unsafe_allow_html=True
                    )


# ── recurring tab ─────────────────────────────────────────────────────────────

def _last_minute(minutes_str):
    """Return the last minute fired in the given minutes cron expression."""
    minutes_str = str(minutes_str)
    if minutes_str == '*':
        return 59
    if '/' in minutes_str:
        base, step = minutes_str.split('/', 1)
        step = int(step)
        if '-' in base:
            start_m, end_m = map(int, base.split('-'))
        else:
            start_m, end_m = int(base), 59
        # Last value = largest multiple of step from start_m that doesn't exceed end_m
        vals = list(range(start_m, end_m + 1, step))
        return vals[-1] if vals else end_m
    if '-' in minutes_str:
        return int(minutes_str.split('-')[1])
    if ',' in minutes_str:
        return max(int(m) for m in minutes_str.split(','))
    return int(minutes_str)


def _first_minute(minutes_str):
    """Return the first minute fired in the given minutes cron expression."""
    minutes_str = str(minutes_str)
    if minutes_str == '*':
        return 0
    if '/' in minutes_str:
        base = minutes_str.split('/')[0]
        if '-' in base:
            return int(base.split('-')[0])
        return int(base)
    if '-' in minutes_str:
        return int(minutes_str.split('-')[0])
    if ',' in minutes_str:
        return min(int(m) for m in minutes_str.split(','))
    return int(minutes_str)


def parse_recurring_windows(job):
    """
    Return (windows, interval_minutes) where windows is a list of
    (start_mst_frac, end_mst_frac) in fractional hours [0,24).
    Start uses the first minute of the pattern; end uses the last minute.
    A UTC range that wraps midnight MST is split into two windows.
    """
    hours_str = str(job.get('hours', '*'))
    minutes_str = str(job.get('minutes', '0'))

    # Interval
    interval = 60
    if '/' in minutes_str:
        try:
            interval = int(minutes_str.split('/')[1])
        except (ValueError, IndexError):
            pass
    elif minutes_str == '*':
        interval = 1

    first_m = _first_minute(minutes_str)
    last_m = _last_minute(minutes_str)

    base_str = hours_str.split('/')[0]

    # Full-day patterns
    if base_str in ('*', '0-23'):
        return [(0 + first_m / 60, 23 + last_m / 60)], interval

    if '-' in base_str:
        parts = base_str.split('-')
        try:
            start_h, end_h = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            return [(0 + first_m / 60, 23 + last_m / 60)], interval
    else:
        try:
            start_h = end_h = int(base_str)
        except ValueError:
            return [(0 + first_m / 60, 23 + last_m / 60)], interval

    # start == end → full day
    if start_h == end_h:
        return [(0 + first_m / 60, 23 + last_m / 60)], interval

    start_mst, start_mst_m = convert_utc_to_mst(start_h, first_m)
    end_mst, end_mst_m = convert_utc_to_mst(end_h, last_m)

    start_frac = start_mst + start_mst_m / 60
    end_frac = end_mst + end_mst_m / 60

    if end_frac >= start_frac:
        return [(start_frac, end_frac)], interval
    else:
        # Wraps midnight: split into [start→24) and [0→end]
        return [(start_frac, 24.0), (0.0, end_frac)], interval




def create_recurring_tab(recurring_jobs):
    if not recurring_jobs:
        st.info("No recurring jobs found")
        return

    st.caption("All times in Mountain Standard Time (MST). Each spike marks an execution.")

    # Group by name.
    # Multi-instance jobs (same name, different UTC hours) get type="discrete" —
    # each instance contributes one spike at its exact time, no continuous baseline.
    # Frequency-based jobs (/ in hours/minutes) get type="continuous".
    from collections import Counter
    name_counts = Counter(j['Name'] for j in recurring_jobs)

    merged = {}
    for job in recurring_jobs:
        name = job['Name']
        enabled = is_job_enabled(job)
        hours_str = str(job.get('hours', '*'))
        minutes_str = str(job.get('minutes', '0'))
        is_freq = '/' in hours_str or '/' in minutes_str or str(hours_str) in ('*', '0-23')

        if name not in merged:
            job_type = "continuous" if is_freq else "discrete"
            merged[name] = {
                "name": name, "windows": [], "interval": 60,
                "enabled": enabled, "type": job_type,
                "discrete_times": [],
            }

        if is_freq:
            windows, interval = parse_recurring_windows(job)
            merged[name]["windows"].extend(windows)
            merged[name]["interval"] = interval
            merged[name]["type"] = "continuous"
        else:
            # Single scheduled instance — record its exact MST time as a spike point
            times = parse_job_schedule(job)
            for utc_h, utc_m in times:
                mst_h, mst_m = convert_utc_to_mst(utc_h, utc_m)
                merged[name]["discrete_times"].append(mst_h + mst_m / 60)

    rows = list(merged.values())
    for r in rows:
        if r["type"] == "continuous":
            r["interval_label"] = f"every {r['interval']}min" if r["interval"] > 1 else "every min"
            r["windows"].sort(key=lambda w: w[0])
            r["first_start"] = r["windows"][0][0]
        else:
            r["discrete_times"].sort()
            r["first_start"] = r["discrete_times"][0] if r["discrete_times"] else 0
            r["interval_label"] = f"{len(r['discrete_times'])}x/day"

    rows.sort(key=lambda r: (not r["enabled"], r["first_start"]))

    def _sparkline_svg(row, vw=600, h=20):
        """Inline SVG using viewBox so it stretches to fill column width."""
        parts = []
        for hr in range(0, 25, 6):
            x = int(hr / 24 * vw)
            parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#e5e7eb" stroke-width="1"/>')
        color = "#2a78d6" if row["enabled"] else "#e34948"
        if row["type"] == "continuous":
            for start_f, end_f in row["windows"]:
                x1 = int(start_f / 24 * vw)
                x2 = int(min(end_f, 24) / 24 * vw)
                bar_h = 6
                y = (h - bar_h) // 2
                parts.append(f'<rect x="{x1}" y="{y}" width="{max(4, x2-x1)}" height="{bar_h}" fill="{color}" rx="1"/>')
        else:
            for t in row["discrete_times"]:
                x = int(t / 24 * vw)
                parts.append(f'<line x1="{x}" y1="{h-12}" x2="{x}" y2="{h-4}" stroke="{color}" stroke-width="3"/>')
        return (
            f'<svg width="100%" height="{h}" viewBox="0 0 {vw} {h}" preserveAspectRatio="none"'
            f' style="display:block" xmlns="http://www.w3.org/2000/svg">'
            + "".join(parts) + "</svg>"
        )

    def _tooltip_text(row):
        if row["type"] == "continuous":
            window_strs = []
            for start_f, end_f in row["windows"]:
                sh = int(start_f); sm = int(round((start_f - sh) * 60))
                eh = int(end_f) % 24; em = int(round((end_f - int(end_f)) * 60))
                window_strs.append(f"{format_time_12hour(sh % 24, sm)} – {format_time_12hour(eh, em)}")
            total_execs = sum(
                max(1, int((e - s) * 60 / row["interval"]) + 1)
                for s, e in row["windows"]
            )
            return f"Window: {' | '.join(window_strs)} · {row['interval_label']} · ~{total_execs} executions/day"
        else:
            time_labels = [
                format_time_12hour(int(t) % 24, int(round((t - int(t)) * 60)))
                for t in row["discrete_times"]
            ]
            return f"Runs at: {', '.join(time_labels)}"

    hour_labels_html = "".join(
        f'<span style="position:absolute;left:{int(h/24*100)}%;font-size:9px;color:#9ca3af;transform:translateX(-50%)">'
        f'{"12A" if h==0 else "6A" if h==6 else "12P" if h==12 else "6P" if h==18 else ""}</span>'
        for h in [0, 6, 12, 18, 24]
    )

    rows_html = ""
    for row in rows:
        dot_color = "#22c55e" if row["enabled"] else "#ef4444"
        dot = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{dot_color};flex-shrink:0;margin-top:1px"></span>'
        tooltip = _tooltip_text(row).replace('"', '&quot;')
        sparkline = _sparkline_svg(row)
        rows_html += f"""
        <tr>
          <td style="padding:6px 10px;white-space:nowrap;width:1%">
            <div class="tt-wrap" data-tip="{tooltip}" style="display:flex;align-items:center;gap:6px">{dot}<span>{row['name']}</span></div>
          </td>
          <td style="padding:6px 10px">
            <div class="tt-wrap" data-tip="{tooltip}">
              <div style="position:relative">{sparkline}
                <div style="position:relative;height:12px">{hour_labels_html}</div>
              </div>
            </div>
          </td>
        </tr>"""

    html = f"""
    <style>
      .rec-wrap {{ background:#ffffff;border-radius:6px;padding:4px 0 }}
      .rec-table {{ width:100%;border-collapse:collapse;font-size:0.9em;color:#1a1a1a }}
      .rec-table thead th {{ padding:6px 10px;text-align:left;font-weight:600;
        color:#374151;border-bottom:2px solid #e5e7eb;white-space:nowrap;background:#ffffff }}
      .rec-table tbody td {{ color:#1a1a1a;background:#ffffff }}
      .rec-table tbody tr {{ border-bottom:1px solid #f3f4f6 }}
      .rec-table tbody tr:hover td {{ background:#e8f0fe }}

      /* CSS tooltip */
      .tt-wrap {{ position:relative;display:flex;align-items:center }}
      .tt-wrap::after {{
        content: attr(data-tip);
        position:absolute; bottom:calc(100% + 6px); left:0;
        background:#1e293b; color:#f8fafc;
        font-size:0.8em; line-height:1.4;
        padding:6px 10px; border-radius:5px;
        white-space:pre-wrap; max-width:380px; min-width:180px;
        box-shadow:0 2px 8px rgba(0,0,0,0.25);
        pointer-events:none; opacity:0; transition:opacity 0.15s;
        z-index:9999;
      }}
      .tt-wrap:hover::after {{ opacity:1 }}
    </style>
    <div class="rec-wrap">
    <table class="rec-table">
      <thead><tr>
        <th>Job</th>
        <th>24h Window (MST)</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>"""

    st.markdown(html, unsafe_allow_html=True)


# ── main fetch + layout ───────────────────────────────────────────────────────

def color_enabled(val):
    if isinstance(val, str):
        color = 'green' if val.lower() == 'true' else 'red'
    else:
        color = 'green' if val else 'red'
    return f'background-color: {color}'


@st.cache_data
def getJobs(atomId, label):
    r = requests.get('https://api.qa.trellis.arizona.edu/ws/rest/v1/util/getScheduledJobs/' + atomId)

    st.header(f"📋 {label}")

    if len(r.content) > 5:
        df = pd.DataFrame.from_dict(r.json())

        show_job_statistics(df)
        st.write("---")

        recurring_jobs, scheduled_jobs = categorize_jobs(df)

        tab1, tab2, tab3 = st.tabs(["📊 Timeline", "🔄 Recurring Jobs", "📋 Table"])

        with tab1:
            create_timeline_tab(scheduled_jobs)

        with tab2:
            create_recurring_tab(recurring_jobs)

        with tab3:
            st.subheader("Complete Job Table")
            st.dataframe(
                data=df.style.map(color_enabled, subset=['enabled']),
                column_order=('Name', 'enabled', 'id', 'hours', 'minutes', 'daysOfWeek', 'daysOfMonth', 'months', 'years', 'cron'),
                use_container_width=True,
                height=600,
            )
    else:
        st.warning('⚠️ No jobs scheduled')

    return df if len(r.content) > 5 else pd.DataFrame()


# ── app shell ─────────────────────────────────────────────────────────────────

VERSION = "2.3"

st.set_page_config(page_title="Boomi Job Scheduler", page_icon="⚙️", layout="wide")
st.title("⚙️ Boomi Scheduled Jobs Dashboard")
st.caption(f"v{VERSION}")
st.sidebar.title('🎛️ Environment Controls')

col1, col2 = st.sidebar.columns(2)
with col1:
    if st.sidebar.button('🏭 Production', type="primary", use_container_width=True):
        st.session_state.selected_env = 'prod'
with col2:
    if st.sidebar.button('🧪 QA', type="secondary", use_container_width=True):
        st.session_state.selected_env = 'qa'

if st.sidebar.button('🏖️ Sandbox', type="secondary", use_container_width=True):
    st.session_state.selected_env = 'sandbox'

st.sidebar.write("---")
if st.sidebar.button('🗑️ Clear Cache', help="Clear cached API responses"):
    clear_cache()
    st.sidebar.success("Cache cleared!")

with st.sidebar.expander("ℹ️ Help & Info"):
    st.write("""
    **Timeline**: Density bar chart + hour-bucket drill-down
    **Recurring Jobs**: Gantt chart of active time windows
    **Table**: Full raw job data

    **Status**:
    - ● Enabled
    - ○ Disabled

    Times shown in MST (UTC-7)
    """)

if 'selected_env' not in st.session_state:
    st.session_state.selected_env = None

if st.session_state.selected_env == 'prod':
    getJobs('3d78acc2-9f2b-41ff-bbfd-a3f2ed30c89e', 'Production Molecule')
elif st.session_state.selected_env == 'qa':
    getJobs('58e8640c-7dcd-44fc-8308-a1f0239fc789', 'QA Atom')
elif st.session_state.selected_env == 'sandbox':
    getJobs('4e7219c4-fb66-40b5-ab23-0a5c9a32b5b1', 'Sandbox Atom')
else:
    st.info("👆 Select an environment from the sidebar to view scheduled jobs")
    try:
        sample_df = pd.read_csv('boomijobschedule.csv')
        if not sample_df.empty:
            st.subheader("📄 Sample Data Preview")
            show_job_statistics(sample_df)
            with st.expander("View Sample Jobs"):
                st.dataframe(sample_df.head(10), use_container_width=True)
    except Exception:
        pass
