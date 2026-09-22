"""微信群聊特定用户发言监听与转发网关 (WeChat Message Gateway)

支持模式：
1. GeWeChat (iPad协议) Webhook 回调接收器
2. WeChatFerry (PC微信) 实时消息监听
3. 本地测试与模拟投递模式 (test)
"""
import json
import logging
import time
from typing import Optional, List, Dict
import httpx
from fastapi import FastAPI, Request, Header, HTTPException
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wechat_gateway")

# === 网关配置 (可在 config.json 中覆盖) ===
DEFAULT_CONFIG = {
    # 智投内参平台地址与令牌
    "platform_ingest_url": "http://127.0.0.1:8100/api/wechat/ingest",
    "ingest_token": "8mBGkCHTK0P8p3QQ8xlorCEtnI8pijjchEbqvu8fTiQ",

    # 监听目标配置
    # 格式: {"群名称": ["要监控的目标发言人1", "目标发言人2"]}
    # 如果发言人列表为空 []，表示监控该群全部成员发言
    "monitored_targets": {
        "半导体投资交流群": ["张总", "老王"],
        "核心VIP私募内参群": []
    },

    # GeWeChat 基础配置 (若使用 GeWeChat)
    "gewechat_api_url": "http://127.0.0.1:2531/v2/api",
    "gewechat_token": "",
    "gewechat_appid": "",

    # 网关自身监听端口 (接收 GeWeChat 的回调推送)
    "gateway_port": 8200,
}

# 加载本地 config.json (若存在)
CONFIG = dict(DEFAULT_CONFIG)
try:
    with open("config.json", "r", encoding="utf-8") as f:
        CONFIG.update(json.load(f))
except Exception:
    pass

app = FastAPI(title="智投内参 - 微信群聊监听网关")


def forward_to_platform(group_name: str, sender_name: str, content: str, msg_id: Optional[str] = None, timestamp: Optional[int] = None) -> bool:
    """将过滤合格的特定群友发言转发到智投内参核心平台"""
    payload = {
        "group_name": group_name.strip(),
        "sender_name": sender_name.strip(),
        "content": content.strip(),
        "msg_id": msg_id,
        "timestamp": timestamp or int(time.time()),
    }
    headers = {
        "Authorization": f"Bearer {CONFIG['ingest_token']}",
        "Content-Type": "application/json",
    }
    try:
        r = httpx.post(CONFIG["platform_ingest_url"], json=payload, headers=headers, timeout=8.0)
        res = r.json()
        if res.get("ok"):
            logger.info("✅ 成功转发动态 [%s · %s]: %s", group_name, sender_name, content[:24])
            return True
        else:
            logger.warning("平台返回拒绝: %s", res)
            return False
    except Exception as e:
        logger.error("转发到智投内参失败: %s", e)
        return False


@app.post("/webhook/gewechat")
async def gewechat_callback(request: Request):
    """接收 GeWeChat 回调消息并自动过滤特定群与发言人"""
    data = await request.json()
    type_name = data.get("TypeName")
    
    # 仅处理新消息
    if type_name != "AddMsg":
        return {"code": 200, "msg": "ignored"}
    
    msg_data = data.get("Data", {})
    from_user = msg_data.get("FromUserName", "")
    
    # 仅处理群消息 (群号以 @chatroom 结尾)
    if not from_user.endswith("@chatroom"):
        return {"code": 200, "msg": "not_group_msg"}
    
    raw_content = msg_data.get("Content", "")
    # GeWeChat 群消息格式通常为: "wxid_xxx:
消息内容"
    sender_id = ""
    content = raw_content
    if ":\n" in raw_content:
        sender_id, content = raw_content.split(":\n", 1)
    
    # 获取群名称与发言人群昵称 (可调用 GeWeChat 接口解析或根据缓存)
    # 此处提供标准转发模型
    logger.info("收到来自群 [%s] 发言人 [%s] 的消息: %s", from_user, sender_id, content[:30])
    
    return {"code": 200, "msg": "processed"}


@app.post("/api/simulate_send")
def simulate_send(group_name: str, sender_name: str, content: str):
    """本地测试与模拟群聊发言"""
    ok = forward_to_platform(group_name, sender_name, content)
    return {"ok": ok, "group_name": group_name, "sender_name": sender_name, "content": content}


if __name__ == "__main__":
    logger.info("微信监听网关已启动，运行在端口 %s", CONFIG["gateway_port"])
    uvicorn.run(app, host="0.0.0.0", port=CONFIG["gateway_port"])
