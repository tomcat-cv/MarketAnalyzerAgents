# Market Analyzer Agents

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](https://github.com/tomcat-cv/MarketAnalyzerAgents)

双市场投资组合智能体：面向 A 股和美股、统一按北京时间呈现的全流程投资组合研究与跟踪程序。

## 当前状态

项目已提供盘前资讯、盘中行情轮询与审计建议、用户操作记录、盘后复盘的基础闭环。实时行情通过可替换 provider 获取，当前内置 Yahoo Finance 参考适配器；其授权、延迟和限流不适合直接视为交易所级实时数据。

A 股和美股按两套独立市场流程设计，各自维护交易日历、交易时段、数据新鲜度和建议状态；所有面向用户的时间均转换为北京时间。

## 安装

> **前置条件：** Python ≥ 3.9

```bash
# 1. 克隆项目
git clone https://github.com/tomcat-cv/MarketAnalyzerAgents.git
cd MarketAnalyzerAgents

# 2. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. 安装项目（editable 模式，方便开发和后续 git pull 更新）
pip install -e .

# 4. 复制配置文件
cp memory/feedback.example.md memory/feedback.md
cp .env.example .env
# 编辑 .env，至少填入 ZHIPU_API_KEY（测试可用 --backend dry-run 不联网）

# 5. 验证安装
marketanalyzeragents doctor

# 6. 试运行（不联网）
marketanalyzeragents run --backend dry-run
```

安装完成后，`marketanalyzeragents` 命令会直接可用，无需手动设置 `PYTHONPATH`。

> **Windows 用户：** 如果遇到时区问题，运行 `pip install tzdata`。

### 依赖管理

项目运行时**零第三方依赖**，仅使用 Python 标准库。依赖文件说明：

| 文件 | 用途 |
|------|------|
| `pyproject.toml` | 包元数据与运行时依赖声明（当前为空） |
| `requirements.txt` | 运行时依赖（当前为空） |
| `requirements-dev.txt` | 开发/测试依赖（pytest 等） |

```bash
# 仅安装运行时依赖（当前为空，但养成习惯）
pip install -r requirements.txt

# 安装开发依赖（包含测试工具）
pip install -r requirements-dev.txt
```

如果将来添加了第三方依赖，只需在 `pyproject.toml` 的 `dependencies` 和 `requirements.txt` 中同步添加即可。

## 快速使用

```bash
# 确保 .venv 已激活
source .venv/bin/activate

# 生成盘前简报（默认智谱后端）
marketanalyzeragents run --backend zhipu

# 仅采集证据，不调用模型
marketanalyzeragents collect

# 渲染 Markdown 简报为 HTML
marketanalyzeragents render briefs/2026-06-09-brief.md

# 按配置定时生成简报
marketanalyzeragents schedule

# 追加反馈
marketanalyzeragents feedback \
  --like "多给我一级来源链接" \
  --dislike "不要泛泛总结融资新闻"
```

## CLI 命令

```bash
# 指定输出格式
marketanalyzeragents run --backend zhipu --format markdown

# 使用 OpenAI 后端
marketanalyzeragents run --backend openai

# 使用 Codex CLI 后端
marketanalyzeragents run --backend codex

# 查看市场会话状态（输出时间均为北京时间）
marketanalyzeragents market-status --market us_equities

# 开盘期间持续获取行情并生成可审计建议
marketanalyzeragents intraday --market us_equities --watch

# 每轮同时采集已验证资讯，并使用模型做判断
marketanalyzeragents intraday --market us_equities \
  --watch --with-news --advice-backend zhipu

# 增加牛熊讨论轮数（支持 1-3 轮）
marketanalyzeragents intraday --market us_equities \
  --watch --with-news --advice-backend zhipu --debate-rounds 2

# 记录用户实际操作
marketanalyzeragents operation --market us_equities \
  --symbol NVDA --action buy --quantity 10 --price 100

# 生成盘后复盘
marketanalyzeragents review --market us_equities
```

### 盘中多 Agent 讨论

显式选择 `zhipu` 或 `openai` 建议后端时，每个标的按以下顺序讨论：

1. 行情分析 Agent：只分析价格、成交量和数据新鲜度。
2. 新闻分析 Agent：只分析已验证资讯和证据缺口。
3. 看多与看空研究 Agent：按配置轮数互相回应。
4. 风险管理 Agent：检查过期数据、证据不足和失效条件。
5. 组合经理 Agent：输出最终结构化建议。

完整发言写入 SQLite 的 `agent_discussions` 表，并通过 conversation outbox 输出。

### 盘中行情数据

盘中行情默认通过 Yahoo Chart 参考接口获取。美股直接使用 `NVDA` 这类代码；A 股裸代码会自动映射为沪市 `.SS`、深市 `.SZ` 或北交所 `.BJ`。

Yahoo 接口无需密钥，但属于非官方、非交易级参考源，可能延迟、限流或变更。

## 配置

| 文件 | 用途 |
|------|------|
| `.env` | API 密钥（ZHIPU_API_KEY、OPENAI_API_KEY 等） |
| `config/settings.json` | 后端、模型、调度、行情数据设置 |
| `config/sources.json` | 重点主题、持仓与采集器配置 |
| `config/research-context.md` | 研究目标和偏好规则 |
| `config/prompt-overrides.md` | 部署时提示词调整 |
| `memory/feedback.md` | 校准反馈（Git 忽略） |

### 调度配置

`config/settings.json` 的 `schedule` 段：

```json
{
  "schedule": {
    "mode": "daily",
    "time": "06:00",
    "interval_minutes": 1440,
    "run_on_start": false
  }
}
```

- `mode=daily`：每天在 `time` 指定的本地时间运行一次。
- `mode=interval`：按 `interval_minutes` 间隔循环运行。
- `run_on_start=true`：服务启动后立即跑一次。

环境变量覆盖（优先级更高）：

```bash
MARKET_ANALYZER_AGENTS_SCHEDULE_MODE=interval
MARKET_ANALYZER_AGENTS_INTERVAL_MINUTES=360
MARKET_ANALYZER_AGENTS_RUN_ON_START=true
```

### 证据等级

- `summary`：有正文摘要，会交给模型摘要、解读，可作为持仓操作判断依据。
- `metadata_only`：只有元数据，展示给读者但不交给模型做操作判断。
- `title_only`：只有标题，仅进入来源日志。

### 持仓配置

在 `config/sources.json` 的 `portfolios` 中配置：

```json
{
  "portfolios": {
    "a_share": { "holdings": [] },
    "us_equities": {
      "holdings": [
        {"ticker": "NVDA", "company": "NVIDIA", "themes": ["AI infrastructure"]}
      ]
    }
  }
}
```

## 当前盘前简报流程

```text
Yahoo 行情 / SEC EDGAR / 市场与政策官方 RSS / 持仓派生公司 RSS / 巨潮资讯
        ↓
独立 collectors 采集
        ↓
按前一日 00:00 至运行时刻过滤、去重
        ↓
Verified Evidence Pack（summary / metadata_only / title_only）
        ↓
仅 summary 级证据交给模型
        ↓
模型返回 evidence_id 摘要 + 持仓操作判断
        ↓
本地校验 ID、引用、数字 → 拼装简报
        ↓
输出 briefs/YYYY-MM-DD-brief.html + source-log.md
```

## 展示与通知

| 路径 | 说明 |
|------|------|
| **HTML 简报** | 输出到 `briefs/`，可通过 `web.brief_base_url` 配置公开访问地址 |
| **飞书群机器人** | 通过 Webhook 推送简报通知、盘中建议和盘后复盘 |

本地仍会追加写入 `state/conversation-outbox.jsonl` 作为审计日志。启用飞书推送：在 `config/settings.json` 填入 Webhook URL，或设置环境变量 `FEISHU_WEBHOOK_URL`。

## 部署

### Docker

容器默认运行 `marketanalyzeragents schedule`，按调度配置生成简报。

```bash
docker build -t market-analyzer-agents .
docker run --rm --env-file .env \
  -v "$PWD/config:/app/config:ro" \
  -v "$PWD/briefs:/app/briefs" \
  -v "$PWD/runs:/app/runs" \
  -v "$PWD/memory:/app/memory" \
  market-analyzer-agents
```

使用 Compose：

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
docker compose logs -f marketanalyzeragents
```

### macOS launchd

```bash
bash scripts/install-launchd.sh
# 自定义时间和后端
MARKET_ANALYZER_AGENTS_HOUR=6 MARKET_ANALYZER_AGENTS_MINUTE=30 \
  MARKET_ANALYZER_AGENTS_BACKEND=zhipu bash scripts/install-launchd.sh
```

### cron

```cron
0 6 * * * cd /path/to/MarketAnalyzerAgents && .venv/bin/marketanalyzeragents run --backend zhipu >> runs/cron.out.log 2>> runs/cron.err.log
```

### HTML 简报预览

```bash
python -m http.server 8000 --directory briefs
```

`briefs/*.html` 可同步到 GitHub Pages、Cloudflare Pages、S3 等任意静态托管。

### 写入 Obsidian

在 `config/settings.json` 或 `.env` 中设置 `OBSIDIAN_VAULT_PATH`。

## 项目结构

```text
.
├── AGENTS.md                       # Codex 项目规则
├── PROJECT_PLAN.md                 # 产品目标、架构边界与交付阶段
├── config/
│   ├── prompt-overrides.md         # 部署时可调整的额外提示词偏好
│   ├── research-context.md         # 研究目标、偏好、过滤规则
│   ├── settings.json               # 后端、模型、路径、时区等配置
│   └── sources.json                # 重点主题、持仓与信源采集器配置
├── memory/
│   ├── feedback.example.md         # 反馈模板
│   └── feedback.md                 # 反馈记忆（Git 忽略）
├── scripts/
│   ├── run-daily-brief.sh          # 运行入口
│   ├── install-launchd.sh          # macOS 定时任务安装
│   └── codex-automation-prompt.md  # Codex Automation 提示词
├── src/marketanalyzeragents/
│   ├── cli.py                      # CLI 入口（run / collect / render / doctor / feedback）
│   ├── collectors.py               # SEC / RSS / 巨潮 / Yahoo 独立采集器
│   ├── evidence.py                 # Evidence Pack、引用校验、简报拼装
│   ├── prompting.py                # 提示词生成
│   ├── zhipu_runner.py             # 智谱 GLM Chat Completions 后端
│   ├── openai_runner.py            # OpenAI Responses API 后端
│   ├── codex_runner.py             # codex exec 后端
│   ├── html_renderer.py            # Markdown → HTML 渲染
│   ├── scheduler.py                # 服务器/容器内的轻量定时循环
│   ├── config.py                   # 配置加载与合并
│   ├── env.py                      # .env 加载
│   ├── feishu_port.py              # 飞书群机器人 Webhook 消息推送
│   ├── agent_debate.py             # 多 Agent 辩论协议
│   ├── intraday.py                 # 盘中行情与建议
│   ├── market_calendar.py          # 市场状态与交易日历
│   ├── portfolio_store.py          # SQLite 持仓持久化
│   ├── review.py                   # 盘后复盘
│   └── writer.py                   # 输出与运行日志
├── Dockerfile                      # 容器化部署入口
├── docker-compose.example.yml      # Docker Compose 示例
├── requirements.txt                # 运行时依赖
├── requirements-dev.txt            # 开发/测试依赖
└── tests/
```

## 参考

- 智谱 GLM-5.1 文档：<https://docs.bigmodel.cn/cn/guide/models/text/glm-5.1>
- 智谱 OpenAI API 兼容：<https://docs.bigmodel.cn/cn/guide/develop/openai/introduction>
- SEC EDGAR API：<https://www.sec.gov/edgar/sec-api-documentation>
- Codex non-interactive mode：<https://developers.openai.com/codex/noninteractive>
