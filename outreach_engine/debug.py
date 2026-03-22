import sys
print("Python path:", sys.executable)

try:
    import slack_sdk
    print("slack_sdk works ✅")
except Exception as e:
    print("Import error ❌:", e)