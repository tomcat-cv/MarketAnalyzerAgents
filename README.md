# Market Analyzer Agents

本地股票交易辅助系统，面向 A 股和美股持仓。当前一期以 HTML 工作台为主界面，支持配置、定时市场分析报告、历史归档和盘中操作建议。

## 核心能力

- 在网页中配置分析模型、持仓、关注 Topic、官方资讯来源和 X/小红书来源；市场情绪指数自动获取并展示在首页。
- 默认按北京时间 08:00、14:00、20:00 生成市场分析报告。
- 首页展示当天最新报告，历史归档页查看过往报告。
- 官方资讯和社媒情绪分开分析；官方资讯保留可读链接。
- 社媒分析支持指定博主，关键词由持仓和关注 Topic 自动派生，并统计积极、消极、中性情绪。
- 市场情绪指数由 VIX、VVIX、CBOE Equity Put/Call、FRED 高收益债利差、S&P 500 趋势、股票/美债相对表现等自动计算；缺失分项会显式标记并按可用权重重算。
- 盘中建议单独展示，结合行情、资讯、社媒和风险环境生成。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

## 常用命令

```bash
marketanalyzeragents web --host 127.0.0.1 --port 8765
marketanalyzeragents report --backend zhipu
marketanalyzeragents report --backend dry-run
marketanalyzeragents suggest --backend zhipu
marketanalyzeragents service
```

## 配置文件

- `config/settings.json`：模型、报告时间、市场状态、行情源等系统配置。
- `config/sources.json`：持仓、关注 Topic、官方来源和社媒来源。
- `config/markets/a_share.json`：A 股市场日历和轮询配置。
- `config/markets/us_equities.json`：美股市场日历和轮询配置。
- `config/calendars/a_share_2026.json`：A 股 2026 休市文件日历。
- `config/calendars/us_equities_2026.json`：美股 2026 休市和提前收盘文件日历。

网页配置只需要维护 X/小红书关注博主列表。社媒关键词由持仓代码、公司名称、持仓主题和关注 Topic 自动生成。平台数据采集是内部扩展点；未配置可用采集方式时只产生 warning，不伪造不可获得的数据。

## 运行数据

- `state/analysis/reports/`：市场分析报告 JSON 和 HTML。
- `state/analysis/suggestions/`：盘中操作建议 JSON。
- Web 工作台默认地址：`http://127.0.0.1:8765`。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover
```
