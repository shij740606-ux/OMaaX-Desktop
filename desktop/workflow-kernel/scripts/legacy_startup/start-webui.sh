#!/bin/bash
# WebUI for bbav5 project
# Port: 8788, State: ~/.hermes/webui-bbav5

export HERMES_WEBUI_PORT=8788
export HERMES_WEBUI_STATE_DIR="$HOME/.hermes/webui-bbav5"
export HERMES_WEBUI_DEFAULT_WORKSPACE="/home/xia-lab/wutx/00test/bbav5"
export HERMES_WEBUI_AGENT_DIR="/home/xia-lab/wutx/01model/hermes-agent"

# Create state dir if not exists
mkdir -p "$HERMES_WEBUI_STATE_DIR"

cd /home/xia-lab/wutx/01model/hermes-webui

echo "Starting WebUI for bbav5..."
echo "  Port: $HERMES_WEBUI_PORT"
echo "  Workspace: $HERMES_WEBUI_DEFAULT_WORKSPACE"
echo "  State: $HERMES_WEBUI_STATE_DIR"
echo "  URL: http://127.0.0.1:8788"

source /home/xia-lab/wutx/01model/hermes-agent/venv/bin/activate
python server.py