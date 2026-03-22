# File: outreach_engine/core/sms_api.py

class SMSAPI:
    def send(self, phone: str, message: str) -> bool:
        """
        Placeholder for SMS sending.
        Replace with real SMS provider code.
        """
        print(f"📩 SMS sent to {phone}: {message}")
        return True  # Simulate success

sms_api = SMSAPI()