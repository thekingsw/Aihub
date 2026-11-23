
import json, re, urllib.request
BAT = r"E:\MyProj\ai-hub\start.bat"
OUT = r"E:\MyProj\ai-hub\verify_mb.json"
with open(BAT, encoding="utf-8", errors="ignore") as f:
    txt = f.read()
m = re.search(r"set\s+AI_HUB_INTERNAL_TOKEN=(.+)", txt)
token = m.group(1).strip()
req = urllib.request.Request("http://100.79.204.50:8090/internal/messages?to=anh",
                             headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode("utf-8"))
msgs = data.get("messages", data if isinstance(data, list) else [])
msgs = sorted(msgs, key=lambda x: x.get("created_at", ""))
last = msgs[-1] if msgs else {}
out = {"count_to_anh": len(msgs),
       "last_id": last.get("id"),
       "last_from": last.get("from"),
       "last_created": last.get("created_at"),
       "last_head": (last.get("body", "") or "")[:120]}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("OK")
