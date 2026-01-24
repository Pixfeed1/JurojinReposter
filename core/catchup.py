"""
Catchup module for finding articles never posted on Facebook.
Identifies WordPress articles that were published but never shared on Facebook.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from core.database import get_connection

logger = logging.getLogger(__name__)

# Don't catch up articles older than this
MAX_CATCHUP_AGE_DAYS = 30


def get_catchup_articles(limit: int = 2) -> list:
    """
    Find articles published on WordPress but never posted on Facebook.

    Rules:
    - Only articles not excluded
    - Only articles published within the last 30 days
    - Never posted on Facebook (no record in reposts with platform='facebook')
    - May have been posted on Twitter/Bluesky (that's fine)
    - Ordered by most recent first

    Args:
        limit: Maximum number of articles to return

    Returns:
        List of article dicts eligible for catchup
    """
    conn = get_connection()
    cursor = conn.cursor()

    cutoff_date = (datetime.now() - timedelta(days=MAX_CATCHUP_AGE_DAYS)).isoformat()

    cursor.execute("""
        SELECT a.* FROM articles a
        WHERE a.excluded = 0
        AND a.published_at >= ?
        AND a.score > 0
        AND a.id NOT IN (
            SELECT DISTINCT article_id FROM reposts
            WHERE platform = 'facebook' AND success = 1
        )
        ORDER BY a.published_at DESC
        LIMIT ?
    """, (cutoff_date, limit))

    rows = cursor.fetchall()
    conn.close()

    articles = [dict(row) for row in rows]
    if articles:
        logger.info(f"Found {len(articles)} catchup articles for Facebook")
        for a in articles:
            logger.info(f"  - ID {a['id']}: {a['title'][:50]}...")
    else:
        logger.info("No catchup articles found for Facebook")

    return articles


def get_catchup_count_today() -> int:
    """Get the number of catchup posts already made today."""
    from core.database import get_daily_count
    return get_daily_count('catchup')


def record_catchup_post() -> None:
    """Record that a catchup post was made today."""
    from core.database import increment_daily_count
    increment_daily_count('catchup')
