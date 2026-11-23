# -*- coding: utf-8 -*-
"""Test Codex + Sol after switching wire_api to 'chat'.
Runs on the PC. Writes result to test_codex_chat_out.json.
"""
import json
import subprocess
import os

OUT = r"E:\MyProj\ai-hub\test_codex_chat_out.json"
WORKDIR = r"E:\MyProj\ai-hub"


def run_codex(prompt, timeout=150):
    cmd = ["codex", "exec", "--skip-git-repo-check",
           "--sandbox", "workspace-write",
           "-c", "model=gpt-6.1-sol"]
    try:
        p = subprocess.run(cmd, input=prompt.encode("utf-8"),
                           capture_output=True, timeout=timeout, cwd=WORKDIR)
        return {
            "rc": p.returncode,
            "stdout": p.stdout.decode("utf-8", errors="ignore")[:3000],
            "stderr": p.stderr.decode("utf-8", errors="ignore")[:2000],
        }
    except subprocess.TimeoutExpired as e:
        return {
            "rc": None,
            "stdout": (e.stdout or b"").decode("utf-8", errors="ignore")[:3000] if e.stdout else "",
            "stderr": "TIMEOUT after %ss" % timeout,
        }
    except Exception as e:
        return {"rc": None, "stdout": "", "stderr": type(e).__name__ + ": " + str(e)[:500]}


result = {}
# Test 1: text only (no tools)
result["text_test"] = run_codex(
    "Bạn là Sol. Trả lời bằng tiếng Việt trong 1 câu ngắn: bạn là model nào? "
    "Không dùng công cụ, chỉ trả lời bằng chữ.", timeout=150)
# Test 2: tool call (write a file)
result["tool_test"] = run_codex(
    "Tạo file codex_chat_test.txt trong thư mục hiện tại, nội dung là 'hello from sol via chat api'. "
    "Chỉ ghi file, không làm gì khác.", timeout=150)
# Check if the file was created
test_file = os.path.join(WORKDIR, "codex_chat_test.txt")
result["tool_file_exists"] = os.path.exists(test_file)
if result["tool_file_exists"]:
    try:
        with open(test_file, encoding="utf-8") as f:
            result["tool_file_content"] = f.read()[:200]
        os.remove(test_file)
        result["tool_file_cleaned"] = True
    except Exception as e:
        result["tool_file_content"] = "read error: " + str(e)[:200]

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("OK")
