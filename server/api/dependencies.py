from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from sqlalchemy.orm import Session
from server.core.database import get_db, User
from server.core.security import decode_access_token, decrypt_api_key

security_bearer = HTTPBearer()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_access_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    user_id = payload.get("sub")
    email = payload.get("email")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == int(user_id)).first()

    if not user:
        user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account inactive")

    return user


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return current_user


def get_verified_user(current_user: User = Depends(get_current_active_user)) -> User:
    if not current_user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Email verification required. Please verify your email address before using this feature.",
        )
    return current_user


def get_user_from_api_key(
    api_key: str = Depends(api_key_header), db: Session = Depends(get_db)
) -> User:
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key required")

    users = db.query(User).filter(User.api_key != None).all()

    user = None
    for u in users:
        try:
            if decrypt_api_key(u.api_key) == api_key:
                user = u
                break
        except Exception:
            continue

    if not user:
        raise HTTPException(status_code=403, detail="Invalid API key")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account inactive")

    if not user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Email verification required. Please verify your email address before using the VST plugin.",
        )

    return user
