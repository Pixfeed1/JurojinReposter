"""
Selector module for choosing the best article to post.
"""

from datetime import datetime, timedelta
from typing import Optional

import yaml

from config.settings import BASE_DIR
from core.database import get_all_articles, get_last_repost


def load_scoring_config() -> dict:
    """Load scoring configuration from YAML file."""
    config_path = BASE_DIR / "config" / "scoring.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def select_best_article(platform: str) -> Optional[dict]:
    """
    Select the best article to post on a given platform.

    Criteria:
    - Article must not be excluded
    - Article must not have been reposted on this platform within min_repost_interval days
    - Returns the article with the highest score
    """
    config = load_scoring_config()
    min_interval = config['min_repost_interval'].get(platform, 90)

    articles = get_all_articles(include_excluded=False)

    eligible_articles = []

    for article in articles:
        # Check if article was reposted recently on this platform
        last_repost = get_last_repost(article['id'], platform)

        if last_repost:
            repost_date = datetime.fromisoformat(last_repost['posted_at'])
            if datetime.now() - repost_date < timedelta(days=min_interval):
                # Article was reposted too recently
                continue

        eligible_articles.append(article)

    if not eligible_articles:
        return None

    # Sort by score descending
    eligible_articles.sort(key=lambda a: a['score'], reverse=True)

    return eligible_articles[0]


def get_top_articles(platform: str, limit: int = 10) -> list:
    """
    Get the top N eligible articles for a platform.

    Returns a list of articles sorted by score, excluding those
    that were reposted recently.
    """
    config = load_scoring_config()
    min_interval = config['min_repost_interval'].get(platform, 90)

    articles = get_all_articles(include_excluded=False)

    eligible_articles = []

    for article in articles:
        last_repost = get_last_repost(article['id'], platform)

        days_since_repost = None
        if last_repost:
            repost_date = datetime.fromisoformat(last_repost['posted_at'])
            days_since_repost = (datetime.now() - repost_date).days

            if days_since_repost < min_interval:
                continue

        article['days_since_repost'] = days_since_repost
        eligible_articles.append(article)

    eligible_articles.sort(key=lambda a: a['score'], reverse=True)

    return eligible_articles[:limit]


def select_article_for_bluesky() -> Optional[dict]:
    """
    Select an article for Bluesky.

    Unlike Twitter, Bluesky includes ALL articles (not just evergreen)
    with a shorter repost interval (30 days).

    Uses random selection among eligible articles to add variety.
    """
    import random

    config = load_scoring_config()
    min_interval = config['min_repost_interval'].get('bluesky', 30)

    articles = get_all_articles(include_excluded=False)

    eligible_articles = []

    for article in articles:
        # Check if article was reposted recently on Bluesky
        last_repost = get_last_repost(article['id'], 'bluesky')

        if last_repost:
            repost_date = datetime.fromisoformat(last_repost['posted_at'])
            if datetime.now() - repost_date < timedelta(days=min_interval):
                continue

        eligible_articles.append(article)

    if not eligible_articles:
        return None

    # Weight selection by score (higher score = more likely to be selected)
    weights = [max(a['score'], 1) for a in eligible_articles]
    selected = random.choices(eligible_articles, weights=weights, k=1)[0]

    return selected


def get_article_eligibility(article_id: int, platform: str) -> dict:
    """
    Check if an article is eligible for posting on a platform.

    Returns a dict with:
    - eligible: bool
    - reason: str (if not eligible)
    - days_until_eligible: int (if not eligible due to timing)
    """
    from core.database import get_article_by_id

    config = load_scoring_config()
    min_interval = config['min_repost_interval'].get(platform, 90)

    article = get_article_by_id(article_id)

    if not article:
        return {'eligible': False, 'reason': 'Article not found'}

    if article['excluded']:
        return {'eligible': False, 'reason': 'Article is excluded'}

    if article['score'] == 0:
        return {'eligible': False, 'reason': 'Article has score of 0 (may be in excluded category)'}

    last_repost = get_last_repost(article_id, platform)

    if last_repost:
        repost_date = datetime.fromisoformat(last_repost['posted_at'])
        days_since = (datetime.now() - repost_date).days

        if days_since < min_interval:
            days_until = min_interval - days_since
            return {
                'eligible': False,
                'reason': f'Reposted {days_since} days ago on {platform}',
                'days_until_eligible': days_until
            }

    return {'eligible': True}
