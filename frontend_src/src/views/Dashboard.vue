<template>
  <div class="dashboard-view">
    <!-- Top Hero Banner / Action Bar -->
    <div class="hero-bar">
      <div class="hero-left">
        <div class="hero-badge">
          <span class="badge-dot"></span>
          <span>投研雷达监控中枢 · 7×24 全网轮询</span>
        </div>
        <h1 class="hero-title">大V订阅与舆情监控看板</h1>
        <p class="hero-desc">实时监听雪球、微博、财联社、华见等全网大V投资动态，智能解析观点与组合调仓信号</p>
      </div>

      <div class="hero-actions">
        <el-button type="primary" :icon="Plus" size="large" class="btn-primary-gradient" @click="showAddModal = true">
          添加关注 / 信源
        </el-button>
        <el-button :icon="Reading" size="large" class="btn-digests" @click="router.push('/digests')">
          投研内参归档
        </el-button>
        <el-button :icon="Refresh" size="large" class="btn-refresh" :loading="refreshing" @click="loadData">
          刷新数据
        </el-button>
      </div>
    </div>



    <!-- Subscriptions Desktop Table Card -->
    <el-card shadow="never" class="table-card desktop-table-view">
      <!-- Table Toolbar (Filter Tabs + Search Bar) -->
      <div class="table-toolbar">
        <div class="toolbar-left">
          <el-radio-group v-model="platformFilter" size="default" class="filter-radio-group">
            <el-radio-button label="all">全部 ({{ subscriptions.length }})</el-radio-button>
            <el-radio-button label="xueqiu">❄️ 雪球博主</el-radio-button>
            <el-radio-button label="news">⚡ 7×24 快讯</el-radio-button>
            <el-radio-button label="xueqiu_cube">📦 组合调仓</el-radio-button>
            <el-radio-button label="weibo">🔴 微博财经</el-radio-button>
          </el-radio-group>
        </div>

        <div class="toolbar-right">
          <el-input
            v-model="searchQuery"
            placeholder="搜索大V姓名、UID或降噪词..."
            :prefix-icon="Search"
            clearable
            class="search-input"
          />
        </div>
      </div>

      <!-- Desktop Table -->
      <el-table
        :data="filteredSubscriptions"
        v-loading="loading"
        style="width: 100%"
        class="modern-table"
        stripe
      >
        <!-- 关注博主 / 信号源 (带精美头像与平台角标) -->
        <el-table-column label="关注大V / 信号源" min-width="260">
          <template #default="{ row }">
            <div class="account-cell-modern">
              <div class="v-avatar" :style="{ background: getAvatarBg(row.account?.platform) }">
                {{ getAvatarInitial(row) }}
              </div>
              <div class="v-info">
                <div class="v-name-row">
                  <span class="v-name">{{ row.account?.display_name || row.account?.name || "未知大V" }}</span>
                  <el-tag :type="platformTagType(row.account?.platform)" size="small" effect="light" class="v-platform-tag">
                    {{ formatPlatform(row.account?.platform) }}
                  </el-tag>
                </div>
                <div class="v-meta">
                  <span v-if="['cls', 'wallstreetcn', 'gelonghui', '36kr', 'jisilu'].includes(row.account?.platform)" class="v-meta-tag">
                    ⚡ 7×24 公共快讯频道
                  </span>
                  <span v-else class="v-meta-uid">
                    UID: {{ row.account?.xueqiu_user_id }}
                  </span>
                </div>
                <div v-if="row.account?.latest_post?.title" class="v-latest-post">
                  <span class="v-latest-time">{{ formatTimeAgo(row.account.latest_post.published_at) }}</span>
                  <span class="v-latest-sep">·</span>
                  <span class="v-latest-title" :title="row.account.latest_post.title">{{ row.account.latest_post.title }}</span>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>

        <!-- 投递频次 -->
        <el-table-column width="155">
          <template #header>
            <div class="col-header-tip">
              <span>投递频次</span>
              <el-tooltip
                content="【实时即刻】：新动态即刻推送；若开启 AI 研报，夜间 23:00 仍会汇总全天复盘。&#10;【每日夜间】：白天静默攒帖，夜间 23:00 统一生成合并研报发送。"
                placement="top"
              >
                <el-icon class="tip-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
          <template #default="{ row }">
            <el-select
              v-model="row.delivery_mode"
              size="small"
              class="sleek-select"
              @change="handleRowUpdate(row)"
            >
              <el-option label="📅 每日夜间" value="daily" />
              <el-option label="⚡ 实时即刻" value="immediate" />
            </el-select>
            <div v-if="row.delivery_mode === 'immediate' && row.ai_summary_enabled" class="sub-mode-hint text-sky">
              ⚡即时推 + 23:00复盘
            </div>
            <div v-else-if="row.delivery_mode === 'daily'" class="sub-mode-hint text-muted">
              📅23:00合并研报
            </div>
          </template>
        </el-table-column>

        <!-- 推送通道 -->
        <el-table-column label="推送通道" width="140">
          <template #default="{ row }">
            <el-select
              v-model="row.delivery_channel"
              size="small"
              class="sleek-select"
              @change="handleRowUpdate(row)"
            >
              <el-option label="🚀 邮件+群" value="both" />
              <el-option label="💬 仅群Bot" value="webhook" />
              <el-option label="📧 仅邮件" value="email" />
            </el-select>
          </template>
        </el-table-column>

        <!-- AI 研报 -->
        <el-table-column label="AI 研报" width="105" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.ai_summary_enabled"
              size="small"
              inline-prompt
              active-text="研报"
              inactive-text="原帖"
              style="--el-switch-on-color: #0284c7"
              @change="handleRowUpdate(row)"
            />
          </template>
        </el-table-column>

        <!-- 监控状态 -->
        <el-table-column label="监控状态" width="110" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.is_enabled"
              size="small"
              inline-prompt
              active-text="开启"
              inactive-text="暂停"
              style="--el-switch-on-color: #10b981; --el-switch-off-color: #ef4444"
              @change="handleRowUpdate(row)"
            />
          </template>
        </el-table-column>

        <!-- 降噪过滤规则 -->
        <el-table-column label="降噪过滤规则" min-width="210">
          <template #default="{ row }">
            <div class="filter-badges">
              <span v-if="row.keywords_include" class="badge-item">
                <el-tag size="small" type="success" effect="light">关注: {{ row.keywords_include }}</el-tag>
              </span>
              <span v-if="row.keywords_exclude" class="badge-item">
                <el-tag size="small" type="info" effect="light">屏蔽: {{ row.keywords_exclude }}</el-tag>
              </span>
              <span v-if="row.alert_keywords" class="badge-item">
                <el-tag size="small" type="danger" effect="light">告警: {{ row.alert_keywords }}</el-tag>
              </span>
              <span v-if="!row.keywords_include && !row.keywords_exclude && !row.alert_keywords" class="badge-all-clear">
                🛡️ 全量动态接收
              </span>
            </div>
          </template>
        </el-table-column>

        <!-- 操作 -->
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <div class="table-actions-group">
              <el-button size="small" type="primary" plain :icon="Setting" @click="openFilterDrawer(row)">
                规则
              </el-button>
              <el-button size="small" type="success" plain :icon="Refresh" :loading="row._triggering" @click="handleTrigger(row)">
                抓取
              </el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="handleDelete(row)">
                退订
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- Empty state when searching -->
      <div v-if="filteredSubscriptions.length === 0 && !loading" class="empty-search-state">
        <el-empty description="未找到符合条件的大V或信源" />
      </div>
    </el-card>

    <!-- Subscriptions Mobile Cards View -->
    <div class="mobile-cards-view">
      <!-- Loading Skeleton Cards -->
      <template v-if="loading && subscriptions.length === 0">
        <el-card v-for="i in 3" :key="i" class="sub-mobile-card" shadow="never">
          <el-skeleton animated :rows="3" avatar />
        </el-card>
      </template>

      <el-empty
        v-else-if="filteredSubscriptions.length === 0 && !loading"
        description="暂无关注的大V或信源，快点击右上角添加吧"
      />

      <el-card
        v-for="row in filteredSubscriptions"
        :key="row.id"
        class="sub-mobile-card"
        shadow="never"
      >
        <!-- Card Top: Avatar, Platform Tag, Big-V Name, Xueqiu ID & Status Switch -->
        <div class="sub-card-top">
          <div class="sub-card-identity">
            <div class="v-avatar-mobile" :style="{ background: getAvatarBg(row.account?.platform) }">
              {{ getAvatarInitial(row) }}
            </div>
            <div class="sub-name-block">
              <div class="sub-name-row">
                <span class="sub-name">{{ row.account?.display_name || row.account?.name || "未知大V" }}</span>
                <el-tag :type="platformTagType(row.account?.platform)" size="small" effect="light">
                  {{ formatPlatform(row.account?.platform) }}
                </el-tag>
              </div>
              <span class="sub-id">
                <template v-if="['cls', 'wallstreetcn', 'gelonghui', '36kr', 'jisilu'].includes(row.account?.platform)">
                  公共快讯源
                </template>
                <template v-else>
                  UID: {{ row.account?.xueqiu_user_id }}
                </template>
              </span>
              <div v-if="row.account?.latest_post?.title" class="sub-latest-post">
                <span class="v-latest-time">{{ formatTimeAgo(row.account.latest_post.published_at) }}</span>
                <span class="v-latest-sep">·</span>
                <span class="sub-latest-title" :title="row.account.latest_post.title">{{ row.account.latest_post.title }}</span>
              </div>
            </div>
          </div>

          <div class="sub-card-switch">
            <el-switch
              v-model="row.is_enabled"
              size="default"
              inline-prompt
              active-text="开"
              inactive-text="停"
              style="--el-switch-on-color: #10b981; --el-switch-off-color: #ef4444"
              @change="handleRowUpdate(row)"
            />
          </div>
        </div>

        <!-- Card Middle: Controls (Frequency, Channel, AI Summary) -->
        <div class="sub-card-controls">
          <div class="sub-ctrl-item">
            <span class="sub-ctrl-label">投递频次</span>
            <el-select
              v-model="row.delivery_mode"
              size="small"
              style="width: 100%"
              @change="handleRowUpdate(row)"
            >
              <el-option label="📅 每日" value="daily" />
              <el-option label="⚡ 实时" value="immediate" />
            </el-select>
            <div v-if="row.delivery_mode === 'immediate' && row.ai_summary_enabled" class="sub-mode-hint text-sky">
              ⚡即时推 + 23:00复盘
            </div>
            <div v-else-if="row.delivery_mode === 'daily'" class="sub-mode-hint text-muted">
              📅23:00合并研报
            </div>
          </div>

          <div class="sub-ctrl-item">
            <span class="sub-ctrl-label">推送通道</span>
            <el-select
              v-model="row.delivery_channel"
              size="small"
              style="width: 100%"
              @change="handleRowUpdate(row)"
            >
              <el-option label="🚀 邮件+群" value="both" />
              <el-option label="💬 仅群Bot" value="webhook" />
              <el-option label="📧 仅邮件" value="email" />
            </el-select>
          </div>

          <div class="sub-ctrl-item-switch">
            <span class="sub-ctrl-label">AI研报</span>
            <el-switch
              v-model="row.ai_summary_enabled"
              size="small"
              inline-prompt
              active-text="研报"
              inactive-text="原帖"
              style="--el-switch-on-color: #0284c7"
              @change="handleRowUpdate(row)"
            />
          </div>
        </div>

        <!-- Filter Badges -->
        <div class="sub-card-filters">
          <div v-if="row.keywords_include || row.keywords_exclude || row.alert_keywords" class="filter-badge-list">
            <el-tag v-if="row.keywords_include" size="small" type="success" effect="light">关注: {{ row.keywords_include }}</el-tag>
            <el-tag v-if="row.keywords_exclude" size="small" type="info" effect="light">屏蔽: {{ row.keywords_exclude }}</el-tag>
            <el-tag v-if="row.alert_keywords" size="small" type="danger" effect="light">告警: {{ row.alert_keywords }}</el-tag>
          </div>
          <div v-else class="filter-empty-tip">
            <span>🛡️ 全量动态接收（无过滤限制）</span>
          </div>
        </div>

        <!-- Card Bottom: Actions -->
        <div class="sub-card-actions">
          <el-button size="small" type="primary" plain :icon="Setting" @click="openFilterDrawer(row)">
            过滤规则
          </el-button>
          <el-button size="small" type="success" plain :icon="Refresh" :loading="row._triggering" @click="handleTrigger(row)">
            立即抓取
          </el-button>
          <el-button size="small" type="danger" text :icon="Delete" @click="handleDelete(row)">
            取消关注
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- Filter Config Drawer -->
    <el-drawer v-model="drawerVisible" title="大V降噪与过滤规则设置" size="min(440px, 100vw)">
      <div v-if="currentSub" class="drawer-content">
        <p class="drawer-sub-name">正在配置：<strong>{{ currentSub.account?.display_name || currentSub.account?.name }}</strong></p>

        <el-form label-position="top">
          <el-form-item label="只关注特定关键词（白名单）">
            <el-input
              v-model="filterForm.keywords_include"
              placeholder="多个词用逗号或空格分隔，如：茅台, 加仓, 业绩超预期"
            />
            <span class="field-tip">配置后，该大V只有正文提到这些词的动态才会推送</span>
          </el-form-item>

          <el-form-item label="屏蔽关键词（黑名单）">
            <el-input
              v-model="filterForm.keywords_exclude"
              placeholder="如：广告, 推广, 抽奖, 互粉"
            />
            <span class="field-tip">只要包含任一词立即自动拦截不发</span>
          </el-form-item>

          <el-form-item label="高危异动即时告警词">
            <el-input
              v-model="filterForm.alert_keywords"
              placeholder="如：暴跌, 闪崩, 减持, 清仓, 违约"
            />
            <span class="field-tip">命中告警词时立即发送高优先级提醒</span>
          </el-form-item>

          <el-form-item label="正文最低有效字数门槛">
            <el-input-number v-model="filterForm.filter_min_length" :min="0" :max="500" style="width: 100%" />
            <span class="field-tip">过滤纯表情、“收到”等无信息量的短回复（推荐设为 10~20 字）</span>
          </el-form-item>

          <!-- 规则即时模拟测试 -->
          <div class="sim-test-card">
            <div class="sim-test-header">
              <span class="sim-test-title">🧪 降噪规则即时模拟测试</span>
              <el-button
                v-if="currentSub?.account?.latest_post?.title"
                size="small"
                link
                type="primary"
                @click="simTestText = currentSub.account.latest_post.title"
              >
                填入最新动态测试
              </el-button>
            </div>
            <el-input
              v-model="simTestText"
              type="textarea"
              :rows="3"
              placeholder="在此输入或粘贴一段大V动态文本，即时验证是否会被过滤或告警..."
            />
            <div v-if="simTestText.trim()" class="sim-test-result">
              <el-tag :type="simTestResult.tagType" effect="dark" size="small" class="sim-result-tag">
                {{ simTestResult.label }}
              </el-tag>
              <span class="sim-result-desc">{{ simTestResult.desc }}</span>
            </div>
          </div>
        </el-form>

        <div style="margin-top: 30px">
          <el-button type="primary" style="width: 100%" :loading="savingFilter" @click="saveFilter">
            保存规则
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- Add Sub Modal -->
    <el-dialog v-model="showAddModal" title="添加关注 / 信源" width="min(500px, 94vw)">
      <el-form label-position="top">
        <el-form-item label="订阅平台源">
          <el-select v-model="addForm.platform" style="width: 100%" @change="onPlatformChange">
            <el-option label="❄️ 雪球博主" value="xueqiu" />
            <el-option label="📦 雪球组合调仓" value="xueqiu_cube" />
            <el-option label="🔴 微博财经博主" value="weibo" />
            <el-option label="⚡ 财联社 7×24 快讯 (免填UID)" value="cls" />
            <el-option label="🌐 华尔街见闻全球快讯 (免填UID)" value="wallstreetcn" />
            <el-option label="📊 格隆汇 7×24 财经快讯 (免填UID)" value="gelonghui" />
            <el-option label="💡 36氪实时快讯 (免填UID)" value="36kr" />
            <el-option label="🛡️ 集思录精选广场 (免填UID)" value="jisilu" />
            <el-option label="🔗 自定义公开 RSS 源" value="custom_rss" />
          </el-select>
        </el-form-item>

        <!-- UID field -->
        <el-form-item
          v-if="!isPresetPlatform"
          :label="addForm.platform === 'custom_rss' ? 'RSS 订阅源完整 URL' : (addForm.platform === 'xueqiu_cube' ? '组合代码 (例如 ZH123456)' : '大V主页用户 UID')"
        >
          <el-input
            v-model="addForm.user_id"
            :placeholder="addForm.platform === 'custom_rss' ? 'https://example.com/feed.xml' : (addForm.platform === 'xueqiu_cube' ? '例如 ZH123456' : '例如 3058599833')"
          />
        </el-form-item>

        <!-- Name field -->
        <el-form-item label="备注显示名称">
          <el-input v-model="addForm.name" placeholder="例如：段永平 或 自定义备注" />
        </el-form-item>

        <!-- Feed Type for Xueqiu -->
        <el-form-item v-if="addForm.platform === 'xueqiu'" label="动态类型">
          <el-select v-model="addForm.feed_type" style="width: 100%">
            <el-option label="全部动态 (推荐)" value="10" />
            <el-option label="原发布动态" value="0" />
            <el-option label="长篇文章" value="2" />
            <el-option label="问答与回复" value="4" />
            <el-option label="热门动态" value="9" />
            <el-option label="组合与交易" value="11" />
          </el-select>
        </el-form-item>

        <el-row :gutter="12">
          <el-col :xs="24" :sm="12">
            <el-form-item label="投递频次">
              <el-select v-model="addForm.delivery_mode" style="width: 100%">
                <el-option label="📅 每日夜间汇总" value="daily" />
                <el-option label="⚡ 实时即刻推送" value="immediate" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="推送通道">
              <el-select v-model="addForm.delivery_channel" style="width: 100%">
                <el-option label="🚀 邮件 + 群" value="both" />
                <el-option label="💬 仅群Bot" value="webhook" />
                <el-option label="📧 仅邮件" value="email" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="AI 智能研报">
          <el-switch
            v-model="addForm.ai_summary_enabled"
            active-text="纳入每日研报"
            inactive-text="仅原帖 (不纳入)"
            style="--el-switch-on-color: #0284c7"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showAddModal = false">取消</el-button>
        <el-button type="primary" :loading="addingSub" @click="handleAddSub">确认添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { Plus, Refresh, Setting, Delete, Search, User, Compass, Timer, QuestionFilled, Reading } from "@element-plus/icons-vue";
