<div align="center">

# 智投内参 (xueqiusub)
### 🚀 专为个人投资者打造的雪球大V动态追踪与 AI 智能投研内参系统

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3.5-4FC08D.svg?logo=vue.js&logoColor=white)](https://vuejs.org/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)

<p align="center">
  <b>聚合关注大V实时发帖</b> · <b>AI 大模型观点提炼</b> · <b>精美邮件与微信即时推送</b> · <b>移动端 PWA 适配</b>
</p>

</div>

---

## 📖 项目简介

在股票和基金投资中，关注多位知名大V与专业投研博主是很多投资者的日常。然而，频繁刷信息流极度浪费精力，碎片化的长文与杂乱的评论更让人抓不住重点。

**智投内参** 是一套全自动的个人投研信息管家：
1. **自动聚合跟踪**：后台自动化轮询雪球博主与组合调仓动态，去粗取精。
2. **AI 投研摘要**：每天固定时间调用大模型（DeepSeek / Qwen / GPT / Claude），自动提炼各博主的核心观点、看多看空标的与异动预警。
3. **多端触达**：生成排版精美的 HTML 晨报/晚报直达邮箱，支持即时调仓预警，更提供独立微信群聊大牛发言监听网关。
4. **现代化前台**：支持移动端 PWA（可直接添加到手机桌面像 App 一样使用）、暗黑模式、富文本动态信息流。

---

## ✨ 核心特性

- 📱 **动态信息流**：
  - 自动渲染雪球表情、个股标签 `$代码$`、原帖引用转发块（`blockquote`）与配图。
  - 智能去除短动态冗余标题，保证浏览高密度与清爽感。
  - 支持按大V姓名或动态关键字极速搜索过滤。
- 🤖 **AI 投研内参**：
  - 支持接入任意兼容 OpenAI 协议的大模型（如 DeepSeek-V3, Qwen-Max, GPT-4o-mini 等）。
  - 按博主自动归纳核心观点、提及股票、关键逻辑与买卖态度。
  - 提供投研历史回溯与时间线索引。
- 💬 **AI 投研智能问答 (RAG)**：
  - 针对所关注大V的历史所有发帖进行自然语言语义提问（例如：“*老木匠最近对紫金矿业怎么看？*”）。
- 📈 **异动与信号检测**：
  - 规则引擎智能识别发帖中的调仓信号（“买入”、“减仓”、“清仓”、“跌停”等），打上醒目标签并支持优先触发预警。
- 📨 **全渠道推送网络**：
  - **邮件推送**：移动端自适应的高颜值 HTML 投研日报。
  - **微信网关**：内置独立微信监听脚本，精准抓取 VIP 交流群中特定技术大牛的发言并接入系统。
- 🛠️ **多用户与权限隔离**：
  - 支持多用户独立管理关注列表与自定义发送时间，内置管理员权限管控。

---

## 🏗️ 架构与技术栈

```text
[雪球公开动态] ----> [内置 RSSHub] ----> [xueqiusub Worker (抓取 & 调度)]
                                                |
                                          (写入 SQLite)
                                                |
[用户浏览器/PWA] <--- [Vue 3 SPA] <--- [xueqiusub Web (FastAPI 接口)]
                                                |
                                     [LLM API] (AI 提炼总结)
                                                |
                                      [SMTP 邮件 / 微信网关]
```

- **后端**：Python 3.12 + FastAPI + SQLAlchemy + SQLite (WAL 模式) + Uvicorn
- **抓取调度**：内置专用 RSSHub 实例 + 定时多线程 Worker
- **前端**：Vue 3 + Vite + Element Plus + Pinia + Vue Router + PWA
- **容器化**：Docker & Docker Compose 一键编排

---

## 🚀 极速部署 (Docker Compose)

只需简单 3 步即可在本地或云服务器启动全套服务：

### 1. 克隆代码仓库
```bash
git clone https://github.com/<your-username>/xueqiusub.git
cd xueqiusub
```

### 2. 配置环境变量
```bash
# 复制配置模板
cp .env.example .env

# 编辑配置文件，填入你的配置（如管理员密码、大模型 API Key、发件邮箱等）
vim .env
```

### 3. 一键启动
```bash
docker compose up -d --build
```

启动完成后：
- 访问 **`http://localhost:8100`** 打开智投内参控制台。
- 使用你在 `.env` 中设置的 `ADMIN_EMAIL` 与 `ADMIN_PASSWORD` 登录即可！

---

## ⚙️ 核心配置说明 (`.env`)

| 配置项 | 说明 | 示例值 |
| :--- | :--- | :--- |
| `BASE_URL` | 外部访问地址（用于邮件/通知跳转） | `http://your-server-ip:8100` |
| `SECRET_KEY` | 系统 JWT 与安全签名密钥 | 随机生成的字符串 |
| `ADMIN_EMAIL` | 默认管理员账号 | `admin@example.com` |
| `ADMIN_PASSWORD` | 默认管理员密码 | 自定义强密码 |
| `LLM_ENABLED` | 是否启用 AI 智能分析功能 | `true` |
| `LLM_API_BASE` | 大模型 API 接口地址 | `https://api.openai.com/v1` |
| `LLM_API_KEY` | 大模型 API Key | `sk-xxxxxx` |
| `LLM_MODEL` | 使用的模型标识符 | `gpt-4o-mini` / `deepseek-chat` |
| `SMTP_HOST` | 邮件发信服务器 | `smtp.qq.com` |
| `SMTP_PORT` | 邮件发信端口 | `465` (SSL) 或 `587` |
| `SMTP_USERNAME` | 发信邮箱账号 | `your_email@qq.com` |
| `SMTP_PASSWORD` | 邮箱密码或授权码 | 你的客户端专用密码 |
| `DIGEST_HOUR` | 每日投研内参推送时刻（24小时制） | `21`（每晚 21:00） |

---

## 📱 移动端使用 (PWA)

系统针对手机移动端做了深度优化：
1. 在手机浏览器（如 Safari、Chrome、Edge）中打开系统主页并登录。
2. 点击浏览器的 **“分享”** $\rightarrow$ **“添加到主屏幕”**。
3. 桌面上将生成 **“智投内参”** 专属图标，打开即享全屏 App 原生体验，底部带有常用信息流导航栏。

---

## ⚠️ 免责声明 (Disclaimer)

1. **非投资建议**：本项目提供的所有发帖聚合、AI 观点提炼、异动分析仅供个人技术研究、量化数据处理参考与学习交流使用，**不构成任何明示或暗示的股票、基金买卖推荐或投资建议**。
2. **风险自负**：证券市场有风险，投资需谨慎。用户依据本系统任何信息所作出的任何投资决策与操作，盈亏均由使用者自行承担。
3. **合规使用**：请严格遵守相关平台的数据使用规范与网络服务条款，合理控制抓取频率，不得将本项目用于商业牟利或恶意爬取。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
