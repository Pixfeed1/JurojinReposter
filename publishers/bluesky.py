"""
Bluesky publisher module.
Posts content to Bluesky with support for links (facets), images and threads.
"""

import logging
import os
import requests
from typing import Optional

from atproto import Client, models

logger = logging.getLogger(__name__)


def get_bluesky_client() -> Optional[Client]:
    """Get an authenticated Bluesky client."""
    handle = os.getenv("BLUESKY_HANDLE")
    app_password = os.getenv("BLUESKY_APP_PASSWORD")

    if not handle or not app_password:
        logger.error("Missing Bluesky credentials (BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)")
        return None

    try:
        client = Client()
        client.login(handle, app_password)
        return client
    except Exception as e:
        logger.error(f"Failed to login to Bluesky: {e}")
        return None


def create_link_facets(text: str, url: str) -> list:
    """Create facets for clickable links in Bluesky posts."""
    facets = []
    url_start = text.find(url)

    if url_start != -1:
        # Bluesky uses byte positions, not character positions
        text_bytes = text.encode('utf-8')
        url_bytes = url.encode('utf-8')
        byte_start = len(text[:url_start].encode('utf-8'))
        byte_end = byte_start + len(url_bytes)

        facets.append(
            models.AppBskyRichtextFacet.Main(
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byteStart=byte_start,
                    byteEnd=byte_end
                ),
                features=[models.AppBskyRichtextFacet.Link(uri=url)]
            )
        )

    return facets


def upload_image(client: Client, image_url: str) -> Optional[models.AppBskyEmbedImages.Main]:
    """Download and upload an image to Bluesky."""
    try:
        response = requests.get(image_url, timeout=10)
        if response.status_code == 200:
            image_data = response.content
            upload = client.upload_blob(image_data)
            return models.AppBskyEmbedImages.Main(
                images=[
                    models.AppBskyEmbedImages.Image(
                        alt="Image article Jurojin",
                        image=upload.blob
                    )
                ]
            )
    except Exception as e:
        logger.warning(f"Could not upload image: {e}")

    return None


def post_to_bluesky(text: str, url: Optional[str] = None,
                    image_url: Optional[str] = None, dry_run: bool = False) -> dict:
    """
    Post a single message to Bluesky.

    Args:
        text: Post text (max 300 chars)
        url: Article URL (will be made clickable)
        image_url: URL of image to attach
        dry_run: If True, simulate without posting

    Returns:
        {"success": True/False, "uri": "...", "error": "..."}
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would post to Bluesky: {text[:50]}...")
        return {"success": True, "uri": "dry-run"}

    client = get_bluesky_client()
    if not client:
        return {"success": False, "error": "Failed to connect to Bluesky"}

    try:
        # Prepare text with URL
        full_text = text
        if url and url not in text:
            full_text = f"{text}\n{url}"

        # Truncate if too long (Bluesky limit is 300 chars)
        if len(full_text) > 300:
            full_text = full_text[:297] + "..."

        # Create facets for clickable link
        facets = []
        if url:
            facets = create_link_facets(full_text, url)

        # Upload image if provided
        embed = None
        if image_url:
            embed = upload_image(client, image_url)

        # Post
        post = client.send_post(
            text=full_text,
            facets=facets if facets else None,
            embed=embed
        )

        logger.info(f"Posted to Bluesky: {post.uri}")
        return {"success": True, "uri": post.uri}

    except Exception as e:
        logger.error(f"Failed to post to Bluesky: {e}")
        return {"success": False, "error": str(e)}


def post_thread_to_bluesky(tweets: list, url: Optional[str] = None,
                           image_url: Optional[str] = None, dry_run: bool = False) -> dict:
    """
    Post a thread to Bluesky (each post replies to the previous one).

    Args:
        tweets: List of post texts
        url: URL to add to the last post
        image_url: Image for the first post
        dry_run: If True, simulate without posting

    Returns:
        {"success": True/False, "uris": [...], "error": "..."}
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would post thread of {len(tweets)} posts to Bluesky")
        for i, t in enumerate(tweets):
            logger.info(f"  Post {i+1}: {t[:50]}...")
        return {"success": True, "uris": ["dry-run"] * len(tweets)}

    client = get_bluesky_client()
    if not client:
        return {"success": False, "error": "Failed to connect to Bluesky"}

    try:
        uris = []
        parent_ref = None
        root_ref = None

        for i, tweet_text in enumerate(tweets):
            # Add URL to last post
            if i == len(tweets) - 1 and url:
                if url not in tweet_text:
                    tweet_text = f"{tweet_text}\n{url}"

            # Truncate if too long
            if len(tweet_text) > 300:
                tweet_text = tweet_text[:297] + "..."

            # Create facets for URL in last post
            facets = []
            if i == len(tweets) - 1 and url:
                facets = create_link_facets(tweet_text, url)

            # Image only on first post
            embed = None
            if i == 0 and image_url:
                embed = upload_image(client, image_url)

            # Reply if not the first post
            reply_to = None
            if parent_ref and root_ref:
                reply_to = models.AppBskyFeedPost.ReplyRef(
                    parent=parent_ref,
                    root=root_ref
                )

            post = client.send_post(
                text=tweet_text,
                facets=facets if facets else None,
                embed=embed,
                reply_to=reply_to
            )

            uris.append(post.uri)

            # Store refs for next post
            post_ref = models.create_strong_ref(post)
            parent_ref = post_ref
            if root_ref is None:
                root_ref = post_ref

        logger.info(f"Posted thread of {len(uris)} posts to Bluesky")
        return {"success": True, "uris": uris}

    except Exception as e:
        logger.error(f"Failed to post thread to Bluesky: {e}")
        return {"success": False, "error": str(e)}