import { dashboardApi, subApi } from "../api.js";
import { ElMessage, ElMessageBox } from "element-plus";

const router = useRouter();
const loading = ref(false);
const refreshing = ref(false);
const subscriptions = ref([]);
const digestSchedule = ref("每天 20:00");
const platformFilter = ref("all");
const searchQuery = ref("");

const formatTimeAgo = (iso) => {
  if (!iso) return "暂无动态";
  const d = new Date(iso);
  const now = new Date();
  const diffSec = Math.floor((now - d) / 1000);
  if (diffSec < 60) return "刚刚";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}小时前`;
  if (diffSec < 86400 * 2) return "昨天";
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${m}-${day}`;
};

const activeSubsCount = computed(() => {
  return subscriptions.value.filter((s) => s.is_enabled).length;
});

const filteredSubscriptions = computed(() => {
  let list = subscriptions.value;
  if (platformFilter.value !== "all") {
    if (platformFilter.value === "news") {
      list = list.filter((s) =>
        ["cls", "wallstreetcn", "gelonghui", "36kr", "jisilu"].includes(s.account?.platform)
      );
    } else {
      list = list.filter((s) => s.account?.platform === platformFilter.value);
    }
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase();
    list = list.filter((s) => {
      const name = (s.account?.display_name || s.account?.name || "").toLowerCase();
      const uid = String(s.account?.xueqiu_user_id || "").toLowerCase();
      const kw = (
        (s.keywords_include || "") +
        " " +
        (s.keywords_exclude || "") +
        " " +
        (s.alert_keywords || "")
      ).toLowerCase();
      return name.includes(q) || uid.includes(q) || kw.includes(q);
    });
  }
  return list;
});

