#!/usr/bin/env python3
"""Nhập lịch sử hộp thư cũ (messages.json) vào AI Hub v1 để xem trong /hub (tab Phòng → Lịch sử hộp thư cũ).

  python import_legacy.py --dry-run                  # chỉ đếm, không ghi gì
  python import_legacy.py                            # nhập (chạy lại được, không trùng)
  python import_legacy.py --map emi=codex            # đổi tên người gửi cũ (mặc định tinker=muse)

Chỉ ĐỌC messages.json; không sửa hộp thư cũ. Tin nhập vào chỉ để xem, không bao giờ giao cho agent.
Nút "Nhập lịch sử hộp thư cũ" ở tab AI trên /hub làm đúng việc này.
"""
import argparse
import json
import os
import sys

import hub_db
import hub_legacy

BASE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.path.join(BASE, "messages.json"))
    parser.add_argument("--db", default=os.environ.get("AI_HUB_DB", os.path.join(BASE, "hub.sqlite3")))
    parser.add_argument("--map", action="append", metavar="CŨ=MỚI", help="đổi tên người gửi/nhận cũ")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        mapping = hub_legacy.parse_map(args.map)
        rows = hub_legacy.load_file(args.source)
        hub = hub_db.HubDB(args.db)
        stats = hub_legacy.import_rows(hub, rows, mapping, args.dry_run)
    except hub_db.HubError as exc:
        sys.exit("Lỗi: %s" % exc)
    print(json.dumps(dict(stats, mapping=mapping), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
