"""
CLI command: publish to Bluesky the posts validated in the CRM (Diffuseur module).

The CRM drops one JSON file per validated post in data/crm_inbox/ (crm-<id>.json):
    {"version": 1, "id": 12, "texte": "...", "lien": "https://...", "image_path": "/home/.../x-bs.jpg",
     "image_url": "https://media.jurojin.net/r/x-bs.jpg", "alt": "...", "publier_a": "2026-11-03T11:30:00Z",
     "tentatives": 0}
When "publier_a" is reached, this command publishes it with the existing Bluesky module
(same account and credentials as the scheduler) and writes the result for the CRM in
data/crm_outbox/crm-<id>.json:
    {"id": 12, "statut": "publie", "uri": "at://...", "publie_le": "..."}  or
    {"id": 12, "statut": "erreur", "erreur": "..."}

Rules:
- one post per run at most (no burst after a downtime);
- never posts without its image: if the image cannot be uploaded, it is a failure;
- 3 attempts, one per run, then a final error;
- a post more than 24 h late is not published (error: reschedule it in the CRM);
- a file is claimed by renaming it to .processing, so the CRM never rewrites it mid-publication.

Usage (cron every 15 minutes):
    python -m cli.crm_inbox
    python -m cli.crm_inbox --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import BASE_DIR, LOG_PATH

logger = logging.getLogger(__name__)

INBOX = Path(os.getenv("CRM_INBOX_DIR", str(BASE_DIR / "data" / "crm_inbox")))
OUTBOX = INBOX.parent / "crm_outbox"
MAX_ATTEMPTS = 3
MAX_LATE = timedelta(hours=24)
STALE_PROCESSING = timedelta(hours=2)
BLUESKY_IMAGE_MAX = 976 * 1024  # Bluesky refuses images above ~1 MB


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
    )


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_result(post_id: int, statut: str, **fields) -> None:
    write_json(OUTBOX / f"crm-{post_id}.json", {"id": post_id, "statut": statut, **fields})


def read_image(item: dict) -> bytes:
    path = item.get("image_path")
    if path and Path(path).is_file():
        data = Path(path).read_bytes()
    elif item.get("image_url"):
        import requests
        resp = requests.get(item["image_url"], timeout=20)
        resp.raise_for_status()
        data = resp.content
    else:
        raise RuntimeError("image introuvable")
    if len(data) > BLUESKY_IMAGE_MAX:
        raise RuntimeError(f"image trop lourde pour Bluesky ({len(data) // 1024} ko)")
    return data


def publish_to_bluesky(item: dict) -> dict:
    """Publish with the existing Bluesky module: same client, facets and truncation rules."""
    from atproto import models
    from publishers.bluesky import (create_hashtag_facets, create_link_facets,
                                    get_bluesky_client, truncate_for_bluesky)

    image = read_image(item)
    client = get_bluesky_client()
    if not client:
        return {"success": False, "error": "Failed to connect to Bluesky"}
    try:
        url = item.get("lien") or None
        text = truncate_for_bluesky(item.get("texte", ""), url)
        facets = []
        if url:
            facets.extend(create_link_facets(text, url))
        facets.extend(create_hashtag_facets(text))
        upload = client.upload_blob(image)
        embed = models.AppBskyEmbedImages.Main(images=[
            models.AppBskyEmbedImages.Image(alt=(item.get("alt") or "")[:1000], image=upload.blob)
        ])
        post = client.send_post(text=text, facets=facets or None, embed=embed)
        return {"success": True, "uri": post.uri}
    except Exception as e:  # noqa: BLE001 - any failure is reported to the CRM
        return {"success": False, "error": str(e) or repr(e)}


def restore_stale(now: datetime) -> None:
    """A .processing file left by a crashed run goes back to the queue."""
    for f in INBOX.glob("crm-*.json.processing"):
        age = now - datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
        if age > STALE_PROCESSING:
            target = f.with_name(f.name[:-len(".processing")])
            if not target.exists():
                os.replace(f, target)
                logger.warning(f"[CRM] {target.name} remis en file après une exécution interrompue")


def due_items(now: datetime) -> list:
    items = []
    for f in sorted(INBOX.glob("crm-*.json")):
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
            when = parse_time(item["publier_a"])
        except Exception as e:  # noqa: BLE001
            logger.error(f"[CRM] fichier illisible {f.name}: {e}")
            continue
        if when <= now:
            items.append((when, f, item))
    items.sort(key=lambda x: x[0])
    return items


def run(now: Optional[datetime] = None, dry_run: bool = False,
        publisher: Callable[[dict], dict] = publish_to_bluesky) -> dict:
    now = now or datetime.now(timezone.utc)
    INBOX.mkdir(parents=True, exist_ok=True)
    restore_stale(now)
    due = due_items(now)
    if not due:
        return {"published": 0, "failed": 0, "pending": 0}

    when, f, item = due[0]
    post_id = int(item.get("id"))
    if now - when > MAX_LATE:
        write_result(post_id, "erreur", erreur="créneau dépassé de plus de 24 h : replanifiez la publication dans le CRM")
        f.unlink(missing_ok=True)
        logger.warning(f"[CRM] publication {post_id} trop en retard, abandonnée")
        return {"published": 0, "failed": 1, "pending": len(due) - 1}

    if dry_run:
        logger.info(f"[CRM][DRY RUN] publierait {post_id}: {item.get('texte', '')[:60]}...")
        return {"published": 0, "failed": 0, "pending": len(due)}

    claimed = f.with_name(f.name + ".processing")
    try:
        os.replace(f, claimed)
    except FileNotFoundError:
        return {"published": 0, "failed": 0, "pending": len(due) - 1}  # removed by the CRM meanwhile

    try:
        result = publisher(item)
    except Exception as e:  # noqa: BLE001
        result = {"success": False, "error": str(e) or repr(e)}

    if result.get("success"):
        write_result(post_id, "publie", uri=result.get("uri"), publie_le=now.isoformat().replace("+00:00", "Z"))
        claimed.unlink(missing_ok=True)
        logger.info(f"[CRM] publication {post_id} publiée sur Bluesky : {result.get('uri')}")
        return {"published": 1, "failed": 0, "pending": len(due) - 1}

    attempts = int(item.get("tentatives") or 0) + 1
    error = str(result.get("error") or "erreur inconnue")[:500]
    if attempts >= MAX_ATTEMPTS:
        write_result(post_id, "erreur", erreur=error)
        claimed.unlink(missing_ok=True)
        logger.error(f"[CRM] publication {post_id} abandonnée après {attempts} essais : {error}")
    else:
        item["tentatives"] = attempts
        item["derniere_erreur"] = error
        write_json(claimed, item)
        os.replace(claimed, f)
        logger.warning(f"[CRM] publication {post_id} : essai {attempts}/{MAX_ATTEMPTS} raté ({error}), nouvel essai au prochain passage")
    return {"published": 0, "failed": 1, "pending": len(due) - 1}


def main():
    parser = argparse.ArgumentParser(description='Publish CRM-validated posts to Bluesky')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be published')
    args = parser.parse_args()
    setup_logging()
    started = time.time()
    report = run(dry_run=args.dry_run)
    if report["published"] or report["failed"] or report["pending"]:
        logger.info(f"[CRM] {report} ({time.time() - started:.1f}s)")


if __name__ == '__main__':
    main()
