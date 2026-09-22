<template>
  <div class="admin-view">
    <!-- Top Action Bar -->
    <div class="action-bar">
      <div>
        <h2 class="page-title">🛡️ 系统管理与运维后台</h2>
        <p class="page-desc">管理全局注册用户、监控全网爬虫抓取源健康度、失败投递补偿重试以及系统运行参数</p>
      </div>
      <div>
        <el-button :icon="Refresh" size="large" :loading="refreshing" @click="loadData">
          刷新数据
        </el-button>
      </div>
    </div>

    <!-- Stats Row -->
    <el-row :gutter="12" class="stats-row">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value">{{ stats.users }}</div>
          <div class="stat-label">注册总用户数</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value" style="color: #10b981">{{ stats.subscriptions }}</div>
          <div class="stat-label">活跃监控订阅数</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value" :style="{ color: stats.failed > 0 ? '#ef4444' : '#64748b' }">
            {{ stats.failed }}
          </div>
          <div class="stat-label">投递失败待重试记录</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Main Tabs -->
    <el-card shadow="never" class="tabs-card" v-loading="loading">
      <el-tabs v-model="activeTab">
        <!-- Tab 1: Users -->
        <el-tab-pane label="👥 用户管理" name="users">
          <div class="tab-toolbar">
            <el-button type="primary" :icon="Plus" @click="showCreateUserModal = true">
              快速创建新用户
            </el-button>
          </div>

          <el-table :data="users" style="width: 100%" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column label="用户邮箱 / 账号" min-width="200">
              <template #default="{ row }">
                <strong>{{ row.email }}</strong>
                <span v-if="row.username" style="color: #94a3b8; margin-left: 6px">({{ row.username }})</span>
              </template>
            </el-table-column>
            <el-table-column label="权限角色" width="130">
              <template #default="{ row }">
                <el-tag :type="row.is_admin ? 'danger' : 'info'" size="small">
                  {{ row.is_admin ? "超级管理员" : "普通用户" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="邮箱验证" width="140">
              <template #default="{ row }">
                <el-tag v-if="row.email_verified" type="success" size="small">已验证</el-tag>
                <el-button
                  v-else
                  size="small"
                  type="warning"
                  link
                  @click="handleVerifyEmail(row)"
                >
                  未验证 (点击通过)
                </el-button>
              </template>
            </el-table-column>
            <el-table-column label="账号状态" width="120" align="center">
              <template #default="{ row }">
                <el-switch
                  v-model="row.is_enabled"
                  size="small"
                  inline-prompt
                  active-text="启用"
                  inactive-text="停用"
                  @change="handleToggleUser(row)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="subscriptions_count" label="关注数" width="90" align="center" />
            <el-table-column prop="created_at" label="注册时间" width="180">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab 2: Accounts -->
        <el-tab-pane label="🌐 抓取信源监控" name="accounts">
          <el-table :data="accounts" style="width: 100%" stripe>
            <el-table-column label="平台" width="95">
              <template #default="{ row }">
                <el-tag size="small">{{ formatPlatform(row.platform) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="信源名称 / 标识" min-width="180">
              <template #default="{ row }">
                <div>
                  <strong>{{ row.display_name }}</strong>
                  <div style="font-size: 12px; color: #94a3b8">UID: {{ row.xueqiu_user_id }}</div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="最新动态 / 抓取时间" min-width="240">
              <template #default="{ row }">
                <div v-if="row.latest_item_title" style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                  {{ row.latest_item_title }}
                </div>
                <div style="font-size: 12px; color: #94a3b8">
                  {{ row.latest_item_time || "监听中" }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="抓取健康度" width="150">
              <template #default="{ row }">
                <el-tag v-if="row.paused_until" type="danger" size="small">
                  熔断暂停中
                </el-tag>
                <el-tag v-else-if="row.failed_fetches > 0" type="warning" size="small">
                  失败重试 ({{ row.failed_fetches }})
                </el-tag>
                <el-tag v-else type="success" size="small">
                  正常监听
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="subscriptions_count" label="订阅人数" width="100" align="center" />
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" link @click="handleResumeAccount(row)">
                  恢复/抓取
                </el-button>
                <el-button size="small" type="danger" link @click="handleDeleteAccount(row)">
                  删除源
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab 3: Failed Deliveries -->
        <el-tab-pane label="⚠️ 失败投递补偿重试" name="deliveries">
          <div v-if="failedDeliveries.length === 0" class="empty-box">
            <el-empty description="当前无失败投递记录，消息投递流水线运行良好" />
          </div>
          <el-table v-else :data="failedDeliveries" style="width: 100%" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="user_email" label="收件邮箱" width="180" />
            <el-table-column prop="title" label="消息动态标题" min-width="200" />
            <el-table-column prop="error_message" label="失败原因" min-width="200">
              <template #default="{ row }">
                <span style="color: #ef4444; font-size: 13px">{{ row.error_message || "未知异常" }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="retry_count" label="重试次数" width="90" align="center" />
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" :loading="row._retrying" @click="handleRetryDelivery(row)">
                  一键重发
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab 4: System Settings -->
        <el-tab-pane label="⚙️ 系统运行与全局参数" name="settings">
          <el-form label-position="top" style="max-width: 680px; margin-top: 10px">
            <el-form-item label="每日邮件汇总全局默认时间 (整点)">
              <el-select v-model="systemSettings.digest_hour" style="width: 220px">
                <el-option
                  v-for="h in 24"
                  :key="h - 1"
                  :label="`${String(h - 1).padStart(2, '0')}:00 (每天 ${String(h - 1).padStart(2, '0')}:00)`"
                  :value="h - 1"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="单封每日汇总最大条数上限">
              <el-select v-model="systemSettings.digest_max_per_email" style="width: 280px">
                <el-option :value="0" label="不限制 / 全部合并为 1 封邮件 (推荐)" />
                <el-option :value="200" label="超过 200 条时自动拆封" />
                <el-option :value="100" label="超过 100 条时自动拆封" />
                <el-option :value="50" label="超过 50 条时自动拆封" />
              </el-select>
            </el-form-item>

            <el-form-item label="系统基准时区">
              <el-select v-model="systemSettings.app_timezone" style="width: 340px">
                <el-option value="Asia/Shanghai" label="Asia/Shanghai (北京时间 UTC+8)" />
                <el-option value="Asia/Hong_Kong" label="Asia/Hong_Kong (香港时间 UTC+8)" />
                <el-option value="Asia/Taipei" label="Asia/Taipei (台北时间 UTC+8)" />
                <el-option value="Asia/Singapore" label="Asia/Singapore (新加坡时间 UTC+8)" />
                <el-option value="Asia/Tokyo" label="Asia/Tokyo (东京时间 UTC+9)" />
                <el-option value="UTC" label="UTC (世界协调时间 UTC+0)" />
                <el-option value="Europe/London" label="Europe/London (伦敦时间 UTC+0/+1)" />
                <el-option value="America/New_York" label="America/New_York (美东时间 UTC-5/-4)" />
              </el-select>
            </el-form-item>

            <el-form-item label="微信群消息采集接入 (WeChat Ingest)">
              <el-switch
                v-model="systemSettings.wechat_ingest_enabled"
                active-text="启用群聊采集 Webhook"
              />
            </el-form-item>

            <el-divider />

            <h4 style="margin: 10px 0">🤖 全局系统公共大模型 (LLM) 通道</h4>
            <el-form-item>
              <el-switch v-model="systemSettings.llm_enabled" active-text="开启全局 AI 智能研报" />
            </el-form-item>

            <el-form-item label="全局 API Base URL">
              <el-input v-model="systemSettings.llm_api_base" placeholder="例如：https://api.deepseek.com/v1" />
            </el-form-item>

            <el-row :gutter="16">
              <el-col :xs="24" :sm="12">
                <el-form-item label="全局模型名称">
                  <el-input v-model="systemSettings.llm_model" placeholder="例如：deepseek-chat" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12">
                <el-form-item label="全局 API Key">
                  <el-input
                    v-model="systemSettings.llm_api_key"
                    type="password"
                    show-password
                    placeholder="留空保持原配置"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <div style="margin-top: 20px">
              <el-button type="primary" size="large" :loading="savingSettings" @click="handleSaveSettings">
                保存系统运行参数
              </el-button>
            </div>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- Create User Modal -->
    <el-dialog v-model="showCreateUserModal" title="快速创建新用户" width="min(460px, 94vw)">
      <el-form label-position="top">
        <el-form-item label="用户邮箱 (登录凭证)">
          <el-input v-model="newUserForm.email" placeholder="user@example.com" />
        </el-form-item>
        <el-form-item label="初始密码 (至少 6 位)">
          <el-input v-model="newUserForm.password" type="password" show-password placeholder="请输入初始密码" />
        </el-form-item>
        <el-form-item label="赋予管理员权限">
          <el-switch v-model="newUserForm.is_admin" active-text="超级管理员" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateUserModal = false">取消</el-button>
        <el-button type="primary" :loading="creatingUser" @click="handleCreateUser">确认创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from "vue";
import { Refresh, Plus } from "@element-plus/icons-vue";
import { adminApi } from "../api.js";
import { ElMessage, ElMessageBox } from "element-plus";

const loading = ref(false);
const refreshing = ref(false);
const activeTab = ref("users");

const stats = reactive({
  users: 0,
  subscriptions: 0,
  failed: 0,
});

const users = ref([]);
const accounts = ref([]);
const failedDeliveries = ref([]);
const systemSettings = reactive({
  digest_hour: 20,
  app_timezone: "Asia/Shanghai",
  digest_max_per_email: 0,
  wechat_ingest_enabled: false,
  llm_enabled: true,
  llm_api_base: "",
  llm_model: "",
  llm_api_key: "",
});

const loadData = async () => {
  loading.value = true;
  try {
    const res = await adminApi.getData();
    Object.assign(stats, res.stats || {});
    users.value = res.users || [];
    accounts.value = res.accounts || [];
    failedDeliveries.value = res.failed_deliveries || [];
    Object.assign(systemSettings, res.settings || {});
  } catch (e) {
    // handled
  } finally {
    loading.value = false;
  }
};

const formatPlatform = (p) => {
  const map = {
    xueqiu: "雪球",
    xueqiu_cube: "雪球组合",
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

const formatTime = (t) => {
  if (!t) return "-";
  return new Date(t).toLocaleString("zh-CN", { hour12: false });
};

// User operations
const showCreateUserModal = ref(false);
const creatingUser = ref(false);
const newUserForm = reactive({
  email: "",
  password: "",
  is_admin: false,
});

const handleCreateUser = async () => {
  if (!newUserForm.email || !newUserForm.password) {
    ElMessage.warning("请填写完整邮箱与初始密码");
    return;
  }
  creatingUser.value = true;
  try {
    await adminApi.createUser(newUserForm);
    ElMessage.success("用户创建成功");
    showCreateUserModal.value = false;
    newUserForm.email = "";
    newUserForm.password = "";
    newUserForm.is_admin = false;
    loadData();
  } catch (e) {}
  finally {
    creatingUser.value = false;
  }
};

const handleToggleUser = async (user) => {
  try {
    const res = await adminApi.toggleUser(user.id);
    user.is_enabled = res.is_enabled;
    ElMessage.success(user.is_enabled ? "已启用该账号" : "已停用该账号");
  } catch (e) {
    user.is_enabled = !user.is_enabled;
  }
};

const handleVerifyEmail = async (user) => {
  try {
    await adminApi.verifyEmail(user.id);
    user.email_verified = true;
    ElMessage.success("已通过邮箱认证");
  } catch (e) {}
};

// Account operations
const handleResumeAccount = async (account) => {
  try {
    const res = await adminApi.resumeAccount(account.id);
    ElMessage.success(res.message || "已触发恢复并抓取");
    loadData();
  } catch (e) {}
};

const handleDeleteAccount = (account) => {
  ElMessageBox.confirm(`确认彻底删除抓取源「${account.display_name}」吗？`, "删除确认", {
    type: "warning",
  }).then(async () => {
    try {
      await adminApi.deleteAccount(account.id);
      ElMessage.success("抓取源已删除");
      loadData();
    } catch (e) {}
  });
};

// Delivery retry
const handleRetryDelivery = async (delivery) => {
  delivery._retrying = true;
  try {
    const res = await adminApi.retryDelivery(delivery.id);
    ElMessage.success(res.message || "已触发补偿重发");
    loadData();
  } catch (e) {}
  finally {
    delivery._retrying = false;
  }
};

// Save global system settings
const savingSettings = ref(false);
const handleSaveSettings = async () => {
  savingSettings.value = true;
  try {
    const res = await adminApi.saveSettings(systemSettings);
    ElMessage.success(res.message || "系统全局参数保存成功");
    loadData();
  } catch (e) {}
  finally {
    savingSettings.value = false;
  }
};

onMounted(() => {
  loadData();
});
</script>

<style scoped>
.admin-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.action-bar {
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
.stats-row {
  margin-bottom: 4px;
}
.stat-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  padding: 12px;
}
.stat-value {
  font-size: 28px;
  font-weight: 800;
  color: #0284c7;
}
.stat-label {
  font-size: 13px;
  color: #64748b;
  margin-top: 4px;
}
.tabs-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  min-height: 500px;
}
.tab-toolbar {
  margin-bottom: 16px;
}
.empty-box {
  padding: 40px 0;
}

@media (max-width: 768px) {
  .admin-view {
    gap: 14px;
  }
  .action-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .action-bar .el-button {
    width: 100%;
  }
  .stats-row .el-col {
    margin-bottom: 8px;
  }
  .stat-card {
    padding: 8px 12px;
  }
  .stat-value {
    font-size: 24px;
  }
}
</style>
