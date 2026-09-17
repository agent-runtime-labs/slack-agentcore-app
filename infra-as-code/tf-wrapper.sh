#!/usr/bin/env bash
# Thin wrapper around terraform with per-environment backend/vars.
#
#   ./tf-wrapper.sh dev init
#   ./tf-wrapper.sh dev plan
#   ./tf-wrapper.sh dev apply
#   ./tf-wrapper.sh dev output [name]
#   ./tf-wrapper.sh dev destroy
#   ./tf-wrapper.sh dev validate
set -euo pipefail

cd "$(dirname "$0")"

env_name="${1:-}"
action="${2:-}"
shift 2 || true

src_dir=./tf-app
vars_dir="../tf-vars/${env_name}"

if [[ -z "${env_name}" || ! -d "tf-vars/${env_name}" ]]; then
  echo "usage: $0 <env> <init|plan|apply|destroy|output|validate> [args]" >&2
  echo "env must be a folder under tf-vars/ (found: $(ls tf-vars | tr '\n' ' '))" >&2
  exit 2
fi

case "${action}" in
  init)
    terraform -chdir="${src_dir}" init -upgrade -reconfigure -backend-config="${vars_dir}/backend.tf" "$@"
    ;;
  validate)
    terraform -chdir="${src_dir}" init -backend=false -input=false >/dev/null
    terraform -chdir="${src_dir}" validate "$@"
    ;;
  plan|destroy)
    terraform -chdir="${src_dir}" "${action}" -var-file="${vars_dir}/app-infra-params.tfvars" "$@"
    ;;
  apply)
    terraform -chdir="${src_dir}" apply -var-file="${vars_dir}/app-infra-params.tfvars" "$@"
    ;;
  output)
    terraform -chdir="${src_dir}" output "$@"
    ;;
  *)
    echo "action must be one of: init, validate, plan, apply, destroy, output" >&2
    exit 1
    ;;
esac
