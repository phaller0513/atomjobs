# Boomi Scheduled Jobs Dashboard

Streamlit app for viewing and exploring scheduled jobs across Boomi environments.

## Setup

Install dependencies:

```bash
pip install streamlit pandas requests pytz plotly
```

## Configuration

Create `.streamlit/secrets.toml` (excluded from version control):

```toml
[api]
base_url = "https://your-api-host/ws/rest/v1/util/getScheduledJobs"

[timezones]
runtime_tz = "UTC"         # timezone the API returns job times in
display_tz = "US/Mountain" # timezone shown in the UI

[environments."Production Molecule"]
environment_id = "your-environment-id"

[environments."QA Atom"]
environment_id = "your-environment-id"

[environments."Sandbox Atom"]
environment_id = "your-environment-id"
```

Add or remove `[environments.*]` blocks to change which environments appear in the sidebar. Timezone values must be valid [pytz](https://pypi.org/project/pytz/) zone names (e.g. `"UTC"`, `"US/Mountain"`, `"America/Phoenix"`).

## Running

```bash
streamlit run boomi_scheduled_jobs.py
```
