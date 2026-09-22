<template>
  <div class="settings-view">
    <div class="page-head">
      <h2 class="page-title">系统与多渠道推送设置</h2>
      <p class="page-desc">管理您的专属大模型通道、飞书/钉钉/企业微信群机器人即时推送、发信邮箱与账号安全</p>
    </div>

    <!-- 敏感凭据仅以占位符回显；加载失败时禁用保存，避免空表单清除已存凭据 -->
    <el-alert
      v-if="!loading && !loaded"
      type="warning"
      :closable="false"
      show-icon
      title="设置加载失败"
      description="为避免误清除已保存的密钥与授权码，保存按钮已临时禁用，请刷新页面重试。"
    />

    <div v-loading="loading" class="settings-sections">
      <!-- 1. LLM AI Settings -->
      <el-card shadow="never" class="settings-card">
        <template #header>
          <div class="card-header-flex">
            <div class="card-header-title">
              <span class="icon">🤖</span>
              <h3>AI 智能研报速览与专属大模型</h3>
            </div>
            <el-tag type="info" size="small">支持 DeepSeek / GPT / 兼容通道</el-tag>
          </div>
        </template>

        <el-form label-position="top">
          <el-form-item>
            <el-switch
              v-model="form.llm_enabled"
              active-text="开启每日邮件/动态 AI 智能速览研报"
            />
          </el-form-item>

          <p class="section-desc">配置您自己的 API Key 后将优先使用个人专属通道，不填则默认使用系统公共通道：</p>

          <el-row :gutter="16">
            <el-col :span="24">
              <el-form-item label="API 接口地址 (Base URL)">
                <el-input v-model="form.llm_api_base" placeholder="例如：https://api.deepseek.com/v1 或官方直连地址" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-form-item label="模型名称 (Model)">
                <el-input v-model="form.llm_model" placeholder="例如：deepseek-chat 或 gpt-4o-mini" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-form-item label="API Key">
                <el-input
                  v-model="form.llm_api_key"
                  type="password"
                  show-password
                  :placeholder="secretPh('has_llm_api_key', '已配置，留空可清除，粘贴新 Key 可覆盖', '输入 sk-... API Key')"
                />
                <span class="field-tip">{{ secretTip('has_llm_api_key') }}</span>
              </el-form-item>
            </el-col>
          </el-row>

          <div class="form-actions">
            <el-button type="primary" :loading="saving" :disabled="!loaded" @click="handleSave">保存 AI 配置</el-button>
          </div>
        </el-form>
      </el-card>

      <!-- 2. Daily Digest Schedule -->
      <el-card shadow="never" class="settings-card">
        <template #header>
          <div class="card-header-title">
            <span class="icon">⏰</span>
            <h3>投研内参每日汇总投递时间</h3>
          </div>
        </template>

        <el-form label-position="top">
          <el-form-item label="每日汇总发送时间 (整点)">
            <el-select v-model="form.digest_hour" style="width: 220px" :class="{ 'hour-invalid': !digestHourValid }">
              <el-option
                v-for="h in 24"
                :key="h - 1"
                :label="`${String(h - 1).padStart(2, '0')}:00 (每天 ${String(h - 1).padStart(2, '0')}:00)`"
                :value="h - 1"
              />
            </el-select>
            <span class="field-tip">仅影响本账号：到达该整点时，系统将汇总当天大V最新观点与调仓信号并投递给您；其他用户的推送时间互不影响</span>
            <span v-if="!digestHourValid" class="field-tip hour-error">每日汇总时间需为 00:00–23:00 之间的整点，请重新选择后再保存</span>
          </el-form-item>

          <div class="form-actions">
            <el-button type="primary" :loading="saving" :disabled="!loaded" @click="handleSave">保存时间偏好</el-button>
          </div>
        </el-form>
      </el-card>

      <!-- 3. Webhook Bot Settings -->
      <el-card shadow="never" class="settings-card">
        <template #header>
          <div class="card-header-flex">
            <div class="card-header-title">
              <span class="icon">🚀</span>
              <h3>群机器人 Webhook 即时推送</h3>
            </div>
            <el-tag type="success" size="small">支持 飞书 / 钉钉 / 企业微信</el-tag>
          </div>
        </template>

        <el-form label-position="top">
          <el-form-item>
            <el-switch
              v-model="form.webhook_enabled"
              active-text="开启群机器人同步推送 (大V发帖秒级推送到群)"
            />
          </el-form-item>

          <el-row :gutter="16">
            <el-col :xs="24" :sm="8">
              <el-form-item label="机器人平台类型">
                <el-select v-model="form.webhook_type" style="width: 100%">
                  <el-option label="🤖 自动识别平台" value="auto" />
                  <el-option label="🐦 飞书群自定义机器人" value="feishu" />
                  <el-option label="📌 钉钉群自定义机器人" value="dingtalk" />
                  <el-option label="💼 企业微信群机器人" value="wecom" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="16">
              <el-form-item label="Webhook 完整链接 (URL)">
                <el-input
                  v-model="form.webhook_url"
                  :placeholder="secretPh('has_webhook_url', '已配置，留空可清除，粘贴新链接可覆盖', '例如：https://open.feishu.cn/... 或 https://oapi.dingtalk.com/...')"
                />
                <span class="field-tip">{{ secretTip('has_webhook_url') }}</span>
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="加签密钥 Secret (可选，若群开启了加签则必填)">
                <el-input
                  v-model="form.webhook_secret"
                  type="password"
                  show-password
                  :placeholder="secretPh('has_webhook_secret', '已配置，留空可清除，输入新 Secret 可覆盖', '留空不启用加签，输入钉钉/飞书 Secret')"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <div class="form-actions">
            <el-button type="primary" :loading="saving" :disabled="!loaded" @click="handleSave">保存 Webhook 配置</el-button>
            <el-button type="success" plain :loading="testingWebhook" @click="handleTestWebhook">
              发送测试消息到群
            </el-button>
            <el-button type="danger" plain :disabled="!loaded" @click="handleClearWebhook">
              清除 Webhook
            </el-button>
          </div>
        </el-form>
      </el-card>

      <!-- 4. SMTP Custom Email -->
      <el-card shadow="never" class="settings-card">
        <template #header>
          <div class="card-header-flex">
            <div class="card-header-title">
              <span class="icon">📧</span>
              <h3>自定义发信邮箱 (SMTP，可选)</h3>
            </div>
            <el-tag type="info" size="small">支持 QQ / 163 / 阿里云 / Gmail 等</el-tag>
          </div>
        </template>

        <el-form label-position="top">
          <!-- Preset quick select -->
          <div class="preset-box">
            <span class="preset-label">⚡ 常用服务商快捷配置：</span>
            <el-select
              v-model="selectedPreset"
              placeholder="选择常用服务商自动填充..."
              class="preset-select"
              @change="applyPreset"
            >
              <el-option label="QQ 邮箱 (smtp.qq.com : 465 SSL)" value="qq" />
              <el-option label="163 网易邮箱 (smtp.163.com : 465 SSL)" value="163" />
              <el-option label="126 网易邮箱 (smtp.126.com : 465 SSL)" value="126" />
              <el-option label="阿里云企业/个人邮箱 (smtp.aliyun.com : 465 SSL)" value="aliyun" />
              <el-option label="新浪邮箱 (smtp.sina.com : 465 SSL)" value="sina" />
              <el-option label="Outlook / Hotmail (smtp.office365.com : 587 STARTTLS)" value="outlook" />
              <el-option label="Gmail (smtp.gmail.com : 465 SSL)" value="gmail" />
            </el-select>
          </div>

          <el-row :gutter="16">
            <el-col :xs="24" :sm="16">
              <el-form-item label="SMTP 服务器主机">
                <el-input v-model="form.smtp_host" placeholder="例如 smtp.qq.com" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="8">
              <el-form-item label="端口">
                <el-input-number v-model="form.smtp_port" :min="1" :max="65535" style="width: 100%" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-form-item label="发信邮箱账号 (用户名)">
                <el-input v-model="form.smtp_username" placeholder="your_email@example.com" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-form-item label="授权码 / 密码">
                <el-input
                  v-model="form.smtp_password"
                  type="password"
                  show-password
                  :placeholder="secretPh('has_smtp_password', '已配置，留空可清除，输入新授权码可覆盖', '国内邮箱填写 16 位授权码')"
                />
                <span class="field-tip">{{ secretTip('has_smtp_password') }}</span>
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="发件人显示昵称 (可选)">
                <el-input v-model="form.smtp_from" placeholder="例如：智投内参发信 或 您的姓名" />
              </el-form-item>
            </el-col>
          </el-row>

          <div class="protocol-checks">
            <el-checkbox v-model="form.smtp_ssl">使用 SSL 加密协议 (推荐 QQ/163 465 端口)</el-checkbox>
            <el-checkbox v-model="form.smtp_starttls">使用 STARTTLS 协议 (推荐 Outlook 587 端口)</el-checkbox>
          </div>

          <div class="form-actions">
            <el-button type="primary" :loading="saving" :disabled="!loaded" @click="handleSave">保存邮箱配置</el-button>
            <el-button type="success" plain :loading="testingEmail" @click="handleTestEmail">
              发送测试邮件
            </el-button>
            <el-button type="danger" plain :disabled="!loaded" @click="handleClearSmtp">
              清除配置 (回退系统默认)
            </el-button>
          </div>
        </el-form>
      </el-card>

      <!-- 5. Password Change -->
      <el-card shadow="never" class="settings-card">
        <template #header>
          <div class="card-header-title">
            <span class="icon">🔒</span>
            <h3>账号安全 · 修改登录密码</h3>
          </div>
        </template>

        <el-form label-position="top" style="max-width: 480px">
          <el-form-item label="当前原密码">
            <el-input v-model="pwdForm.old_password" type="password" show-password placeholder="请输入当前密码" />
          </el-form-item>
          <el-form-item label="新密码 (至少 6 位)">
            <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="请输入新密码" />
          </el-form-item>
          <el-form-item label="确认新密码">
            <el-input v-model="pwdForm.confirm_password" type="password" show-password placeholder="请再次输入新密码" />
          </el-form-item>

          <div class="form-actions">
            <el-button type="primary" :loading="savingPwd" @click="handleChangePassword">
              确认修改密码
            </el-button>
          </div>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from "vue";
