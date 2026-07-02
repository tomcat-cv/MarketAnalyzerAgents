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

    function timeZoneLabel(value, fallback) {
      const text = String(value || "");
      const match = text.match(/([+-]\d{2}:\d{2}|Z)$/);
      return match ? `UTC${match[1] === "Z" ? "+00:00" : match[1]}` : fallback;
    }

    function formatTime(value, fallbackZone = state.data?.display_timezone || "Asia/Shanghai", length = 19) {
      if (!value) return "";
      return `${String(value).replace("T", " ").slice(0, length)} ${timeZoneLabel(value, fallbackZone)}`;
    }

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
          <div class="subtle">${esc(formatTime(item.as_of_beijing))}</div>
        </div>
      `).join("");
    }

    function renderOverview(data) {
      const markets = Object.entries(data.markets || {});
      const openMarkets = markets.filter(([, item]) => item.state === "open").map(([market]) => marketLabel(market));
      const quotedHoldings = (data.holdings || []).filter(item => item.quote).length;
      const quoteRefreshFailures = (((data.quote_refresh || {}).failures) || []).length;
      const latestBrief = (data.briefs || [])[0];
      document.getElementById("overview").innerHTML = `
        <div class="metric"><span>交易状态</span><strong>${esc(openMarkets.length ? openMarkets.join(" / ") : "休市")}</strong><small>${esc(markets.map(([market, item]) => `${marketLabel(market)} ${stateLabel(item.state)}`).join(" · "))}</small></div>
        <div class="metric"><span>组合覆盖</span><strong>${esc((data.holdings || []).length)}</strong><small>${quoteRefreshFailures ? `${quotedHoldings} 个已有最近行情 · ${quoteRefreshFailures} 个刷新失败` : `${quotedHoldings} 个已有最近行情`}</small></div>
        <div class="metric"><span>盘中提醒</span><strong>${esc((data.notifications || []).length)}</strong><small>来自建议与 outbox 事件</small></div>
        <div class="metric"><span>最新简报</span><strong>${latestBrief ? esc(latestBrief.name) : "暂无"}</strong><small>${latestBrief ? esc(formatTime(latestBrief.modified_at, latestBrief.timezone, 16)) : "briefs 目录为空"}</small></div>
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
            ${h.quote ? `<span><span class="price">${esc(h.quote.price)}</span><br><span class="subtle">${esc(formatTime(h.quote.observed_at))}</span></span>` : `<span class="subtle">暂无</span>`}
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
          <div class="meta">${esc(formatTime(a.created_at || a.generated_at))} · ${esc(fmt(a.market))} ${esc(fmt(a.symbol))}</div>
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
          <span class="subtle">${esc(formatTime(b.modified_at, b.timezone))}</span>
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
      document.getElementById("clock").textContent = `更新时间 ${formatTime(data.generated_at, data.display_timezone)}`;
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