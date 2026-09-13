"use strict";

const state = {
  authenticated: false,
  csrf: "",
  username: "",
  currentPath: "",
  search: "",
  page: 1,
  pages: 1,
  perPage: 100,
  listSerial: 0,
  previewSerial: 0,
  algorithms: [],
  system: null,
  uploadItems: [],
  uploading: false,
  cancelUploads: false,
  currentXhr: null,
  previewAbortController: null,
};

const dom = {};
const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const textExtensions = new Set([
  "txt", "log", "md", "markdown", "py", "js", "ts", "tsx", "jsx", "css",
  "html", "htm", "json", "jsonl", "yaml", "yml", "xml", "csv", "tsv",
  "ini", "cfg", "conf", "toml", "sh", "bash", "zsh", "fish", "sql", "c",
  "h", "cc", "cpp", "hpp", "java", "go", "rs", "properties", "gitignore",
]);
const imageExtensions = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico"]);
const videoExtensions = new Set(["mp4", "webm", "mov"]);
const audioExtensions = new Set(["mp3", "wav", "ogg", "m4a"]);
const archiveExtensions = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"]);
const officeMathNamespace = "http://schemas.openxmlformats.org/officeDocument/2006/math";
const mathMlNamespace = "http://www.w3.org/1998/Math/MathML";
const mathOperatorCharacters = new Set(Array.from(
  "+-−=<>≤≥≠≈≃≅∼×÷*/·⋅∘±∓()[]{}|,;:!?∈∉∋∑∏∫∬∭∪∩⊂⊃⊆⊇→←↔⇒⇐⇔∞^_′″%&"
));
const codeExtensions = new Set([
  "py", "js", "ts", "tsx", "jsx", "css", "html", "json", "yaml", "yml",
  "sh", "zsh", "c", "h", "cc", "cpp", "java", "go", "rs", "sql",
]);
const documentExtensions = new Set(["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "md", "txt", "csv"]);


class RequestError extends Error {
  constructor(message, status = 0, code = "network_error") {
    super(message);
    this.name = "RequestError";
    this.status = status;
    this.code = code;
  }
}


function byId(id) {
  return document.getElementById(id);
}


function show(element, visible = true) {
  element.classList.toggle("hidden", !visible);
  element.hidden = !visible;
}


function formatSize(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return "—";
  const value = Number(bytes);
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / (1024 ** index);
  const digits = scaled >= 100 || index === 0 ? 0 : scaled >= 10 ? 1 : 2;
  return `${scaled.toFixed(digits)} ${units[index]}`;
}


function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateFormatter.format(date).split("/").join("-");
}


function extensionOf(name) {
  const index = name.lastIndexOf(".");
  return index > 0 && index < name.length - 1 ? name.slice(index + 1).toLowerCase() : "";
}


function joinPath(parent, name) {
  return parent ? `${parent}/${name}` : name;
}


function pathLabel(path) {
  return path || "根目录";
}


function fileEndpoint(path, download = false) {
  const query = new URLSearchParams({ path });
  if (download) query.set("download", "1");
  return `/api/file?${query.toString()}`;
}


function docxEndpoint(path) {
  const query = new URLSearchParams({ path });
  return `/api/docx-file?${query.toString()}`;
}


async function apiRequest(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const request = {
    method: options.method || "GET",
    headers,
    credentials: "same-origin",
    cache: "no-store",
  };
  if (options.signal) request.signal = options.signal;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    request.body = JSON.stringify(options.json);
  } else if (options.body !== undefined) {
    request.body = options.body;
  }
  if (options.csrf) headers.set("X-CSRF-Token", state.csrf);

  let response;
  try {
    response = await fetch(url, request);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new RequestError("无法连接服务器，请检查网络后重试");
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok) {
    if (response.status === 401 && options.handleAuth !== false) {
      switchToLogin("会话已过期，请重新登录");
    }
    throw new RequestError(payload.error || `请求失败（${response.status}）`, response.status, payload.code);
  }
  return payload;
}


function toast(message, type = "success", title = "") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  const marker = document.createElement("span");
  marker.setAttribute("aria-hidden", "true");
  marker.textContent = type === "success" ? "●" : type === "error" ? "!" : "◆";
  const copy = document.createElement("div");
  if (title) {
    const strong = document.createElement("strong");
    strong.textContent = title;
    copy.appendChild(strong);
  }
  const text = document.createElement("span");
  text.textContent = message;
  copy.appendChild(text);
  item.append(marker, copy);
  dom.toastRegion.appendChild(item);
  window.setTimeout(() => item.remove(), type === "error" ? 6000 : 3500);
}


function switchToLogin(message = "") {
  Runner.reset();
  if (state.previewAbortController) state.previewAbortController.abort();
  state.previewAbortController = null;
  state.authenticated = false;
  state.csrf = "";
  state.previewSerial += 1;
  if (dom.previewDialog.open) dom.previewDialog.close();
  if (dom.uploadDialog.open) dom.uploadDialog.close();
  show(dom.appView, false);
  show(dom.loginView, true);
  dom.password.value = "";
  dom.loginError.textContent = message;
  window.setTimeout(() => dom.username.focus(), 0);
}


function switchToApp(session) {
  state.authenticated = true;
  state.csrf = session.csrf;
  state.username = session.username;
  dom.currentUser.textContent = session.username;
  dom.userAvatar.textContent = (Array.from(session.username)[0] || "U").toUpperCase();
  dom.loginError.textContent = "";
  show(dom.loginView, false);
  show(dom.appView, true);
}


async function bootstrap() {
  try {
    const session = await apiRequest("/api/session", { handleAuth: false });
    switchToApp(session);
    await loadWorkspace();
  } catch (error) {
    switchToLogin(error.status && error.status !== 401 ? error.message : "");
  }
}


async function loadWorkspace() {
  const hashPath = pathFromHash();
  state.currentPath = hashPath;
  state.page = 1;
  state.search = "";
  dom.entrySearch.value = "";
  show(dom.clearSearch, false);
  await Promise.allSettled([loadSystem(), loadAlgorithms()]);
  Runner.pollJobs();
  if (Runner.route()) return;
  await loadDirectory();
}


