
import os, json, py_compile
p = r"E:\MyProj\expense\db_conn.py"
out = {"exists": os.path.exists(p)}
if out["exists"]:
    with open(p, encoding="utf-8") as f:
        content = f.read()
    out["size_bytes"] = len(content.encode("utf-8"))
    out["chars"] = len(content)
    out["lines"] = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    out["content"] = content
    try:
        py_compile.compile(p, doraise=True)
        out["py_compile"] = "OK"
    except Exception as e:
        out["py_compile"] = "FAIL: " + str(e)
    # Test connect() actually works.
    try:
        import sys
        sys.path.insert(0, r"E:\MyProj\expense")
        os.chdir(r"E:\MyProj\expense")
        import db_conn
        conn = db_conn.connect()
        out["connect_works"] = True
        conn.close()
    except Exception as e:
        out["connect_works"] = False
        out["connect_error"] = str(e)
# List the expense folder.
out["folder"] = sorted(os.listdir(r"E:\MyProj\expense"))
with open(r"E:\MyProj\ai-hub\db_conn_verify.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("WROTE verify")