const loadData = async () => {
  loading.value = true;
  try {
    const res = await dashboardApi.getData();
    subscriptions.value = res.subscriptions || [];
    if (res.digest_hour !== undefined) {
      digestSchedule.value = `每天 ${String(res.digest_hour).padStart(2, "0")}:00`;
    }
  } catch (e) {
    // error handled by api interceptor
  } finally {
    loading.value = false;
  }
};

const formatPlatform = (p) => {
  const map = {
    xueqiu: "雪球",
    xueqiu_cube: "组合",
    weibo: "微博",
    cls: "财联社",
    wallstreetcn: "华见",
    gelonghui: "格隆汇",
    "36kr": "36氪",
    jisilu: "集思录",
    custom_rss: "RSS",
  };
  return map[p] || "雪球";
};

const platformTagType = (p) => {
  const map = {
    xueqiu: "primary",
    xueqiu_cube: "success",
    weibo: "warning",
    cls: "danger",
    wallstreetcn: "danger",
    gelonghui: "danger",
    "36kr": "info",
    jisilu: "warning",
    custom_rss: "info",
  };
  return map[p] || "primary";
};

const getAvatarBg = (platform) => {
  const map = {
    xueqiu: "linear-gradient(135deg, #0284c7 0%, #0369a1 100%)",
    xueqiu_cube: "linear-gradient(135deg, #10b981 0%, #047857 100%)",
    weibo: "linear-gradient(135deg, #f97316 0%, #c2410c 100%)",
    cls: "linear-gradient(135deg, #ef4444 0%, #b91c1c 100%)",
    wallstreetcn: "linear-gradient(135deg, #f43f5e 0%, #be123c 100%)",
    gelonghui: "linear-gradient(135deg, #f59e0b 0%, #b45309 100%)",
    "36kr": "linear-gradient(135deg, #6366f1 0%, #4338ca 100%)",
    jisilu: "linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%)",
    custom_rss: "linear-gradient(135deg, #0284c7 0%, #0f172a 100%)",
  };
  return map[platform] || "linear-gradient(135deg, #0284c7 0%, #0369a1 100%)";
};

