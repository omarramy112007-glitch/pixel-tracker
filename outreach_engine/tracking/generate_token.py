# outreach_engine/tracking/generate_token.py

from google_auth_oauthlib.flow import InstalledAppFlow
import pickle

# الصلاحيات المطلوبة
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify"
]

# المسار لملف credentials من Google Cloud
flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)

# يفتح المتصفح لتسجيل دخول Gmail
creds = flow.run_local_server(port=0)

# حفظ الـ token في ملف pickle
with open("token.pkl", "wb") as f:
    pickle.dump(creds, f)

print("✅ token.pkl created successfully!")