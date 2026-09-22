<template>
  <div class="login-page">
    <el-card class="login-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <span style="font-size: 32px">📈</span>
          <h2>雪球订阅助手</h2>
          <p class="subtitle">实时追踪大V核心研判 · AI 智能投研内参</p>
        </div>
      </template>

      <el-form :model="form" :rules="rules" ref="formRef" label-position="top">
        <el-form-item label="登录邮箱 / 用户名" prop="email">
          <el-input
            v-model="form.email"
            placeholder="请输入管理员邮箱或用户名"
            prefix-icon="User"
            size="large"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入登录密码"
            prefix-icon="Lock"
            show-password
            size="large"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <div style="margin-top: 24px">
          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            :loading="loading"
            @click="handleSubmit"
          >
            立即登录
          </el-button>
        </div>

        <div class="auth-footer">
          <span>还没有账号？</span>
          <router-link to="/register" class="register-link">立即注册</router-link>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from "vue";
import { useRouter } from "vue-router";
import { authApi } from "../api.js";
import { ElMessage } from "element-plus";

const router = useRouter();
const formRef = ref(null);
const loading = ref(false);

const form = reactive({
  email: "",
  password: "",
});

const rules = {
  email: [{ required: true, message: "请输入账号", trigger: "blur" }],
  password: [{ required: true, message: "请输入密码", trigger: "blur" }],
};

const handleSubmit = async () => {
  if (!formRef.value) return;
  await formRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    try {
      await authApi.login(form.email, form.password);
      ElMessage.success("登录成功，欢迎使用！");
      router.push("/");
    } catch (e) {
      // Handled by interceptor
    } finally {
      loading.value = false;
    }
  });
};
</script>

<style scoped>
.login-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 80vh;
}
.login-card {
  width: 100%;
  max-width: 440px;
  border-radius: 16px;
  border: 1px solid #e2e8f0;
}
.card-header {
  text-align: center;
  padding: 10px 0 0;
}
.card-header h2 {
  margin: 10px 0 6px;
  color: #0f172a;
  font-size: 22px;
}
.subtitle {
  color: #64748b;
  font-size: 13px;
  margin: 0;
}
.auth-footer {
  margin-top: 20px;
  text-align: center;
  font-size: 13px;
  color: #64748b;
}
.register-link {
  color: #0284c7;
  font-weight: 600;
  text-decoration: none;
  margin-left: 6px;
}
.register-link:hover {
  text-decoration: underline;
}
</style>