const getAvatarInitial = (row) => {
  const name = row.account?.display_name || row.account?.name || "V";
  return name.slice(0, 1).toUpperCase();
};

// Row-level inline update
const handleRowUpdate = async (row) => {
  try {
    await subApi.update(row.id, {
      is_enabled: row.is_enabled,
      delivery_mode: row.delivery_mode,
      delivery_channel: row.delivery_channel,
      ai_summary_enabled: row.ai_summary_enabled,
    });
    ElMessage.success("设置已更新");
  } catch (e) {
    // revert on error if needed
  }
};

// Filter Drawer
const drawerVisible = ref(false);
const currentSub = ref(null);
const savingFilter = ref(false);
const filterForm = reactive({
  keywords_include: "",
  keywords_exclude: "",
  alert_keywords: "",
  filter_min_length: 0,
});

const simTestText = ref("");
const simTestResult = computed(() => {
  const text = simTestText.value.trim();
  if (!text) return null;

  const minLen = Number(filterForm.filter_min_length) || 0;
  if (minLen > 0 && text.length < minLen) {
    return {
      tagType: "danger",
      label: "🚫 字数拦截",
      desc: `正文长度（${text.length}字）低于门槛要求（${minLen}字），将被系统静默丢弃。`,
    };
  }

  const excludes = (filterForm.keywords_exclude || "")
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const hitExclude = excludes.find((w) => text.includes(w));
  if (hitExclude) {
    return {
      tagType: "danger",
      label: "🚫 黑名单屏蔽",
      desc: `命中黑名单屏蔽词「${hitExclude}」，将被自动拦截不予推送。`,
    };
  }

  const alerts = (filterForm.alert_keywords || "")
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const hitAlert = alerts.find((w) => text.includes(w));
  if (hitAlert) {
    return {
      tagType: "warning",
      label: "🚨 触发高危告警",
      desc: `命中紧急异动告警词「${hitAlert}」，将附带红标强提醒即时推送！`,
    };
  }

  const includes = (filterForm.keywords_include || "")
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (includes.length > 0) {
    const hitInclude = includes.find((w) => text.includes(w));
    if (!hitInclude) {
      return {
        tagType: "info",
        label: "⚠️ 未命中白名单",
        desc: `正文未包含已设置的任何关注词（如：${includes.slice(0, 3).join(", ")}），将被过滤。`,
      };
    }
  }

  return {
    tagType: "success",
    label: "✅ 正常放行",
    desc: "通过全部过滤规则验证，动态将正常生成并推送通知！",
  };
});

