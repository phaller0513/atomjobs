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

def show_job_statistics(df, sidebar=False):
    total = len(df)
    enabled = sum(1 for _, j in df.iterrows() if is_job_enabled(j))
    disabled = total - enabled
    recurring, scheduled = categorize_jobs(df)

    if sidebar:
        st.sidebar.write("---")
        st.sidebar.markdown(f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:4px">
  <div style="background:#1a1f2e;border:1px solid #2d3550;border-radius:8px;padding:14px 8px;text-align:center">
    <div style="font-size:28px;font-weight:700;line-height:1">{total}</div>
    <div style="font-size:11px;color:#7a8aaa;margin-top:5px;text-transform:uppercase;letter-spacing:.05em">Total</div>
  </div>
  <div style="background:#0f2318;border:1px solid #1e4530;border-radius:8px;padding:14px 8px;text-align:center">
    <div style="font-size:28px;font-weight:700;line-height:1;color:#4CAF50">{enabled}</div>
    <div style="font-size:11px;color:#5a8a6a;margin-top:5px;text-transform:uppercase;letter-spacing:.05em">Enabled</div>
  </div>
  <div style="background:#1a1428;border:1px solid #302040;border-radius:8px;padding:14px 8px;text-align:center">
    <div style="font-size:28px;font-weight:700;line-height:1;color:#9b7fd4">{len(recurring)}</div>
    <div style="font-size:11px;color:#6a5a8a;margin-top:5px;text-transform:uppercase;letter-spacing:.05em">Recurring</div>
  </div>
  <div style="background:#1a1f2e;border:1px solid #2d3550;border-radius:8px;padding:14px 8px;text-align:center">
    <div style="font-size:28px;font-weight:700;line-height:1;color:#5ba3d9">{len(scheduled)}</div>
    <div style="font-size:11px;color:#4a6a8a;margin-top:5px;text-transform:uppercase;letter-spacing:.05em">Single</div>
  </div>
</div>
""", unsafe_allow_html=True)
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Jobs", total)
        c2.metric("Enabled", enabled, delta=f"{enabled/total*100:.1f}%")
        c3.metric("Disabled", disabled, delta=f"{disabled/total*100:.1f}%")
        c4.metric("Recurring", len(recurring))
        c5.metric("Scheduled", len(scheduled))


# ── timeline tab ──────────────────────────────────────────────────────────────

def _is_fullday_row(row):
    """Return True if a recurring row covers the full 24h window."""
    if row.get("kind") != "recurring":
        return False
    if row.get("type") == "discrete":
        return False
    windows = row.get("windows", [])
    total_span = sum(e - s for s, e in windows)
    return total_span >= 22


def _build_single_rows(scheduled_jobs):
    """Build table rows for single (non-recurring) jobs."""
    rows = []
    for job in scheduled_jobs:
        times = parse_job_schedule(job)
        mst_times = sorted(convert_utc_to_mst(h, m) for h, m in times)
        if not mst_times:
            continue
        first_h, first_m = mst_times[0]
        rows.append({
            "name": job["Name"],
            "enabled": is_job_enabled(job),
            "kind": "single",
            "mst_times": mst_times,
            "time_labels": [format_time_12hour(h, m) for h, m in mst_times],
            "sort_key": first_h + first_m / 60,
        })
    return rows


def _build_recurring_rows(recurring_jobs):
    """Build merged table rows for recurring jobs (same logic as create_recurring_tab)."""
    from collections import Counter
    merged = {}
    for job in recurring_jobs:
        name = job['Name']
        enabled = is_job_enabled(job)
        hours_str = str(job.get('hours', '*'))
        minutes_str = str(job.get('minutes', '0'))
        is_freq = '/' in hours_str or '/' in minutes_str or str(hours_str) in ('*', '0-23')

        if name not in merged:
            merged[name] = {
                "name": name, "windows": [], "interval": 60,
                "enabled": enabled, "type": "continuous" if is_freq else "discrete",
                "discrete_times": [], "kind": "recurring",
            }
        if is_freq:
            windows, interval = parse_recurring_windows(job)
            merged[name]["windows"].extend(windows)
            merged[name]["interval"] = interval
            merged[name]["type"] = "continuous"
        else:
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
        r["sort_key"] = r["first_start"]
    return rows


def _sparkline_for_row(row, vw=600, h=24):
    """Unified sparkline covering both single and recurring row types."""
    parts = []
    mid = h // 2
    for hr in range(0, 25, 6):
        x = int(hr / 24 * vw)
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#d1d5db" stroke-width="1"/>')
    for hr in [3, 9, 15, 21]:
        x = int(hr / 24 * vw)
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#d1d5db" stroke-width="1"/>')
    color = "#22c55e" if row["enabled"] else "#e34948"

    if row["kind"] == "single":
        for exec_h, exec_m in row["mst_times"]:
            t = exec_h + exec_m / 60
            x = int(t / 24 * vw)
            parts.append(f'<line x1="{x}" y1="{mid-8}" x2="{x}" y2="{mid+1}" stroke="{color}" stroke-width="2"/>')
    elif row["type"] == "discrete":
        for t in row["discrete_times"]:
            x = int(t / 24 * vw)
            parts.append(f'<line x1="{x}" y1="{mid-7}" x2="{x}" y2="{mid+1}" stroke="{color}" stroke-width="3"/>')
    else:
        use_ekg = row["interval"] >= 10
        for start_f, end_f in row["windows"]:
            x1 = int(start_f / 24 * vw)
            x2 = int(min(end_f, 24) / 24 * vw)
            if not use_ekg:
                bar_h = 6
                parts.append(f'<rect x="{x1}" y="{mid - bar_h//2}" width="{max(4, x2-x1)}" height="{bar_h}" fill="{color}" rx="1"/>')
            else:
                parts.append(f'<line x1="{x1}" y1="{mid}" x2="{x2}" y2="{mid}" stroke="{color}" stroke-width="1" opacity="0.35"/>')
                t = start_f
                while t <= end_f + 1e-9:
                    x = int(t / 24 * vw)
                    parts.append(f'<line x1="{x}" y1="{mid-8}" x2="{x}" y2="{mid+1}" stroke="{color}" stroke-width="2"/>')
                    t += row["interval"] / 60.0

    return (
        f'<svg width="100%" height="{h}" viewBox="0 0 {vw} {h}" preserveAspectRatio="none"'
        f' style="display:block" xmlns="http://www.w3.org/2000/svg">'
        + "".join(parts) + "</svg>"
    )


def _tooltip_for_row(row):
    if row["kind"] == "single":
        return "Runs at: " + ", ".join(row["time_labels"])
    if row["type"] == "continuous":
        window_strs = []
        for start_f, end_f in row["windows"]:
            sh = int(start_f); sm = int(round((start_f - sh) * 60))
            eh = int(end_f) % 24; em = int(round((end_f - int(end_f)) * 60))
            window_strs.append(f"{format_time_12hour(sh % 24, sm)} – {format_time_12hour(eh, em)}")
        total_execs = sum(max(1, int((e - s) * 60 / row["interval"]) + 1) for s, e in row["windows"])
        return f"Window: {' | '.join(window_strs)} · {row['interval_label']} · ~{total_execs} executions/day"
    time_labels = [format_time_12hour(int(t) % 24, int(round((t - int(t)) * 60))) for t in row["discrete_times"]]
    return f"Runs at: {', '.join(time_labels)}"


def _row_active_in_hour(row, hour):
    """Return True if this row has any activity during the given hour (0-23)."""
    if _is_fullday_row(row):
        return True
    if row["kind"] == "single":
        return any(h == hour for h, m in row["mst_times"])
    if row["type"] == "discrete":
        return any(int(t) % 24 == hour for t in row["discrete_times"])
    # continuous windowed — check if any window overlaps [hour, hour+1)
    for start_f, end_f in row["windows"]:
        if start_f < hour + 1 and end_f > hour:
            return True
    return False


def create_combined_tab(scheduled_jobs, recurring_jobs):
    caption_placeholder = st.empty()

    single_rows = _build_single_rows(scheduled_jobs)
    recurring_rows = _build_recurring_rows(recurring_jobs)

    fullday = [r for r in recurring_rows if _is_fullday_row(r)]
    windowed = [r for r in recurring_rows if not _is_fullday_row(r)]
    interleaved = sorted(single_rows + windowed, key=lambda r: r["sort_key"])

    # 26-stop time slider — same as Single Jobs tab
    _fmt = ["All","12A","1A","2A","3A","4A","5A","6A","7A","8A","9A","10A","11A",
            "12P","1P","2P","3P","4P","5P","6P","7P","8P","9P","10P","11P","12A"]
    selected = st.select_slider(
        "Jump to hour", options=list(range(26)), value=0,
        format_func=lambda i: _fmt[i], label_visibility="collapsed", key="slider_combined",
    )

    if selected != 0:
        target = (selected - 1) % 24
        end_label = _fmt[(target + 1) % 24 + 1]
        caption_placeholder.caption(f"All times in Mountain Standard Time (MST). Currently displaying {_fmt[target + 1]} – {end_label}")
        filtered_interleaved = [r for r in interleaved if _row_active_in_hour(r, target)]
        visible_rows = filtered_interleaved + fullday
        if not visible_rows:
            st.info(f"No jobs run at {_fmt[selected]}")
            return
        _render_job_table(
            visible_rows, _sparkline_for_row, _tooltip_for_row,
            divider_before=len(filtered_interleaved) if fullday else None,
        )
    else:
        caption_placeholder.caption("All times in Mountain Standard Time (MST)")
        _render_job_table(
            interleaved + fullday, _sparkline_for_row, _tooltip_for_row,
            divider_before=len(interleaved) if fullday else None,
        )


# ── shared table renderer ────────────────────────────────────────────────────

def _render_job_table(rows, sparkline_fn, tooltip_fn, divider_before=None):
    _hr_labels = {0:"12A", 3:"3A", 6:"6A", 9:"9A", 12:"12P", 15:"3P", 18:"6P", 21:"9P", 24:""}
    hour_labels_html = "".join(
        f'<span style="position:absolute;left:{int(h/24*100)}%;font-size:9px;color:#9ca3af;transform:translateX(-50%)">'
        f'{_hr_labels[h]}</span>'
        for h in [0, 3, 6, 9, 12, 15, 18, 21]
    )
    rows_html = ""
    for idx, row in enumerate(rows):
        if divider_before is not None and idx == divider_before:
            rows_html += '<tr><td colspan="2" style="padding:6px 10px;color:#9ca3af;font-size:0.8em;font-style:italic;background:#ffffff;border-top:2px solid #e5e7eb">24-hour recurring jobs</td></tr>'
        dot_color = "#22c55e" if row["enabled"] else "#ef4444"
        dot = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{dot_color};flex-shrink:0;margin-top:1px"></span>'
        tooltip = tooltip_fn(row).replace('"', '&quot;')
        sparkline = sparkline_fn(row)
        rows_html += f"""
        <tr>
          <td style="padding:6px 10px;white-space:nowrap;width:1%">
            <div class="tt-wrap" data-tip="{tooltip}" style="display:flex;align-items:center;gap:6px">{dot}<span>{row['name']}</span></div>
          </td>
          <td style="padding:6px 10px">
            <div class="tt-wrap" data-tip="{tooltip}">
              <div style="position:relative;width:100%">{sparkline}
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
      .tt-wrap {{ position:relative;display:flex;align-items:center;width:100% }}
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
      <thead><tr><th>Job</th><th>Time (MST)</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>"""
    st.markdown(html, unsafe_allow_html=True)


# ── single jobs tab ───────────────────────────────────────────────────────────

def create_single_jobs_tab(scheduled_jobs):
    if not scheduled_jobs:
        st.info("No scheduled jobs found")
        return
    caption_placeholder = st.empty()
    caption_placeholder.caption("All times in Mountain Standard Time (MST)")
    rows = sorted(_build_single_rows(scheduled_jobs), key=lambda r: r["sort_key"])

    # 26-stop time filter slider
    _fmt = ["All","12A","1A","2A","3A","4A","5A","6A","7A","8A","9A","10A","11A",
            "12P","1P","2P","3P","4P","5P","6P","7P","8P","9P","10P","11P","12A"]
    selected = st.select_slider(
        "Jump to hour", options=list(range(26)), value=0,
        format_func=lambda i: _fmt[i], label_visibility="collapsed", key="slider_single",
    )
    if selected != 0:
        target = (selected - 1) % 24
        end_label = _fmt[(target + 1) % 24 + 1]
        caption_placeholder.caption(f"All times in Mountain Standard Time (MST). Currently displaying {_fmt[target + 1]} – {end_label}")
        rows = [r for r in rows if any(h == target for h, m in r["mst_times"])]
        if not rows:
            st.info(f"No jobs run at {_fmt[selected]}")
            return

    _render_job_table(rows, _sparkline_for_row, _tooltip_for_row)


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
    rows = _build_recurring_rows(recurring_jobs)
    rows.sort(key=lambda r: (not r["enabled"], r["first_start"]))
    _render_job_table(rows, _sparkline_for_row, _tooltip_for_row)


# ── main fetch + layout ───────────────────────────────────────────────────────

def color_enabled(val):
    if isinstance(val, str):
        color = 'green' if val.lower() == 'true' else 'red'
    else:
        color = 'green' if val else 'red'
    return f'background-color: {color}'


@st.cache_data
def fetchJobs(atomId):
    r = requests.get('https://api.qa.trellis.arizona.edu/ws/rest/v1/util/getScheduledJobs/' + atomId)
    if len(r.content) > 5:
        return pd.DataFrame.from_dict(r.json())
    return pd.DataFrame()


def renderJobs(df, label):
    if df.empty:
        st.warning('No jobs scheduled')
        return

    show_job_statistics(df, sidebar=True)

    recurring_jobs, scheduled_jobs = categorize_jobs(df)

    tab1, tab2, tab3, tab4 = st.tabs(["All Jobs", "Single Jobs", "Recurring Jobs", "Table"])

    with tab1:
        create_combined_tab(scheduled_jobs, recurring_jobs)

    with tab2:
        create_single_jobs_tab(scheduled_jobs)

    with tab3:
        create_recurring_tab(recurring_jobs)

    with tab4:
        st.subheader("Complete Job Table")
        st.dataframe(
            data=df.style.map(color_enabled, subset=['enabled']),
            column_order=('Name', 'enabled', 'id', 'hours', 'minutes', 'daysOfWeek', 'daysOfMonth', 'months', 'years', 'cron'),
            use_container_width=True,
            height=600,
        )


# ── app shell ─────────────────────────────────────────────────────────────────

VERSION = "4.6"

st.set_page_config(page_title="Boomi Job Scheduler", layout="wide")

ENV_OPTIONS = {
    'Production': ('3d78acc2-9f2b-41ff-bbfd-a3f2ed30c89e', 'Production Molecule'),
    'QA':         ('58e8640c-7dcd-44fc-8308-a1f0239fc789', 'QA Atom'),
    'Sandbox':    ('4e7219c4-fb66-40b5-ab23-0a5c9a32b5b1', 'Sandbox Atom'),
}

st.sidebar.markdown("""
<style>
div[data-testid="stRadio"] label p { font-size: 20px !important; }
div[data-testid="stRadio"] label { padding: 6px 0 !important; }
section[data-testid="stSidebar"] > div { padding-bottom: 80px; }
.sidebar-footer {
    position: fixed;
    bottom: 0;
    left: 0;
    width: 244px;
    background: #0e1117;
    border-top: 1px solid #262730;
    padding: 10px 16px;
    z-index: 999;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
</style>
""", unsafe_allow_html=True)

st.sidebar.markdown("<div style='font-size:13px;font-weight:600;margin-bottom:6px'>Environment</div>", unsafe_allow_html=True)

selected_env = st.sidebar.radio(
    "Environment",
    options=list(ENV_OPTIONS.keys()),
    index=None,
    label_visibility="collapsed",
)

with st.sidebar.expander("Help & Info"):
    st.write("""
**All Jobs**: Combined view of all scheduled jobs

**Single Jobs**: One-time scheduled runs

**Recurring Jobs**: Jobs on repeating schedules

**Table**: Full raw job data

**Status**: Enabled = green, Disabled = red

Times shown in MST (UTC-7)
""")

if st.sidebar.button('↺', help="Clear cached API responses", key="clear_cache_btn"):
    clear_cache()
    st.sidebar.success("Cache cleared!")

st.sidebar.markdown(f"""
<div class="sidebar-footer">
    <span style="font-size:12px;color:#666">v{VERSION}</span>
    <span style="font-size:12px;color:#555">Boomi Job Scheduler</span>
</div>
""", unsafe_allow_html=True)

if selected_env:
    atom_id, label = ENV_OPTIONS[selected_env]
    st.title(f"Boomi Scheduled Jobs - {selected_env}")
    renderJobs(fetchJobs(atom_id), label)
else:
    st.title("Boomi Scheduled Jobs")
    st.info("Select an environment from the sidebar to view scheduled jobs")