import { settingsApi } from "../api.js";
import { ElMessage, ElMessageBox } from "element-plus";

const loading = ref(false);
const saving = ref(false);
const testingWebhook = ref(false);
const testingEmail = ref(false);
const savingPwd = ref(false);
const selectedPreset = ref("");
// 每日汇总整点（0-23）的前端校验状态，与后端 Pydantic 的 ge/le 一致
const digestHourValid = ref(true);
// 与后端 main.py 的 SECRET_MASK 一致：敏感字段后端只回显该哨兵，永不下发明文
const SECRET_MASK = "********";
// GET 成功后置为 true；未成功前禁用保存，避免空表单把已存凭据全部清除
const loaded = ref(false);
// 各敏感字段是否已配置（由后端 has_xxx 标记驱动占位提示）
const configured = reactive({
  has_llm_api_key: false,
  has_smtp_password: false,
  has_webhook_url: false,
  has_webhook_secret: false,
});

const form = reactive({
  llm_enabled: true,
  llm_api_base: "",
  llm_api_key: "",
  llm_model: "",
  digest_hour: 20,
  smtp_host: "",
  smtp_port: 465,
  smtp_username: "",
  smtp_password: "",
  smtp_from: "",
  smtp_ssl: true,
  smtp_starttls: false,
  webhook_enabled: false,
  webhook_type: "auto",
  webhook_url: "",
  webhook_secret: "",
});

