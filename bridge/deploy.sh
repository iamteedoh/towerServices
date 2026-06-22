#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build, push, and deploy the towerServices bridge using values from .env.
# All environment-specific values live in .env (gitignored), never in git.
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root
ENV_FILE="${ENV_FILE:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found. Run: cp .env.example .env && edit it" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${REGISTRY:?set in .env}" "${IMAGE:?}" "${IMAGE_TAG:?}" "${K8S_NAMESPACE:?}" "${BRIDGE_NODEPORT:?}"
KUBECTL="${KUBECTL:-kubectl}"
RENDER_DIR="bridge/k8s/rendered"

cmd="${1:-all}"   # all | build | push | render | apply

build() {
  echo ">> building ${REGISTRY}/${IMAGE}:${IMAGE_TAG}"
  docker build --platform linux/amd64 \
    -t "${REGISTRY}/${IMAGE}:${IMAGE_TAG}" -f bridge/Dockerfile .
}
push() { echo ">> pushing"; docker push "${REGISTRY}/${IMAGE}:${IMAGE_TAG}"; }
render() {
  echo ">> rendering manifests -> ${RENDER_DIR}"
  mkdir -p "$RENDER_DIR"
  envsubst < bridge/k8s/deployment.yaml.tmpl > "${RENDER_DIR}/deployment.yaml"
  # Restrict to our vars so nginx's own $http_upgrade/$host/etc. survive.
  envsubst '${K8S_NAMESPACE} ${AWX_PROXY_NODEPORT} ${AWX_UPSTREAM} ${BRIDGE_UPSTREAM}' \
    < proxy/k8s/awx-proxy.yaml.tmpl > "${RENDER_DIR}/awx-proxy.yaml"
  echo "rendered ${RENDER_DIR}/deployment.yaml ${RENDER_DIR}/awx-proxy.yaml"
}
proxy() {
  render
  echo ">> applying AWX reverse-proxy to ${K8S_NAMESPACE}"
  $KUBECTL apply -f "${RENDER_DIR}/awx-proxy.yaml"
}
apply() {
  render
  echo ">> applying to namespace ${K8S_NAMESPACE} (run KUBECTL=... if remote)"
  $KUBECTL apply -f "${RENDER_DIR}/deployment.yaml"
}

case "$cmd" in
  build) build ;;
  push) push ;;
  render) render ;;
  apply) apply ;;
  proxy) proxy ;;
  all) build; push; apply ;;
  *) echo "usage: $0 [all|build|push|render|apply|proxy]" >&2; exit 2 ;;
esac
