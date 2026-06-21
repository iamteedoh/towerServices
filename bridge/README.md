# towerServices bridge

A thin FastAPI service that lets the Homepage dashboard (or any HTTP client)
read service health and run maintenance actions, by shelling out to the
`site.yml` playbook in this repo.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/healthz` | liveness |
| GET  | `/api/v1/status/{scope}` | run status, return aggregated `{scope,color,state,hosts}` |
| POST | `/api/v1/action/{scope}/{action}` | run an action (JSON result) |
| GET  | `/api/v1/action/{scope}/{action}` | same, for Homepage link-buttons |

`scope` ∈ `aap_controller, aap_hub, aap_eda, legacy_tower, awx`.
`action` ∈ `status, enable, disable, start, stop, restart`.
Destructive actions (`disable`, `stop`) require `&confirm=true`.

## Auth

Set `TOWERSERVICES_TOKEN`. Comparisons are constant-time (`secrets.compare_digest`).
Empty token disables auth (do not do this on a routable network).

- `GET /status` and `POST /action` are **header-only**: `Authorization: Bearer <token>`.
  Homepage's `customapi` widget sends this header for status tiles.
- `GET /action` (the Homepage link-button) also accepts `?token=<token>`, because
  a tile `href` cannot carry a header. This leaks the token into access logs,
  browser history, and `Referer` — keep it LAN-only, front it with a proxy that
  drops the query string from logs, and rotate the token. Prefer the POST
  endpoint (header auth) for everything else.

## Config (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `TOWERSERVICES_REPO` | `/app/repo` | path to this repo inside the container |
| `TOWERSERVICES_PLAYBOOK` | `site.yml` | playbook to run |
| `TOWERSERVICES_INVENTORY` | `inventories/production` | inventory path |
| `TOWERSERVICES_STATUS_DIR` | `/var/run/towerservices` | where per-host JSON lands |
| `TOWERSERVICES_TOKEN` | _(empty)_ | shared secret |
| `KUBECONFIG` | _(unset)_ | required for the `awx` scope |

## Build & deploy

All environment-specific values (registry, namespace, NodePort, URLs) live in
`.env` at the repo root — never in git. Start from the sample:

```bash
cp .env.example .env && $EDITOR .env        # set REGISTRY, K8S_NAMESPACE, ...

# 1. Secrets (token, kubeconfig for AWX, SSH key for AAP/Tower). Replace <ns>.
kubectl -n <ns> create secret generic towerservices-bridge \
  --from-literal=token="$(openssl rand -hex 24)"
kubectl -n <ns> create secret generic towerservices-kubeconfig \
  --from-file=config=$HOME/.kube/config
kubectl -n <ns> create secret generic towerservices-ssh \
  --from-file=id_ed25519=/path/to/key       # optional, only for AAP/Tower

# 2. Build, push, render manifests from .env, and apply
./bridge/deploy.sh all                       # or: build | push | render | apply
# For a remote cluster, point kubectl at it, e.g.:
#   KUBECTL="ssh myhost sudo k3s kubectl" ./bridge/deploy.sh apply

# 3. Verify (use your BRIDGE_URL / NodePort)
curl -H "Authorization: Bearer <token>" "$BRIDGE_URL/api/v1/status/awx"
```

`deploy.sh render` writes `bridge/k8s/rendered/deployment.yaml` (gitignored) so
you can review the concrete manifest before applying.

## Local dev

```bash
pip install -r bridge/requirements.txt
export TOWERSERVICES_REPO="$PWD" TOWERSERVICES_STATUS_DIR=/tmp/ts
uvicorn bridge.app:app --reload --port 8080
```

## Security notes

- Runs Ansible as the bridge's service account; scope it tightly. The SSH key
  it mounts should be a dedicated maintenance key with sudo only for
  `systemctl` on the tower hosts.
- The GET action endpoint exists for Homepage convenience. Prefer POST for
  scripts; keep the token out of logs/referrers by fronting with your reverse
  proxy if you expose it beyond the LAN.
- Actions are serialized per request but not globally locked — avoid firing
  conflicting actions on the same scope simultaneously.