async function handleLogin(event) {
  event.preventDefault();
  const username = dom.username.value.trim();
  const password = dom.password.value;
  if (!username) {
    dom.loginError.textContent = "请输入用户名";
    dom.username.focus();
    return;
  }
  if (!password) {
    dom.loginError.textContent = "请输入密码";
    dom.password.focus();
    return;
  }
  dom.loginButton.disabled = true;
  dom.loginButton.classList.add("is-loading");
  dom.loginError.textContent = "";
  try {
    const session = await apiRequest("/api/login", {
      method: "POST",
      json: { username, password },
      handleAuth: false,
    });
    switchToApp(session);
    await loadWorkspace();
  } catch (error) {
    dom.loginError.textContent = error.message;
    dom.password.select();
  } finally {
    dom.loginButton.disabled = false;
    dom.loginButton.classList.remove("is-loading");
  }
}


async function handleLogout() {
  dom.logoutButton.disabled = true;
  try {
    await apiRequest("/api/logout", { method: "POST", json: {}, csrf: true });
  } catch (_error) {
    // A failed logout still clears the local view; an expired session is already unusable.
  } finally {
    dom.logoutButton.disabled = false;
    switchToLogin();
  }
}


async function loadSystem() {
  try {
    const info = await apiRequest("/api/system");
    state.system = info;
    dom.rootLabel.textContent = info.root_name;
    const free = info.disk.free;
    const total = info.disk.total;
    const mountPoint = info.storage && info.storage.mount_point ? info.storage.mount_point : "数据盘";
    dom.diskText.textContent = `${mountPoint} · 可用 ${formatSize(free)} / ${formatSize(total)}`;
    const warningThreshold = Math.max(info.min_free_bytes, total * 0.05);
    const warning = free < warningThreshold;
    dom.diskStatus.classList.toggle("warning", warning);
    show(dom.storageWarning, warning);
    if (warning) {
      dom.storageWarningText.textContent = `${mountPoint} 当前仅剩 ${formatSize(free)}，上传大文件前请确认容量。系统至少保留 ${formatSize(info.min_free_bytes)}。`;
    }
    dom.uploadHint.textContent = `单文件上限 ${formatSize(info.max_upload_bytes)}；大文件将直接写入数据盘，上传期间请保持页面开启。`;
  } catch (error) {
    if (error.status !== 401) {
      dom.diskText.textContent = "容量读取失败";
      toast(error.message, "error", "无法读取磁盘信息");
    }
  }
}


async function loadAlgorithms() {
  const algorithms = [];
  try {
    let page = 1;
    let pages = 1;
    do {
      const query = new URLSearchParams({
        path: "",
        dirs_only: "1",
        page: String(page),
        per_page: "200",
      });
      const result = await apiRequest(`/api/list?${query.toString()}`);
      algorithms.push(...result.entries);
      pages = result.pagination.pages;
      page += 1;
    } while (page <= pages);
    state.algorithms = algorithms;
    dom.algorithmCount.textContent = String(algorithms.length);
    renderAlgorithms();
  } catch (error) {
    if (error.status !== 401) toast(error.message, "error", "算法目录加载失败");
  }
}


function renderAlgorithms() {
  const filter = dom.algorithmSearch.value.trim().toLowerCase();
  const currentTop = state.currentPath.split("/")[0] || "";
  dom.algorithmList.replaceChildren();

  const allButton = makeAlgorithmButton("", "全部算法", currentTop === "");
  dom.algorithmList.appendChild(allButton);
  let visible = 0;
  for (const entry of state.algorithms) {
    if (filter && !entry.name.toLowerCase().includes(filter)) continue;
    visible += 1;
    dom.algorithmList.appendChild(makeAlgorithmButton(entry.path, entry.name, currentTop === entry.name));
  }
  show(dom.algorithmEmpty, visible === 0 && Boolean(filter));
}


function makeAlgorithmButton(path, label, active) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `algorithm-item${active ? " active" : ""}`;
  button.title = label;
  const icon = document.createElement("span");
  icon.className = "folder-mini";
  icon.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = label;
  button.append(icon, name);
  button.addEventListener("click", () => {
    navigateTo(path);
    closeSidebar();
  });
  return button;
}


function pathFromHash() {
  const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const value = new URLSearchParams(hash).get("path") || "";
  if (value.startsWith("/") || value.split("/").some((part) => part === ".." || part === ".")) return "";
  return value;
}


function updateHash(path, replace = false) {
  const hash = `#${new URLSearchParams({ path }).toString()}`;
  if (window.location.hash === hash) return;
  if (replace) window.history.replaceState(null, "", hash);
  else window.history.pushState(null, "", hash);
}


function navigateTo(path, options = {}) {
  const wasTesting = Runner.leave();
  if (path === state.currentPath && !options.force && !wasTesting) return;
  state.currentPath = path;
  state.page = 1;
  state.search = "";
  dom.entrySearch.value = "";
  show(dom.clearSearch, false);
  if (!options.fromHistory) updateHash(path, Boolean(options.replace));
  renderAlgorithms();
  loadDirectory();
}


async function loadDirectory() {
  const serial = ++state.listSerial;
  setDirectoryLoading(true);
  renderHeading();
  const query = new URLSearchParams({
    path: state.currentPath,
    search: state.search,
    page: String(state.page),
    per_page: String(state.perPage),
  });
  try {
    const result = await apiRequest(`/api/list?${query.toString()}`);
    if (serial !== state.listSerial) return;
    state.currentPath = result.path;
    state.page = result.pagination.page;
    state.pages = result.pagination.pages;
    renderHeading(result.pagination);
    renderEntries(result.entries, result.pagination);
  } catch (error) {
    if (serial !== state.listSerial || error.status === 401) return;
    renderLoadError(error.message);
    toast(error.message, "error", "目录加载失败");
  } finally {
    if (serial === state.listSerial) setDirectoryLoading(false);
  }
}


function setDirectoryLoading(loading) {
  show(dom.loadingState, loading);
  if (loading) {
    show(dom.emptyState, false);
    show(dom.pagination, false);
  }
  dom.refreshButton.disabled = loading;
}


function renderHeading(pagination = null) {
  const parts = state.currentPath ? state.currentPath.split("/") : [];
  dom.directoryTitle.textContent = parts.length ? parts[parts.length - 1] : "全部算法";
  if (!pagination) {
    dom.directorySummary.textContent = "正在加载…";
  } else if (state.search) {
    dom.directorySummary.textContent = `“${state.search}”找到 ${pagination.total} 项`;
  } else {
    dom.directorySummary.textContent = `当前层级共 ${pagination.total} 项`;
  }
  renderBreadcrumbs(parts);
}


