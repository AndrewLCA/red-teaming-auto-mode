import json
from pathlib import Path

text = Path("monitor_sys_prompt.txt").read_text(encoding="utf-8")
markdown = json.loads(text)

Path("output.md").write_text(markdown, encoding="utf-8")