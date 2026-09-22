<template>
  <div class="layout-container">
    <el-header v-if="!isLoginPage" class="header">
      <div class="logo-area" @click="router.push('/')" style="cursor: pointer">
        <img src="/favicon.svg" class="logo-img" alt="logo" />
        <span class="logo-title">智投内参</span>
        <span class="logo-tag hidden-xs">AI 投研版</span>
      </div>

      <!-- Desktop Top Menu -->
      <el-menu
        :default-active="activeRoute"
        mode="horizontal"
        router
        class="nav-menu desktop-only"
        :ellipsis="false"
      >
        <el-menu-item index="/">
          <el-icon><DataAnalysis /></el-icon>
          <span>我的订阅</span>
        </el-menu-item>
        <el-menu-item index="/discover">
          <el-icon><Compass /></el-icon>
          <span>发现广场</span>
        </el-menu-item>
        <el-menu-item index="/timeline">
          <el-icon><Document /></el-icon>
          <span>动态信息流</span>
        </el-menu-item>
        <el-menu-item index="/digests">
          <el-icon><Reading /></el-icon>
          <span>投研内参</span>
        </el-menu-item>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>AI 投研问答</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>推送设置</span>
        </el-menu-item>
        <el-menu-item index="/help">
          <el-icon><QuestionFilled /></el-icon>
          <span>使用说明</span>
        </el-menu-item>
        <el-menu-item v-if="currentUser?.is_admin" index="/admin">
          <el-icon><Management /></el-icon>
          <span>管理后台</span>
        </el-menu-item>
      </el-menu>

      <!-- User Area -->
      <div class="user-area">
        <!-- Dark Mode Toggle Button -->
        <el-tooltip :content="isDark ? '切换浅色日间模式' : '切换夜间暗黑模式'" placement="bottom">
          <div class="theme-toggle-btn" @click="toggleTheme">
            <el-icon :size="16">
              <Moon v-if="!isDark" />
              <Sunny v-else />
            </el-icon>
          </div>
        </el-tooltip>

        <el-tooltip content="使用说明" placement="bottom" class="mobile-only">
          <el-button link size="small" class="mobile-only" @click="router.push('/help')">
            <el-icon :size="18"><QuestionFilled /></el-icon>
          </el-button>
        </el-tooltip>

        <div v-if="currentUser" class="user-badge desktop-only">
          <span class="user-avatar-dot"></span>
          <span class="user-email-text">{{ currentUser.email }}</span>
        </div>

        <el-button v-if="currentUser" type="danger" size="small" plain class="btn-logout" @click="handleLogout">
          退出
        </el-button>
      </div>
    </el-header>

    <!-- Main Content View -->
    <el-main class="main-content">
      <router-view />
    </el-main>

    <!-- Mobile Bottom TabBar -->
    <nav v-if="!isLoginPage" class="mobile-bottom-nav">
      <div
        class="tab-item"
        :class="{ active: activeRoute === '/' }"
        @click="router.push('/')"
      >
        <el-icon class="tab-icon"><DataAnalysis /></el-icon>
        <span class="tab-label">订阅</span>
      </div>

      <div
        class="tab-item"
        :class="{ active: activeRoute === '/discover' }"
        @click="router.push('/discover')"
      >
        <el-icon class="tab-icon"><Compass /></el-icon>
        <span class="tab-label">广场</span>
      </div>

      <div
        class="tab-item"
        :class="{ active: activeRoute === '/timeline' }"
        @click="router.push('/timeline')"
      >
        <el-icon class="tab-icon"><Document /></el-icon>
        <span class="tab-label">动态</span>
      </div>

      <div
        class="tab-item"
        :class="{ active: activeRoute === '/chat' }"
        @click="router.push('/chat')"
      >
        <el-icon class="tab-icon"><ChatDotRound /></el-icon>
        <span class="tab-label">AI问答</span>
      </div>

      <div
        class="tab-item"
        :class="{ active: activeRoute === '/settings' }"
        @click="router.push('/settings')"
      >
        <el-icon class="tab-icon"><Setting /></el-icon>
        <span class="tab-label">设置</span>
      </div>

      <div
        v-if="currentUser?.is_admin"
        class="tab-item"
        :class="{ active: activeRoute === '/admin' }"
        @click="router.push('/admin')"
      >
        <el-icon class="tab-icon"><Management /></el-icon>
        <span class="tab-label">后台</span>
      </div>
    </nav>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { authApi } from "./api.js";
import { ElMessage } from "element-plus";
import {
  DataAnalysis,
  Compass,
  Document,
  Reading,
  ChatDotRound,
  Setting,
  QuestionFilled,
  UserFilled,
  Management,
  Moon,
  Sunny,
} from "@element-plus/icons-vue";

const route = useRoute();
const router = useRouter();
const currentUser = ref(null);

const isDark = ref(document.documentElement.classList.contains("dark"));
const toggleTheme = () => {
  isDark.value = !isDark.value;
  if (isDark.value) {
    document.documentElement.classList.add("dark");
    localStorage.setItem("app_theme", "dark");
    ElMessage.success("已切换为夜间暗黑科技模式");
  } else {
    document.documentElement.classList.remove("dark");
    localStorage.setItem("app_theme", "light");
    ElMessage.success("已切换为日间明亮模式");
  }
};

