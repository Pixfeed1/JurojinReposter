"""
CLI command for syncing articles from WordPress.

Usage:
    python -m cli.sync          # Incremental sync (recent articles)
    python -m cli.sync --full   # Full sync (all articles)
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter
from config.settings import LOG_PATH, WORDPRESS_POST_TYPES
from core.database import init_database, upsert_article
from core.scoring import recalculate_all_scores
from sources.wordpress import fetch_articles


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
    parser = argparse.ArgumentParser(description='Sync articles from WordPress')
    parser.add_argument('--full', action='store_true',
                        help='Perform full sync (all articles)')
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("Starting WordPress sync...")
    logger.info(f"Mode: {'Full' if args.full else 'Incremental'}")

    # Initialize database
    init_database()

    # Fetch articles
    try:
        articles = fetch_articles(full_sync=args.full)
    except Exception as e:
        logger.error(f"Error fetching articles: {e}")
        sys.exit(1)

    if not articles:
        logger.warning("No articles fetched")
        sys.exit(0)

    # Insert/update articles
    inserted = 0
    updated = 0
    by_type = Counter()

    for article in articles:
        try:
            from core.database import get_article_by_wp_id
            existing = get_article_by_wp_id(article['wp_id'])

            upsert_article(article)
            by_type[article.get('post_type', 'posts')] += 1

            if existing:
                updated += 1
            else:
                inserted += 1

        except Exception as e:
            logger.error(f"Error saving article {article.get('wp_id')}: {e}")

    # Build post type name mapping
    post_type_names = {pt['endpoint']: pt['name'] for pt in WORDPRESS_POST_TYPES}

    logger.info(f"Inserted: {inserted}, Updated: {updated}")
    logger.info("By type:")
    for post_type, count in by_type.items():
        type_name = post_type_names.get(post_type, post_type)
        logger.info(f"  {type_name}: {count}")

    # Recalculate scores
    logger.info("Recalculating scores...")
    count = recalculate_all_scores()
    logger.info(f"Scores updated for {count} articles")

    logger.info("Sync completed successfully")
    logger.info("=" * 50)


if __name__ == '__main__':
    main()
