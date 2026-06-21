[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-ea4aaa?logo=github)](https://github.com/sponsors/iamteedoh) [![Patreon](https://img.shields.io/badge/Support-Patreon-f96854?logo=patreon)](https://patreon.com/iamteedoh) [![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/iamteedoh)

# towerServices

Enable, disable, start, stop, restart, and **status-check** the services behind
Ansible Automation Platform (AAP), legacy Ansible Tower, and AWX — for planned
maintenance or one-button remediation. A small bridge service exposes the same
actions over HTTP so a **Homepage dashboard tile** can show health (🟢/🔴) and
trigger the action without anyone SSHing to a box.

## Platforms & scopes

Each scope is an inventory group. Pick it with `-l <scope>` (CLI) or as the path
segment in the bridge URL.

| Scope            | Runtime    | What it manages |
|------------------|------------|-----------------|
| `aap_controller` | systemd    | AAP 2.x controller (web, task, rsyslog, receptor, nginx, redis) |
| `aap_hub`        | systemd    | Private Automation Hub (pulpcore-*) |
| `aap_eda`        | systemd    | Event-Driven Ansible |
| `legacy_tower`   | systemd    | Ansible Tower 3.x (`ansible-tower` umbrella) |
| `awx`            | kubernetes | AWX-on-k8s Deployments (scale up/down, rollout restart) |

## Actions

`status` (read-only) · `enable` · `disable` · `start` · `stop` · `restart`

- **enable/disable** also change boot enablement (systemd) or scale replicas
  to desired/0 (k8s). **start/stop** change runtime only.
- **disable** and **stop** are destructive and require `confirm=true`.

## CLI usage

```bash
ansible-galaxy collection install -r requirements.yml

# Status of the controller (writes per-host JSON to $TOWERSERVICES_STATUS_DIR)
ansible-playbook site.yml -l aap_controller -e service_action=status

# Maintenance window: take the controller offline, then bring it back
ansible-playbook site.yml -l aap_controller -e service_action=disable -e confirm=true
ansible-playbook site.yml -l aap_controller -e service_action=enable

# Rolling restart across a controller cluster, one node at a time
ansible-playbook site.yml -l aap_controller -e service_action=restart -e maintenance_serial=1

# Disable AWX (scale awx-web/awx-task to 0)
ansible-playbook site.yml -l awx -e service_action=disable -e confirm=true
```

## Layout

```
site.yml                       entry playbook (systemd + kubernetes plays)
ansible.cfg, requirements.yml
inventories/production/
  hosts.yml                    groups = scopes (edit hostnames)
  group_vars/<scope>.yml       service lists per platform
roles/tower_services/          the reusable role (validate → act → report)
bridge/                        FastAPI HTTP bridge + Dockerfile + k8s manifests
homepage/services-snippet.yaml Homepage tiles (status widget + action buttons)
```

## Homepage button

The bridge turns each scope into an HTTP endpoint Homepage can read and trigger.
See [bridge/README.md](bridge/README.md) for build/deploy, then paste
[homepage/services-snippet.yaml](homepage/services-snippet.yaml) into the
homepage repo and redeploy. The status tile shows:

- 🟢 **Healthy** — all services active
- 🔴 **Disabled** — all services stopped
- 🟠 **Degraded** — mixed (some down)

## Status JSON

Every run writes `$TOWERSERVICES_STATUS_DIR/<host>.json`:

```json
{
  "host": "controller1.example.tld",
  "platform": "systemd",
  "action": "status",
  "state": "healthy",
  "color": "green",
  "services": [
    {"name": "automation-controller-web", "active_state": "active",
     "enabled": "enabled", "active": true}
  ],
  "checked_at": "2026-06-21T18:00:00+00:00"
}
```
