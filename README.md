# Daily Research Brief Agent

这是一个不使用 Claude、也不使用 n8n 的每日研究简报项目。目标是复刻原文里的“每天早上自动读互联网，然后给你一份 5 分钟简报”的工作流，但把编排换成本地 CLI、Codex/Codex Automation 或 macOS `launchd`。

## 原文拆解

原文方案的本质是 6 个模块：

1. **研究上下文**：把你的关注方向、偏好、过滤规则写进长期上下文。
2. **定时触发**：每天早上自动运行。
3. **可靠信源采集**：程序直接读取官方 API、官方 RSS 和法定披露平台。
4. **LLM 归纳**：去重、判断信号、生成简报。
5. **知识库落盘**：写入 Obsidian 或本地 Markdown。
6. **反馈校准**：把你喜欢/不喜欢的输出写回记忆，下次继续用。

本项目的替换关系：

| 原文模块 | 本项目替代 |
| --- | --- |
| Claude / Claude Project | 智谱 GLM-5.1、OpenAI Responses API 或 Codex CLI |
| CLAUDE.md | `AGENTS.md` + `config/research-context.md` |
| n8n workflow | `dailyresearch` CLI + Codex Automation / launchd |
| Filesystem MCP | 本地 Python 文件读写 |
| Brave Search MCP | 独立 collectors + Verified Evidence Pack |
| Obsidian output | `briefs/` 或你的 Obsidian vault 路径 |

## 项目结构

```text
.
├── AGENTS.md                       # 给 Codex 的项目规则
├── config/
│   ├── research-context.md         # 你的研究目标、偏好、过滤规则
│   ├── settings.json               # 后端、模型、路径、时区等配置
│   └── sources.json                # 关注范围和可靠信源采集器配置
├── memory/
│   ├── feedback.example.md         # 可提交的反馈模板
│   └── feedback.md                 # 本地反馈记忆，Git 默认忽略
├── scripts/
│   ├── run-daily-brief.sh          # 运行入口
│   ├── install-launchd.sh          # macOS 每日定时任务安装
│   └── codex-automation-prompt.md  # Codex Automation 提示词
├── src/dailyresearch/
│   ├── cli.py                      # CLI
│   ├── collectors.py               # SEC/RSS/巨潮独立采集器
│   ├── evidence.py                 # Evidence Pack 和引用校验
│   ├── zhipu_runner.py             # 智谱 GLM Chat Completions 后端
│   ├── openai_runner.py            # OpenAI Responses API 后端
│   ├── codex_runner.py             # codex exec 后端
│   ├── prompting.py                # 简报提示词生成
│   └── writer.py                   # 输出和运行日志
└── tests/
```

## 快速开始

先做一次不联网的 dry run，确认提示词和输出路径。默认输出格式是 HTML：

```bash
cp memory/feedback.example.md memory/feedback.md
PYTHONPATH=src python3 -m dailyresearch doctor
PYTHONPATH=src python3 -m dailyresearch run --backend dry-run
```

使用默认的智谱 GLM-5.1 后端：

```bash
cp .env.example .env
# 编辑 .env，填入 ZHIPU_API_KEY
PYTHONPATH=src python3 -m dailyresearch run --backend zhipu
```

`.env` 至少需要：

```bash
ZHIPU_API_KEY=你的智谱API_KEY
ZHIPU_MODEL=glm-5.1
ZHIPU_API_BASE=https://open.bigmodel.cn/api/paas/v4
```

智谱后端只负责受控的证据摘要和持仓操作分析，不使用模型托管的 `web_search`。
实时资料由独立采集器提供。

## 可靠信源流程

当前主流程是：

```text
Yahoo 行情快照 / SEC EDGAR / 官方 RSS / 巨潮资讯
        ↓
独立 collectors 采集
        ↓
按“前一日 00:00 至实际运行时刻”过滤、来源校验、去重
        ↓
Verified Evidence Pack
        ↓
仅把 summary 级证据交给 GLM-5.1
        ↓
GLM 返回 evidence_id 摘要 + 每个持仓的受控操作判断 JSON
        ↓
本地校验 ID、拼装来源链接与复核队列
        ↓
按A股市场、美股市场、持仓资讯与操作分析拼装简报，并单独输出采集日志
```

模型 runner 中没有 `web_search` 或其他联网工具接口。真正会被程序访问的
来源必须明确配置在 `config/sources.json` 的 `collectors` 下。

当前默认启用：

- Yahoo Finance 行情快照：补足A股/美股大盘、半导体方向，以及已配置美股持仓的窗口期涨跌和最新价；链接指向本次 chart API 数据，定位为补充行情聚合源，不替代官方披露
- SEC EDGAR submissions API + filing 正文：按 `portfolios.us_equities.holdings` 查找最新 filings，正文成功获取时可进入摘要与操作分析
- Federal Reserve、BLS、EIA 官方 RSS
- NVIDIA、Marvell、Bloom Energy 官方公告/投资者关系 RSS
- 巨潮资讯法定披露平台：覆盖A股市场事件、重点方向关键词，以及未来配置的A股持仓代码