const openFilterDrawer = async (sub) => {
  currentSub.value = sub;
  simTestText.value = sub.account?.latest_post?.title || "";
  try {
    const data = await subApi.getFilter(sub.id);
    filterForm.keywords_include = data.keywords_include || "";
    filterForm.keywords_exclude = data.keywords_exclude || "";
    filterForm.alert_keywords = data.alert_keywords || "";
    filterForm.filter_min_length = data.filter_min_length || 0;
    drawerVisible.value = true;
  } catch (e) {}
};

const saveFilter = async () => {
  if (!currentSub.value) return;
  savingFilter.value = true;
  try {
    await subApi.saveFilter(currentSub.value.id, filterForm);
    ElMessage.success("降噪规则更新成功");
    drawerVisible.value = false;
    loadData();
  } catch (e) {}
  finally {
    savingFilter.value = false;
  }
};

const handleTrigger = async (sub) => {
  sub._triggering = true;
  try {
    const res = await subApi.trigger(sub.id);
    const count = res?.new_count ?? 0;
    if (count > 0) {
      ElMessage.success(`抓取成功！检测到 ${count} 条新动态并已推送`);
    } else {
      ElMessage.info("已完成抓取，博主暂无新发布内容");
    }
    loadData();
  } catch (e) {}
  finally {
    sub._triggering = false;
  }
};

