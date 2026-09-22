# 智投内参 - 微信群聊特定用户监听网关

## 作用
本网关用于挂机监听微信交流群，将**特定大牛群友（或群主）的高价值发言**精准捕获，自动 POST 转发到「智投内参」主平台，实现飞书/钉钉秒级推送与每日 AI 调仓雷达汇总。

## 运行配置
编辑 `config.json`：
```json
{
  "platform_ingest_url": "http://your-server-ip:8100/api/wechat/ingest",
  "ingest_token": "wechat_alpha_token_8888",
  "monitored_targets": {
    "半导体投资交流群": ["张总", "老王"]
  }
}
```

## 测试命令 (模拟发言)
```bash
python3 wechat_listener.py
# 在另一终端发送测试发言
curl -X POST "http://127.0.0.1:8200/api/simulate_send?group_name=半导体投资交流群&sender_name=张总&content=中际旭创三季报超预期，建议回踩逢低建仓"
```
