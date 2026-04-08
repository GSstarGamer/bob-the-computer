#!/usr/bin/env bash
set -euo pipefail

BOB_LLM_USER="${BOB_LLM_USER:-girish}"
BOB_LLM_ROOT="${BOB_LLM_ROOT:-/opt/bob-llm}"
BOB_LLM_ETC="${BOB_LLM_ETC:-/etc/bob-llm}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "bootstrap_remote.sh must run as root" >&2
  exit 1
fi

apt-get update
apt-get install -y python3-venv python3-pip build-essential git curl caddy

mkdir -p "${BOB_LLM_ROOT}/models" "${BOB_LLM_ROOT}/venv" "${BOB_LLM_ETC}"
python3 -m venv "${BOB_LLM_ROOT}/venv"
"${BOB_LLM_ROOT}/venv/bin/pip" install --upgrade pip setuptools wheel
"${BOB_LLM_ROOT}/venv/bin/pip" install vllm "huggingface_hub[cli]"

install -d -o "${BOB_LLM_USER}" -g "${BOB_LLM_USER}" "${BOB_LLM_ROOT}" "${BOB_LLM_ROOT}/models"
chown -R "${BOB_LLM_USER}:${BOB_LLM_USER}" "${BOB_LLM_ROOT}"

install -m 0644 "${SCRIPT_DIR}/systemd/bob-llm@.service" /etc/systemd/system/bob-llm@.service
install -m 0644 "${SCRIPT_DIR}/Caddyfile" /etc/caddy/Caddyfile

systemctl daemon-reload
systemctl enable caddy

echo "Remote bootstrap complete."
echo "Next steps:"
echo "1. Copy an instance env file to ${BOB_LLM_ETC}/gpt-oss.env"
echo "2. systemctl enable --now bob-llm@gpt-oss"
echo "3. systemctl restart caddy"
