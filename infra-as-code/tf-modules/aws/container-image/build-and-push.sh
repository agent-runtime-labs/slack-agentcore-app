#!/usr/bin/env bash
# Called by Terraform (terraform_data.build_and_push). All inputs come from env vars.
set -euo pipefail

: "${AWS_REGION:?}" "${IMAGE_URI:?}" "${CONTEXT_DIR:?}" "${DOCKERFILE:?}" "${TARGET:?}" "${PLATFORM:?}"

REGISTRY="${IMAGE_URI%%/*}"

# Terraform can run multiple container-image modules' local-exec provisioners
# concurrently. Concurrent `docker login` calls to the same registry race on
# macOS's osxkeychain credential helper (item already exists in the keychain,
# -25299), so serialize just the login with a spinlock.
login_lock="${TMPDIR:-/tmp}/docker-ecr-login-${REGISTRY//[^a-zA-Z0-9]/_}.lock"
echo "Logging in to ${REGISTRY}"
for _ in $(seq 1 60); do
  mkdir "${login_lock}" 2>/dev/null && break
  sleep 1
done
trap 'rmdir "${login_lock}" 2>/dev/null || true' EXIT

login_ok=0
for _ in 1 2 3; do
  if aws ecr get-login-password --region "${AWS_REGION}" \
      | docker login --username AWS --password-stdin "${REGISTRY}"; then
    login_ok=1
    break
  fi
  echo "docker login failed, retrying..." >&2
  sleep 2
done
rmdir "${login_lock}" 2>/dev/null || true
trap - EXIT
[ "${login_ok}" = "1" ]

# --provenance/--sbom=false: Lambda rejects image indexes that carry attestations.
echo "Building ${IMAGE_URI} (${PLATFORM}, target ${TARGET})"
docker buildx build \
  --platform "${PLATFORM}" \
  --target "${TARGET}" \
  --provenance=false \
  --sbom=false \
  --file "${DOCKERFILE}" \
  --tag "${IMAGE_URI}" \
  --push \
  "${CONTEXT_DIR}"
