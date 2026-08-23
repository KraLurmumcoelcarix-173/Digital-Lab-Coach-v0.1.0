# Release & Course-Deployment Guide (v0.1.0)

The operational runbook for shipping DLC to a class. Written for the
instructor/maintainer teaching undergraduate with Digital; students never need to read this file.

## What you are building

Three pieces, in this order:

1. **A release zip** (`DigitalLabCoach.zip`, built by a script — no
   tests or dev clutter, sample circuits and course data included),
   published on GitHub Releases. The README's green **Download** button
   always points at the latest one. Students: download → unzip →
   double-click `START_HERE` → point at `Digital.jar` → paste the course
   URL + token in Settings → work. Windows uses `START_HERE.bat`,
   macOS/Linux uses `./start.sh` — both are in the zip.
2. **The course proxy** — a small server holding YOUR API key, with
   per-machine daily limits (re-download-proof), a whole-server daily
   circuit breaker, telemetry ingest, and the admin dashboard.
3. **Two secrets**: the *course token* students paste once, and the
   *admin token* only you hold (it opens the dashboard).

Prerequisites: your fork is pushed and green (`uv run pytest -q`), the
four README screenshots are in `docs/screenshots/`, and you have ~30
minutes. The personal step-by-step checklist for the v0.1.0 launch:
[RELEASE_TODO.md](RELEASE_TODO.md).

## 1. Build the zip and cut the GitHub release

1. Make sure `master` is green: `uv run pytest -q`.
2. Build the student zip:
   ```bash
   uv run python scripts/make_release_zip.py
   ```
   **Expect:** `wrote dist/DigitalLabCoach.zip (…MB, … files, top folder
   DigitalLabCoach-0.1.0/)`. Spot-check: unzip it somewhere and confirm
   there is no `tests/` folder inside and `START_HERE.bat` is at the top
   level.
3. Tag and push:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
4. On GitHub: **Releases → Draft a new release** → choose tag `v0.1.0`,
   title `Digital Lab Coach v0.1.0`, paste the release notes, and
   **attach `dist/DigitalLabCoach.zip` as a release asset** (drag it
   into the assets box). Keep the filename exactly `DigitalLabCoach.zip`
   — the README Download button URL
   (`…/releases/latest/download/DigitalLabCoach.zip`) depends on it and
   will keep working for every future version. **Publish.**
5. Click the README's **Download** button to confirm it serves your zip.
   (GitHub's automatic "Source code" archives also appear on the release
   — harmless; students use the button.)

## 2. Generate the course secrets

Run once per deployment (each run makes fresh random values):

```bash
python -c "import secrets; print('course-' + secrets.token_urlsafe(18))"
python -c "import secrets; print('admin-'  + secrets.token_urlsafe(18))"
```

The two lines printed are the **course token** (goes to students) and the
**admin token** (stays with you — it opens the dashboard). Save both in a
private note **outside any git folder** (e.g.
`C:\Users\you\dlc_course_secrets.txt` or a password manager). They are
never committed and never baked into the release.

## 3. Run the course proxy

The proxy holds your API key, enforces per-machine daily limits that
survive re-downloads, collects the anonymized telemetry, and serves the
dashboard. Full endpoint reference: [../proxy/README.md](../proxy/README.md).

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # your course key
export DLC_COURSE_TOKEN=<course token from step 2>
export DLC_ADMIN_TOKEN=<admin token from step 2>
export DLC_PROXY_DB=/path/to/dlc_proxy.db     # keep this file safe
uv run uvicorn proxy.dlc_proxy:app --host 0.0.0.0 --port 8321
```

(Windows PowerShell: `$env:ANTHROPIC_API_KEY = "sk-ant-..."` etc.)

Sanity check: `curl http://localhost:8321/v1/health` — expect
`"key_configured":true`, `"key_format_ok":true`,
`"course_token_set":true`. Startup also prints a warning if the key looks
malformed or the course token is missing.

### Spend protection (three layers, all on by default)

