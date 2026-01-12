"""
CLI command for posting to social media platforms.

Usage:
    python -m cli.post --platform twitter
    python -m cli.post --platform facebook
    python -m cli.post --platform twitter --dry-run
    python -m cli.post --platform twitter --article-id 123
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH
from core.database import init_database, get_article_by_id
from core.selector import select_best_article, get_article_eligibility
from ai.groq_client import generate_accroche
from publishers.ayrshare import publish_article


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
    logger.info(f"  ID: {article['id']}, Score: {article['score']}")
    logger.info(f"  URL: {article['url']}")

    # Generate accroche
    logger.info("Generating accroche...")
    accroche = generate_accroche(
        article_id=article['id'],
        title=article['title'],
        excerpt=article['excerpt'],
        platform=args.platform
    )
    logger.info(f"Accroche: {accroche}")

    # Publish
    logger.info(f"Publishing to {args.platform}...")
    success = publish_article(
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
