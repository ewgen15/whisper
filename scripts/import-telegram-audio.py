#!/usr/bin/env python3
"""Pair voice messages from a Telegram Desktop export with the Notion pages the bot created.

Chain: voice message id -> bot reply (reply_to_message_id) -> Notion link -> page id.
Read-only: prints a summary and writes the pairs to --report. The export has no Bot API file_id,
so telegram_message_id is the stable key.

    python3 scripts/import-telegram-audio.py ChatExport_2026-10-27 --report pairs.json
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

# The id is the tail of the URL path ("Title-<id>" or "<id>", dashed or not). Searching the whole
# URL would swallow hex-looking letters from the title slug into the id.
NOTION_ID = re.compile(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})$")


def notion_page_id(msg):
    for ent in msg.get("text_entities", []):
        href = ent.get("href") or ent.get("text") or ""
        if "notion" in href:
            path = href.split("#")[0].split("?")[0].rstrip("/")
            found = NOTION_ID.search(path)
            if found:
                return found.group(1).replace("-", "")
    return None


def build_pairs(export_dir):
    with open(os.path.join(export_dir, "result.json"), encoding="utf-8") as f:
        messages = json.load(f)["messages"]
    by_id = {m["id"]: m for m in messages}
    voices = [m for m in messages if m.get("media_type") == "voice_message"]

    pages = defaultdict(list)  # voice id -> notion page ids from the bot's replies
    for m in messages:
        voice = by_id.get(m.get("reply_to_message_id"))
        if voice is not None and voice.get("media_type") == "voice_message":
            page = notion_page_id(m)
            if page:
                pages[voice["id"]].append(page)

    pairs, unpaired = [], []
    for v in voices:
        path = os.path.join(export_dir, v["file"])
        row = {
            "telegram_message_id": v["id"],
            "recorded_at": datetime.fromtimestamp(int(v["date_unixtime"]), timezone.utc).isoformat(),
            "duration_s": v["duration_seconds"],
            "file": v["file"],
            "file_exists": os.path.isfile(path),
        }
        ids = pages.get(v["id"], [])
        if len(set(ids)) == 1:
            pairs.append({**row, "notion_page_id": ids[0]})
        else:
            unpaired.append({**row, "notion_page_ids": sorted(set(ids))})
    return pairs, unpaired


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export_dir")
    ap.add_argument("--report", help="write {pairs, unpaired} as JSON here")
    args = ap.parse_args()

    pairs, unpaired = build_pairs(args.export_dir)
    usable = [p for p in pairs if p["file_exists"]]
    print(f"voice messages: {len(pairs) + len(unpaired)}")
    print(f"paired with exactly one Notion page: {len(pairs)} (file on disk: {len(usable)})")
    print(f"distinct Notion pages: {len({p['notion_page_id'] for p in pairs})}")
    print(f"unpaired (no link, or several different links): {len(unpaired)}")
    print(f"audio to import: {sum(p['duration_s'] for p in usable) / 60:.0f} min")
    for u in unpaired:
        print(f"  {u['recorded_at'][:16]}  msg {u['telegram_message_id']}  {u['duration_s']}s  links={len(u['notion_page_ids'])}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"pairs": pairs, "unpaired": unpaired}, f, ensure_ascii=False, indent=1)
        print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
