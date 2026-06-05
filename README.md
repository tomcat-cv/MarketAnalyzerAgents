# Daily Research Brief Agent

每日自动采集可靠信源，生成一份证据可溯源的 5 分钟研究简报。支持智谱 GLM-5.1、OpenAI Responses API 和 Codex CLI 后端，通过 CLI、launchd 或 cron 调度运行。

## 项目结构

```text
.
├── AGENTS.md                       # Codex 项目规则
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
├── src/dailyresearch/
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
PYTHONPATH=src python3 -m dailyresearch doctor

# 不联网 dry run，确认提示词和输出路径
PYTHONPATH=src python3 -m dailyresearch run --backend dry-run

# 生成简报
PYTHONPATH=src python3 -m dailyresearch run --backend zhipu

# 按配置定时生成简报
PYTHONPATH=src python3 -m dailyresearch schedule
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
PYTHONPATH=src python3 -m dailyresearch run --backend zhipu

# 指定输出格式
PYTHONPATH=src python3 -m dailyresearch run --backend zhipu --format markdown

# 使用 OpenAI 后端
PYTHONPATH=src python3 -m dailyresearch run --backend openai

# 使用 Codex CLI 后端
PYTHONPATH=src python3 -m dailyresearch run --backend codex

# 仅采集证据，不调用模型
PYTHONPATH=src python3 -m dailyresearch collect

# 把 Markdown 简报渲染成 HTML
PYTHONPATH=src python3 -m dailyresearch render briefs/2026-05-29-brief.md

# 按配置定时运行。默认读取 config/settings.json 的 schedule 段
PYTHONPATH=src python3 -m dailyresearch schedule

# 通过调度入口立即跑一次，适合验证容器命令
PYTHONPATH=src python3 -m dailyresearch schedule --once --backend dry-run

# 追加反馈
PYTHONPATH=src python3 -m dailyresearch feedback \
  --like “多给我一级来源链接” \
  --dislike “不要泛泛总结融资新闻”
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
DAILYRESEARCH_SCHEDULE_MODE=interval
DAILYRESEARCH_INTERVAL_MINUTES=360
DAILYRESEARCH_RUN_ON_START=true
```

提示词偏好在 `config/research-context.md`、`memory/feedback.md` 和
`config/prompt-overrides.md` 中配置。它们只影响关注重点、写作风格和风险表述，
不会作为事实证据使用；事实只能来自 Evidence Pack 中的已采集条目。

## 简报流程

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

容器默认运行 `dailyresearch schedule`，按 `config/settings.json` 或环境变量里的调度配置生成简报。

```bash
docker build -t dailyresearch .
docker run --rm --env-file .env \
  -v "$PWD/config:/app/config:ro" \
  -v "$PWD/briefs:/app/briefs" \
  -v "$PWD/runs:/app/runs" \
  -v "$PWD/memory:/app/memory" \
  dailyresearch
```

使用 Compose：

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
docker compose logs -f dailyresearch
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
DAILYRESEARCH_HOUR=6 DAILYRESEARCH_MINUTE=30 DAILYRESEARCH_BACKEND=codex bash scripts/install-launchd.sh
```

**cron：**

```cron
0 6 * * * cd /path/to/dailyresearch && /bin/bash scripts/run-daily-brief.sh --backend zhipu >> runs/cron.out.log 2>> runs/cron.err.log
```

**Codex Automation：** 使用 `scripts/codex-automation-prompt.md` 作为 prompt。

## 参考

- 智谱 GLM-5.1 文档：<https://docs.bigmodel.cn/cn/guide/models/text/glm-5.1>
- 智谱 OpenAI API 兼容：<https://docs.bigmodel.cn/cn/guide/develop/openai/introduction>
- SEC EDGAR API：<https://www.sec.gov/edgar/sec-api-documentation>
- Codex non-interactive mode：<https://developers.openai.com/codex/noninteractive>
