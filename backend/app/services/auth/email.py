"""
services/auth/email.py — 邮件发送（SMTP）
"""
from __future__ import annotations

import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_EMAIL, SITE_URL
from app.core.logging import get_logger
from app.services.auth.tokens import create_email_token

logger = get_logger(__name__)


def send_email(to: str, subject: str, html_body: str) -> bool:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        plain = re.sub(r'<[^>]+>', '', html_body)
        logger.info("EMAIL DEV preview | to=%s | subject=%s | body=%s", to, subject, plain[:500])
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = FROM_EMAIL
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo(); srv.starttls(); srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(FROM_EMAIL, [to], msg.as_string())
        logger.info("email sent | to=%s | subject=%s", to, subject)
        return True
    except Exception as e:
        logger.exception("email send failed | to=%s | subject=%s | error=%s", to, subject, e)
        return False


def send_verification_email(user_id: int, email: str, name: str) -> bool:
    token = create_email_token(user_id, "verify_email")
    url = f"{SITE_URL}/api/auth/verify-email/{token}"
    html = (f'<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px 24px">'
            f'<p style="font-size:20px;font-weight:900;color:#ff8f2a">VILTROX Creator Platform</p>'
            f'<h2 style="font-size:22px;font-weight:800">Verify your email</h2>'
            f'<p style="color:#788190;font-size:14px">Hi {name}, verify your email to activate your account.</p>'
            f'<a href="{url}" style="display:inline-block;padding:13px 28px;background:#ff8f2a;'
            f'color:#fff;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Verify Email</a>'
            f'<p style="color:#aaa;font-size:12px;margin-top:24px">Link expires in 7 days.</p></div>')
    return send_email(email, "Verify your Viltrox Creator account", html)


def send_password_reset_email(user_id: int, email: str, name: str) -> bool:
    token = create_email_token(user_id, "reset_password")
    url = f"{SITE_URL}?reset_token={token}"
    html = (f'<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px 24px">'
            f'<p style="font-size:20px;font-weight:900;color:#ff8f2a">VILTROX Creator Platform</p>'
            f'<h2 style="font-size:22px;font-weight:800">Reset your password</h2>'
            f'<p style="color:#788190;font-size:14px">Hi {name}, click below to reset your password (expires 1 hour).</p>'
            f'<a href="{url}" style="display:inline-block;padding:13px 28px;background:#1a1d23;'
            f'color:#fff;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Reset Password</a>'
            f'</div>')
    return send_email(email, "Reset your Viltrox password", html)
