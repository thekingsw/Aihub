# -*- coding: utf-8 -*-
r"""Start AI Hub via WMI and exit immediately (no waiting). The caller checks
the port separately. Writes a small result file.
"""
import json
import subprocess

OUT = r"E:\MyProj\ai-hub\start_aihub_out.json"
result = {}
try:
    r = subprocess.run(["wmic", "process", "call", "create",
                        r"cmd /c E:\MyProj\ai-hub\start.bat"],
                       capture_output=True, text=True, timeout=30)
    result["wmic_rc"] = r.returncode
    result["wmic_out"] = ((r.stdout or "") + (r.stderr or ""))[:800]
except Exception as e:
    result["wmic_error"] = type(e).__name__ + ": " + str(e)[:300]

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("OK wmic_rc=%s" % result.get("wmic_rc"))
