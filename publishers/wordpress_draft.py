"""
WordPress draft publisher.
Creates draft posts via WP REST API with article prompts.
"""

import logging
from typing import Optional
from base64 import b64encode

import requests

from config.settings import (
    WORDPRESS_URL,
    WORDPRESS_USERNAME,
    WORDPRESS_APP_PASSWORD
)

logger = logging.getLogger(__name__)

# WordPress REST API endpoints
WP_POSTS_ENDPOINT = f"{WORDPRESS_URL}/posts"
WP_CATEGORIES_ENDPOINT = f"{WORDPRESS_URL}/categories"
WP_TAGS_ENDPOINT = f"{WORDPRESS_URL}/tags"


def get_auth_header() -> dict:
    """Get Basic Auth header for WordPress API."""
    if not WORDPRESS_USERNAME or not WORDPRESS_APP_PASSWORD:
        return {}

    credentials = f"{WORDPRESS_USERNAME}:{WORDPRESS_APP_PASSWORD}"
    token = b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {token}"}


def get_categories() -> dict:
    """
    Fetch all categories from WordPress.

    Returns:
        Dict mapping category name (lowercase) to ID
    """
    try:
        response = requests.get(
            WP_CATEGORIES_ENDPOINT,
            params={"per_page": 100},
            headers=get_auth_header(),
            timeout=30
        )

        if response.status_code == 200:
            categories = response.json()
            return {cat['name'].lower(): cat['id'] for cat in categories}
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")

    return {}


def get_tags() -> dict:
    """
    Fetch all tags from WordPress.

    Returns:
        Dict mapping tag name (lowercase) to ID
    """
    try:
        response = requests.get(
            WP_TAGS_ENDPOINT,
            params={"per_page": 100},
            headers=get_auth_header(),
            timeout=30
        )

        if response.status_code == 200:
            tags = response.json()
            return {tag['name'].lower(): tag['id'] for tag in tags}
    except Exception as e:
        logger.error(f"Error fetching tags: {e}")

    return {}


def find_category_id(category_name: str, categories: dict = None) -> Optional[int]:
    """
    Find the WordPress category ID for a category name.
    Falls back to 'Actualités' if not found.
    """
    if categories is None:
        categories = get_categories()

    # Try exact match (lowercase)
    if category_name.lower() in categories:
        return categories[category_name.lower()]

    # Try partial match
    for cat_name, cat_id in categories.items():
        if category_name.lower() in cat_name or cat_name in category_name.lower():
            return cat_id

    # Fallback to Actualités
    fallback_names = ['actualités', 'actualites', 'actu', 'news']
    for name in fallback_names:
        if name in categories:
            return categories[name]

    # Return first category as last resort
    if categories:
        return list(categories.values())[0]

    return None


def find_tag_ids(tag_names: list, tags: dict = None) -> list:
    """
    Find WordPress tag IDs for a list of tag names.
    Only returns IDs for existing tags (never creates new ones).
    """
    if tags is None:
        tags = get_tags()

    tag_ids = []
    for tag_name in tag_names:
        tag_lower = tag_name.lower()
        if tag_lower in tags:
            tag_ids.append(tags[tag_lower])
        else:
            # Try partial match
            for existing_tag, tag_id in tags.items():
                if tag_lower in existing_tag or existing_tag in tag_lower:
                    tag_ids.append(tag_id)
                    break

    return tag_ids


def format_content_html(content: str) -> str:
    """
    Format the prompt content as readable HTML for WordPress.
    Converts markdown-like formatting to HTML.
    """
    lines = content.split('\n')
    html_lines = []

    for line in lines:
        line = line.strip()

        if not line:
            html_lines.append('')
            continue

        # Headers (=== SECTION ===)
        if line.startswith('===') and line.endswith('==='):
            title = line.strip('= ')
            html_lines.append(f'<h2 style="color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 8px;">{title}</h2>')

        # Emoji headers (📌 SUJET :)
        elif line.startswith(('📌', '📊', '⏰', '📝', '📏', '🎯', '🔑', '🔗', '🌐')):
            html_lines.append(f'<p><strong>{line}</strong></p>')

        # List items
        elif line.startswith('- '):
            html_lines.append(f'<li>{line[2:]}</li>')

        # Numbered items
        elif line and line[0].isdigit() and '. ' in line[:4]:
            html_lines.append(f'<p style="margin-left: 20px;">{line}</p>')

        # Section headers in prompt
        elif line.startswith('[Section') or line.startswith('- [Section'):
            html_lines.append(f'<p style="margin-left: 20px; color: #059669;"><strong>{line}</strong></p>')

        # Regular content
        else:
            html_lines.append(f'<p>{line}</p>')

    # Wrap lists
    html_content = '\n'.join(html_lines)
    html_content = html_content.replace('</p>\n<li>', '</p>\n<ul>\n<li>')
    html_content = html_content.replace('</li>\n<p>', '</li>\n</ul>\n<p>')

    return html_content


