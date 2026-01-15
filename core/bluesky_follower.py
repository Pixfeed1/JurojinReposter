"""
Bluesky automatic follow/unfollow system.
Targets accounts in the 3D/VFX/Cinema niche with smart segmentation.
"""

import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Optional

import yaml
from atproto import Client

from config.settings import BASE_DIR, DATABASE_PATH
from core.database import get_connection, init_database
from core.bot_detector import BotDetector

logger = logging.getLogger(__name__)


class BlueskyFollower:
    """Manages automatic follow/unfollow for Bluesky."""

    def __init__(self):
        self.config = self._load_config()
        self.client = None
        self.bot_detector = BotDetector()

    def _load_config(self) -> dict:
        """Load follow strategy configuration."""
        config_path = BASE_DIR / "config" / "follow_strategy.yaml"
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)['bluesky_follow']

    def _connect_bluesky(self) -> Client:
        """Connect to Bluesky API."""
        if self.client is None:
            handle = os.getenv("BLUESKY_HANDLE")
            app_password = os.getenv("BLUESKY_APP_PASSWORD")

            if not handle or not app_password:
                raise ValueError("Missing BLUESKY_HANDLE or BLUESKY_APP_PASSWORD")

            self.client = Client()
            self.client.login(handle, app_password)
            logger.info(f"Connected to Bluesky as {handle}")

        return self.client

    def search_targets_by_hashtag(self, hashtag: str, limit: int = 50) -> list:
        """
        Search for accounts that posted with a given hashtag.
        Returns a list of filtered profiles.
        """
        client = self._connect_bluesky()
        targets = []

        try:
            response = client.app.bsky.feed.search_posts(
                params={"q": f"#{hashtag}", "limit": limit}
            )

            seen_dids = set()
            for post in response.posts:
                author = post.author

                if author.did in seen_dids:
                    continue
                seen_dids.add(author.did)

                if self._passes_filters(author):
                    segment = self._determine_segment(author)
                    if segment:
                        targets.append({
                            "did": author.did,
                            "handle": author.handle,
                            "display_name": getattr(author, 'display_name', None),
                            "followers_count": getattr(author, 'followers_count', 0),
                            "following_count": getattr(author, 'following_count', 0),
                            "segment": segment,
                            "source_hashtag": hashtag
                        })

        except Exception as e:
            logger.error(f"Error searching hashtag {hashtag}: {e}")

        return targets

    def _passes_filters(self, profile) -> bool:
        """Check if a profile passes all filters."""
        filters = self.config['filters']

        # Bot detection check
        if filters.get('exclude_bots', True):
            bot_config = filters.get('bot_detection', {})
            deep_check = bot_config.get('deep_check_enabled', False)
            min_confidence = bot_config.get('min_confidence_to_skip', 50)

            bot_check = self.bot_detector.is_bot(profile, deep_check=deep_check)
            if bot_check["is_bot"] and bot_check["confidence"] >= min_confidence:
                handle = getattr(profile, 'handle', 'unknown')
                logger.info(f"Skipping bot: @{handle} (confidence: {bot_check['confidence']}%, reasons: {bot_check['reasons']})")
                return False

        followers = getattr(profile, 'followers_count', 0) or 0
        following = getattr(profile, 'following_count', 0) or 0

        # Ratio following/followers
        if followers > 0:
            ratio = following / followers
            if ratio > filters['max_following_ratio']:
                return False

        # Minimum followers
        if followers < 10:
            return False

        # Check if already following
        if filters.get('exclude_following_back', True):
            if self._already_following(profile.did):
                return False

        return True

    def _determine_segment(self, profile) -> Optional[str]:
        """Determine segment based on follower count."""
        followers = getattr(profile, 'followers_count', 0) or 0

        for segment in self.config['segments']:
            if 'min_followers' in segment and 'max_followers' in segment:
                if segment['min_followers'] <= followers <= segment['max_followers']:
                    return segment['name']

        # For engaged/creators segments, accept anyone with decent followers
        if followers >= 10:
            return "engaged"

        return None

    def _already_following(self, did: str) -> bool:
        """Check if we're already following this account."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM bluesky_follows WHERE did = ? AND unfollowed_at IS NULL",
            (did,)
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def _get_today_follows_count(self, segment: str = None) -> int:
        """Count follows made today (total or by segment)."""
        conn = get_connection()
        cursor = conn.cursor()

        today = datetime.now().strftime("%Y-%m-%d")

        if segment:
            cursor.execute(
                """SELECT COUNT(*) FROM bluesky_follows
                   WHERE DATE(followed_at) = ? AND segment = ?""",
                (today, segment)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM bluesky_follows WHERE DATE(followed_at) = ?",
                (today,)
            )

        count = cursor.fetchone()[0]
        conn.close()
        return count

    def _can_follow_segment(self, segment_name: str) -> bool:
        """Check if we can still follow in this segment today."""
        segment_config = next(
            (s for s in self.config['segments'] if s['name'] == segment_name),
            None
        )
        if not segment_config:
            return False

        current_count = self._get_today_follows_count(segment_name)
        return current_count < segment_config.get('daily_quota', 5)

    def follow_account(self, target: dict, dry_run: bool = False) -> dict:
        """Follow an account and record in database."""
        if dry_run:
            logger.info(f"[DRY RUN] Would follow @{target['handle']} ({target['segment']})")
            return {"success": True, "dry_run": True}

        client = self._connect_bluesky()

        try:
            client.app.bsky.graph.follow.create(
                repo=client.me.did,
                record={
                    "subject": target['did'],
                    "createdAt": datetime.utcnow().isoformat() + "Z"
                }
            )

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO bluesky_follows
                (did, handle, display_name, followers_count, following_count, segment, source_hashtag)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                target['did'],
                target['handle'],
                target['display_name'],
                target['followers_count'],
                target['following_count'],
                target['segment'],
                target['source_hashtag']
            ))
            conn.commit()
            conn.close()

            logger.info(f"Followed @{target['handle']} ({target['segment']}, {target['followers_count']} followers)")
            return {"success": True}

        except Exception as e:
            logger.error(f"Error following @{target['handle']}: {e}")
            return {"success": False, "error": str(e)}

    def run_follow_session(self, dry_run: bool = False) -> int:
        """
        Execute a follow session.
        Respects quotas per segment and spaces follows by 10+ minutes.
        Returns number of accounts followed.
        """
        init_database()

        total_today = self._get_today_follows_count()
        daily_limit = self.config['daily_limit']

        if total_today >= daily_limit:
            logger.info(f"Daily limit reached ({total_today}/{daily_limit})")
            return 0

        remaining = daily_limit - total_today
        logger.info(f"Follow session: {total_today}/{daily_limit} today, {remaining} remaining")

        # Collect targets
        all_targets = []
        for hashtag in self.config['search_hashtags']:
            targets = self.search_targets_by_hashtag(hashtag, limit=30)
            all_targets.extend(targets)
            time.sleep(1)

        # Deduplicate
        seen = set()
        unique_targets = []
        for t in all_targets:
            if t['did'] not in seen:
                seen.add(t['did'])
                unique_targets.append(t)

        logger.info(f"Found {len(unique_targets)} unique targets")

        # Group by segment
        by_segment = {}
        for t in unique_targets:
            seg = t['segment']
            if seg not in by_segment:
                by_segment[seg] = []
            by_segment[seg].append(t)

        # Follow respecting quotas
        followed = 0
        min_delay = self.config.get('min_delay_between_follows', 600)

        for segment_name, targets in by_segment.items():
            if not self._can_follow_segment(segment_name):
                logger.info(f"Segment {segment_name}: quota reached")
                continue

            random.shuffle(targets)

            for target in targets:
                if not self._can_follow_segment(segment_name):
                    break
                if followed >= remaining:
                    break

                result = self.follow_account(target, dry_run=dry_run)
                if result['success']:
                    followed += 1

                    if not dry_run and followed < remaining:
                        delay = min_delay + random.randint(0, 300)
                        logger.info(f"Waiting {delay//60} minutes before next follow...")
                        time.sleep(delay)

        logger.info(f"Session complete: {followed} new follows")
        return followed

    def check_follow_backs(self, dry_run: bool = False) -> int:
        """Check who has followed us back."""
        client = self._connect_bluesky()
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT did, handle FROM bluesky_follows
            WHERE follow_back = FALSE
            AND unfollowed_at IS NULL
            AND followed_at < datetime('now', '-1 day')
            AND (follow_back_checked_at IS NULL OR follow_back_checked_at < datetime('now', '-1 day'))
        """)

        to_check = cursor.fetchall()
        logger.info(f"Checking {len(to_check)} accounts for follow-back...")

        follow_backs = 0
        for row in to_check:
            did, handle = row['did'], row['handle']
            try:
                # Get our followers
                response = client.app.bsky.graph.get_followers(
                    params={"actor": client.me.did, "limit": 100}
                )

                follower_dids = [f.did for f in response.followers]
                is_following_back = did in follower_dids

                cursor.execute("""
                    UPDATE bluesky_follows
                    SET follow_back = ?, follow_back_checked_at = CURRENT_TIMESTAMP
                    WHERE did = ?
                """, (is_following_back, did))

                status = "follows back" if is_following_back else "no follow back"
                logger.info(f"@{handle}: {status}")

                if is_following_back:
                    follow_backs += 1

                time.sleep(1)

            except Exception as e:
                logger.error(f"Error checking @{handle}: {e}")

        conn.commit()
        conn.close()

        logger.info(f"Found {follow_backs} follow-backs")
        return follow_backs

    def unfollow_non_followers(self, dry_run: bool = False) -> int:
        """Unfollow accounts that haven't followed back after N days."""
        client = self._connect_bluesky()
        conn = get_connection()
        cursor = conn.cursor()

        days = self.config.get('unfollow_after_days', 7)

        cursor.execute("""
            SELECT did, handle FROM bluesky_follows
            WHERE follow_back = FALSE
            AND unfollowed_at IS NULL
            AND followed_at < datetime('now', ? || ' days')
        """, (f"-{days}",))

        to_unfollow = cursor.fetchall()
        logger.info(f"Found {len(to_unfollow)} accounts to unfollow (no follow-back after {days} days)")

        unfollowed = 0
        for row in to_unfollow:
            did, handle = row['did'], row['handle']

            if dry_run:
                logger.info(f"[DRY RUN] Would unfollow @{handle}")
                unfollowed += 1
                continue

            try:
                # Find and delete the follow record
                response = client.app.bsky.graph.get_follows(
                    params={"actor": client.me.did, "limit": 100}
                )

                for follow in response.follows:
                    if follow.did == did:
                        # Delete the follow using the record URI
                        # The follow record URI is stored in the response
                        try:
                            client.app.bsky.graph.follow.delete(
                                repo=client.me.did,
                                rkey=follow.uri.split('/')[-1]
                            )
                        except:
                            pass
                        break

                cursor.execute("""
                    UPDATE bluesky_follows
                    SET unfollowed_at = CURRENT_TIMESTAMP
                    WHERE did = ?
                """, (did,))

                logger.info(f"Unfollowed @{handle}")
                unfollowed += 1
                time.sleep(2)

            except Exception as e:
                logger.error(f"Error unfollowing @{handle}: {e}")

        conn.commit()
        conn.close()
        logger.info(f"Unfollowed {unfollowed} accounts")
        return unfollowed

    def get_stats(self) -> dict:
        """Get follow system statistics."""
        conn = get_connection()
        cursor = conn.cursor()

        stats = {}

        # Total following
        cursor.execute("SELECT COUNT(*) FROM bluesky_follows WHERE unfollowed_at IS NULL")
        stats['total_following'] = cursor.fetchone()[0]

        # Follow backs
        cursor.execute("SELECT COUNT(*) FROM bluesky_follows WHERE follow_back = TRUE AND unfollowed_at IS NULL")
        stats['follow_backs'] = cursor.fetchone()[0]

        # By segment
        cursor.execute("""
            SELECT segment, COUNT(*) FROM bluesky_follows
            WHERE unfollowed_at IS NULL
            GROUP BY segment
        """)
        stats['by_segment'] = dict(cursor.fetchall())

        # Today
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(*) FROM bluesky_follows WHERE DATE(followed_at) = ?", (today,))
        stats['today'] = cursor.fetchone()[0]

        # Follow-back rate
        if stats['total_following'] > 0:
            stats['follow_back_rate'] = round(stats['follow_backs'] / stats['total_following'] * 100, 1)
        else:
            stats['follow_back_rate'] = 0

        # Daily limit
        stats['daily_limit'] = self.config['daily_limit']

        conn.close()
        return stats
