# Market Analyzer Agents

双市场投资组合智能体：面向 A 股和美股、统一按北京时间呈现的全流程投资组合研究与跟踪程序。

## 当前状态

项目已提供盘前资讯、盘中行情轮询与审计建议、用户操作记录、盘后复盘的基础闭环。实时行情通过可替换 provider 获取，当前内置 Yahoo Finance 参考适配器；其授权、延迟和限流不适合直接视为交易所级实时数据。对话层当前实现 JSONL outbox，后续可替换为正式交互渠道。

A 股和美股按两套独立市场流程设计，各自维护交易日历、交易时段、数据新鲜度和建议状态；所有面向用户的时间均转换为北京时间。美股时间必须基于 `America/New_York` 动态换算，不能固定写死夏令时或冬令时的北京时间。

完整目标、边界和分阶段计划见 [`PROJECT_PLAN.md`](PROJECT_PLAN.md)。

当前盘前模块支持智谱 GLM-5.1、OpenAI Responses API 和 Codex CLI 后端，通过 CLI、launchd 或 cron 调度运行。

产品名、Python 包名和安装后的 CLI 统一为 `Market Analyzer Agents` / `marketanalyzeragents`。

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
│   └── writer.py                   # 输出与运行日志
├── Dockerfile                      # 容器化部署入口
├── docker-compose.example.yml      # Docker Compose 示例
└── tests/
```

## 快速开始

```bash
cp memory/feedback.example.md memory/feedback.md
cp .env.example .env
# 编辑 .env，填入 ZHIPU_API_KEY

# 检查配置
PYTHONPATH=src python3 -m marketanalyzeragents doctor

# 不联网 dry run，确认提示词和输出路径
PYTHONPATH=src python3 -m marketanalyzeragents run --backend dry-run

# 生成简报
PYTHONPATH=src python3 -m marketanalyzeragents run --backend zhipu

# 按配置定时生成简报
PYTHONPATH=src python3 -m marketanalyzeragents schedule
```

`.env` 至少需要：

```bash
ZHIPU_API_KEY=你的智谱API_KEY
ZHIPU_MODEL=glm-5.1
ZHIPU_API_BASE=https://open.bigmodel.cn/api/paas/v4
```

## CLI 命令

```bash
# 生成简报（默认智谱后端）
PYTHONPATH=src python3 -m marketanalyzeragents run --backend zhipu

# 指定输出格式
PYTHONPATH=src python3 -m marketanalyzeragents run --backend zhipu --format markdown

# 使用 OpenAI 后端
PYTHONPATH=src python3 -m marketanalyzeragents run --backend openai

# 使用 Codex CLI 后端
PYTHONPATH=src python3 -m marketanalyzeragents run --backend codex

# 仅采集证据，不调用模型
PYTHONPATH=src python3 -m marketanalyzeragents collect

# 把 Markdown 简报渲染成 HTML
PYTHONPATH=src python3 -m marketanalyzeragents render briefs/2026-05-29-brief.md

# 按配置定时运行。默认读取 config/settings.json 的 schedule 段
PYTHONPATH=src python3 -m marketanalyzeragents schedule

# 通过调度入口立即跑一次，适合验证容器命令
PYTHONPATH=src python3 -m marketanalyzeragents schedule --once --backend dry-run

# 追加反馈
PYTHONPATH=src python3 -m marketanalyzeragents feedback \
  --like “多给我一级来源链接” \
  --dislike “不要泛泛总结融资新闻”

# 查看独立市场会话状态（输出时间均为北京时间）
PYTHONPATH=src python3 -m marketanalyzeragents market-status --market us_equities

# 开盘期间持续获取行情并生成可审计建议
PYTHONPATH=src python3 -m marketanalyzeragents intraday --market us_equities --watch

# 每轮同时采集已验证资讯，并使用模型做判断
PYTHONPATH=src python3 -m marketanalyzeragents intraday --market us_equities \
  --watch --with-news --advice-backend zhipu

# 增加牛熊讨论轮数（支持 1-3 轮）
PYTHONPATH=src python3 -m marketanalyzeragents intraday --market us_equities \
  --watch --with-news --advice-backend zhipu --debate-rounds 2

