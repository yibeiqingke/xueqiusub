<template>
  <div class="timeline-view">
    <div class="header-bar">
      <div>
        <h2 class="page-title">动态信息流</h2>
        <p class="page-desc">聚合关注大V的全部实时发帖，按时间倒序排列</p>
      </div>
      <div class="filter-box">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索动态内容或大V姓名..."
          prefix-icon="Search"
          clearable
          class="search-input"
          @input="handleSearch"
        />
        <el-button :icon="Refresh" @click="loadFeeds">刷新</el-button>
      </div>
    </div>

    <div class="feed-list">
      <!-- Skeleton Loading -->
      <template v-if="loading && feeds.length === 0">
        <el-card v-for="i in 3" :key="i" class="feed-card" shadow="never">
          <el-skeleton animated :rows="3" avatar />
        </el-card>
      </template>

      <el-empty v-if="filteredFeeds.length === 0 && !loading" description="暂无符合条件的动态" />

      <el-card
        v-for="item in filteredFeeds"
        :key="item.id"
        shadow="hover"
        :class="['feed-card', { 'has-danger-alert': checkSignalType(item)?.type === 'danger' }]"
      >
        <div class="feed-header">
          <div class="author-info">
            <el-avatar :size="36" style="background: #0284c7; font-weight: 700;">
              {{ (item.author_name || "V")[0] }}
            </el-avatar>
            <div>
              <div class="author-row">
                <span class="author-name">{{ item.author_name || "未知大V" }}</span>
                <el-tag
                  v-if="checkSignalType(item)"
                  :type="checkSignalType(item).type"
                  size="small"
                  effect="dark"
                  round
                  class="signal-badge"
                >
                  {{ checkSignalType(item).label }}
                </el-tag>
              </div>
              <div class="post-time">{{ item.published_at_str || item.published_at }}</div>
            </div>
          </div>
          <el-link
            v-if="safeUrl(item.link)"
            :href="safeUrl(item.link)"
            target="_blank"
            rel="noopener noreferrer"
            type="primary"
            :underline="false"
          >
            查看原帖 ↗
          </el-link>
        </div>

        <div v-if="shouldShowTitle(item)" class="feed-title">
          {{ item.title }}
        </div>

        <div class="feed-content" v-html="formatContent(item.content)"></div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { dashboardApi } from "../api.js";

const loading = ref(false);
const feeds = ref([]);
const searchKeyword = ref("");

const loadFeeds = async () => {
  loading.value = true;
  try {
    const data = await dashboardApi.getTimeline({ limit: 50 });
    feeds.value = data.items || [];
  } catch (e) {
  } finally {
    loading.value = false;
  }
};

const shouldShowTitle = (item) => {
  const title = (item.title || "").trim();
  if (!title) return false;
  if (["雪球新动态", "新动态", "雪球动态", "动态"].includes(title)) return false;

  // 纯文本比对：如果标题只是正文开头的截断（无独立标题的微博/短动态），则隐藏重复的加粗标题
  const plainContent = (item.content || "")
    .replace(/<[^>]+>/g, "")
    .replace(/&[a-zA-Z0-9#]+;/g, " ")
    .replace(/\s+/g, "");
  const plainTitle = title.replace(/\.\.\.$/, "").replace(/\s+/g, "");

  if (
    plainTitle &&
    plainTitle.length >= 8 &&
    plainContent.startsWith(plainTitle.slice(0, Math.min(20, plainTitle.length)))
  ) {
    return false;
  }
  return true;
};

const checkSignalType = (item) => {
  const text = ((item.title || "") + " " + (item.content || "")).toLowerCase();
  if (/(暴跌|闪崩|跌停|强赎|爆仓|违约|清仓)/.test(text)) {
    return { type: "danger", label: "🚨 异动预警" };
  }
  if (item.source_type === "xueqiu_cube" || /(调仓|买入|建仓|加仓|做多)/.test(text)) {
    if (/(买入|建仓|加仓|做多)/.test(text)) {
      return { type: "success", label: "📈 加仓信号" };
    }
    return { type: "primary", label: "📦 调仓动态" };
  }
  if (/(减仓|卖出|止损|止盈|看空)/.test(text)) {
    return { type: "warning", label: "📉 减仓信号" };
  }
  return null;
};

// ---------------------------------------------------------------------------
// HTML 内容清洗与安全格式化（保留富文本：表情、链接、股票标签、引用块）
// ---------------------------------------------------------------------------
const ALLOWED_TAGS = new Set([
  "A", "IMG", "BLOCKQUOTE", "BR", "P", "DIV", "SPAN",
  "B", "STRONG", "I", "EM", "UL", "OL", "LI", "HR"
]);

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);

const formatContent = (c) => {
  if (!c) return "";
  try {
    const parser = new DOMParser();
    const normalized = String(c).replace(/\r\n/g, "\n").replace(/\n/g, "<br/>");
    const doc = parser.parseFromString(`<body>${normalized}</body>`, "text/html");
    const body = doc.body;

    const sanitize = (node) => {
      const children = Array.from(node.childNodes);
      for (const child of children) {
        if (child.nodeType === Node.TEXT_NODE) {
          continue;
        }

        if (child.nodeType === Node.ELEMENT_NODE) {
          const tag = child.tagName.toUpperCase();

          if (!ALLOWED_TAGS.has(tag)) {
            if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "INPUT", "BUTTON", "SVG", "MATH", "LINK", "META"].includes(tag)) {
              child.remove();
            } else {
              sanitize(child);
              while (child.firstChild) {
                node.insertBefore(child.firstChild, child);
              }
              child.remove();
            }
            continue;
          }

          // 剥除所有不安全属性与行内事件
          const attrs = Array.from(child.attributes);
          for (const attr of attrs) {
            const name = attr.name.toLowerCase();
            if (name.startsWith("on") || name === "style" || name.startsWith("data-") || name === "id") {
              child.removeAttribute(attr.name);
            }
          }

          // 细化元素安全策略
          if (tag === "A") {
            const href = (child.getAttribute("href") || "").trim();
            if (!/^https?:\/\//i.test(href) && !/^\//.test(href)) {
              child.removeAttribute("href");
            } else {
              child.setAttribute("target", "_blank");
              child.setAttribute("rel", "noopener noreferrer");
              if (href.includes("/S/") || (child.className && child.className.includes("xq_stock"))) {
                child.className = "timeline-stock-pill";
              } else {
                child.className = "timeline-link";
              }
            }
          } else if (tag === "IMG") {
            const src = (child.getAttribute("src") || "").trim();
            if (!/^https?:\/\//i.test(src)) {
              child.remove();
              continue;
            }
            const alt = (child.getAttribute("alt") || "").trim();
            const title = (child.getAttribute("title") || "").trim();
            const isEmoji =
              src.includes("face/") ||
              src.includes("emoji_") ||
              src.includes("imedao.com/ugc/images/face") ||
              (alt.startsWith("[") && alt.endsWith("]"));

            if (isEmoji) {
              child.className = "timeline-emoji";
              child.setAttribute("alt", alt || "[表情]");
              child.setAttribute("title", title || alt || "");
              child.removeAttribute("height");
              child.removeAttribute("width");
            } else {
              child.className = "timeline-photo";
              child.setAttribute("loading", "lazy");
            }
          } else if (tag === "BLOCKQUOTE") {
            child.className = "timeline-quote";
          }

          sanitize(child);
        } else {
          child.remove();
        }
      }
    };

    sanitize(body);

    let html = body.innerHTML;
    // 高亮转发人//@引用
    html = html.replace(/(\/\/@[^:<>\n]{1,120}:)/g, '<span class="timeline-forward-tag">$1</span>');
    return html;
  } catch (e) {
    return escapeHtml(c).replace(/\n/g, "<br/>");
  }
};