function renderBreadcrumbs(parts) {
  dom.breadcrumbs.replaceChildren();
  const root = document.createElement("button");
  root.type = "button";
  root.className = `breadcrumb-button${parts.length ? "" : " current"}`;
  root.textContent = "全部算法";
  root.disabled = parts.length === 0;
  root.addEventListener("click", () => navigateTo(""));
  dom.breadcrumbs.appendChild(root);
  parts.forEach((part, index) => {
    const separator = document.createElement("span");
    separator.className = "breadcrumb-separator";
    separator.textContent = "/";
    separator.setAttribute("aria-hidden", "true");
    const button = document.createElement("button");
    button.type = "button";
    button.className = `breadcrumb-button${index === parts.length - 1 ? " current" : ""}`;
    button.textContent = part;
    button.title = part;
    button.disabled = index === parts.length - 1;
    button.addEventListener("click", () => navigateTo(parts.slice(0, index + 1).join("/")));
    dom.breadcrumbs.append(separator, button);
  });
}


function renderEntries(entries, pagination) {
  dom.fileList.replaceChildren();
  show(dom.emptyState, entries.length === 0);
  if (entries.length === 0) {
    dom.emptyTitle.textContent = state.search ? "未找到匹配项" : "此目录为空";
    dom.emptyDescription.textContent = state.search ? "请尝试其他关键词。" : "可上传文件或创建新文件夹。";
  }
  for (const entry of entries) dom.fileList.appendChild(makeEntryRow(entry));

  show(dom.pagination, pagination.total > 0);
  dom.paginationSummary.textContent = `共 ${pagination.total} 项 · 每页 ${pagination.per_page} 项`;
  dom.pageIndicator.textContent = `${pagination.page} / ${pagination.pages}`;
  dom.previousPage.disabled = pagination.page <= 1;
  dom.nextPage.disabled = pagination.page >= pagination.pages;
}


function renderLoadError(message) {
  dom.fileList.replaceChildren();
  dom.emptyTitle.textContent = "目录加载失败";
  dom.emptyDescription.textContent = message;
  show(dom.emptyState, true);
  show(dom.pagination, false);
  dom.directorySummary.textContent = "加载失败，可点击刷新重试";
}


function makeEntryRow(entry) {
  const row = document.createElement("tr");
  const nameCell = document.createElement("td");
  const nameWrap = document.createElement("div");
  nameWrap.className = "name-cell";
  nameWrap.appendChild(makeFileIcon(entry));
  const name = document.createElement("button");
  name.type = "button";
  name.className = "entry-name";
  name.textContent = entry.name;
  name.title = entry.name;
  name.addEventListener("click", () => openEntry(entry));
  nameWrap.appendChild(name);
  nameCell.appendChild(nameWrap);

  const typeCell = document.createElement("td");
  typeCell.className = "file-type";
  typeCell.textContent = typeLabel(entry);
  const sizeCell = document.createElement("td");
  sizeCell.className = "file-size";
  sizeCell.textContent = entry.kind === "file" ? formatSize(entry.size) : "—";
  const dateCell = document.createElement("td");
  dateCell.className = "file-date";
  dateCell.textContent = formatDate(entry.modified);
  const actionCell = document.createElement("td");
  actionCell.appendChild(makeRowActions(entry));
  row.append(nameCell, typeCell, sizeCell, dateCell, actionCell);
  if (entry.kind === "directory") {
    row.addEventListener("dblclick", (event) => {
      if (!event.target.closest(".row-actions")) navigateTo(entry.path);
    });
  }
  return row;
}


function makeFileIcon(entry) {
  const icon = document.createElement("span");
  icon.setAttribute("aria-hidden", "true");
  if (entry.kind === "directory") {
    icon.className = "file-icon folder";
    return icon;
  }
  if (entry.kind === "symlink") {
    icon.className = "file-icon link";
    icon.textContent = "LINK";
    return icon;
  }
  const ext = extensionOf(entry.name);
  let category = "unknown";
  if (imageExtensions.has(ext)) category = "image";
  else if (archiveExtensions.has(ext)) category = "archive";
  else if (codeExtensions.has(ext)) category = "code";
  else if (documentExtensions.has(ext)) category = "document";
  else if (videoExtensions.has(ext) || audioExtensions.has(ext)) category = "media";
  icon.className = `file-icon ${category}`;
  icon.textContent = (ext || "FILE").slice(0, 4).toUpperCase();
  return icon;
}


function typeLabel(entry) {
  if (entry.kind === "directory") return "文件夹";
  if (entry.kind === "symlink") return "符号链接（受限）";
  if (entry.kind === "other") return "特殊文件（受限）";
  const ext = extensionOf(entry.name);
  return ext ? `${ext.toUpperCase()} 文件` : "文件";
}


function makeSvgIcon(type) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const definitions = {
    open: ["path", "M9 18l6-6-6-6"],
    preview: ["path", "M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6", "circle", "12", "12", "2.5"],
    download: ["path", "M12 3v12m0 0 4-4m-4 4-4-4M5 20h14"],
    delete: ["path", "M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"],
  };
  const values = definitions[type];
  if (type === "preview") {
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", values[1]);
    const circle = document.createElementNS(svg.namespaceURI, "circle");
    circle.setAttribute("cx", values[3]);
    circle.setAttribute("cy", values[4]);
    circle.setAttribute("r", values[5]);
    svg.append(path, circle);
  } else {
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", values[1]);
    svg.appendChild(path);
  }
  return svg;
}


function actionButton(type, label, handler, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `action-button${danger ? " danger" : ""}`;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.appendChild(makeSvgIcon(type));
  button.addEventListener("click", handler);
  return button;
}


function makeRowActions(entry) {
  const actions = document.createElement("div");
  actions.className = "row-actions";
  if (entry.kind === "directory") {
    actions.appendChild(actionButton("open", `进入 ${entry.name}`, () => navigateTo(entry.path)));
  } else if (entry.kind === "file") {
    actions.appendChild(actionButton("preview", `预览 ${entry.name}`, () => openPreview(entry)));
    const download = document.createElement("a");
    download.className = "action-button";
    download.href = fileEndpoint(entry.path, true);
    download.setAttribute("download", "");
    download.title = `下载 ${entry.name}`;
    download.setAttribute("aria-label", `下载 ${entry.name}`);
    download.appendChild(makeSvgIcon("download"));
    actions.appendChild(download);
  }
  actions.appendChild(actionButton("delete", `删除 ${entry.name}`, () => deleteEntry(entry), true));
  return actions;
}


function openEntry(entry) {
  if (entry.kind === "directory") navigateTo(entry.path);
  else if (entry.kind === "file") openPreview(entry);
  else toast("符号链接和特殊文件不能预览或打开", "warning");
}


