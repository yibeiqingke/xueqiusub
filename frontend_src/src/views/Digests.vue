<template>
  <div class="digests-page">
    <!-- Top Hero Header -->
    <div class="hero-bar">
      <div class="hero-left">
        <div class="hero-title-wrap">
          <div class="hero-badge">
            <span class="pulse-dot"></span>
            AI 投研复盘档案库 · 结构化归档
          </div>
          <h1 class="hero-title">投研内参与历史研报</h1>
          <p class="hero-desc">
            系统按每日夜间 23:00 与周度自动沉淀的 AI 深度投研研报，支持随时检索、研读与一键导出
          </p>
        </div>
      </div>
      <div class="hero-actions">
        <el-button :icon="Refresh" :loading="loading" @click="loadData">
          刷新归档
        </el-button>
        <el-button type="primary" :icon="DataAnalysis" @click="router.push('/')">
          返回订阅看板
        </el-button>
      </div>
    </div>

    <!-- Main Container (Sidebar + Reader) -->
    <div v-loading="loading" class="digest-layout">
      <!-- Left: Historical Digest List -->
      <el-card shadow="never" class="digest-sidebar-card">
        <template #header>
          <div class="sidebar-header">
            <span class="sidebar-title">📑 历史研报期数 ({{ filteredDigests.length }})</span>
          </div>
          <el-input
            v-model="searchKeyword"
            placeholder="搜索研报观点、标的或关键词..."
            clearable
            size="small"
            style="margin-top: 10px"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </template>

        <div v-if="filteredDigests.length === 0" class="empty-sidebar">
          <el-empty description="未找到相关研报记录" :image-size="60" />
        </div>

        <div v-else class="digest-nav-list">
          <div
            v-for="item in filteredDigests"
            :key="item.id"
            :class="['digest-nav-item', { active: selectedDigest?.id === item.id }]"
            @click="selectedDigest = item"
          >
            <div class="nav-item-top">
              <el-tag :type="item.isWeekly ? 'warning' : 'primary'" size="small" effect="light">
                {{ item.isWeekly ? '📊 周度复盘' : '📅 每日全天复盘' }}
              </el-tag>
              <span class="nav-item-time">{{ formatTime(item.created_at) }}</span>
            </div>
            <div class="nav-item-title">
              {{ item.displayTitle }}
            </div>
            <div class="nav-item-meta">
              <span>约 {{ item.summary_text.length }} 字</span>
              <span class="nav-meta-dot">·</span>
              <span>点击查阅</span>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Right: Markdown Reader Card -->
      <el-card shadow="never" class="digest-reader-card">
        <template v-if="selectedDigest">
          <div class="reader-header">
            <div class="reader-title-block">
              <div class="reader-tags">
                <el-tag :type="selectedDigest.isWeekly ? 'warning' : 'primary'" size="default" effect="dark">
                  {{ selectedDigest.isWeekly ? '周度大V观点深度复盘' : '每日全天大V舆情与投研复盘' }}
                </el-tag>
                <span class="reader-date">生成于 {{ formatFullDate(selectedDigest.created_at) }}</span>
              </div>
              <h2 class="reader-h2">{{ selectedDigest.displayTitle }}</h2>
            </div>
            <div class="reader-actions">
              <el-button size="default" :icon="DocumentCopy" @click="copyContent">
                复制研报正文
              </el-button>
            </div>
          </div>

          <!-- Rendered Markdown Body -->
          <div class="markdown-body" v-html="renderedContent"></div>
        </template>

        <div v-else class="reader-empty">
          <el-empty description="请从左侧选择一期历史研报进行查阅" />
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  Refresh,
  DataAnalysis,
  Search,
  DocumentCopy,
} from "@element-plus/icons-vue";
import { marked } from "marked";
import { digestsApi } from "../api";

