<template>
  <div class="discover-view">
    <!-- Header Banner -->
    <div class="banner">
      <div class="banner-badge">🧭 投研雷达 · 精选信源</div>
      <h1 class="banner-title">大V精选与信源发现广场</h1>
      <p class="banner-desc">
        严选涵盖价值投资、宏观策略、周期量化、7×24 秒级官方电报及可转债套利等优质信源。点击「一键关注」即可秒级纳入实时监控与 AI 智能投研雷达，无需手动寻找配置 UID。
      </p>
    </div>

    <!-- Category Tabs -->
    <div class="category-tabs">
      <el-radio-group v-model="currentCategory" size="large">
        <el-radio-button label="all">全部精选</el-radio-button>
        <el-radio-button label="value">💎 价值投资</el-radio-button>
        <el-radio-button label="macro">🌐 宏观策略</el-radio-button>
        <el-radio-button label="cycle">🎯 周期实战</el-radio-button>
        <el-radio-button label="news">⚡ 7×24 快讯</el-radio-button>
        <el-radio-button label="arbitrage">🛡️ 套利与转债</el-radio-button>
      </el-radio-group>
    </div>

    <!-- Source Cards Grid -->
    <div class="sources-grid">
      <template v-if="loading && sources.length === 0">
        <el-card v-for="i in 6" :key="i" class="source-card" shadow="never">
          <el-skeleton animated :rows="4" />
        </el-card>
      </template>

      <el-card
        v-for="source in filteredSources"
        :key="source.id"
        class="source-card"
        shadow="hover"
      >
        <div class="card-header">
          <div class="badges">
            <span class="platform-tag" :style="{ backgroundColor: source.badge_color || '#0284c7' }">
              {{ source.platform_name || formatPlatform(source.platform) }}
            </span>
            <span class="category-tag">{{ source.category_name }}</span>
          </div>
        </div>

        <h3 class="source-name">{{ source.name }}</h3>
        <div class="source-tag">{{ source.tag }}</div>
        <p class="source-desc">{{ source.desc }}</p>

        <div class="card-footer">
          <span class="source-id">UID: {{ source.xueqiu_user_id }}</span>
          <el-button
            v-if="source.is_subscribed"
            type="success"
            size="default"
            plain
            disabled
          >
            <el-icon><Check /></el-icon> 监控中
          </el-button>
          <el-button
            v-else
            type="primary"
            size="default"
            :loading="source._subscribing"
            @click="handleSubscribe(source)"
          >
            一键关注
          </el-button>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { Check } from "@element-plus/icons-vue";
import { discoverApi } from "../api.js";
import { ElMessage } from "element-plus";

const loading = ref(false);
const sources = ref([]);
const currentCategory = ref("all");

const filteredSources = computed(() => {
  if (currentCategory.value === "all") return sources.value;
  return sources.value.filter((s) => s.category === currentCategory.value);
});

const loadSources = async () => {
  loading.value = true;
  try {
    const res = await discoverApi.getSources();
    sources.value = res.sources || [];
  } catch (e) {
    // handled
  } finally {
    loading.value = false;
  }
};

const formatPlatform = (p) => {
  const map = { xueqiu: "雪球", weibo: "微博", cls: "财联社", wallstreetcn: "华尔街见闻", jisilu: "集思录" };
  return map[p] || "雪球";
};

const handleSubscribe = async (source) => {
  source._subscribing = true;
  try {
    const res = await discoverApi.subscribe({
      platform: source.platform,
      xueqiu_user_id: source.xueqiu_user_id,
      display_name: source.name,
    });
    if (res.ok) {
      source.is_subscribed = true;
      ElMessage.success(res.message || `已成功关注「${source.name}」！`);
    } else {
      ElMessage.warning(res.message || "关注失败");
    }
  } catch (e) {
    // handled
  } finally {
    source._subscribing = false;
  }
};

onMounted(() => {
  loadSources();
});
</script>

<style scoped>
.discover-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.banner {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #0369a1 100%);
  border-radius: 16px;
  padding: 30px 24px;
  color: #ffffff;
  box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.15);
}
.banner-badge {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.15);
  padding: 3px 10px;
  border-radius: 20px;
  margin-bottom: 12px;
  letter-spacing: 0.5px;
}
.banner-title {
  margin: 0 0 10px;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.banner-desc {
  margin: 0;
  font-size: 13.5px;
  color: #cbd5e1;
  line-height: 1.6;
  max-width: 800px;
}
.category-tabs {
  margin: 4px 0 10px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 6px;
  white-space: nowrap;
}
.category-tabs::-webkit-scrollbar {
  height: 4px;
}
.category-tabs::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}
.sources-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}
.source-card {
  border-radius: 14px;
  border: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  transition: all 0.25s ease;
}
.source-card:hover {
  transform: translateY(-3px);
  border-color: #cbd5e1;
  box-shadow: 0 12px 24px -6px rgba(0, 0, 0, 0.08);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.badges {
  display: flex;
  gap: 8px;
  align-items: center;
}
.platform-tag {
  color: #ffffff;
  font-size: 11.5px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 6px;
}
.category-tag {
  background: #f1f5f9;
  color: #475569;
  font-size: 11.5px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 6px;
}
.source-name {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 700;
  color: #0f172a;
}
.source-tag {
  font-size: 12px;
  font-weight: 600;
  color: #0284c7;
  margin-bottom: 10px;
}
.source-desc {
  font-size: 13px;
  color: #64748b;
  line-height: 1.55;
  margin: 0 0 16px;
  min-height: 44px;
}
.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px solid #f1f5f9;
  padding-top: 14px;
}
.source-id {
  font-size: 12px;
  color: #94a3b8;
  font-family: monospace;
}

@media (max-width: 640px) {
  .banner {
    padding: 20px 16px;
    border-radius: 12px;
  }
  .banner-title {
    font-size: 20px;
  }
  .banner-desc {
    font-size: 13px;
  }
  .sources-grid {
    grid-template-columns: 1fr;
  }
}
</style>
