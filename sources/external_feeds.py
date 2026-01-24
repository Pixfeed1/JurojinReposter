"""
External feeds module for fetching and parsing RSS/Atom feeds.
Used for Facebook curation of external content.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

import requests
import yaml

from config.settings import BASE_DIR
from core.database import is_external_post_known

logger = logging.getLogger(__name__)

# Namespaces for RSS/Atom parsing
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'media': 'http://search.yahoo.com/mrss/',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'yt': 'http://www.youtube.com/xml/schemas/2015',
}


def load_curation_config() -> dict:
    """Load curation sources configuration."""
    config_path = BASE_DIR / "config" / "curation_sources.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def fetch_feed(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch RSS/Atom feed content."""
    try:
        headers = {
            'User-Agent': 'JurojinReposter/1.0 (RSS Reader)'
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch feed {url}: {e}")
        return None


def parse_rss_item(item: ET.Element) -> dict:
    """Parse a single RSS <item> element."""
    title = item.findtext('title', '').strip()
    link = item.findtext('link', '').strip()
    description = item.findtext('description', '').strip()

    # Try dc:creator
    creator = item.findtext('dc:creator', '', NAMESPACES)

    # Try pubDate
    pub_date = item.findtext('pubDate', '')

    # Clean HTML from description
    if '<' in description:
        from bs4 import BeautifulSoup
        description = BeautifulSoup(description, 'html.parser').get_text()

    return {
        'title': title,
        'url': link,
        'description': description[:500] if description else '',
        'author': creator,
        'pub_date': pub_date
    }


def parse_atom_entry(entry: ET.Element) -> dict:
    """Parse a single Atom <entry> element."""
    title = entry.findtext('{http://www.w3.org/2005/Atom}title', '').strip()

    # Link can be in href attribute
    link_elem = entry.find('{http://www.w3.org/2005/Atom}link[@rel="alternate"]')
    if link_elem is None:
        link_elem = entry.find('{http://www.w3.org/2005/Atom}link')
    link = link_elem.get('href', '') if link_elem is not None else ''

    # Summary or content
    summary = entry.findtext('{http://www.w3.org/2005/Atom}summary', '').strip()
    if not summary:
        content = entry.findtext('{http://www.w3.org/2005/Atom}content', '').strip()
        summary = content

    # Clean HTML
    if '<' in summary:
        from bs4 import BeautifulSoup
        summary = BeautifulSoup(summary, 'html.parser').get_text()

    return {
        'title': title,
        'url': link,
        'description': summary[:500] if summary else '',
        'author': entry.findtext('{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name', ''),
        'pub_date': entry.findtext('{http://www.w3.org/2005/Atom}published', '')
    }


def parse_youtube_entry(entry: ET.Element) -> dict:
    """Parse a YouTube Atom feed entry."""
    title = entry.findtext('{http://www.w3.org/2005/Atom}title', '').strip()

    link_elem = entry.find('{http://www.w3.org/2005/Atom}link[@rel="alternate"]')
    link = link_elem.get('href', '') if link_elem is not None else ''

    # YouTube video description
    description = ''
    media_group = entry.find('{http://search.yahoo.com/mrss/}group')
    if media_group is not None:
        description = media_group.findtext('{http://search.yahoo.com/mrss/}description', '')

    return {
        'title': title,
        'url': link,
        'description': description[:500] if description else '',
        'author': entry.findtext('{http://www.youtube.com/xml/schemas/2015}channelId', ''),
        'pub_date': entry.findtext('{http://www.w3.org/2005/Atom}published', '')
    }


def parse_feed(xml_content: str, feed_type: str = None) -> list:
    """Parse RSS or Atom feed content into a list of items."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse feed XML: {e}")
        return []

    items = []

    # Detect feed type
    if root.tag == '{http://www.w3.org/2005/Atom}feed':
        # Atom feed
        entries = root.findall('{http://www.w3.org/2005/Atom}entry')
        for entry in entries[:10]:  # Limit to 10 most recent
            if feed_type == 'youtube':
                items.append(parse_youtube_entry(entry))
            else:
                items.append(parse_atom_entry(entry))
    elif root.tag == 'rss' or root.find('channel') is not None:
        # RSS feed
        channel = root.find('channel')
        if channel is not None:
            for item in channel.findall('item')[:10]:
                items.append(parse_rss_item(item))
    else:
        logger.warning(f"Unknown feed format: root tag = {root.tag}")

    return items


def apply_keyword_filter(item: dict, filter_config: dict) -> bool:
    """
    Apply keyword filter to an item.

    Returns True if item passes the filter.
    """
    title_lower = item['title'].lower()
    desc_lower = item['description'].lower()
    combined = f"{title_lower} {desc_lower}"

    # Check reject keywords first
    reject_keywords = filter_config.get('reject_keywords', [])
    for keyword in reject_keywords:
        if keyword.lower() in combined:
            return False

    # Check require keywords (if list is empty, all pass)
    require_keywords = filter_config.get('require_keywords', [])
    if not require_keywords:
        return True

    for keyword in require_keywords:
        if keyword.lower() in combined:
            return True

    return False


def fetch_new_items(max_per_category: int = 5) -> list:
    """
    Fetch new items from all configured RSS sources.

    Returns a list of items that:
    - Pass the keyword filter
    - Haven't been processed before (not in external_posts table)

    Each item includes: title, url, description, source_name, category
    """
    config = load_curation_config()
    sources = config.get('sources', {})
    filters = config.get('filters', {})

    new_items = []

    for category, feeds in sources.items():
        category_items = 0

        for feed_info in feeds:
            if category_items >= max_per_category:
                break

            url = feed_info['url']
            source_name = feed_info['name']
            filter_name = feed_info.get('filter', 'news')
            feed_type = feed_info.get('type')

            # Fetch the feed
            xml_content = fetch_feed(url)
            if not xml_content:
                continue

            # Parse items
            items = parse_feed(xml_content, feed_type)

            # Get filter config
            filter_config = filters.get(filter_name, {'require_keywords': [], 'reject_keywords': []})

            for item in items:
                if not item['title'] or not item['url']:
                    continue

                # Skip if already known
                if is_external_post_known(item['url']):
                    continue

                # Apply keyword filter
                if not apply_keyword_filter(item, filter_config):
                    continue

                item['source_name'] = source_name
                item['category'] = category
                new_items.append(item)
                category_items += 1

                if category_items >= max_per_category:
                    break

    logger.info(f"Found {len(new_items)} new items from external feeds")
    return new_items
