"""
Database module for SQLite operations.
"""

import sqlite3
import time
import functools
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import DATABASE_PATH

logger = logging.getLogger(__name__)

# Database connection settings
DB_TIMEOUT = 30  # Wait up to 30 seconds for locks
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the database if it doesn't exist.

    Uses WAL mode for better concurrency and a timeout for lock handling.
    """
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row

    # Enable WAL mode for better concurrency (allows concurrent reads/writes)
    conn.execute("PRAGMA journal_mode=WAL")
    # Reduce lock contention
    conn.execute("PRAGMA busy_timeout=30000")  # 30 seconds in ms

    return conn


def retry_on_lock(func):
    """Decorator to retry database operations on lock errors."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    last_error = e
                    wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"Database locked, retrying in {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(wait_time)
                else:
                    raise
        logger.error(f"Database operation failed after {MAX_RETRIES} retries: {last_error}")
        raise last_error
    return wrapper


def init_database() -> None:
    """Initialize the database with required tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Articles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            wp_id INTEGER UNIQUE,
            url TEXT,
            title TEXT,
            excerpt TEXT,
            category TEXT,
            post_type TEXT DEFAULT 'posts',
            image_url TEXT,
            word_count INTEGER,
            published_at DATETIME,
            score INTEGER DEFAULT 0,
            is_evergreen BOOLEAN DEFAULT 0,
            excluded BOOLEAN DEFAULT 0,
            priority_boost INTEGER DEFAULT 0,
            synced_at DATETIME
        )
    """)

    # Add post_type column if it doesn't exist (for migration)
    try:
        cursor.execute("ALTER TABLE articles ADD COLUMN post_type TEXT DEFAULT 'posts'")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Reposts history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reposts (
            id INTEGER PRIMARY KEY,
            article_id INTEGER,
            platform TEXT,
            accroche TEXT,
            posted_at DATETIME,
            success BOOLEAN,
            error_message TEXT,
            format TEXT DEFAULT 'simple',
            posts_count INTEGER DEFAULT 1,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    """)

    # Add format and posts_count columns if they don't exist (for migration)
    try:
        cursor.execute("ALTER TABLE reposts ADD COLUMN format TEXT DEFAULT 'simple'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE reposts ADD COLUMN posts_count INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Post queue table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS post_queue (
            id INTEGER PRIMARY KEY,
            article_id INTEGER,
            platform TEXT,
            accroche TEXT,
            scheduled_at DATETIME,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_attempt_at DATETIME,
            error_message TEXT,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    """)

    # Accroches cache table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accroches_cache (
            id INTEGER PRIMARY KEY,
            article_id INTEGER,
            platform TEXT,
            accroche TEXT,
            used BOOLEAN DEFAULT 0,
            created_at DATETIME,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    """)

    # Bluesky follows tracking table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bluesky_follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            did TEXT UNIQUE NOT NULL,
            handle TEXT NOT NULL,
            display_name TEXT,
            followers_count INTEGER,
            following_count INTEGER,
            segment TEXT,
            followed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            follow_back BOOLEAN DEFAULT FALSE,
            follow_back_checked_at TIMESTAMP,
            unfollowed_at TIMESTAMP,
            source_hashtag TEXT,
            notes TEXT,
            language TEXT DEFAULT 'unknown'
        )
    """)

    # Add language column if it doesn't exist (for migration)
    try:
        cursor.execute("ALTER TABLE bluesky_follows ADD COLUMN language TEXT DEFAULT 'unknown'")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # External posts table (curation from RSS feeds)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS external_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT UNIQUE NOT NULL,
            source_name TEXT NOT NULL,
            category TEXT,
            title TEXT NOT NULL,
            description TEXT,
            accroche TEXT,
            linked_article_url TEXT,
            posted_at TIMESTAMP,
            facebook_post_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Daily counters for Facebook scheduling
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facebook_daily_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            counter_type TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            UNIQUE(date, counter_type)
        )
    """)

    conn.commit()
    conn.close()


@retry_on_lock
def upsert_article(article_data: dict) -> int:
    """Insert or update an article. Returns the article ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO articles (
            wp_id, url, title, excerpt, category, post_type, image_url,
            word_count, published_at, is_evergreen, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(wp_id) DO UPDATE SET
            url = excluded.url,
            title = excluded.title,
            excerpt = excluded.excerpt,
            category = excluded.category,
            post_type = excluded.post_type,
            image_url = excluded.image_url,
            word_count = excluded.word_count,
            published_at = excluded.published_at,
            is_evergreen = excluded.is_evergreen,
            synced_at = excluded.synced_at
    """, (
        article_data['wp_id'],
        article_data['url'],
        article_data['title'],
        article_data['excerpt'],
        article_data['category'],
        article_data.get('post_type', 'posts'),
        article_data.get('image_url'),
        article_data['word_count'],
        article_data['published_at'],
        article_data.get('is_evergreen', False),
        datetime.now().isoformat()
    ))

    conn.commit()

    # Get the article ID
    cursor.execute("SELECT id FROM articles WHERE wp_id = ?", (article_data['wp_id'],))
    article_id = cursor.fetchone()['id']

    conn.close()
    return article_id


@retry_on_lock
def update_article_score(article_id: int, score: int) -> None:
    """Update the score of an article."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE articles SET score = ? WHERE id = ?", (score, article_id))
    conn.commit()
    conn.close()


