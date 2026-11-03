"""Update status.json on the prop-builds branch after a run (read by the site, so it holds nothing secret):
    python run_status.py <status.json>
- hf: last known Hugging Face state per engine (last ok, last "quota used up", seconds left / retry time when the
  quota message gave them), from the lines hf_status.py wrote to $HF_STATUS.
- failed: the last 30 failed builds with their request (so the site can rebuild them); a later success of the
  same name or request file removes the entry.
Prints "changed" when the file was rewritten.
Env: HF_STATUS, JOB_STATUS, NAME, MODE, HEIGHT, ENGINE, IMAGE, REQUEST_FILE, PROMPT, GITHUB_SHA, GITHUB_EVENT_NAME, RUN_URL
"""
import datetime
import json
import os
import sys

path = sys.argv[1]
env = os.environ.get
try:
    with open(path) as f:
        status = json.load(f)
    if not isinstance(status, dict):
        raise ValueError
except (OSError, ValueError):
    status = {}
before = json.dumps(status, sort_keys=True)
hf = status.setdefault("hf", {})
failed = status.setdefault("failed", [])


def at(time, seconds):
    t = datetime.datetime.strptime(time, "%Y-%m-%dT%H:%M:%SZ") + datetime.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


lines = []
if env("HF_STATUS") and os.path.exists(env("HF_STATUS")):
    for raw in open(env("HF_STATUS")):
        try:
            lines.append(json.loads(raw))
        except ValueError:
            pass
for line in lines:
    e = hf.setdefault(str(line.get("engine", "?")), {})
    t = line.get("time")
    e["checked"] = t
    if line.get("ok"):
        e["last_ok"] = t
    elif line.get("quota"):
        e["last_quota"] = t
        if "left_s" in line:
            e["left_s"], e["requested_s"], e["left_at"] = line["left_s"], line.get("requested_s"), t
        if "retry_s" in line:
            e["retry_at"] = at(t, line["retry_s"])
    else:
        e["last_error"] = t

name, source = env("NAME", ""), env("REQUEST_FILE", "")
job = env("JOB_STATUS", "")
same = lambda f: (name and f.get("name") == name) or (source and f.get("source") == source)
if job == "success":
    failed[:] = [f for f in failed if not same(f)]
elif job == "failure" and name:
    entry = {"name": name, "request": env("GITHUB_SHA", ""), "source": source, "mode": env("MODE", "ai"),
             "height": env("HEIGHT", "0"), "engine": env("ENGINE", ""), "image": env("IMAGE", ""),
             "event": env("GITHUB_EVENT_NAME", ""), "run": env("RUN_URL", ""),
             "reason": "quota" if any(not l.get("ok") and l.get("quota") for l in lines) else "error",
             "time": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    if not source and env("PROMPT"):
        entry["prompt"] = env("PROMPT")[:200]
    failed[:] = [entry] + [f for f in failed if not same(f)][:29]

if json.dumps(status, sort_keys=True) != before:
    status["updated"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("changed")
