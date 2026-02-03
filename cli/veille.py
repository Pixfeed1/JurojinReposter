"""
CLI command for RSS Veille module.
Monitors RSS feeds, analyzes relevance, generates EEAT briefs, creates WordPress drafts.

Usage:
    python -m cli.veille fetch          # Fetch RSS feeds
    python -m cli.veille analyze        # Analyze relevance with Groq
    python -m cli.veille generate       # Generate briefs + WordPress drafts
    python -m cli.veille run            # Full pipeline (fetch + analyze + generate)
    python -m cli.veille status         # Show statistics
    python -m cli.veille --dry-run      # Don't actually create WordPress drafts
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH, BASE_DIR
from core.database import (
    init_database, init_veille_tables,
    save_rss_article, get_rss_articles_for_analysis,
    update_rss_relevance, get_relevant_rss_articles,
    save_veille_brief, update_brief_wp_draft,
    get_veille_stats, is_rss_article_known
)
from sources.rss_fetcher import fetch_all_feeds, get_feed_stats
from ai.relevance_analyzer import analyze_batch
from ai.brief_generator import generate_brief
from publishers.wordpress_draft import publish_veille_brief, check_api_status
import yaml


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


def load_config() -> dict:
    """Load RSS feeds configuration."""
    config_path = BASE_DIR / "config" / "rss_feeds.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def cmd_fetch():
    """Fetch all RSS feeds and save new articles."""
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("VEILLE - Fetching RSS feeds...")

    # Initialize tables
    init_database()
    init_veille_tables()

    # Fetch feeds
    articles = fetch_all_feeds()

    # Save to database
    new_count = 0
    duplicate_count = 0

    for article in articles:
        if is_rss_article_known(article['url']):
            duplicate_count += 1
            continue

        article_id = save_rss_article(article)
        if article_id:
            new_count += 1
        else:
            duplicate_count += 1

    print(f"\n📡 RSS FETCH COMPLETE")
    print(f"   New articles: {new_count}")
    print(f"   Duplicates skipped: {duplicate_count}")
    print(f"   Total fetched: {len(articles)}")

    logger.info(f"Fetch complete: {new_count} new, {duplicate_count} duplicates")
    return new_count


def cmd_analyze():
    """Analyze unprocessed articles for relevance."""
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("VEILLE - Analyzing relevance...")

    # Initialize tables
    init_database()
    init_veille_tables()

    # Get articles to analyze
    articles = get_rss_articles_for_analysis(limit=50)

    if not articles:
        print("\n✅ No articles to analyze")
        return 0

    print(f"\n🔍 Analyzing {len(articles)} articles...")

    # Analyze batch
    results = analyze_batch(articles)

    # Save results
    relevant_count = 0
    for article_id, score, reason in results:
        if article_id:
            update_rss_relevance(article_id, score)
            if score >= 7:
                relevant_count += 1

    print(f"\n📊 ANALYSIS COMPLETE")
    print(f"   Articles analyzed: {len(results)}")
    print(f"   Relevant (score >= 7): {relevant_count}")

    logger.info(f"Analysis complete: {len(results)} analyzed, {relevant_count} relevant")
    return relevant_count


def cmd_generate(dry_run: bool = False):
    """Generate briefs for relevant articles."""
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("VEILLE - Generating briefs...")

    # Initialize tables
    init_database()
    init_veille_tables()

    # Load config
    config = load_config()
    settings = config.get('settings', {})
    max_briefs = settings.get('max_briefs_per_run', 5)
    min_score = settings.get('min_relevance_score', 7)

    # Get relevant articles without briefs
    articles = get_relevant_rss_articles(min_score=min_score, limit=max_briefs)

    if not articles:
        print("\n✅ No articles need brief generation")
        return 0

    print(f"\n📝 Generating {len(articles)} briefs...")

    briefs_created = 0
    drafts_created = 0

    for article in articles:
        print(f"\n  Processing: {article['title'][:50]}...")

        # Generate brief
        brief_data = generate_brief(article)

        # Save brief to database
        brief_id = save_veille_brief(article['id'], brief_data)
        briefs_created += 1

        print(f"    ✓ Brief generated: {brief_data['title'][:40]}...")

        # Publish to WordPress
        result = publish_veille_brief(brief_data, article, dry_run=dry_run)

        if result['success']:
            drafts_created += 1
            if not dry_run:
                update_brief_wp_draft(brief_id, result['post_id'], result.get('url', ''))
                print(f"    ✓ WordPress draft: ID={result['post_id']}")
            else:
                print(f"    ➡️  [DRY RUN] Would create WordPress draft")
        else:
            print(f"    ✗ WordPress error: {result.get('error', 'Unknown')}")

    print(f"\n📋 GENERATION COMPLETE")
    print(f"   Briefs created: {briefs_created}")
    print(f"   WordPress drafts: {drafts_created}")

    logger.info(f"Generation complete: {briefs_created} briefs, {drafts_created} drafts")
    return briefs_created


def cmd_run(dry_run: bool = False):
    """Run full pipeline: fetch -> analyze -> generate."""
    logger = logging.getLogger(__name__)

    print("\n" + "=" * 60)
    print("🚀 VEILLE RSS - FULL PIPELINE")
    print("=" * 60)

    # Step 1: Fetch
    print("\n[1/3] Fetching RSS feeds...")
    new_articles = cmd_fetch()

    # Step 2: Analyze
    print("\n[2/3] Analyzing relevance...")
    relevant = cmd_analyze()

    # Step 3: Generate
    print("\n[3/3] Generating briefs...")
    briefs = cmd_generate(dry_run=dry_run)

    print("\n" + "=" * 60)
    print("✅ VEILLE COMPLETE")
    print(f"   New articles: {new_articles}")
    print(f"   Relevant found: {relevant}")
    print(f"   Briefs generated: {briefs}")
    print("=" * 60)

    logger.info(f"Full pipeline complete: {new_articles} new, {relevant} relevant, {briefs} briefs")


def cmd_status():
    """Show veille module statistics."""
    print("\n" + "=" * 60)
    print("📊 VEILLE RSS - STATUS")
    print("=" * 60)

    # Initialize tables
    init_database()
    init_veille_tables()

    # Feed stats
    feed_stats = get_feed_stats()
    print(f"\n📡 RSS FEEDS CONFIGURED:")
    print(f"   Total feeds: {feed_stats['total_feeds']}")
    print(f"   By category:")
    for cat, count in feed_stats['by_category'].items():
        print(f"     - {cat}: {count}")

    # Veille stats
    veille_stats = get_veille_stats()
    print(f"\n📰 ARTICLES:")
    print(f"   Total fetched: {veille_stats['total_rss_articles']}")
    print(f"   Analyzed: {veille_stats['analyzed_articles']}")
    print(f"   Relevant (score >= 7): {veille_stats['relevant_articles']}")
    print(f"   Recent (48h): {veille_stats['recent_articles']}")

    print(f"\n📝 BRIEFS:")
    print(f"   Generated: {veille_stats['briefs_generated']}")
    print(f"   WordPress drafts: {veille_stats['wp_drafts_created']}")

    if veille_stats.get('by_source'):
        print(f"\n📊 TOP SOURCES:")
        for source, count in list(veille_stats['by_source'].items())[:5]:
            print(f"   - {source}: {count}")

    # WordPress status
    print(f"\n🌐 WORDPRESS API:")
    wp_status = check_api_status()
    if wp_status.get('connected'):
        print(f"   ✅ Connected ({wp_status.get('username', 'OK')})")
    else:
        print(f"   ❌ Not connected: {wp_status.get('error', 'Unknown')}")

    print("=" * 60)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        description='Veille RSS - Monitor feeds, analyze relevance, generate EEAT briefs'
    )
    parser.add_argument('command', nargs='?', default='status',
                        choices=['fetch', 'analyze', 'generate', 'run', 'status'],
                        help='Command to run')
    parser.add_argument('--dry-run', action='store_true',
                        help='Don\'t create WordPress drafts')

    args = parser.parse_args()

    if args.command == 'fetch':
        cmd_fetch()
    elif args.command == 'analyze':
        cmd_analyze()
    elif args.command == 'generate':
        cmd_generate(dry_run=args.dry_run)
    elif args.command == 'run':
        cmd_run(dry_run=args.dry_run)
    else:  # status
        cmd_status()


if __name__ == '__main__':
    main()
