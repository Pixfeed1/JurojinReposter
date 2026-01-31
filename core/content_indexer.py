"""
Content indexer for extracting keywords from existing articles.
Creates a searchable index of article content for trend matching.
"""

import logging
import re
from collections import Counter
from typing import Optional

from core.database import get_connection

logger = logging.getLogger(__name__)

# French stop words to exclude
STOP_WORDS = {
    'le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'et', 'en', 'au', 'aux',
    'ce', 'ces', 'cette', 'cet', 'qui', 'que', 'quoi', 'dont', 'ou', 'mais',
    'donc', 'car', 'ni', 'ne', 'pas', 'plus', 'moins', 'tres', 'bien', 'mal',
    'pour', 'par', 'sur', 'sous', 'avec', 'sans', 'dans', 'entre', 'vers',
    'chez', 'avant', 'apres', 'pendant', 'depuis', 'jusqu', 'comme', 'si',
    'son', 'sa', 'ses', 'leur', 'leurs', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes',
    'notre', 'nos', 'votre', 'vos', 'il', 'elle', 'ils', 'elles', 'on', 'nous',
    'vous', 'je', 'tu', 'se', 'lui', 'eux', 'y', 'tout', 'tous', 'toute',
    'toutes', 'autre', 'autres', 'meme', 'memes', 'quel', 'quelle', 'quels',
    'quelles', 'peu', 'beaucoup', 'trop', 'assez', 'aussi', 'encore', 'deja',
    'jamais', 'toujours', 'souvent', 'parfois', 'ici', 'est', 'sont', 'ete',
    'etre', 'avoir', 'fait', 'faire', 'peut', 'peuvent', 'doit', 'doivent',
    'faut', 'article', 'articles', 'voir', 'alors', 'ainsi', 'comment',
    'pourquoi', 'quand', 'the', 'and', 'for', 'with', 'this', 'that', 'from',
    'vous', 'votre', 'vos', 'cest', "c'est", 'nan', 'non', 'oui'
}

# Minimum word length for keywords
MIN_WORD_LENGTH = 3


def extract_keywords_from_text(text: str, max_keywords: int = 20) -> list:
    """
    Extract keywords from a text string.

    Returns list of (keyword, count) tuples sorted by frequency.
    """
    if not text:
        return []

    # Normalize text
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\d+', '', text)

    # Split into words
    words = text.split()

    # Filter words
    filtered = [
        w for w in words
        if len(w) >= MIN_WORD_LENGTH
        and w not in STOP_WORDS
        and not w.isdigit()
    ]

    # Count frequencies
    counter = Counter(filtered)

    return counter.most_common(max_keywords)


def extract_article_keyword(article: dict) -> str:
    """
    Extract the main SEO keyword from an article.
    Based on title, category, and content analysis.

    Returns the primary keyword for anchor text purposes.
    """
    title = article.get('title', '')
    category = article.get('category', '')

    # Try to identify the main subject from title
    title_words = extract_keywords_from_text(title, max_keywords=5)

    if title_words:
        # Prefer multi-word phrases if category matches
        if category:
            category_lower = category.lower()
            for word, _ in title_words:
                if word in category_lower or category_lower in word:
                    return word

        # Otherwise return the most frequent meaningful word
        return title_words[0][0]

    return title[:30] if title else ''


def get_article_index() -> list:
    """
    Build an index of all articles with extracted keywords.

    Returns:
        List of dicts: {
            'id': int,
            'title': str,
            'url': str,
            'category': str,
            'primary_keyword': str,
            'keywords': [(keyword, count), ...],
            'score': int
        }
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, url, excerpt, category, score
        FROM articles
        WHERE excluded = 0 AND score > 0
        ORDER BY score DESC
    """)

    articles = []
    for row in cursor.fetchall():
        article = dict(row)

        # Combine title and excerpt for keyword extraction
        combined_text = f"{article['title']} {article.get('excerpt', '')}"
        keywords = extract_keywords_from_text(combined_text, max_keywords=15)

        article['keywords'] = keywords
        article['primary_keyword'] = extract_article_keyword(article)

        articles.append(article)

    conn.close()
    logger.info(f"Indexed {len(articles)} articles")
    return articles


def get_all_keywords() -> dict:
    """
    Get a mapping of all keywords to their articles.

    Returns:
        Dict: keyword -> [article_ids]
    """
    articles = get_article_index()
    keyword_map = {}

    for article in articles:
        for keyword, _ in article['keywords']:
            if keyword not in keyword_map:
                keyword_map[keyword] = []
            keyword_map[keyword].append(article['id'])

    return keyword_map


def search_articles_by_keyword(keyword: str, limit: int = 10) -> list:
    """
    Search articles containing a keyword in title or excerpt.

    Returns list of matching articles.
    """
    conn = get_connection()
    cursor = conn.cursor()

    keyword_pattern = f"%{keyword.lower()}%"

    cursor.execute("""
        SELECT id, title, url, excerpt, category, score
        FROM articles
        WHERE excluded = 0
        AND score > 0
        AND (LOWER(title) LIKE ? OR LOWER(excerpt) LIKE ?)
        ORDER BY score DESC
        LIMIT ?
    """, (keyword_pattern, keyword_pattern, limit))

    articles = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # Add primary keyword to each article
    for article in articles:
        article['primary_keyword'] = extract_article_keyword(article)

    return articles


def get_articles_by_category(category: str, limit: int = 10) -> list:
    """
    Get articles from a specific category.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, url, excerpt, category, score
        FROM articles
        WHERE excluded = 0
        AND score > 0
        AND LOWER(category) LIKE ?
        ORDER BY score DESC
        LIMIT ?
    """, (f"%{category.lower()}%", limit))

    articles = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for article in articles:
        article['primary_keyword'] = extract_article_keyword(article)

    return articles
