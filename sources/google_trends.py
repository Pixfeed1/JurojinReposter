"""
Google Trends data source.
Fetches trending topics for predefined keywords and general trends in France.
"""

import logging
import time
from typing import Optional

import yaml
from pytrends.request import TrendReq

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

# Rate limiting
REQUEST_DELAY = 2  # seconds between requests to avoid 429 errors


def load_trends_config() -> dict:
    """Load trends configuration."""
    config_path = BASE_DIR / "config" / "trends_config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_pytrends_client() -> TrendReq:
    """Get a pytrends client configured for France."""
    return TrendReq(
        hl='fr-FR',
        tz=60,  # UTC+1 (France)
        timeout=(10, 25),
        retries=3,
        backoff_factor=0.5
    )


def get_interest_over_time(keywords: list, timeframe: str = 'now 7-d') -> dict:
    """
    Get interest over time for a list of keywords.

    Args:
        keywords: List of keywords (max 5 per request)
        timeframe: Timeframe string (e.g., 'now 7-d', 'today 1-m')

    Returns:
        Dict with keyword -> interest_score (0-100)
    """
    if not keywords:
        return {}

    pytrends = get_pytrends_client()
    results = {}

    # Process in batches of 5 (pytrends limit)
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i+5]
        try:
            pytrends.build_payload(
                kw_list=batch,
                cat=0,
                timeframe=timeframe,
                geo='FR'
            )

            df = pytrends.interest_over_time()

            if not df.empty:
                for keyword in batch:
                    if keyword in df.columns:
                        # Get average interest over the period
                        avg_interest = df[keyword].mean()
                        results[keyword] = int(avg_interest)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.warning(f"Error fetching trends for {batch}: {e}")
            continue

    return results


def get_trending_searches_fr() -> list:
    """
    Get currently trending searches in France.

    Returns:
        List of trending search terms
    """
    pytrends = get_pytrends_client()

    try:
        df = pytrends.trending_searches(pn='france')
        if df is not None and not df.empty:
            trends = df[0].tolist()
            logger.info(f"Found {len(trends)} trending searches in France")
            return trends
    except Exception as e:
        logger.warning(f"Error fetching trending searches: {e}")

    return []


def get_realtime_trends(category: str = 'all') -> list:
    """
    Get realtime trending stories.

    Args:
        category: Category filter ('all', 'technology', 'entertainment', 'games', etc.)

    Returns:
        List of dicts with title and related_queries
    """
    pytrends = get_pytrends_client()

    # Category mapping for Google Trends
    category_map = {
        'all': 'all',
        'technology': 't',
        'entertainment': 'e',
        'games': 'g',
        'business': 'b',
        'science': 's'
    }

    cat = category_map.get(category, 'all')

    try:
        df = pytrends.realtime_trending_searches(pn='FR', cat=cat)
        if df is not None and not df.empty:
            trends = []
            for _, row in df.iterrows():
                trends.append({
                    'title': row.get('title', ''),
                    'entity_names': row.get('entityNames', [])
                })
            logger.info(f"Found {len(trends)} realtime trends for category '{category}'")
            return trends
    except Exception as e:
        logger.warning(f"Error fetching realtime trends: {e}")

    return []


def get_related_queries(keyword: str) -> dict:
    """
    Get related queries for a keyword.

    Returns:
        Dict with 'top' and 'rising' lists of related queries
    """
    pytrends = get_pytrends_client()

    try:
        pytrends.build_payload([keyword], geo='FR', timeframe='now 7-d')
        related = pytrends.related_queries()

        result = {'top': [], 'rising': []}

        if keyword in related:
            if related[keyword]['top'] is not None:
                result['top'] = related[keyword]['top']['query'].tolist()[:10]
            if related[keyword]['rising'] is not None:
                result['rising'] = related[keyword]['rising']['query'].tolist()[:10]

        time.sleep(REQUEST_DELAY)
        return result

    except Exception as e:
        logger.warning(f"Error fetching related queries for '{keyword}': {e}")
        return {'top': [], 'rising': []}


def fetch_predefined_trends() -> list:
    """
    Fetch trends for all predefined keywords in config.

    Returns:
        List of dicts: {'keyword': str, 'category': str, 'interest': int}
    """
    config = load_trends_config()
    keywords_config = config.get('keywords', {})

    trending = []

    for category, keywords in keywords_config.items():
        logger.info(f"Checking trends for category: {category}")

        interests = get_interest_over_time(keywords)

        for keyword, interest in interests.items():
            if interest >= 20:  # Only include if there's meaningful interest
                trending.append({
                    'keyword': keyword,
                    'category': category,
                    'interest': interest,
                    'type': 'predefined'
                })
                logger.info(f"  {keyword}: {interest}")

    # Sort by interest descending
    trending.sort(key=lambda x: x['interest'], reverse=True)
    return trending


def fetch_general_trends() -> list:
    """
    Fetch general trending topics in France.

    Returns:
        List of dicts: {'keyword': str, 'type': 'general'}
    """
    trends = []

    # Daily trending searches
    trending_searches = get_trending_searches_fr()
    for search in trending_searches[:20]:  # Limit to top 20
        trends.append({
            'keyword': search,
            'type': 'general',
            'source': 'trending_searches'
        })

    # Realtime trends by category
    config = load_trends_config()
    categories = config.get('general_categories', ['technology', 'entertainment'])

    for category in categories:
        realtime = get_realtime_trends(category)
        for trend in realtime[:10]:
            trends.append({
                'keyword': trend['title'],
                'type': 'general',
                'source': f'realtime_{category}',
                'entities': trend.get('entity_names', [])
            })

    return trends


def fetch_all_trends() -> dict:
    """
    Fetch both predefined and general trends.

    Returns:
        {
            'predefined': [...],  # Trends from our keyword list
            'general': [...]      # General trending topics in France
        }
    """
    logger.info("Fetching Google Trends data...")

    return {
        'predefined': fetch_predefined_trends(),
        'general': fetch_general_trends()
    }
