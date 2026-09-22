import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

from app.config import get_settings


password_hash = PasswordHash.recommended()
settings = get_settings()
serializer = URLSafeTimedSerializer(settings.secret_key)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def make_token(user_id: int, purpose: str) -> str:
    return serializer.dumps({"user_id": user_id, "purpose": purpose}, salt=purpose)


def read_token(token: str, purpose: str, max_age: int | None = None) -> int | None:
    try:
        data = serializer.loads(token, salt=purpose, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("purpose") != purpose:
        return None
    return int(data["user_id"])


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)



import base64
import hashlib
import hmac


def encrypt_secret(plaintext: str, secret_key: str | None = None) -> str:
    """使用 PBKDF2 + HMAC-SHA256 对敏感字符串进行对称加密（无外部依赖）。"""
    if not plaintext:
        return ""
    if plaintext.startswith("enc:"):
        return plaintext

    key = secret_key or get_settings().secret_key
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, 100000, dklen=64)
    enc_key = derived[:32]
    mac_key = derived[32:]

    plain_bytes = plaintext.encode("utf-8")
    keystream = b""
    counter = 0
    while len(keystream) < len(plain_bytes):
        keystream += hmac.new(enc_key, salt + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1

    cipher_bytes = bytes(p ^ k for p, k in zip(plain_bytes, keystream))
    mac = hmac.new(mac_key, salt + cipher_bytes, hashlib.sha256).digest()
    payload = salt + mac + cipher_bytes
    return "enc:" + base64.urlsafe_b64encode(payload).decode("utf-8")


def decrypt_secret(ciphertext: str, secret_key: str | None = None) -> str:
    """解密由 encrypt_secret 加密的密文字符串。若不是 enc: 开头则原样返回。"""
    if not ciphertext or not ciphertext.startswith("enc:"):
        return ciphertext or ""

    try:
        key = secret_key or get_settings().secret_key
        raw = base64.urlsafe_b64decode(ciphertext[4:].encode("utf-8"))
        if len(raw) < 16 + 32:
            return ""
        salt = raw[:16]
        mac = raw[16:48]
        cipher_bytes = raw[48:]

        derived = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, 100000, dklen=64)
        enc_key = derived[:32]
        mac_key = derived[32:]

        expected_mac = hmac.new(mac_key, salt + cipher_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("MAC check failed: key mismatch or corrupted data")

        keystream = b""
        counter = 0
        while len(keystream) < len(cipher_bytes):
            keystream += hmac.new(enc_key, salt + counter.to_bytes(4, "big"), hashlib.sha256).digest()
            counter += 1

        plain_bytes = bytes(c ^ k for c, k in zip(cipher_bytes, keystream))
        return plain_bytes.decode("utf-8")
    except Exception as exc:
        logger = __import__("logging").getLogger("app.security")
        logger.error("解密敏感字段失败: %s", exc)
        return ""
