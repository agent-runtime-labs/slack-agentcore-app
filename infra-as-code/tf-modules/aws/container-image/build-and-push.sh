#!/usr/bin/env bash
# Called by Terraform (terraform_data.build_and_push). All inputs come from env vars.
set -euo pipefail

: "${AWS_REGION:?}" "${IMAGE_URI:?}" "${CONTEXT_DIR:?}" "${DOCKERFILE:?}" "${TARGET:?}" "${PLATFORM:?}"

REGISTRY="${IMAGE_URI%%/*}"

echo "Logging in to ${REGISTRY}"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

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