const pwdForm = reactive({
  old_password: "",
  new_password: "",
  confirm_password: "",
});

const PRESETS = {
  qq: { host: "smtp.qq.com", port: 465, ssl: true, starttls: false },
  163: { host: "smtp.163.com", port: 465, ssl: true, starttls: false },
  126: { host: "smtp.126.com", port: 465, ssl: true, starttls: false },
  aliyun: { host: "smtp.aliyun.com", port: 465, ssl: true, starttls: false },
  sina: { host: "smtp.sina.com", port: 465, ssl: true, starttls: false },
  outlook: { host: "smtp.office365.com", port: 587, ssl: false, starttls: true },
  gmail: { host: "smtp.gmail.com", port: 465, ssl: true, starttls: false },
};

const applyPreset = (key) => {
  const p = PRESETS[key];
  if (!p) return;
  form.smtp_host = p.host;
  form.smtp_port = p.port;
  form.smtp_ssl = p.ssl;
  form.smtp_starttls = p.starttls;
};

// 敏感字段的占位提示与说明文案：只依据后端 has_xxx 标记，不接触真实值
const secretPh = (flag, configuredPh, emptyPh) => (configured[flag] ? configuredPh : emptyPh);
const secretTip = (flag) =>
  configured[flag]
    ? `已加密保存，此处仅显示占位符 ${SECRET_MASK}；保持原样提交不会改动凭据，清空并提交即删除。`
    : "尚未配置，填写后保存即生效。";

const loadSettings = async () => {
  loading.value = true;
  try {
    const data = await settingsApi.get();
    // 只回填表单已声明的键，后端新增的 has_xxx 标记不进入提交负载
    Object.keys(form).forEach((k) => {
      if (k in data) form[k] = data[k];
    });
    Object.keys(configured).forEach((k) => {
      configured[k] = Boolean(data[k]);
    });
    digestHourValid.value = true;
    loaded.value = true;
  } catch (e) {}
  finally {
    loading.value = false;
  }
};

