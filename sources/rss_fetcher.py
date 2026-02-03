"""
RSS Feed Fetcher for JurojinReposter veille module.
Parses RSS feeds and stores articles in SQLite.
No external API calls, no risk of IP blocking.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

import feedparser
import yaml

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)


def load_feeds_config() -> dict:
    """Load RSS feeds configuration."""
    config_path = BASE_DIR / "config" / "rss_feeds.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def parse_feed(url: str, source_name: str, timeout: int = 30) -> list:
    """
    Parse a single RSS feed.

    Args:
        url: Feed URL
        source_name: Name of the source for logging
        timeout: Request timeout in seconds

    Returns:
        List of article dicts
    """
    articles = []

    try:
        logger.info(f"Fetching feed: {source_name}")
        feed = feedparser.parse(url, request_headers={
            'User-Agent': 'JurojinReposter/1.0 (RSS Reader)'
        })

        if feed.bozo and feed.bozo_exception:
            logger.warning(f"Feed parse warning for {source_name}: {feed.bozo_exception}")

        if not feed.entries:
            logger.warning(f"No entries found in feed: {source_name}")
            return []

        for entry in feed.entries:
            article = {
                'title': entry.get('title', '').strip(),
                'url': entry.get('link', ''),
                'summary': _clean_summary(entry.get('summary', entry.get('description', ''))),
                'published': _parse_date(entry),
                'source_name': source_name,
                'source_url': url,
                'author': entry.get('author', ''),
            }

            # Skip if no title or URL
            if article['title'] and article['url']:
                articles.append(article)

        logger.info(f"  Found {len(articles)} articles from {source_name}")

    except Exception as e:
        logger.error(f"Error fetching feed {source_name} ({url}): {e}")

    return articles


def _clean_summary(summary: str) -> str:
    """Clean HTML tags from summary."""
    import re
    if not summary:
        return ""
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', summary)
    # Normalize whitespace
    clean = ' '.join(clean.split())
    # Limit length
    if len(clean) > 500:
        clean = clean[:497] + "..."
    return clean


def _parse_date(entry) -> Optional[datetime]:
    """Parse publication date from feed entry."""
    # Try different date fields
    for date_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
        date_tuple = entry.get(date_field)
        if date_tuple:
            try:
                return datetime(*date_tuple[:6])
            except (TypeError, ValueError):
                continue

    # Fallback: use current time
    return datetime.now()


def fetch_all_feeds(categories: list = None) -> list:
    """
    Fetch all configured RSS feeds.

    Args:
        categories: Optional list of categories to fetch (None = all)

    Returns:
        List of all articles from all feeds
    """
    config = load_feeds_config()
    settings = config.get('settings', {})
    feeds_config = config.get('feeds', {})

    lookback_hours = settings.get('lookback_hours', 48)
    request_delay = settings.get('request_delay', 1)
    cutoff_date = datetime.now() - timedelta(hours=lookback_hours)

    all_articles = []
    feeds_processed = 0
    feeds_failed = 0

    for category, feeds in feeds_config.items():
        # Filter by category if specified
        if categories and category not in categories:
            continue

        logger.info(f"Processing category: {category}")

        for feed_info in feeds:
            name = feed_info.get('name', 'Unknown')
            url = feed_info.get('url', '')
            language = feed_info.get('language', 'en')
            priority = feed_info.get('priority', 'medium')

            if not url:
                continue

            articles = parse_feed(url, name)

            if articles:
                feeds_processed += 1
                # Add metadata to each article
                for article in articles:
                    article['category'] = category
                    article['language'] = language
                    article['priority'] = priority

                    # Filter by date
                    if article['published'] and article['published'] >= cutoff_date:
                        all_articles.append(article)
            else:
                feeds_failed += 1

            # Rate limiting
            time.sleep(request_delay)

    logger.info(f"Fetch complete: {feeds_processed} feeds OK, {feeds_failed} failed, {len(all_articles)} articles")
    return all_articles


def get_feed_stats() -> dict:
    """Get statistics about configured feeds."""
    config = load_feeds_config()
    feeds_config = config.get('feeds', {})

    stats = {
        'total_feeds': 0,
        'by_category': {},
        'by_priority': {'high': 0, 'medium': 0, 'low': 0},
        'by_language': {'fr': 0, 'en': 0}
    }

    for category, feeds in feeds_config.items():
        count = len(feeds)
        stats['total_feeds'] += count
        stats['by_category'][category] = count

        for feed in feeds:
            priority = feed.get('priority', 'medium')
            language = feed.get('language', 'en')
            stats['by_priority'][priority] = stats['by_priority'].get(priority, 0) + 1
            stats['by_language'][language] = stats['by_language'].get(language, 0) + 1

    return stats
