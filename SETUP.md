# AI Report 设置指南

## 📋 前置条件

1. Python 3.9+
2. DeepSeek API Key（或其他 OpenAI 兼容的 API）
3. Tavily API Key（用于产品信息搜索）

## 🔧 本地运行

### 方式一：使用运行脚本（推荐）

```bash
# 1. 复制环境变量模板
cp env_example.txt .env

# 2. 编辑 .env 填入你的 API Keys
nano .env  # 或用其他编辑器

# 3. 首次运行（安装依赖）
./run.sh --install

# 4. 后续运行
./run.sh
```

### 方式二：手动运行

```bash
# 1. 安装依赖
pip3 install -r requirements.txt

# 2. 设置环境变量
export DEEPSEEK_API_KEY="sk-your-api-key"
export TAVILY_API_KEY="tvly-your-api-key"

# 3. 运行
python3 main.py
```

### 方式三：使用 config.json

编辑 `config.json` 文件：

```json
{
  "llm_api_key": "sk-your-deepseek-api-key",
  "llm_base_url": "https://api.deepseek.com",
  "llm_model": "deepseek-chat",
  "tavily_api_key": "tvly-your-tavily-api-key",
  "webhook_url": ""
}
```

然后运行：
```bash
python3 main.py
```

---

## ☁️ GitHub Actions 自动运行

### 步骤 1：推送代码到 GitHub

```bash
# 初始化 Git（如果还没有）
git init
git add .
git commit -m "Initial commit"

# 创建 GitHub 仓库并推送
git remote add origin https://github.com/YOUR_USERNAME/ai-report.git
git branch -M main
git push -u origin main
```

### 步骤 2：配置 GitHub Secrets

1. 打开你的 GitHub 仓库
2. 进入 `Settings` > `Secrets and variables` > `Actions`
3. 点击 `New repository secret`
4. 添加以下 Secrets：

| Secret 名称 | 值 | 必填 |
|-------------|-----|------|
| `DEEPSEEK_API_KEY` | `sk-xxx...` | ✅ |
| `TAVILY_API_KEY` | `tvly-xxx...` | ✅ |
| `FEISHU_WEBHOOK` | `https://open.feishu.cn/...` | ❌ |

### 步骤 3：启用 Actions

1. 进入仓库的 `Actions` 标签页
2. 如果看到提示，点击 `I understand my workflows, go ahead and enable them`
3. 工作流会在每天 **北京时间 09:00** 自动运行

### 步骤 4：手动触发测试

1. 进入 `Actions` > `Daily AI Report Generation`
2. 点击 `Run workflow` > `Run workflow`
3. 等待运行完成，检查生成的报告

### 查看运行结果

- **报告文件**：自动提交到 `reports/` 目录
- **历史记录**：自动更新 `data/history.json`
- **Artifacts**：在 Actions 运行记录中可下载报告

---

## 🔑 API Keys 获取

### DeepSeek API
1. 访问 https://platform.deepseek.com/
2. 注册账号并创建 API Key
3. 充值后即可使用（很便宜）

### Tavily API
1. 访问 https://tavily.com/
2. 注册账号
3. 在 Dashboard 获取 API Key
4. 免费额度：1000 次/月

---

## ❓ 常见问题

### Q: 报告全是英文怎么办？
A: 检查 DeepSeek API Key 是否正确设置，确保使用的是 `deepseek-chat` 模型。

### Q: 搜索失败怎么办？
A: 检查 Tavily API Key 是否正确，或者额度是否用尽。

### Q: 如何修改运行时间？
A: 编辑 `.github/workflows/daily_run.yml` 中的 `cron` 表达式。
- `0 1 * * *` = 北京时间 09:00
- `0 23 * * *` = 北京时间 07:00

### Q: 如何添加飞书推送？
A: 在 GitHub Secrets 中添加 `FEISHU_WEBHOOK`，填入飞书机器人的 Webhook 地址。
