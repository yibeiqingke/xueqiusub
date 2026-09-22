import axios from "axios";
import { ElMessage } from "element-plus";

const api = axios.create({
  baseURL: "",
  withCredentials: true,
  timeout: 60000,
});

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response && error.response.status === 401) {
      if (!window.location.pathname.includes("/login")) {
        window.location.href = "/login";
      }
    } else {
      const msg = error.response?.data?.detail || error.message || "请求失败";
      ElMessage.error(msg);
    }
    return Promise.reject(error);
  }
);

export const authApi = {
  getMe: () => api.get("/api/auth/me"),
  login: (email, password) => api.post("/api/auth/login", { email, password }),
  register: (data) => api.post("/api/auth/register", data),
  logout: () => api.post("/api/auth/logout"),
};

export const dashboardApi = {
  getData: () => api.get("/api/dashboard/data"),
  getTimeline: (params) => api.get("/api/dashboard/timeline", { params }),
};

export const subApi = {
  add: (data) => api.post("/api/subscriptions", data),
  update: (id, data) => api.patch(`/api/subscriptions/${id}`, data),
  delete: (id) => api.delete(`/api/subscriptions/${id}`),
  getFilter: (id) => api.get(`/api/subscriptions/${id}/filter`),
  saveFilter: (id, data) => api.post(`/api/subscriptions/${id}/filter`, data),
  trigger: (id) => api.post(`/api/subscriptions/${id}/trigger`),
};

export const discoverApi = {
  getSources: () => api.get("/api/discover/sources"),
  subscribe: (data) => api.post("/api/discover/subscribe", data),
};

export const chatApi = {
  getHistory: () => api.get("/api/chat/history"),
  send: (query) => api.post("/api/chat", { query }),
  clear: () => api.post("/api/chat/clear"),
};

export const settingsApi = {
  get: () => api.get("/api/user/settings"),
  save: (data) => api.post("/api/user/settings", data),
  testWebhook: () => api.post("/api/user/settings/test-webhook"),
  testEmail: () => api.post("/api/user/settings/test-email"),
  changePassword: (data) => api.post("/api/user/password", data),
};

export const adminApi = {
  getData: () => api.get("/api/admin/data"),
  createUser: (data) => api.post("/api/admin/users", data),
  toggleUser: (id) => api.post(`/api/admin/users/${id}/toggle`),
  verifyEmail: (id) => api.post(`/api/admin/users/${id}/verify-email`),
  resumeAccount: (id) => api.post(`/api/admin/accounts/${id}/resume`),
  deleteAccount: (id) => api.delete(`/api/admin/accounts/${id}`),
  retryDelivery: (id) => api.post(`/api/admin/deliveries/${id}/retry`),
  saveSettings: (data) => api.post("/api/admin/settings", data),
};

export default api;

export const digestsApi = {
  getHistory: (limit = 50) => api.get(`/api/digests/history?limit=${limit}`),
};