默认不调用、仅预留或等待密钥启用：

- Finnhub：适配器已实现，默认关闭；配置 `FINNHUB_API_KEY` 并显式启用后，仅接收配置的可信发布方
- Reuters/LSEG：商业授权接口预留
- NewsAPI、NewsData.io：免费方案延迟或生产限制较大，仅预留
- AKShare：作为A股便利/回退适配器预留，不作为法定披露源
- 上交所、深交所独立公告适配器：官方入口优先，但当前未启用未经确认的非稳定接口，巨潮保持启用

证据分为三个等级：

- `summary`：来源提供了可总结的正文摘要，允许交给大模型归纳
- `metadata_only`：SEC filing 正文未能获取时只有提交元数据，只进入人工复核队列
- `title_only`：只有公告标题，只进入人工复核队列

每天早上运行时，默认信息窗口为 `Asia/Shanghai` 时区前一日 `00:00` 至实际运行
时刻，而不是简单回看最近 24 小时。只有 `summary` 会进入 GLM/OpenAI 请求；
标题或元数据仍会归入市场/持仓资讯章节，但不会参与操作分析。如果本期只有标题
或元数据，程序会直接生成带“观察”结论的待核验简报，不调用大模型。

GLM/OpenAI 不直接写完整简报，只返回每条 `summary` 证据的结构化压缩摘要、阅读友好
解读，以及每个配置持仓的 `加仓/减仓/持有/观察` 判断。本地代码验证 evidence ID、
持仓代码、操作枚举和数字依据后，确定性地拼装以下三类内容：

1. 市场总体资讯（可靠信源），内部再分A股整体/重点方向与美股整体/宏观驱动
2. 持仓公司相关资讯（可靠信源）
3. 根据市场动态分析持仓应该作何操作

模型无法自行添加来源；加仓或减仓必须引用证据，证据不足时只能给低置信度判断。

单独测试采集器，不调用模型：

```bash
PYTHONPATH=src python3 -m dailyresearch collect
```

Evidence Pack 会保留每条证据的标题、时间、来源类型、原始 URL 和匹配主题。
如果一条证据都没有采集到，正式生成流程会直接失败，不允许模型凭空写简报。
如果模型引用了 Evidence Pack 以外的 URL，流程也会失败并把错误写进 `runs/`。
采集失败不会静默忽略，会记录在 Evidence Pack 的 `errors` 字段中。Evidence Pack
还会记录每个采集器的 `coverage` 状态，区分已采集、已查询无条目、未启用、缺少
密钥、仅预留和采集失败，避免把“没有抓到”误读为“没有发生”。

持仓配置位于：

```json
{
  "portfolios": {
    "a_share": {
      "holdings": []
    },
    "us_equities": {
      "holdings": [
        {"ticker": "NVDA", "company": "NVIDIA", "themes": ["AI infrastructure"]}
      ]
    }
  }
}
```

A股持仓可以保持空数组；美股持仓会同时进入 SEC/Finnhub 公司级查询和操作分析。

简报输出会写到：

```text
briefs/YYYY-MM-DD-brief.html
```

采集覆盖、证据等级和来源明细会单独写到同目录：

```text
briefs/YYYY-MM-DD-brief-source-log.md
```

如果你想临时输出 Markdown：

```bash
PYTHONPATH=src python3 -m dailyresearch run --backend zhipu --format markdown
```

仍然可以显式使用 OpenAI 后端：

```bash
PYTHONPATH=src python3 -m dailyresearch run --backend openai
```

把已有 Markdown 简报渲染成 HTML：

```bash
PYTHONPATH=src python3 -m dailyresearch render briefs/2026-05-29-brief.md
```

使用 Codex CLI 后端：

```bash
PYTHONPATH=src python3 -m dailyresearch run --backend codex
```

Codex 后端会生成严格的分类简报任务提示词，然后调用 `codex exec` 在当前仓库中
完成证据摘要、受控持仓分析和落盘；它同样禁止浏览、搜索和无证据外推断。

## HTML 简报

HTML 简报是一个独立文件，内置 CSS，不依赖前端构建步骤。它支持：

- 响应式表格和移动端阅读
- 来源链接和 Source Log
- Markdown 图片语法，例如 `![caption](https://example.com/chart.png)`
- 打印样式

配置项在 `config/settings.json`：

```json
{
  "output_format": "html",
  "output_dir": "briefs"
}
```

本地预览可以直接打开 HTML 文件，也可以起一个静态服务：

```bash
python3 -m http.server 8000 --directory briefs
```

然后访问 <http://localhost:8000/>。

## 部署方式

### 本机部署

适合个人每日简报。需要：

