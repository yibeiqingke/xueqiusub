<template>
  <div class="auth-page">
    <el-card class="auth-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <div class="header-icon">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <line x1="20" y1="8" x2="20" y2="14"></line>
              <line x1="23" y1="11" x2="17" y2="11"></line>
            </svg>
          </div>
          <h2>创建新账号</h2>
          <p class="subtitle">开启全网财经大V动态监控与 AI 智能推送服务</p>
        </div>
      </template>

      <el-form :model="form" :rules="rules" ref="formRef" label-position="top">
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="3-32 位用户名"
            prefix-icon="User"
            size="large"
            maxlength="32"
          />
        </el-form-item>

        <el-form-item label="设置密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位字符"
            prefix-icon="Lock"
            show-password
            size="large"
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            placeholder="请再次输入密码"
            prefix-icon="Key"
            show-password
            size="large"
          />
        </el-form-item>

        <el-form-item label="接收通知邮箱（选填）" prop="email">
          <el-input
            v-model="form.email"
            placeholder="用于接收研报推送与找回密码"
            prefix-icon="Message"
            size="large"
          />
        </el-form-item>

        <el-form-item label="验证问答：我们的微信群名叫什么？" prop="answer">
          <el-input
            v-model="form.answer"
            placeholder="请输入社群专属问答答案"
            prefix-icon="ChatLineRound"
            size="large"
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <div style="margin-top: 24px">
          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            :loading="loading"
            @click="handleRegister"
          >
            完成注册并开启
          </el-button>
        </div>

        <div class="auth-footer">
          <span>已有账号？</span>
          <router-link to="/login" class="link-btn">直接登录</router-link>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from "vue";
import { useRouter } from "vue-router";
import { authApi } from "../api.js";
import { ElMessage, ElMessageBox } from "element-plus";

const router = useRouter();
const formRef = ref(null);
const loading = ref(false);

const form = reactive({
  username: "",
  password: "",
  confirm: "",
  email: "",
  answer: "",
});

const validateConfirm = (rule, value, callback) => {
  if (value !== form.password) {
    callback(new Error("两次输入的密码不一致"));
  } else {
    callback();
  }
};

const rules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    { min: 3, max: 32, message: "长度需在 3 到 32 个字符之间", trigger: "blur" },
  ],
  password: [
    { required: true, message: "请设置登录密码", trigger: "blur" },
    { min: 6, message: "密码至少 6 位", trigger: "blur" },
  ],
  confirm: [
    { required: true, message: "请确认密码", trigger: "blur" },
    { validator: validateConfirm, trigger: "blur" },
  ],
  answer: [
    { required: true, message: "请输入微信群名答案", trigger: "blur" },
  ],
};

const handleRegister = async () => {
  if (!formRef.value) return;
  await formRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    try {
      const res = await authApi.register({
        username: form.username,
        password: form.password,
        confirm: form.confirm,
        email: form.email,
        answer: form.answer,
      });

      if (res.data.require_verify) {
        await ElMessageBox.alert(
          "注册成功！验证邮件已发送至您的邮箱，请查收邮件完成验证后登录。",
          "完成验证",
          { confirmButtonText: "去登录", type: "success" }
        );
        router.push("/login");
      } else {
        ElMessage.success(res.data.msg || "注册成功，已自动登录！");
        router.push("/");
      }
    } catch (e) {
      // Handled by axios interceptor
    } finally {
      loading.value = false;
    }
  });
};
</script>

<style scoped>
.auth-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 82vh;
  padding: 24px 16px;
}
.auth-card {
  width: 100%;
  max-width: 460px;
  border-radius: 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  background: var(--card-bg, #ffffff);
}
.card-header {
  text-align: center;
  padding: 8px 0 0;
}
.header-icon {
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: #e0f2fe;
  color: #0284c7;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 12px;
}
.card-header h2 {
  margin: 0 0 6px;
  color: var(--text-main, #0f172a);
  font-size: 22px;
  font-weight: 700;
}
.subtitle {
  color: var(--text-muted, #64748b);
  font-size: 13px;
  margin: 0;
}
.auth-footer {
  margin-top: 20px;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted, #64748b);
}
.link-btn {
  color: #0284c7;
  font-weight: 600;
  text-decoration: none;
  margin-left: 6px;
  transition: color 0.2s;
}
.link-btn:hover {
  color: #0369a1;
  text-decoration: underline;
}
</style>
