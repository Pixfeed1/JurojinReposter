"""
CLI command for the scheduler (called by CRON).

Usage:
    python -m cli.scheduler
"""

import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH
from core.database import init_database, update_queue_status, add_repost, get_previous_accroches
from core.scheduler import (
    is_scheduled_time,
    should_post_today,
    get_items_to_retry,
    load_scheduling_config,
    get_retry_config
)
from core.selector import select_best_article
from ai.groq_client import generate_accroche
from ai.thread_generator import generate_twitter_content
from ai.bluesky_generator import generate_bluesky_content
from publishers.twitter_direct import post_thread, post_tweet
from publishers.ayrshare import publish_article as publish_to_facebook
from publishers.bluesky import post_to_bluesky, post_thread_to_bluesky


def setup_logging():
    """Configure logging."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler()
        ]
    )


def process_platform(platform: str, logger: logging.Logger) -> bool:
    """
    Process posting for a platform if it's the scheduled time.

    Returns True if a post was made, False otherwise.
    """
    config = load_scheduling_config()
    platform_config = config.get(platform, {})

    if not platform_config.get('enabled', False):
        logger.debug(f"{platform} is disabled")
        return False

    # Check if it's a scheduled time
    if not is_scheduled_time(platform):
        logger.debug(f"Not a scheduled time for {platform}")
        return False

    # Check if we should post today (for platforms with post_every_n_days)
    if not should_post_today(platform):
        logger.info(f"Skipping {platform} - not scheduled for today")
        return False

    logger.info(f"Processing scheduled post for {platform}")

    # Select best article
    article = select_best_article(platform)
    if not article:
        logger.warning(f"No eligible articles found for {platform}")
        return False

    logger.info(f"Selected article: {article['title']} (ID: {article['id']}, Score: {article['score']}, Words: {article['word_count']})")

    # Publish using appropriate publisher
    if platform == 'twitter':
        # Use thread generator for Twitter
        previous_hooks = get_previous_accroches(article['id'], 'twitter')

        content = generate_twitter_content(
            title=article['title'],
            excerpt=article['excerpt'],
            word_count=article['word_count'],
            score=article['score'],
            previous_hooks=previous_hooks,
            force_format=None  # Auto mode
        )

        logger.info(f"Format: {content['format']} ({content['posts_count']} tweet(s))")

        if content['type'] == 'thread':
            # Post as thread
            result = post_thread(
                tweets=content['tweets'],
                article_url=article['url'],
                image_url=article.get('image_url'),
                dry_run=False
            )
            success = result['success']

            # Record the repost
            accroche = content['tweets'][0] if content['tweets'] else ''
            add_repost(
                article_id=article['id'],
                platform='twitter',
                accroche=accroche,
                success=success,
                error_message=result.get('error'),
                format=content['format'],
                posts_count=content['posts_count']
            )
        else:
            # Post as simple tweet
            tweet_text = f"{content['tweets'][0]}\n{article['url']}"
            result = post_tweet(
                text=tweet_text,
                image_url=article.get('image_url'),
                dry_run=False
            )
            success = result['success']

            # Record the repost
            add_repost(
                article_id=article['id'],
                platform='twitter',
                accroche=content['tweets'][0],
                success=success,
                error_message=result.get('error'),
                format='simple',
                posts_count=1
            )
    elif platform == 'bluesky':
        # Generate Bluesky content
        previous_posts = get_previous_accroches(article['id'], 'bluesky')

        content = generate_bluesky_content(
            title=article['title'],
            excerpt=article['excerpt'],
            category=article.get('category', ''),
            post_type=article.get('post_type', 'posts'),
            word_count=article['word_count'],
            previous_posts=previous_posts
        )

        # Replace [LIEN] with actual URL
        posts = [p.replace("[LIEN]", article['url']) for p in content['posts']]

        logger.info(f"Bluesky format: {content['type']} ({len(posts)} post(s))")

        if content['type'] == 'thread':
            result = post_thread_to_bluesky(
                tweets=posts,
                url=article['url'],
                image_url=article.get('image_url'),
                dry_run=False
            )
            success = result['success']

            add_repost(
                article_id=article['id'],
                platform='bluesky',
                accroche=posts[0] if posts else '',
                success=success,
                error_message=result.get('error'),
                format='thread',
                posts_count=len(posts)
            )
        else:
            result = post_to_bluesky(
                text=posts[0],
                url=article['url'],
                image_url=article.get('image_url'),
                dry_run=False
            )
            success = result['success']

            add_repost(
                article_id=article['id'],
                platform='bluesky',
                accroche=posts[0],
                success=success,
                error_message=result.get('error'),
                format='simple',
                posts_count=1
            )
    else:
        # Generate accroche for Facebook
        accroche = generate_accroche(
            article_id=article['id'],
            title=article['title'],
            excerpt=article['excerpt'],
            platform=platform
        )
        logger.info(f"Generated accroche: {accroche}")

        success = publish_to_facebook(
            article_id=article['id'],
            article_url=article['url'],
            accroche=accroche,
            platform=platform,
            image_url=article.get('image_url')
        )

    if success:
        logger.info(f"Successfully posted to {platform}")
    else:
        logger.error(f"Failed to post to {platform}")

    return success


def process_retries(logger: logging.Logger) -> int:
    """
    Process failed items in the queue that are ready for retry.

    Returns the number of successful retries.
    """
    retry_config = get_retry_config()
    items_to_retry = get_items_to_retry()

    if not items_to_retry:
        logger.debug("No items to retry")
        return 0

    logger.info(f"Processing {len(items_to_retry)} items for retry")

    success_count = 0
    for item in items_to_retry:
        logger.info(f"Retrying: {item['title']} on {item['platform']} (attempt {item['attempts'] + 1})")

        platform = item['platform']
        if platform == 'twitter':
            success = publish_to_twitter(
                article_id=item['article_id'],
                article_url=item['url'],
                accroche=item['accroche'],
                image_url=item.get('image_url')
            )
        else:
            success = publish_to_facebook(
                article_id=item['article_id'],
                article_url=item['url'],
                accroche=item['accroche'],
                platform=platform,
                image_url=item.get('image_url')
            )

        if success:
            update_queue_status(item['id'], 'completed')
            success_count += 1
            logger.info(f"Retry successful for article {item['article_id']}")
        else:
            max_attempts = retry_config.get('max_attempts', 3)
            if item['attempts'] + 1 >= max_attempts:
                update_queue_status(item['id'], 'permanently_failed',
                                    'Max retry attempts reached')
                logger.error(f"Permanently failed: article {item['article_id']}")
            else:
                update_queue_status(item['id'], 'failed')
                logger.warning(f"Retry failed for article {item['article_id']}")

    return success_count


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("Scheduler running...")

    # Initialize database
    init_database()

    # Process each platform
    platforms = ['twitter', 'facebook', 'bluesky']
    posts_made = 0

    for platform in platforms:
        try:
            if process_platform(platform, logger):
                posts_made += 1
        except Exception as e:
            logger.error(f"Error processing {platform}: {e}")

    # Process retries
    try:
        retries_successful = process_retries(logger)
        if retries_successful:
            logger.info(f"Successfully retried {retries_successful} posts")
    except Exception as e:
        logger.error(f"Error processing retries: {e}")

    logger.info(f"Scheduler completed. Posts made: {posts_made}")
    logger.info("=" * 50)


if __name__ == '__main__':
    main()
