#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${BTC_SIGNAL_RUNTIME_DIR:-${ROOT_DIR}/.runtime}"
PID_FILE="${RUNTIME_DIR}/btc-signal.pid"
LOG_FILE="${RUNTIME_DIR}/btc-signal.log"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${BTC_SIGNAL_HOST:-127.0.0.1}"
PORT="${BTC_SIGNAL_PORT:-8000}"

mkdir -p "${RUNTIME_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(tr -dc '0-9' < "${PID_FILE}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    echo "项目已经运行，PID=${EXISTING_PID}，地址：http://${HOST}:${PORT}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

if command -v lsof >/dev/null 2>&1; then
  LISTENER_PID="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ -n "${LISTENER_PID}" ]]; then
    echo "端口 ${PORT} 已被 PID=${LISTENER_PID} 占用，请先停止该进程。" >&2
    exit 1
  fi
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "未找到 Python 命令：${PYTHON_BIN}" >&2
  exit 1
fi

cd "${ROOT_DIR}"
nohup env PYTHONUNBUFFERED=1 "${PYTHON_BIN}" "${ROOT_DIR}/main.py" >> "${LOG_FILE}" 2>&1 &
APP_PID=$!
printf '%s\n' "${APP_PID}" > "${PID_FILE}"

for _ in {1..50}; do
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    rm -f "${PID_FILE}"
    echo "项目启动失败，最近日志：" >&2
    tail -n 30 "${LOG_FILE}" >&2 || true
    exit 1
  fi

  if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 1 "http://${HOST}:${PORT}/api/status" >/dev/null 2>&1; then
    echo "项目启动成功，PID=${APP_PID}，地址：http://${HOST}:${PORT}"
    echo "日志文件：${LOG_FILE}"
    exit 0
  fi
  sleep 0.2
done

rm -f "${PID_FILE}"
kill "${APP_PID}" 2>/dev/null || true
echo "项目未在预期时间内通过健康检查，请查看日志：${LOG_FILE}" >&2
exit 1
