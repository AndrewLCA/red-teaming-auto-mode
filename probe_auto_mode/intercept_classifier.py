import json
from mitmproxy import http

CLASSIFIER_BETA = "auto-mode-classifier-2026-07-16"

def request(flow: http.HTTPFlow):
    if flow.request.method != "POST":
        return

    if flow.request.path.split("?")[0] != "/v1/messages":
        return

    beta = flow.request.headers.get("anthropic-beta", "")

    if CLASSIFIER_BETA not in [
        item.strip() for item in beta.split(",")
    ]:
        return

    try:
        body = json.loads(flow.request.get_text())
    except (ValueError, UnicodeDecodeError):
        return

    messages = body.get("messages", [])

    is_severity_request = (
        "</severity>" in body.get("stop_sequences", [])
        and any(
            "<transcript>" in block.get("text", "")
            for msg in messages
            for block in msg.get("content", [])
            if isinstance(block, dict)
        )
    )

    print(
        "[AUTO MODE CLASSIFIER]",
        flow.request.pretty_url,
        "model:", body.get("model"),
        "stage1:", is_severity_request,
    )