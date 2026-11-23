# -*- coding: utf-8 -*-
"""Test calling Sol via Chat Completions API (no Codex, no tools).
Runs on the PC. Reads bearer token from Codex config.toml (never prints it).
Writes result to test_sol_chat_out.json (UTF-8).
"""
import json
import re
import urllib.request
import urllib.error

try:
    import tomllib
except ImportError:
    tomllib = None

OUT = r"E:\MyProj\ai-hub\test_sol_chat_out.json"
CFG = r"C:\Users\HP\.codex\config.toml"

result = {}
token = None
base_url = None
try:
    with open(CFG, "rb") as f:
        cfg = tomllib.load(f)
    prov = cfg.get("model_providers", {}).get("OpenAI", {})
    token = prov.get("experimental_bearer_token")
    base_url = prov.get("base_url")
    result["config_parsed"] = True
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


def chat(model, messages, max_tokens=300, timeout=90):
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


msgs = [{"role": "user", "content": (
    "Bạn là Sol, trả lời bằng tiếng Việt trong 1 câu ngắn: bạn là model nào?")}]
results = {}
for model in ("gpt-6.1-sol", "gpt-6-astra"):
    resp = chat(model, msgs, max_tokens=200, timeout=90)
    results[model] = {
        "ok": resp["ok"],
        "status": resp["status"],
        "text": extract_text(resp) if resp["ok"] else None,
        "error": None if resp["ok"] else str(resp["data"])[:800],
    }
result["models"] = results

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("OK")
