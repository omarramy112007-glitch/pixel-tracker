# File: outreach_engine/analytics/send_time_predictor.py

from datetime import datetime, timedelta
import pytz

# يمكن هنا تستخدم ML model لاحقاً، الآن مثال بسيط بناءً على بيانات سابقة
def predict_best_send_time(lead: dict) -> datetime:
    """
    Returns best UTC datetime to send next email
    based on lead timezone and engagement patterns.
    """

    tz_str = lead.get("timezone", "UTC")
    tz = pytz.timezone(tz_str)

    # مثال: أفضل وقت بين 10am - 2pm حسب البيانات السابقة
    now_utc = datetime.utcnow()
    now_local = now_utc.astimezone(tz)

    # إذا تاريخ آخر email موجود نحسب فرق ديناميكي
    last_sent = lead.get("last_email_sent_at")
    if last_sent:
        last_sent_local = last_sent.astimezone(tz)
        # dynamic delay بناءً على engagement
        delay_hours = 24 if lead.get("email_opened") else 48  # لو فتحها، أسرع، لو لا، أطول
        next_send_local = last_sent_local + timedelta(hours=delay_hours)
    else:
        next_send_local = now_local  # أول إرسال

    # اجعل التوقيت ضمن ساعات العمل 9am-5pm local
    send_hour = min(max(next_send_local.hour, 9), 17)
    next_send_local = next_send_local.replace(hour=send_hour, minute=0, second=0, microsecond=0)

    return next_send_local.astimezone(pytz.UTC)


def predict_reply_probability(lead: dict) -> float:
    """
    Return a score 0-1 for likelihood to reply.
    Example: higher if lead opened/clicked previous emails.
    """

    score = 0.1  # base
    if lead.get("email_opened"):
        score += 0.3
    if lead.get("link_clicked"):
        score += 0.4
    if lead.get("status") == "replied":
        score = 1.0
    return min(score, 1.0)