// ---------------------------------------------------------------------------
// [sanitize:start]
// summary_text is LLM markdown built on untrusted post text and is injected
// with v-html. marked dropped its "sanitize" option long ago and v18.0.13
// still passes raw HTML straight through -- its built-in renderer is literally
//   html({ text }) { return text }
// verified against the shipped lib/marked.esm.js of marked@18.0.13. So instead
// of hand-writing an HTML allowlist parser (or adding a dependency) we override
// the three renderer methods that can emit attacker-controlled markup:
//   html  -> raw HTML, escape it instead of emitting it
//   link  -> marked only encodeURI()s the href, it never filters the scheme
//   image -> same: src is escaped but not scheme-checked
// Anything else the built-in emits goes through its escape helper R().
// ---------------------------------------------------------------------------
const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

// Single pass over the 5 chars, so an inserted "&amp;" is never rescanned:
// "&lt;" becomes "&amp;lt;" exactly once and can never double-escape. Quotes
// and apostrophes ARE required here, because these values are written into
// title=/href=/alt="" attribute contexts delimited by '"'.
const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);

// Fail-closed allowlist, not a denylist. Control and space chars are stripped
// before the scheme test because browsers ignore them, so "java(TAB)script:"
// and " JS:..." cannot slip past. Protocol-relative "//host" is rejected.
const LINK_SCHEMES = /^(?:https?:|mailto:|tel:|#|\/(?![/\\]))/i;
const IMAGE_SCHEMES = /^(?:https?:|\/(?![/\\]))/i; // no data: URIs in digests
const pickUrl = (u, allowed) => {
  const raw = String(u ?? "").trim();
  const compact = raw.replace(/[\s\u0000-\u0020\u007f]+/g, "");
  return allowed.test(compact) ? raw : "";
};
const safeUrl = (u) => pickUrl(u, LINK_SCHEMES);
const safeImageUrl = (u) => pickUrl(u, IMAGE_SCHEMES);

// Method shorthand, not arrows: marked calls these as renderer.fn(token) with
// this bound to the renderer instance, which is what exposes this.parser.
const safeRenderer = {
  html({ text }) {
    return escapeHtml(text ?? "");
  },
  link(token) {
    const label =
      token.tokens && token.tokens.length
        ? this.parser.parseInline(token.tokens)
        : escapeHtml(token.text ?? "");
    const href = safeUrl(token.href);
    if (href === "") return label; // drop the anchor, keep the readable text
    const title = token.title ? ` title="${escapeHtml(token.title)}"` : "";
    const rel = ' target="_blank" rel="noopener noreferrer nofollow"';
    return `<a href="${escapeHtml(href)}"${title}${rel}>${label}</a>`;
  },
  image(token) {
    const src = safeImageUrl(token.href);
    if (src === "") return ""; // drop entirely rather than emit a broken tag
    const alt = escapeHtml(token.text ?? "");
    const title = token.title ? ` title="${escapeHtml(token.title)}"` : "";
    return `<img src="${escapeHtml(src)}" alt="${alt}" loading="lazy"${title}>`;
  },
};
// ---------------------------------------------------------------------------
// [sanitize:end]
// ---------------------------------------------------------------------------

// Register once at module scope: marked.use() wraps and mutates the renderer
// instance, so calling it per render would nest wrappers.
marked.setOptions({
  breaks: true,
  gfm: true,
});
marked.use({ renderer: safeRenderer });

const router = useRouter();
const loading = ref(false);
const digests = ref([]);
const selectedDigest = ref(null);
const searchKeyword = ref("");

const formatTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${m}-${day} ${h}:${min}`;
};

const formatFullDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${y}年${m}月${day}日 ${h}:${min}`;
};

