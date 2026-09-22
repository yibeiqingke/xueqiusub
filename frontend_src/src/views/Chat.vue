<template>
  <div class="chat-view">
    <div class="chat-header">
      <div>
        <h2 class="page-title">AI 智能投研助手</h2>
        <p class="page-desc">基于大模型与你订阅的所有雪球大V动态，进行全局多轮深度问答与研判分析</p>
      </div>
      <el-button type="danger" plain size="small" :icon="Delete" @click="handleClear">
        清空对话
      </el-button>
    </div>

    <!-- Suggested Prompts -->
    <div class="prompt-suggestions">
      <span class="suggestion-label">💡 快捷研判：</span>
      <el-tag
        v-for="p in suggestions"
        :key="p"
        class="suggestion-tag"
        effect="plain"
        round
        @click="applySuggestion(p)"
      >
        {{ p }}
      </el-tag>
    </div>

    <!-- Message History Area -->
    <el-card shadow="never" class="messages-card" ref="msgContainer">
      <div v-if="messages.length === 0" class="empty-chat">
        <span style="font-size: 48px">🤖</span>
        <h3>你好！我是你的雪球投研 AI 助手</h3>
        <p>你可以直接向我提问关于已关注大V的投资观点、持仓变化或行业研判。</p>
      </div>

      <div
        v-for="(msg, index) in messages"
        :key="index"
        :class="['msg-row', msg.role === 'user' ? 'user-row' : 'assistant-row']"
      >
        <div v-if="msg.role !== 'user'" class="avatar-bot">🤖</div>
        <div class="msg-bubble">
          <div class="msg-text" v-html="formatMessage(msg.content)"></div>
        </div>
        <div v-if="msg.role === 'user'" class="avatar-user">👤</div>
      </div>

      <div v-if="sending" class="msg-row assistant-row">
        <div class="avatar-bot">🤖</div>
        <div class="msg-bubble loading-bubble">
          <span class="loading-dots">AI 正在检索大V动态并思考分析中...</span>
        </div>
      </div>
    </el-card>

    <!-- Input Box -->
    <div class="input-container">
      <el-input
        v-model="inputQuery"
        type="textarea"
        :rows="3"
        placeholder="输入你想向 AI 提问的投研问题（按 Ctrl+Enter 快速发送）..."
        resize="none"
        @keydown.ctrl.enter="handleSend"
      />
      <div class="input-actions">
        <span class="input-tip">支持按 Ctrl+Enter 快捷发送</span>
        <el-button type="primary" size="large" :loading="sending" :icon="Promotion" @click="handleSend">
          发送提问
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from "vue";
import { Delete, Promotion } from "@element-plus/icons-vue";
import { chatApi } from "../api.js";
import { ElMessage, ElMessageBox } from "element-plus";

const messages = ref([]);
const inputQuery = ref("");
const sending = ref(false);
const msgContainer = ref(null);

const suggestions = [
  "总结大V们对今日大盘行情的关键研判",
  "近期是否有大V提到了调仓或减仓信号？",
  "梳理关注的大V对半导体板块的最新分歧",
];

const loadHistory = async () => {
  try {
    const res = await chatApi.getHistory();
    messages.value = res.messages || [];
    scrollToBottom();
  } catch (e) {}
};

const scrollToBottom = () => {
  nextTick(() => {
    if (msgContainer.value?.$el) {
      msgContainer.value.$el.scrollTop = msgContainer.value.$el.scrollHeight;
    }
  });
};

// ---------------------------------------------------------------------------
// [sanitize:start]
// msg.content is an LLM reply built on top of untrusted post text, so it is
// attacker-influenced, and it is injected with v-html. Escape FIRST, then
// re-enable only the cosmetic constructs this view wants (line breaks and
// bold). Every raw "<" in the output comes from a literal template below.
// ---------------------------------------------------------------------------
const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

// Single pass over the 5 chars, so an inserted "&amp;" is never rescanned:
// "&lt;" becomes "&amp;lt;" exactly once and can never double-escape.
// Quotes are not required in a pure text context, but escaping them keeps the
// invariant "no markup-significant char survives" independent of the output
// context, so the helper stays safe if reused inside an attribute.
const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);

