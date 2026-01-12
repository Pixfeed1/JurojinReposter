"""
WordPress source module for fetching articles via WP REST API.
"""

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config.settings import WORDPRESS_URL, WORDPRESS_PER_PAGE, WORDPRESS_POST_TYPES
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


def fetch_posts_by_type(endpoint: str, post_type_name: str, post_type_evergreen: bool,
                        categories: dict, per_page: int, max_pages: int) -> list:
    """
    Fetch posts of a specific type from WordPress API.

    Args:
        endpoint: The REST API endpoint (e.g., 'posts', 'guide', 'jeu')
        post_type_name: Human-readable name for logging
        post_type_evergreen: Whether this post type is evergreen by default
        categories: Dict mapping category IDs to slugs
        per_page: Number of posts per page
        max_pages: Maximum pages to fetch

    Returns:
        List of article dictionaries
    """
    articles = []
    page = 1

    while page <= max_pages:
        try:
            logger.info(f"Fetching {post_type_name} page {page}...")
            response = requests.get(
                f"{WORDPRESS_URL}/{endpoint}",
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
                logger.info(f"Reached end of {post_type_name} at page {page}")
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

                # Determine if evergreen: post type default OR category-based
                is_evergreen = post_type_evergreen or is_evergreen_category(primary_category)

                article = {
                    'wp_id': post['id'],
                    'url': post['link'],
                    'title': title,
                    'excerpt': excerpt[:500] if excerpt else '',  # Limit excerpt length
                    'category': primary_category,
                    'post_type': endpoint,
                    'image_url': image_url,
                    'word_count': word_count,
                    'published_at': post['date'],
                    'is_evergreen': is_evergreen
                }

                articles.append(article)

            logger.info(f"Fetched {len(data)} {post_type_name} from page {page}")

            if len(data) < per_page:
                break

            page += 1

        except requests.RequestException as e:
            logger.error(f"Error fetching {post_type_name} page {page}: {e}")
            break

    return articles


def fetch_articles(per_page: int = WORDPRESS_PER_PAGE,
                   full_sync: bool = False) -> list:
    """
    Fetch articles from all configured post types in WordPress API.

    Args:
        per_page: Number of articles per page
        full_sync: If True, fetch all articles. If False, fetch only recent ones.

    Returns:
        List of article dictionaries ready for database insertion
    """
    # First, fetch all categories
    categories = fetch_categories()
    logger.info(f"Fetched {len(categories)} categories")

    all_articles = []
    max_pages = 100 if full_sync else 5  # Limit pages for incremental sync

    # Loop through all configured post types
    for post_type in WORDPRESS_POST_TYPES:
        endpoint = post_type['endpoint']
        name = post_type['name']
        evergreen = post_type.get('evergreen', False)

        logger.info(f"--- Syncing {name} ({endpoint}) ---")

        articles = fetch_posts_by_type(
            endpoint=endpoint,
            post_type_name=name,
            post_type_evergreen=evergreen,
            categories=categories,
            per_page=per_page,
            max_pages=max_pages
        )

        logger.info(f"Fetched {len(articles)} {name}")
        all_articles.extend(articles)

    logger.info(f"Total content fetched: {len(all_articles)}")
    return all_articles


def fetch_single_article(post_id: int, post_type: str = 'posts') -> Optional[dict]:
    """
    Fetch a single article by its WordPress ID.

    Args:
        post_id: The WordPress post ID
        post_type: The post type endpoint (default: 'posts')

    Returns:
        Article dictionary or None if not found
    """
    categories = fetch_categories()

    # Get post type config for evergreen default
    post_type_config = next(
        (pt for pt in WORDPRESS_POST_TYPES if pt['endpoint'] == post_type),
        {'evergreen': False}
    )

    try:
        response = requests.get(
            f"{WORDPRESS_URL}/{post_type}/{post_id}",
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

        # Determine if evergreen
        is_evergreen = post_type_config.get('evergreen', False) or is_evergreen_category(primary_category)

        return {
            'wp_id': post['id'],
            'url': post['link'],
            'title': title,
            'excerpt': excerpt[:500] if excerpt else '',
            'category': primary_category,
            'post_type': post_type,
            'image_url': image_url,
            'word_count': word_count,
            'published_at': post['date'],
            'is_evergreen': is_evergreen
        }

    except requests.RequestException as e:
        logger.error(f"Error fetching {post_type} {post_id}: {e}")
        return None
