"""
CLI command for TrendsToWordPress.
Monitors Google Trends and creates article briefs as WordPress drafts.

Usage:
    python -m cli.trends              # Run with core keywords (less API calls)
    python -m cli.trends --full       # Run with all keywords (more API calls, may hit rate limits)
    python -m cli.trends --offline    # Skip Google Trends, use popular topics from config
    python -m cli.trends --dry-run    # Show what would be created without creating
    python -m cli.trends --trends     # Only show trends without creating drafts
    python -m cli.trends --status     # Check API connectivity
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_PATH, BASE_DIR
from core.database import init_database
from sources.google_trends import fetch_all_trends, fetch_predefined_trends
from core.article_matcher import find_opportunities, get_best_opportunities
from ai.prompt_generator import generate_article_brief
from publishers.wordpress_draft import publish_brief, check_api_status
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
    """Load trends configuration."""
    config_path = BASE_DIR / "config" / "trends_config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def show_trends(trends: dict):
    """Display fetched trends in a readable format."""
    print("\n" + "=" * 60)
    print("📊 GOOGLE TRENDS - RESULTATS")
    print("=" * 60)

    print("\n🎯 TENDANCES SUR NOS MOTS-CLES :")
    print("-" * 40)
    for trend in trends.get('predefined', [])[:15]:
        bar = "█" * (trend['interest'] // 5)
        print(f"  {trend['keyword'][:30]:<30} {trend['interest']:>3} {bar}")

    print("\n🔥 TENDANCES GENERALES FRANCE :")
    print("-" * 40)
    for trend in trends.get('general', [])[:15]:
        source = trend.get('source', 'general')[:15]
        print(f"  {trend['keyword'][:40]:<40} [{source}]")


def show_opportunities(opportunities: list):
    """Display opportunities in a readable format."""
    print("\n" + "=" * 60)
    print("💡 OPPORTUNITES DE CONTENU")
    print("=" * 60)

    for i, opp in enumerate(opportunities[:10], 1):
        print(f"\n{i}. {opp['trend']}")
        print(f"   Type: {opp['opportunity_type']} | Score: {opp['score']}")
        print(f"   Articles lies: {len(opp.get('related_articles', []))}")
        if opp.get('related_articles'):
            for article in opp['related_articles'][:2]:
                print(f"     - {article['title'][:50]}...")


def get_offline_trends() -> dict:
    """
    Generate fake trends from config keywords (no Google API calls).
    Useful when rate-limited by Google.
    """
    config = load_config()
    keywords = config.get('keywords_core', [])

    # Generate fake trends with random-ish interest scores
    import random
    predefined = []
    for keyword in keywords:
        predefined.append({
            'keyword': keyword,
            'category': 'core',
            'interest': random.randint(40, 80),  # Simulated interest
            'type': 'predefined'
        })

    # Sort by "interest"
    predefined.sort(key=lambda x: x['interest'], reverse=True)

    return {
        'predefined': predefined,
        'general': []
    }


def run_trends_check(dry_run: bool = False, force_full: bool = False, offline: bool = False):
    """Main function to check trends and create drafts."""
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    if offline:
        mode_str = "OFFLINE mode (no Google API)"
    elif force_full:
        mode_str = "FULL mode"
    else:
        mode_str = "CORE mode (8 keywords)"
    logger.info(f"TrendsToWordPress - Starting ({mode_str})...")

    # Initialize database
    init_database()

    # Load config
    config = load_config()
    max_prompts = config.get('matching', {}).get('max_prompts_per_day', 3)

    # Fetch trends
    if offline:
        logger.info("Using offline mode (no Google Trends API)...")
        trends = get_offline_trends()
    else:
        logger.info("Fetching Google Trends...")
        trends = fetch_all_trends(force_full=force_full)

    show_trends(trends)

    # Find opportunities
    logger.info("Analyzing opportunities...")
    opportunities = find_opportunities(
        trends.get('predefined', []),
        trends.get('general', [])
    )

    show_opportunities(opportunities)

    # Select best opportunities
    best = get_best_opportunities(opportunities, max_count=max_prompts)

    if not best:
        logger.info("No significant opportunities found today")
        print("\n❌ Pas d'opportunite significative detectee")
        return

    print(f"\n✅ {len(best)} opportunite(s) selectionnee(s) pour generation de brief")

    # Generate and publish briefs
    created_count = 0
    for opp in best:
        logger.info(f"Generating brief for: {opp['trend']}")

        # Generate the brief
        brief = generate_article_brief(opp)

        print(f"\n📝 Brief genere: {brief['title']}")
        print(f"   Type: {brief['article_type']} | Urgence: {brief['urgency']}")
        print(f"   Categorie: {brief['category']}")
        print(f"   Tags: {', '.join(brief['tags'][:5])}")

        # Publish to WordPress
        result = publish_brief(brief, dry_run=dry_run)

        if result['success']:
            created_count += 1
            if dry_run:
                print("   ➡️  [DRY RUN] Draft would be created")
            else:
                print(f"   ✅ Draft cree: ID={result['post_id']}")
                if result.get('url'):
                    print(f"   🔗 {result['url']}")
        else:
            print(f"   ❌ Erreur: {result['error']}")

    logger.info(f"TrendsToWordPress completed. Briefs created: {created_count}")
    print(f"\n{'=' * 60}")
    print(f"✅ Termine - {created_count} brief(s) cree(s)")
    print("=" * 60)


def check_status():
    """Check API connectivity."""
    print("\n📡 Verification des connexions API...")
    print("-" * 40)

    # Check WordPress
    wp_status = check_api_status()
    if wp_status['connected']:
        print(f"✅ WordPress: Connecte ({wp_status.get('username', 'OK')})")
    else:
        print(f"❌ WordPress: {wp_status.get('error', 'Non connecte')}")

    # Check Groq
    from ai.prompt_generator import get_groq_client
    groq_client = get_groq_client()
    if groq_client:
        print("✅ Groq: Configure")
    else:
        print("❌ Groq: Non configure (GROQ_API_KEY manquant)")

    # Check pytrends
    try:
        from pytrends.request import TrendReq
        print("✅ pytrends: Installe")
    except ImportError:
        print("❌ pytrends: Non installe (pip install pytrends)")


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description='TrendsToWordPress - Monitor trends and create article briefs')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be created without creating')
    parser.add_argument('--trends', action='store_true', help='Only show trends without creating drafts')
    parser.add_argument('--status', action='store_true', help='Check API connectivity')
    parser.add_argument('--full', action='store_true', help='Use full keywords list (more API calls, slower)')
    parser.add_argument('--offline', action='store_true', help='Skip Google Trends API (use when rate-limited)')

    args = parser.parse_args()

    if args.status:
        check_status()
        return

    if args.trends:
        logger.info("Fetching trends only...")
        if args.offline:
            trends = get_offline_trends()
        else:
            trends = fetch_all_trends(force_full=args.full)
        show_trends(trends)

        opportunities = find_opportunities(
            trends.get('predefined', []),
            trends.get('general', [])
        )
        show_opportunities(opportunities)
        return

    # Full run
    run_trends_check(dry_run=args.dry_run, force_full=args.full, offline=args.offline)


if __name__ == '__main__':
    main()