const parsedDigests = computed(() => {
  return digests.value.map((item) => {
    const text = item.summary_text || "";
    const isWeekly = text.includes("过去一周") || text.includes("周报") || text.includes("周度");
    
    // Extract first title
    const lines = text.split("\n");
    let displayTitle = "";
    for (const l of lines) {
      const trimmed = l.trim();
      if (trimmed.startsWith("#")) {
        displayTitle = trimmed.replace(/^#+\s*/, "").replace(/[💡📊⚠️]/g, "").trim();
        break;
      }
    }
    if (!displayTitle) {
      displayTitle = isWeekly ? "周度大V动态与调仓复盘" : "每日大V动态与全天舆情复盘";
    }

    return {
      ...item,
      isWeekly,
      displayTitle,
    };
  });
});

const filteredDigests = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase();
  if (!kw) return parsedDigests.value;
  return parsedDigests.value.filter((d) => {
    return (
      (d.displayTitle || "").toLowerCase().includes(kw) ||
      (d.summary_text || "").toLowerCase().includes(kw)
    );
  });
});

const renderedContent = computed(() => {
  if (!selectedDigest.value?.summary_text) return "";
  return marked.parse(selectedDigest.value.summary_text);
});

const loadData = async () => {
  loading.value = true;
  try {
    const res = await digestsApi.getHistory(60);
    digests.value = res.items || [];
    if (digests.value.length > 0) {
      const first = parsedDigests.value[0];
      if (!selectedDigest.value || !digests.value.some((d) => d.id === selectedDigest.value.id)) {
        selectedDigest.value = first;
      }
    }
  } catch (e) {
  } finally {
    loading.value = false;
  }
};

const copyContent = async () => {
  if (!selectedDigest.value?.summary_text) return;
  try {
    await navigator.clipboard.writeText(selectedDigest.value.summary_text);
    ElMessage.success("研报正文已成功复制到剪贴板！");
  } catch (e) {
    ElMessage.info("请长按页面手动复制正文");
  }
};

onMounted(() => {
  loadData();
});
</script>

<style scoped>
.digests-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* Hero Header */
.hero-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #ffffff;
  border-radius: 16px;
  padding: 24px 28px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.02);
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  padding: 3px 10px;
  border-radius: 20px;
  margin-bottom: 8px;
}
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: #0284c7;
  box-shadow: 0 0 0 2px rgba(2, 132, 199, 0.3);
}
.hero-title {
  margin: 0 0 6px 0;
  font-size: 24px;
  font-weight: 800;
  color: #0f172a;
}
.hero-desc {
  margin: 0;
  font-size: 13.5px;
  color: #64748b;
}
.hero-actions {
  display: flex;
  gap: 10px;
}

/* Layout */
.digest-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  align-items: start;
}

/* Left Sidebar */
.digest-sidebar-card {
  border-radius: 16px;
  border: 1px solid #e2e8f0;
}
.sidebar-title {
  font-weight: 700;
  font-size: 14px;
  color: #0f172a;
}
.digest-nav-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: calc(100vh - 280px);
  overflow-y: auto;
  padding-right: 4px;
}
.digest-nav-item {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #f1f5f9;
  background: #f8fafc;
  cursor: pointer;
  transition: all 0.15s ease;
}
.digest-nav-item:hover {
  border-color: #cbd5e1;
  background: #ffffff;
}
.digest-nav-item.active {
  background: #e0f2fe;
  border-color: #38bdf8;
  box-shadow: 0 2px 8px rgba(2, 132, 199, 0.08);
}
.nav-item-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.nav-item-time {
  font-size: 11.5px;
  color: #64748b;
}
.nav-item-title {
  font-size: 13.5px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 6px;
  line-height: 1.4;
}
.digest-nav-item.active .nav-item-title {
  color: #0369a1;
}
.nav-item-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: #94a3b8;
}
.nav-meta-dot {
  opacity: 0.5;
}

/* Right Reader */
.digest-reader-card {
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  min-height: 600px;
}
.reader-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 18px;
  margin-bottom: 24px;
}
.reader-tags {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.reader-date {
  font-size: 12.5px;
  color: #64748b;
}
.reader-h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 800;
  color: #0f172a;
  line-height: 1.35;
}
.reader-actions {
  display: flex;
  gap: 8px;
}