const isDigestHourError = (e) => {
  const detail = e?.response?.data?.detail;
  return Array.isArray(detail) && detail.some((d) => (d.loc || []).includes("digest_hour"));
};

const handleSave = async () => {
  // 设置未加载成功时禁止提交：空表单会被后端解释为「清除全部凭据」
  if (!loaded.value) {
    ElMessage.error("设置未能加载，为避免误清除已保存的凭据，请刷新页面后重试");
    return;
  }
  const hour = Number(form.digest_hour);
  digestHourValid.value = Number.isInteger(hour) && hour >= 0 && hour <= 23;
  if (!digestHourValid.value) {
    ElMessage.error("每日汇总时间需为 00:00–23:00 之间的整点");
    return;
  }
  saving.value = true;
  try {
    await settingsApi.save(form);
    ElMessage.success("配置已成功保存并立即生效");
  } catch (e) {
    // 后端 Pydantic 对 digest_hour 越界返回 422 结构化错误，这里转成可读提示
    if (isDigestHourError(e)) {
      digestHourValid.value = false;
      ElMessage.error("每日汇总时间需为 00:00–23:00 之间的整点");
    }
  }
  finally {
    saving.value = false;
  }
};

const handleTestWebhook = async () => {
  testingWebhook.value = true;
  try {
    const res = await settingsApi.testWebhook();
    ElMessage.success(res.message || "已向群机器人发送测试消息，请在群聊中查收！");
  } catch (e) {}
  finally {
    testingWebhook.value = false;
  }
};

const handleClearWebhook = () => {
  ElMessageBox.confirm("确定清除群机器人 Webhook 配置？", "提示", { type: "warning" }).then(async () => {
    form.webhook_enabled = false;
    form.webhook_url = "";
    form.webhook_secret = "";
    await handleSave();
  });
};

const handleTestEmail = async () => {
  testingEmail.value = true;
  try {
    const res = await settingsApi.testEmail();
    ElMessage.success(res.message || "测试邮件已成功投递，请查收邮箱！");
  } catch (e) {}
  finally {
    testingEmail.value = false;
  }
};

const handleClearSmtp = () => {
  ElMessageBox.confirm("确定清除自管 SMTP 配置？清除后将回退使用系统默认全局发信通道。", "提示", { type: "warning" }).then(async () => {
    form.smtp_host = "";
    form.smtp_username = "";
    form.smtp_password = "";
    form.smtp_from = "";
    await handleSave();
  });
};

const handleChangePassword = async () => {
  if (!pwdForm.old_password || !pwdForm.new_password) {
    ElMessage.warning("请填写原密码与新密码");
    return;
  }
  if (pwdForm.new_password.length < 6) {
    ElMessage.warning("新密码至少 6 位");
    return;
  }
  if (pwdForm.new_password !== pwdForm.confirm_password) {
    ElMessage.warning("两次输入的新密码不一致");
    return;
  }
  savingPwd.value = true;
  try {
    const res = await settingsApi.changePassword({
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    });
    ElMessage.success(res.message || "密码修改成功");
    pwdForm.old_password = "";
    pwdForm.new_password = "";
    pwdForm.confirm_password = "";
  } catch (e) {}
  finally {
    savingPwd.value = false;
  }
};

onMounted(() => {
  loadSettings();
});
</script>

<style scoped>
.settings-view {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
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
.settings-sections {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.settings-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
}
.card-header-flex {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-header-title .icon {
  font-size: 18px;
}
.card-header-title h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}
.section-desc {
  font-size: 13px;
  color: #64748b;
  margin: 0 0 16px;
}
.field-tip {
  font-size: 12px;
  color: #94a3b8;
  display: block;
  margin-top: 4px;
}
.hour-error {
  color: #dc2626;
}
.hour-invalid :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px #dc2626 inset;
}
.preset-box {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.preset-label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  white-space: nowrap;
}
.preset-select {
  width: 320px;
}
.protocol-checks {
  margin-bottom: 20px;
  display: flex;
  gap: 24px;
}
.form-actions {
  margin-top: 10px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .preset-box {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
  .preset-select {
    width: 100% !important;
  }
  .protocol-checks {
    flex-direction: column;
    gap: 8px;
  }
  .form-actions .el-button {
    margin-left: 0 !important;
    width: 100%;
  }
}
</style>
