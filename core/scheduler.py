"""
Scheduler module for handling publication timing and retry logic.
"""

from datetime import datetime, timedelta
from typing import Optional

import yaml

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from dateutil import tz as ZoneInfo

from config.settings import BASE_DIR
from core.database import (
    get_failed_queue,
    get_last_repost,
    get_pending_queue,
    add_to_queue,
)


def load_scheduling_config() -> dict:
    """Load scheduling configuration from YAML file."""
    config_path = BASE_DIR / "config" / "scheduling.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_timezone(tz_name: str):
    """Get a timezone object from a timezone name."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except ImportError:
        from dateutil import tz
        return tz.gettz(tz_name)


def is_scheduled_time(platform: str, tolerance_minutes: int = 5) -> bool:
    """
    Check if the current time matches a scheduled posting time for a platform.

    Args:
        platform: The platform to check ('twitter' or 'facebook')
        tolerance_minutes: Number of minutes of tolerance around the scheduled time

    Returns:
        True if current time is within tolerance of a scheduled time
    """
    config = load_scheduling_config()
    platform_config = config.get(platform, {})

    if not platform_config.get('enabled', False):
        return False

    times = platform_config.get('times', [])
    timezone = get_timezone(platform_config.get('timezone', 'UTC'))

    now = datetime.now(timezone)
    current_time = now.time()

    for scheduled_time_str in times:
        scheduled_hour, scheduled_minute = map(int, scheduled_time_str.split(':'))
        scheduled_time = datetime.now(timezone).replace(
            hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0
        )

        # Check if within tolerance
        time_diff = abs((now - scheduled_time).total_seconds() / 60)
        if time_diff <= tolerance_minutes:
            return True

    return False


def should_post_today(platform: str) -> bool:
    """
    Check if we should post today on this platform.

    For Facebook, checks the post_every_n_days setting.
    """
    config = load_scheduling_config()
    platform_config = config.get(platform, {})

    if not platform_config.get('enabled', False):
        return False

    # Check post_every_n_days for platforms that have it
    post_every_n_days = platform_config.get('post_every_n_days')
    if post_every_n_days and post_every_n_days > 1:
        # Get the last successful repost on this platform
        from core.database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT posted_at FROM reposts
            WHERE platform = ? AND success = 1
            ORDER BY posted_at DESC LIMIT 1
        """, (platform,))
        row = cursor.fetchone()
        conn.close()

        if row:
            last_post_date = datetime.fromisoformat(row['posted_at']).date()
            days_since = (datetime.now().date() - last_post_date).days

            if days_since < post_every_n_days:
                return False

    return True


def get_next_scheduled_time(platform: str) -> Optional[datetime]:
    """
    Get the next scheduled posting time for a platform.

    Returns:
        datetime of next scheduled time, or None if platform is disabled
    """
    config = load_scheduling_config()
    platform_config = config.get(platform, {})

    if not platform_config.get('enabled', False):
        return None

    times = platform_config.get('times', [])
    if not times:
        return None

    timezone = get_timezone(platform_config.get('timezone', 'UTC'))
    now = datetime.now(timezone)

    # Find the next scheduled time today or tomorrow
    scheduled_times = []
    for time_str in times:
        hour, minute = map(int, time_str.split(':'))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if scheduled <= now:
            # This time has passed today, schedule for tomorrow
            scheduled += timedelta(days=1)

        # Check post_every_n_days
        post_every_n_days = platform_config.get('post_every_n_days', 1)
        if post_every_n_days > 1:
            from core.database import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT posted_at FROM reposts
                WHERE platform = ? AND success = 1
                ORDER BY posted_at DESC LIMIT 1
            """, (platform,))
            row = cursor.fetchone()
            conn.close()

            if row:
                last_post_date = datetime.fromisoformat(row['posted_at']).date()
                next_eligible_date = last_post_date + timedelta(days=post_every_n_days)

                while scheduled.date() < next_eligible_date:
                    scheduled += timedelta(days=1)

        scheduled_times.append(scheduled)

    return min(scheduled_times) if scheduled_times else None


def get_retry_config() -> dict:
    """Get retry configuration."""
    config = load_scheduling_config()
    return config.get('retry', {
        'max_attempts': 3,
        'delay_minutes': 60
    })


def get_items_to_retry() -> list:
    """
    Get failed queue items that are ready to be retried.

    Returns items where:
    - Status is 'failed'
    - attempts < max_attempts
    - last_attempt_at + delay_minutes has passed
    """
    retry_config = get_retry_config()
    max_attempts = retry_config.get('max_attempts', 3)
    delay_minutes = retry_config.get('delay_minutes', 60)

    failed_items = get_failed_queue(max_attempts)

    ready_to_retry = []
    for item in failed_items:
        if item['last_attempt_at']:
            last_attempt = datetime.fromisoformat(item['last_attempt_at'])
            if datetime.now() - last_attempt >= timedelta(minutes=delay_minutes):
                ready_to_retry.append(item)
        else:
            # No last attempt recorded, retry immediately
            ready_to_retry.append(item)

    return ready_to_retry


def schedule_post(article_id: int, platform: str, accroche: str,
                  scheduled_at: Optional[datetime] = None) -> int:
    """
    Schedule a post for a specific time.

    If scheduled_at is not provided, uses the next scheduled time for the platform.

    Returns:
        Queue ID of the scheduled post
    """
    if scheduled_at is None:
        scheduled_at = get_next_scheduled_time(platform)
        if scheduled_at is None:
            scheduled_at = datetime.now()

    return add_to_queue(article_id, platform, accroche, scheduled_at)