/* Markdown typography */
.markdown-body {
  font-size: 15px;
  line-height: 1.75;
  color: #1e293b;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  color: #0f172a;
  font-weight: 700;
  margin-top: 1.6em;
  margin-bottom: 0.8em;
}
.markdown-body :deep(h2) {
  font-size: 18px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e2e8f0;
  color: #0369a1;
}
.markdown-body :deep(h3) {
  font-size: 16px;
  color: #0f172a;
}
.markdown-body :deep(blockquote) {
  margin: 14px 0;
  padding: 10px 16px;
  background: #f8fafc;
  border-left: 4px solid #38bdf8;
  border-radius: 0 8px 8px 0;
  color: #475569;
  font-size: 14px;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 22px;
  margin: 10px 0;
}
.markdown-body :deep(li) {
  margin-bottom: 6px;
}
.markdown-body :deep(strong) {
  color: #0f172a;
  font-weight: 700;
}
.markdown-body :deep(code) {
  background: #f1f5f9;
  color: #0284c7;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13.5px;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 24px 0;
}

/* Dark Mode Overrides */
:global(html.dark) .hero-bar {
  background: #1e293b;
  border-color: #334155;
}
:global(html.dark) .hero-badge {
  background: #0f172a;
  border-color: #0284c7;
}
:global(html.dark) .hero-title {
  color: #f8fafc;
}
:global(html.dark) .hero-desc {
  color: #94a3b8;
}
:global(html.dark) .digest-sidebar-card,
:global(html.dark) .digest-reader-card {
  background: #1e293b;
  border-color: #334155;
}
:global(html.dark) .sidebar-title {
  color: #f8fafc;
}
:global(html.dark) .digest-nav-item {
  background: #0f172a;
  border-color: #334155;
}
:global(html.dark) .digest-nav-item:hover {
  border-color: #475569;
}
:global(html.dark) .digest-nav-item.active {
  background: rgba(2, 132, 199, 0.2);
  border-color: #38bdf8;
}
:global(html.dark) .nav-item-title {
  color: #f1f5f9;
}
:global(html.dark) .digest-nav-item.active .nav-item-title {
  color: #38bdf8;
}
:global(html.dark) .reader-header {
  border-bottom-color: #334155;
}
:global(html.dark) .reader-h2 {
  color: #f8fafc;
}
:global(html.dark) .markdown-body {
  color: #cbd5e1;
}
:global(html.dark) .markdown-body :deep(h1),
:global(html.dark) .markdown-body :deep(h2),
:global(html.dark) .markdown-body :deep(h3),
:global(html.dark) .markdown-body :deep(h4) {
  color: #f8fafc;
}
:global(html.dark) .markdown-body :deep(h2) {
  border-bottom-color: #334155;
  color: #38bdf8;
}
:global(html.dark) .markdown-body :deep(blockquote) {
  background: #0f172a;
  border-left-color: #38bdf8;
  color: #94a3b8;
}
:global(html.dark) .markdown-body :deep(strong) {
  color: #f8fafc;
}
:global(html.dark) .markdown-body :deep(code) {
  background: #334155;
  color: #38bdf8;
}
:global(html.dark) .markdown-body :deep(hr) {
  border-top-color: #334155;
}

/* Mobile Responsive */
@media (max-width: 992px) {
  .digest-layout {
    grid-template-columns: 1fr;
  }
  .digest-nav-list {
    max-height: 240px;
  }
}
@media (max-width: 768px) {
  .hero-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
    padding: 16px;
  }
  .hero-actions {
    width: 100%;
  }
  .hero-actions .el-button {
    flex: 1;
  }
  .reader-header {
    flex-direction: column;
    gap: 12px;
  }
}
</style>