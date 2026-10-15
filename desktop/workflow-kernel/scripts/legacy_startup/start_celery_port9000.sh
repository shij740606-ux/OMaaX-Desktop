#!/bin/bash
# Start PlantAI-dev Celery workers for the port-9000 runtime profile.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${BBAV5_PROJECT_ROOT:-$SCRIPT_DIR}"

CONDA_BASE="${BBAV5_CONDA_BASE:-/home/xia-lab/miniconda3}"
DEFAULT_ENV="${BBAV5_DEFAULT_ENV:-test4py311}"
CONDA_ENV="$CONDA_BASE/envs/$DEFAULT_ENV"
export PATH="$CONDA_ENV/bin:$PATH"

export BBAV5_PROJECT_ROOT="${BBAV5_PROJECT_ROOT:-$SCRIPT_DIR}"
export PLANTAI_PROJECT_ROOT="${PLANTAI_PROJECT_ROOT:-$SCRIPT_DIR}"
export BBAV5_QUEUE_PREFIX="${BBAV5_QUEUE_PREFIX:-plantai_dev_9000}"
export BBAV5_REDIS_URL="${BBAV5_REDIS_URL:-redis://localhost:6379/10}"

WORKER_NAME_PREFIX="${BBAV5_WORKER_NAME_PREFIX:-${BBAV5_QUEUE_PREFIX//[^A-Za-z0-9_]/_}}"
LOG_DIR="${BBAV5_CELERY_LOG_DIR:-logs/celery_plantai_dev_9000}"
mkdir -p "$LOG_DIR"

CELERY="$CONDA_ENV/bin/celery"
APP="celery_app"
LOGLEVEL="${BBAV5_CELERY_LOGLEVEL:-info}"

DEFAULT_CONCURRENCY="${BBAV5_DEFAULT_CONCURRENCY:-1}"
HEAVY_CONCURRENCY="${BBAV5_HEAVY_CONCURRENCY:-2}"
MEDIUM_CONCURRENCY="${BBAV5_MEDIUM_CONCURRENCY:-2}"
LIGHT_CONCURRENCY="${BBAV5_LIGHT_CONCURRENCY:-4}"
RNASEQ_CONCURRENCY="${BBAV5_RNASEQ_CONCURRENCY:-2}"

worker_online() {
    local name="$1"
    if [ "${BBAV5_CELERY_FORCE_START:-0}" = "1" ]; then
        return 1
    fi
    pgrep -f -- "--hostname=${name}@%h" >/dev/null 2>&1
}

start_worker() {
    local name="$1"
    local queue="$2"
    local concurrency="$3"
    if [ "$concurrency" -le 0 ]; then
        echo "Skipping ${name}: concurrency=${concurrency}"
        return
    fi
    if worker_online "$name"; then
        echo "Skipping ${name}: worker process already exists (${name}@%h)"
        return
    fi
    echo "Starting ${name}: queue=${queue}, concurrency=${concurrency}"
    "$CELERY" -A "$APP" worker \
        --detach \
        --loglevel="$LOGLEVEL" \
        --hostname="${name}@%h" \
        --queues="$queue" \
        --concurrency="$concurrency" \
        --prefetch-multiplier=1 \
        --logfile="$LOG_DIR/${name}.log" \
        --pidfile="$LOG_DIR/${name}.pid"
}

start_worker "${WORKER_NAME_PREFIX}_default" "${BBAV5_QUEUE_PREFIX}.default" "$DEFAULT_CONCURRENCY"
start_worker "${WORKER_NAME_PREFIX}_heavy" "${BBAV5_QUEUE_PREFIX}.heavy" "$HEAVY_CONCURRENCY"
start_worker "${WORKER_NAME_PREFIX}_medium" "${BBAV5_QUEUE_PREFIX}.medium" "$MEDIUM_CONCURRENCY"
start_worker "${WORKER_NAME_PREFIX}_light" "${BBAV5_QUEUE_PREFIX}.light" "$LIGHT_CONCURRENCY"
start_worker "${WORKER_NAME_PREFIX}_rnaseq" "${BBAV5_QUEUE_PREFIX}.rnaseq" "$RNASEQ_CONCURRENCY"

echo "Celery workers started with queue prefix: ${BBAV5_QUEUE_PREFIX}"
echo "Redis: ${BBAV5_REDIS_URL}"
echo "Logs: ${LOG_DIR}"
