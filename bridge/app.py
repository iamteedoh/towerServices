# SPDX-License-Identifier: GPL-3.0-or-later
"""towerServices bridge.

A thin HTTP layer between the Homepage dashboard and the Ansible playbooks.

  GET  /healthz                         liveness
  GET  /api/v1/status/{scope}           run a read-only status check, return
                                        an aggregated verdict for the scope
                                        (shaped for Homepage's customapi widget)
  POST /api/v1/action/{scope}/{action}  run enable|disable|start|stop|restart
  GET  /api/v1/action/{scope}/{action}  same, for Homepage link-buttons
                                        (requires ?token=... ; destructive
                                        actions require &confirm=true)

`scope` is an inventory group: aap_controller | aap_hub | aap_eda |
legacy_tower | awx. The bridge shells out to ansible-playbook with
-l <scope> -e service_action=<action> and reads the per-host JSON the
playbook writes to STATUS_DIR.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)

# --- configuration (env-driven; see bridge/README.md) ----------------------
REPO_DIR = Path(os.environ.get("TOWERSERVICES_REPO", "/app/repo"))
PLAYBOOK = os.environ.get("TOWERSERVICES_PLAYBOOK", "site.yml")
INVENTORY = os.environ.get("TOWERSERVICES_INVENTORY", "inventories/production")
STATUS_DIR = Path(os.environ.get("TOWERSERVICES_STATUS_DIR", "/tmp/towerservices"))
API_TOKEN = os.environ.get("TOWERSERVICES_TOKEN", "")
# Fail closed: if no token is configured, authenticated calls are rejected (503)
# rather than silently running open. Set TOWERSERVICES_ALLOW_ANON=1 to opt into
# an explicit no-auth mode (local dev only).
ALLOW_ANON = os.environ.get("TOWERSERVICES_ALLOW_ANON") == "1"
ANSIBLE_BIN = os.environ.get("ANSIBLE_PLAYBOOK_BIN", "ansible-playbook")

# Redirect-gate (/go/awx): the AWX link points here. Up -> redirect to the real
# AWX UI; down -> a maintenance page that distinguishes a deliberate disable
# (CR replicas=0) from an unexpected outage.
AWX_API_URL = os.environ.get("AWX_API_URL", "")            # internal, for health
AWX_API_TOKEN = os.environ.get("AWX_API_TOKEN", "")        # for the paused check
AWX_PUBLIC_URL = os.environ.get("AWX_PUBLIC_URL", "")      # browser-facing AWX UI
AWX_NAMESPACE = os.environ.get("AWX_NAMESPACE", "awx")
AWX_CR_NAME = os.environ.get("AWX_CR_NAME", "awx")

VALID_SCOPES = {"aap_controller", "aap_hub", "aap_eda", "legacy_tower", "awx"}
DESTRUCTIVE = {"disable", "stop"}
VALID_ACTIONS = {"status", "enable", "disable", "start", "stop", "restart",
                 "pause", "resume"}

# state (from the playbook) -> (tile color, display label)
STATE_COLOR = {"healthy": "green", "disabled": "red", "paused": "amber",
               "degraded": "amber", "unknown": "grey"}
STATE_LABEL = {"healthy": "Healthy", "disabled": "Disabled", "paused": "Paused",
               "degraded": "Degraded", "unknown": "Unknown"}

app = FastAPI(title="towerServices bridge", version="1.1.0")

# CORS so a dashboard (Homepage) can call the action endpoints from the browser
# with an Authorization header. Set TOWERSERVICES_CORS_ORIGINS to a comma-list of
# allowed origins (e.g. http://homepage.lan,http://192.168.0.5:30089). Defaults to
# EMPTY (no cross-origin accepted) — fail closed; set it explicitly. Use "*" only
# if you really want any origin on a trusted LAN.
CORS_ORIGINS = [o.strip() for o in
                os.environ.get("TOWERSERVICES_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["authorization", "content-type"],
)


def _check_token(request: Request, token_qs: str | None = None) -> None:
    """Authenticate via `Authorization: Bearer <token>` (preferred) or, only
    for the GET link-button endpoint, a `?token=` query param. All comparisons
    are constant-time to avoid leaking the token via timing."""
    if not API_TOKEN:
        if ALLOW_ANON:
            return
        raise HTTPException(status_code=503,
                            detail="auth not configured (set TOWERSERVICES_TOKEN)")
    header = request.headers.get("authorization", "")
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    candidates = [c for c in (bearer, token_qs) if c is not None]
    if not any(secrets.compare_digest(c, API_TOKEN) for c in candidates):
        raise HTTPException(status_code=401, detail="invalid or missing token")


async def _run_playbook(scope: str, action: str, confirm: bool,
                        force: bool = False) -> dict:
    """Run the playbook for one scope/action and return aggregated status."""
    env = dict(os.environ, TOWERSERVICES_STATUS_DIR=str(STATUS_DIR))
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        ANSIBLE_BIN, PLAYBOOK,
        "-i", INVENTORY,
        "-l", scope,
        "-e", f"service_action={action}",
        "-e", f"confirm={'true' if confirm else 'false'}",
        "-e", f"drain_force={'true' if force else 'false'}",
        "-e", f"status_output_dir={STATUS_DIR}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(REPO_DIR), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return {
        "rc": proc.returncode,
        "log": stdout.decode(errors="replace")[-4000:],
        "status": _aggregate(scope),
    }


def _aggregate(scope: str) -> dict:
    """Combine per-host JSON files into one verdict for the scope, preserving
    the distinct 'paused' state (rather than collapsing it into 'degraded')."""
    hosts = []
    for jf in sorted(STATUS_DIR.glob("*.json")):
        try:
            hosts.append(json.loads(jf.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    states = [str(h.get("state", "unknown")).lower() for h in hosts]
    if not states:
        overall = "unknown"
    elif all(s == "healthy" for s in states):
        overall = "healthy"
    elif all(s == "disabled" for s in states):
        overall = "disabled"
    elif all(s == "paused" for s in states):
        overall = "paused"
    elif all(s in ("disabled", "unknown") for s in states):
        overall = "disabled"
    else:
        overall = "degraded"
    # surface a blocked graceful-disable (running jobs) and the offending jobs
    blocked = any((h.get("drain") or {}).get("blocked") for h in hosts)
    jobs = [j for h in hosts for j in (h.get("drain") or {}).get("jobs", [])]
    return {"scope": scope, "color": STATE_COLOR[overall],
            "state": STATE_LABEL[overall],
            "drain": {"blocked": blocked, "jobs": jobs}, "hosts": hosts}


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/v1/status/{scope}")
async def status(request: Request, scope: str) -> JSONResponse:
    # Header-only auth: Homepage's customapi widget sends Authorization.
    if scope not in VALID_SCOPES:
        raise HTTPException(404, f"unknown scope '{scope}'")
    _check_token(request)
    result = await _run_playbook(scope, "status", confirm=False)
    # Homepage customapi reads this top-level shape directly.
    return JSONResponse(result["status"])


async def _do_action(request: Request, scope: str, action: str,
                     confirm: bool, force: bool = False,
                     token_qs: str | None = None) -> JSONResponse:
    if scope not in VALID_SCOPES:
        raise HTTPException(404, f"unknown scope '{scope}'")
    if action not in VALID_ACTIONS:
        raise HTTPException(400, f"unknown action '{action}'")
    _check_token(request, token_qs)
    if action in DESTRUCTIVE and not confirm:
        raise HTTPException(400, f"action '{action}' requires confirm=true")
    result = await _run_playbook(scope, action, confirm=confirm, force=force)
    code = 200 if result["rc"] == 0 else 500
    return JSONResponse(result, status_code=code)


@app.post("/api/v1/action/{scope}/{action}")
async def action_post(request: Request, scope: str, action: str,
                      confirm: bool = Query(default=False),
                      force: bool = Query(default=False)) -> JSONResponse:
    # Header-only auth (Authorization: Bearer <token>). Use this for scripts.
    return await _do_action(request, scope, action, confirm, force=force)


@app.get("/api/v1/action/{scope}/{action}")
async def action_get(request: Request, scope: str, action: str,
                     token: str | None = Query(default=None),
                     confirm: bool = Query(default=False),
                     force: bool = Query(default=False)) -> JSONResponse:
    # Convenience for Homepage link-buttons ONLY: a tile href is a plain GET
    # and cannot carry an Authorization header, so the token rides the query
    # string here. This leaks the token into access logs / browser history /
    # Referer — keep this endpoint on the LAN, behind a proxy that strips the
    # query string from logs, and rotate the token periodically. Prefer the
    # POST endpoint (header auth) everywhere else.
    return await _do_action(request, scope, action, confirm, force=force,
                            token_qs=token)


# --- redirect-gate: the AWX link points here ------------------------------
def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            code = getattr(r, "status", None) or r.getcode()
            return 200 <= code < 500
    except Exception:
        return False


def _awx_web_up() -> bool:
    return bool(AWX_API_URL) and _http_ok(AWX_API_URL.rstrip("/") + "/api/")


def _awx_cr_replicas():
    """Read AWX CR spec.replicas (None on failure) to tell a deliberate disable
    (0) from an unexpected outage."""
    try:
        from kubernetes import client, config
        kubeconfig = os.environ.get("KUBECONFIG", "")
        if kubeconfig and os.path.exists(kubeconfig):
            config.load_kube_config(config_file=kubeconfig)
        else:
            config.load_incluster_config()
        obj = client.CustomObjectsApi().get_namespaced_custom_object(
            "awx.ansible.com", "v1beta1", AWX_NAMESPACE, "awxs", AWX_CR_NAME)
        return (obj.get("spec") or {}).get("replicas")
    except Exception:
        return None


def _maintenance_html(deliberate: bool) -> str:
    if deliberate:
        head = "AWX is Disabled"
        msg = ("AWX is currently <strong>completely disabled</strong> per a "
               "deliberate disablement request from an Admin.")
        sub = "All AWX services are stopped for maintenance."
    else:
        head = "AWX Unavailable"
        msg = "AWX is currently <strong>temporarily unavailable</strong>."
        sub = "The service may be starting up or experiencing an issue."
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="20">
<title>{head}</title>
<style>
  html,body{{height:100%;margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
  body{{display:flex;align-items:center;justify-content:center;
       background:radial-gradient(circle at 50% 30%,#1f2937,#0b0f17);color:#e5e7eb}}
  .card{{max-width:560px;margin:24px;padding:40px 44px;border-radius:16px;
        background:#111827;border:1px solid #1f2937;box-shadow:0 20px 60px rgba(0,0,0,.5);text-align:center}}
  .dot{{width:14px;height:14px;border-radius:50%;display:inline-block;margin-right:10px;
       background:{'#f04a4a' if deliberate else '#fbbf24'};box-shadow:0 0 14px {'#f04a4a' if deliberate else '#fbbf24'}}}
  h1{{font-size:22px;margin:0 0 14px}} p{{line-height:1.6;color:#cbd5e1;margin:8px 0}}
  .sub{{color:#94a3b8;font-size:14px;margin-top:18px}}
</style></head>
<body><div class="card">
  <h1><span class="dot"></span>{head}</h1>
  <p>{msg}</p>
  <p class="sub">{sub} This page refreshes automatically and will return you to AWX once it is back.</p>
</div></body></html>"""


