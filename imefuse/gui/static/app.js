const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, text) => {
  const n = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
  if (text !== undefined) n.textContent = text;
  return n;
};

let state = { imes: [], exports: [], backups: [] };

async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opt);
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data;
}

// ---- 进度条：只有拿到真实阶段时才画分段，否则只走不确定进度 ----------
function makeBar(host, label) {
  host.innerHTML = "";
  const wrap = el("div", { class: "prog" });
  const head = el("div", { class: "prog-head" }, label || "");
  const track = el("div", { class: "prog-track" });
  const fill = el("div", { class: "prog-fill" });
  track.appendChild(fill);
  const tail = el("div", { class: "prog-msg" }, "");
  wrap.appendChild(head);
  wrap.appendChild(track);
  wrap.appendChild(tail);
  host.appendChild(wrap);
  wrap.classList.add("indeterminate");
  const started = Date.now();
  const timer = setInterval(() => {
    const secs = ((Date.now() - started) / 1000).toFixed(1);
    head.textContent = (label || "") + "  " + secs + "s";
  }, 100);
  return {
    step(done, total, msg) {
      if (total) {
        wrap.classList.remove("indeterminate");
        fill.style.width = Math.round((done / total) * 100) + "%";
        head.textContent = `[${done}/${total}] ${label || ""}`;
      }
      if (msg !== undefined) tail.textContent = msg;
    },
    done(msg) {
      clearInterval(timer);
      wrap.classList.remove("indeterminate");
      wrap.classList.add("done");
      fill.style.width = "100%";
      if (msg !== undefined) tail.textContent = msg;
      setTimeout(() => { host.innerHTML = ""; }, 1600);
    },
    fail(msg) {
      clearInterval(timer);
      wrap.classList.remove("indeterminate");
      wrap.classList.add("failed");
      tail.textContent = msg || "失败";
    },
  };
}


const LEVEL_BADGE = {
  ready: ["ready", "就绪"],
  limited: ["limited", "可用·受限"],
  blocked: ["blocked", "不可用"],
};

function renderCards() {
  const box = $("#cards");
  box.innerHTML = "";
  state.imes.forEach((ime) => {
    const card = el("div", { class: "card" });
    const h = el("h3");
    h.textContent = ime.label;
    const [lcls, ltext] = LEVEL_BADGE[ime.level] || ["off", ime.level || "?"];
    h.appendChild(el("span", { class: "badge " + lcls }, ltext));
    if (ime.state === "ready") {
      const writable = (ime.caps || {}).writable !== false;
      h.appendChild(el("span", { class: "badge " + (writable ? "ready" : "na") },
        writable ? "可读可写" : "只读"));
    }
    if (ime.installed) {
      h.appendChild(el("span", { class: "badge " + (ime.running ? "on" : "off") },
        ime.running ? "运行中" : "已停止"));
    }
    card.appendChild(h);
    card.appendChild(el("div", { class: "ver" },
      "v" + (ime.version || "?") + (ime.store ? " · " + ime.store : "")));

    const num = el("div", { class: "num" });
    if (ime.records === null || ime.records === undefined) {
      num.textContent = "—";
    } else {
      num.textContent = ime.records;
      num.appendChild(el("small", {}, "条个人词"));
    }
    card.appendChild(num);
    if (ime.detail) card.appendChild(el("div", { class: "meta" }, ime.detail));

    const notes = ime.limits || [];
    if (notes.length) {
      const ul = el("ul", { class: "limits" });
      notes.forEach((n) => {
        const mark = n.level === "blocked" ? "✕ " : n.level === "limited" ? "! " : "· ";
        ul.appendChild(el("li", { class: n.level }, mark + n.text));
      });
      card.appendChild(ul);
    }
    box.appendChild(card);
  });
}

function fillSelect(sel, items, valueKey, labelFn) {
  const node = $(sel);
  const keep = node.value;
  node.innerHTML = "";
  if (!items.length) {
    node.appendChild(el("option", { value: "" }, "（本机没有可用的输入法）"));
    return;
  }
  items.forEach((it) => {
    const o = el("option", { value: it[valueKey] }, labelFn(it));
    node.appendChild(o);
  });
  if (keep && items.some((i) => String(i[valueKey]) === keep)) node.value = keep;
}

function imeLabel(i) {
  const mark = i.level === "limited" ? " ⚠" : "";
  return i.label + mark;
}

