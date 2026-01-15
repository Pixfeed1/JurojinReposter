"""
CLI command for Bluesky follow/unfollow management.

Usage:
    python -m cli.follow run              # Execute follow session
    python -m cli.follow run --dry-run    # Simulate without following
    python -m cli.follow check            # Check for follow-backs
    python -m cli.follow unfollow         # Unfollow non-followers
    python -m cli.follow stats            # Show statistics
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH
from core.bluesky_follower import BlueskyFollower


def setup_logging():
    """Configure logging."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler()
        ]
    )


def main():
    parser = argparse.ArgumentParser(description='Bluesky Follow Manager')
    parser.add_argument('action', choices=['run', 'check', 'unfollow', 'stats'],
                        help='Action to perform')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without actually following/unfollowing')

    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info(f"Bluesky Follow Manager - {args.action}")

    try:
        follower = BlueskyFollower()

        if args.action == 'run':
            logger.info("Starting follow session...")
            followed = follower.run_follow_session(dry_run=args.dry_run)
            logger.info(f"Follow session complete: {followed} new follows")

        elif args.action == 'check':
            logger.info("Checking follow-backs...")
            follow_backs = follower.check_follow_backs(dry_run=args.dry_run)
            logger.info(f"Found {follow_backs} follow-backs")

        elif args.action == 'unfollow':
            logger.info("Unfollowing non-followers...")
            unfollowed = follower.unfollow_non_followers(dry_run=args.dry_run)
            logger.info(f"Unfollowed {unfollowed} accounts")

        elif args.action == 'stats':
            stats = follower.get_stats()
            print("\n=== Bluesky Follow Stats ===")
            print(f"Total following: {stats['total_following']}")
            print(f"Follow-backs: {stats['follow_backs']} ({stats['follow_back_rate']}%)")
            print(f"Today: {stats['today']}/{stats['daily_limit']}")
            print("\nBy segment:")
            for seg, count in stats.get('by_segment', {}).items():
                print(f"  {seg}: {count}")

    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

    logger.info("=" * 50)


if __name__ == "__main__":
    main()
