"""
Twitter direct publisher using Twitter API v2 with OAuth 1.0a.
"""

import logging
import os
import tempfile
from typing import Optional

import requests
import tweepy

logger = logging.getLogger(__name__)

# Twitter API credentials from environment variables
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")


def get_twitter_client() -> Optional[tweepy.Client]:
    """Get a Twitter API v2 client."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        logger.error("Twitter API credentials not configured")
        return None

    try:
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        return client
    except Exception as e:
        logger.error(f"Error creating Twitter client: {e}")
        return None


def get_twitter_api_v1() -> Optional[tweepy.API]:
    """Get a Twitter API v1.1 client (needed for media upload)."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        return None

    try:
        auth = tweepy.OAuth1UserHandler(
            TWITTER_API_KEY,
            TWITTER_API_SECRET,
            TWITTER_ACCESS_TOKEN,
            TWITTER_ACCESS_TOKEN_SECRET
        )
        return tweepy.API(auth)
    except Exception as e:
        logger.error(f"Error creating Twitter API v1: {e}")
        return None


def download_image(image_url: str) -> Optional[str]:
    """Download an image to a temporary file. Returns the file path."""
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        # Determine file extension from content type
        content_type = response.headers.get('content-type', 'image/jpeg')
        ext = '.jpg'
        if 'png' in content_type:
            ext = '.png'
        elif 'gif' in content_type:
            ext = '.gif'

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
            f.write(response.content)
            return f.name

    except Exception as e:
        logger.error(f"Error downloading image: {e}")
        return None


def upload_media(api_v1: tweepy.API, image_path: str) -> Optional[str]:
    """Upload media to Twitter. Returns the media_id."""
    try:
        media = api_v1.media_upload(filename=image_path)
        return media.media_id_string
    except Exception as e:
        logger.error(f"Error uploading media: {e}")
        return None


def post_tweet(text: str, image_url: Optional[str] = None,
               dry_run: bool = False) -> dict:
    """
    Post a tweet to Twitter.

    Args:
        text: The tweet text
        image_url: Optional image URL to attach
        dry_run: If True, don't actually post

    Returns:
        dict with 'success', 'tweet_id', 'error' keys
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would tweet: {text[:50]}...")
        if image_url:
            logger.info(f"[DRY RUN] With image: {image_url}")
        return {
            'success': True,
            'tweet_id': 'dry_run',
            'error': None
        }

    client = get_twitter_client()
    if not client:
        return {
            'success': False,
            'tweet_id': None,
            'error': 'Twitter credentials not configured'
        }

    media_ids = None

    # Handle image upload if provided
    if image_url:
        api_v1 = get_twitter_api_v1()
        if api_v1:
            image_path = download_image(image_url)
            if image_path:
                try:
                    media_id = upload_media(api_v1, image_path)
                    if media_id:
                        media_ids = [media_id]
                        logger.info(f"Uploaded media: {media_id}")
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(image_path)
                    except Exception:
                        pass

    try:
        # Post the tweet
        response = client.create_tweet(text=text, media_ids=media_ids)

        tweet_id = response.data['id']
        logger.info(f"Tweet posted successfully: {tweet_id}")

        return {
            'success': True,
            'tweet_id': tweet_id,
            'error': None
        }

    except tweepy.TweepyException as e:
        error_msg = str(e)
        logger.error(f"Twitter API error: {error_msg}")
        return {
            'success': False,
            'tweet_id': None,
            'error': error_msg
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        return {
            'success': False,
            'tweet_id': None,
            'error': error_msg
        }


def publish_to_twitter(article_id: int, article_url: str, accroche: str,
                       image_url: Optional[str] = None,
                       dry_run: bool = False) -> bool:
    """
    Publish an article to Twitter and record the result.

    Args:
        article_id: Database article ID
        article_url: The article URL
        accroche: The hook text
        image_url: Optional image URL
        dry_run: If True, don't actually post

    Returns:
        True if successful, False otherwise
    """
    from core.database import add_repost

    # Build tweet text
    tweet_text = f"{accroche}\n{article_url}"

    result = post_tweet(tweet_text, image_url, dry_run)

    # Record the repost attempt (skip for dry run)
    if not dry_run:
        add_repost(
            article_id=article_id,
            platform='twitter',
            accroche=accroche,
            success=result['success'],
            error_message=result['error']
        )

    return result['success']


def check_twitter_credentials() -> dict:
    """
    Check if Twitter credentials are configured and valid.

    Returns:
        dict with 'configured', 'valid', 'error', 'username' keys
    """
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        return {
            'configured': False,
            'valid': False,
            'error': 'Twitter credentials not set in environment',
            'username': None
        }

    try:
        client = get_twitter_client()
        if not client:
            return {
                'configured': True,
                'valid': False,
                'error': 'Could not create Twitter client',
                'username': None
            }

        # Verify credentials by getting the authenticated user
        me = client.get_me()
        if me and me.data:
            return {
                'configured': True,
                'valid': True,
                'error': None,
                'username': me.data.username
            }
        else:
            return {
                'configured': True,
                'valid': False,
                'error': 'Could not verify credentials',
                'username': None
            }

    except tweepy.TweepyException as e:
        return {
            'configured': True,
            'valid': False,
            'error': str(e),
            'username': None
        }
