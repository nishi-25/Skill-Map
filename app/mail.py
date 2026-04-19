"""
メール送信モジュール

SMTP設定の優先順位:
  1. DB (app_settings テーブル) — Admin画面から設定可能
  2. 環境変数 (SMTP_HOST, SMTP_USER, SMTP_PASSWORD 等)
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

logger = logging.getLogger(__name__)

# ─── 環境変数フォールバック ────────────────────────────────────
_ENV_DEFAULTS = {
    "smtp_host": os.environ.get("SMTP_HOST", ""),
    "smtp_port": os.environ.get("SMTP_PORT", "587"),
    "smtp_user": os.environ.get("SMTP_USER", ""),
    "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
    "smtp_from_name": os.environ.get("SMTP_FROM_NAME", "スキルマップ"),
    "app_url": os.environ.get("APP_URL", "http://localhost:8190"),
}


def _get_settings() -> dict:
    """DB から SMTP 設定を読み込み、未設定なら環境変数にフォールバック"""
    settings = dict(_ENV_DEFAULTS)
    try:
        from database import SessionLocal
        import models
        db = SessionLocal()
        try:
            rows = db.query(models.AppSetting).filter(
                models.AppSetting.key.in_([
                    "smtp_host", "smtp_port", "smtp_user",
                    "smtp_password", "smtp_from_name", "app_url",
                ])
            ).all()
            for row in rows:
                if row.value:  # 空文字は環境変数フォールバック
                    settings[row.key] = row.value
        finally:
            db.close()
    except Exception as e:
        logger.debug("DB設定読み込みスキップ: %s", e)
    return settings


def is_mail_configured(settings: dict | None = None) -> bool:
    """SMTP設定が有効かどうか"""
    if settings is None:
        settings = _get_settings()
    return bool(settings.get("smtp_host") and settings.get("smtp_user")
                and settings.get("smtp_password"))


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """汎用メール送信 (HTML)"""
    settings = _get_settings()
    if not is_mail_configured(settings):
        logger.warning("SMTP未設定のためメール送信をスキップ: %s → %s", subject, to_email)
        return False

    smtp_host = settings["smtp_host"]
    smtp_port = int(settings.get("smtp_port", "587"))
    smtp_user = settings["smtp_user"]
    smtp_password = settings["smtp_password"]
    from_name = settings.get("smtp_from_name", "スキルマップ")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((from_name, smtp_user))
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        logger.info("メール送信成功: %s → %s", subject, to_email)
        return True
    except Exception as e:
        logger.error("メール送信失敗: %s → %s : %s", subject, to_email, e)
        return False


# ════════════════════════════════════════════════════════════════
# テンプレート付きメール送信関数
# ════════════════════════════════════════════════════════════════

def _base_html(title: str, body: str) -> str:
    """メール共通HTMLテンプレート"""
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f1f3f9;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:520px;margin:30px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
  <div style="background:linear-gradient(135deg,#6366f1,#4f46e5);padding:24px 30px;">
    <h1 style="margin:0;color:#fff;font-size:18px;">🗺️ スキルマップ</h1>
  </div>
  <div style="padding:30px;">
    <h2 style="margin:0 0 16px;font-size:16px;color:#1e1b4b;">{title}</h2>
    {body}
  </div>
  <div style="padding:16px 30px;background:#f8fafc;text-align:center;font-size:12px;color:#94a3b8;">
    このメールはスキルマップシステムから自動送信されました
  </div>
</div>
</body>
</html>
"""


