import { createRouter, createWebHistory } from "vue-router";
import Dashboard from "./views/Dashboard.vue";
import Discover from "./views/Discover.vue";
import Timeline from "./views/Timeline.vue";
import Chat from "./views/Chat.vue";
import Settings from "./views/Settings.vue";
import Help from "./views/Help.vue";
import Admin from "./views/Admin.vue";
import Login from "./views/Login.vue";
import Register from "./views/Register.vue";
import Digests from "./views/Digests.vue";

const routes = [
  { path: "/", name: "Dashboard", component: Dashboard, meta: { title: "大V监控看板" } },
  { path: "/discover", name: "Discover", component: Discover, meta: { title: "大V发现广场" } },
  { path: "/timeline", name: "Timeline", component: Timeline, meta: { title: "动态时间线" } },
  { path: "/chat", name: "Chat", component: Chat, meta: { title: "AI 智能投研助手" } },
  { path: "/digests", name: "Digests", component: Digests, meta: { title: "投研内参归档" } },
  { path: "/settings", name: "Settings", component: Settings, meta: { title: "系统与偏好设置" } },
  { path: "/help", name: "Help", component: Help, meta: { title: "使用说明与指南" } },
  { path: "/admin", name: "Admin", component: Admin, meta: { title: "管理后台" } },
  { path: "/login", name: "Login", component: Login, meta: { title: "登录与接入" } },
  { path: "/register", name: "Register", component: Register, meta: { title: "注册新账号" } },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, from, next) => {
  if (to.meta.title) {
    document.title = `${to.meta.title} - 智投内参`;
  }
  next();
});

export default router;
