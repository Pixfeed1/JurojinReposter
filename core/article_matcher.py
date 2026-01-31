"""
Article matcher for trend-to-content matching.
Bidirectional matching: trends → articles and articles → trends.
"""

import logging
from typing import Optional

import yaml

from config.settings import BASE_DIR
from core.content_indexer import (
    get_article_index,
    search_articles_by_keyword,
    extract_keywords_from_text
)

logger = logging.getLogger(__name__)

# Try to import rapidfuzz for better matching, fall back to basic matching
try:
    from rapidfuzz import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    logger.warning("rapidfuzz not installed, using basic matching")


def load_trends_config() -> dict:
    """Load trends configuration."""
    config_path = BASE_DIR / "config" / "trends_config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def calculate_similarity(text1: str, text2: str) -> int:
    """
    Calculate similarity score between two texts.

    Returns score 0-100.
    """
    if FUZZY_AVAILABLE:
        return fuzz.token_set_ratio(text1.lower(), text2.lower())
    else:
        # Basic matching: check if one contains the other
        t1, t2 = text1.lower(), text2.lower()
        if t1 in t2 or t2 in t1:
            return 90
        # Word overlap
        words1 = set(t1.split())
        words2 = set(t2.split())
        overlap = len(words1 & words2)
        total = len(words1 | words2)
        return int((overlap / total) * 100) if total > 0 else 0


def match_trend_to_articles(trend_keyword: str, articles: list = None,
                            min_score: int = 60, max_results: int = 5) -> list:
    """
    Find articles related to a trending keyword.

    Args:
        trend_keyword: The trending search term
        articles: Optional pre-loaded article index
        min_score: Minimum similarity score (0-100)
        max_results: Maximum number of matches to return

    Returns:
        List of matching articles with similarity scores
    """
    if articles is None:
        articles = get_article_index()

    matches = []

    # Extract keywords from trend for better matching
    trend_words = set(trend_keyword.lower().split())

    for article in articles:
        # Calculate similarity with title
        title_score = calculate_similarity(trend_keyword, article['title'])

        # Check keyword overlap
        article_keywords = {kw.lower() for kw, _ in article.get('keywords', [])}
        keyword_overlap = len(trend_words & article_keywords)

        # Boost score for keyword matches
        boost = keyword_overlap * 15

        # Check category relevance
        category = article.get('category', '').lower()
        if any(word in category for word in trend_words):
            boost += 20

        final_score = min(100, title_score + boost)

        if final_score >= min_score:
            matches.append({
                'article': article,
                'similarity_score': final_score,
                'match_type': 'title' if title_score >= min_score else 'keywords'
            })

    # Sort by score descending
    matches.sort(key=lambda x: x['similarity_score'], reverse=True)

    return matches[:max_results]


def find_opportunities(predefined_trends: list, general_trends: list) -> list:
    """
    Find content opportunities by matching trends with existing articles.

    Args:
        predefined_trends: Trends from our keyword list
        general_trends: General trending topics in France

    Returns:
        List of opportunities: {
            'trend': str,
            'trend_type': 'predefined' | 'general',
            'interest': int (for predefined),
            'related_articles': [...],
            'opportunity_type': 'new_article' | 'update' | 'trending_topic',
            'score': int (opportunity score)
        }
    """
    config = load_trends_config()
    min_similarity = config.get('matching', {}).get('min_similarity_score', 60)
    max_articles = config.get('matching', {}).get('max_related_articles', 5)

    # Pre-load article index for efficiency
    articles = get_article_index()

    opportunities = []

    # 1. Process predefined trends (our keywords that are trending)
    for trend in predefined_trends:
        keyword = trend['keyword']
        interest = trend.get('interest', 50)

        # Find related articles
        related = match_trend_to_articles(keyword, articles, min_similarity, max_articles)

        if related:
            # We have content, opportunity to update or leverage
            opp_type = 'update' if len(related) >= 3 else 'trending_topic'
            score = int(interest * 0.7 + len(related) * 10)
        else:
            # No content, opportunity for new article
            opp_type = 'new_article'
            score = int(interest * 0.8)

        opportunities.append({
            'trend': keyword,
            'trend_type': 'predefined',
            'category': trend.get('category', ''),
            'interest': interest,
            'related_articles': [m['article'] for m in related],
            'opportunity_type': opp_type,
            'score': score
        })

    # 2. Process general trends (match against our content)
    for trend in general_trends:
        keyword = trend['keyword']

        # Skip very short or generic terms
        if len(keyword) < 4:
            continue

        # Find related articles
        related = match_trend_to_articles(keyword, articles, min_similarity, max_articles)

        if related:
            # General trend matches our content = high value opportunity
            score = 70 + len(related) * 10

            opportunities.append({
                'trend': keyword,
                'trend_type': 'general',
                'source': trend.get('source', 'trending'),
                'related_articles': [m['article'] for m in related],
                'opportunity_type': 'trending_topic',
                'score': score
            })

    # Sort by opportunity score
    opportunities.sort(key=lambda x: x['score'], reverse=True)

    logger.info(f"Found {len(opportunities)} content opportunities")
    return opportunities


def get_best_opportunities(opportunities: list, max_count: int = 3) -> list:
    """
    Select the best opportunities for article generation.

    Prioritizes:
    1. High interest predefined trends with existing content
    2. General trends matching our content
    3. New article opportunities for trending keywords

    Args:
        opportunities: Full list of opportunities
        max_count: Maximum number to return

    Returns:
        Top opportunities for prompt generation
    """
    if not opportunities:
        return []

    # Filter out low-score opportunities
    filtered = [o for o in opportunities if o['score'] >= 50]

    # Prefer diversity: don't pick all from same category
    selected = []
    categories_used = set()

    for opp in filtered:
        category = opp.get('category', opp.get('trend', ''))

        # Skip if we already have too many from this category
        if category in categories_used and len(selected) < max_count - 1:
            continue

        selected.append(opp)
        categories_used.add(category)

        if len(selected) >= max_count:
            break

    return selected


def generate_anchor_variations(keyword: str, article_title: str) -> list:
    """
    Generate SEO anchor text variations for a keyword.

    Returns list of anchor texts from exact match to long-tail variations.
    """
    anchors = []

    # 1. Exact match (if keyword is substantial)
    if len(keyword) >= 5:
        anchors.append(keyword)

    # 2. Title-based variation
    title_words = article_title.lower().split()
    keyword_words = keyword.lower().split()

    # Find overlapping concepts
    for word in keyword_words:
        if len(word) >= 4:
            for i, tw in enumerate(title_words):
                if word in tw or tw in word:
                    # Create phrase from title
                    start = max(0, i - 1)
                    end = min(len(title_words), i + 2)
                    phrase = ' '.join(title_words[start:end])
                    if phrase not in anchors and len(phrase) > 5:
                        anchors.append(phrase)
                    break

    # 3. Long-tail variations
    long_tail_prefixes = ['tutoriel', 'guide', 'apprendre', 'comprendre', 'maitriser']
    for prefix in long_tail_prefixes:
        variation = f"{prefix} {keyword}"
        if variation not in anchors:
            anchors.append(variation)
            break

    return anchors[:3]  # Return max 3 variations
