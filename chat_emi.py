# -*- coding: utf-8 -*-
"""Chat with Emi via the provider Chat Completions API (no Codex, no tools).
Runs on the PC. Reads the bearer token from Codex config.toml directly.
NEVER prints the token. Writes API responses to chat_emi_out.json (UTF-8).
"""
import json
import os
import re
import urllib.request
import urllib.error

try:
    import tomllib
except ImportError:
    tomllib = None

OUT = r"E:\MyProj\ai-hub\chat_emi_out.json"
CFG = r"C:\Users\HP\.codex\config.toml"
WORKER = r"E:\MyProj\ai-hub\emi_worker.py"
TASK = r"E:\MyProj\ai-hub\emi-tasks\inbox\review-worker-v1.json"

result = {}

# --- 1. Read token + base_url from config.toml (never print token) ---
token = None
base_url = None
try:
    with open(CFG, "rb") as f:
        cfg = tomllib.load(f)
    prov = cfg.get("model_providers", {}).get("OpenAI", {})
    token = prov.get("experimental_bearer_token")
    base_url = prov.get("base_url")
    result["config_parsed"] = True
    result["model_default"] = cfg.get("model")
    result["model_review"] = cfg.get("review_model")
except Exception as e:
    result["config_parsed"] = False
    result["config_error"] = str(e)[:200]
    try:
        with open(CFG, encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        m = re.search(r'experimental_bearer_token\s*=\s*"([^"]+)"', txt)
        if m:
            token = m.group(1)
        m2 = re.search(r'base_url\s*=\s*"([^"]+)"', txt)
        if m2:
            base_url = m2.group(1)
        result["config_fallback"] = True
    except Exception as e2:
        result["config_fallback_error"] = str(e2)[:200]

result["base_url"] = base_url
result["has_token"] = bool(token)
if not token or not base_url:
    result["fatal"] = "no token or base_url"
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("OK")
    raise SystemExit


def chat(model, messages, max_tokens=4000, timeout=180):
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "x-openai-actor-authorization": "local-image-extension",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "status": r.status,
                    "data": json.loads(r.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "data": e.read().decode("utf-8", errors="ignore")[:1000]}
    except Exception as e:
        return {"ok": False, "status": None,
                "data": type(e).__name__ + ": " + str(e)[:500]}


def extract_text(resp):
    try:
        return resp["data"]["choices"][0]["message"]["content"]
    except Exception:
        return None


# --- 2. Test: tiny message, model gpt-6.1-sol, fallback gpt-6-astra ---
test_msgs = [{"role": "user", "content": (
    "Bạn là Emi, cộng sự AI. Trả lời bằng tiếng Việt trong 1 câu ngắn: "
    "bạn đã sẵn sàng làm việc chưa?")}]
test_resp = chat("gpt-6.1-sol", test_msgs, max_tokens=200, timeout=90)
test_model = "gpt-6.1-sol"
if not test_resp["ok"]:
    test_resp2 = chat("gpt-6-astra", test_msgs, max_tokens=200, timeout=90)
    if test_resp2["ok"]:
        test_resp = test_resp2
        test_model = "gpt-6-astra"
result["test_model"] = test_model
result["test_ok"] = test_resp["ok"]
result["test_status"] = test_resp["status"]
result["test_text"] = extract_text(test_resp) if test_resp["ok"] else None
result["test_error"] = None if test_resp["ok"] else str(test_resp["data"])[:800]

# --- 3. If test OK, send the real review prompt ---
if test_resp["ok"]:
    try:
        with open(WORKER, encoding="utf-8") as f:
            worker_code = f.read()
    except Exception as e:
        worker_code = "(không đọc được emi_worker.py: %s)" % str(e)[:200]
    try:
        with open(TASK, encoding="utf-8") as f:
            task = json.load(f)
        task_body = task.get("body", "") or json.dumps(task, ensure_ascii=False)
    except Exception as e:
        task_body = "(không đọc được task review-worker-v1: %s)" % str(e)[:200]
    prompt = (
        "Bạn là Emi, cộng sự AI ngang hàng (team Astra/Sol). "
        "Nhiệm vụ review (chỉ phân tích, KHÔNG sửa file, KHÔNG chạy lệnh):\n\n"
        + task_body
        + "\n\n--- File emi_worker.py cần review ---\n```python\n"
        + worker_code
        + "\n```\n\nHãy trả lời bằng tiếng Việt, theo đúng yêu cầu trong nhiệm vụ. "
        "Cuối bản review ghi rõ: các lỗi tìm thấy (nếu có) + đề xuất sửa cụ thể."
    )
    rev_resp = chat(test_model, [{"role": "user", "content": prompt}],
                    max_tokens=6000, timeout=300)
    result["review_ok"] = rev_resp["ok"]
    result["review_status"] = rev_resp["status"]
    result["review_text"] = extract_text(rev_resp) if rev_resp["ok"] else None
    result["review_error"] = None if rev_resp["ok"] else str(rev_resp["data"])[:800]

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("OK")