def get_article_by_id(article_id: int) -> Optional[dict]:
    """Get an article by its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_article_by_wp_id(wp_id: int) -> Optional[dict]:
    """Get an article by its WordPress ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM articles WHERE wp_id = ?", (wp_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_articles(include_excluded: bool = False) -> list:
    """Get all articles from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    if include_excluded:
        cursor.execute("SELECT * FROM articles ORDER BY score DESC")
    else:
        cursor.execute("SELECT * FROM articles WHERE excluded = 0 ORDER BY score DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@retry_on_lock
def exclude_article(article_id: int, excluded: bool = True) -> None:
    """Mark an article as excluded or included."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE articles SET excluded = ? WHERE id = ?", (excluded, article_id))
    conn.commit()
    conn.close()


@retry_on_lock
def set_priority_boost(article_id: int, boost: int) -> None:
    """Set priority boost for an article."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE articles SET priority_boost = ? WHERE id = ?", (boost, article_id))
    conn.commit()
    conn.close()


@retry_on_lock
def add_repost(article_id: int, platform: str, accroche: str,
               success: bool, error_message: Optional[str] = None,
               format: str = 'simple', posts_count: int = 1) -> int:
    """Add a repost record. Returns the repost ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reposts (article_id, platform, accroche, posted_at, success, error_message, format, posts_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (article_id, platform, accroche, datetime.now().isoformat(), success, error_message, format, posts_count))
    conn.commit()
    repost_id = cursor.lastrowid
    conn.close()
    return repost_id


def get_last_repost(article_id: int, platform: str) -> Optional[dict]:
    """Get the last repost for an article on a platform."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM reposts
        WHERE article_id = ? AND platform = ? AND success = 1
        ORDER BY posted_at DESC LIMIT 1
    """, (article_id, platform))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_repost_history(limit: int = 20) -> list:
    """Get recent repost history."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.*, a.title, a.url
        FROM reposts r
        JOIN articles a ON r.article_id = a.id
        ORDER BY r.posted_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_previous_accroches(article_id: int, platform: str) -> list:
    """Get previously used accroches for an article on a platform."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT accroche FROM reposts
        WHERE article_id = ? AND platform = ? AND success = 1
        ORDER BY posted_at DESC
    """, (article_id, platform))
    rows = cursor.fetchall()
    conn.close()
    return [row['accroche'] for row in rows]


@retry_on_lock
def add_to_queue(article_id: int, platform: str, accroche: str,
                 scheduled_at: datetime) -> int:
    """Add a post to the queue. Returns the queue ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO post_queue (article_id, platform, accroche, scheduled_at, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (article_id, platform, accroche, scheduled_at.isoformat()))
    conn.commit()
    queue_id = cursor.lastrowid
    conn.close()
    return queue_id


