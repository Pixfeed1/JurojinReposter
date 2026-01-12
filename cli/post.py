"""
CLI command for posting to social media platforms.

Usage:
    python -m cli.post --platform twitter
    python -m cli.post --platform facebook
    python -m cli.post --platform twitter --dry-run
    python -m cli.post --platform twitter --article-id 123
    python -m cli.post --platform twitter --format thread
    python -m cli.post --platform twitter --format simple
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH
from core.database import init_database, get_article_by_id, add_repost, get_previous_accroches
from core.selector import select_best_article, get_article_eligibility
from ai.groq_client import generate_accroche
from ai.thread_generator import generate_twitter_content
from publishers.twitter_direct import publish_to_twitter, post_thread, post_tweet
from publishers.ayrshare import publish_article as publish_to_facebook


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


def main():
    parser = argparse.ArgumentParser(description='Post to social media')
    parser.add_argument('--platform', required=True,
                        choices=['twitter', 'facebook'],
                        help='Platform to post to')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate posting without actually posting')
    parser.add_argument('--article-id', type=int,
                        help='Force posting a specific article by ID')
    parser.add_argument('--format', choices=['simple', 'thread', 'auto'],
                        default='auto',
                        help='Twitter format: simple (1 tweet), thread (multi-tweet), or auto (based on score/word count)')
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info(f"Starting post to {args.platform}...")
    if args.dry_run:
        logger.info("DRY RUN MODE - No actual posting will occur")

    # Initialize database
    init_database()

    # Select article
    if args.article_id:
        article = get_article_by_id(args.article_id)
        if not article:
            logger.error(f"Article {args.article_id} not found")
            sys.exit(1)

        # Check eligibility
        eligibility = get_article_eligibility(args.article_id, args.platform)
        if not eligibility['eligible']:
            logger.warning(f"Article not eligible: {eligibility['reason']}")
            if not args.dry_run:
                logger.error("Use --dry-run to simulate posting anyway")
                sys.exit(1)
    else:
        article = select_best_article(args.platform)
        if not article:
            logger.warning(f"No eligible articles found for {args.platform}")
            sys.exit(0)

    logger.info(f"Selected article: {article['title']}")
    logger.info(f"  ID: {article['id']}, Score: {article['score']}, Words: {article['word_count']}")
    logger.info(f"  URL: {article['url']}")

    # Publish
    logger.info(f"Publishing to {args.platform}...")

    if args.platform == 'twitter':
        # Use thread generator for Twitter
        force_format = None if args.format == 'auto' else args.format
        previous_hooks = get_previous_accroches(article['id'], 'twitter')

        content = generate_twitter_content(
            title=article['title'],
            excerpt=article['excerpt'],
            word_count=article['word_count'],
            score=article['score'],
            previous_hooks=previous_hooks,
            force_format=force_format
        )

        logger.info(f"Format: {content['format']} ({content['posts_count']} tweet(s))")
        for i, tweet in enumerate(content['tweets']):
            logger.info(f"  Tweet {i+1}: {tweet[:80]}{'...' if len(tweet) > 80 else ''}")

        if content['type'] == 'thread':
            # Post as thread
            result = post_thread(
                tweets=content['tweets'],
                article_url=article['url'],
                image_url=article.get('image_url'),
                dry_run=args.dry_run
            )
            success = result['success']

            # Record the repost (skip for dry run)
            if not args.dry_run:
                # Store first tweet as accroche for history
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
                dry_run=args.dry_run
            )
            success = result['success']

            # Record the repost (skip for dry run)
            if not args.dry_run:
                add_repost(
                    article_id=article['id'],
                    platform='twitter',
                    accroche=content['tweets'][0],
                    success=success,
                    error_message=result.get('error'),
                    format='simple',
                    posts_count=1
                )
    else:
        # Use Ayrshare for Facebook (generate simple accroche)
        logger.info("Generating accroche...")
        accroche = generate_accroche(
            article_id=article['id'],
            title=article['title'],
            excerpt=article['excerpt'],
            platform=args.platform
        )
        logger.info(f"Accroche: {accroche}")

        success = publish_to_facebook(
            article_id=article['id'],
            article_url=article['url'],
            accroche=accroche,
            platform=args.platform,
            image_url=article.get('image_url'),
            dry_run=args.dry_run
        )

    if success:
        logger.info("Post successful!")
    else:
        logger.error("Post failed!")
        sys.exit(1)

    logger.info("=" * 50)


if __name__ == '__main__':
    main()
