#!/bin/bash
# AI Report 本地运行脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 AI Report 日报生成器${NC}"
echo "================================"

# 切换到脚本所在目录
cd "$(dirname "$0")"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ 错误: 未找到 Python3${NC}"
    exit 1
fi

# 检查 .env 文件
if [ -f .env ]; then
    echo -e "${GREEN}✓ 加载 .env 配置${NC}"
    export $(grep -v '^#' .env | xargs)
fi

# 检查必要的环境变量
if [ -z "$DEEPSEEK_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$LLM_API_KEY" ]; then
    echo -e "${YELLOW}⚠️  警告: 未设置 LLM API Key${NC}"
    echo "请设置 DEEPSEEK_API_KEY 或编辑 .env 文件"
    
    if [ -f .env.example ] && [ ! -f .env ]; then
        echo -e "${YELLOW}提示: 复制 .env.example 为 .env 并填入你的 API Keys${NC}"
        echo "  cp .env.example .env"
    fi
    exit 1
fi

if [ -z "$TAVILY_API_KEY" ]; then
    echo -e "${YELLOW}⚠️  警告: 未设置 TAVILY_API_KEY，搜索功能可能受限${NC}"
fi

# 安装依赖（如果需要）
if [ "$1" == "--install" ]; then
    echo -e "${GREEN}📦 安装依赖...${NC}"
    pip3 install -r requirements.txt
    echo -e "${GREEN}✓ 依赖安装完成${NC}"
fi

# 运行日报生成
echo -e "${GREEN}📊 开始生成日报...${NC}"
echo ""

python3 main.py

echo ""
echo -e "${GREEN}✅ 完成！${NC}"
echo "报告保存在 reports/ 目录"