def get_pending_queue(platform: Optional[str] = None) -> list:
    """Get pending items from the queue."""
    conn = get_connection()
    cursor = conn.cursor()
    if platform:
        cursor.execute("""
            SELECT q.*, a.title, a.url, a.image_url
            FROM post_queue q
            JOIN articles a ON q.article_id = a.id
            WHERE q.status = 'pending' AND q.platform = ?
            ORDER BY q.scheduled_at ASC
        """, (platform,))
    else:
        cursor.execute("""
            SELECT q.*, a.title, a.url, a.image_url
            FROM post_queue q
            JOIN articles a ON q.article_id = a.id
            WHERE q.status = 'pending'
            ORDER BY q.scheduled_at ASC
        """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_failed_queue(max_attempts: int = 3) -> list:
    """Get failed items that can be retried."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT q.*, a.title, a.url, a.image_url
        FROM post_queue q
        JOIN articles a ON q.article_id = a.id
        WHERE q.status = 'failed' AND q.attempts < ?
        ORDER BY q.last_attempt_at ASC
    """, (max_attempts,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@retry_on_lock
def update_queue_status(queue_id: int, status: str,
                        error_message: Optional[str] = None) -> None:
    """Update the status of a queue item."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE post_queue
        SET status = ?, attempts = attempts + 1,
            last_attempt_at = ?, error_message = ?
        WHERE id = ?
    """, (status, datetime.now().isoformat(), error_message, queue_id))
    conn.commit()
    conn.close()


@retry_on_lock
def cache_accroche(article_id: int, platform: str, accroche: str) -> int:
    """Cache a generated accroche. Returns the cache ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO accroches_cache (article_id, platform, accroche, created_at)
        VALUES (?, ?, ?, ?)
    """, (article_id, platform, accroche, datetime.now().isoformat()))
    conn.commit()
    cache_id = cursor.lastrowid
    conn.close()
    return cache_id


def get_cached_accroche(article_id: int, platform: str) -> Optional[str]:
    """Get an unused cached accroche for an article."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, accroche FROM accroches_cache
        WHERE article_id = ? AND platform = ? AND used = 0
        ORDER BY created_at DESC LIMIT 1
    """, (article_id, platform))
    row = cursor.fetchone()

    if row:
        # Mark as used
        cursor.execute("UPDATE accroches_cache SET used = 1 WHERE id = ?", (row['id'],))
        conn.commit()
        accroche = row['accroche']
    else:
        accroche = None

    conn.close()
    return accroche


def get_stats() -> dict:
    """Get statistics about the database."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Total articles
    cursor.execute("SELECT COUNT(*) as count FROM articles")
    stats['total_articles'] = cursor.fetchone()['count']

    # Active articles (not excluded)
    cursor.execute("SELECT COUNT(*) as count FROM articles WHERE excluded = 0")
    stats['active_articles'] = cursor.fetchone()['count']

    # Evergreen articles
    cursor.execute("SELECT COUNT(*) as count FROM articles WHERE is_evergreen = 1")
    stats['evergreen_articles'] = cursor.fetchone()['count']

    # Articles by post type
    cursor.execute("""
        SELECT post_type, COUNT(*) as count
        FROM articles
        GROUP BY post_type
    """)
    stats['articles_by_type'] = {row['post_type'] or 'posts': row['count'] for row in cursor.fetchall()}

    # Total reposts
    cursor.execute("SELECT COUNT(*) as count FROM reposts WHERE success = 1")
    stats['total_reposts'] = cursor.fetchone()['count']

    # Reposts by platform
    cursor.execute("""
        SELECT platform, COUNT(*) as count
        FROM reposts WHERE success = 1
        GROUP BY platform
    """)
    stats['reposts_by_platform'] = {row['platform']: row['count'] for row in cursor.fetchall()}

    # Reposts by format (threads vs simple)
    cursor.execute("""
        SELECT format, COUNT(*) as count, SUM(posts_count) as total_posts
        FROM reposts WHERE success = 1
        GROUP BY format
    """)
    stats['reposts_by_format'] = {
        row['format'] or 'simple': {'count': row['count'], 'total_posts': row['total_posts'] or row['count']}
        for row in cursor.fetchall()
    }

    # Total tweets posted (including thread tweets)
    cursor.execute("SELECT SUM(posts_count) as total FROM reposts WHERE success = 1 AND platform = 'twitter'")
    result = cursor.fetchone()
    stats['total_tweets'] = result['total'] if result['total'] else 0

    # Pending in queue
    cursor.execute("SELECT COUNT(*) as count FROM post_queue WHERE status = 'pending'")
    stats['pending_queue'] = cursor.fetchone()['count']

    # Failed in queue
    cursor.execute("SELECT COUNT(*) as count FROM post_queue WHERE status = 'failed'")
    stats['failed_queue'] = cursor.fetchone()['count']

    conn.close()
    return stats


# === External posts (curation) ===

def is_external_post_known(source_url: str) -> bool:
    """Check if an external article has already been processed."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM external_posts WHERE source_url = ?", (source_url,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


@retry_on_lock
def add_external_post(source_url: str, source_name: str, category: str,
                      title: str, description: str, accroche: str,
                      linked_article_url: Optional[str] = None) -> int:
    """Add an external post to the database. Returns the post ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO external_posts (source_url, source_name, category, title,
                                    description, accroche, linked_article_url, posted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (source_url, source_name, category, title, description, accroche,
          linked_article_url, datetime.now().isoformat()))
    conn.commit()
    post_id = cursor.lastrowid
    conn.close()
    return post_id


def get_pending_external_posts(limit: int = 5) -> list:
    """Get external posts that haven't been posted to Facebook yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM external_posts
        WHERE facebook_post_id IS NULL
        ORDER BY created_at ASC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@retry_on_lock
def mark_external_post_published(post_id: int, facebook_post_id: str) -> None:
    """Mark an external post as published to Facebook."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE external_posts
        SET facebook_post_id = ?, posted_at = ?
        WHERE id = ?
    """, (facebook_post_id, datetime.now().isoformat(), post_id))
    conn.commit()
    conn.close()