| Layer | Default | Tune with |
|---|---|---|
| Per-student daily caps (what students feel) | Mode A 1/day, Mode B 2/day | `dlc/l3/limits.py` `CAPS` |
| Per-machine proxy backstop (wipe-proof) | modeA 8, modeB 10, grade 20, explain 20 calls/day | `CALL_BUDGETS` in `proxy/dlc_proxy.py` |
| Whole-server circuit breaker | 600 calls/day AND $20 est./day | env `DLC_GLOBAL_DAILY_CALLS`, `DLC_GLOBAL_DAILY_USD` |

If the breaker trips, every AI request answers "the course server has
reached its daily capacity" until midnight (server time) — a leaked token
can burn at most one day's cap, never your balance.

### Option A — your own machine (small pilots)

Works exactly like the test setup: students on the same network use
`http://<your-LAN-IP>:8321`. Machine must stay on while students work.

### Option B — small cloud VM (recommended for a real class)

Any ~$5/month VPS (1 vCPU / 1 GB) is plenty.

1. Clone the repo on the VM, install uv, `uv sync`.
2. Put the env vars in a systemd unit so the proxy survives reboots:
   ```ini
   # /etc/systemd/system/dlc-proxy.service
   [Unit]
   Description=DLC course proxy
   After=network.target
   [Service]
   WorkingDirectory=/opt/dlc
   Environment=ANTHROPIC_API_KEY=sk-ant-...
   Environment=DLC_COURSE_TOKEN=...
   Environment=DLC_ADMIN_TOKEN=...
   Environment=DLC_PROXY_DB=/opt/dlc/dlc_proxy.db
   ExecStart=/root/.local/bin/uv run uvicorn proxy.dlc_proxy:app --host 127.0.0.1 --port 8321
   Restart=on-failure
   [Install]
   WantedBy=multi-user.target
   ```
   `systemctl enable --now dlc-proxy`
3. **HTTPS** (so tokens are never sent in the clear): put
   [Caddy](https://caddyserver.com) in front — a 2-line Caddyfile gets an
   automatic Let's Encrypt certificate:
   ```
   dlc.your-domain.edu {
       reverse_proxy 127.0.0.1:8321
   }
   ```
   Students then use `https://dlc.your-domain.edu` as the course URL.

## 4. Watch the class — the dashboard

Open **`http://<proxy-host>:8321/admin/view`** (or your HTTPS URL +
`/admin/view`) in a browser. Enter the **admin token** once — it is kept
only in that browser. You get:

- top tiles: machines, events, today's LLM calls vs cap, today's and
  all-time estimated spend, breaker state, key health;
- the machines table (first/last seen, per-machine event and call counts);
- per-day activity and per-day LLM usage by machine and feature.

Raw data when you want it:
`/admin/summary`, `/admin/daily`, `/admin/export.csv?table=events|machines|llm_calls`
(all accept the token via the `X-DLC-Admin-Token` header or `?token=`).

## 5. Announce to students

Template:

> Digital Lab Coach is ready. Download the v0.1.0 zip from
> `<release URL>`, unzip, and double-click `START_HERE` (Windows) or run
> `./start.sh` (macOS/Linux). On first run, point it at your Digital.jar,
> then open Settings (gear icon) → **Course server** and paste:
> URL `https://…` — token `course-…`. That's all — no API key needed.
> Everything except the AI coach also works fully offline.

## 6. Rotating the course token

Any time (e.g. every few weeks, or after a suspected leak):

1. Generate a new course token (step 2 command).
2. Restart the proxy with the new `DLC_COURSE_TOKEN`.
3. Announce the new token; students paste it in Settings → Course server.

No re-release, no re-download; identities, history, and limits are
untouched. Rotating the admin token is the same but only you re-enter it
on the dashboard.

## 7. End-of-release checklist

- [ ] Suite green (`uv run pytest -q`) on the tagged commit.
- [ ] Release published; download + `START_HERE` tested on a clean machine.
- [ ] Proxy up with fresh tokens; `/v1/health` all-true; dashboard loads.
- [ ] One end-to-end student flow through the proxy (keyless machine).
- [ ] **Rotate the development API key** and set the new one only in the
      proxy env — the old key that was used during development is retired
      the moment the class deployment goes live.
