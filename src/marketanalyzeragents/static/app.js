const state = { data: null, configDirty: false };

const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;"
}[char]));
const marketLabel = market => market === "us_equities" ? "美股" : "A 股";
const stateLabel = value => ({open: "开盘", closed: "休市", pre_market: "盘前", post_market: "盘后", break: "午休"}[value] || value);

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

function formatTime(value) {
  return value ? String(value).replace("T", " ").slice(0, 16) : "";
}

function markdownLite(markdown) {
  const lines = String(markdown || "").split(/\n/);
  let html = "";
  let inList = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      if (inList) { html += "</ul>"; inList = false; }
      continue;
    }
    if (line.startsWith("## ")) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<h3>${esc(line.slice(3))}</h3>`;
      continue;
    }
    if (line.startsWith("# ")) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<h2>${esc(line.slice(2))}</h2>`;
      continue;
    }
    if (line.startsWith("- ")) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${linkify(esc(line.slice(2)))}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    html += `<p>${linkify(esc(line))}</p>`;
  }
  if (inList) html += "</ul>";
  return html;
}

function linkify(value) {
  return value.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function openTab(name) {
  document.querySelectorAll(".tab-panel").forEach(panel => { panel.hidden = panel.id !== `tab-${name}`; });
  document.querySelectorAll(".tab-button").forEach(button => { button.classList.toggle("active", button.dataset.tab === name); });
}

function renderMarketStatus(markets) {
  document.getElementById("market-status").innerHTML = Object.entries(markets || {}).map(([market, item]) => `
    <div class="market-status">
      <strong>${marketLabel(market)}</strong>
      <div class="state ${esc(item.state)}">${esc(stateLabel(item.state))}</div>
      <div class="subtle">${esc(formatTime(item.as_of_beijing))}</div>
    </div>
  `).join("");
}

function renderOverview(data) {
  const reports = data.reports || [];
  const suggestions = data.suggestions || [];
  const social = data.social_sources || {};
  const sourceCount = (data.official_sources || []).length + Object.values(social).filter(item => item && item.enabled !== false).length;
  document.getElementById("overview").innerHTML = `
    <div class="metric"><span>定时报告</span><strong>${esc((data.report_schedule || []).join(" / "))}</strong><small>北京时间</small></div>
    <div class="metric"><span>持仓</span><strong>${esc((data.holdings || []).length)}</strong><small>A 股与美股分开处理</small></div>
    <div class="metric"><span>来源</span><strong>${esc(sourceCount)}</strong><small>官方资讯与社媒分层</small></div>
    <div class="metric"><span>历史报告</span><strong>${esc(reports.length)}</strong><small>盘中建议 ${suggestions.length} 条</small></div>
  `;
}

function renderLatestReport(report) {
  const root = document.getElementById("latest-report");
  if (!report) {
    root.innerHTML = `<div class="empty">还没有生成今日报告。</div>`;
    return;
  }
  root.innerHTML = `${markdownLite(report.markdown)}<p><a href="${esc(report.url)}" target="_blank">打开可读 HTML 报告</a></p>`;
}

function renderSuggestions(items) {
  const root = document.getElementById("suggestions");
  if (!items.length) {
    root.innerHTML = `<div class="empty">还没有盘中建议。</div>`;
    return;
  }
  root.innerHTML = items.map(item => `
    <article class="alert">
      <div class="meta">${esc(formatTime(item.generated_at))}</div>
      <div class="title">${esc(item.title || "盘中操作建议")}</div>
      <div>${markdownLite(item.markdown)}</div>
    </article>
  `).join("");
}

function renderTracking(data) {
  document.getElementById("tracking-count").textContent = `${(data.holdings || []).length} 持仓 / ${(data.focus_topics || []).length} Topic`;
  document.getElementById("tracking").innerHTML = `
    <div class="card-list">
      ${(data.holdings || []).map(item => `
        <article class="holding-card">
          <div class="card-top"><div><div class="ticker">${esc(item.ticker)}</div><div class="subtle">${esc(item.company || "")}</div></div><span class="market-badge">${marketLabel(item.market)}</span></div>
          <div>${(item.themes || []).map(theme => `<span class="pill">${esc(theme)}</span>`).join("")}</div>
        </article>
      `).join("")}
      ${(data.focus_topics || []).map(item => `
        <article class="topic-card">
          <div class="ticker">${esc(item.name)}</div>
          <div class="subtle">${esc(item.id)}</div>
          <div>${(item.keywords || []).map(word => `<span class="pill">${esc(word)}</span>`).join("")}</div>
        </article>
      `).join("")}
    </div>`;
}

function renderArchive(reports) {
  const root = document.getElementById("reports");
  if (!reports.length) {
    root.innerHTML = `<div class="empty">暂无历史报告。</div>`;
    return;
  }
  root.innerHTML = reports.map(report => `
    <a class="brief-row" href="${esc(report.url)}" target="_blank">
      <span><strong>${esc(report.title)}</strong><br><span class="subtle">${esc(report.official_count || 0)} 官方 / ${esc(report.social_count || 0)} 社媒</span></span>
      <span class="subtle">${esc(formatTime(report.generated_at))}</span>
    </a>
  `).join("");
}

function renderHoldingConfig(holdings) {
  document.getElementById("holding-config-count").textContent = `${holdings.length} 个`;
  const root = document.getElementById("holding-config-list");
  if (!holdings.length) {
    root.innerHTML = `<div class="empty">还没有配置持仓。</div>`;
    return;
  }
  root.innerHTML = `<table><thead><tr><th>标的</th><th>市场</th><th>主题</th><th></th></tr></thead><tbody>${holdings.map(item => `
    <tr>
      <td><div class="ticker">${esc(item.ticker)}</div><div class="subtle">${esc(item.company || "")}</div></td>
      <td>${marketLabel(item.market)}</td>
      <td>${(item.themes || []).map(theme => `<span class="pill">${esc(theme)}</span>`).join("")}</td>
      <td><button class="danger" data-delete-holding="${esc(item.market)}:${esc(item.ticker)}" type="button">删除</button></td>
    </tr>`).join("")}</tbody></table>`;
  root.querySelectorAll("[data-delete-holding]").forEach(button => {
    button.addEventListener("click", async () => {
      const [market, ticker] = button.dataset.deleteHolding.split(":");
      await postJson("/api/holdings/delete", {market, ticker});
      await refresh();
    });
  });
}

function renderTopicConfig(topics) {
  document.getElementById("topic-config-count").textContent = `${topics.length} 个`;
  const root = document.getElementById("topic-config-list");
  if (!topics.length) {
    root.innerHTML = `<div class="empty">还没有配置 Topic。</div>`;
    return;
  }
  root.innerHTML = `<table><thead><tr><th>Topic</th><th>关键词</th><th></th></tr></thead><tbody>${topics.map(item => `
    <tr>
      <td><div class="ticker">${esc(item.name)}</div><div class="subtle">${esc(item.id)}</div></td>
      <td>${(item.keywords || []).map(word => `<span class="pill">${esc(word)}</span>`).join("")}</td>
      <td><button class="danger" data-delete-topic="${esc(item.id)}" type="button">删除</button></td>
    </tr>`).join("")}</tbody></table>`;
  root.querySelectorAll("[data-delete-topic]").forEach(button => {
    button.addEventListener("click", async () => {
      await postJson("/api/topics/delete", {id: button.dataset.deleteTopic});
      await refresh();
    });
  });
}

function lines(value) {
  return String(value || "").split(/\n|,|，/).map(item => item.trim()).filter(Boolean);
}

function socialAdapter(config) {
  if (config?.adapter) return config.adapter;
  return (config?.manual_posts || []).length ? "manual" : "disabled";
}

function formatManualPosts(posts) {
  return (posts || []).map(item => [
    item.author || "",
    item.published_at || "",
    item.sentiment || "",
    item.url || "",
    item.text || ""
  ].join(" | ")).join("\n");
}

function parseManualPosts(value) {
  return String(value || "").split(/\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const parts = line.split("|").map(part => part.trim());
    return {
      author: parts[0] || "",
      published_at: parts[1] || "",
      sentiment: parts[2] || "",
      url: parts[3] || "",
      text: parts.slice(4).join(" | ").trim()
    };
  }).filter(item => item.text);
}