// Body excludes "<" and ">" (and newlines), so a "**bold**" span can never
// carry a tag delimiter into the strong template nor cross a real line break.
const BOLD_RE = /\*\*([^<>\n]+?)\*\*/g;

const formatMessage = (content) => {
  if (!content) return "";
  let html = escapeHtml(content); // 1. neutralise everything
  html = html.replace(/\n/g, "<br/>"); // 2. cosmetic line breaks
  html = html.replace(BOLD_RE, "<strong>$1</strong>"); // 3. bold
  return html;
};
// ---------------------------------------------------------------------------
// [sanitize:end]
// ---------------------------------------------------------------------------

const applySuggestion = (text) => {
  inputQuery.value = text;
  handleSend();
};

const handleSend = async () => {
  const q = inputQuery.value.trim();
  if (!q || sending.value) return;

  messages.value.push({ role: "user", content: q });
  inputQuery.value = "";
  sending.value = true;
  scrollToBottom();

  try {
    const res = await chatApi.send(q);
    messages.value.push({ role: "assistant", content: res.reply || res.content || "生成完成" });
  } catch (e) {
    messages.value.push({ role: "assistant", content: "⚠️ 抱歉，调用大模型分析时发生异常，请检查配置或稍后重试。" });
  } finally {
    sending.value = false;
    scrollToBottom();
  }
};

const handleClear = () => {
  ElMessageBox.confirm("确定要清空当前的 AI 投研历史会话吗？", "清空提示", {
    type: "warning",
  }).then(async () => {
    try {
      await chatApi.clear();
      messages.value = [];
      ElMessage.success("历史对话已清空");
    } catch (e) {}
  });
};

onMounted(() => {
  loadHistory();
});
</script>

<style scoped>
.chat-view {
  max-width: 1000px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  gap: 14px;
}
.chat-header {
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
.prompt-suggestions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.suggestion-label {
  font-size: 13px;
  color: #64748b;
  font-weight: 600;
}
.suggestion-tag {
  cursor: pointer;
  transition: all 0.2s;
}
.suggestion-tag:hover {
  background: #0284c7;
  color: #ffffff;
  border-color: #0284c7;
}
.messages-card {
  flex: 1;
  overflow-y: auto;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  padding: 10px;
  background: #ffffff;
}
.empty-chat {
  text-align: center;
  padding: 60px 20px;
  color: #64748b;
}
.empty-chat h3 {
  margin: 16px 0 8px;
  color: #0f172a;
}
.msg-row {
  display: flex;
  margin-bottom: 20px;
  gap: 12px;
}
.user-row {
  justify-content: flex-end;
}
.assistant-row {
  justify-content: flex-start;
}
.avatar-bot, .avatar-user {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  background: #e0f2fe;
  flex-shrink: 0;
}
.avatar-user {
  background: #f1f5f9;
}
.msg-bubble {
  max-width: 80%;
  padding: 12px 18px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.65;
}
.user-row .msg-bubble {
  background: #0284c7;
  color: #ffffff;
  border-bottom-right-radius: 4px;
}
.assistant-row .msg-bubble {
  background: #f8fafc;
  color: #1e293b;
  border: 1px solid #e2e8f0;
  border-bottom-left-radius: 4px;
}
.loading-bubble {
  color: #64748b;
  font-style: italic;
}
.input-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.input-tip {
  font-size: 12px;
  color: #94a3b8;
}

@media (max-width: 768px) {
  .chat-view {
    height: calc(100vh - 145px);
    gap: 10px;
  }
  .chat-header {
    margin-bottom: 2px;
  }
  .prompt-suggestions {
    overflow-x: auto;
    white-space: nowrap;
    flex-wrap: nowrap;
    padding-bottom: 4px;
    -webkit-overflow-scrolling: touch;
  }
  .prompt-suggestions::-webkit-scrollbar {
    display: none;
  }
  .msg-bubble {
    max-width: 90% !important;
  }
  .input-tip {
    display: none;
  }
}
</style>
