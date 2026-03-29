from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from jose import JWTError, jwt
import secrets
import pyotp
import qrcode
import io
import base64
import bcrypt
from server.config import settings
from cryptography.fernet import Fernet

_cipher = None


def get_cipher():
    global _cipher
    if _cipher is None:
        if not settings.ENCRYPTION_KEY:
            raise ValueError("ENCRYPTION_KEY not set in environment")
        _cipher = Fernet(settings.ENCRYPTION_KEY.encode())
    return _cipher


def encrypt_api_key(api_key: str) -> str:
    cipher = get_cipher()
    encrypted = cipher.encrypt(api_key.encode())
    return encrypted.decode()


def decrypt_api_key(encrypted_key: str) -> str:
    cipher = get_cipher()
    decrypted = cipher.decrypt(encrypted_key.encode())
    return decrypted.decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def generate_api_key() -> str:
    return f"on_{secrets.token_urlsafe(32)}"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str):
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None


def generate_verification_token() -> str:
    return secrets.token_urlsafe(32)


def verify_verification_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None


def generate_2fa_secret() -> str:
    return pyotp.random_base32()


def verify_2fa_token(secret: str, token: str) -> bool:
    if not secret or not token:
        return False
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(token, valid_window=2)
    except Exception as e:
        print(f"2FA verification error: {e}")
        return False


def get_2fa_qr_code(email: str, secret: str) -> str:
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=email, issuer_name="Obsidian Neural")

    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.getvalue()).decode()

    return f"data:image/png;base64,{img_base64}"


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)
