import os; p=r"E:\MyProj\expense\TASKS.md"; print("TASKS.md bytes:", os.path.getsize(p) if os.path.exists(p) else "MISSING")