1. Python 3.9+
2. 项目根目录 `.env` 中配置 `ZHIPU_API_KEY`
3. 根据你的关注方向维护 `config/research-context.md` 和 `config/sources.json`

手动运行：

```bash
cd /path/to/dailyresearch
bash scripts/run-daily-brief.sh --backend zhipu
```

### 静态站点部署

`briefs/*.html` 是静态文件，可以同步到任意静态托管位置，例如 Nginx 静态目录、GitHub Pages、Cloudflare Pages、S3 + CloudFront，或 Obsidian/本地文件夹。项目本身不需要常驻服务；只要定时任务生成新 HTML，再同步 `briefs/` 即可。

### 服务器部署

在一台 Linux/macOS 服务器上 clone 项目，创建 `.env`，然后用 cron、systemd timer、launchd 或 Codex Automation 调度 `bash scripts/run-daily-brief.sh --backend zhipu`。如果要发布到 Web，把 `briefs/` 暴露为静态目录。

## 写入 Obsidian

如果你想把简报直接写进 Obsidian vault，在 `.env` 里设置：

```bash
OBSIDIAN_VAULT_PATH=/Users/you/Documents/Obsidian/Vault
```

也可以在 `config/settings.json` 里改：

```json
{
  "obsidian": {
    "vault_path": "/Users/you/Documents/Obsidian/Vault",
    "note_dir": "Daily Research"
  }
}
```

## 定时运行

### 方式 A：Codex Automation

在 Codex 里创建一个每日 automation，工作目录指向这个仓库，prompt 使用：

```text
scripts/codex-automation-prompt.md
```

这条路线最接近“使用 Codex 实现，不要 n8n”：Codex 负责调度、执行、读写文件和汇报结果。

### 方式 B：macOS launchd

每天 06:00 运行智谱 GLM-5.1 后端，生成 HTML 简报：

```bash
bash scripts/install-launchd.sh
```

改成每天 06:30，且使用 Codex 后端：

```bash
DAILYRESEARCH_HOUR=6 DAILYRESEARCH_MINUTE=30 DAILYRESEARCH_BACKEND=codex bash scripts/install-launchd.sh
```

日志会写到 `runs/launchd.out.log` 和 `runs/launchd.err.log`。

手动触发一次 launchd 任务：

```bash
launchctl kickstart -k "gui/$(id -u)/com.dailyresearch.brief"
```

卸载定时任务：

```bash
launchctl unload "$HOME/Library/LaunchAgents/com.dailyresearch.brief.plist"
rm "$HOME/Library/LaunchAgents/com.dailyresearch.brief.plist"
```

### 方式 C：cron

Linux 或 NAS 上可以用 cron。示例：每天 06:00 运行：

```cron
0 6 * * * cd /path/to/dailyresearch && /bin/bash scripts/run-daily-brief.sh --backend zhipu >> runs/cron.out.log 2>> runs/cron.err.log
```

## 反馈闭环

把你的反馈写入 `memory/feedback.md`，或者用命令追加：

```bash
PYTHONPATH=src python3 -m dailyresearch feedback \
  --like "多给我一级来源链接" \
  --dislike "不要泛泛总结融资新闻" \
  --correction "AI agent 方向优先看开发者 adoption，而不是融资金额"
```

下次运行时，反馈会自动进入提示词。

## 后续路线

当前 MVP 已完成：

- 本地配置和研究上下文
- 独立可靠信源采集器和 Verified Evidence Pack
- 智谱 GLM-5.1 证据摘要与受控持仓分析后端
- OpenAI Responses API 后端
- Codex CLI 后端
- HTML / Markdown 输出到本地或 Obsidian
- prompt/run/response 日志
- 反馈记忆
- launchd 调度脚本
- 基础单元测试

建议下一步：

- 增加更多官方 RSS/网页抓取适配器，提升正文级证据覆盖率
- 增加去重缓存，避免连续几天重复同一条新闻
- 增加确定性评分步骤：相关性、可信度、新鲜度
- 输出周报，把每日 brief 聚合成趋势复盘
- 加一个小型 Web UI，用于编辑 sources、反馈和查看历史简报

## 参考

- 原文镜像：<https://www.xrticles.com/article/how-to-build-a-claude-research-agent-that-reads-the-internet-every-morning-and-briefs-you-in-5-mins>
- 智谱 GLM-5.1 文档：<https://docs.bigmodel.cn/cn/guide/models/text/glm-5.1>
- 智谱 OpenAI API 兼容：<https://docs.bigmodel.cn/cn/guide/develop/openai/introduction>
- SEC EDGAR API：<https://www.sec.gov/edgar/sec-api-documentation>
- BLS RSS：<https://www.bls.gov/feed/>
- Codex non-interactive mode：<https://developers.openai.com/codex/noninteractive>
- Codex automations：<https://openai.com/academy/codex-automations>
