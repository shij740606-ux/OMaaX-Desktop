#!/bin/bash
# 启动Celery Worker

cd /home/xia-lab/wutx/00test/bbav5

# 使用conda环境的Python和Celery
CONDA_ENV="/home/xia-lab/miniconda3/envs/test4py311"
export PATH="$CONDA_ENV/bin:$PATH"

echo "Starting Celery Worker with conda environment: test4py311"
"$CONDA_ENV/bin/celery" -A celery_app worker --loglevel=info --concurrency=2 &

echo "Celery Worker started!"
echo "To monitor tasks, run: $CONDA_ENV/bin/celery -A celery_app flower --port=5555"
