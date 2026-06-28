# Market Analyzer Agents

面向 A 股和美股持仓的本地研究与跟踪程序。系统按北京时间展示用户可见时间，同时分别计算 A 股和美股交易状态。

## 核心能力

- 盘前采集可靠信源，生成可追溯证据包和 Markdown 简报。
- 盘中轮询行情，保存 quote/bar，结合已验证资讯输出可审计建议。
- 模型建议采用固定讨论协议：行情分析、新闻分析、看多/看空、风险复核、组合经理。
- 手工记录实际操作，并在盘后用当时可见建议和后续行情生成复盘。
- 建议、操作、讨论和 outbox 事件保存在本地 SQLite/JSONL，便于审计。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

## 常用命令

```bash
marketanalyzeragents run --backend zhipu
marketanalyzeragents run --market a_share --backend zhipu
marketanalyzeragents run --market us_equities --backend zhipu
marketanalyzeragents run --backend dry-run
marketanalyzeragents collect
marketanalyzeragents market-status --market us_equities
marketanalyzeragents intraday --market us_equities --watch
marketanalyzeragents operation --market us_equities --symbol NVDA --action buy --quantity 10 --price 100
marketanalyzeragents review --market us_equities
marketanalyzeragents service --markets a_share us_equities
marketanalyzeragents web --host 127.0.0.1 --port 8765
```

## 市场配置

- `config/markets/a_share.json`：A 股盘前简报、交易日覆盖和盘中轮询配置；默认北京时间 09:00 生成简报。
- `config/markets/us_equities.json`：美股盘前简报、交易日覆盖和盘中轮询配置；默认北京时间 20:00 生成简报。
- `config/sources.json`：共享证据源、公共主题和组合持仓；黄金、白银、宏观等公共主题会被两套市场简报复用。

## 运行数据

- `briefs/`：盘前 Markdown 简报和 source log。
- `runs/`：每次运行的证据、prompt、模型响应和校验记录。
- `state/portfolio.db`：quotes、price bars、suggestions、operations、agent discussions。
- `state/conversation-outbox.jsonl`：传输中立事件 outbox。
- Web 工作台：默认 `http://127.0.0.1:8765`，提供持仓配置、盘前简报入口和盘中提醒流。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover
```
