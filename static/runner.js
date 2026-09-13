"use strict";

// Same-origin JSON transport + a real PTY. Poll cursors survive page navigation/reconnection.
const Runner = {
  active: false, path: "", relative: "", page: 1, gpu: "all", info: null,
  jobs: [], jobId: "", offset: 0, serial: 0, filesSerial: 0, term: null, fit: null,
  polling: false, outputPolling: false, inputQueue: Promise.resolve(), starting: false,
  activeStates: new Set(["preparing", "loading", "creating", "running", "stopping", "cleanup_failed"]),
  labels: {preparing: "准备中", loading: "加载镜像", creating: "创建容器", running: "运行中", stopping: "停止中", succeeded: "已完成", failed: "失败", stopped: "已停止", cleanup_failed: "清理失败，请重试停止"},

  post(action, json) {
    return apiRequest(`/api/tests/${action}`, {method: "POST", json, csrf: true});
  },
  query(action, params) {
    return apiRequest(`/api/tests/${action}?${new URLSearchParams(params)}`);
  },
  init() {
    byId("algorithm-test-button").onclick = () => this.open(state.currentPath.split("/")[0]);
    byId("test-start").onclick = () => this.start();
    byId("test-stop").onclick = () => this.stop(this.jobId);
    byId("test-interrupt").onclick = () => this.sendInput("\x03");
    byId("test-command").onclick = () => this.command();
    byId("test-clear").onclick = () => this.clearOutput();
    byId("test-gpu-button").onclick = () => this.gpuDialog();
    byId("test-gpu-save").onclick = () => {
      const selected = document.querySelector('input[name="test-gpu"]:checked');
      if (selected) this.gpu = selected.value;
      byId("test-gpu-dialog").close();
      this.buttons();
    };
    byId("test-up").onclick = () => {
      this.relative = this.relative.split("/").slice(0, -1).join("/");
      this.page = 1; this.files();
    };
    byId("test-files-refresh").onclick = () => this.files();
    byId("test-files-prev").onclick = () => {this.page -= 1; this.files();};
    byId("test-files-next").onclick = () => {this.page += 1; this.files();};
    byId("test-file-manager").onclick = () => navigateTo(joinPath(this.path, this.relative).replace(/\/$/, ""));
    window.setInterval(() => this.pollJobs(), 1500);
    window.setInterval(() => this.pollOutput(), 300);
    window.setInterval(() => {
      if (this.active && state.authenticated && this.jobs.some(j => j.algorithm === this.path && this.isActive(j))) this.files(true);
    }, 3000);
    new ResizeObserver(() => {
      if (this.active && this.fit) {
        this.fit.fit();
        clearTimeout(this.resizeTimer);
        this.resizeTimer = setTimeout(() => this.resize(), 200);
      }
    }).observe(byId("test-terminal"));
  },
  reset() {
    this.leave(); this.jobs = []; this.jobId = ""; this.jobsSignature = null;
    if (this.term) this.term.reset();
    for (const id of ["test-gpu-dialog", "test-command-dialog"]) {
      if (byId(id).open) byId(id).close();
    }
    byId("running-widget").open = false;
    byId("running-jobs").replaceChildren();
  },
  isActive(job) { return job && this.activeStates.has(job.status); },
  route() {
    const route = new URLSearchParams(location.hash.slice(1));
    if (route.get("view") !== "test") return false;
    this.open(route.get("path") || "", route.get("job") || "", true);
    return true;
  },
  async open(path, jobId = "", fromHistory = false) {
    if (!path) {window.alert("请先选择一个算法文件夹"); return;}
    const serial = ++this.serial;
    try {
      const info = await this.query("info", {path});
      if (serial !== this.serial || !state.authenticated) return;
      this.info = info; this.path = path; this.relative = ""; this.page = 1;
      this.gpu = "all";
      this.jobId = ""; this.offset = 0;
      this.outputGeneration = (this.outputGeneration || 0) + 1;
      if (this.term) this.term.reset();
      byId("test-files").textContent = "正在加载运行文件…";
      this.filesSignature = null;
      this.active = true;
      state.currentPath = path;
      show(document.querySelector(".workspace"), false);
      show(byId("test-view"), true);
      byId("test-title").textContent = path + " · 算法测试";
      byId("test-profile").textContent = info.history
        ? `已参考 ${info.history.started_at.slice(0, 10)} 成功记录${info.history.command.length || Object.keys(info.history.environment).length ? " · 含历史小规模测试参数，请核对运行命令" : ""}`
        : "未收录历史成功记录 · 使用当前 params.json 与测试说明";
      byId("test-archive").replaceChildren(...info.archives.map(name => new Option(name, name)));
      byId("test-archive").value = info.archive;
      this.buttons();
      this.breadcrumbs();
      if (!fromHistory) history.pushState(null, "", `#${new URLSearchParams({path, view: "test", ...(jobId ? {job: jobId} : {})})}`);
      await this.pollJobs();
      if (serial !== this.serial) return;
      const running = this.jobs.find(j => j.algorithm === path && this.isActive(j));
      this.attach(jobId || (running ? running.id : ""));
      this.files(); this.buttons();
    } catch (error) {
      if (serial !== this.serial) return;
      if (error.status !== 401) window.alert(error.code === "docker_not_found" ? "没有找到docker文件" : error.message);
      if (fromHistory) navigateTo(path, {force: true});
    }
  },
  leave() {
    const was = this.active;
    this.serial += 1; this.filesSerial += 1; this.active = false;
    show(byId("test-view"), false);
    show(document.querySelector(".workspace"), true);
    return was;
  },
  breadcrumbs() {
    const target = byId("test-breadcrumbs"); target.replaceChildren();
    for (const [label, path] of [["全部算法", ""], [this.path, this.path], ["算法测试", null]]) {
      if (target.childNodes.length) {
        const sep = document.createElement("span"); sep.textContent = "/"; target.append(sep);
      }
      const button = document.createElement("button"); button.className = "breadcrumb-button";
      button.textContent = label; button.disabled = path === null;
      button.onclick = () => navigateTo(path);
      target.append(button);
    }
  },
  attach(id) {
    this.jobId = id; this.offset = 0;
    this.outputGeneration = (this.outputGeneration || 0) + 1;
    if (!this.term) {
      this.term = new Terminal({cursorBlink: true, fontSize: 13, fontFamily: '"DejaVu Sans Mono", Consolas, monospace',
        scrollback: 6000, theme: {background: "#101827", foreground: "#dce5f5"}, allowProposedApi: false});
      this.fit = new FitAddon.FitAddon(); this.term.loadAddon(this.fit);
      this.term.open(byId("test-terminal"));
      this.term.onData(data => this.sendInput(data));
    }
    this.term.reset(); this.fit.fit();
    if (!id) this.term.writeln("准备就绪。点击「查看运行命令」检查配置，点击「开始」运行算法。\r\n输入仅发送给运行中的容器，不会执行服务器 Shell。");
    else {
      const job = this.jobs.find(j => j.id === id);
      if (job) {this.gpu = job.gpu; byId("test-archive").value = job.archive;}
      this.resize(); this.pollOutput();
    }
    this.buttons();
  },
  async pollJobs() {
    if (!state.authenticated || this.polling) return;
    this.polling = true;
    try {
      const result = await this.query("jobs", {});
      if (!state.authenticated) return;
      this.jobs = result.jobs;
      byId("test-limit").textContent = result.max_jobs;
      byId("running-summary").textContent = `后台任务 · ${result.active} / ${result.max_jobs}`;
      byId("running-widget").classList.toggle("has-active", result.active > 0);
      this.renderJobs();
      if (this.active && !this.jobId) {
        const running = this.jobs.find(j => j.algorithm === this.path && this.isActive(j));
        if (running) this.attach(running.id);
      }
      this.buttons();
    } catch (error) {
      if (error.status !== 401) byId("running-summary").textContent = "后台任务 · 连接中断";
    } finally {this.polling = false;}
  },
  renderJobs() {
    const target = byId("running-jobs");
    // Preserve clickable DOM while polling to avoid swallowing a user's pointer-up.
    const signature = JSON.stringify(this.jobs.map(j => [j.id, j.status, j.exit_code]));
    if (signature === this.jobsSignature) return;
    this.jobsSignature = signature;
    target.replaceChildren();
    if (!this.jobs.length) {target.textContent = "暂无任务。算法运行后可在这里重新连接。"; return;}
    const visibleJobs = [...this.jobs.filter(j => this.isActive(j)), ...this.jobs.filter(j => !this.isActive(j))];
    for (const job of visibleJobs) {
      const row = document.createElement("div"); row.className = "running-job";
      const title = document.createElement("strong"); title.textContent = job.algorithm;
      const meta = document.createElement("small"); meta.textContent = `${this.labels[job.status]} · ${this.gpuLabel(job.gpu)} · ${job.client} · ${job.id.slice(0, 6)}`;
      const actions = document.createElement("div"); actions.className = "running-job-actions";
      const open = document.createElement("button"); open.className = "button secondary small"; open.textContent = "查看终端";
      open.onclick = () => {byId("running-widget").open = false; this.open(job.algorithm, job.id);};
      actions.append(open);
      if (this.isActive(job)) {
        const stop = document.createElement("button"); stop.className = "button subtle small"; stop.textContent = "停止";
        stop.onclick = () => this.stop(job.id); actions.append(stop);
      }
      row.append(title, meta, actions); target.append(row);
    }
  },
  gpuLabel(gpu) {return gpu === "all" ? "全部 GPU" : gpu === "none" ? "不使用 GPU" : `GPU ${gpu}`;},
  buttons() {
    if (!this.active) return;
    const job = this.jobs.find(j => j.id === this.jobId);
    const busy = this.jobs.find(j => j.algorithm === this.path && this.isActive(j));
    byId("test-start").disabled = this.starting;
    byId("test-start").textContent = this.starting ? "提交中…" : busy ? "▷ 开始（已有任务）" : "▷ 开始";
    byId("test-stop").disabled = !this.isActive(job) || job.status === "stopping";
    byId("test-gpu-button").disabled = Boolean(busy);
    byId("test-archive").disabled = Boolean(busy);
    byId("test-clear").disabled = Boolean(busy);
    byId("test-gpu-button").textContent = "选择 GPU · " + this.gpuLabel(this.gpu);
    byId("test-status").textContent = job ? `${this.labels[job.status]} · 任务 ${job.id.slice(0, 8)} · ${this.gpuLabel(job.gpu)}${job.exit_code !== null ? ` · 退出码 ${job.exit_code}` : ""}` : "选择设备后开始测试";
  },
  async pollOutput() {
    if (!this.active || !this.jobId || !state.authenticated || this.outputPolling) return;
    const id = this.jobId, generation = this.outputGeneration;
    this.outputPolling = true;
    try {
      const result = await this.query("output", {id, offset: this.offset});
      if (id !== this.jobId || generation !== this.outputGeneration || !this.active) return;
      if (result.reset) this.term.writeln("\r\n[较早输出超过 2 MiB，已截断]\r\n");
      const bytes = Uint8Array.from(atob(result.data), c => c.charCodeAt(0));
      if (bytes.length) await new Promise(resolve => this.term.write(bytes, resolve));
      if (id !== this.jobId || generation !== this.outputGeneration) return;
      this.offset = result.offset;
      const index = this.jobs.findIndex(j => j.id === id);
      if (index >= 0) this.jobs[index] = result.job;
      byId("test-connection").textContent = this.isActive(result.job) ? "● 已连接 · 实时更新" : "任务已结束 · 输出可回看";
      this.buttons();
    } catch (error) {
      if (error.status !== 401) byId("test-connection").textContent = "连接中断，正在重连…";
    } finally {this.outputPolling = false;}
  },
  sendInput(data) {
    const id = this.jobId;
    if (!this.isActive(this.jobs.find(j => j.id === id))) return;
    // UTF-8 <= 4096 bytes per request; keep keystrokes and pasted chunks in order.
    for (let i = 0; i < data.length; i += 512) {
      const chunk = data.slice(i, i + 512);
      this.inputQueue = this.inputQueue.then(() => this.post("input", {id, data: chunk})).catch(error => {
        byId("test-connection").textContent = error.message;
      });
    }
  },
  async resize() {
    if (!this.active || !this.jobId || !this.term || !state.authenticated) return;
    try {await this.post("resize", {id: this.jobId, rows: Math.max(5, Math.min(200, this.term.rows)), cols: Math.max(20, Math.min(400, this.term.cols))});} catch (_) { /* retried on next resize */ }
  },
  async start() {
    if (this.starting) return;
    if (this.jobs.some(j => j.algorithm === this.path && this.isActive(j))) {
      window.alert("需要先停止在运行的算法"); return;
    }
    this.starting = true; this.buttons();
    const path = this.path, serial = this.serial;
    try {
      const result = await this.post("start", {path, archive: byId("test-archive").value, gpu: this.gpu});
      this.jobs.unshift(result.job);
      if (!this.active || this.path !== path || serial !== this.serial) {this.pollJobs(); return;}
      this.attach(result.job.id);
      history.replaceState(null, "", `#${new URLSearchParams({path: this.path, view: "test", job: result.job.id})}`);
      this.term.focus(); this.pollJobs();
    } catch (error) {
      window.alert(error.code === "algorithm_busy" ? "需要先停止在运行的算法" : error.message);
      this.pollJobs();
    } finally {this.starting = false; this.buttons();}
  },
  async stop(id) {
    if (!id) return;
    const job = this.jobs.find(j => j.id === id);
    if (!window.confirm(`停止 ${job ? job.algorithm : "此"} 算法的当前任务？`)) return;
    try {await this.post("stop", {id}); this.pollJobs();} catch (error) {window.alert(error.message);}
  },
  async command() {
    byId("test-command").disabled = true;
    try {
      const result = await this.post("plan", {path: this.path, archive: byId("test-archive").value, gpu: this.gpu});
      byId("test-command-text").textContent = result.commands.join("\n\n");
      byId("test-command-dialog").showModal();
    } catch (error) {window.alert(error.message);} finally {byId("test-command").disabled = false;}
  },
  async gpuDialog() {
    try {
      this.info = await this.query("info", {path: this.path});
      const target = byId("test-gpu-options"); target.replaceChildren();
      const choices = [["all", "全部 GPU（默认）"], ...this.info.gpus.map(g => [g.index, `GPU ${g.index} · ${g.name} · 显存 ${g.memory_used} / ${g.memory_total} MiB`]), ["none", "不使用 GPU（CPU 模式）"]];
      for (const [value, text] of choices) {
        const label = document.createElement("label"); label.className = "gpu-choice";
        const input = document.createElement("input"); input.type = "radio"; input.name = "test-gpu"; input.value = value; input.checked = value === this.gpu;
        const span = document.createElement("span"); span.textContent = text; label.append(input, span); target.append(label);
      }
      byId("test-gpu-warning").textContent = this.info.warning;
      byId("test-gpu-dialog").showModal();
    } catch (error) {window.alert(error.message);}
  },
  async clearOutput() {
    if (!window.confirm(`将永久删除 ${this.path}/output 内的全部文件和子文件夹，保留 output 目录。此操作不可恢复，确认清空？`)) return;
    try {
      const result = await this.post("clear-output", {path: this.path, confirmed: true});
      toast(`已清空 output，删除 ${result.removed} 项（不可恢复）`);
      this.files(); loadSystem();
    } catch (error) {window.alert(error.message);}
  },
  async files(quiet = false) {
    if (!this.active || !state.authenticated) return;
    const serial = ++this.filesSerial;
    try {
      const result = await this.query("files", {path: this.path, relative: this.relative, page: this.page});
      if (serial !== this.filesSerial || !this.active) return;
      const target = byId("test-files");
      const signature = JSON.stringify([this.path, this.relative, result]);
      if (signature === this.filesSignature) return;
      this.filesSignature = signature;
      target.replaceChildren();
      byId("test-file-path").textContent = "/" + this.relative;
      byId("test-up").disabled = !this.relative;
      byId("test-files-count").textContent = `${this.page} / ${Math.max(1, Math.ceil(result.total / 100))}`;
      byId("test-files-prev").disabled = this.page <= 1;
      byId("test-files-next").disabled = this.page * 100 >= result.total;
      for (const entry of result.entries) {
        const button = document.createElement("button"); button.className = "test-file";
        const icon = document.createElement("b"); icon.textContent = entry.kind === "directory" ? "▸" : "·";
        const name = document.createElement("span"); name.textContent = entry.name;
        const size = document.createElement("small"); size.textContent = entry.kind === "directory" ? "文件夹" : formatSize(entry.size);
        button.append(icon, name, size);
        button.onclick = () => {
          if (entry.kind === "directory") {this.relative = joinPath(this.relative, entry.name); this.page = 1; this.files();}
          else openPreview({...entry, modified: new Date(entry.modified * 1000).toISOString()});
        };
        target.append(button);
      }
      if (!result.entries.length) target.textContent = "此目录没有运行文件";
    } catch (error) {
      if (!quiet && error.status !== 401) toast(error.message, "error");
    }
  },
};
