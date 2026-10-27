#!/usr/bin/env python3
"""One-off import of the historical voice archive into audio_files.

Reads a chat export (result.json + voice_messages/), pairs each voice message with the Notion page
that was created from it (voice -> reply -> page link), gives every recording its own id and a path
under AUDIO_DIR in the same layout the app uses (YYYY/MM/<id>.opus), and prepares the rows.

    python3 scripts/import-voice-history.py <export_dir>                 # summary only
    python3 scripts/import-voice-history.py <export_dir> --stage <dir>   # copy files + write rows.sql

The id is derived from the source message id, so a second run produces the same ids and paths
(`on conflict do nothing` in rows.sql makes it safe to re-apply). Nothing source-specific is stored.
"""
import argparse
import json
import os
import re
import shutil
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone

# The id is the tail of the URL path ("Title-<id>" or "<id>", dashed or not). Searching the whole
# URL would swallow hex-looking letters from the title slug into the id.
NOTION_ID = re.compile(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})$")
NAMESPACE = uuid.UUID("6f1b7a52-0c3e-4f0a-9d57-2b8e1c4a7d90")


def notion_page_id(msg):
    for ent in msg.get("text_entities", []):
        href = ent.get("href") or ent.get("text") or ""
        if "notion" in href:
            path = href.split("#")[0].split("?")[0].rstrip("/")
            found = NOTION_ID.search(path)
            if found:
                return found.group(1).replace("-", "")
    return None


def build_rows(export_dir):
    with open(os.path.join(export_dir, "result.json"), encoding="utf-8") as f:
        messages = json.load(f)["messages"]
    by_id = {m["id"]: m for m in messages}

    pages = defaultdict(set)  # voice message id -> Notion pages from the replies to it
    for m in messages:
        voice = by_id.get(m.get("reply_to_message_id"))
        if voice is not None and voice.get("media_type") == "voice_message":
            page = notion_page_id(m)
            if page:
                pages[voice["id"]].add(page)

    rows = []
    for v in messages:
        if v.get("media_type") != "voice_message":
            continue
        recorded = datetime.fromtimestamp(int(v["date_unixtime"]), timezone.utc)
        rec_id = uuid.uuid5(NAMESPACE, str(v["id"]))
        found = pages.get(v["id"], set())
        rows.append({
            "id": str(rec_id),
            "notion_page_id": next(iter(found)) if len(found) == 1 else None,
            "audio_path": f"{recorded:%Y}/{recorded:%m}/{rec_id.hex}.opus",
            "recorded_at": recorded.isoformat(),
            "duration_s": v["duration_seconds"],
            "source_file": os.path.join(export_dir, v["file"]),
        })
    return rows


def sql_value(x):
    return "null" if x is None else "'" + str(x).replace("'", "''") + "'"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export_dir")
    ap.add_argument("--stage", help="copy files here under their new names and write rows.sql")
    args = ap.parse_args()

    rows = build_rows(args.export_dir)
    missing = [r for r in rows if not os.path.isfile(r["source_file"])]
    if missing:
        sys.exit(f"{len(missing)} audio files are missing from the export, e.g. {missing[0]['source_file']}")
    linked = [r for r in rows if r["notion_page_id"]]
    pages = [r["notion_page_id"] for r in linked]
    assert len(set(pages)) == len(pages), "one Notion page is linked to several recordings"
    print(f"recordings: {len(rows)} ({sum(r['duration_s'] for r in rows) / 60:.0f} min)")
    print(f"linked to a Notion page: {len(linked)}, without link: {len(rows) - len(linked)}")

    if args.stage:
        for r in rows:
            dest = os.path.join(args.stage, r["audio_path"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(r["source_file"], dest)
        values = ",\n".join(
            "(" + ", ".join([sql_value(r["id"]), sql_value(r["notion_page_id"]), sql_value(r["audio_path"]),
                            sql_value(r["recorded_at"]), str(int(r["duration_s"]))]) + ")"
            for r in rows
        )
        with open(os.path.join(args.stage, "rows.sql"), "w", encoding="utf-8") as f:
            f.write("insert into public.audio_files (id, notion_page_id, audio_path, recorded_at, duration_s) values\n"
                    f"{values}\non conflict (id) do nothing;\n")
        print(f"staged in {args.stage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
