<template>
  <div class="help-view">
    <div class="page-head">
      <h2 class="page-title">使用说明与系统指南</h2>
      <p class="page-desc">详细了解如何订阅全网多平台博主、配置关键词降噪、AI 智能投研模型与多渠道群机器人推送</p>
    </div>

    <div class="help-content">
      <!-- Section 1: Multi-Platform -->
      <el-card shadow="never" class="guide-card">
        <template #header>
          <div class="card-title">
            <span class="step-num">1</span>
            <h3>如何订阅全网多平台博主与快讯源</h3>
            <el-tag size="small" type="info">支持 9+ 平台</el-tag>
          </div>
        </template>
        <div class="guide-body">
          <div class="guide-item">
            <h4>❄️ 雪球投资博主</h4>
            <p>在浏览器中打开博主的雪球个人主页，浏览器地址栏中的纯数字即为其雪球 UID。例如：</p>
            <code>https://xueqiu.com/u/3058599833</code> ➜ 提取 UID <code>3058599833</code>
          </div>

          <div class="guide-item">
            <h4>📦 雪球组合调仓</h4>
            <p>订阅雪球实盘组合调仓信号，平台选择「雪球组合调仓」，输入组合代码（如 <code>ZH123456</code>）。系统将在主理人调仓时秒级推送。</p>
          </div>

          <div class="guide-item">
            <h4>🔴 微博财经大V</h4>
            <p>平台选择「微博财经博主」，打开博主的微博个人主页，提取地址栏中的 UID（如任泽平、但斌等）：</p>
            <code>https://weibo.com/u/1111681197</code> ➜ 提取 UID <code>1111681197</code>
          </div>

          <div class="guide-item">
            <h4>⚡ 公共快讯频道 (免填 UID，一键秒级订阅)</h4>
            <p>支持 <strong>财联社 7×24 快讯</strong>、<strong>华尔街见闻全球快讯</strong>、<strong>格隆汇 7×24 财经</strong>、<strong>36氪实时快讯</strong> 与 <strong>集思录精选广场</strong>。在添加订阅弹窗选择对应平台，系统会自动填入预设标识与名称，直接点击确认即可添加。</p>
          </div>

          <div class="guide-item">
            <h4>🌐 自定义公开 RSS 源</h4>
            <p>选择「自定义 RSS 订阅源」，直接粘贴任意公开的 RSS 订阅链接（如券商策略专栏、微信公众号 RSS、Substack 研报等），系统将自动按周期抓取最新文章。</p>
          </div>
        </div>
      </el-card>

      <!-- Section 2: Filtering -->
      <el-card shadow="never" class="guide-card">
        <template #header>
          <div class="card-title">
            <span class="step-num">2</span>
            <h3>关键词降噪过滤与异动告警规则</h3>
            <el-tag size="small" type="success">智能降噪</el-tag>
          </div>
        </template>
        <div class="guide-body">
          <p class="intro-p">在订阅看板中点击大V右侧的「规则配置」，可对该大V定制专属过滤引擎：</p>
          <ul>
            <li><strong>包含关键词（白名单）</strong>：配置后，该博主只有正文中出现这些词的动态才会被推送（多个词用逗号或空格隔开，如 <code>茅台, 加仓, 业绩超预期</code>）。</li>
            <li><strong>屏蔽关键词（黑名单）</strong>：若博主正文中包含这些词，自动拦截不推送（如 <code>广告, 抽奖, 互粉, 推广</code>）。</li>
            <li><strong>高危异动告警词</strong>：命中这些词时（如 <code>暴跌, 闪崩, 违约, 清仓</code>），系统将以高优先级即刻推送到群或邮件。</li>
            <li><strong>正文最低字数门槛</strong>：过滤博主回复纯表情、“收到”等无信息量的超短内容（推荐设为 10~20 字）。</li>
          </ul>
        </div>
      </el-card>

      <!-- Section 3: Webhook -->
      <el-card shadow="never" class="guide-card">
        <template #header>
          <div class="card-title">
            <span class="step-num">3</span>
            <h3>飞书 / 钉钉 / 企业微信群机器人推送配置</h3>
            <el-tag size="small" type="warning">即时秒推</el-tag>
          </div>
        </template>
        <div class="guide-body">
          <div class="guide-item">
            <h4>1. 飞书自定义机器人</h4>
            <p>在飞书群右上角「设置」➜「群机器人」➜「添加自定义机器人」，复制 Webhook 地址。如开启了安全设置中的「签名校验」，将获取到的 Secret 填入加签密钥中。</p>
          </div>
          <div class="guide-item">
            <h4>2. 钉钉自定义机器人</h4>
            <p>在钉钉群「群设置」➜「智能群助手」➜「添加机器人」➜「自定义 (通过 Webhook 接入)」，安全设置推荐勾选「加签」，并复制加签密钥填入系统的 Secret 框。</p>
          </div>
          <div class="guide-item">
            <h4>3. 企业微信群机器人</h4>
            <p>在企业微信群中右键添加「群机器人」，复制 Webhook 链接粘贴至设置中即可使用。</p>
          </div>
        </div>
      </el-card>

      <!-- Section 4: SMTP -->
      <el-card shadow="never" class="guide-card">
        <template #header>
          <div class="card-title">
            <span class="step-num">4</span>
            <h3>发信邮箱授权码与防垃圾邮件说明</h3>
            <el-tag size="small" type="danger">邮箱投递</el-tag>
          </div>
        </template>
        <div class="guide-body">
          <ul>
            <li><strong>授权码而非登录密码</strong>：QQ 邮箱、163 网易邮箱等服务商在第三方发信时，必须前往邮箱网页版「设置」➜「账户」中开启 POP3/SMTP 服务，并单独生成 <strong>16 位授权码</strong> 填入密码栏。</li>
            <li><strong>推荐端口与加密</strong>：QQ/163/Gmail 推荐使用 <code>465</code> 端口并勾选 <strong>SSL 加密</strong>；Outlook 推荐使用 <code>587</code> 端口并勾选 <strong>STARTTLS</strong>。</li>
            <li><strong>防止进入垃圾箱</strong>：首次收到系统投递的每日精选或即刻推送时，建议在邮箱中将发件人添加至「白名单」或标记为「不是垃圾邮件」。</li>
          </ul>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.help-view {
  max-width: 900px;
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
.help-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.guide-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.step-num {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #0284c7;
  color: #ffffff;
  font-weight: 700;
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.card-title h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
  flex: 1;
}
.guide-body {
  font-size: 14px;
  color: #334155;
  line-height: 1.6;
}
.guide-item {
  margin-bottom: 16px;
}
.guide-item:last-child {
  margin-bottom: 0;
}
.guide-item h4 {
  margin: 0 0 4px;
  font-size: 14.5px;
  color: #0f172a;
}
.guide-item p {
  margin: 0 0 6px;
  font-size: 13px;
  color: #64748b;
}
code {
  background: #f1f5f9;
  color: #0284c7;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12.5px;
}
.intro-p {
  margin: 0 0 10px;
  color: #475569;
}
ul {
  padding-left: 20px;
  margin: 0;
}
li {
  margin-bottom: 8px;
  font-size: 13px;
  color: #475569;
}
</style>