const SAFE_HREF_RE = /^(?:https?:|mailto:|tel:|#|\/(?![/\\]))/i;
const safeUrl = (u) => {
  const raw = String(u ?? "").trim();
  const compact = raw.replace(/[\s\u0000-\u0020\u007f]+/g, "");
  return SAFE_HREF_RE.test(compact) ? raw : "";
};

const filteredFeeds = computed(() => {
  if (!searchKeyword.value.trim()) return feeds.value;
  const kw = searchKeyword.value.toLowerCase();
  return feeds.value.filter(
    (item) =>
      (item.title && item.title.toLowerCase().includes(kw)) ||
      (item.content && item.content.toLowerCase().includes(kw)) ||
      (item.author_name && item.author_name.toLowerCase().includes(kw))
  );
});

onMounted(() => {
  loadFeeds();
});
</script>

<style scoped>
.timeline-view {
  max-width: 900px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #0f172a;
}
.page-desc {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 13px;
}
.filter-box {
  display: flex;
  gap: 12px;
}
.search-input {
  width: 280px;
}
.feed-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.feed-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
}
.feed-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.author-info {
  display: flex;
  align-items: center;
  gap: 12px;
}
.author-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.author-name {
  font-weight: 600;
  color: #0f172a;
  font-size: 15px;
}
.signal-badge {
  font-weight: 600;
  font-size: 11px;
}
.has-danger-alert {
  border-left: 4px solid #ef4444 !important;
}
.post-time {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}
.feed-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  margin-bottom: 10px;
  line-height: 1.4;
}
.feed-content {
  font-size: 14.5px;
  line-height: 1.75;
  color: #1e293b;
  word-break: break-word;
}

/* 股票标签样式 */
:deep(.timeline-stock-pill) {
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #e0f2fe;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 13px;
  text-decoration: none;
  display: inline-block;
  margin: 0 2px;
  font-weight: 500;
  transition: all 0.2s;
}
:deep(.timeline-stock-pill:hover) {
  background: #e0f2fe;
  color: #0369a1;
}

/* 用户提及与普通超链接 */
:deep(.timeline-link) {
  color: #0284c7;
  text-decoration: none;
  font-weight: 500;
  transition: color 0.15s;
}
:deep(.timeline-link:hover) {
  color: #0369a1;
  text-decoration: underline;
}

/* 表情图标 */
:deep(.timeline-emoji) {
  width: 20px;
  height: 20px;
  vertical-align: -3px;
  display: inline-block;
  margin: 0 1px;
  object-fit: contain;
}

/* 正文配图 */
:deep(.timeline-photo) {
  max-width: 100%;
  max-height: 400px;
  border-radius: 8px;
  margin: 10px 0;
  display: block;
  object-fit: cover;
  border: 1px solid #e2e8f0;
}

/* 转发 //@引用标签 */
:deep(.timeline-forward-tag) {
  color: #0284c7;
  font-weight: 600;
  margin-right: 2px;
}

/* 引用/转发卡片 (blockquote) */
:deep(.timeline-quote),
:deep(blockquote) {
  margin: 12px 0 6px;
  padding: 12px 16px;
  background: #f8fafc;
  border-left: 3px solid #0284c7;
  border-radius: 0 8px 8px 0;
  color: #475569;
  font-size: 13.5px;
  line-height: 1.65;
}
:deep(.timeline-quote .timeline-link),
:deep(blockquote .timeline-link) {
  color: #0369a1;
}

@media (max-width: 640px) {
  .header-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .filter-box {
    width: 100%;
  }
  .search-input {
    width: 100% !important;
    flex: 1;
  }
}
</style>
