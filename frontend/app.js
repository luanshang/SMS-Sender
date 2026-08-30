/* ============================================================
 * SMS Board · 短信接收面板（Vue 3 · 零构建）
 * ------------------------------------------------------------
 * - Vue 由多 CDN 依次尝试加载（jsdelivr → npmmirror → unpkg），
 *   适配国内外网络环境；全部失败时给出友好提示。
 * - 数据源：GET /api/messages（历史） / GET /api/stats（统计）
 *           /api/stream（SSE 实时） / DELETE /api/messages[...]
 * ============================================================ */

const VUE_CDNS = [
  "https://cdn.jsdelivr.net/npm/vue@3.4.29/dist/vue.esm-browser.prod.js",
  "https://registry.npmmirror.com/vue/3.4.29/files/dist/vue.esm-browser.prod.js",
  "https://unpkg.com/vue@3.4.29/dist/vue.esm-browser.prod.js",
];

let Vue = null;
for (const url of VUE_CDNS) {
  try {
    Vue = await import(url);
    break;
  } catch (e) {
    console.warn("[SMS Board] Vue CDN 加载失败:", url, e);
  }
}
if (!Vue) {
  const appEl = document.getElementById("app");
  if (appEl) {
    appEl.innerHTML =
      '<div class="empty"><div class="big">🌐</div><p><b>无法加载 Vue 运行库</b></p>' +
      '<p class="hint">服务器无法访问 jsdelivr / npmmirror CDN，请检查服务器外网连通性。</p></div>';
  }
  throw new Error("Vue unavailable");
}

const { createApp, ref, reactive, computed, watch, onBeforeUnmount } = Vue;

const PAGE_SIZE = 50;
const AVATAR_COLORS = [
  "#f43f5e", "#f97316", "#f59e0b", "#84cc16",
  "#10b981", "#06b6d4", "#3b82f6", "#8b5cf6", "#d946ef",
];