@app.get("/go/{scope}")
def go(scope: str):
    """Redirect to the real AWX UI when it's up; otherwise show a maintenance
    page (deliberate disable vs. unexpected outage). Used as the AWX link."""
    if scope != "awx":
        raise HTTPException(404, f"no gate for scope '{scope}'")
    if _awx_web_up() and AWX_PUBLIC_URL:
        return RedirectResponse(AWX_PUBLIC_URL, status_code=302)
    return HTMLResponse(_maintenance_html(_awx_cr_replicas() == 0), status_code=503)


@app.get("/maint/{scope}")
def maint(scope: str):
    """Page-only maintenance response (no redirect). The reverse-proxy serves
    this when the AWX backend is unreachable, so the page shows at the AWX URL
    itself — for direct hits and refreshes, not just navigation through a gate."""
    if scope != "awx":
        raise HTTPException(404, f"no maintenance page for scope '{scope}'")
    return HTMLResponse(_maintenance_html(_awx_cr_replicas() == 0), status_code=503)


# --- in-AWX paused banner (injected by the proxy) -------------------------
def _awx_paused() -> bool:
    """Fast check (no Ansible): are all job-running instances drained?"""
    if not (AWX_API_URL and AWX_API_TOKEN):
        return False
    try:
        req = urllib.request.Request(
            AWX_API_URL.rstrip("/") + "/api/v2/instances/",
            headers={"Authorization": "Bearer " + AWX_API_TOKEN})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        jobs = [i for i in data.get("results", [])
                if i.get("node_type") in ("control", "hybrid", "execution")]
        return bool(jobs) and all(not i.get("enabled") for i in jobs)
    except Exception:
        return False