def create_draft(title: str, content: str, category: str = None,
                 tags: list = None, dry_run: bool = False) -> dict:
    """
    Create a draft post in WordPress.

    Args:
        title: Post title
        content: Post content (the prompt)
        category: Category name
        tags: List of tag names
        dry_run: If True, don't actually create the post

    Returns:
        {"success": bool, "post_id": int or None, "url": str or None, "error": str or None}
    """
    if not WORDPRESS_USERNAME or not WORDPRESS_APP_PASSWORD:
        return {
            "success": False,
            "post_id": None,
            "url": None,
            "error": "WordPress credentials not configured"
        }

    # Get category ID
    categories = get_categories()
    category_id = find_category_id(category or 'Actualités', categories)

    # Get tag IDs (only existing tags)
    all_tags = get_tags()
    tag_ids = find_tag_ids(tags or [], all_tags)

    # Format content as HTML
    html_content = format_content_html(content)

    # Build post data
    post_data = {
        "title": title,
        "content": html_content,
        "status": "draft",
        "categories": [category_id] if category_id else [],
        "tags": tag_ids
    }

    if dry_run:
        logger.info(f"[DRY RUN] Would create draft:")
        logger.info(f"  Title: {title}")
        logger.info(f"  Category ID: {category_id}")
        logger.info(f"  Tags: {tag_ids}")
        logger.info(f"  Content length: {len(content)} chars")
        return {
            "success": True,
            "post_id": "dry_run",
            "url": None,
            "error": None
        }

    try:
        response = requests.post(
            WP_POSTS_ENDPOINT,
            headers={
                **get_auth_header(),
                "Content-Type": "application/json"
            },
            json=post_data,
            timeout=60
        )

        if response.status_code in (200, 201):
            data = response.json()
            post_id = data.get('id')
            post_url = data.get('link')

            logger.info(f"Created WordPress draft: ID={post_id}")
            return {
                "success": True,
                "post_id": post_id,
                "url": post_url,
                "error": None
            }
        else:
            error_msg = response.text[:200]
            logger.error(f"WordPress API error: {response.status_code} - {error_msg}")
            return {
                "success": False,
                "post_id": None,
                "url": None,
                "error": f"HTTP {response.status_code}: {error_msg}"
            }

    except requests.RequestException as e:
        logger.error(f"WordPress request error: {e}")
        return {
            "success": False,
            "post_id": None,
            "url": None,
            "error": str(e)
        }


def publish_brief(brief: dict, dry_run: bool = False) -> dict:
    """
    Publish an article brief as a WordPress draft.

    Args:
        brief: Dict from generate_article_brief()
        dry_run: If True, don't actually create the post

    Returns:
        Result dict from create_draft()
    """
    return create_draft(
        title=brief['title'],
        content=brief['content'],
        category=brief.get('category'),
        tags=brief.get('tags', []),
        dry_run=dry_run
    )


def publish_veille_brief(brief_data: dict, source_article: dict, dry_run: bool = False) -> dict:
    """
    Publish a veille brief as a WordPress draft.

    Args:
        brief_data: Dict from generate_brief() in brief_generator
        source_article: Original RSS article dict
        dry_run: If True, don't actually create the post

    Returns:
        Result dict from create_draft()
    """
    from ai.brief_generator import format_brief_for_wordpress

    # Format the content
    content = format_brief_for_wordpress(brief_data, source_article)

    # Add [BRIEF] prefix to title
    title = f"[BRIEF] {brief_data.get('title', source_article.get('title', 'Sans titre'))}"

    # Determine category from source category
    category_mapping = {
        '3d_vfx': 'Arts Numérique',
        'tech_ia': 'Actualités',
        'gaming': 'Gaming',
        'cinema_anime': 'Cinéma',
        'tech_fr': 'Actualités'
    }
    category = category_mapping.get(source_article.get('category', ''), 'Actualités')

    # Extract tags from keywords
    tags = []
    if brief_data.get('keyword_main'):
        tags.append(brief_data['keyword_main'])
    if brief_data.get('keywords_longtail'):
        # Split comma or semicolon separated
        longtail = brief_data['keywords_longtail']
        for sep in [',', ';', '/', '-']:
            if sep in longtail:
                tags.extend([t.strip() for t in longtail.split(sep) if t.strip()])
                break
        else:
            tags.append(longtail)

    return create_draft(
        title=title,
        content=content,
        category=category,
        tags=tags[:5],  # Limit tags
        dry_run=dry_run
    )


def check_api_status() -> dict:
    """
    Check WordPress API connectivity and credentials.
    """
    if not WORDPRESS_USERNAME or not WORDPRESS_APP_PASSWORD:
        return {
            "configured": False,
            "connected": False,
            "error": "WORDPRESS_USERNAME or WORDPRESS_APP_PASSWORD not set"
        }

    try:
        # Try to fetch current user
        response = requests.get(
            f"{WORDPRESS_URL}/users/me",
            headers=get_auth_header(),
            timeout=30
        )

        if response.status_code == 200:
            user = response.json()
            return {
                "configured": True,
                "connected": True,
                "username": user.get('name', 'Unknown'),
                "error": None
            }
        else:
            return {
                "configured": True,
                "connected": False,
                "error": f"HTTP {response.status_code}"
            }

    except requests.RequestException as e:
        return {
            "configured": True,
            "connected": False,
            "error": str(e)
        }
