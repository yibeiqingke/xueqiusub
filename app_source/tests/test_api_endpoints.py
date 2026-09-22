from fastapi.testclient import TestClient
from app.main import app
from app.models import User
from app.database import SessionLocal

client = TestClient(app)

def test_spa_root_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "app" in resp.text
    assert "AI 投研内参" in resp.text

def test_api_auth_me_unauthorized():
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "请先登录"

def test_api_dashboard_unauthorized():
    resp = client.get("/api/dashboard/data")
    assert resp.status_code == 401

def test_api_discover_sources_unauthorized():
    resp = client.get("/api/discover/sources")
    assert resp.status_code == 401

def test_api_settings_unauthorized():
    resp = client.get("/api/user/settings")
    assert resp.status_code == 401

def test_authenticated_dashboard_data_and_patch():
    db = SessionLocal()
    user2 = db.query(User).filter_by(id=2).first()
    assert user2 is not None
    
    from app.main import api_dashboard_data, api_discover_sources, api_get_user_settings, api_update_subscription, UpdateSubJsonRequest
    data = api_dashboard_data(user=user2, db=db)
    assert "subscriptions" in data
    assert len(data["subscriptions"]) >= 2
    
    first_sub = data["subscriptions"][0]
    assert "is_enabled" in first_sub
    assert "delivery_mode" in first_sub
    assert "delivery_channel" in first_sub
    assert "ai_summary_enabled" in first_sub
    assert "account" in first_sub
    assert "display_name" in first_sub["account"]
    assert first_sub["account"]["display_name"] in ["洋葱", "买股票的老木匠"]

    disc = api_discover_sources(user=user2, db=db)
    assert "sources" in disc
    assert len(disc["sources"]) >= 10

    cfg = api_get_user_settings(user=user2)
    assert "digest_hour" in cfg
    assert "webhook_enabled" in cfg
    assert "smtp_host" in cfg

    sub_id = first_sub["id"]
    patch_res = api_update_subscription(
        subscription_id=sub_id,
        payload=UpdateSubJsonRequest(is_enabled=True, delivery_mode="immediate"),
        user=user2,
        db=db,
    )
    assert patch_res["ok"] is True

def test_api_admin_unauthorized():
    resp = client.get("/api/admin/data")
    assert resp.status_code in [401, 403]

def test_api_admin_authorized():
    db = SessionLocal()
    admin = db.query(User).filter_by(id=1).first()
    assert admin is not None and admin.is_admin
    from app.main import api_admin_data
    res = api_admin_data(_admin=admin, db=db)
    assert "stats" in res
    assert res["stats"]["users"] >= 2
    assert "users" in res
    assert "accounts" in res
    assert "settings" in res


def test_api_register_validation():
    # 1. 答案错误
    resp1 = client.post(
        "/api/auth/register",
        json={"username": "newuser_test", "password": "password123", "confirm": "password123", "answer": "wrong"},
    )
    assert resp1.status_code == 400
    assert resp1.json()["detail"] == "群名答案不正确"

    # 2. 两次密码不一致
    resp2 = client.post(
        "/api/auth/register",
        json={"username": "newuser_test", "password": "password123", "confirm": "mismatch", "answer": "避难所2026"},
    )
    assert resp2.status_code == 400
    assert resp2.json()["detail"] == "两次输入的密码不一致"

    # 3. 密码太短
    resp3 = client.post(
        "/api/auth/register",
        json={"username": "newuser_test", "password": "123", "confirm": "123", "answer": "避难所2026"},
    )
    assert resp3.status_code == 400
    assert resp3.json()["detail"] == "密码至少 6 位"