function renderSelectors() {
  // 受限的仍可选（比如豆包还能再写一千多条），不可用的不出现
  const pick = state.imes.filter((i) => i.level !== "blocked");
  fillSelect("#exportIme", pick, "ime", imeLabel);
  fillSelect("#targetIme", pick, "ime", imeLabel);
  fillSelect("#backupIme", state.imes.filter((i) => i.installed), "ime", imeLabel);
  fillSelect("#migSource", pick, "ime", imeLabel);
  fillSelect("#migTarget", pick, "ime", imeLabel);
  fillSelect("#fileList", state.exports, "file",
    (e) => `${e.file} · ${e.count} 条 · ${e.source || "?"}`);
}

function renderBackups() {
  const box = $("#backupList");
  box.innerHTML = "";
  if (!state.backups.length) {
    box.appendChild(el("p", { class: "hint" }, "还没有备份。"));
    return;
  }
  const t = el("table");
  t.innerHTML = "<thead><tr><th>时间</th><th>输入法</th><th>标签</th><th>大小</th><th></th></tr></thead>";
  const tb = el("tbody");
  state.backups.forEach((b) => {
    const tr = el("tr");
    tr.appendChild(el("td", {}, (b.at || b.name).replace(/-/g, (m, i) => (i === 8 ? " " : "-"))));
    tr.appendChild(el("td", {}, b.ime || "?"));
    tr.appendChild(el("td", {}, b.label || ""));
    tr.appendChild(el("td", {}, b.size || ""));
    const td = el("td");
    const btn = el("button", {}, "回滚到此备份");
    btn.onclick = async () => {
      if (!confirm(`确认把 ${b.ime} 回滚到 ${b.name}？当前数据会被覆盖。`)) return;
      btn.disabled = true;
      btn.textContent = "回滚中…";
      try {
        await api("/api/restore", { name: b.name, ime: b.ime });
        await refresh();
      } catch (e) {
        alert("回滚失败：" + e.message);
        btn.disabled = false;
        btn.textContent = "回滚到此备份";
      }
    };
    td.appendChild(btn);
    tr.appendChild(td);
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  box.appendChild(t);
}

function renderExportPreview(data) {
  const box = $("#exportPreview");
  box.innerHTML = "";
  if (!data) return;
  const p = el("p", { class: "msg" });
  p.appendChild(document.createTextNode(`已导出 ${data.count} 条 → ${data.file}`));
  [["下载 .jsonl", data.file], ["下载 .tsv", data.tsv]].forEach(([label, name]) => {
    if (!name) return;
    const a = el("a", { href: "/api/download/" + encodeURIComponent(name), class: "dl" });
    const btn = el("button", {}, label);
    a.appendChild(btn);
    p.appendChild(a);
  });
  box.appendChild(p);
  const t = el("table");
  t.innerHTML = "<thead><tr><th>词</th><th>拼音</th><th>词频</th></tr></thead>";
  const tb = el("tbody");
  data.top.forEach((r) => {
    const tr = el("tr");
    tr.appendChild(el("td", {}, r.w));
    tr.appendChild(el("td", {}, r.py));
    tr.appendChild(el("td", { class: "num" }, r.n));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  box.appendChild(t);
}

let diffReady = false;

function renderDiff(data) {
  const s = data.summary;
  const sum = $("#diffSummary");
  sum.innerHTML = "";
  const items = [
    ["新增", s.add, "add"], ["累加", s.bump, "bump"],
    ["同词异音", s.conflict, "conflict"], ["保持不变", s.keep, "keep"],
    ["删除", s.deleted, "keep"], ["降频", s.lowered, "keep"],
  ];
  items.forEach(([label, val, cls]) => {
    const d = el("div");
    d.appendChild(el("b", { class: cls }, String(val)));
    d.appendChild(document.createTextNode(" " + label));
    sum.appendChild(d);
  });
  const box = $("#diffTable");
  box.innerHTML = "";
  const t = el("table");
  t.innerHTML = "<thead><tr><th>词</th><th>拼音</th><th>动作</th><th>旧</th><th>新</th><th>合并后</th></tr></thead>";
  const tb = el("tbody");
  data.rows.forEach((r) => {
    const tr = el("tr");
    tr.appendChild(el("td", {}, r.w));
    tr.appendChild(el("td", {}, r.py));
    tr.appendChild(el("td", { class: r.action }, r.action));
    tr.appendChild(el("td", { class: "num" }, r.old));
    tr.appendChild(el("td", { class: "num" }, r.new));
    tr.appendChild(el("td", { class: "num" }, r.merged));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  box.appendChild(t);
  if (data.truncated) box.appendChild(el("p", { class: "hint" }, `还有 ${data.truncated} 条未显示`));
  diffReady = true;
  $("#btnApply").disabled = false;
}

async function refresh(bootBar) {
  const data = await api("/api/status");
  state = data;
  renderCards();
  renderSelectors();
  renderBackups();
  if (bootBar) bootBar.done(`${data.imes.length} 家已探测`);
}

$("#btnExport").onclick = async () => {
  const ime = $("#exportIme").value;
  const label = state.imes.find((i) => i.ime === ime)?.label || ime;
  // 导出是一次阻塞调用，后端给不出中间阶段 —— 只走不确定进度 + 秒数
  const bar = makeBar($("#exportPreview"), `读取 ${label} 的词库`);
  $("#exportMsg").textContent = "";
  $("#btnExport").disabled = true;
  try {
    const data = await api("/api/export", { ime });
    bar.done(`${data.count} 条 → ${data.file}`);
    renderExportPreview(data);
    await refresh();
  } catch (e) {
    bar.fail(e.message);
    $("#exportMsg").textContent = "失败：" + e.message;
  } finally {
    $("#btnExport").disabled = false;
  }
};

$("#btnDiff").onclick = async () => {
  const ime = $("#targetIme").value;
  const file = $("#fileList").value;
  if (!file) return;
  $("#btnApply").disabled = true;
  diffReady = false;
  const label = state.imes.find((i) => i.ime === ime)?.label || ime;
  const bar = makeBar($("#diffSummary"), `读取 ${label} 的词库并计算 diff`);
  try {
    const data = await api("/api/diff", { ime, file, strategy: $("#strategy").value });
    const s = data.summary;
    bar.done(`新增 ${s.add} · 累加 ${s.bump} · 同词异音 ${s.conflict}`);
    renderDiff(data);
  } catch (e) {
    bar.fail(e.message);
    $("#diffSummary").innerHTML = '<span class="err">' + e.message + "</span>";
  }
};

$("#btnApply").onclick = async () => {
  if (!diffReady) return;
  const ime = $("#targetIme").value;
  const file = $("#fileList").value;
  const label = state.imes.find((i) => i.ime === ime)?.label || ime;
  if (!confirm(`确认把 ${file} 合并进 ${label}？\n\n会先自动备份、退出输入法、写入后重启。`)) return;
  $("#btnApply").disabled = true;
  $("#btnApply").textContent = "写入中…";
  // 写入有 6 个离散阶段，后端拿不到（一次 HTTP 调用），所以先走不确定进度，
  // 结束时用返回的真实数字收尾。给的是"正在做事"而不是编造的百分比。
  const bar = makeBar($("#diffSummary"), `备份 → 退出 → 写入 ${label} → 重启`);
  try {
    const data = await api("/api/apply", { ime, file, strategy: $("#strategy").value });
    bar.done(`写入 ${data.written}/${data.requested || data.written} 条`);
    await refresh();
  } catch (e) {
    bar.fail(e.message);
    alert("写入失败：" + e.message);
  } finally {
    $("#btnApply").textContent = "确认合并写入";
    $("#btnApply").disabled = false;
  }
};
$("#btnBackup").onclick = async () => {
  const ime = $("#backupIme").value;
  $("#backupMsg").textContent = "备份中…";
  try {
    const meta = await api("/api/backup", { ime });
    $("#backupMsg").textContent = `已备份到 ${meta.path}`;
    await refresh();
  } catch (e) {
    $("#backupMsg").textContent = "失败：" + e.message;
  }
};

// 首屏要等 /api/status 把三家词库都解析一遍（微键 20 万条键值，约 5-10 秒）。
// 这期间必须有东西动，否则看着像卡死。
const bootBar = makeBar($("#bootStatus"), "正在探测本机输入法");
refresh(bootBar).catch((e) => {
  bootBar.fail(e.message);
  document.body.insertAdjacentHTML("afterbegin",
    '<p class="err" style="padding:16px 32px">加载失败：' + e.message + "</p>");
});

let migReady = false;

function renderMigrate(data) {
  const s = data.summary || {};
  const sum = $("#migSummary");
  sum.innerHTML = "";
  const head = el("div");
  head.innerHTML = `<b>${data.source_records || 0}</b> 条来自 ${data.source || "?"}`;
  sum.appendChild(head);
  [["新增", s.add, "add"], ["累加", s.bump, "bump"],
   ["同词异音", s.conflict, "conflict"], ["保持不变", s.keep, "keep"],
   ["删除", s.deleted, "keep"], ["降频", s.lowered, "keep"]].forEach(([label, val, cls]) => {
    const d = el("div");
    d.appendChild(el("b", { class: cls }, String(val)));
    d.appendChild(document.createTextNode(" " + label));
    sum.appendChild(d);
  });
  const box = $("#migTable");
  box.innerHTML = "";
  const rows = data.rows || [];
  if (rows.length) {
    const t = el("table");
    t.innerHTML = "<thead><tr><th>词</th><th>拼音</th><th>动作</th><th>旧</th><th>新</th><th>合并后</th></tr></thead>";
    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr");
      tr.appendChild(el("td", {}, r.w));
      tr.appendChild(el("td", {}, r.py));
      tr.appendChild(el("td", { class: r.action }, r.action));
      tr.appendChild(el("td", { class: "num" }, r.old));
      tr.appendChild(el("td", { class: "num" }, r.new));
      tr.appendChild(el("td", { class: "num" }, r.merged));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    box.appendChild(t);
  }
  migReady = true;
  $("#btnMigrateApply").disabled = !rows.length;
}

$("#btnMigratePreview").onclick = async () => {
  const source = $("#migSource").value;
  const target = $("#migTarget").value;
  if (!source || !target) return;
  if (source === target) { alert("源和目标不能是同一个输入法"); return; }
  const srcLabel = state.imes.find((i) => i.ime === source)?.label || source;
  const bar = makeBar($("#migSummary"), `读取 ${srcLabel} 的词库并计算 diff`);
  $("#migTable").innerHTML = "";
  $("#btnMigrateApply").disabled = true;
  migReady = false;
  try {
    const data = await api("/api/migrate", { source, target, strategy: $("#migStrategy").value });
    const s = data.summary || {};
    bar.done(`新增 ${s.add || 0} · 累加 ${s.bump || 0} · 同词异音 ${s.conflict || 0}`);
    renderMigrate(data);
  } catch (e) {
    bar.fail(e.message);
    $("#migSummary").innerHTML = '<span class="err">' + e.message + "</span>";
  }
};

$("#btnMigrateApply").onclick = async () => {
  if (!migReady) return;
  const source = $("#migSource").value;
  const target = $("#migTarget").value;
  const label = state.imes.find((i) => i.ime === target)?.label || target;
  if (!confirm(`确认把 ${source} 的个人词迁移进 ${label}？\n\n会先自动备份、退出输入法、写入后重启。`)) return;
  $("#btnMigrateApply").disabled = true;
  $("#btnMigrateApply").textContent = "迁移中…";
  const bar = makeBar($("#migSummary"), `导出 → 备份 → 退出 → 写入 ${label} → 重启`);
  try {
    const data = await api("/api/migrate", {
      source, target, strategy: $("#migStrategy").value, apply: true,
    });
    bar.done(`写入 ${data.written}/${data.requested || data.written} 条`);
    await refresh();
  } catch (e) {
    bar.fail(e.message);
    alert("迁移失败：" + e.message);
  } finally {
    $("#btnMigrateApply").textContent = "确认迁移";
    $("#btnMigrateApply").disabled = false;
  }
};

// ---- upload a file from anywhere -----------------------------------------
async function uploadFile(file) {
  if (!file) return;
  const msg = $("#uploadMsg");
  msg.textContent = "上传 " + file.name + " …";
  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name) },
      body: file,
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    msg.textContent = `已载入 ${data.count} 条（${data.source}）`;
    await refresh();
    const sel = $("#fileList");
    if ([...sel.options].some((o) => o.value === data.file)) {
      sel.value = data.file;
    }
  } catch (e) {
    msg.innerHTML = '<span class="err">上传失败：' + e.message + "</span>";
  }
}

$("#btnPick").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = (ev) => {
  uploadFile(ev.target.files[0]);
  ev.target.value = "";
};

const dz = $("#dropZone");
["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (ev) => {
  ev.preventDefault(); dz.classList.add("hot");
}));
["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (ev) => {
  ev.preventDefault(); dz.classList.remove("hot");
}));
dz.addEventListener("drop", (ev) => {
  const f = ev.dataTransfer.files && ev.dataTransfer.files[0];
  if (f) uploadFile(f);
});