# 记录用户实际操作
PYTHONPATH=src python3 -m marketanalyzeragents operation --market us_equities \
  --symbol NVDA --action buy --quantity 10 --price 100

# 生成盘后复盘
PYTHONPATH=src python3 -m marketanalyzeragents review --market us_equities
```

### 盘中多 Agent 讨论

显式选择 `zhipu` 或 `openai` 建议后端时，每个标的按以下顺序讨论：

1. 行情分析 Agent：只分析价格、成交量和数据新鲜度。
2. 新闻分析 Agent：只分析已验证资讯和证据缺口。
3. 看多与看空研究 Agent：按配置轮数互相回应。
4. 风险管理 Agent：检查过期数据、证据不足和失效条件。
5. 组合经理 Agent：输出最终结构化建议。

完整发言写入 SQLite 的 `agent_discussions` 表，并通过 conversation outbox
输出。行情 Agent 只接收当前行情和近期价格序列，新闻 Agent 才接收证据，
后续角色同时获得两类分析和持仓配置。默认每个标的最多使用 8 条证据、20
个历史价格点，讨论轮数默认 1，均可在 `config/settings.json` 的
`intraday_agents` 段调整。

该角色分层参考了本地 Apache-2.0 项目 TradingAgents 的分析、辩论和管理者
裁决思路，但当前实现是适配本项目标准库架构的独立轻量协议，不依赖
LangGraph，也未复制其执行图代码。

### 盘中行情数据

盘中行情默认通过 Yahoo Chart 参考接口获取。美股直接使用 `NVDA` 这类代码；
A 股裸代码会自动映射为沪市 `.SS`、深市 `.SZ` 或北交所 `.BJ`。每轮获取最新
`1m` 行情和配置区间的日线 OHLCV，计算区间涨跌、年化波动率和最大回撤，
随后写入 SQLite 并交给盘中 agents。

Yahoo 接口无需密钥，但属于非官方、非交易级参考源，可能延迟、限流或变更。
生产部署应替换为有合法行情权限的供应商。富途 OpenAPI 是同时覆盖 A 股和
美股实时行情、历史 K 线的一种候选，但需要运行 OpenD 并满足行情权限与额度。

```json
"market_data": {
  "provider": "yahoo",
  "history_range": "6mo",
  "history_interval": "1d"
}
```

## 调度与提示词配置

定时频率在 `config/settings.json` 的 `schedule` 段配置：

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
- `run_on_start=true`：服务启动后立即跑一次，然后等待下一次计划时间。

部署时也可以用环境变量覆盖：

```bash
MARKET_ANALYZER_AGENTS_SCHEDULE_MODE=interval
MARKET_ANALYZER_AGENTS_INTERVAL_MINUTES=360
MARKET_ANALYZER_AGENTS_RUN_ON_START=true
```

提示词偏好在 `config/research-context.md`、`memory/feedback.md` 和
`config/prompt-overrides.md` 中配置。它们只影响关注重点、写作风格和风险表述，
不会作为事实证据使用；事实只能来自 Evidence Pack 中的已采集条目。

## 当前盘前简报流程

```text
Yahoo 行情 / SEC EDGAR / 官方 RSS / 巨潮资讯
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

### 配置模型

`config/sources.json` 中有两类用户配置：

- `focus_topics`：重点主题雷达，例如半导体、黄金、白银。半导体这类股票主题可以拆成 A股/美股分组；黄金、白银这类跨资产主题单独展示，不塞进 A股或美股。
- `portfolios`：用户持仓，可以是个股或基金。A股和美股分开配置，持仓简报按市场分组展示。

当前免费部署版本的主题行情主要用 Yahoo Finance 代理：A股半导体 ETF、PHLX Semiconductor Index、COMEX Gold/Silver futures、GLD、SLV。巨潮资讯默认只查询已配置 A股持仓，不再用 `黄金`、`白银`、`业绩预告` 这类泛关键词扫全市场。

### 证据等级

证据分级是为了控制大模型“能用什么、不能用什么”，避免只凭标题或提交记录做过度推断：

