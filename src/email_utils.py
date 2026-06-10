import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

CATEGORY_LABELS = {
    "pricing": "PRICING CHANGE",
    "messaging": "MESSAGING SHIFT",
    "product": "PRODUCT UPDATE",
    "hiring": "HIRING SIGNAL",
    "partnership": "PARTNERSHIP",
    "funding": "FUNDING / M&A",
    "leadership": "LEADERSHIP CHANGE",
    "customer": "NEW CUSTOMER",
    "threat_intel": "THREAT INTEL",
    "community": "COMMUNITY MENTION",
    "review": "REVIEW ACTIVITY",
    "competitive_positioning": "COMPETITIVE POSITIONING",
    "press": "PRESS / ANALYST",
}


def send_smtp(subject: str, body: str, mime_type: str = "plain"):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    alert_email = os.environ.get("ALERT_EMAIL")

    if not all([smtp_user, smtp_password, alert_email]):
        raise ValueError("SMTP_USER, SMTP_PASSWORD, and ALERT_EMAIL must be set")

    subtype = "alternative" if mime_type == "html" else "mixed"
    msg = MIMEMultipart(subtype)
    msg["From"] = smtp_user
    msg["To"] = alert_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, mime_type))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, alert_email, msg.as_string())

    return alert_email