const handleDelete = (sub) => {
  const name = sub.account?.display_name || sub.account?.name || "该博主";
  ElMessageBox.confirm(`确认取消关注并停止监控「${name}」吗？`, "退订确认", {
    type: "warning",
  }).then(async () => {
    try {
      await subApi.delete(sub.id);
      ElMessage.success("已取消关注");
      loadData();
    } catch (e) {}
  });
};

// Add Sub Modal
const showAddModal = ref(false);
const addingSub = ref(false);
const addForm = reactive({
  platform: "xueqiu",
  user_id: "",
  name: "",
  feed_type: "10",
  delivery_mode: "immediate",
  delivery_channel: "both",
  ai_summary_enabled: true,
});

const PRESETS = {
  cls: { name: "财联社 7×24 快讯", uid: "cls" },
  wallstreetcn: { name: "华尔街见闻全球快讯", uid: "wallstreetcn" },
  gelonghui: { name: "格隆汇 7×24 财经快讯", uid: "gelonghui" },
  "36kr": { name: "36氪实时快讯", uid: "36kr" },
  jisilu: { name: "集思录精选广场", uid: "jisilu" },
};

const isPresetPlatform = computed(() => {
  return Boolean(PRESETS[addForm.platform]);
});

const onPlatformChange = () => {
  if (PRESETS[addForm.platform]) {
    addForm.user_id = PRESETS[addForm.platform].uid;
    addForm.name = PRESETS[addForm.platform].name;
  } else {
    if (["cls", "wallstreetcn", "gelonghui", "36kr", "jisilu"].includes(addForm.user_id)) {
      addForm.user_id = "";
      addForm.name = "";
    }
  }
};

const handleAddSub = async () => {
  if (!addForm.user_id) {
    ElMessage.warning("请输入用户 UID 或链接");
    return;
  }
  addingSub.value = true;
  try {
    await subApi.add(addForm);
    ElMessage.success("关注成功添加");
    showAddModal.value = false;
    addForm.user_id = "";
    addForm.name = "";
    loadData();
  } catch (e) {}
  finally {
    addingSub.value = false;
  }
};

onMounted(() => {
  loadData();
});
</script>

