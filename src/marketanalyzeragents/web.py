from __future__ import annotations

import json
import mimetypes
import sqlite3
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .config import find_project_root, load_json, load_market_settings, load_settings, resolve_path
from .evidence import configured_focus_topics, configured_portfolio_holdings
from .market_calendar import market_status
from .writer import markdown_to_html, write_json


MARKETS = ("a_share", "us_equities")


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Market Analyzer Workbench</title>
  <style>
    :root {
      --ink: #182026;
      --muted: #65727a;
      --line: #d4ded9;
      --paper: #eef3ef;
      --panel: #ffffff;
      --panel-2: #edf3ef;
      --black: #192227;
      --green: #1f7a4d;
      --red: #a33b36;
      --amber: #9b6a18;
      --blue: #245c85;
      --teal: #0b6d70;
      --shadow: 0 16px 36px rgba(24, 32, 38, .09);
      --soft-shadow: 0 8px 18px rgba(24, 32, 38, .06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Avenir Next", "DIN Alternate", "PingFang SC", "Hiragino Sans GB", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(36, 92, 133, .05) 1px, transparent 1px),
        linear-gradient(rgba(11, 109, 112, .045) 1px, transparent 1px),
        radial-gradient(circle at 12% 0%, rgba(31, 122, 77, .1), transparent 24%),
        radial-gradient(circle at 88% 10%, rgba(36, 92, 133, .11), transparent 28%),
        var(--paper);
      background-size: 32px 32px;
    }
    header {
      display: grid;
      grid-template-columns: minmax(240px, .9fr) minmax(360px, 1.2fr);
      gap: 18px;
      align-items: end;
      padding: 22px clamp(16px, 3vw, 36px) 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(242, 245, 243, .94);
      position: sticky;
      top: 0;
      z-index: 10;
      backdrop-filter: blur(12px);
    }
    h1 { margin: 0; font-size: 28px; line-height: 1; letter-spacing: 0; }
    h2 { margin: 0; font-size: 15px; letter-spacing: 0; }
    .subtle { color: var(--muted); font-size: 13px; }
    .tabs {
      display: flex;
      gap: 8px;
      padding: 14px clamp(16px, 3vw, 36px) 0;
    }
    .tab-button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      padding: 9px 14px;
      border-radius: 999px;
    }
    .tab-button.active {
      border-color: var(--black);
      background: var(--black);
      color: #fff;
    }
    .status-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .market-status {
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 12px;
      min-height: 72px;
      box-shadow: var(--shadow);
      border-radius: 8px;
    }
    .market-status strong { display: block; font-size: 13px; text-transform: uppercase; }
    .state { margin-top: 8px; font-size: 20px; font-weight: 800; }
    .state.open { color: var(--green); }
    .state.closed, .state.post_market { color: var(--muted); }
    .state.pre_market, .state.break { color: var(--amber); }
    main { padding: 16px clamp(16px, 3vw, 36px) 36px; }
    .overview-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, .92);
      padding: 14px;
      min-height: 92px;
      border-radius: 8px;
      box-shadow: var(--soft-shadow);
    }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 8px; font-size: 24px; line-height: 1; }
    .metric small { display: block; margin-top: 8px; color: var(--muted); font-size: 12px; }
    .home-grid, .config-grid {
      display: grid;
      grid-template-columns: minmax(420px, 1.35fr) minmax(320px, .85fr);
      gap: 18px;
      align-items: start;
    }
    .config-grid { grid-template-columns: minmax(360px, .95fr) minmax(420px, 1.05fr); }
    section {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, .96);
      box-shadow: var(--shadow);
      margin-bottom: 18px;
      border-radius: 8px;
      overflow: hidden;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-2);
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 11px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { text-align: left; color: var(--muted); font-weight: 800; background: #f8faf9; }
    tr:last-child td { border-bottom: 0; }
    .ticker { font-size: 15px; font-weight: 800; color: var(--black); }
    .card-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 12px; }
    .holding-card, .topic-card {
      border: 1px solid var(--line);
      background: #fbfdfc;
      border-radius: 8px;
      padding: 12px;
      min-width: 0;
      box-shadow: var(--soft-shadow);
    }
    .card-top {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
      margin-bottom: 10px;
    }
    .market-badge {
      border: 1px solid #bdd0cc;
      background: #eff6f3;
      color: var(--teal);
      padding: 3px 7px;
      font-size: 12px;
      white-space: nowrap;
    }
    .quote-line {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      padding: 9px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      margin-bottom: 9px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border: 1px solid #b8c9c4;
      background: #f7fbfa;
      font-size: 12px;
      margin: 2px 4px 2px 0;
    }
    .price { font-variant-numeric: tabular-nums; font-weight: 800; }
    .topic-meta { display: grid; gap: 8px; }
    .topic-meta strong { font-size: 12px; color: var(--muted); }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 14px; }
    .wide { grid-column: 1 / -1; }
    label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      background: #fbfdfc;
      color: var(--ink);
      padding: 9px 10px;
      font: inherit;
      border-radius: 0;
    }
    textarea { min-height: 78px; resize: vertical; }
    .actions { display: flex; gap: 8px; justify-content: flex-end; padding: 0 14px 14px; }
    button, .button-link {
      border: 1px solid var(--black);
      background: var(--black);
      color: #fff;
      padding: 8px 11px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
      text-decoration: none;
    }
    button.secondary, .button-link.secondary { background: transparent; color: var(--black); }
    button.danger { border-color: var(--red); background: transparent; color: var(--red); padding: 5px 8px; }
    button.ghost { border-color: var(--line); background: transparent; color: var(--teal); padding: 5px 8px; }
    .inline-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .alerts { display: grid; gap: 10px; padding: 12px; }
    .alert {
      border-left: 4px solid var(--blue);
      background: #fbfdfc;
      padding: 11px 12px;
      border-top: 1px solid var(--line);
      border-right: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }
    .alert.high, .alert.buy, .alert.sell, .alert.加仓, .alert.减仓 { border-left-color: var(--red); }
    .alert .meta { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .alert .title { font-weight: 800; margin-bottom: 6px; }
    .alert p { margin: 0; line-height: 1.45; }
    .brief-list { display: grid; gap: 1px; background: var(--line); }
    .brief-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 11px 12px;
      color: var(--ink);
      text-decoration: none;
      background: #fbfdfc;
    }
    .brief-row:hover { background: #edf6f2; }
    .empty { padding: 18px 14px; color: var(--muted); }
    .toast {
      position: fixed;
      right: 20px;
      bottom: 20px;
      background: var(--black);
      color: #fff;
      padding: 10px 12px;
      opacity: 0;
      transform: translateY(8px);
      transition: opacity .18s ease, transform .18s ease;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    [hidden] { display: none !important; }
    @media (max-width: 960px) {
      header, .home-grid, .config-grid, .overview-grid, .card-list { grid-template-columns: 1fr; }
      .status-strip, .form-grid { grid-template-columns: 1fr; }
      .tabs { overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Market Analyzer</h1>
      <div class="subtle" id="clock">正在连接本地工作台</div>
    </div>
    <div class="status-strip" id="market-status"></div>
  </header>
  <nav class="tabs" aria-label="主页面">
    <button class="tab-button active" data-tab="home" type="button">首页</button>
    <button class="tab-button" data-tab="config" type="button">配置</button>
  </nav>
  <main>
    <div class="tab-panel" id="tab-home">
      <div class="overview-grid" id="overview"></div>
      <div class="home-grid">
        <div>
          <section>
            <div class="section-head">
              <h2>持仓概览</h2>
              <span class="subtle" id="holding-count"></span>
            </div>
            <div id="holdings"></div>
          </section>
          <section>
            <div class="section-head">
              <h2>关注 Topic</h2>
              <span class="subtle" id="topic-count"></span>
            </div>
            <div id="topics"></div>
          </section>
        </div>
        <aside>
          <section>
            <div class="section-head">
              <h2>盘中提醒</h2>
              <span class="subtle" id="alert-count"></span>
            </div>
            <div class="alerts" id="alerts"></div>
          </section>
          <section>
            <div class="section-head">
              <h2>盘前简报</h2>
              <a class="button-link secondary" href="/briefs/" target="_blank">打开目录</a>
            </div>
            <div class="brief-list" id="briefs"></div>
          </section>
        </aside>
      </div>
    </div>
    <div class="tab-panel" id="tab-config" hidden>
      <div class="config-grid">
        <div>
          <section>
            <div class="section-head">
              <h2>分析模型</h2>
              <span class="subtle">config/settings.json</span>
            </div>
            <form id="model-form">
              <div class="form-grid">
                <div>
                  <label>盘前后端</label>
                  <select name="backend">
                    <option value="zhipu">智谱</option>
                    <option value="openai">OpenAI</option>
                    <option value="dry-run">Dry run</option>
                  </select>
                </div>
                <div>
                  <label>当前模型</label>
                  <input name="model" placeholder="glm-5.1 / gpt-5.4">
                </div>
                <div>
                  <label>智谱模型</label>
                  <input name="zhipu_model" placeholder="glm-5.1">
                </div>
                <div>
                  <label>OpenAI 模型</label>
                  <input name="openai_model" placeholder="gpt-5.4">
                </div>
                <div>
                  <label>盘中分析后端</label>
                  <select name="advice_backend">
                    <option value="conservative">保守规则</option>
                    <option value="zhipu">智谱</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </div>
                <div>
                  <label>讨论轮数</label>
                  <input name="debate_rounds" type="number" min="1" max="3" step="1">
                </div>
              </div>
              <div class="actions">
                <button type="submit">保存模型</button>
              </div>
            </form>
          </section>
          <section>
            <div class="section-head">
              <h2>配置持仓</h2>
              <span class="subtle">config/sources.json</span>
            </div>
            <form id="holding-form">
              <div class="form-grid">
                <div>
                  <label>市场</label>
                  <select name="market">
                    <option value="us_equities">美股</option>
                    <option value="a_share">A 股</option>
                  </select>
                </div>
                <div>
                  <label>代码</label>
                  <input name="ticker" placeholder="NVDA / 600519" required>
                </div>
                <div>
                  <label>行情代码</label>
                  <input name="symbol" placeholder="留空默认等于代码">
                </div>
                <div>
                  <label>名称</label>
                  <input name="company" placeholder="NVIDIA">
                </div>
                <div class="wide">
                  <label>关注主题</label>
                  <textarea name="themes" placeholder="AI accelerators&#10;data-center revenue"></textarea>
                </div>
              </div>
              <div class="actions">
                <button class="secondary" type="reset">清空</button>
                <button type="submit">保存持仓</button>
              </div>
            </form>
          </section>
          <section>
            <div class="section-head">
              <h2>持仓列表</h2>
              <span class="subtle" id="holding-config-count"></span>
            </div>
            <div id="holding-config-list"></div>
          </section>
        </div>
        <div>
          <section>
            <div class="section-head">
              <h2>配置 Topic</h2>
              <span class="subtle">focus_topics</span>
            </div>
            <form id="topic-form">
              <div class="form-grid">
                <div>
                  <label>ID</label>
                  <input name="id" placeholder="semiconductors" required>
                </div>
                <div>
                  <label>名称</label>
                  <input name="name" placeholder="半导体">
                </div>
                <div class="wide">
                  <label>分组</label>
                  <textarea name="segments" placeholder="美股半导体 | 主题:半导体:美股&#10;A股半导体 | 主题:半导体:A股"></textarea>
                </div>
                <div class="wide">
                  <label>跟踪工具</label>
                  <textarea name="instruments" placeholder="^SOX | PHLX Semiconductor Index | 主题:半导体:美股&#10;512480.SS | A股半导体ETF代理 | 主题:半导体:A股"></textarea>
                </div>
              </div>
              <div class="actions">
                <button class="secondary" type="reset">清空</button>
                <button type="submit">保存 Topic</button>
              </div>
            </form>
          </section>
          <section>
            <div class="section-head">
              <h2>Topic 列表</h2>
              <span class="subtle" id="topic-config-count"></span>
            </div>
            <div id="topic-config-list"></div>
          </section>
        </div>
      </div>
    </div>
  </main>
  <div class="toast" id="toast"></div>
  <script>
    const state = { data: null, configDirty: false };
    const fmt = value => value || "";
    const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    }[char]));
    const marketLabel = market => market === "us_equities" ? "美股" : "A 股";
    const stateLabel = value => ({
      open: "开盘",
      closed: "休市",
      pre_market: "盘前",
      post_market: "盘后",
      overnight: "夜盘",
      overnight_break: "夜盘休整",
      break: "午间休市"
    }[value] || value);

    function toast(text) {
      const el = document.getElementById("toast");
      el.textContent = text;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 1800);
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "request failed");
      return data;
    }

    function openTab(name) {
      document.querySelectorAll(".tab-panel").forEach(panel => {
        panel.hidden = panel.id !== `tab-${name}`;
      });
      document.querySelectorAll(".tab-button").forEach(button => {
        button.classList.toggle("active", button.dataset.tab === name);
      });
    }

    function renderMarketStatus(markets) {
      const root = document.getElementById("market-status");
      root.innerHTML = Object.entries(markets).map(([market, item]) => `
        <div class="market-status">
          <strong>${market === "us_equities" ? "US Equities" : "A Share"}</strong>
          <div class="state ${esc(item.state)}">${esc(stateLabel(item.state))}</div>
          <div class="subtle">北京时间 ${esc(fmt(item.as_of_beijing).replace("T", " ").slice(0, 19))}</div>
        </div>
      `).join("");
    }

    function renderOverview(data) {
      const markets = Object.entries(data.markets || {});
      const openMarkets = markets.filter(([, item]) => item.state === "open").map(([market]) => marketLabel(market));
      const quotedHoldings = (data.holdings || []).filter(item => item.quote).length;
      const latestBrief = (data.briefs || [])[0];
      document.getElementById("overview").innerHTML = `
        <div class="metric"><span>交易状态</span><strong>${esc(openMarkets.length ? openMarkets.join(" / ") : "休市")}</strong><small>${esc(markets.map(([market, item]) => `${marketLabel(market)} ${stateLabel(item.state)}`).join(" · "))}</small></div>
        <div class="metric"><span>组合覆盖</span><strong>${esc((data.holdings || []).length)}</strong><small>${quotedHoldings} 个已有最近行情</small></div>
        <div class="metric"><span>盘中提醒</span><strong>${esc((data.notifications || []).length)}</strong><small>来自建议与 outbox 事件</small></div>
        <div class="metric"><span>最新简报</span><strong>${latestBrief ? esc(latestBrief.name) : "暂无"}</strong><small>${latestBrief ? esc(latestBrief.modified_at.replace("T", " ").slice(0, 16)) : "briefs 目录为空"}</small></div>
      `;
    }

    function renderHoldings(holdings) {
      document.getElementById("holding-count").textContent = `${holdings.length} 个标的`;
      const root = document.getElementById("holdings");
      if (!holdings.length) {
        root.innerHTML = `<div class="empty">还没有配置持仓。</div>`;
        return;
      }
      root.innerHTML = `<div class="card-list">${holdings.map(h => `
        <article class="holding-card">
          <div class="card-top">
            <div><div class="ticker">${esc(h.ticker)}</div><div class="subtle">${esc(fmt(h.company))}</div></div>
            <span class="market-badge">${marketLabel(h.market)}</span>
          </div>
          <div class="quote-line">
            <span class="subtle">最新行情</span>
            ${h.quote ? `<span><span class="price">${esc(h.quote.price)}</span><br><span class="subtle">${esc(fmt(h.quote.observed_at).replace("T", " ").slice(0, 19))}</span></span>` : `<span class="subtle">暂无</span>`}
          </div>
          <div>${(h.themes || []).map(t => `<span class="pill">${esc(t)}</span>`).join("") || `<span class="subtle">未设置主题</span>`}</div>
        </article>
      `).join("")}</div>`;
    }

    function renderHoldingConfig(holdings) {
      document.getElementById("holding-config-count").textContent = `${holdings.length} 个`;
      const root = document.getElementById("holding-config-list");
      if (!holdings.length) {
        root.innerHTML = `<div class="empty">还没有配置持仓。</div>`;
        return;
      }
      root.innerHTML = `<table>
        <thead><tr><th>标的</th><th>市场</th><th>行情代码</th><th>关注主题</th><th></th></tr></thead>
        <tbody>${holdings.map(h => `
          <tr>
            <td><div class="ticker">${esc(h.ticker)}</div><div class="subtle">${esc(fmt(h.company))}</div></td>
            <td>${marketLabel(h.market)}</td>
            <td>${esc(h.symbol || h.ticker)}</td>
            <td>${(h.themes || []).map(t => `<span class="pill">${esc(t)}</span>`).join("") || `<span class="subtle">未设置</span>`}</td>
            <td><div class="inline-actions"><button class="ghost" data-edit-holding="${esc(h.market)}:${esc(h.ticker)}">编辑</button><button class="danger" data-delete="${esc(h.market)}:${esc(h.ticker)}">删除</button></div></td>
          </tr>
        `).join("")}</tbody>
      </table>`;
      root.querySelectorAll("[data-delete]").forEach(button => {
        button.addEventListener("click", async () => {
          const [market, ticker] = button.dataset.delete.split(":");
          await postJson("/api/holdings/delete", {market, ticker});
          toast("持仓已删除");
          await refresh();
        });
      });
      root.querySelectorAll("[data-edit-holding]").forEach(button => {
        button.addEventListener("click", () => {
          const [market, ticker] = button.dataset.editHolding.split(":");
          const holding = (state.data.holdings || []).find(item => item.market === market && item.ticker === ticker);
          if (holding) fillHoldingForm(holding);
          openTab("config");
        });
      });
    }

    function renderAlerts(alerts) {
      document.getElementById("alert-count").textContent = `${alerts.length} 条`;
      const root = document.getElementById("alerts");
      if (!alerts.length) {
        root.innerHTML = `<div class="empty">暂无需要打扰你的盘中事件。</div>`;
        return;
      }
      root.innerHTML = alerts.map(a => {
        const klass = `${a.confidence || ""} ${a.action || ""}`.replaceAll("低", "").replaceAll("中", "");
        return `<article class="alert ${esc(klass)}">
          <div class="meta">${esc(fmt(a.created_at || a.generated_at).replace("T", " ").slice(0, 19))} · ${esc(fmt(a.market))} ${esc(fmt(a.symbol))}</div>
          <div class="title">${esc(fmt(a.action || a.type || "提醒"))}${a.confidence ? " / " + esc(a.confidence) : ""}</div>
          <p>${esc(fmt(a.rationale || a.output || a.message || ""))}</p>
        </article>`;
      }).join("");
    }

    function renderBriefs(briefs) {
      const root = document.getElementById("briefs");
      if (!briefs.length) {
        root.innerHTML = `<div class="empty">还没有生成盘前简报。</div>`;
        return;
      }
      root.innerHTML = briefs.map(b => `
        <a class="brief-row" href="${esc(b.url)}" target="_blank">
          <span><strong>${esc(b.name)}</strong><br><span class="subtle">${esc(b.market || "shared")}</span></span>
          <span class="subtle">${esc(b.modified_at.replace("T", " ").slice(0, 19))}</span>
        </a>
      `).join("");
    }

    function renderTopics(topics) {
      document.getElementById("topic-count").textContent = `${topics.length} 个 topic`;
      const root = document.getElementById("topics");
      if (!topics.length) {
        root.innerHTML = `<div class="empty">还没有配置关注 topic。</div>`;
        return;
      }
      root.innerHTML = `<div class="card-list">${topics.map(topic => `
        <article class="topic-card">
          <div class="card-top">
            <div><div class="ticker">${esc(topic.name)}</div><div class="subtle">${esc(topic.id)}</div></div>
            <span class="market-badge">${esc((topic.instruments || []).length)} 工具</span>
          </div>
          <div class="topic-meta">
            <div><strong>主题标签</strong><br>${(topic.segments || []).flatMap(s => s.topics || []).map(t => `<span class="pill">${esc(t)}</span>`).join("") || `<span class="subtle">未设置</span>`}</div>
            <div><strong>跟踪工具</strong><br>${(topic.instruments || []).map(i => `<span class="pill">${esc(i.symbol)}</span>`).join("") || `<span class="subtle">未设置</span>`}</div>
          </div>
        </article>
      `).join("")}</div>`;
    }

    function renderTopicConfig(topics) {
      document.getElementById("topic-config-count").textContent = `${topics.length} 个`;
      const root = document.getElementById("topic-config-list");
      if (!topics.length) {
        root.innerHTML = `<div class="empty">还没有配置关注 topic。</div>`;
        return;
      }
      const rows = topics.map(topic => `
        <tr>
          <td><div class="ticker">${esc(topic.name)}</div><div class="subtle">${esc(topic.id)}</div></td>
          <td>${(topic.segments || []).flatMap(s => s.topics || []).map(t => `<span class="pill">${esc(t)}</span>`).join("") || `<span class="subtle">未设置</span>`}</td>
          <td>${(topic.instruments || []).map(i => `<span class="pill">${esc(i.symbol)}</span>`).join("") || `<span class="subtle">未设置</span>`}</td>
          <td><div class="inline-actions"><button class="ghost" data-edit-topic="${esc(topic.id)}">编辑</button><button class="danger" data-delete-topic="${esc(topic.id)}">删除</button></div></td>
        </tr>
      `).join("");
      root.innerHTML = `<table><thead><tr><th>名称</th><th>主题标签</th><th>工具</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
      root.querySelectorAll("[data-edit-topic]").forEach(button => {
        button.addEventListener("click", () => {
          const topic = (state.data.focus_topics || []).find(item => item.id === button.dataset.editTopic);
          if (topic) fillTopicForm(topic);
          openTab("config");
        });
      });
      root.querySelectorAll("[data-delete-topic]").forEach(button => {
        button.addEventListener("click", async () => {
          await postJson("/api/topics/delete", {id: button.dataset.deleteTopic});
          toast("Topic 已删除");
          await refresh();
        });
      });
    }

    function renderConfiguration(config) {
      const form = document.getElementById("model-form");
      form.backend.value = config.backend || "zhipu";
      form.model.value = config.model || "";
      form.zhipu_model.value = (config.zhipu || {}).model || "";
      form.openai_model.value = (config.openai || {}).model || "";
      form.advice_backend.value = (config.intraday_agents || {}).advice_backend || "conservative";
      form.debate_rounds.value = (config.intraday_agents || {}).debate_rounds || 1;
    }

    function fillHoldingForm(holding) {
      const form = document.getElementById("holding-form");
      form.market.value = holding.market;
      form.ticker.value = holding.ticker;
      form.symbol.value = holding.symbol || holding.ticker;
      form.company.value = holding.company || "";
      form.themes.value = (holding.themes || []).join("\n");
      state.configDirty = true;
    }

    function tagsFromText(value) {
      return String(value || "").split(/,|，/).map(item => item.trim()).filter(Boolean);
    }

    function parseSegments(value) {
      return String(value || "").split(/\n/).map(line => line.trim()).filter(Boolean).map(line => {
        const parts = line.split("|").map(part => part.trim());
        return {name: parts[0], topics: tagsFromText(parts[1] || parts[0])};
      }).filter(item => item.name && item.topics.length);
    }

    function parseInstruments(value) {
      return String(value || "").split(/\n/).map(line => line.trim()).filter(Boolean).map(line => {
        const parts = line.split("|").map(part => part.trim());
        return {symbol: parts[0], name: parts[1] || parts[0], topics: tagsFromText(parts[2] || "")};
      }).filter(item => item.symbol);
    }

    function fillTopicForm(topic) {
      const form = document.getElementById("topic-form");
      form.id.value = topic.id;
      form.name.value = topic.name || "";
      form.segments.value = (topic.segments || []).map(s => `${s.name} | ${(s.topics || []).join(", ")}`).join("\n");
      form.instruments.value = (topic.instruments || []).map(i => `${i.symbol} | ${i.name || i.symbol} | ${(i.topics || []).join(", ")}`).join("\n");
      state.configDirty = true;
    }

    function render(data) {
      state.data = data;
      document.getElementById("clock").textContent = `本地更新时间 ${data.generated_at.replace("T", " ").slice(0, 19)}`;
      renderMarketStatus(data.markets || {});
      renderOverview(data);
      renderHoldings(data.holdings || []);
      renderHoldingConfig(data.holdings || []);
      renderAlerts(data.notifications || []);
      renderBriefs(data.briefs || []);
      renderTopics(data.focus_topics || []);
      renderTopicConfig(data.focus_topics || []);
      if (!state.configDirty) renderConfiguration(data.configuration || {});
    }

    async function refresh() {
      const response = await fetch("/api/state");
      render(await response.json());
    }

    document.querySelectorAll(".tab-button").forEach(button => {
      button.addEventListener("click", () => openTab(button.dataset.tab));
    });

    document.querySelectorAll("#model-form, #holding-form, #topic-form").forEach(form => {
      form.addEventListener("input", () => { state.configDirty = true; });
      form.addEventListener("reset", () => { setTimeout(() => { state.configDirty = false; render(state.data); }, 0); });
    });

    document.getElementById("model-form").backend.addEventListener("change", event => {
      const form = event.target.form;
      if (event.target.value === "openai") form.model.value = form.openai_model.value;
      if (event.target.value === "zhipu") form.model.value = form.zhipu_model.value;
    });
    document.getElementById("model-form").openai_model.addEventListener("input", event => {
      const form = event.target.form;
      if (form.backend.value === "openai") form.model.value = event.target.value;
    });
    document.getElementById("model-form").zhipu_model.addEventListener("input", event => {
      const form = event.target.form;
      if (form.backend.value === "zhipu") form.model.value = event.target.value;
    });

    document.getElementById("model-form").addEventListener("submit", async event => {
      event.preventDefault();
      const form = new FormData(event.target);
      await postJson("/api/model-config", {
        backend: form.get("backend"),
        model: String(form.get("model") || "").trim(),
        zhipu_model: String(form.get("zhipu_model") || "").trim(),
        openai_model: String(form.get("openai_model") || "").trim(),
        advice_backend: form.get("advice_backend"),
        debate_rounds: form.get("debate_rounds")
      });
      state.configDirty = false;
      toast("模型配置已保存");
      await refresh();
    });

    document.getElementById("holding-form").addEventListener("submit", async event => {
      event.preventDefault();
      const form = new FormData(event.target);
      await postJson("/api/holdings", {
        market: form.get("market"),
        ticker: String(form.get("ticker") || "").trim(),
        symbol: String(form.get("symbol") || "").trim(),
        company: String(form.get("company") || "").trim(),
        themes: String(form.get("themes") || "").split(/\n|,/).map(v => v.trim()).filter(Boolean)
      });
      event.target.reset();
      state.configDirty = false;
      toast("持仓已保存");
      await refresh();
    });

    document.getElementById("topic-form").addEventListener("submit", async event => {
      event.preventDefault();
      const form = new FormData(event.target);
      await postJson("/api/topics", {
        id: String(form.get("id") || "").trim(),
        name: String(form.get("name") || "").trim(),
        segments: parseSegments(form.get("segments")),
        instruments: parseInstruments(form.get("instruments"))
      });
      event.target.reset();
      state.configDirty = false;
      toast("Topic 已保存");
      await refresh();
    });

    refresh().catch(error => toast(error.message));
    const events = new EventSource("/events");
    events.onmessage = event => render(JSON.parse(event.data));
    events.onerror = () => setTimeout(refresh, 3000);
  </script>
</body>
</html>
"""


def _read_sources(root: Path, settings: Mapping[str, Any]) -> dict[str, Any]:
    path = resolve_path(root, settings.get("sources_path", "config/sources.json"))
    value = load_json(path, {})
    return value if isinstance(value, dict) else {}


def _sources_path(root: Path, settings: Mapping[str, Any]) -> Path:
    return resolve_path(root, settings.get("sources_path", "config/sources.json"))


def _settings_path(root: Path) -> Path:
    return root / "config" / "settings.json"


def _model_configuration(settings: Mapping[str, Any]) -> dict[str, Any]:
    backend = str(settings.get("backend", "zhipu"))
    openai_settings = settings.get("openai", {})
    zhipu_settings = settings.get("zhipu", {})
    agent_settings = settings.get("intraday_agents", {})
    if not isinstance(openai_settings, Mapping):
        openai_settings = {}
    if not isinstance(zhipu_settings, Mapping):
        zhipu_settings = {}
    if not isinstance(agent_settings, Mapping):
        agent_settings = {}
    active_provider_settings = settings.get(backend, {})
    if not isinstance(active_provider_settings, Mapping):
        active_provider_settings = {}
    return {
        "backend": backend,
        "model": str(active_provider_settings.get("model") or settings.get("model", "")),
        "openai": {
            "api_base": str(openai_settings.get("api_base", "")),
            "model": str(openai_settings.get("model", "")),
            "reasoning_effort": str(openai_settings.get("reasoning_effort", "medium")),
        },
        "zhipu": {
            "api_base": str(zhipu_settings.get("api_base", "")),
            "model": str(zhipu_settings.get("model", "")),
            "temperature": zhipu_settings.get("temperature", 0.2),
            "max_tokens": zhipu_settings.get("max_tokens", 32768),
            "thinking": str(zhipu_settings.get("thinking", "enabled")),
        },
        "intraday_agents": {
            "advice_backend": str(agent_settings.get("advice_backend", "conservative")),
            "debate_rounds": int(agent_settings.get("debate_rounds", 1)),
        },
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = value.replace(",", "\n").splitlines()
    elif isinstance(value, list):
        candidates = value
    else:
        return []
    return [str(item).strip() for item in candidates if str(item).strip()]


def _normalized_topic(payload: Mapping[str, Any]) -> dict[str, Any]:
    topic_id = str(payload.get("id", "")).strip()
    if not topic_id:
        raise ValueError("topic id is required")
    name = str(payload.get("name", "")).strip() or topic_id
    segments = []
    raw_segments = payload.get("segments", [])
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if not isinstance(segment, Mapping):
                continue
            segment_name = str(segment.get("name", "")).strip()
            topics = _string_list(segment.get("topics", []))
            if segment_name and topics:
                segments.append({"name": segment_name, "topics": topics})
    instruments = []
    raw_instruments = payload.get("instruments", [])
    if isinstance(raw_instruments, list):
        for instrument in raw_instruments:
            if not isinstance(instrument, Mapping):
                continue
            symbol = str(instrument.get("symbol", "")).strip()
            if not symbol:
                continue
            instruments.append(
                {
                    "symbol": symbol,
                    "name": str(instrument.get("name", "")).strip() or symbol,
                    "topics": _string_list(instrument.get("topics", [])),
                }
            )
    return {"id": topic_id, "name": name, "segments": segments, "instruments": instruments}


def update_model_configuration(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path = _settings_path(root)
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}

    backend = str(payload.get("backend", settings.get("backend", "zhipu"))).strip()
    if backend not in {"zhipu", "openai", "dry-run"}:
        raise ValueError("backend must be zhipu, openai, or dry-run")
    settings["backend"] = backend

    openai_model = str(payload.get("openai_model", "")).strip()
    zhipu_model = str(payload.get("zhipu_model", "")).strip()
    selected_model = str(payload.get("model", "")).strip()
    if openai_model:
        settings.setdefault("openai", {})["model"] = openai_model
    if zhipu_model:
        settings.setdefault("zhipu", {})["model"] = zhipu_model
    if backend == "openai" and (selected_model or openai_model):
        settings.setdefault("openai", {})["model"] = selected_model or openai_model
        settings["model"] = selected_model or openai_model
    if backend == "zhipu" and (selected_model or zhipu_model):
        settings.setdefault("zhipu", {})["model"] = selected_model or zhipu_model
        settings["model"] = selected_model or zhipu_model

    advice_backend = str(payload.get("advice_backend", "")).strip()
    if advice_backend:
        if advice_backend not in {"conservative", "zhipu", "openai"}:
            raise ValueError("advice_backend must be conservative, zhipu, or openai")
        settings.setdefault("intraday_agents", {})["advice_backend"] = advice_backend
    if str(payload.get("debate_rounds", "")).strip():
        debate_rounds = int(payload["debate_rounds"])
        if debate_rounds < 1 or debate_rounds > 3:
            raise ValueError("debate_rounds must be between 1 and 3")
        settings.setdefault("intraday_agents", {})["debate_rounds"] = debate_rounds

    write_json(path, settings)
    return _model_configuration(load_settings(root))


def _latest_quotes(root: Path, settings: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    if not db_path.exists():
        return {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(
            """
            SELECT q.* FROM quotes q
            JOIN (
              SELECT market, symbol, max(observed_at) AS observed_at
              FROM quotes GROUP BY market, symbol
            ) latest
            ON latest.market=q.market AND latest.symbol=q.symbol AND latest.observed_at=q.observed_at
            """
        ).fetchall():
            rows[(row["market"], row["symbol"].upper())] = dict(row)
    return rows


def _recent_suggestions(root: Path, settings: Mapping[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT * FROM suggestions
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.Error:
            return []
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence_ids"] = json.loads(item.pop("evidence_json", "[]"))
        except json.JSONDecodeError:
            item["evidence_ids"] = []
        item["type"] = "intraday_suggestion"
        result.append(item)
    return result


def _outbox_events(root: Path, settings: Mapping[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    path = resolve_path(root, settings.get("state", {}).get("conversation_outbox", "state/conversation-outbox.jsonl"))
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return list(reversed(events))


def _brief_files(root: Path, settings: Mapping[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    brief_root = resolve_path(root, "briefs")
    if not brief_root.exists():
        return []
    files = [
        path
        for path in brief_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".html", ".md"}
        and "source-log" not in path.stem
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    result = []
    for path in files[:limit]:
        relative = path.relative_to(brief_root)
        parts = relative.parts
        market = parts[0] if parts and parts[0] in MARKETS else ""
        result.append(
            {
                "name": path.stem,
                "market": market,
                "url": "/briefs/" + urllib.parse.quote(relative.as_posix()),
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return result


def load_dashboard_state(root: Path | None = None) -> dict[str, Any]:
    project_root = root or find_project_root()
    settings = load_settings(project_root)
    sources = _read_sources(project_root, settings)
    quotes = _latest_quotes(project_root, settings)
    holdings = []
    for holding in configured_portfolio_holdings(sources):
        copied = dict(holding)
        key = (copied["market"], str(copied.get("symbol", copied["ticker"])).upper())
        copied["quote"] = quotes.get(key)
        holdings.append(copied)

    markets = {}
    for market in MARKETS:
        market_settings = load_market_settings(project_root, settings, market).get("markets", {}).get(market, {})
        status = market_status(
            market,
            holidays=market_settings.get("holidays", []),
            extra_open_dates=market_settings.get("extra_open_dates", []),
            early_closes=market_settings.get("early_closes", {}),
        )
        markets[market] = {
            "state": status.state,
            "as_of_beijing": status.as_of_beijing.isoformat(timespec="seconds"),
            "session_open_beijing": status.session_open_beijing.isoformat(timespec="seconds")
            if status.session_open_beijing
            else None,
            "session_close_beijing": status.session_close_beijing.isoformat(timespec="seconds")
            if status.session_close_beijing
            else None,
        }

    notifications = _recent_suggestions(project_root, settings)
    seen = {(item.get("type"), item.get("market"), item.get("symbol"), item.get("created_at")) for item in notifications}
    for event in _outbox_events(project_root, settings):
        key = (event.get("type"), event.get("market"), event.get("symbol"), event.get("created_at"))
        if key not in seen:
            notifications.append(event)
            seen.add(key)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "markets": markets,
        "holdings": holdings,
        "focus_topics": configured_focus_topics(sources),
        "configuration": _model_configuration(settings),
        "notifications": notifications[:50],
        "briefs": _brief_files(project_root, settings),
    }


def upsert_holding(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    market = str(payload.get("market", "")).strip()
    if market not in MARKETS:
        raise ValueError("market must be a_share or us_equities")
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    symbol = str(payload.get("symbol", "")).strip().upper() or ticker
    company = str(payload.get("company", "")).strip() or ticker
    themes = [str(value).strip() for value in payload.get("themes", []) if str(value).strip()]

    portfolios = sources.setdefault("portfolios", {})
    market_portfolio = portfolios.setdefault(market, {})
    holdings = market_portfolio.setdefault("holdings", [])
    if not isinstance(holdings, list):
        holdings = []
        market_portfolio["holdings"] = holdings

    new_value = {"ticker": ticker, "symbol": symbol, "company": company, "themes": themes}
    for index, holding in enumerate(holdings):
        if str(holding.get("ticker", "")).strip().upper() == ticker:
            holdings[index] = new_value
            break
    else:
        holdings.append(new_value)

    write_json(_sources_path(root, settings), sources)
    return new_value


def delete_holding(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    market = str(payload.get("market", "")).strip()
    ticker = str(payload.get("ticker", "")).strip().upper()
    if market not in MARKETS or not ticker:
        raise ValueError("market and ticker are required")
    holdings = sources.get("portfolios", {}).get(market, {}).get("holdings", [])
    if isinstance(holdings, list):
        sources["portfolios"][market]["holdings"] = [
            holding
            for holding in holdings
            if str(holding.get("ticker", "")).strip().upper() != ticker
        ]
    write_json(_sources_path(root, settings), sources)
    return {"market": market, "ticker": ticker, "deleted": True}


def upsert_focus_topic(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    new_value = _normalized_topic(payload)

    topics = sources.setdefault("focus_topics", [])
    if not isinstance(topics, list):
        topics = []
        sources["focus_topics"] = topics

    for index, topic in enumerate(topics):
        if not isinstance(topic, Mapping):
            continue
        if str(topic.get("id", topic.get("name", ""))).strip() == new_value["id"]:
            topics[index] = new_value
            break
    else:
        topics.append(new_value)

    write_json(_sources_path(root, settings), sources)
    return new_value


def delete_focus_topic(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    topic_id = str(payload.get("id", "")).strip()
    if not topic_id:
        raise ValueError("topic id is required")
    topics = sources.get("focus_topics", [])
    if isinstance(topics, list):
        sources["focus_topics"] = [
            topic
            for topic in topics
            if not isinstance(topic, Mapping)
            or str(topic.get("id", topic.get("name", ""))).strip() != topic_id
        ]
    write_json(_sources_path(root, settings), sources)
    return {"id": topic_id, "deleted": True}


class DashboardHandler(BaseHTTPRequestHandler):
    root: Path

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            return
        if parsed.path == "/api/state":
            self._send_json(load_dashboard_state(self.root))
            return
        if parsed.path == "/events":
            self._send_events()
            return
        if parsed.path == "/briefs/" or parsed.path.startswith("/briefs/"):
            self._send_brief(parsed.path)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path == "/api/state":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/holdings":
                self._send_json(upsert_holding(self.root, payload))
                return
            if parsed.path == "/api/holdings/delete":
                self._send_json(delete_holding(self.root, payload))
                return
            if parsed.path == "/api/topics":
                self._send_json(upsert_focus_topic(self.root, payload))
                return
            if parsed.path == "/api/topics/delete":
                self._send_json(delete_focus_topic(self.root, payload))
                return
            if parsed.path == "/api/model-config":
                self._send_json(update_model_configuration(self.root, payload))
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _send_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        for _ in range(720):
            try:
                data = json.dumps(load_dashboard_state(self.root), ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(5)
            except (BrokenPipeError, ConnectionResetError):
                break

    def _send_brief(self, path: str) -> None:
        brief_root = (self.root / "briefs").resolve()
        if path == "/briefs/":
            body = "\n".join(
                f'<a href="{item["url"]}">{item["name"]}</a><br>'
                for item in _brief_files(self.root, load_settings(self.root), limit=200)
            )
            self._send_bytes(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                f"<!doctype html><meta charset='utf-8'><body>{body}</body>".encode("utf-8"),
            )
            return
        relative = urllib.parse.unquote(path.removeprefix("/briefs/"))
        candidate = (brief_root / relative).resolve()
        if not str(candidate).startswith(str(brief_root)) or not candidate.exists() or not candidate.is_file():
            self._send_json({"error": "brief not found"}, HTTPStatus.NOT_FOUND)
            return
        if candidate.suffix.lower() == ".md":
            markdown = candidate.read_text(encoding="utf-8")
            title = candidate.stem
            self._send_bytes(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                markdown_to_html(markdown, title=title).encode("utf-8"),
            )
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, content_type, candidate.read_bytes())


def run_web_server(host: str = "127.0.0.1", port: int = 8765, root: Path | None = None) -> None:
    project_root = root or find_project_root()

    class Handler(DashboardHandler):
        pass

    Handler.root = project_root
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Market Analyzer web dashboard: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Web dashboard stopped.", flush=True)
    finally:
        server.server_close()
