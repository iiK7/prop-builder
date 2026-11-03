"""One line per Hugging Face call, appended to $HF_STATUS (run_status.py turns it into status.json for the site).
Only the engine, the time, ok / quota used up and the numbers in a quota message are kept: never the error text,
never the token.
"""
import datetime
import json
import os
import re


def _seconds(m):
    h, mnt, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mnt * 60 + s


def record(engine, ok, error=None):
    path = os.environ.get("HF_STATUS")
    if not path:
        return
    line = {"engine": engine, "ok": bool(ok), "time": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    if not ok:
        msg = str(error or "")
        line["quota"] = "quota" in msg.lower()
        # ZeroGPU: "You have exceeded your GPU quota (60s requested vs. 39s left). Try again in 0:18:32"
        m = re.search(r"(\d+(?:\.\d+)?)\s*s\s+requested\s+vs\.?\s+(\d+(?:\.\d+)?)\s*s\s+left", msg, re.I)
        if m:
            line["requested_s"], line["left_s"] = float(m.group(1)), float(m.group(2))
        m = re.search(r"try again in\s+(?:(\d+):)?(\d{1,2}):(\d{2})\b", msg, re.I)
        if m:
            line["retry_s"] = _seconds(m)
    try:
        with open(path, "a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass
