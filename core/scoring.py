"""
Scoring module for calculating article scores.
"""

from datetime import datetime, timedelta
from pathlib import Path

import yaml

from config.settings import BASE_DIR
from core.database import get_connection, get_last_repost, update_article_score


def load_scoring_config() -> dict:
    """Load scoring configuration from YAML file."""
    config_path = BASE_DIR / "config" / "scoring.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def calculate_score(article: dict, config: dict = None) -> int:
    """
    Calculate the score for an article based on various criteria.

    Scoring rules:
    - Evergreen category: +50
    - Article older than 6 months: +30
    - Never reposted: +40
    - Not reposted in 90 days: +30
    - Word count > 1000: +20
    - Priority boost: +boost value
    - Article published < 3 months: -30
    - Excluded category: score = 0
    - Excluded article: score = 0
    """
    if config is None:
        config = load_scoring_config()

    weights = config['weights']
    evergreen_categories = config['evergreen_categories']
    excluded_categories = config['excluded_categories']

    # Check if article is excluded
    if article.get('excluded'):
        return 0

    # Check if category is excluded
    category = article.get('category', '').lower()
    if category in [c.lower() for c in excluded_categories]:
        return 0

    score = 0

    # Evergreen category bonus
    if category in [c.lower() for c in evergreen_categories]:
        score += weights['evergreen_category']

    # Parse published date
    published_at = article.get('published_at')
    if published_at:
        if isinstance(published_at, str):
            try:
                published_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            except ValueError:
                published_date = datetime.strptime(published_at[:10], '%Y-%m-%d')
        else:
            published_date = published_at

        now = datetime.now(published_date.tzinfo) if published_date.tzinfo else datetime.now()

        # Article older than 6 months bonus
        if now - published_date > timedelta(days=180):
            score += weights['article_older_than_6_months']

        # Recent article penalty (< 3 months)
        if now - published_date < timedelta(days=90):
            score += weights['recent_article_penalty']

    # Word count bonus
    if article.get('word_count', 0) > 1000:
        score += weights['word_count_over_1000']

    # Priority boost
    score += article.get('priority_boost', 0)

    # Check repost history for both platforms
    article_id = article.get('id')
    if article_id:
        never_reposted = True

        for platform in ['twitter', 'facebook']:
            last_repost = get_last_repost(article_id, platform)
            if last_repost:
                never_reposted = False
                repost_date = datetime.fromisoformat(last_repost['posted_at'])
                now = datetime.now()
                min_interval = config['min_repost_interval'].get(platform, 90)

                if now - repost_date > timedelta(days=min_interval):
                    score += weights['not_reposted_90_days']

        if never_reposted:
            score += weights['never_reposted']

    return max(0, score)


def recalculate_all_scores() -> int:
    """Recalculate scores for all articles. Returns the number of articles updated."""
    config = load_scoring_config()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM articles")
    articles = [dict(row) for row in cursor.fetchall()]
    conn.close()

    count = 0
    for article in articles:
        score = calculate_score(article, config)
        update_article_score(article['id'], score)
        count += 1

    return count


def is_evergreen_category(category: str, config: dict = None) -> bool:
    """Check if a category is considered evergreen."""
    if config is None:
        config = load_scoring_config()

    return category.lower() in [c.lower() for c in config['evergreen_categories']]


def is_excluded_category(category: str, config: dict = None) -> bool:
    """Check if a category is excluded from reposting."""
    if config is None:
        config = load_scoring_config()

    return category.lower() in [c.lower() for c in config['excluded_categories']]