function previewKind(entry) {
  const ext = extensionOf(entry.name);
  const lower = entry.name.toLowerCase();
  if (imageExtensions.has(ext)) return "image";
  if (ext === "docx") return "docx";
  if (ext === "pdf") return "pdf";
  if (videoExtensions.has(ext)) return "video";
  if (audioExtensions.has(ext)) return "audio";
  if (textExtensions.has(ext) || ["dockerfile", "makefile", "readme", "license"].includes(lower)) return "text";
  return "unsupported";
}


async function openPreview(entry) {
  if (state.previewAbortController) state.previewAbortController.abort();
  state.previewAbortController = null;
  const serial = ++state.previewSerial;
  const kind = previewKind(entry);
  dom.previewTitle.textContent = entry.name;
  dom.previewMeta.textContent = `${typeLabel(entry)} · ${formatSize(entry.size)} · ${formatDate(entry.modified)}`;
  const icon = makeFileIcon(entry);
  icon.classList.add("large");
  dom.previewIcon.replaceWith(icon);
  icon.id = "preview-icon";
  dom.previewIcon = icon;
  dom.previewDownload.href = fileEndpoint(entry.path, true);
  dom.previewDownload.setAttribute("download", entry.name);
  dom.previewBody.className = "preview-body";
  dom.previewBody.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "preview-loading";
  loading.textContent = "正在加载预览…";
  dom.previewBody.appendChild(loading);
  if (!dom.previewDialog.open) dom.previewDialog.showModal();

  const url = fileEndpoint(entry.path, false);
  if (kind === "docx") {
    await renderDocxPreview(entry, serial, loading);
  } else if (kind === "image") {
    const image = document.createElement("img");
    image.className = "preview-media";
    image.alt = entry.name;
    image.addEventListener("load", () => {
      if (serial === state.previewSerial) loading.remove();
    });
    image.addEventListener("error", () => {
      if (serial === state.previewSerial && dom.previewDialog.open) {
        showPreviewError("图片加载失败，可下载后查看");
      }
    });
    image.src = url;
    dom.previewBody.appendChild(image);
  } else if (kind === "pdf") {
    dom.previewBody.replaceChildren();
    const frame = document.createElement("iframe");
    frame.className = "preview-frame";
    frame.title = entry.name;
    frame.src = url;
    dom.previewBody.appendChild(frame);
  } else if (kind === "video" || kind === "audio") {
    dom.previewBody.replaceChildren();
    const media = document.createElement(kind);
    media.className = "preview-media";
    media.controls = true;
    media.preload = "metadata";
    media.src = url;
    dom.previewBody.appendChild(media);
  } else if (kind === "text") {
    dom.previewBody.className = "preview-body text-mode";
    try {
      const query = new URLSearchParams({ path: entry.path });
      const result = await apiRequest(`/api/preview?${query.toString()}`);
      if (serial !== state.previewSerial || !dom.previewDialog.open) return;
      const pre = document.createElement("pre");
      pre.className = "preview-text";
      pre.textContent = result.content;
      dom.previewBody.replaceChildren(pre);
      dom.previewMeta.textContent = `${typeLabel(entry)} · ${formatSize(result.size)} · ${result.encoding}`;
      if (result.truncated) {
        const note = document.createElement("div");
        note.className = "truncated-note";
        note.textContent = `为保证浏览速度，仅显示前 ${formatSize(state.system ? state.system.preview_bytes : 0)}；可下载查看完整文件。`;
        dom.previewBody.appendChild(note);
      }
    } catch (error) {
      if (serial === state.previewSerial && error.status !== 401) showPreviewError(error.message);
    }
  } else {
    dom.previewBody.replaceChildren();
    const unsupported = document.createElement("div");
    unsupported.className = "unsupported-preview";
    const title = document.createElement("strong");
    title.textContent = "此格式暂不支持在线预览";
    const hint = document.createElement("span");
    hint.textContent = "你仍可以使用下方按钮下载原文件。";
    unsupported.append(title, hint);
    dom.previewBody.appendChild(unsupported);
  }
}