def send_registration_notice(admin_email: str, admin_name: str,
                              new_username: str, new_display_name: str,
                              new_email: str) -> bool:
    """新規ユーザー登録 → Admin/Managerへ通知"""
    app_url = _get_settings().get("app_url", "http://localhost:8190")
    body = f"""
    <p style="color:#334155;line-height:1.7;">
      新しいユーザーが登録しました。承認をお願いします。
    </p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr>
        <td style="padding:8px 12px;background:#f8fafc;font-weight:600;width:100px;font-size:14px;">ユーザー名</td>
        <td style="padding:8px 12px;font-size:14px;">{new_username}</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;background:#f8fafc;font-weight:600;font-size:14px;">表示名</td>
        <td style="padding:8px 12px;font-size:14px;">{new_display_name}</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;background:#f8fafc;font-weight:600;font-size:14px;">メール</td>
        <td style="padding:8px 12px;font-size:14px;">{new_email or '未登録'}</td>
      </tr>
    </table>
    <div style="text-align:center;margin-top:20px;">
      <a href="{app_url}/admin/users"
         style="display:inline-block;padding:10px 28px;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;text-decoration:none;border-radius:10px;font-weight:600;font-size:14px;">
        ユーザー管理を開く
      </a>
    </div>
    """
    subject = f"[スキルマップ] 新規ユーザー登録: {new_display_name}"
    return _send(admin_email, subject, _base_html("新規ユーザー登録通知", body))


def send_approval_notice(user_email: str, display_name: str) -> bool:
    """承認完了 → ユーザーへ通知"""
    app_url = _get_settings().get("app_url", "http://localhost:8190")
    body = f"""
    <p style="color:#334155;line-height:1.7;">
      <strong>{display_name}</strong> さん、こんにちは。
    </p>
    <p style="color:#334155;line-height:1.7;">
      アカウントが承認されました！🎉<br>
      スキルマップにログインして、スキルの申告を始めましょう。
    </p>
    <div style="text-align:center;margin-top:24px;">
      <a href="{app_url}/login"
         style="display:inline-block;padding:12px 32px;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;text-decoration:none;border-radius:10px;font-weight:600;font-size:15px;">
        ログインする
      </a>
    </div>
    """
    subject = "[スキルマップ] アカウントが承認されました"
    return _send(user_email, subject, _base_html("アカウント承認完了", body))


def send_test_mail(to_email: str) -> tuple[bool, str]:
    """テストメール送信。(成功フラグ, メッセージ) を返す"""
    settings = _get_settings()
    if not is_mail_configured(settings):
        return False, "SMTP設定が不完全です。ホスト・ユーザー・パスワードを全て入力してください。"

    body = """
    <p style="color:#334155;line-height:1.7;">このメールはテスト送信です。</p>
    <p style="color:#334155;line-height:1.7;">このメールが届いていれば、SMTP設定は正しく機能しています。</p>
    """
    try:
        ok = _send(to_email, "[スキルマップ] テストメール", _base_html("テスト送信", body))
        if ok:
            return True, f"{to_email} にテストメールを送信しました。"
        return False, "メール送信に失敗しました。サーバーログを確認してください。"
    except Exception as e:
        return False, f"エラー: {e}"


def send_password_reset_mail(to_email: str, display_name: str, reset_url: str) -> bool:
    """パスワードリセットリンクを送信"""
    body = f"""
    <p style="color:#334155;line-height:1.7;">
      <strong>{display_name}</strong> さん、こんにちは。
    </p>
    <p style="color:#334155;line-height:1.7;">
      パスワードリセットのリクエストを受け付けました。<br>
      下のボタンをクリックして、新しいパスワードを設定してください。
    </p>
    <div style="text-align:center;margin:24px 0;">
      <a href="{reset_url}"
         style="display:inline-block;padding:12px 32px;background:linear-gradient(135deg,#f59e0b,#f97316);color:#fff;text-decoration:none;border-radius:10px;font-weight:600;font-size:15px;">
        パスワードをリセット
      </a>
    </div>
    <p style="color:#94a3b8;font-size:.8rem;line-height:1.6;">
      このリンクは <strong>30分間</strong> 有効です。<br>
      心当たりがない場合は、このメールを無視してください。
    </p>
    """
    subject = "[スキルマップ] パスワードリセット"
    return _send(to_email, subject, _base_html("パスワードリセット", body))
