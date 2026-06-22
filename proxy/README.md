# AWX maintenance reverse-proxy

An nginx reverse-proxy that **becomes the AWX URL** so a maintenance page can be
shown at the AWX address itself when AWX is disabled — including direct hits and
refreshes, which a redirect-gate can't catch.

- AWX up → proxies through to AWX (websockets included, for live job output).
- AWX down → falls back to the bridge's `/maint/awx`, which serves the
  maintenance page (distinguishing a deliberate disable from an outage).

## How it fits

```
browser → :30083 (this proxy) ─┬─ up   → awx-service (ClusterIP) → awx-web
                               └─ down → towerservices-bridge /maint/awx
```

## Required AWX-side setup

For the proxy to OWN the AWX URL and for login to work behind it, the AWX
install needs two changes (both on the AWX custom resource):

1. **Expose AWX internally only** so the proxy can take its NodePort:
   ```bash
   kubectl -n awx patch awx awx --type merge -p '{"spec":{"service_type":"ClusterIP"}}'
   ```

2. **Trust the proxy origin for CSRF** — otherwise the login POST is rejected
   with `403 (Origin checking failed ... does not match any trusted origins)`,
   because AWX (Django) only trusts its own computed origin by default:
   ```bash
   kubectl -n awx patch awx awx --type merge -p '{"spec":{"extra_settings":[
     {"setting":"CSRF_TRUSTED_ORIGINS",
      "value":["http://<host>:<port>","https://<host>:<port>"]}]}}'
   ```
   Use the exact scheme+host+port users reach AWX on (e.g.
   `http://192.168.4.53:30083`). Both schemes are listed to cover http/https
   forwarding ambiguity. Changing `extra_settings` triggers an operator
   redeploy of AWX (~1–2 min; the proxy shows the maintenance page meanwhile).

To revert to AWX directly on its NodePort and remove the proxy:
```bash
kubectl -n awx patch awx awx --type merge -p '{"spec":{"service_type":"NodePort"}}'
kubectl -n <ns> delete deploy,svc,configmap awx-proxy
```

## Deploy

Values come from `.env` (`AWX_PROXY_NODEPORT`, `AWX_UPSTREAM`, `BRIDGE_UPSTREAM`,
`K8S_NAMESPACE`). Render + apply with:
```bash
./bridge/deploy.sh proxy
```
`deploy.sh` renders `proxy/k8s/awx-proxy.yaml.tmpl` with a **restricted**
`envsubst` (only our vars) so nginx's own `$host` / `$http_upgrade` survive.