/* ---------- 工具 ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function colorOf(key) {
  let h = 0;
  for (const ch of String(key)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function formatNum(n) {
  return n === undefined || n === null ? "–" : String(n);
}

/* ---------- 应用 ---------- */
createApp({
  setup() {
    /* ===== 主题 ===== */
    const theme = ref(document.documentElement.dataset.theme || "light");
    watch(theme, (v) => {
      document.documentElement.dataset.theme = v;
      localStorage.setItem("sb-theme", v);
    }, { immediate: true });
    const toggleTheme = () => {
      theme.value = theme.value === "dark" ? "light" : "dark";
    };

    /* ===== Token ===== */
    const token = ref((() => {
      const fromUrl = new URLSearchParams(location.search).get("token");
      if (fromUrl) {
        localStorage.setItem("sb-token", fromUrl);
        return fromUrl;
      }
      return localStorage.getItem("sb-token") || "";
    })());
    const tokenInput = ref(token.value);

    /* ===== 数据 ===== */
    const messages = ref([]);
    const total = ref(null);
    const stats = ref(null);
    const loading = ref(true);
    const loadingMore = ref(false);
    const hasMore = ref(false);
    const lastId = ref(0);
    const filterKw = ref("");
    const connText = ref("连接中…");
    const connOn = ref(false);
    const groupOpen = reactive({});

    /* ===== 开关 ===== */
    const soundOn = ref(localStorage.getItem("sb-sound") !== "0");
    watch(soundOn, (v) => localStorage.setItem("sb-sound", v ? "1" : "0"), { immediate: true });

    const notifSupported = "Notification" in window;
    const notifOn = ref(localStorage.getItem("sb-notif") === "1");
    watch(notifOn, (v) => localStorage.setItem("sb-notif", v ? "1" : "0"), { immediate: true });

    /* ===== UI 状态 ===== */
    const showClear = ref(false);
    const clearing = ref(false);
    const deletingId = ref(null);
    const copiedId = ref(null);
    const toast = ref("");
    let toastTimer = null;
    let delTimer = null;
    let copyTimer = null;
    let statsTimer = null;
    let titleTimer = null;

    const showToast = (text) => {
      toast.value = text;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toast.value = ""; }, 2400);
    };

    /* ===== API ===== */
    async function api(path, opts = {}) {
      const sep = path.includes("?") ? "&" : "?";
      const r = await fetch(path + sep + "token=" + encodeURIComponent(token.value), opts);
      if (r.status === 401) {
        connText.value = "Token 错误";
        connOn.value = false;
        throw new Error("unauthorized");
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }

    async function fetchStats() {
      try {
        const data = await api("/api/stats");
        if (data && typeof data === "object") stats.value = data;
      } catch (e) { /* 静默 */ }
    }
    const statsDirty = () => {
      clearTimeout(statsTimer);
      statsTimer = setTimeout(fetchStats, 500);
    };

    /* ===== 历史加载 ===== */
    async function loadHistory(initial = true) {
      if (initial) loading.value = true;
      try {
        const data = await api(`/api/messages?limit=${PAGE_SIZE}&offset=0`);
        if (typeof data.total === "number") total.value = data.total;
        if (initial) {
          messages.value = data.items || [];
          lastId.value = messages.value.reduce((mx, it) => Math.max(mx, Number(it.id) || 0), 0);
        } else {
          mergeNewer(data.items || []);
        }
        hasMore.value = messages.value.length < total.value;
        connText.value = "实时在线";
        connOn.value = true;
        fetchStats();
      } catch (e) {
        if (initial) showToast("加载历史失败：请检查 Token 或网络");
      } finally {
        loading.value = false;
      }
    }

    /* SSE 断线补漏：把比 lastId 新的消息补到最前 */
    function mergeNewer(items) {
      const have = new Set(messages.value.map((m) => Number(m.id)));
      for (const it of items) {
        const id = Number(it.id);
        if (!id || have.has(id) || id <= lastId.value) continue;
        messages.value.unshift(it);
        have.add(id);
        lastId.value = id;
      }
    }

    async function loadMore() {
      if (loadingMore.value) return;
      loadingMore.value = true;
      try {
        const data = await api(`/api/messages?limit=${PAGE_SIZE}&offset=${messages.value.length}`);
        const have = new Set(messages.value.map((m) => Number(m.id)));
        const add = (data.items || []).filter((it) => !have.has(Number(it.id)));
        messages.value = messages.value.concat(add); // DESC，追加的旧消息在末尾
        if (typeof data.total === "number") total.value = data.total;
        hasMore.value = messages.value.length < total.value;
      } catch (e) {
        showToast("加载更多失败");
      } finally {
        loadingMore.value = false;
      }
    }

    /* ===== SSE 实时 ===== */
    let sse = null;
    function connectSSE() {
      if (sse) { sse.close(); sse = null; }
      sse = new EventSource(`/api/stream?token=${encodeURIComponent(token.value)}`);
      sse.onopen = () => { connText.value = "实时在线"; connOn.value = true; };
      sse.onerror = () => {
        if (sse && sse.readyState === EventSource.CLOSED) {
          connText.value = "连接断开，重连中…";
          connOn.value = false;
          sse.close();
          sse = null;
        }
      };
      sse.onmessage = (e) => {
        let ev = null;
        try { ev = JSON.parse(e.data); } catch (_) { return; }
        if (!ev || typeof ev !== "object") return;
        if (ev.type === "delete") removeLocal(ev.id);
        else if (ev.type === "clear") onClearEvent();
        else prepend(ev);
      };
    }

    function prepend(item) {
      const id = Number(item.id);
      if (!id || id <= lastId.value) return;
      if (messages.value.some((m) => Number(m.id) === id)) return;
      lastId.value = id;
      messages.value.unshift(item);
      if (typeof total.value === "number") total.value++;
      beep(item.code ? 1318 : 880);
      notify(item);
      statsDirty();
      flashTitle(item.code ? "🔑 SMS Board" : "📩 SMS Board");
    }

    function onClearEvent() {
      messages.value = [];
      lastId.value = 0;
      total.value = 0;
      hasMore.value = false;
      fetchStats();
      showToast("数据已被清空");
    }

    function removeLocal(id) {
      const before = messages.value.length;
      messages.value = messages.value.filter((m) => Number(m.id) !== Number(id));
      const removed = before - messages.value.length;
      if (removed && typeof total.value === "number") {
        total.value = Math.max(0, total.value - removed);
      }
    }

    /* ===== 提示音 ===== */
    let audioCtx = null;
    function beep(freq = 880) {
      if (!soundOn.value) return;
      try {
        audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
        const ctx = audioCtx;
        const t = ctx.currentTime;
        const tone = (f, start, peak, dur) => {
          const o = ctx.createOscillator();
          const g = ctx.createGain();
          o.type = "sine"; o.frequency.value = f;
          o.connect(g); g.connect(ctx.destination);
          g.gain.setValueAtTime(0.0001, start);
          g.gain.exponentialRampToValueAtTime(peak, start + 0.02);
          g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
          o.start(start); o.stop(start + dur + 0.02);
        };
        tone(freq, t, 0.14, 0.22);
        if (freq > 1000) tone(freq * 1.25, t + 0.16, 0.12, 0.24); // 验证码双音
      } catch (e) { /* 忽略 */ }
    }

    /* ===== 桌面通知 ===== */
    async function toggleNotif() {
      if (!notifSupported) { showToast("当前浏览器不支持桌面通知"); return; }
      if (!notifOn.value) {
        let p = Notification.permission;
        if (p === "default") p = await Notification.requestPermission();
        if (p === "granted") { notifOn.value = true; showToast("桌面通知已开启"); }
        else showToast("通知权限被拒绝，请在浏览器设置中允许");
      } else {
        notifOn.value = false;
      }
    }
    function notify(item) {
      if (!notifOn.value || !notifSupported) return;
      if (Notification.permission !== "granted") return;
      try {
        const title = `SMS · ${item.contact_name || item.from_number || "新消息"}`;
        const body = item.code ? `验证码 ${item.code}\n${item.content}` : (item.content || "");
        new Notification(title, { body: body.slice(0, 140), tag: "sms-" + item.id });
      } catch (e) { /* 忽略 */ }
    }

    /* ===== 操作 ===== */
    async function copyText(text, id) {
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
      } catch (e) {
        showToast("复制失败");
        return;
      }
      copiedId.value = id;
      showToast(`验证码 ${text} 已复制`);
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => { copiedId.value = null; }, 2000);
    }

    function askDelete(m) {
      if (deletingId.value !== m.id) {
        deletingId.value = m.id; // 第一次点击进入确认态
        clearTimeout(delTimer);
        delTimer = setTimeout(() => { deletingId.value = null; }, 3000);
        return;
      }
      clearTimeout(delTimer);
      deletingId.value = null;
      api(`/api/messages/${m.id}`, { method: "DELETE" })
        .then(() => { removeLocal(m.id); showToast("已删除"); })
        .catch(() => showToast("删除失败"));
    }

    function doClear() {
      clearing.value = true;
      api("/api/messages", { method: "DELETE" })
        .then(() => showToast("已清空全部数据"))
        .catch(() => showToast("清空失败"))
        .finally(() => {
          clearing.value = false;
          showClear.value = false;
          loadHistory(true);
        });
    }

    function onTokenChange() {
      token.value = tokenInput.value.trim();
      localStorage.setItem("sb-token", token.value);
      refreshAll();
    }

    function refreshAll() {
      connectSSE();
      loadHistory(true);
      fetchStats();
    }

    function flashTitle(text) {
      document.title = text;
      clearTimeout(titleTimer);
      titleTimer = setTimeout(() => { document.title = "SMS Board · 短信接收面板"; }, 4000);
    }

    /* ===== 渲染辅助 ===== */
    function highlight(text, code) {
      let t = esc(text);
      const escCode = esc(code);
      if (escCode && t.includes(escCode)) {
        t = t.split(escCode).join(`<mark>${escCode}</mark>`);
      } else {
        t = t.replace(/(?<!\d)\d{4,8}(?!\d)/g, (m) => `<mark>${m}</mark>`);
      }
      return t;
    }

    function matchKw(m, kw) {
      return [m.from_number, m.contact_name, m.content, m.code, m.sim_slot, m.device_name]
        .some((v) => String(v || "").toLowerCase().includes(kw));
    }

    /* ===== 派生数据 ===== */
    const statCards = computed(() => [
      { icon: "📥", label: "总条数", value: formatNum(stats.value?.total), color: "#60a5fa" },
      { icon: "🗓️", label: "今日", value: formatNum(stats.value?.today), color: "#34d399" },
      { icon: "📆", label: "近 7 天", value: formatNum(stats.value?.week), color: "#a78bfa" },
      { icon: "🔑", label: "验证码", value: formatNum(stats.value?.code_count), color: "#fbbf24" },
    ]);

    const topSenders = computed(() => (stats.value?.senders || []).slice(0, 6));

    const groupList = computed(() => {
      const kw = filterKw.value.trim().toLowerCase();
      const pool = kw ? messages.value.filter((m) => matchKw(m, kw)) : messages.value;
      const map = new Map();
      for (const m of pool) {
        const key = m.from_number || m.contact_name || "(发件人)";
        let g = map.get(key);
        if (!g) {
          g = { key, number: m.from_number || "", contact_name: m.contact_name || "", list: [] };
          map.set(key, g);
        }
        g.list.push(m);
      }
      const arr = [...map.values()];
      for (const g of arr) {
        g.list.sort((a, b) => (Number(b.id) || 0) - (Number(a.id) || 0));
        g.count = g.list.length;
        g.lastTime = g.list[0].receive_time || g.list[0].created_at || "";
        const label = (g.contact_name || g.number || "发件人").trim();
        g.initial = (Array.from(label)[0] || "?").toUpperCase();
        g.color = colorOf(g.key);
      }
      arr.sort((a, b) => (Number(b.list[0]?.id) || 0) - (Number(a.list[0]?.id) || 0));
      return arr;
    });

    /* ===== 启动 & 清理 ===== */
    connectSSE();
    loadHistory(true);

    const patchTimer = setInterval(() => {
      if (!sse || sse.readyState === EventSource.CLOSED) {
        connectSSE();
        loadHistory(false);
      }
    }, 15000);

    const statsTimer2 = setInterval(fetchStats, 60000);

    onBeforeUnmount(() => {
      if (sse) sse.close();
      clearInterval(patchTimer);
      clearInterval(statsTimer2);
      clearTimeout(toastTimer);
      clearTimeout(delTimer);
      clearTimeout(copyTimer);
      clearTimeout(statsTimer);
      clearTimeout(titleTimer);
    });

    return {
      theme, toggleTheme,
      tokenInput, onTokenChange,
      connText, connOn, refreshAll,
      filterKw,
      loading, loadingMore, hasMore, loadMore,
      groupList, groupOpen,
      highlight, copyText, copiedId,
      askDelete, deletingId,
      showClear, clearing, doClear,
      soundOn, notifOn, toggleNotif,
      stats, statCards, topSenders,
      total, toast,
    };
  },
}).mount("#app");