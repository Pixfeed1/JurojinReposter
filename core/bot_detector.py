"""
Bot detection module for Bluesky.
Combines external API checks with local heuristics to identify bot accounts.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests
from atproto import Client

logger = logging.getLogger(__name__)


class BotDetector:
    """
    Detects bot accounts before following them.
    Uses local heuristics by default (fast). External API disabled.
    """

    def __init__(self):
        self.client = None
        self.bskycheck_enabled = False  # Disabled by default (too slow/unstable)

    def _connect_bluesky(self) -> Client:
        """Connect to Bluesky API."""
        if self.client is None:
            handle = os.getenv("BLUESKY_HANDLE")
            app_password = os.getenv("BLUESKY_APP_PASSWORD")

            if not handle or not app_password:
                raise ValueError("Missing BLUESKY_HANDLE or BLUESKY_APP_PASSWORD")

            self.client = Client()
            self.client.login(handle, app_password)

        return self.client

    def check_bskycheck_api(self, handle: str) -> Optional[dict]:
        """
        Check via bskycheck.com API if enabled.
        Disabled by default - too slow/unstable.
        Returns: {"is_bot": bool, "confidence": float, "source": "bskycheck"}
        """
        if not self.bskycheck_enabled:
            return None

        try:
            url = f"https://bskycheck.com/api/botcheck?handle={handle}"
            response = requests.get(url, timeout=2)  # Short timeout

            if response.status_code == 200:
                data = response.json()
                return {
                    "is_bot": data.get("is_bot", False),
                    "confidence": data.get("confidence", 0),
                    "source": "bskycheck"
                }
        except:
            # Silently disable on any error
            self.bskycheck_enabled = False

        return None

    def analyze_profile(self, profile) -> dict:
        """
        Heuristic analysis of a profile.
        Returns a score from 0 (human) to 100 (certain bot).
        """
        score = 0
        reasons = []

        # 1. No avatar = suspicious
        if not getattr(profile, 'avatar', None):
            score += 15
            reasons.append("no_avatar")

        # 2. No bio = suspicious
        description = getattr(profile, 'description', None)
        if not description or len(description) < 10:
            score += 15
            reasons.append("no_bio")

        # 3. Following/followers ratio very unbalanced
        followers = getattr(profile, 'followers_count', 0) or 0
        following = getattr(profile, 'following_count', 0) or 0

        if followers > 0:
            ratio = following / followers
            if ratio > 10:
                score += 25
                reasons.append(f"high_ratio:{ratio:.1f}")
            elif ratio > 5:
                score += 15
                reasons.append(f"suspicious_ratio:{ratio:.1f}")
        elif following > 100:
            score += 30
            reasons.append("zero_followers_mass_following")

        # 4. Generic name or bot pattern
        display_name = (getattr(profile, 'display_name', '') or "").lower()
        handle = (getattr(profile, 'handle', '') or "").lower()

        bot_patterns = [
            "bot", "auto", "feed", "mirror", "repost",
            "news", "update", "alert", "notify", "tracker"
        ]

        for pattern in bot_patterns:
            if pattern in display_name or pattern in handle:
                score += 20
                reasons.append(f"bot_pattern:{pattern}")
                break

        # 5. Handle with many digits
        digits_in_handle = sum(c.isdigit() for c in handle)
        if digits_in_handle > 5:
            score += 10
            reasons.append("many_digits_in_handle")

        return {
            "score": min(score, 100),
            "reasons": reasons,
            "is_bot": score >= 50,
            "source": "local_heuristics"
        }

    def analyze_posting_behavior(self, did: str, limit: int = 20) -> dict:
        """
        Analyze posting behavior.
        Returns an additional score.
        """
        client = self._connect_bluesky()
        score = 0
        reasons = []

        try:
            response = client.app.bsky.feed.get_author_feed(
                params={"actor": did, "limit": limit}
            )

            posts = response.feed

            if len(posts) == 0:
                return {"score": 10, "reasons": ["no_posts"]}

            # Analyze posts
            links_count = 0
            post_times = []

            for item in posts:
                post = item.post.record

                # Check for links/embeds
                if hasattr(post, 'embed') and post.embed:
                    links_count += 1

                # Collect timestamps
                if hasattr(post, 'createdAt'):
                    post_times.append(post.createdAt)

            # Too many links = likely spam
            link_ratio = links_count / len(posts) if posts else 0
            if link_ratio > 0.9:
                score += 20
                reasons.append(f"high_link_ratio:{link_ratio:.0%}")

            # Posts at too regular intervals = bot
            if len(post_times) >= 5:
                intervals = []
                sorted_times = sorted(post_times)
                for i in range(1, len(sorted_times)):
                    try:
                        t1 = datetime.fromisoformat(sorted_times[i-1].replace('Z', '+00:00'))
                        t2 = datetime.fromisoformat(sorted_times[i].replace('Z', '+00:00'))
                        intervals.append((t2 - t1).total_seconds())
                    except:
                        pass

                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    variance = sum((x - avg_interval) ** 2 for x in intervals) / len(intervals)

                    if variance < 100 and avg_interval < 300:
                        score += 25
                        reasons.append("mechanical_timing")

            return {"score": score, "reasons": reasons}

        except Exception as e:
            logger.error(f"Error analyzing posting behavior: {e}")
            return {"score": 0, "reasons": ["analysis_error"]}

    def is_bot(self, profile, deep_check: bool = False) -> dict:
        """
        Main method: check if an account is a bot.
        Uses fast local heuristics by default.

        Args:
            profile: Bluesky profile object
            deep_check: if True, also analyze posts (slower, use sparingly)

        Returns:
            {
                "is_bot": bool,
                "confidence": float (0-100),
                "reasons": list,
                "source": str
            }
        """
        # Fast local profile analysis (instant)
        profile_analysis = self.analyze_profile(profile)
        total_score = profile_analysis["score"]
        all_reasons = profile_analysis["reasons"]

        # Deep check only if explicitly requested (slower - analyzes posts)
        if deep_check and 30 <= total_score <= 70:
            did = getattr(profile, 'did', None)
            if did:
                behavior_analysis = self.analyze_posting_behavior(did)
                total_score += behavior_analysis["score"]
                all_reasons.extend(behavior_analysis["reasons"])

        return {
            "is_bot": total_score >= 50,
            "confidence": min(total_score, 100),
            "reasons": all_reasons,
            "source": "local_heuristics"
        }
