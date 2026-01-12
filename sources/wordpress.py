"""
WordPress source module for fetching articles via WP REST API.
"""

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config.settings import WORDPRESS_URL, WORDPRESS_PER_PAGE
from core.scoring import is_evergreen_category

logger = logging.getLogger(__name__)


def strip_html(html: str) -> str:
    """Remove HTML tags from a string."""
    if not html:
        return ""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(separator=' ', strip=True)


def count_words(text: str) -> int:
    """Count words in a text string."""
    if not text:
        return 0
    # Remove extra whitespace and split
    words = text.split()
    return len(words)


def fetch_categories() -> dict:
    """
    Fetch all categories from WordPress.

    Returns:
        dict mapping category ID to category slug
    """
    categories = {}
    page = 1
    per_page = 100

    while True:
        try:
            response = requests.get(
                f"{WORDPRESS_URL}/categories",
                params={'per_page': per_page, 'page': page},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            if not data:
                break

            for cat in data:
                categories[cat['id']] = cat['slug']

            if len(data) < per_page:
                break

            page += 1

        except requests.RequestException as e:
            logger.error(f"Error fetching categories page {page}: {e}")
            break

    return categories


def fetch_media(media_id: int) -> Optional[str]:
    """
    Fetch media URL from WordPress.

    Args:
        media_id: The WordPress media ID

    Returns:
        URL of the large image, or None if not found
    """
    if not media_id:
        return None

    try:
        response = requests.get(
            f"{WORDPRESS_URL}/media/{media_id}",
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        # Try to get large size, fall back to full
        sizes = data.get('media_details', {}).get('sizes', {})
        if 'large' in sizes:
            return sizes['large']['source_url']
        elif 'full' in sizes:
            return sizes['full']['source_url']
        else:
            return data.get('source_url')

    except requests.RequestException as e:
        logger.warning(f"Error fetching media {media_id}: {e}")
        return None


def fetch_articles(per_page: int = WORDPRESS_PER_PAGE,
                   full_sync: bool = False) -> list:
    """
    Fetch articles from WordPress API.

    Args:
        per_page: Number of articles per page
        full_sync: If True, fetch all articles. If False, fetch only recent ones.

    Returns:
        List of article dictionaries ready for database insertion
    """
    # First, fetch all categories
    categories = fetch_categories()
    logger.info(f"Fetched {len(categories)} categories")

    articles = []
    page = 1
    max_pages = 100 if full_sync else 5  # Limit pages for incremental sync

    while page <= max_pages:
        try:
            logger.info(f"Fetching articles page {page}...")
            response = requests.get(
                f"{WORDPRESS_URL}/posts",
                params={
                    'per_page': per_page,
                    'page': page,
                    'status': 'publish',
                    '_fields': 'id,title,excerpt,link,date,categories,featured_media,content'
                },
                timeout=60
            )

            if response.status_code == 400:
                # No more pages
                logger.info(f"Reached end of articles at page {page}")
                break

            response.raise_for_status()
            data = response.json()

            if not data:
                break

            for post in data:
                # Get primary category (first one)
                post_categories = post.get('categories', [])
                primary_category = ''
                if post_categories:
                    cat_id = post_categories[0]
                    primary_category = categories.get(cat_id, '')

                # Strip HTML from excerpt and content
                excerpt = strip_html(post.get('excerpt', {}).get('rendered', ''))
                content = strip_html(post.get('content', {}).get('rendered', ''))
                title = strip_html(post.get('title', {}).get('rendered', ''))

                # Calculate word count from content
                word_count = count_words(content)

                # Fetch featured image
                image_url = None
                featured_media = post.get('featured_media')
                if featured_media:
                    image_url = fetch_media(featured_media)

                article = {
                    'wp_id': post['id'],
                    'url': post['link'],
                    'title': title,
                    'excerpt': excerpt[:500] if excerpt else '',  # Limit excerpt length
                    'category': primary_category,
                    'image_url': image_url,
                    'word_count': word_count,
                    'published_at': post['date'],
                    'is_evergreen': is_evergreen_category(primary_category)
                }

                articles.append(article)

            logger.info(f"Fetched {len(data)} articles from page {page}")

            if len(data) < per_page:
                break

            page += 1

        except requests.RequestException as e:
            logger.error(f"Error fetching articles page {page}: {e}")
            break

    logger.info(f"Total articles fetched: {len(articles)}")
    return articles


def fetch_single_article(post_id: int) -> Optional[dict]:
    """
    Fetch a single article by its WordPress ID.

    Args:
        post_id: The WordPress post ID

    Returns:
        Article dictionary or None if not found
    """
    categories = fetch_categories()

    try:
        response = requests.get(
            f"{WORDPRESS_URL}/posts/{post_id}",
            timeout=30
        )
        response.raise_for_status()
        post = response.json()

        # Get primary category
        post_categories = post.get('categories', [])
        primary_category = ''
        if post_categories:
            cat_id = post_categories[0]
            primary_category = categories.get(cat_id, '')

        # Strip HTML
        excerpt = strip_html(post.get('excerpt', {}).get('rendered', ''))
        content = strip_html(post.get('content', {}).get('rendered', ''))
        title = strip_html(post.get('title', {}).get('rendered', ''))

        # Calculate word count
        word_count = count_words(content)

        # Fetch featured image
        image_url = None
        featured_media = post.get('featured_media')
        if featured_media:
            image_url = fetch_media(featured_media)

        return {
            'wp_id': post['id'],
            'url': post['link'],
            'title': title,
            'excerpt': excerpt[:500] if excerpt else '',
            'category': primary_category,
            'image_url': image_url,
            'word_count': word_count,
            'published_at': post['date'],
            'is_evergreen': is_evergreen_category(primary_category)
        }

    except requests.RequestException as e:
        logger.error(f"Error fetching article {post_id}: {e}")
        return None
