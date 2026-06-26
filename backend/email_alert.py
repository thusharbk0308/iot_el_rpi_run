"""
Async email alert module for FaceGuard Security System.
Uses Python stdlib smtplib — no extra pip install required.
All sends are dispatched in a background daemon thread so the
recognition pipeline is never blocked.
"""
import os
import socket
import smtplib
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

import config


class EmailAlert:
    def _is_enabled(self):
        return (
            getattr(config, "EMAIL_ALERTS_ENABLED", False) and
            bool(getattr(config, "EMAIL_SENDER", "")) and
            bool(getattr(config, "EMAIL_RECIPIENT", "")) and
            bool(getattr(config, "EMAIL_PASSWORD", ""))
        )

    # ── Public API ─────────────────────────────────────────────
    def send(self, image_path, confidence, timestamp=None):
        """Non-blocking: dispatches email in a background thread."""
        if not self._is_enabled():
            return
        ts = timestamp or datetime.now()
        t = threading.Thread(
            target=self._send_email,
            args=(image_path, confidence, ts),
            daemon=True,
        )
        t.start()

    # ── Internal ───────────────────────────────────────────────
    @staticmethod
    def _get_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "N/A"

    @staticmethod
    def _get_cpu_temp():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return f"{int(f.read()) / 1000:.1f}°C"
        except Exception:
            return "N/A"

    def _send_email(self, image_path, confidence, timestamp):
        try:
            if isinstance(timestamp, str):
                ts_str = timestamp
            else:
                ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

            date_str = ts_str[:10]
            time_str = ts_str[11:]
            ip       = self._get_ip()
            temp     = self._get_cpu_temp()

            msg              = MIMEMultipart("related")
            msg["Subject"]   = "Security Alert - Unknown Person Detected"
            msg["From"]      = config.EMAIL_SENDER
            msg["To"]        = config.EMAIL_RECIPIENT

            html_body = f"""
<html>
<body style="font-family:Arial,sans-serif;background:#0f172a;color:#f8fafc;padding:20px;margin:0;">
  <div style="max-width:580px;margin:auto;background:#1e293b;border-radius:12px;
              padding:28px;border:1px solid #334155;">
    <h2 style="color:#ef4444;margin-top:0;">&#128680; Security Alert</h2>
    <p style="color:#94a3b8;margin-top:0;">
      An <strong>unauthorized person</strong> was detected by your
      Raspberry Pi Smart Door Lock.
    </p>
    <hr style="border:none;border-top:1px solid #334155;margin:20px 0;">
    <table style="width:100%;border-collapse:collapse;">
      <tr style="border-bottom:1px solid #334155;">
        <td style="padding:10px 8px;color:#94a3b8;width:40%;">Date</td>
        <td style="padding:10px 8px;color:#f8fafc;font-weight:500;">{date_str}</td>
      </tr>
      <tr style="border-bottom:1px solid #334155;">
        <td style="padding:10px 8px;color:#94a3b8;">Time</td>
        <td style="padding:10px 8px;color:#f8fafc;font-weight:500;">{time_str}</td>
      </tr>
      <tr style="border-bottom:1px solid #334155;">
        <td style="padding:10px 8px;color:#94a3b8;">Confidence</td>
        <td style="padding:10px 8px;color:#f8fafc;font-weight:500;">{confidence:.2f}%</td>
      </tr>
      <tr style="border-bottom:1px solid #334155;">
        <td style="padding:10px 8px;color:#94a3b8;">Door Status</td>
        <td style="padding:10px 8px;color:#ef4444;font-weight:600;">LOCKED</td>
      </tr>
      <tr style="border-bottom:1px solid #334155;">
        <td style="padding:10px 8px;color:#94a3b8;">Device IP</td>
        <td style="padding:10px 8px;color:#f8fafc;font-weight:500;">{ip}</td>
      </tr>
      <tr>
        <td style="padding:10px 8px;color:#94a3b8;">CPU Temperature</td>
        <td style="padding:10px 8px;color:#f8fafc;font-weight:500;">{temp}</td>
      </tr>
    </table>
    <hr style="border:none;border-top:1px solid #334155;margin:20px 0;">
    <p style="color:#94a3b8;font-size:12px;margin:0;">
      Snapshot attached below. &nbsp;|&nbsp;
      Dashboard: <a href="http://{ip}:5000" style="color:#3b82f6;">http://{ip}:5000</a>
    </p>
  </div>
</body>
</html>"""

            alt_part = MIMEMultipart("alternative")
            msg.attach(alt_part)
            alt_part.attach(MIMEText(html_body, "html"))

            # Attach snapshot image
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as f:
                    img = MIMEImage(f.read())
                    img.add_header(
                        "Content-Disposition",
                        "attachment",
                        filename=os.path.basename(image_path),
                    )
                    msg.attach(img)

            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
                server.send_message(msg)

            print(f"[EMAIL] Alert sent to {config.EMAIL_RECIPIENT}")

        except Exception as e:
            print(f"[EMAIL ERROR] Failed to send alert: {e}")