const isLoginPage = computed(() => route.path === "/login");
const activeRoute = computed(() => route.path);

const checkLogin = async () => {
  if (route.path === "/login") return;
  try {
    const data = await authApi.getMe();
    currentUser.value = data.user;
  } catch (e) {
    // 401 interceptor handles redirect
  }
};

const handleLogout = async () => {
  try {
    await authApi.logout();
    currentUser.value = null;
    ElMessage.success("已安全退出");
    router.push("/login");
  } catch (e) {
    router.push("/login");
  }
};

onMounted(() => {
  checkLogin();
});
</script>

<style scoped>
.layout-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background-color: #f8fafc;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ffffff;
  border-bottom: 1px solid rgba(226, 232, 240, 0.8);
  padding: 0 24px;
  box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.03);
  position: sticky;
  top: 0;
  z-index: 100;
  height: 60px;
}
.logo-area {
  display: flex;
  align-items: center;
  gap: 10px;
}
.logo-img {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(2, 132, 199, 0.25);
}
.logo-title {
  font-size: 17.5px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.5px;
}
.logo-tag {
  font-size: 11px;
  font-weight: 700;
  background: #ecfdf5;
  color: #059669;
  border: 1px solid #a7f3d0;
  padding: 2px 8px;
  border-radius: 12px;
}
.nav-menu {
  border-bottom: none !important;
  flex: 1;
  margin: 0 20px;
  height: 60px;
  line-height: 60px;
  background: transparent !important;
}
.nav-menu :deep(.el-menu-item) {
  height: 38px !important;
  line-height: 38px !important;
  margin: 11px 4px !important;
  border-radius: 8px !important;
  border-bottom: none !important;
  font-weight: 500;
  font-size: 13.5px;
  transition: all 0.15s ease;
  color: #475569;
  padding: 0 14px !important;
}
.nav-menu :deep(.el-menu-item:hover) {
  background-color: #f1f5f9 !important;
  color: #0284c7 !important;
}
.nav-menu :deep(.el-menu-item.is-active) {
  background-color: #e0f2fe !important;
  color: #0284c7 !important;
  font-weight: 700 !important;
}
.user-area {
  display: flex;
  align-items: center;
  gap: 10px;
}
.theme-toggle-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #475569;
  transition: all 0.2s ease;
}
.theme-toggle-btn:hover {
  background: #f1f5f9;
  color: #0284c7;
  border-color: #cbd5e1;
}
.user-badge {
  font-size: 13px;
  color: #475569;
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f1f5f9;
  padding: 5px 12px;
  border-radius: 20px;
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-avatar-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: #10b981;
}
.user-email-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.btn-logout {
  border-radius: 8px;
}
.main-content {
  padding: 24px;
  max-width: 1400px;
  width: 100%;
  margin: 0 auto;
  box-sizing: border-box;
}

/* Dark Mode Overrides for Navigation */
:global(html.dark) .logo-tag {
  background: #064e3b !important;
  color: #34d399 !important;
  border-color: #059669 !important;
}
:global(html.dark) .nav-menu :deep(.el-menu-item) {
  color: #94a3b8 !important;
}
:global(html.dark) .nav-menu :deep(.el-menu-item:hover) {
  background-color: #334155 !important;
  color: #38bdf8 !important;
}
:global(html.dark) .nav-menu :deep(.el-menu-item.is-active) {
  background-color: rgba(2, 132, 199, 0.2) !important;
  color: #38bdf8 !important;
}
:global(html.dark) .theme-toggle-btn {
  background: #1e293b !important;
  border-color: #334155 !important;
  color: #fbbf24 !important;
}

/* Mobile Bottom Navigation Bar */
.mobile-bottom-nav {
  display: none;
}

/* Responsive Breakpoint: Mobile (< 768px) */
@media (max-width: 768px) {
  .header {
    padding: 0 14px;
    height: 52px;
  }
  .desktop-only {
    display: none !important;
  }
  .mobile-only {
    display: inline-flex !important;
  }
  .hidden-xs {
    display: none !important;
  }
  .main-content {
    padding: 14px 12px;
    padding-bottom: calc(72px + env(safe-area-inset-bottom));
  }

  .mobile-bottom-nav {
    display: flex;
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 999;
    background: #ffffff;
    border-top: 1px solid #e2e8f0;
    box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.05);
    padding-bottom: max(6px, env(safe-area-inset-bottom));
    height: calc(56px + env(safe-area-inset-bottom));
    align-items: center;
    justify-content: space-around;
  }

  .tab-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    flex: 1;
    height: 100%;
    color: #64748b;
    cursor: pointer;
    transition: color 0.15s ease;
    user-select: none;
    -webkit-tap-highlight-color: transparent;
    padding-top: 4px;
  }

  .tab-item:active {
    background-color: #f1f5f9;
  }

  .tab-item.active {
    color: #0284c7;
    font-weight: 600;
  }

  .tab-icon {
    font-size: 20px;
    margin-bottom: 2px;
  }

  .tab-label {
    font-size: 11px;
    line-height: 1;
  }
}

@media (min-width: 769px) {
  .mobile-only {
    display: none !important;
  }
}
</style>
