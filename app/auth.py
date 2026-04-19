import os
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
import models

SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production-skillmap!!")
SESSION_MAX_AGE = 86400 * 7   # 7日

serializer = URLSafeTimedSerializer(SECRET_KEY)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_session_cookie(response, user_id: int):
    token = serializer.dumps(user_id)
    response.set_cookie("session", token, httponly=True, max_age=SESSION_MAX_AGE)


def clear_session_cookie(response):
    response.delete_cookie("session")


def get_current_user(request: Request, db) -> "models.User | None":
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        user_id = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return db.query(models.User).filter(models.User.id == user_id).first()
    except (BadSignature, SignatureExpired):
        return None


def require_login(request: Request, db) -> "models.User":
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_approved(request: Request, db) -> "models.User":
    user = require_login(request, db)
    if not user.is_approved:
        raise HTTPException(status_code=302, headers={"Location": "/pending"})
    return user


def require_admin(request: Request, db) -> "models.User":
    user = require_approved(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    return user


def require_manager_or_admin(request: Request, db) -> "models.User":
    user = require_approved(request, db)
    if user.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Manager以上の権限が必要です")
    return user