- `summary`：有正文摘要、行情快照正文或 filing 正文摘录。会交给模型摘要、解读，并可作为持仓操作判断的依据。
- `metadata_only`：只有 filing 表单号、提交日期、文档描述等元数据，正文没有成功抓取或正文太短。会展示给读者复核，但不交给模型做操作判断。
- `title_only`：只有标题，例如巨潮公告标题或没有摘要的 RSS 条目。默认仅进入来源日志，不交给模型做操作判断。

仅 `summary` 会进入模型请求。如果本期没有 `summary`，程序直接生成待核验简报，不调用模型。

这个分级对当前阶段是合理的：它牺牲了一些覆盖率，换取更低的幻觉和误判风险。后续如果接入可稳定抽取正文的 A股公告、新闻或行情数据源，可以把更多条目升级为 `summary`。

### 简报结构

1. 市场概览
2. 重点主题雷达
3. 持仓简报
4. 持仓操作分析（加仓/减仓/持有/观察）

加仓或减仓必须引用证据，证据不足时只能给低置信度判断。

### 默认启用的采集器

- Yahoo Finance 行情快照：A股/美股大盘、持仓窗口期涨跌和最新价
- SEC EDGAR：按美股持仓查找最新 filings
- Federal Reserve、BLS、EIA 官方 RSS
- NVIDIA、Marvell、Bloom Energy 官方公告 RSS
- 巨潮资讯法定披露平台：按已配置 A股持仓查询公告标题

### 预留采集器（默认关闭）

- Finnhub：配置 `FINNHUB_API_KEY` 并在 `sources.json` 显式启用
- Reuters/LSEG、NewsAPI、NewsData.io、AKShare：商业授权或受限，仅预留

### 持仓配置

持仓在 `config/sources.json` 的 `portfolios` 中配置：

```json
{
  “portfolios”: {
    “a_share”: { “holdings”: [] },
    “us_equities”: {
      “holdings”: [
        {“ticker”: “NVDA”, “company”: “NVIDIA”, “themes”: [“AI infrastructure”]}
      ]
    }
  }
}
```

A股持仓可以保持空数组；美股持仓会进入 SEC 查询和操作分析。

## HTML 简报

HTML 简报是独立文件，内置 CSS，支持响应式表格、移动端阅读、来源链接和打印样式。

```bash
# 本地预览
python3 -m http.server 8000 --directory briefs
```

## 部署

### 本机

需要 Python 3.9+、`.env` 中的 `ZHIPU_API_KEY`。

```bash
bash scripts/run-daily-brief.sh --backend zhipu
```

### Docker

容器默认运行 `marketanalyzeragents schedule`，按 `config/settings.json` 或环境变量里的调度配置生成简报。

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

### 静态站点

`briefs/*.html` 可同步到 GitHub Pages、Cloudflare Pages、S3 等任意静态托管。项目不需要常驻服务。

### 写入 Obsidian

在 `config/settings.json` 或 `.env` 中设置 `OBSIDIAN_VAULT_PATH`。

### 定时调度

**macOS launchd：**

```bash
bash scripts/install-launchd.sh
# 自定义时间和后端
MARKET_ANALYZER_AGENTS_HOUR=6 MARKET_ANALYZER_AGENTS_MINUTE=30 MARKET_ANALYZER_AGENTS_BACKEND=codex bash scripts/install-launchd.sh
```

**cron：**

```cron
0 6 * * * cd /path/to/market-analyzer-agents && /bin/bash scripts/run-daily-brief.sh --backend zhipu >> runs/cron.out.log 2>> runs/cron.err.log
```

**Codex Automation：** 使用 `scripts/codex-automation-prompt.md` 作为 prompt。

## 参考

- 智谱 GLM-5.1 文档：<https://docs.bigmodel.cn/cn/guide/models/text/glm-5.1>
- 智谱 OpenAI API 兼容：<https://docs.bigmodel.cn/cn/guide/develop/openai/introduction>
- SEC EDGAR API：<https://www.sec.gov/edgar/sec-api-documentation>
- Codex non-interactive mode：<https://developers.openai.com/codex/noninteractive>