function renderConfig(data) {
  if (state.configDirty) return;
  const model = document.getElementById("model-form");
  const config = data.configuration || {};
  model.backend.value = config.backend || "zhipu";
  model.model.value = config.model || "";
  model.zhipu_model.value = config.zhipu_model || "";
  model.openai_model.value = config.openai_model || "";
  model.advice_backend.value = config.advice_backend || "zhipu";
  model.debate_rounds.value = config.debate_rounds || 1;
  model.report_schedule.value = (config.report_schedule || data.report_schedule || []).join(", ");
  model.intraday_suggestion_interval_seconds.value = config.intraday_suggestion_interval_seconds || 1800;

  const source = document.getElementById("source-form");
  source.official_sources.value = (data.official_sources || []).map(item => `${item.name || ""} | ${item.url || ""} | ${(item.topics || []).join(", ")}`).join("\n");
  const social = data.social_sources || {};
  source.x_keywords.value = ((social.x || {}).keywords || []).join("\n");
  source.x_accounts.value = ((social.x || {}).accounts || []).join("\n");
  source.x_adapter.value = socialAdapter(social.x);
  source.x_manual_posts.value = formatManualPosts((social.x || {}).manual_posts);
  source.xiaohongshu_keywords.value = ((social.xiaohongshu || {}).keywords || []).join("\n");
  source.xiaohongshu_accounts.value = ((social.xiaohongshu || {}).accounts || []).join("\n");
  source.xiaohongshu_adapter.value = socialAdapter(social.xiaohongshu);
  source.xiaohongshu_manual_posts.value = formatManualPosts((social.xiaohongshu || {}).manual_posts);
  source.fear_value.value = (data.fear_greed || {}).value || "";
  source.fear_label.value = (data.fear_greed || {}).label || "";
  source.fear_source_url.value = (data.fear_greed || {}).source_url || "";
}

