"""
CLI command for managing the reposter.

Usage:
    python -m cli.manage --stats
    python -m cli.manage --queue
    python -m cli.manage --exclude 123
    python -m cli.manage --include 123
    python -m cli.manage --force 456
    python -m cli.manage --history 20
    python -m cli.manage --top 10 --platform twitter
"""

import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH
from core.database import (
    init_database,
    get_stats,
    get_pending_queue,
    get_failed_queue,
    get_repost_history,
    exclude_article,
    get_article_by_id,
    set_priority_boost
)
from core.selector import get_top_articles
from core.scoring import recalculate_all_scores


def setup_logging():
    """Configure logging."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )


def show_stats():
    """Display database statistics."""
    stats = get_stats()

    print("\n=== Jurojin Reposter Statistics ===\n")
    print(f"Total articles:     {stats['total_articles']}")
    print(f"Active articles:    {stats['active_articles']}")
    print(f"Evergreen articles: {stats['evergreen_articles']}")
    print(f"\nTotal reposts:      {stats['total_reposts']}")

    if stats['reposts_by_platform']:
        print("\nReposts by platform:")
        for platform, count in stats['reposts_by_platform'].items():
            print(f"  {platform}: {count}")

    print(f"\nPending in queue:   {stats['pending_queue']}")
    print(f"Failed in queue:    {stats['failed_queue']}")
    print()


def show_queue():
    """Display the current post queue."""
    pending = get_pending_queue()
    failed = get_failed_queue()

    print("\n=== Post Queue ===\n")

    if pending:
        print("Pending:")
        for item in pending:
            print(f"  [{item['id']}] {item['platform']}: {item['title'][:50]}...")
            print(f"       Scheduled: {item['scheduled_at']}")
    else:
        print("No pending items")

    print()

    if failed:
        print("Failed (awaiting retry):")
        for item in failed:
            print(f"  [{item['id']}] {item['platform']}: {item['title'][:50]}...")
            print(f"       Attempts: {item['attempts']}, Error: {item['error_message'][:50] if item['error_message'] else 'N/A'}")
    else:
        print("No failed items")

    print()


def show_history(limit: int):
    """Display repost history."""
    history = get_repost_history(limit)

    print(f"\n=== Last {limit} Reposts ===\n")

    if not history:
        print("No repost history")
        return

    for item in history:
        status = "OK" if item['success'] else "FAILED"
        posted_at = datetime.fromisoformat(item['posted_at']).strftime('%Y-%m-%d %H:%M')
        print(f"[{status}] {posted_at} - {item['platform']}")
        print(f"    {item['title'][:60]}...")
        print(f"    Accroche: {item['accroche'][:60]}...")
        if not item['success'] and item['error_message']:
            print(f"    Error: {item['error_message'][:60]}")
        print()


def show_top_articles(platform: str, limit: int):
    """Display top eligible articles for a platform."""
    articles = get_top_articles(platform, limit)

    print(f"\n=== Top {limit} Articles for {platform} ===\n")

    if not articles:
        print("No eligible articles found")
        return

    for i, article in enumerate(articles, 1):
        days_info = ""
        if article.get('days_since_repost') is not None:
            days_info = f" (last repost: {article['days_since_repost']}d ago)"
        else:
            days_info = " (never reposted)"

        print(f"{i}. [{article['score']}] {article['title'][:60]}...")
        print(f"   ID: {article['id']}, Category: {article['category']}{days_info}")
        print()


def exclude_or_include_article(article_id: int, exclude: bool):
    """Exclude or include an article."""
    article = get_article_by_id(article_id)

    if not article:
        print(f"Article {article_id} not found")
        return

    action = "excluded" if exclude else "included"
    exclude_article(article_id, exclude)

    # Recalculate score
    recalculate_all_scores()

    print(f"Article {article_id} ({article['title'][:50]}...) has been {action}")


def force_article(article_id: int, boost: int = 1000):
    """Force an article to be selected next by giving it a high priority boost."""
    article = get_article_by_id(article_id)

    if not article:
        print(f"Article {article_id} not found")
        return

    set_priority_boost(article_id, boost)
    recalculate_all_scores()

    new_article = get_article_by_id(article_id)
    print(f"Article {article_id} boosted. New score: {new_article['score']}")
    print(f"Title: {article['title'][:60]}...")
    print("\nNote: The boost will persist. Use --boost 0 to reset.")


def main():
    parser = argparse.ArgumentParser(description='Manage the Jurojin Reposter')
    parser.add_argument('--stats', action='store_true',
                        help='Show statistics')
    parser.add_argument('--queue', action='store_true',
                        help='Show post queue')
    parser.add_argument('--history', type=int, metavar='N',
                        help='Show last N reposts')
    parser.add_argument('--top', type=int, metavar='N',
                        help='Show top N eligible articles')
    parser.add_argument('--platform', choices=['twitter', 'facebook'],
                        default='twitter',
                        help='Platform for --top command')
    parser.add_argument('--exclude', type=int, metavar='ID',
                        help='Exclude article by ID')
    parser.add_argument('--include', type=int, metavar='ID',
                        help='Include previously excluded article by ID')
    parser.add_argument('--force', type=int, metavar='ID',
                        help='Force article to be selected next (high boost)')
    parser.add_argument('--boost', type=int, metavar='POINTS',
                        help='Set custom boost value for --force (default: 1000)')
    parser.add_argument('--recalculate', action='store_true',
                        help='Recalculate all article scores')

    args = parser.parse_args()

    setup_logging()

    # Initialize database
    init_database()

    # Handle commands
    if args.stats:
        show_stats()
    elif args.queue:
        show_queue()
    elif args.history:
        show_history(args.history)
    elif args.top:
        show_top_articles(args.platform, args.top)
    elif args.exclude:
        exclude_or_include_article(args.exclude, True)
    elif args.include:
        exclude_or_include_article(args.include, False)
    elif args.force:
        boost = args.boost if args.boost is not None else 1000
        force_article(args.force, boost)
    elif args.recalculate:
        count = recalculate_all_scores()
        print(f"Recalculated scores for {count} articles")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