async function renderDocxPreview(entry, serial, loading) {
  if (!window.docx || typeof window.docx.renderAsync !== "function" || !window.JSZip) {
    showPreviewError("DOCX 预览组件未能加载，请刷新页面后重试");
    return;
  }
  const controller = new AbortController();
  state.previewAbortController = controller;
  try {
    loading.textContent = "正在校验 DOCX 文档…";
    const infoQuery = new URLSearchParams({ path: entry.path });
    const info = await apiRequest(`/api/docx-info?${infoQuery.toString()}`, {
      signal: controller.signal,
    });
    if (serial !== state.previewSerial || !dom.previewDialog.open) return;
    loading.textContent = `正在读取文档（${formatSize(info.size)}）…`;
    const response = await fetch(docxEndpoint(entry.path), {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch (_error) { payload = {}; }
      if (response.status === 401) switchToLogin("会话已过期，请重新登录");
      throw new RequestError(payload.error || `读取 DOCX 失败（${response.status}）`, response.status, payload.code);
    }
    const expectedSize = Number(info.size);
    const rawResponseSize = response.headers.get("Content-Length");
    const responseSize = rawResponseSize === null ? Number.NaN : Number(rawResponseSize);
    if (!Number.isSafeInteger(expectedSize) || expectedSize < 0 || responseSize !== expectedSize) {
      throw new RequestError("DOCX 文件在校验后发生变化，请重新打开", 409, "docx_changed");
    }
    const documentData = await response.arrayBuffer();
    if (documentData.byteLength !== expectedSize) {
      throw new RequestError("DOCX 文件读取不完整，请重新打开", 409, "docx_incomplete");
    }
    if (serial !== state.previewSerial || !dom.previewDialog.open) return;
    loading.textContent = "正在优化 Word 公式结构…";
    const preparedDocument = await prepareDocxMath(documentData, controller.signal);
    if (serial !== state.previewSerial || !dom.previewDialog.open) return;
    loading.textContent = "正在排版 DOCX 页面…";

    const frame = document.createElement("iframe");
    frame.className = "docx-frame";
    frame.title = `${entry.name} 在线预览`;
    frame.setAttribute("sandbox", "allow-same-origin");
    const loaded = waitForDocxFrame(frame, controller.signal);
    frame.srcdoc = "<!doctype html><html><head><meta charset='utf-8'></head><body><div id='docx-root'></div></body></html>";
    dom.previewBody.appendChild(frame);
    await loaded;
    if (serial !== state.previewSerial || !dom.previewDialog.open) return;
    const frameDocument = frame.contentDocument;
    const container = frameDocument && frameDocument.getElementById("docx-root");
    if (!frameDocument || !container) {
      throw new RequestError("无法创建 DOCX 预览页面，请刷新后重试", 0, "docx_frame_error");
    }
    let renderTimeout = 0;
    try {
      await Promise.race([
        window.docx.renderAsync(preparedDocument.data, container, frameDocument.head, {
          breakPages: true,
          ignoreLastRenderedPageBreak: false,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
          renderComments: false,
          renderAltChunks: false,
          useBase64URL: true,
          debug: false,
        }),
        new Promise((_resolve, reject) => {
          renderTimeout = window.setTimeout(
            () => reject(new RequestError("DOCX 排版超时，请下载后查看", 0, "docx_render_timeout")),
            45_000,
          );
        }),
        new Promise((_resolve, reject) => controller.signal.addEventListener(
          "abort",
          () => reject(new DOMException("DOCX preview cancelled", "AbortError")),
          { once: true },
        )),
      ]);
    } finally {
      if (renderTimeout) window.clearTimeout(renderTimeout);
    }
    if (serial !== state.previewSerial || !dom.previewDialog.open) return;
    const renderedMathCount = normalizeDocxMath(frameDocument);
    sanitizeDocxDocument(frameDocument);
    const customStyle = frameDocument.createElement("style");
    customStyle.textContent = "html,body{min-height:100%;margin:0;background:#eef2f6}body{padding:24px;box-sizing:border-box}.docx-wrapper{padding:0!important;background:transparent!important}.docx-wrapper>section.docx{margin:0 auto 22px!important;box-shadow:0 3px 18px rgba(25,42,70,.14)!important}math{font-family:'Cambria Math','STIX Two Math','DejaVu Math TeX Gyre',math,serif}@media(max-width:700px){body{padding:10px}}";
    frameDocument.head.appendChild(customStyle);
    fitDocxMath(frameDocument);
    frame.classList.add("ready");
    loading.remove();
    const mathMeta = renderedMathCount ? ` · ${renderedMathCount} 个公式已优化` : "";
    dom.previewMeta.textContent = `DOCX 文件 · ${formatSize(info.size)} · 解压后 ${formatSize(info.unpacked_size)} · ${info.entries} 个文档项${mathMeta}`;
  } catch (error) {
    if (error.name === "AbortError" || serial !== state.previewSerial) return;
    if (error.status !== 401) showPreviewError(error.message || "DOCX 预览失败");
  } finally {
    if (state.previewAbortController === controller) state.previewAbortController = null;
  }
}


async function prepareDocxMath(documentData, signal) {
  if (signal.aborted) throw new DOMException("DOCX preview cancelled", "AbortError");
  const archive = await window.JSZip.loadAsync(documentData);
  const documentPart = archive.file("word/document.xml");
  if (!documentPart) return { data: documentData, accents: 0 };
  const xmlText = await documentPart.async("string");
  if (signal.aborted) throw new DOMException("DOCX preview cancelled", "AbortError");
  const xmlDocument = new DOMParser().parseFromString(xmlText, "application/xml");
  if (xmlDocument.querySelector("parsererror")) return { data: documentData, accents: 0 };
  const combinedScripts = Array.from(
    xmlDocument.getElementsByTagNameNS(officeMathNamespace, "sSubSup")
  );
  for (const combined of combinedScripts) rewriteCombinedSubSup(combined);
  const accents = Array.from(xmlDocument.getElementsByTagNameNS(officeMathNamespace, "acc"));
  if (!accents.length && !combinedScripts.length) return { data: documentData, accents: 0 };

  for (const accent of accents) {
    const prefix = accent.prefix || "m";
    const properties = Array.from(accent.children).find((child) => (
      child.namespaceURI === officeMathNamespace && child.localName === "accPr"
    ));
    if (properties) {
      const groupProperties = renameXmlElement(properties, "groupChrPr");
      const character = Array.from(groupProperties.children).find((child) => (
        child.namespaceURI === officeMathNamespace && child.localName === "chr"
      ));
      if (character) {
        const value = character.getAttributeNS(officeMathNamespace, "val")
          || character.getAttribute("val") || "";
        character.setAttributeNS(
          officeMathNamespace,
          prefix + ":val",
          normalizeAccentCharacter(value),
        );
      }
      let vertical = Array.from(groupProperties.children).find((child) => (
        child.namespaceURI === officeMathNamespace && child.localName === "vertJc"
      ));
      if (!vertical) {
        vertical = xmlDocument.createElementNS(officeMathNamespace, prefix + ":vertJc");
        groupProperties.appendChild(vertical);
      }
      // docx-preview maps groupChr + vertJc=bot to MathML mover (accent above).
      vertical.setAttributeNS(officeMathNamespace, prefix + ":val", "bot");
    }
    renameXmlElement(accent, "groupChr");
  }

  archive.file("word/document.xml", new XMLSerializer().serializeToString(xmlDocument));
  const data = await archive.generateAsync({
    type: "arraybuffer",
    compression: "DEFLATE",
    compressionOptions: { level: 6 },
  });
  if (signal.aborted) throw new DOMException("DOCX preview cancelled", "AbortError");
  return { data, accents: accents.length, combinedScripts: combinedScripts.length };
}


function rewriteCombinedSubSup(combined) {
  const directChildren = Array.from(combined.children);
  const base = directChildren.find((child) => child.localName === "e");
  const sub = directChildren.find((child) => child.localName === "sub");
  const sup = directChildren.find((child) => child.localName === "sup");
  if (!base || (!sub && !sup)) return;
  const prefix = combined.prefix || "m";
  const document = combined.ownerDocument;
  const superscript = document.createElementNS(officeMathNamespace, prefix + ":sSup");
  const outerBase = document.createElementNS(officeMathNamespace, prefix + ":e");
  const subscript = document.createElementNS(officeMathNamespace, prefix + ":sSub");
  subscript.appendChild(base);
  if (sub) subscript.appendChild(sub);
  else subscript.appendChild(document.createElementNS(officeMathNamespace, prefix + ":sub"));
  outerBase.appendChild(subscript);
  superscript.appendChild(outerBase);
  if (sup) superscript.appendChild(sup);
  else superscript.appendChild(document.createElementNS(officeMathNamespace, prefix + ":sup"));
  combined.replaceWith(superscript);
}


function renameXmlElement(element, localName) {
  const qualifiedName = element.prefix ? element.prefix + ":" + localName : localName;
  const replacement = element.ownerDocument.createElementNS(element.namespaceURI, qualifiedName);
  for (const attribute of Array.from(element.attributes)) {
    replacement.setAttributeNS(attribute.namespaceURI, attribute.name, attribute.value);
  }
  while (element.firstChild) replacement.appendChild(element.firstChild);
  element.replaceWith(replacement);
  return replacement;
}


function normalizeAccentCharacter(value) {
  return new Map([
    ["\u0302", "^"],
    ["\u0303", "~"],
    ["\u0304", "¯"],
    ["\u0305", "¯"],
    ["\u0307", "˙"],
    ["\u0308", "¨"],
  ]).get(value) || value || "^";
}


function normalizeDocxMath(frameDocument) {
  const formulas = Array.from(frameDocument.getElementsByTagNameNS(mathMlNamespace, "math"));
  for (const formula of formulas) {
    const declaredSize = Array.from(formula.querySelectorAll("[style]"))
      .map((node) => node.style.fontSize)
      .find(Boolean);

    for (const stringToken of Array.from(
      formula.getElementsByTagNameNS(mathMlNamespace, "ms")
    )) {
      replaceMathStringToken(stringToken);
    }

    for (const tokenName of ["mn", "mi", "mo", "mtext"]) {
      for (const token of Array.from(
        formula.getElementsByTagNameNS(mathMlNamespace, tokenName)
      )) {
        if (token.children.length) replaceMathElementTag(token, "mrow");
      }
    }

    for (const limit of Array.from(
      formula.getElementsByTagNameNS(mathMlNamespace, "munderover")
    )) {
      normalizeMathLimits(limit);
    }

    for (const node of Array.from(formula.querySelectorAll("[style]"))) {
      node.style.removeProperty("font-family");
      node.style.removeProperty("font-size");
      node.style.removeProperty("min-height");
      node.style.removeProperty("line-height");
      if (!node.getAttribute("style")) node.removeAttribute("style");
    }
    formula.style.fontFamily = "'Cambria Math','STIX Two Math','DejaVu Math TeX Gyre',math,serif";
    if (declaredSize) formula.style.fontSize = declaredSize;

    for (const mover of Array.from(
      formula.getElementsByTagNameNS(mathMlNamespace, "mover")
    )) {
      const accent = mover.lastElementChild;
      if (accent && /^[~^¯˙¨]$/u.test(accent.textContent.trim())) {
        mover.setAttribute("accent", "true");
      }
    }
  }
  return formulas.length;
}


function replaceMathStringToken(token) {
  const text = token.textContent || "";
  const tokens = [];
  let buffer = "";
  let kind = "";
  const flush = () => {
    if (!buffer) return;
    const item = token.ownerDocument.createElementNS(mathMlNamespace, kind);
    item.textContent = buffer;
    copyMathTokenAttributes(token, item);
    tokens.push(item);
    buffer = "";
  };

  for (const character of Array.from(text)) {
    let nextKind;
    if (/\s/u.test(character)) nextKind = "mtext";
    else if (mathOperatorCharacters.has(character)) nextKind = "mo";
    else if (/\p{Number}/u.test(character)) nextKind = "mn";
    else nextKind = "mi";
    if (nextKind === "mo") {
      flush();
      kind = "mo";
      buffer = character;
      flush();
    } else if (kind && kind !== nextKind) {
      flush();
      kind = nextKind;
      buffer = character;
    } else {
      kind = nextKind;
      buffer += character;
    }
  }
  flush();

  if (!tokens.length) {
    token.remove();
  } else if (tokens.length === 1) {
    token.replaceWith(tokens[0]);
  } else {
    const row = token.ownerDocument.createElementNS(mathMlNamespace, "mrow");
    row.append(...tokens);
    token.replaceWith(row);
  }
}


function copyMathTokenAttributes(source, target) {
  for (const attribute of Array.from(source.attributes)) {
    if (attribute.name !== "style") {
      target.setAttributeNS(attribute.namespaceURI, attribute.name, attribute.value);
    }
  }
  target.style.cssText = source.style.cssText;
}


function replaceMathElementTag(element, tagName) {
  const replacement = element.ownerDocument.createElementNS(mathMlNamespace, tagName);
  for (const attribute of Array.from(element.attributes)) {
    replacement.setAttributeNS(attribute.namespaceURI, attribute.name, attribute.value);
  }
  while (element.firstChild) replacement.appendChild(element.firstChild);
  element.replaceWith(replacement);
  return replacement;
}


function normalizeMathLimits(limit) {
  const [base, lower, upper] = Array.from(limit.children);
  const lowerEmpty = !lower || !lower.textContent.trim();
  const upperEmpty = !upper || !upper.textContent.trim();
  if (!base) return;
  if (lowerEmpty && upperEmpty) {
    limit.replaceWith(base);
  } else if (upperEmpty) {
    const replacement = limit.ownerDocument.createElementNS(mathMlNamespace, "munder");
    replacement.append(base, lower);
    limit.replaceWith(replacement);
  } else if (lowerEmpty) {
    const replacement = limit.ownerDocument.createElementNS(mathMlNamespace, "mover");
    replacement.append(base, upper);
    limit.replaceWith(replacement);
  }
}


function fitDocxMath(frameDocument) {
  for (const formula of Array.from(
    frameDocument.getElementsByTagNameNS(mathMlNamespace, "math")
  )) {
    const paragraph = formula.closest("p");
    if (!paragraph) continue;
    const available = paragraph.getBoundingClientRect().width;
    const width = formula.getBoundingClientRect().width;
    if (!available || width <= available) continue;
    const fontSize = Number.parseFloat(frameDocument.defaultView.getComputedStyle(formula).fontSize);
    if (!fontSize) continue;
    const fittedSize = Math.max(9, fontSize * (available / width) * 0.97);
    formula.style.fontSize = fittedSize.toFixed(2) + "px";
  }
}


function waitForDocxFrame(frame, signal) {
  return new Promise((resolve, reject) => {
    let timeout = 0;
    const cleanup = () => {
      frame.removeEventListener("load", onLoad);
      signal.removeEventListener("abort", onAbort);
      if (timeout) window.clearTimeout(timeout);
    };
    const onLoad = () => {
      cleanup();
      resolve();
    };
    const onAbort = () => {
      cleanup();
      reject(new DOMException("DOCX preview cancelled", "AbortError"));
    };
    frame.addEventListener("load", onLoad, { once: true });
    signal.addEventListener("abort", onAbort, { once: true });
    timeout = window.setTimeout(() => {
      cleanup();
      reject(new RequestError("DOCX 预览页面加载超时", 0, "docx_frame_timeout"));
    }, 5_000);
  });
}


function sanitizeDocxDocument(frameDocument) {
  frameDocument.querySelectorAll("script,iframe,object,embed,form,input,button,meta[http-equiv],link").forEach((node) => node.remove());
  frameDocument.querySelectorAll("*").forEach((node) => {
    for (const attribute of Array.from(node.attributes)) {
      if (attribute.name.toLowerCase().startsWith("on")) node.removeAttribute(attribute.name);
    }
    if (node.matches("a[href]")) {
      const href = node.getAttribute("href").trim();
      if (href.startsWith("#")) {
        node.setAttribute("target", "_self");
        node.removeAttribute("rel");
      } else if (/^(https?:|mailto:)/i.test(href)) {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      } else node.removeAttribute("href");
    } else if (node.hasAttribute("href")) {
      const href = node.getAttribute("href").trim();
      if (!/^(data:image\/|blob:)/i.test(href)) node.removeAttribute("href");
    }
    if (node.hasAttribute("src")) {
      const src = node.getAttribute("src").trim();
      if (!/^(data:|blob:)/i.test(src)) node.removeAttribute("src");
    }
  });
}


function showPreviewError(message) {
  dom.previewBody.className = "preview-body";
  const error = document.createElement("div");
  error.className = "preview-error";
  const title = document.createElement("strong");
  title.textContent = "预览加载失败";
  const text = document.createElement("span");
  text.textContent = message;
  error.append(title, text);
  dom.previewBody.replaceChildren(error);
}


function closePreview() {
  if (state.previewAbortController) state.previewAbortController.abort();
  state.previewAbortController = null;
  state.previewSerial += 1;
  dom.previewBody.replaceChildren();
  if (dom.previewDialog.open) dom.previewDialog.close();
}


async function createDirectory() {
  const name = window.prompt(`在“${pathLabel(state.currentPath)}”中新建文件夹：`, "");
  if (name === null) return;
  if (!name.trim()) {
    toast("文件夹名称不能为空", "warning");
    return;
  }
  dom.mkdirButton.disabled = true;
  try {
    await apiRequest("/api/mkdir", {
      method: "POST",
      json: { path: state.currentPath, name },
      csrf: true,
    });
    toast(`已创建文件夹“${name.trim()}”`);
    await loadDirectory();
    if (!state.currentPath) await loadAlgorithms();
  } catch (error) {
    if (error.status !== 401) toast(error.message, "error", "创建失败");
  } finally {
    dom.mkdirButton.disabled = false;
  }
}


async function deleteEntry(entry) {
  let confirmed = false;
  if (entry.kind === "directory" && state.system && state.system.recursive_delete) {
    const typed = window.prompt(`将永久删除文件夹及其中全部内容：\n${entry.path}\n\n请输入文件夹名称“${entry.name}”确认：`, "");
    confirmed = typed === entry.name;
    if (typed !== null && !confirmed) toast("输入的文件夹名称不匹配，已取消删除", "warning");
  } else {
    const note = entry.kind === "directory"
      ? "默认安全设置只允许删除空文件夹。"
      : "删除后无法在网页中恢复。";
    confirmed = window.confirm(`确认删除？\n\n${entry.path}\n\n${note}`);
  }
  if (!confirmed) return;
  try {
    await apiRequest("/api/delete", {
      method: "POST",
      json: { path: entry.path },
      csrf: true,
    });
    toast(`已删除“${entry.name}”`);
    await Promise.allSettled([
      loadDirectory(),
      loadSystem(),
      state.currentPath ? Promise.resolve() : loadAlgorithms(),
    ]);
  } catch (error) {
    if (error.status !== 401) toast(error.message, "error", "删除失败");
  }
}


function openUploadDialog() {
  state.uploadItems = [];
  state.uploading = false;
  state.cancelUploads = false;
  dom.fileInput.value = "";
  dom.uploadTarget.textContent = pathLabel(state.currentPath);
  dom.startUpload.disabled = true;
  dom.startUpload.textContent = "开始上传";
  renderUploadItems();
  dom.uploadDialog.showModal();
}


function addUploadFiles(fileList) {
  const existing = new Set(state.uploadItems.map((item) => `${item.file.name}\0${item.file.size}\0${item.file.lastModified}`));
  for (const file of Array.from(fileList)) {
    const key = `${file.name}\0${file.size}\0${file.lastModified}`;
    if (existing.has(key)) continue;
    existing.add(key);
    const tooLarge = state.system && file.size > state.system.max_upload_bytes;
    state.uploadItems.push({
      file,
      progress: 0,
      status: tooLarge ? "error" : "ready",
      message: tooLarge ? "超过单文件上限" : "等待上传",
    });
  }
  renderUploadItems();
}


function renderUploadItems() {
  dom.uploadList.replaceChildren();
  for (const item of state.uploadItems) {
    const row = document.createElement("div");
    row.className = `upload-row ${item.status}`;
    const copy = document.createElement("div");
    copy.className = "upload-file-copy";
    const name = document.createElement("strong");
    name.textContent = item.file.name;
    name.title = item.file.name;
    const size = document.createElement("span");
    size.textContent = formatSize(item.file.size);
    copy.append(name, size);
    const track = document.createElement("div");
    track.className = "progress-track";
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", String(Math.round(item.progress)));
    const bar = document.createElement("div");
    bar.className = "progress-bar";
    bar.style.width = `${item.progress}%`;
    track.appendChild(bar);
    const status = document.createElement("span");
    status.className = "upload-state";
    status.textContent = item.message;
    status.title = item.message;
    row.append(copy, track, status);
    dom.uploadList.appendChild(row);
  }
  const ready = state.uploadItems.some((item) => item.status === "ready");
  dom.startUpload.disabled = state.uploading || !ready;
}


async function startUploads() {
  if (state.uploading) return;
  const mode = dom.conflictMode.value;
  if (mode === "overwrite" && !window.confirm("同名文件将被永久覆盖。确认使用覆盖模式吗？")) return;
  state.uploading = true;
  state.cancelUploads = false;
  dom.startUpload.disabled = true;
  dom.startUpload.textContent = "上传中…";
  dom.conflictMode.disabled = true;
  let succeeded = 0;
  let failed = 0;

  for (const item of state.uploadItems) {
    if (state.cancelUploads) break;
    if (item.status !== "ready") {
      if (item.status === "error") failed += 1;
      continue;
    }
    item.status = "uploading";
    item.message = "0%";
    renderUploadItems();
    try {
      const result = await uploadOne(item, mode);
      item.progress = 100;
      item.status = "success";
      item.message = result.renamed ? `完成：${result.name}` : "上传完成";
      succeeded += 1;
    } catch (error) {
      if (error.code === "upload_cancelled") {
        item.status = "ready";
        item.message = "已取消";
      } else {
        item.status = "error";
        item.message = error.message;
        failed += 1;
        if (error.status === 401) {
          switchToLogin("会话已过期，请重新登录");
          break;
        }
      }
    }
    renderUploadItems();
  }

  state.uploading = false;
  state.currentXhr = null;
  dom.conflictMode.disabled = false;
  dom.startUpload.textContent = "开始上传";
  renderUploadItems();
  if (succeeded) {
    toast(`上传完成 ${succeeded}/${succeeded + failed}`, failed ? "warning" : "success");
    await Promise.allSettled([loadDirectory(), loadSystem(), state.currentPath ? Promise.resolve() : loadAlgorithms()]);
  } else if (failed && state.authenticated) {
    toast("没有文件上传成功，请查看每个文件的错误信息", "error");
  }
}


function uploadOne(item, mode) {
  return new Promise((resolve, reject) => {
    const query = new URLSearchParams({
      path: state.currentPath,
      filename: item.file.name,
      conflict: mode,
    });
    const xhr = new XMLHttpRequest();
    state.currentXhr = xhr;
    xhr.open("POST", `/api/upload?${query.toString()}`);
    xhr.responseType = "json";
    xhr.setRequestHeader("X-CSRF-Token", state.csrf);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      item.progress = Math.min(99, (event.loaded / event.total) * 100);
      item.message = `${Math.round(item.progress)}%`;
      renderUploadItems();
    });
    xhr.addEventListener("load", () => {
      const payload = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
      else reject(new RequestError(payload.error || `上传失败（${xhr.status}）`, xhr.status, payload.code));
    });
    xhr.addEventListener("error", () => reject(new RequestError("网络中断，上传失败")));
    xhr.addEventListener("abort", () => reject(new RequestError("上传已取消", 0, "upload_cancelled")));
    xhr.send(item.file);
  });
}


