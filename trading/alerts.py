"""Email alerts for trading risk events (halts, kill switch, account failure)."""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from config.logging_config import get_logger

logger = get_logger(__name__)


def send_alert(subject: str, body: str) -> bool:
    """Best-effort email alert. Never raises — a notification failure must
    not block the trading cycle that triggered it. No-ops (logs only) if
    ALERT_EMAIL_TO / SMTP_USER / SMTP_PASSWORD aren't all configured."""
    from config.loader import get_settings
    cfg = get_settings()

    if not (cfg.alert_email_to and cfg.smtp_user and cfg.smtp_password):
        logger.info("alerts: not configured — skipping %r", subject)
        return False

    msg = MIMEText(body)
    msg["Subject"] = f"[Raf3nd] {subject}"
    msg["From"] = cfg.smtp_user
    msg["To"] = cfg.alert_email_to

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.sendmail(cfg.smtp_user, [cfg.alert_email_to], msg.as_string())
        logger.info("alerts: sent %r to %s", subject, cfg.alert_email_to)
        return True
    except Exception as exc:
        logger.error("alerts: send failed — %s", exc)
        return False
