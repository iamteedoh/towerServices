# role: tower_services

Validate → act → report against a list of services, on either systemd or
Kubernetes, for one inventory host group ("scope").

## Variables

| Var | Default | Notes |
|-----|---------|-------|
| `service_action` | `status` | `status\|enable\|disable\|start\|stop\|restart` |
| `confirm` | `false` | required `true` for `disable`/`stop` |
| `tower_platform` | `systemd` | `systemd` or `kubernetes` (set per group_vars) |
| `tower_services` | `[]` | units (systemd) or Deployment names (k8s) — set per group_vars |
| `awx_namespace` | `awx` | k8s namespace for the `awx` scope |
| `awx_desired_replicas` | `1` | replica count restored on enable/start |
| `status_output_dir` | `/tmp/towerservices` | control-node dir for per-host JSON |
| `write_status_file` | `true` | toggle JSON emission |

## Behaviour

- **systemd**: maps the action to `systemd_service` `state`/`enabled`. Stops and
  disables in reverse service order; starts/enables in forward order. Tolerates
  "unit not found" only when stopping/disabling.
- **kubernetes**: `k8s_scale` to desired/0 for enable/disable/start/stop; a
  `restartedAt` annotation patch for restart; `k8s_info` for status.
- Always finishes by computing `overall_state`/`overall_color` and writing the
  per-host status JSON consumed by the bridge.

This role intentionally uses unprefixed shared vars (`service_action`,
`tower_services`) so the same names drive the CLI, surveys, and the bridge.