function closeUploadDialog() {
  if (state.uploading) {
    if (!window.confirm("文件仍在上传，确定取消剩余任务并关闭吗？")) return;
    state.cancelUploads = true;
    if (state.currentXhr) state.currentXhr.abort();
  }
  if (dom.uploadDialog.open) dom.uploadDialog.close();
}


function openSidebar() {
  dom.sidebar.classList.add("open");
  show(dom.sidebarBackdrop, true);
}


function closeSidebar() {
  dom.sidebar.classList.remove("open");
  show(dom.sidebarBackdrop, false);
}


function bindEvents() {
  dom.loginForm.addEventListener("submit", handleLogin);
  dom.togglePassword.addEventListener("click", () => {
    const showing = dom.password.type === "text";
    dom.password.type = showing ? "password" : "text";
    dom.togglePassword.textContent = showing ? "显示" : "隐藏";
    dom.togglePassword.setAttribute("aria-label", showing ? "显示密码" : "隐藏密码");
  });
  dom.logoutButton.addEventListener("click", handleLogout);
  dom.refreshButton.addEventListener("click", () => loadDirectory());
  dom.mkdirButton.addEventListener("click", createDirectory);
  dom.uploadButton.addEventListener("click", openUploadDialog);
  dom.previousPage.addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      loadDirectory();
    }
  });
  dom.nextPage.addEventListener("click", () => {
    if (state.page < state.pages) {
      state.page += 1;
      loadDirectory();
    }
  });

  let searchTimer = 0;
  dom.entrySearch.addEventListener("input", () => {
    show(dom.clearSearch, Boolean(dom.entrySearch.value));
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.search = dom.entrySearch.value.trim();
      state.page = 1;
      loadDirectory();
    }, 320);
  });
  dom.clearSearch.addEventListener("click", (event) => {
    event.preventDefault();
    dom.entrySearch.value = "";
    state.search = "";
    state.page = 1;
    show(dom.clearSearch, false);
    loadDirectory();
    dom.entrySearch.focus();
  });
  dom.algorithmSearch.addEventListener("input", renderAlgorithms);
  dom.sidebarToggle.addEventListener("click", openSidebar);
  dom.sidebarBackdrop.addEventListener("click", closeSidebar);

  dom.dropZone.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => addUploadFiles(dom.fileInput.files));
  for (const eventName of ["dragenter", "dragover"]) {
    dom.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dom.dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dom.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dom.dropZone.classList.remove("dragging");
    });
  }
  dom.dropZone.addEventListener("drop", (event) => addUploadFiles(event.dataTransfer.files));
  dom.startUpload.addEventListener("click", startUploads);

  document.querySelectorAll(".modal-close").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog === dom.uploadDialog) closeUploadDialog();
      else if (dialog === dom.previewDialog) closePreview();
      else if (dialog && dialog.open) dialog.close();
    });
  });
  dom.uploadDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeUploadDialog();
  });
  dom.previewDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closePreview();
  });
  dom.previewDialog.addEventListener("close", () => {
    if (state.previewAbortController) state.previewAbortController.abort();
    state.previewAbortController = null;
    state.previewSerial += 1;
    dom.previewBody.replaceChildren();
  });

  window.addEventListener("hashchange", () => {
    if (!state.authenticated) return;
    if (Runner.route()) return;
    const path = pathFromHash();
    if (path !== state.currentPath || Runner.active) navigateTo(path, { fromHistory: true });
  });
}


function cacheDom() {
  const ids = [
    "toast-region", "login-view", "app-view", "login-form", "username", "password",
    "toggle-password", "login-error", "login-button", "root-label", "disk-status",
    "disk-text", "storage-warning", "storage-warning-text", "current-user", "user-avatar",
    "logout-button", "sidebar", "sidebar-toggle", "sidebar-backdrop", "algorithm-count",
    "algorithm-search", "algorithm-list", "algorithm-empty", "breadcrumbs", "directory-title",
    "directory-summary", "refresh-button", "mkdir-button", "upload-button", "entry-search",
    "clear-search", "file-list", "loading-state", "empty-state", "empty-title",
    "empty-description", "pagination", "pagination-summary", "page-indicator", "previous-page",
    "next-page", "upload-dialog", "upload-target", "file-input", "drop-zone", "conflict-mode",
    "upload-list", "upload-hint", "start-upload", "preview-dialog", "preview-icon",
    "preview-title", "preview-meta", "preview-body", "preview-download",
  ];
  for (const id of ids) {
    const key = id.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
    dom[key] = byId(id);
  }
}


document.addEventListener("DOMContentLoaded", () => {
  cacheDom();
  bindEvents();
  Runner.init();
  bootstrap();
});
