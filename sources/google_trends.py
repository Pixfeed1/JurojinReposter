"""
Google Trends data source.
Fetches trending topics for predefined keywords and general trends in France.
"""

import logging
import random
import time
from typing import Optional

import yaml
from pytrends.request import TrendReq

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

# Rate limiting - Google is aggressive with 429 errors
BASE_DELAY = 5  # Base seconds between requests
MAX_DELAY = 60  # Maximum delay after backoff
JITTER = 2  # Random jitter to add (0 to JITTER seconds)

# Track rate limiting state
_rate_limit_state = {
    'consecutive_errors': 0,
    'last_request_time': 0,
    'backoff_until': 0
}


def _wait_for_rate_limit():
    """Wait appropriate time before making a request."""
    now = time.time()

    # Check if we're in backoff period
    if now < _rate_limit_state['backoff_until']:
        wait_time = _rate_limit_state['backoff_until'] - now
        logger.info(f"Rate limit backoff: waiting {wait_time:.1f}s")
        time.sleep(wait_time)

    # Calculate delay based on consecutive errors
    delay = BASE_DELAY * (2 ** min(_rate_limit_state['consecutive_errors'], 4))
    delay = min(delay, MAX_DELAY)
    delay += random.uniform(0, JITTER)

    # Ensure minimum time between requests
    time_since_last = now - _rate_limit_state['last_request_time']
    if time_since_last < delay:
        sleep_time = delay - time_since_last
        time.sleep(sleep_time)

    _rate_limit_state['last_request_time'] = time.time()


def _handle_rate_limit_error():
    """Handle a 429 rate limit error."""
    _rate_limit_state['consecutive_errors'] += 1

    # Exponential backoff: 10s, 20s, 40s, 80s, max 120s
    backoff = min(10 * (2 ** _rate_limit_state['consecutive_errors']), 120)
    _rate_limit_state['backoff_until'] = time.time() + backoff

    logger.warning(f"Rate limited! Backing off for {backoff}s (error #{_rate_limit_state['consecutive_errors']})")


def _handle_success():
    """Reset rate limit state on successful request."""
    if _rate_limit_state['consecutive_errors'] > 0:
        _rate_limit_state['consecutive_errors'] = max(0, _rate_limit_state['consecutive_errors'] - 1)


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

    # Process in batches of 3 (reduced from 5 to be gentler on rate limits)
    batch_size = 3
    for i in range(0, len(keywords), batch_size):
        batch = keywords[i:i+batch_size]

        # Wait for rate limit
        _wait_for_rate_limit()

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

            _handle_success()

        except Exception as e:
            error_str = str(e)
            if '429' in error_str or 'too many' in error_str.lower():
                _handle_rate_limit_error()
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
    _wait_for_rate_limit()

    try:
        df = pytrends.trending_searches(pn='france')
        if df is not None and not df.empty:
            trends = df[0].tolist()
            logger.info(f"Found {len(trends)} trending searches in France")
            _handle_success()
            return trends
    except Exception as e:
        error_str = str(e)
        if '429' in error_str or 'too many' in error_str.lower():
            _handle_rate_limit_error()
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
    _wait_for_rate_limit()

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
            _handle_success()
            return trends
    except Exception as e:
        error_str = str(e)
        if '429' in error_str or 'too many' in error_str.lower():
            _handle_rate_limit_error()
        logger.warning(f"Error fetching realtime trends: {e}")

    return []


def get_related_queries(keyword: str) -> dict:
    """
    Get related queries for a keyword.

    Returns:
        Dict with 'top' and 'rising' lists of related queries
    """
    pytrends = get_pytrends_client()
    _wait_for_rate_limit()

    try:
        pytrends.build_payload([keyword], geo='FR', timeframe='now 7-d')
        related = pytrends.related_queries()

        result = {'top': [], 'rising': []}

        if keyword in related:
            if related[keyword]['top'] is not None:
                result['top'] = related[keyword]['top']['query'].tolist()[:10]
            if related[keyword]['rising'] is not None:
                result['rising'] = related[keyword]['rising']['query'].tolist()[:10]

        _handle_success()
        return result

    except Exception as e:
        error_str = str(e)
        if '429' in error_str or 'too many' in error_str.lower():
            _handle_rate_limit_error()
        logger.warning(f"Error fetching related queries for '{keyword}': {e}")
        return {'top': [], 'rising': []}


def fetch_predefined_trends(force_full: bool = False) -> list:
    """
    Fetch trends for predefined keywords in config.

    Args:
        force_full: If True, use full keywords list even if mode is 'core'

    Returns:
        List of dicts: {'keyword': str, 'category': str, 'interest': int}
    """
    config = load_trends_config()
    mode = config.get('mode', 'core')

    trending = []

    # Use core keywords (single list) or full keywords (by category)
    if mode == 'core' and not force_full:
        keywords_list = config.get('keywords_core', [])
        if keywords_list:
            logger.info(f"Checking {len(keywords_list)} core keywords...")
            interests = get_interest_over_time(keywords_list)

            for keyword, interest in interests.items():
                if interest >= 20:
                    trending.append({
                        'keyword': keyword,
                        'category': 'core',
                        'interest': interest,
                        'type': 'predefined'
                    })
                    logger.info(f"  {keyword}: {interest}")
    else:
        # Full mode: check by category
        keywords_config = config.get('keywords', {})
        for category, keywords in keywords_config.items():
            logger.info(f"Checking trends for category: {category}")

            interests = get_interest_over_time(keywords)

            for keyword, interest in interests.items():
                if interest >= 20:
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


def fetch_all_trends(force_full: bool = False) -> dict:
    """
    Fetch both predefined and general trends.

    Args:
        force_full: If True, use full keywords list (more API calls)

    Returns:
        {
            'predefined': [...],  # Trends from our keyword list
            'general': [...]      # General trending topics in France
        }
    """
    mode = "full" if force_full else "core"
    logger.info(f"Fetching Google Trends data (mode: {mode})...")

    return {
        'predefined': fetch_predefined_trends(force_full=force_full),
        'general': fetch_general_trends()
    }