function render(data) {
  state.data = data;
  document.getElementById("clock").textContent = `更新时间 ${formatTime(data.generated_at)} ${data.display_timezone}`;
  renderMarketStatus(data.markets);
  renderOverview(data);
  renderLatestReport(data.latest_report);
  renderSuggestions(data.suggestions || []);
  renderTracking(data);
  renderArchive(data.reports || []);
  renderHoldingConfig(data.holdings || []);
  renderTopicConfig(data.focus_topics || []);
  renderConfig(data);
}

async function refresh() {
  const response = await fetch("/api/state");
  render(await response.json());
}

document.querySelectorAll(".tab-button").forEach(button => {
  button.addEventListener("click", () => openTab(button.dataset.tab));
});

document.querySelectorAll("form").forEach(form => {
  form.addEventListener("input", () => { state.configDirty = true; });
  form.addEventListener("reset", () => { setTimeout(() => { state.configDirty = false; render(state.data); }, 0); });
});

document.getElementById("model-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  await postJson("/api/model-config", Object.fromEntries(form.entries()));
  state.configDirty = false;
  toast("模型配置已保存");
  await refresh();
});

document.getElementById("holding-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  await postJson("/api/holdings", {
    market: form.get("market"),
    ticker: form.get("ticker"),
    symbol: form.get("symbol"),
    company: form.get("company"),
    themes: lines(form.get("themes"))
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
    id: form.get("id"),
    name: form.get("name"),
    keywords: lines(form.get("keywords"))
  });
  event.target.reset();
  state.configDirty = false;
  toast("Topic 已保存");
  await refresh();
});

document.getElementById("source-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const official = String(form.get("official_sources") || "").split(/\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const parts = line.split("|").map(part => part.trim());
    return {type: "rss", enabled: true, name: parts[0], url: parts[1], topics: lines(parts[2] || ""), allowed_domains: []};
  }).filter(item => item.name && item.url);
  await postJson("/api/sources", {
    official_sources: official,
    social_sources: {
      x: {enabled: true, adapter: form.get("x_adapter"), keywords: lines(form.get("x_keywords")), accounts: lines(form.get("x_accounts")), manual_posts: parseManualPosts(form.get("x_manual_posts"))},
      xiaohongshu: {enabled: true, adapter: form.get("xiaohongshu_adapter"), keywords: lines(form.get("xiaohongshu_keywords")), accounts: lines(form.get("xiaohongshu_accounts")), manual_posts: parseManualPosts(form.get("xiaohongshu_manual_posts"))}
    },
    fear_greed: {value: form.get("fear_value"), label: form.get("fear_label"), source_url: form.get("fear_source_url")}
  });
  state.configDirty = false;
  toast("来源配置已保存");
  await refresh();
});

document.getElementById("run-report").addEventListener("click", async () => {
  toast("正在生成报告");
  await postJson("/api/report/run", {});
  await refresh();
});

document.getElementById("run-suggestion").addEventListener("click", async () => {
  toast("正在刷新建议");
  await postJson("/api/suggestion/run", {});
  await refresh();
});

refresh().catch(error => toast(error.message));