@app.get("/state/{scope}")
def state(scope: str):
    """Lightweight live state for the injected banner (no Ansible run)."""
    if scope != "awx":
        raise HTTPException(404, f"no state for scope '{scope}'")
    if not _awx_web_up():
        return {"scope": scope, "state": "disabled", "paused": False}
    paused = _awx_paused()
    return {"scope": scope, "state": "paused" if paused else "healthy",
            "paused": paused}


BANNER_JS = (
    "(function(){var ID='ts-maint-banner';"
    "function ensure(show,text){var el=document.getElementById(ID);"
    "if(!show){if(el)el.remove();return;}"
    "if(!el){el=document.createElement('div');el.id=ID;"
    "el.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:2147483647;'+"
    "'background:#b45309;color:#fff;font:600 13px/1.4 system-ui,-apple-system,sans-serif;'+"
    "'padding:8px 16px;text-align:center;box-shadow:0 -2px 8px rgba(0,0,0,.35);';"
    "document.body.appendChild(el);}el.textContent=text;}"
    "function poll(){fetch('/_ts/state',{cache:'no-store'})"
    ".then(function(r){return r.json();}).then(function(s){"
    "if(s&&s.state==='paused'){ensure(true,"
    "'\\u26A0\\uFE0F Maintenance: AWX job execution is PAUSED \\u2014 new jobs will not run until maintenance is complete.');}"
    "else{ensure(false);}}).catch(function(){});}"
    "poll();setInterval(poll,20000);})();"
)


@app.get("/banner.js")
def banner_js():
    """The script the proxy injects into AWX pages — shows an amber banner
    across the AWX UI whenever job execution is paused."""
    return Response(content=BANNER_JS, media_type="application/javascript")
