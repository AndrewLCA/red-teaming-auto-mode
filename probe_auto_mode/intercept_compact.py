from mitmproxy import http
import json

MARKER = "This session is being continued from a previous conversation"

def request(flow: http.HTTPFlow):
    if "/v1/messages" not in flow.request.path:
        return

    try:
        body = json.loads(flow.request.get_text())
    except (ValueError, UnicodeDecodeError):
        return

    messages = body.get("messages", [])

    if any(
        MARKER in str(msg.get("content", ""))
        for msg in messages
    ):
        print("\nPOST-COMPACTION REQUEST DETECTED")
        print("Pausing request for editing...")
        flow.intercept()