<style scoped>
.dashboard-view {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

/* Hero Action Bar */
.hero-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #ffffff;
  border-radius: 16px;
  padding: 24px 28px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.03);
}
.hero-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  padding: 3px 10px;
  border-radius: 20px;
  width: fit-content;
}
.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: #0284c7;
  animation: pulse-dot 2s infinite ease-in-out;
}
@keyframes pulse-dot {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.5); opacity: 0.5; }
}
.hero-title {
  margin: 0;
  font-size: 24px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.5px;
}
.hero-desc {
  margin: 0;
  color: #64748b;
  font-size: 13.5px;
}
.hero-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.btn-primary-gradient {
  background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
  border: none !important;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);
  transition: all 0.2s;
}
.btn-primary-gradient:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px rgba(2, 132, 199, 0.35);
}
.btn-refresh {
  border-color: #cbd5e1 !important;
  color: #334155;
  font-weight: 500;
}

/* Modern Stats Cards */
.stats-row {
  margin-bottom: 2px;
}
.stat-card-modern {
  background: #ffffff;
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  padding: 20px 22px;
  display: flex;
  gap: 16px;
  align-items: flex-start;
  box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.03);
  position: relative;
  overflow: hidden;
  transition: all 0.25s ease;
}
.stat-card-modern:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 25px -4px rgba(0, 0, 0, 0.08);
}
.stat-card-modern::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
}
.stat-blue::before { background: linear-gradient(90deg, #0284c7, #38bdf8); }
.stat-emerald::before { background: linear-gradient(90deg, #10b981, #34d399); }
.stat-indigo::before { background: linear-gradient(90deg, #6366f1, #818cf8); }

.stat-icon-wrapper {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.icon-blue { background: #e0f2fe; color: #0284c7; }
.icon-emerald { background: #dcfce7; color: #10b981; }
.icon-indigo { background: #ede9fe; color: #6366f1; }

.stat-content {
  display: flex;
  flex-direction: column;
  flex: 1;
}
.stat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.stat-title {
  font-size: 13.5px;
  color: #64748b;
  font-weight: 600;
}
.stat-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 600;
}
.badge-blue { background: #f0f9ff; color: #0284c7; }
.badge-emerald { background: #ecfdf5; color: #059669; }
.badge-indigo { background: #eef2ff; color: #4f46e5; }

.stat-num {
  font-size: 32px;
  font-weight: 800;
  line-height: 1.1;
  letter-spacing: -1px;
}
.text-blue { color: #0284c7; }
.text-emerald { color: #10b981; }
.text-indigo { color: #6366f1; }

.stat-footer {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 6px;
}

/* Subscriptions Table Card */
.table-card {
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.03);
  padding: 8px 12px;
}
.table-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid #f1f5f9;
  flex-wrap: wrap;
  gap: 12px;
}
.filter-radio-group :deep(.el-radio-button__inner) {
  border-radius: 8px !important;
  margin-right: 6px;
  border: 1px solid #e2e8f0 !important;
  background: #f8fafc;
  color: #475569;
  font-weight: 500;
}
.filter-radio-group :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: #0284c7 !important;
  color: #ffffff !important;
  border-color: #0284c7 !important;
  box-shadow: none !important;
}
.search-input {
  width: 260px;
}

/* Modern Big-V Cell with Avatar */
.account-cell-modern {
  display: flex;
  align-items: center;
  gap: 12px;
}
.v-avatar {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font-weight: 800;
  font-size: 16px;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.12);
}
.v-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.v-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.v-name {
  font-weight: 700;
  color: #0f172a;
  font-size: 14.5px;
}
.v-platform-tag {
  font-size: 11px;
  font-weight: 600;
  height: 20px;
  line-height: 18px;
  padding: 0 6px;
}
.v-meta {
  margin-top: 2px;
}
.v-meta-tag {
  font-size: 11.5px;
  color: #f59e0b;
  font-weight: 600;
}
.v-meta-uid {
  font-size: 11.5px;
  color: #94a3b8;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

/* Sleek Select in Table */
.sleek-select :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #e2e8f0 inset !important;
  border-radius: 8px;
  background-color: #f8fafc;
}
.sleek-select:hover :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #0284c7 inset !important;
}

/* Filter Badges */
.filter-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.badge-all-clear {
  font-size: 12px;
  color: #94a3b8;
}

/* Table Actions */
.table-actions-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  white-space: nowrap;
}
.table-actions-group .el-button {
  font-weight: 500;
  padding: 5px 9px;
  flex-shrink: 0;
}

/* Mobile cards layout */
.mobile-cards-view {
  display: none;
  flex-direction: column;
  gap: 12px;
}
.sub-mobile-card {
  border-radius: 14px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.03);
}
.sub-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.sub-card-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.v-avatar-mobile {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font-weight: 800;
  font-size: 16px;
  flex-shrink: 0;
}
.sub-name-block {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.sub-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.sub-name {
  font-weight: 700;
  font-size: 15px;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub-id {
  font-size: 11.5px;
  color: #94a3b8;
  margin-top: 1px;
}
.sub-card-switch {
  flex-shrink: 0;
  margin-left: 10px;
}
.sub-card-controls {
  display: flex;
  gap: 10px;
  align-items: center;
  background: #f8fafc;
  padding: 10px;
  border-radius: 10px;
  margin-bottom: 12px;
}
.sub-ctrl-item {
  flex: 1;
  min-width: 0;
}
.sub-ctrl-item-switch {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0 4px;
}
.sub-ctrl-label {
  font-size: 11px;
  color: #64748b;
  display: block;
  margin-bottom: 4px;
  font-weight: 600;
}
.sub-card-filters {
  margin-bottom: 12px;
}
.filter-badge-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.filter-empty-tip {
  font-size: 12px;
  color: #94a3b8;
}
.sub-card-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  border-top: 1px solid #f1f5f9;
  padding-top: 10px;
}

/* Dark Mode Overrides for Modern Dashboard */
:global(html.dark) .hero-bar {
  background-color: var(--app-card-bg) !important;
  border-color: var(--app-border) !important;
}
:global(html.dark) .hero-badge {
  background: #0f172a !important;
  border-color: #0284c7 !important;
}
:global(html.dark) .hero-title {
  color: #f8fafc !important;
}
:global(html.dark) .stat-card-modern {
  background-color: var(--app-card-bg) !important;
  border-color: var(--app-border) !important;
}
:global(html.dark) .icon-blue { background: #0f172a !important; }
:global(html.dark) .icon-emerald { background: #0f172a !important; }
:global(html.dark) .icon-indigo { background: #0f172a !important; }
:global(html.dark) .badge-blue { background: #0f172a !important; }
:global(html.dark) .badge-emerald { background: #0f172a !important; }
:global(html.dark) .badge-indigo { background: #0f172a !important; }
:global(html.dark) .table-toolbar {
  border-bottom-color: var(--app-border) !important;
}
:global(html.dark) .filter-radio-group .el-radio-button__inner {
  background: #0f172a !important;
  border-color: #334155 !important;
  color: #cbd5e1 !important;
}
:global(html.dark) .v-name {
  color: #f8fafc !important;
}
:global(html.dark) .sleek-select .el-input__wrapper {
  background-color: #0f172a !important;
  box-shadow: 0 0 0 1px #334155 inset !important;
}

/* Responsive Breakpoint for Mobile (< 768px) */
@media (max-width: 768px) {
  .dashboard-view {
    gap: 14px;
  }
  .hero-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
    padding: 18px 16px;
  }
  .hero-title {
    font-size: 20px;
  }
  .hero-actions {
    width: 100%;
  }
  .hero-actions .el-button {
    flex: 1;
  }
  .stats-row .el-col {
    margin-bottom: 10px;
  }
  .stat-card-modern {
    padding: 14px 16px;
  }
  .stat-num {
    font-size: 26px;
  }
  .desktop-table-view {
    display: none !important;
  }
  .mobile-cards-view {
    display: flex !important;
  }
}

/* Latest post preview */
.v-latest-post {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  margin-top: 4px;
  max-width: 320px;
}
.sub-latest-post {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  margin-top: 6px;
}
.v-latest-time {
  color: #0284c7;
  background: #f0f9ff;
  border-radius: 4px;
  padding: 1px 5px;
  font-weight: 600;
  flex-shrink: 0;
}
.v-latest-sep {
  color: #94a3b8;
  flex-shrink: 0;
}
.v-latest-title,
.sub-latest-title {
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Header tip */
.col-header-tip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.tip-icon {
  font-size: 13px;
  color: #94a3b8;
  cursor: pointer;
}
.tip-icon:hover {
  color: #0284c7;
}

/* Mode sub-hints */
.sub-mode-hint {
  font-size: 11px;
  margin-top: 3px;
  font-weight: 500;
  line-height: 1.2;
}
.text-sky {
  color: #0284c7;
}
.text-muted {
  color: #94a3b8;
}

/* Simulation test card */
.sim-test-card {
  margin-top: 20px;
  padding: 14px;
  background: #f8fafc;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
}
.sim-test-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.sim-test-title {
  font-size: 13px;
  font-weight: 700;
  color: #1e293b;
}
.sim-test-result {
  margin-top: 10px;
  padding: 8px 10px;
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.sim-result-tag {
  flex-shrink: 0;
}
.sim-result-desc {
  font-size: 12px;
  color: #475569;
  line-height: 1.4;
}

/* Dark mode overrides for new elements */
:global(html.dark) .v-latest-time {
  background: #0f172a;
  color: #38bdf8;
}
:global(html.dark) .v-latest-title,
:global(html.dark) .sub-latest-title {
  color: #94a3b8;
}
:global(html.dark) .sim-test-card {
  background: #0f172a;
  border-color: #334155;
}
:global(html.dark) .sim-test-title {
  color: #f8fafc;
}
:global(html.dark) .sim-test-result {
  background: #1e293b;
  border-color: #334155;
}
:global(html.dark) .sim-result-desc {
  color: #cbd5e1;
}

</style>
