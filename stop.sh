#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${BTC_SIGNAL_RUNTIME_DIR:-${ROOT_DIR}/.runtime}"
PID_FILE="${RUNTIME_DIR}/btc-signal.pid"
PORT="${BTC_SIGNAL_PORT:-8000}"
APP_PID=""

if [[ -f "${PID_FILE}" ]]; then
  APP_PID="$(tr -dc '0-9' < "${PID_FILE}")"
fi

if [[ -z "${APP_PID}" ]] || ! kill -0 "${APP_PID}" 2>/dev/null; then
  APP_PID=""
  if command -v lsof >/dev/null 2>&1; then
    CANDIDATE_PID="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
    if [[ -n "${CANDIDATE_PID}" ]]; then
      PROCESS_CWD="$(lsof -a -p "${CANDIDATE_PID}" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
      PROCESS_COMMAND="$(ps -p "${CANDIDATE_PID}" -o command= 2>/dev/null || true)"
      if [[ "${PROCESS_CWD}" == "${ROOT_DIR}" && "${PROCESS_COMMAND}" == *"main.py"* ]]; then
        APP_PID="${CANDIDATE_PID}"
      fi
    fi
  fi
fi

if [[ -z "${APP_PID}" ]]; then
  rm -f "${PID_FILE}"
  echo "项目当前未运行。"
  exit 0
fi

kill "${APP_PID}"
for _ in {1..100}; do
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    rm -f "${PID_FILE}"
    echo "项目已停止，原 PID=${APP_PID}。"
    exit 0
  fi
  sleep 0.1
done

echo "项目在 10 秒内未停止，请检查 PID=${APP_PID}。" >&2
exit 1
