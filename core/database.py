"""
Database module for SQLite operations.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the database if it doesn't exist."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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

    conn.commit()
    conn.close()


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


def exclude_article(article_id: int, excluded: bool = True) -> None:
    """Mark an article as excluded or included."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE articles SET excluded = ? WHERE id = ?", (excluded, article_id))
    conn.commit()
    conn.close()


def set_priority_boost(article_id: int, boost: int) -> None:
    """Set priority boost for an article."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE articles SET priority_boost = ? WHERE id = ?", (boost, article_id))
    conn.commit()
    conn.close()


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
