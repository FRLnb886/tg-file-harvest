const PAGE_SIZE = 50;
const MAX_SIZE = 500 * 1024 * 1024;
const TOKEN_KEY = "tgHarvestPanelToken";

// file:// 预览或隐私模式下 localStorage 可能不可用，不能用它拖垮整个页面
function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (error) {
    return "";
  }
}

function writeToken(value) {
  try {
    localStorage.setItem(TOKEN_KEY, value);
  } catch (error) {
    /* 存不下就只放在内存里 */
  }
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const state = {
  mode: "loading",
  token: readToken(),
  view: "dashboard",
  summary: null,
  channels: [],
  files: { items: [], total: 0, offset: 0 },
  staticFiles: [],
  fileQuery: "",
  fileChannel: "",
  fileStatus: "",
  runs: [],
  runId: "",
  logOffset: 0,
  logText: "",
  pollTimer: 0,
  loginStage: "idle",
};

const el = (id) => document.getElementById(id);

function toast(message, kind = "info") {
  const node = el("toast");
  node.textContent = message;
  node.className = `toast toast-${kind}`;
  node.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => {
    node.hidden = true;
  }, 3200);
}

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (state.token) headers["X-Panel-Token"] = state.token;
  let response;
  try {
    response = await fetch(`api${path}`, Object.assign({}, options, { headers }));
  } catch (error) {
    throw new ApiError(0, "无法连接面板");
  }
  if (response.status === 401) throw new ApiError(401, "访问令牌无效");
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = String(body.detail);
    } catch (error) {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

function formatSize(bytes) {
  const value = Number(bytes) || 0;
  if (value <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let size = value;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size >= 10 || index === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`;
}

function formatTime(seconds) {
  const value = Number(seconds) || 0;
  if (!value) return "-";
  return formatDate(new Date(value * 1000));
}

function formatIso(text) {
  if (!text) return "-";
  if (/^\d{8}-\d{6}$/.test(text)) {
    const [, y, m, d, hh, mm] = text.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$/);
    return `${y}-${m}-${d} ${hh}:${mm}`;
  }
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? text : formatDate(date);
}

function formatDate(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

const STATUS_TEXT = { ok: "已入库", failed: "失败", skipped: "跳过", pending: "等待中", unknown: "未知" };

function statusBadge(status) {
  const span = document.createElement("span");
  span.className = `tag tag-${status || "unknown"}`;
  span.textContent = STATUS_TEXT[status] || status || "未知";
  return span;
}

function setMode(mode) {
  state.mode = mode;
  const badge = el("mode-badge");
  const panelOnly = document.querySelectorAll('[data-view="channels"], [data-view="account"], [data-view="logs"]');
  if (mode === "panel") {
    badge.textContent = "面板模式";
    badge.className = "badge badge-ok";
  } else if (mode === "static") {
    badge.textContent = "静态模式（只读）";
    badge.className = "badge badge-muted";
  } else if (mode === "locked") {
    badge.textContent = "需要令牌";
    badge.className = "badge badge-warn";
  } else {
    badge.textContent = "连接中…";
    badge.className = "badge badge-muted";
  }
  const hide = mode !== "panel";
  panelOnly.forEach((node) => {
    node.hidden = hide;
  });
  el("run-btn").hidden = hide;
  if (hide && ["channels", "account", "logs"].includes(state.view)) setView("files");
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((node) => {
    node.classList.toggle("is-active", node.dataset.view === view);
  });
  document.querySelectorAll("[data-view-panel]").forEach((node) => {
    node.classList.toggle("is-active", node.dataset.viewPanel === view);
  });
  const titles = {
    dashboard: ["概览", "账号、频道与最近一次采集"],
    files: ["文件库", "已入库文件、失败与跳过的记录"],
    channels: ["频道与关键词", "配置要拉取的频道，以及文件名关键词过滤"],
    account: ["TG 账号", "登录、检查与退出登录"],
    logs: ["运行日志", "每次采集的完整输出"],
  };
  const [title, sub] = titles[view] || ["", ""];
  el("view-title").textContent = title;
  el("view-sub").textContent = sub;
  if (view === "logs" && state.mode === "panel") {
    loadRuns().then(() => {
      if (!state.runId && state.runs.length) openRun(state.runs[0].id);
    });
  }
  if (view === "channels" && state.mode === "panel") loadChannels();
}

function applySummary(summary) {
  state.summary = summary;
  const counts = summary.counts || {};
  el("stat-ok").textContent = counts.ok || 0;
  el("stat-failed").textContent = counts.failed || 0;
  el("stat-skipped").textContent = counts.skipped || 0;
  el("stat-channels").textContent = summary.channels || 0;
  el("stat-channels-hint").textContent = `${summary.enabled_channels || 0} 个启用`;
  el("stat-failed-hint").textContent = counts.failed ? "会自动重试" : "没有失败";

  const account = summary.account || {};
  const accPill = el("account-pill");
  if (account.has_session) {
    accPill.textContent = "已配置";
    accPill.className = "pill pill-ok";
    el("account-line").textContent = `api_id ${account.api_id || "-"} · session 已保存`;
  } else {
    accPill.textContent = "未配置";
    accPill.className = "pill pill-warn";
    el("account-line").textContent = "还没有保存 session，去「TG 账号」完成登录。";
  }

  const run = summary.last_run;
  const runPill = el("run-pill");
  if (!run) {
    runPill.textContent = "—";
    runPill.className = "pill";
    el("run-line").textContent = "还没有运行记录。";
  } else {
    const running = run.running;
    runPill.textContent = running ? "运行中" : run.exit_code === 0 ? "成功" : "失败";
    runPill.className = `pill ${running ? "pill-info" : run.exit_code === 0 ? "pill-ok" : "pill-bad"}`;
    el("run-line").textContent = `${formatIso(run.started_at)} 开始 · ${
      run.finished_at ? formatIso(run.finished_at) + " 结束" : "尚未结束"
    }`;
  }

  renderKeywords(summary.keywords || []);
  renderAccountStatus();
}

function renderKeywords(keywords) {
  const box = el("keyword-chips");
  box.textContent = "";
  if (!keywords.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "还没有配置包含关键词——当前会采集全部符合扩展名/大小条件的文件。";
    box.appendChild(empty);
    return;
  }
  keywords.forEach((keyword) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = keyword;
    chip.addEventListener("click", () => {
      el("file-q").value = keyword;
      state.fileQuery = keyword;
      setView("files");
      loadFiles(true);
    });
    box.appendChild(chip);
  });
}

function renderAccountStatus() {
  const account = (state.summary && state.summary.account) || {};
  const pill = el("acc-pill");
  if (account.has_session) {
    pill.textContent = "已保存 session";
    pill.className = "pill pill-ok";
    el("acc-status").textContent = `api_id ${account.api_id || "-"}，api_hash ${
      account.api_hash_set ? "已保存" : "缺失"
    }${account.pending ? "，正在登录流程中" : ""}`;
  } else {
    pill.textContent = "未配置";
    pill.className = "pill pill-warn";
    el("acc-status").textContent = "还没有保存 session，按下面三步登录。";
  }
}

async function loadChannels() {
  if (state.mode !== "panel") return;
  try {
    const data = await api("/channels");
    state.channels = data.items || [];
    renderChannels();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function channelCard(channel) {
  const card = document.createElement("article");
  card.className = "channel";
  card.innerHTML = `
    <div class="channel-head">
      <input data-field="id" placeholder="频道 username 或数字 id" spellcheck="false" />
      <input data-field="title" placeholder="备注名（可空）" spellcheck="false" />
      <label class="switch"><input type="checkbox" data-field="enabled" /> 启用</label>
      <button class="btn ghost small" data-action="remove" type="button">删除</button>
    </div>
    <div class="channel-grid">
      <label>最小大小（字节）<input data-field="min_size" inputmode="numeric" /></label>
      <label>最大大小（字节）<input data-field="max_size" inputmode="numeric" /></label>
      <label>扩展名（逗号分隔）<input data-field="include_ext" placeholder="zip, pdf" spellcheck="false" /></label>
      <label>包含关键词（逗号分隔）<input data-field="include_keywords" placeholder="教程, 资料" spellcheck="false" /></label>
      <label class="span-2">排除关键词（逗号分隔）<input data-field="exclude_keywords" placeholder="广告, 推广" spellcheck="false" /></label>
    </div>
  `;
  const value = (field, v) => {
    const node = card.querySelector(`[data-field="${field}"]`);
    if (node.type === "checkbox") node.checked = Boolean(v);
    else node.value = v === undefined || v === null ? "" : v;
  };
  value("id", channel.id);
  value("title", channel.title);
  value("enabled", channel.enabled !== false);
  value("min_size", channel.min_size ?? 1024);
  value("max_size", channel.max_size ?? MAX_SIZE);
  value("include_ext", (channel.include_ext || []).join(", "));
  value("include_keywords", (channel.include_keywords || []).join(", "));
  value("exclude_keywords", (channel.exclude_keywords || []).join(", "));
  card.querySelector('[data-action="remove"]').addEventListener("click", () => {
    card.remove();
  });
  return card;
}

function renderChannels() {
  const list = el("channel-list");
  list.textContent = "";
  if (!state.channels.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "还没有频道，点「新增频道」开始配置。";
    list.appendChild(empty);
    return;
  }
  state.channels.forEach((channel) => list.appendChild(channelCard(channel)));
}

function splitList(text) {
  return String(text || "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function collectChannels() {
  const cards = Array.from(el("channel-list").querySelectorAll(".channel"));
  const items = cards.map((card) => {
    const read = (field) => card.querySelector(`[data-field="${field}"]`);
    const enabledNode = read("enabled");
    return {
      id: read("id").value.trim(),
      title: read("title").value.trim(),
      enabled: enabledNode.checked,
      min_size: Number(read("min_size").value.trim() || 0),
      max_size: Number(read("max_size").value.trim() || MAX_SIZE),
      include_ext: splitList(read("include_ext").value),
      include_keywords: splitList(read("include_keywords").value),
      exclude_keywords: splitList(read("exclude_keywords").value),
    };
  });
  return items;
}

function validateChannels(items) {
  const seen = new Set();
  for (const item of items) {
    if (!item.id) return "频道 id 不能为空";
    if (seen.has(item.id)) return `频道 id 重复：${item.id}`;
    seen.add(item.id);
    if (!Number.isFinite(item.max_size) || item.max_size > MAX_SIZE) return `频道 ${item.id} 的 max_size 超过 500MB`;
    if (!Number.isFinite(item.min_size) || item.min_size < 0 || item.min_size >= item.max_size) {
      return `频道 ${item.id} 的 min_size 必须 >= 0 且小于 max_size`;
    }
  }
  return "";
}

async function saveChannels() {
  const items = collectChannels();
  const problem = validateChannels(items);
  if (problem) {
    toast(problem, "bad");
    return;
  }
  try {
    const data = await api("/channels", { method: "PUT", body: JSON.stringify({ items }) });
    state.channels = data.items || [];
    renderChannels();
    toast("已保存频道配置", "ok");
    await refreshSummary();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function fillChannelOptions(channels) {
  const select = el("file-channel");
  const current = select.value;
  select.textContent = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部频道";
  select.appendChild(all);
  channels.forEach((channel) => {
    const option = document.createElement("option");
    option.value = channel;
    option.textContent = channel;
    select.appendChild(option);
  });
  select.value = channels.includes(current) ? current : "";
  state.fileChannel = select.value;
}

async function loadFiles(reset = false) {
  if (reset) state.files.offset = 0;
  if (state.mode === "panel") {
    const params = new URLSearchParams({
      q: state.fileQuery,
      channel: state.fileChannel,
      status: state.fileStatus,
      offset: String(state.files.offset),
      limit: String(PAGE_SIZE),
    });
    try {
      const data = await api(`/records?${params}`);
      state.files.items = data.items || [];
      state.files.total = data.total || 0;
    } catch (error) {
      toast(error.message, "bad");
      return;
    }
  } else {
    const needle = state.fileQuery.trim().toLowerCase();
    const rows = state.staticFiles.filter((row) => {
      if (state.fileChannel && row.channel !== state.fileChannel) return false;
      if (needle && !row.name.toLowerCase().includes(needle)) return false;
      return true;
    });
    state.files.total = rows.length;
    state.files.items = rows.slice(state.files.offset, state.files.offset + PAGE_SIZE);
  }
  renderFiles();
}

function renderFiles() {
  const body = el("file-rows");
  body.textContent = "";
  const items = state.files.items;
  el("file-count").textContent = state.files.total ? `共 ${state.files.total} 条` : "";

  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "empty";
    cell.textContent = state.files.total ? "没有更多数据" : "还没有匹配的记录";
    row.appendChild(cell);
    body.appendChild(row);
    updatePager();
    return;
  }

  items.forEach((item) => {
    const row = document.createElement("tr");

    const name = document.createElement("td");
    name.className = "name";
    name.textContent = item.name || "(未命名)";
    name.title = item.error ? `${item.name}\n${item.error}` : item.name || "";
    row.appendChild(name);

    const size = document.createElement("td");
    size.className = "col-size mono";
    size.textContent = formatSize(item.size);
    row.appendChild(size);

    const channel = document.createElement("td");
    channel.className = "col-channel mono";
    channel.textContent = item.channel || "-";
    row.appendChild(channel);

    const status = document.createElement("td");
    status.className = "col-status";
    status.appendChild(statusBadge(item.status));
    row.appendChild(status);

    const time = document.createElement("td");
    time.className = "col-time mono";
    time.textContent = formatTime(item.ts);
    row.appendChild(time);

    const actions = document.createElement("td");
    actions.className = "col-actions";
    if (item.url) {
      const download = document.createElement("a");
      download.className = "btn small";
      download.href = item.url;
      download.rel = "noopener noreferrer";
      download.textContent = "下载";
      actions.appendChild(download);

      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "btn ghost small";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(item.url);
          copy.textContent = "已复制";
        } catch (error) {
          copy.textContent = "失败";
        }
        window.setTimeout(() => {
          copy.textContent = "复制";
        }, 1400);
      });
      actions.appendChild(copy);
    } else {
      const hint = document.createElement("span");
      hint.className = "muted";
      hint.textContent = item.status === "failed" ? "重试中" : "无直链";
      actions.appendChild(hint);
    }
    row.appendChild(actions);
    body.appendChild(row);
  });
  updatePager();
}

function updatePager() {
  const start = state.files.total ? state.files.offset + 1 : 0;
  const end = Math.min(state.files.offset + PAGE_SIZE, state.files.total);
  el("page-info").textContent = `${start}-${end} / ${state.files.total}`;
  el("prev-page").disabled = state.files.offset <= 0;
  el("next-page").disabled = end >= state.files.total;
}

async function loadRuns() {
  if (state.mode !== "panel") return;
  try {
    const data = await api("/runs");
    state.runs = data.items || [];
    const select = el("run-select");
    const current = state.runId;
    select.textContent = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = state.runs.length ? "选择运行记录" : "还没有运行记录";
    select.appendChild(empty);
    state.runs.forEach((run) => {
      const option = document.createElement("option");
      option.value = run.id;
      const flag = run.running ? "运行中" : run.exit_code === 0 ? "成功" : run.exit_code === null ? "未知" : "失败";
      option.textContent = `${run.id} · ${flag}`;
      select.appendChild(option);
    });
    select.value = state.runs.some((run) => run.id === current) ? current : "";
  } catch (error) {
    toast(error.message, "bad");
  }
}

function schedulePoll(delay = 1200) {
  window.clearTimeout(state.pollTimer);
  state.pollTimer = window.setTimeout(pumpLog, delay);
}

async function openRun(runId) {
  state.runId = runId;
  state.logOffset = 0;
  state.logText = "";
  el("run-select").value = runId;
  const view = el("log-view");
  view.textContent = "";
  await pumpLog();
}

async function pumpLog() {
  if (!state.runId || state.mode !== "panel") return;
  let data;
  try {
    data = await api(`/runs/${state.runId}?offset=${state.logOffset}`);
  } catch (error) {
    toast(error.message, "bad");
    return;
  }
  if (data.text) {
    state.logText += data.text;
    const view = el("log-view");
    view.textContent = state.logText;
    if (el("auto-scroll").checked) view.scrollTop = view.scrollHeight;
  }
  state.logOffset = data.next_offset;
  if (data.running && el("auto-refresh").checked) schedulePoll(1200);
  if (!data.running && state.view === "logs") refreshSummary();
}

async function startCollect() {
  const button = el("run-btn");
  button.disabled = true;
  button.textContent = "启动中…";
  try {
    const run = await api("/collect", { method: "POST", body: JSON.stringify({ dry_run: false, limit: 0 }) });
    toast(`已启动采集 ${run.id}`, "ok");
    setView("logs");
    await loadRuns();
    await openRun(run.id);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    button.disabled = false;
    button.textContent = "立即采集";
  }
}

async function refreshSummary() {
  if (state.mode !== "panel") return;
  try {
    const summary = await api("/summary");
    applySummary(summary);
  } catch (error) {
    if (error.status === 401) {
      setMode("locked");
      el("token-modal").hidden = false;
    }
  }
}

async function enterStaticMode() {
  setMode("static");
  el("view-sub").textContent = "静态只读模式：展示 web/data/manifest.json 的内容";
  el("token-btn").hidden = true;
  try {
    const response = await fetch(`data/manifest.json?_=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const manifest = await response.json();
    const rows = (manifest.items || []).map((item) => ({
      id: item.id,
      name: String(item.name || ""),
      size: Number(item.size) || 0,
      channel: String(item.channel || ""),
      status: "ok",
      ts: Number(item.ts) || 0,
      url: String(item.url || ""),
      error: "",
    }));
    rows.sort((a, b) => b.ts - a.ts);
    state.staticFiles = rows;
    const channels = Array.from(new Set(rows.map((row) => row.channel).filter(Boolean))).sort();
    fillChannelOptions(channels);
    state.files.total = rows.length;
    state.files.items = rows.slice(0, PAGE_SIZE);
    el("stat-ok").textContent = rows.length;
    el("stat-channels").textContent = channels.length;
    state.summary = { keywords: [] };
    renderKeywords([]);
    setView("files");
    renderFiles();
  } catch (error) {
    toast(`manifest 读取失败：${error.message}`, "bad");
  }
}

async function detectMode() {
  setMode("loading");
  try {
    const summary = await api("/summary");
    setMode("panel");
    applySummary(summary);
    await Promise.all([loadChannels(), loadFiles(true), loadRuns()]);
    setView("dashboard");
  } catch (error) {
    if (error.status === 401) {
      setMode("locked");
      el("token-modal").hidden = false;
      return;
    }
    await enterStaticMode();
  }
}

function bindEvents() {
  el("nav").addEventListener("click", (event) => {
    const button = event.target.closest(".nav-item");
    if (button) setView(button.dataset.view);
  });
  document.querySelectorAll("[data-goto]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.goto));
  });

  el("refresh-btn").addEventListener("click", async () => {
    if (state.mode === "panel") {
      await refreshSummary();
      await Promise.all([loadChannels(), loadFiles(), loadRuns()]);
      toast("已刷新", "ok");
    } else {
      enterStaticMode();
    }
  });
  el("run-btn").addEventListener("click", startCollect);

  let queryTimer = 0;
  el("file-q").addEventListener("input", () => {
    state.fileQuery = el("file-q").value;
    window.clearTimeout(queryTimer);
    queryTimer = window.setTimeout(() => loadFiles(true), 180);
  });
  el("file-channel").addEventListener("change", () => {
    state.fileChannel = el("file-channel").value;
    loadFiles(true);
  });
  el("file-status").addEventListener("change", () => {
    state.fileStatus = el("file-status").value;
    loadFiles(true);
  });
  el("prev-page").addEventListener("click", () => {
    state.files.offset = Math.max(0, state.files.offset - PAGE_SIZE);
    loadFiles();
  });
  el("next-page").addEventListener("click", () => {
    state.files.offset += PAGE_SIZE;
    loadFiles();
  });

  el("add-channel").addEventListener("click", () => {
    const list = el("channel-list");
    if (list.querySelector(".muted")) list.textContent = "";
    list.appendChild(
      channelCard({
        id: "",
        title: "",
        enabled: true,
        min_size: 1024,
        max_size: MAX_SIZE,
        include_ext: [],
        include_keywords: [],
        exclude_keywords: [],
      }),
    );
  });
  el("save-channels").addEventListener("click", saveChannels);

  el("acc-check").addEventListener("click", async () => {
    if (state.mode !== "panel") return;
    try {
      const result = await api("/account/check", { method: "POST" });
      if (result.ok) {
        const user = result.user || {};
        toast(`账号有效：${user.name || user.username || user.id || ""}`, "ok");
      } else {
        toast(result.reason || "session 无效", "bad");
      }
    } catch (error) {
      toast(error.message, "bad");
    }
  });

  el("acc-logout").addEventListener("click", async () => {
    if (state.mode !== "panel") return;
    try {
      await api("/account/logout", { method: "POST" });
      toast("已退出登录并清理 session", "ok");
      await refreshSummary();
    } catch (error) {
      toast(error.message, "bad");
    }
  });

  el("login-send").addEventListener("click", async () => {
    const apiId = Number(el("login-api-id").value.trim());
    const apiHash = el("login-api-hash").value.trim();
    const phone = el("login-phone").value.trim();
    if (!apiId || !apiHash || !phone) {
      toast("api_id / api_hash / 手机号都要填", "bad");
      return;
    }
    el("login-hint").textContent = "发送中…";
    try {
      await api("/account/login/start", {
        method: "POST",
        body: JSON.stringify({ api_id: apiId, api_hash: apiHash, phone }),
      });
      state.loginStage = "code";
      el("login-step2").hidden = false;
      el("login-step3").hidden = true;
      el("login-pill").textContent = "第 2 步";
      el("login-hint").textContent = "验证码已发送，请查看 Telegram";
      toast("验证码已发送", "ok");
    } catch (error) {
      el("login-hint").textContent = "";
      toast(error.message, "bad");
    }
  });

  el("login-code-btn").addEventListener("click", async () => {
    const code = el("login-code").value.trim();
    if (!code) {
      toast("请输入验证码", "bad");
      return;
    }
    try {
      const result = await api("/account/login/code", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      if (result.need_password) {
        state.loginStage = "password";
        el("login-step3").hidden = false;
        el("login-pill").textContent = "第 3 步";
        toast("该账号开了两步验证，请输入密码", "info");
        return;
      }
      finishLogin(result);
    } catch (error) {
      toast(error.message, "bad");
    }
  });

  el("login-password-btn").addEventListener("click", async () => {
    const password = el("login-password").value;
    if (!password) {
      toast("请输入两步验证密码", "bad");
      return;
    }
    try {
      const result = await api("/account/login/password", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      finishLogin(result);
    } catch (error) {
      toast(error.message, "bad");
    }
  });

  el("run-select").addEventListener("change", () => {
    const value = el("run-select").value;
    if (value) openRun(value);
  });
  el("copy-log").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.logText || "");
      toast("日志已复制", "ok");
    } catch (error) {
      toast("复制失败", "bad");
    }
  });
  el("auto-refresh").addEventListener("change", () => {
    if (el("auto-refresh").checked) schedulePoll(600);
  });

  el("token-btn").addEventListener("click", () => {
    el("token-input").value = state.token;
    el("token-modal").hidden = false;
  });
  el("token-cancel").addEventListener("click", () => {
    el("token-modal").hidden = true;
  });
  el("token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.token = el("token-input").value.trim();
    writeToken(state.token);
    el("token-modal").hidden = true;
    detectMode();
  });
}

function finishLogin(result) {
  const user = result.user || {};
  el("login-step2").hidden = true;
  el("login-step3").hidden = true;
  el("login-pill").textContent = "已登录";
  el("login-hint").textContent = "";
  state.loginStage = "done";
  toast(`登录成功：${user.name || user.username || user.id || ""}`, "ok");
  refreshSummary();
}

bindEvents();
detectMode();
