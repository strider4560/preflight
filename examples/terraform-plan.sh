#!/usr/bin/env bash
set -euo pipefail
# Invoked by the gate from your IaC repository root. No automatic apply.
terraform init -input=false
terraform plan -input=false "$@"
