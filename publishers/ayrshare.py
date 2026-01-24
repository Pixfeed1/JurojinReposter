"""
Ayrshare publisher module for posting to Twitter and Facebook.
"""

import logging
from typing import Optional

import requests

from config.settings import AYRSHARE_API_KEY, AYRSHARE_API_URL
from core.database import add_repost

logger = logging.getLogger(__name__)


class AyrsharePublisher:
    """Publisher class for Ayrshare API."""

    def __init__(self):
        self.api_key = AYRSHARE_API_KEY
        self.api_url = AYRSHARE_API_URL

    def _get_headers(self) -> dict:
        """Get API headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def post(self, accroche: str, url: str, platform: str,
             image_url: Optional[str] = None, dry_run: bool = False) -> dict:
        """
        Post content to a social media platform.

        Args:
            accroche: The hook/text to post
            url: The article URL to include
            platform: Target platform ('twitter' or 'facebook')
            image_url: Optional image URL to include
            dry_run: If True, don't actually post

        Returns:
            dict with 'success', 'error_message', and 'response' keys
        """
        if not self.api_key:
            return {
                'success': False,
                'error_message': 'AYRSHARE_API_KEY not configured',
                'response': None
            }

        # Build the post content
        post_text = f"{accroche}\n{url}"

        # Build the payload
        payload = {
            "post": post_text,
            "platforms": [platform]
        }

        if image_url:
            payload["mediaUrls"] = [image_url]

        if dry_run:
            logger.info(f"[DRY RUN] Would post to {platform}:")
            logger.info(f"  Text: {post_text}")
            logger.info(f"  Image: {image_url}")
            return {
                'success': True,
                'error_message': None,
                'response': {'dry_run': True, 'payload': payload}
            }

        try:
            response = requests.post(
                self.api_url,
                headers=self._get_headers(),
                json=payload,
                timeout=60
            )

            data = response.json()

            # Check for errors
            if response.status_code != 200:
                error_msg = data.get('message', str(data))
                logger.error(f"Ayrshare API error: {error_msg}")
                return {
                    'success': False,
                    'error_message': error_msg,
                    'response': data
                }

            # Check platform-specific status
            if 'status' in data and data['status'] == 'error':
                error_msg = data.get('message', 'Unknown error')
                logger.error(f"Post failed: {error_msg}")
                return {
                    'success': False,
                    'error_message': error_msg,
                    'response': data
                }

            logger.info(f"Successfully posted to {platform}")
            return {
                'success': True,
                'error_message': None,
                'response': data
            }

        except requests.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error_message': error_msg,
                'response': None
            }
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error_message': error_msg,
                'response': None
            }


def publish_article(article_id: int, article_url: str, accroche: str,
                    platform: str, image_url: Optional[str] = None,
                    dry_run: bool = False) -> bool:
    """
    Publish an article to a social media platform and record the result.

    Args:
        article_id: Database article ID
        article_url: The article URL
        accroche: The hook text
        platform: Target platform
        image_url: Optional image URL
        dry_run: If True, don't actually post

    Returns:
        True if successful, False otherwise
    """
    publisher = AyrsharePublisher()
    result = publisher.post(accroche, article_url, platform, image_url, dry_run)

    # Record the repost attempt (skip for dry run)
    if not dry_run:
        add_repost(
            article_id=article_id,
            platform=platform,
            accroche=accroche,
            success=result['success'],
            error_message=result['error_message']
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
        source_name: Name of the source (e.g., "3DVF", "The Verge")
        image_url: Optional image URL
        dry_run: If True, don't actually post

    Returns:
        True if successful, False otherwise
    """
    publisher = AyrsharePublisher()
    result = publisher.post(accroche, source_url, 'facebook', image_url, dry_run)

    if result['success']:
        logger.info(f"Published external article from {source_name} to Facebook")
    else:
        logger.error(f"Failed to publish external article: {result['error_message']}")

    return result['success']


def check_api_status() -> dict:
    """
    Check the Ayrshare API status and configuration.

    Returns:
        dict with 'configured', 'connected', and 'error' keys
    """
    if not AYRSHARE_API_KEY:
        return {
            'configured': False,
            'connected': False,
            'error': 'AYRSHARE_API_KEY not set'
        }

    try:
        # Try to get user info to verify API key
        response = requests.get(
            "https://api.ayrshare.com/api/user",
            headers={
                "Authorization": f"Bearer {AYRSHARE_API_KEY}"
            },
            timeout=30
        )

        if response.status_code == 200:
            return {
                'configured': True,
                'connected': True,
                'error': None,
                'user': response.json()
            }
        else:
            return {
                'configured': True,
                'connected': False,
                'error': f"API returned status {response.status_code}"
            }

    except requests.RequestException as e:
        return {
            'configured': True,
            'connected': False,
            'error': str(e)
        }
