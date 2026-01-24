"""
Facebook publisher using Facebook Graph API directly.
Posts to a Facebook Page via Page Access Token.
"""

import logging
import time
from typing import Optional

import requests

from config.settings import FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN
from core.database import add_repost

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v19.0"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def _make_request(endpoint: str, data: dict, retries: int = MAX_RETRIES) -> dict:
    """
    Make a POST request to Facebook Graph API with retry logic.

    Returns:
        {"success": bool, "post_id": str or None, "error": str or None}
    """
    url = f"{GRAPH_API_URL}/{endpoint}"
    data['access_token'] = FACEBOOK_PAGE_ACCESS_TOKEN

    last_error = None
    for attempt in range(retries):
        try:
            response = requests.post(url, data=data, timeout=60)
            result = response.json()

            if 'error' in result:
                error_msg = result['error'].get('message', str(result['error']))
                error_code = result['error'].get('code', 0)

                # Don't retry on auth errors or invalid parameters
                if error_code in (190, 200, 100):
                    logger.error(f"Facebook API error (non-retryable): {error_msg}")
                    return {"success": False, "post_id": None, "error": error_msg}

                last_error = error_msg
                logger.warning(f"Facebook API error (attempt {attempt + 1}): {error_msg}")
            elif 'id' in result:
                return {"success": True, "post_id": result['id'], "error": None}
            else:
                last_error = f"Unexpected response: {result}"
                logger.warning(f"Unexpected Facebook response: {result}")

        except requests.RequestException as e:
            last_error = str(e)
            logger.warning(f"Facebook request error (attempt {attempt + 1}): {e}")

        if attempt < retries - 1:
            wait_time = RETRY_DELAY * (2 ** attempt)
            time.sleep(wait_time)

    logger.error(f"Facebook API failed after {retries} attempts: {last_error}")
    return {"success": False, "post_id": None, "error": last_error}


def post_to_facebook(text: str, link: Optional[str] = None,
                     image_url: Optional[str] = None,
                     dry_run: bool = False) -> dict:
    """
    Post content to a Facebook Page.

    Args:
        text: The post text/message
        link: Optional URL to attach as link preview
        image_url: Optional image URL to post as photo
        dry_run: If True, don't actually post

    Returns:
        {"success": bool, "post_id": str or None, "error": str or None}
    """
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        return {
            "success": False,
            "post_id": None,
            "error": "FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN not configured"
        }

    if dry_run:
        logger.info(f"[DRY RUN] Would post to Facebook:")
        logger.info(f"  Text: {text[:100]}...")
        if link:
            logger.info(f"  Link: {link}")
        if image_url:
            logger.info(f"  Image: {image_url}")
        return {"success": True, "post_id": "dry_run", "error": None}

    # Post with image (uses /photos endpoint)
    if image_url:
        data = {
            "message": text,
            "url": image_url
        }
        if link:
            # Include link in message text since photo posts don't support link preview
            if link not in text:
                data["message"] = f"{text}\n{link}"
        result = _make_request(f"{FACEBOOK_PAGE_ID}/photos", data)

    # Post with link preview (uses /feed endpoint)
    elif link:
        data = {
            "message": text,
            "link": link
        }
        result = _make_request(f"{FACEBOOK_PAGE_ID}/feed", data)

    # Text-only post
    else:
        data = {"message": text}
        result = _make_request(f"{FACEBOOK_PAGE_ID}/feed", data)

    if result['success']:
        logger.info(f"Posted to Facebook (ID: {result['post_id']})")
    else:
        logger.error(f"Failed to post to Facebook: {result['error']}")

    return result


def publish_article(article_id: int, article_url: str, accroche: str,
                    platform: str = 'facebook', image_url: Optional[str] = None,
                    dry_run: bool = False) -> bool:
    """
    Publish a jurojin.net article to Facebook and record the result.

    Args:
        article_id: Database article ID
        article_url: The article URL
        accroche: The hook text
        platform: Platform name (always 'facebook')
        image_url: Optional image URL
        dry_run: If True, don't actually post

    Returns:
        True if successful, False otherwise
    """
    # Build post text with link
    post_text = f"{accroche}\n{article_url}"

    result = post_to_facebook(
        text=post_text,
        link=article_url,
        image_url=image_url,
        dry_run=dry_run
    )

    # Record the repost attempt (skip for dry run)
    if not dry_run:
        add_repost(
            article_id=article_id,
            platform='facebook',
            accroche=accroche,
            success=result['success'],
            error_message=result['error']
        )

    return result['success']


def publish_external(source_url: str, accroche: str, source_name: str,
                     image_url: Optional[str] = None,
                     dry_run: bool = False) -> bool:
    """
    Publish an external article to Facebook.

    Args:
        source_url: The external article URL
        accroche: The hook text
        source_name: Name of the source
        image_url: Optional image URL
        dry_run: If True, don't actually post

    Returns:
        True if successful, False otherwise
    """
    post_text = f"{accroche}\n{source_url}"

    result = post_to_facebook(
        text=post_text,
        link=source_url,
        image_url=image_url,
        dry_run=dry_run
    )

    if result['success']:
        logger.info(f"Published external article from {source_name} to Facebook")
    else:
        logger.error(f"Failed to publish external article: {result['error']}")

    return result['success']


def check_api_status() -> dict:
    """
    Check the Facebook Graph API status and page access.

    Returns:
        dict with 'configured', 'connected', 'page_name', and 'error' keys
    """
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        return {
            'configured': False,
            'connected': False,
            'error': 'FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN not set'
        }

    try:
        response = requests.get(
            f"{GRAPH_API_URL}/{FACEBOOK_PAGE_ID}",
            params={
                'access_token': FACEBOOK_PAGE_ACCESS_TOKEN,
                'fields': 'name,id,fan_count'
            },
            timeout=30
        )

        data = response.json()

        if 'error' in data:
            return {
                'configured': True,
                'connected': False,
                'error': data['error'].get('message', 'Unknown error')
            }

        return {
            'configured': True,
            'connected': True,
            'page_name': data.get('name', 'Unknown'),
            'page_id': data.get('id'),
            'fan_count': data.get('fan_count', 0),
            'error': None
        }

    except requests.RequestException as e:
        return {
            'configured': True,
            'connected': False,
            'error': str(e)
        }
