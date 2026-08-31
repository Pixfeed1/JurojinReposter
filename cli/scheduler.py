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
from core.database import (
    init_database, update_queue_status, add_repost, get_previous_accroches,
    get_daily_count, increment_daily_count, add_external_post
)
from core.scheduler import (
    is_scheduled_time,
    should_post_today,
    get_items_to_retry,
    load_scheduling_config,
    get_retry_config
)
from core.selector import select_best_article
from core.catchup import get_catchup_articles, get_catchup_count_today, record_catchup_post
from ai.groq_client import generate_accroche
from ai.thread_generator import generate_twitter_content
from ai.bluesky_generator import generate_bluesky_content
from ai.facebook_generator import generate_facebook_content
from ai.curation_filter import process_external_items
from sources.external_feeds import fetch_new_items
from publishers.twitter_direct import post_thread, post_tweet
from publishers.facebook import publish_article as publish_to_facebook, publish_external
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
            force_format=None,  # Auto mode
            category=article.get('category', '')
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
    elif platform == 'facebook':
        return process_facebook(article, logger)

    if success:
        logger.info(f"Successfully posted to {platform}")
    else:
        logger.error(f"Failed to post to {platform}")

    return success


def process_facebook(article: dict, logger: logging.Logger) -> bool:
    """
    Process a Facebook post for a jurojin.net article.
    Uses the dedicated Facebook generator with intelligent linking.
    """
    previous_posts = get_previous_accroches(article['id'], 'facebook')

    content = generate_facebook_content(
        title=article['title'],
        excerpt=article['excerpt'],
        category=article.get('category', ''),
        previous_posts=previous_posts
    )

    accroche = content['accroche']
    logger.info(f"Generated Facebook accroche: {accroche[:80]}...")
    if content.get('linked_url'):
        logger.info(f"  Linked article: {content['linked_url']}")

    success = publish_to_facebook(
        article_id=article['id'],
        article_url=article['url'],
        accroche=accroche,
        platform='facebook',
        image_url=article.get('image_url')
    )

    if success:
        logger.info("Successfully posted jurojin article to Facebook")
    else:
        logger.error("Failed to post jurojin article to Facebook")

    return success


def process_facebook_catchup(logger: logging.Logger) -> bool:
    """
    Process catchup: post articles never shared on Facebook.
    Max 2 per day.
    """
    config = load_scheduling_config()
    max_catchup = config.get('facebook', {}).get('max_catchup_per_day', 2)

    current_count = get_catchup_count_today()
    if current_count >= max_catchup:
        logger.debug(f"Catchup limit reached ({current_count}/{max_catchup})")
        return False

    articles = get_catchup_articles(limit=1)
    if not articles:
        return False

    article = articles[0]
    logger.info(f"Facebook catchup: {article['title'][:50]}...")

    previous_posts = get_previous_accroches(article['id'], 'facebook')
    content = generate_facebook_content(
        title=article['title'],
        excerpt=article['excerpt'],
        category=article.get('category', ''),
        previous_posts=previous_posts
    )

    success = publish_to_facebook(
        article_id=article['id'],
        article_url=article['url'],
        accroche=content['accroche'],
        platform='facebook',
        image_url=article.get('image_url')
    )

    if success:
        record_catchup_post()
        logger.info("Facebook catchup post successful")
    else:
        logger.error("Facebook catchup post failed")

    return success


def process_facebook_curation(logger: logging.Logger) -> bool:
    """
    Process curation: share filtered external articles on Facebook.
    Max 3 per day.
    """
    config = load_scheduling_config()
    max_external = config.get('facebook', {}).get('max_external_per_day', 3)

    current_count = get_daily_count('external')
    if current_count >= max_external:
        logger.debug(f"External posts limit reached ({current_count}/{max_external})")
        return False

    # Fetch new items from RSS feeds
    new_items = fetch_new_items(max_per_category=3)
    if not new_items:
        logger.debug("No new external items found")
        return False

    # Filter and generate accroches via AI
    accepted = process_external_items(new_items)
    if not accepted:
        logger.debug("No external items passed curation filter")
        return False

    # Post the first accepted item
    item = accepted[0]
    logger.info(f"Facebook curation: {item['title'][:50]}... (via {item['source_name']})")

    success = publish_external(
        source_url=item['url'],
        accroche=item['accroche'],
        source_name=item['source_name'],
        image_url=None
    )

    if success:
        # Record in database
        add_external_post(
            source_url=item['url'],
            source_name=item['source_name'],
            category=item['category'],
            title=item['title'],
            description=item.get('description', ''),
            accroche=item['accroche'],
            linked_article_url=item.get('linked_url')
        )
        increment_daily_count('external')
        logger.info("Facebook curation post successful")
    else:
        logger.error("Facebook curation post failed")

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
    posts_made = 0

    # Twitter and Bluesky: standard processing
    for platform in ['twitter', 'bluesky']:
        try:
            if process_platform(platform, logger):
                posts_made += 1
        except Exception as e:
            logger.error(f"Error processing {platform}: {e}")

    # Facebook: priority system (jurojin > catchup > curation)
    try:
        config = load_scheduling_config()
        fb_config = config.get('facebook', {})

        if fb_config.get('enabled', False) and is_scheduled_time('facebook'):
            facebook_posted = False

            # Priority 1: jurojin.net article
            article = select_best_article('facebook')
            if article:
                logger.info(f"Facebook priority 1: jurojin article '{article['title'][:40]}...'")
                facebook_posted = process_facebook(article, logger)

            # Priority 2: catchup (if no jurojin article posted)
            if not facebook_posted:
                logger.info("Facebook priority 2: checking catchup...")
                facebook_posted = process_facebook_catchup(logger)

            # Priority 3: external curation (if nothing else posted)
            if not facebook_posted:
                logger.info("Facebook priority 3: checking curation...")
                facebook_posted = process_facebook_curation(logger)

            if facebook_posted:
                posts_made += 1
            else:
                logger.info("Facebook: nothing to post this slot")
    except Exception as e:
        logger.error(f"Error processing facebook: {e}")

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