# === Daily counters ===

def get_daily_count(counter_type: str) -> int:
    """Get today's count for a counter type (catchup, external)."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT count FROM facebook_daily_counters
        WHERE date = ? AND counter_type = ?
    """, (today, counter_type))
    row = cursor.fetchone()
    conn.close()
    return row['count'] if row else 0


@retry_on_lock
def increment_daily_count(counter_type: str) -> None:
    """Increment today's counter for a given type."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO facebook_daily_counters (date, counter_type, count)
        VALUES (?, ?, 1)
        ON CONFLICT(date, counter_type) DO UPDATE SET count = count + 1
    """, (today, counter_type))
    conn.commit()
    conn.close()


def find_related_article(title: str, category: str = None) -> Optional[dict]:
    """Find a related jurojin article by category and keyword matching."""
    conn = get_connection()
    cursor = conn.cursor()

    # Extract keywords from title (words > 4 chars)
    keywords = [w.lower() for w in title.split() if len(w) > 4]

    # First: try matching by category
    if category:
        cursor.execute("""
            SELECT id, url, title, category FROM articles
            WHERE excluded = 0 AND category = ?
            ORDER BY score DESC LIMIT 10
        """, (category,))
        category_matches = [dict(row) for row in cursor.fetchall()]

        # Check keyword overlap
        for article in category_matches:
            article_words = [w.lower() for w in article['title'].split() if len(w) > 4]
            overlap = set(keywords) & set(article_words)
            if overlap:
                conn.close()
                return article

    # Second: keyword search across all articles
    for keyword in keywords[:5]:
        cursor.execute("""
            SELECT id, url, title, category FROM articles
            WHERE excluded = 0 AND (title LIKE ? OR excerpt LIKE ?)
            ORDER BY score DESC LIMIT 1
        """, (f"%{keyword}%", f"%{keyword}%"))
        row = cursor.fetchone()
        if row:
            conn.close()
            return dict(row)

    conn.close()
    return None


# === RSS Veille Module ===

def init_veille_tables() -> None:
    """Initialize tables for RSS veille module."""
    conn = get_connection()
    cursor = conn.cursor()

    # RSS articles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rss_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            source_name TEXT NOT NULL,
            source_url TEXT,
            category TEXT,
            language TEXT DEFAULT 'en',
            priority TEXT DEFAULT 'medium',
            author TEXT,
            published_at TIMESTAMP,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            relevance_score INTEGER,
            relevance_analyzed_at TIMESTAMP,
            brief_generated BOOLEAN DEFAULT FALSE,
            brief_generated_at TIMESTAMP,
            wp_draft_id INTEGER,
            wp_draft_url TEXT
        )
    """)

    # Briefs generated table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS veille_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rss_article_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            keyword_main TEXT,
            keywords_longtail TEXT,
            angle TEXT,
            structure TEXT,
            external_sources TEXT,
            internal_links TEXT,
            word_count_target INTEGER,
            tone_notes TEXT,
            full_brief TEXT NOT NULL,
            wp_draft_id INTEGER,
            wp_draft_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (rss_article_id) REFERENCES rss_articles(id)
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Veille tables initialized")


@retry_on_lock
def save_rss_article(article: dict) -> Optional[int]:
    """Save an RSS article to the database. Returns article ID or None if duplicate."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO rss_articles (
                url, title, summary, source_name, source_url,
                category, language, priority, author, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article['url'],
            article['title'],
            article.get('summary', ''),
            article['source_name'],
            article.get('source_url', ''),
            article.get('category', ''),
            article.get('language', 'en'),
            article.get('priority', 'medium'),
            article.get('author', ''),
            article.get('published').isoformat() if article.get('published') else None
        ))
        conn.commit()
        article_id = cursor.lastrowid
        conn.close()
        return article_id
    except sqlite3.IntegrityError:
        # Duplicate URL
        conn.close()
        return None


