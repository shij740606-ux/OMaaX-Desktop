#!/bin/bash
# 重启bbav20311服务并加载新提示词

echo "=============================================="
echo "重启BBAV20311服务 - 加载新提示词"
echo "=============================================="
echo ""

# 1. 查找并停止现有服务
echo "1. 停止现有服务..."
MAIN_PID=$(ps aux | grep "python3 main.py" | grep -v grep | grep -v restart | awk '{print $2}' | head -1)

if [ -n "$MAIN_PID" ]; then
    echo "   找到主服务PID: $MAIN_PID"
    kill -15 $MAIN_PID 2>/dev/null
    sleep 2
    echo "   已发送停止信号"
else
    echo "   未找到运行中的主服务"
fi

# 2. 激活conda环境
echo ""
echo "2. 激活conda环境..."
source ~/anaconda3/etc/profile.d/conda.sh
conda activate test4py311
echo "   环境: $(conda info --envs | grep '*')"

# 3. 验证提示词文件
echo ""
echo "3. 验证提示词文件..."
PROMPT_COUNT=$(ls /home/xia-lab/wutx/00test/bbav20311/prompts/*_agent.md 2>/dev/null | wc -l)
echo "   提示词文件数量: $PROMPT_COUNT"

if [ "$PROMPT_COUNT" -eq 8 ]; then
    echo "   ✅ 所有提示词文件存在"
else
    echo "   ⚠️ 提示词文件数量不匹配"
fi

# 4. 启动服务
echo ""
echo "4. 启动服务..."
cd /home/xia-lab/wutx/00test/bbav20311
nohup python3 main.py > /tmp/bbav20311_restart.log 2>&1 &
NEW_PID=$!
echo "   新PID: $NEW_PID"

# 5. 等待服务启动
echo ""
echo "5. 等待服务启动..."
sleep 5

# 6. 验证服务
echo ""
echo "6. 验证服务..."
if ps -p $NEW_PID > /dev/null; then
    echo "   ✅ 服务进程运行中"
    
    # 测试API
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:7865/api/info 2>/dev/null)
    if [ "$HTTP_STATUS" = "200" ]; then
        echo "   ✅ API响应正常 (HTTP 200)"
    else
        echo "   ⚠️ API响应异常 (HTTP $HTTP_STATUS)"
    fi
else
    echo "   ❌ 服务启动失败"
    exit 1
fi

echo ""
echo "=============================================="
echo "重启完成"
echo "=============================================="
echo "PID: $NEW_PID"
echo "日志: /tmp/bbav20311_restart.log"
echo "提示词: /home/xia-lab/wutx/00test/bbav20311/prompts/"
echo "=============================================="
