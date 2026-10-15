#!/usr/bin/env bash
# Local Kubernetes deployment of TenantPilot on kind - the same Helm chart CI deploys.
#   ./scripts/kind-up.sh          create cluster (if needed), build + load images, helm upgrade --install
#   SKIP_BUILD=1 ./scripts/kind-up.sh   redeploy without rebuilding images
set -euo pipefail
CLUSTER="${CLUSTER:-tenantpilot}"
NAMESPACE="${NAMESPACE:-tenantpilot}"
RELEASE="${RELEASE:-tenantpilot}"
# A unique tag per build: re-using a mutable tag like "local" leaves the pod template
# unchanged, so Kubernetes never rolls the Deployment and old pods keep the old image.
TAG="${TAG:-dev-$(date +%Y%m%d%H%M%S)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  step "Creating kind cluster '$CLUSTER'"
  kind create cluster --name "$CLUSTER" --config deploy/kind/kind-config.yaml --wait 180s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

step "Ingress controller (Traefik) + metrics-server (needed by the HPA)"
helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
helm repo update traefik >/dev/null
helm upgrade --install traefik traefik/traefik -n traefik --create-namespace \
  -f deploy/kind/traefik-values.yaml --wait --timeout 5m >/dev/null
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null 2>&1 || true
helm repo update metrics-server >/dev/null
helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system \
  --set 'args={--kubelet-insecure-tls}' --wait --timeout 5m >/dev/null

if [[ -z "${SKIP_BUILD:-}" ]]; then
  step "Building images (tag: $TAG)"
  docker build -t "tenantpilot-backend:$TAG" -f docker/backend.Dockerfile .
  docker build -t "tenantpilot-web:$TAG" apps/web
fi
step "Loading images into the kind node"
kind load docker-image "tenantpilot-backend:$TAG" "tenantpilot-web:$TAG" --name "$CLUSTER"

step "helm upgrade --install $RELEASE"
helm upgrade --install "$RELEASE" helm/tenantpilot -n "$NAMESPACE" --create-namespace \
  -f helm/tenantpilot/values-kind.yaml \
  --set image.tag="$TAG" --set webImage.tag="$TAG" \
  ${ANTHROPIC_API_KEY:+--set secrets.anthropicApiKey="$ANTHROPIC_API_KEY"} \
  --wait --timeout 10m

step "Rollout status"
for d in api mcp worker web; do
  kubectl -n "$NAMESPACE" rollout status "deploy/$RELEASE-$d" --timeout 180s
done

step "Smoke tests"
helm test "$RELEASE" -n "$NAMESPACE" --logs | tail -n 3
for _ in $(seq 1 30); do curl -sf http://localhost:8080/api/health >/dev/null && break; sleep 2; done
curl -sf http://localhost:8080/api/ready && echo
kubectl -n "$NAMESPACE" get pods,svc,hpa,ingress
printf '\nTenantPilot is up: http://localhost:8080  (admin@tenantpilot.dev / tenantpilot-demo)\n'