def get_rss_articles_for_analysis(limit: int = 50) -> list:
    """Get RSS articles that haven't been analyzed for relevance yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM rss_articles
        WHERE relevance_score IS NULL
        ORDER BY published_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@retry_on_lock
def update_rss_relevance(article_id: int, score: int) -> None:
    """Update the relevance score of an RSS article."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE rss_articles
        SET relevance_score = ?, relevance_analyzed_at = ?
        WHERE id = ?
    """, (score, datetime.now().isoformat(), article_id))
    conn.commit()
    conn.close()


def get_relevant_rss_articles(min_score: int = 7, limit: int = 10) -> list:
    """Get relevant RSS articles that haven't had briefs generated yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM rss_articles
        WHERE relevance_score >= ? AND brief_generated = FALSE
        ORDER BY relevance_score DESC, published_at DESC
        LIMIT ?
    """, (min_score, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@retry_on_lock
def save_veille_brief(rss_article_id: int, brief_data: dict) -> int:
    """Save a generated brief. Returns brief ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO veille_briefs (
            rss_article_id, title, keyword_main, keywords_longtail,
            angle, structure, external_sources, internal_links,
            word_count_target, tone_notes, full_brief
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        rss_article_id,
        brief_data['title'],
        brief_data.get('keyword_main', ''),
        brief_data.get('keywords_longtail', ''),
        brief_data.get('angle', ''),
        brief_data.get('structure', ''),
        brief_data.get('external_sources', ''),
        brief_data.get('internal_links', ''),
        brief_data.get('word_count_target', 1500),
        brief_data.get('tone_notes', ''),
        brief_data['full_brief']
    ))

    # Mark article as having brief generated
    cursor.execute("""
        UPDATE rss_articles
        SET brief_generated = TRUE, brief_generated_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), rss_article_id))

    conn.commit()
    brief_id = cursor.lastrowid
    conn.close()
    return brief_id


@retry_on_lock
def update_brief_wp_draft(brief_id: int, wp_draft_id: int, wp_draft_url: str) -> None:
    """Update brief with WordPress draft info."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE veille_briefs
        SET wp_draft_id = ?, wp_draft_url = ?
        WHERE id = ?
    """, (wp_draft_id, wp_draft_url, brief_id))

    # Also update the RSS article
    cursor.execute("""
        UPDATE rss_articles
        SET wp_draft_id = ?, wp_draft_url = ?
        WHERE id = (SELECT rss_article_id FROM veille_briefs WHERE id = ?)
    """, (wp_draft_id, wp_draft_url, brief_id))

    conn.commit()
    conn.close()


def is_rss_article_known(url: str) -> bool:
    """Check if an RSS article URL is already in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM rss_articles WHERE url = ?", (url,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def get_veille_stats() -> dict:
    """Get statistics about the veille module."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Total RSS articles
    cursor.execute("SELECT COUNT(*) as count FROM rss_articles")
    stats['total_rss_articles'] = cursor.fetchone()['count']

    # Analyzed articles
    cursor.execute("SELECT COUNT(*) as count FROM rss_articles WHERE relevance_score IS NOT NULL")
    stats['analyzed_articles'] = cursor.fetchone()['count']

    # Relevant articles (score >= 7)
    cursor.execute("SELECT COUNT(*) as count FROM rss_articles WHERE relevance_score >= 7")
    stats['relevant_articles'] = cursor.fetchone()['count']

    # Briefs generated
    cursor.execute("SELECT COUNT(*) as count FROM veille_briefs")
    stats['briefs_generated'] = cursor.fetchone()['count']

    # WordPress drafts created
    cursor.execute("SELECT COUNT(*) as count FROM veille_briefs WHERE wp_draft_id IS NOT NULL")
    stats['wp_drafts_created'] = cursor.fetchone()['count']

    # Articles by source
    cursor.execute("""
        SELECT source_name, COUNT(*) as count
        FROM rss_articles
        GROUP BY source_name
        ORDER BY count DESC
        LIMIT 10
    """)
    stats['by_source'] = {row['source_name']: row['count'] for row in cursor.fetchall()}

    # Recent articles (last 48h)
    cursor.execute("""
        SELECT COUNT(*) as count FROM rss_articles
        WHERE fetched_at >= datetime('now', '-48 hours')
    """)
    stats['recent_articles'] = cursor.fetchone()['count']

    conn.close()
    return stats
