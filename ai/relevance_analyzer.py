"""
Relevance Analyzer for RSS articles.
Uses Groq API to score article relevance for jurojin.net.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

import yaml
from groq import Groq

from config.settings import GROQ_API_KEY, BASE_DIR

logger = logging.getLogger(__name__)

# Groq client
_groq_client = None


def get_groq_client() -> Optional[Groq]:
    """Get or create Groq client."""
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def load_feeds_config() -> dict:
    """Load RSS feeds configuration for boost keywords."""
    config_path = BASE_DIR / "config" / "rss_feeds.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


RELEVANCE_PROMPT = """Tu es un expert en curation de contenu pour jurojin.net, un blog francophone sur:
- Arts numériques (3D, VFX, animation, Blender, Cinema 4D, Maya, ZBrush)
- Tech et IA (Stable Diffusion, Midjourney, ComfyUI, sécurité informatique)
- Gaming (jeux vidéo, retro gaming, esports)
- Cinéma et séries (films, anime, Dragon Ball, Naruto, One Piece)
- Culture geek (science-fiction, cyberpunk, pop culture)

Analyse cet article et donne-lui un score de pertinence de 0 à 10 pour jurojin.net.

ARTICLE:
Titre: {title}
Source: {source}
Résumé: {summary}

CRITÈRES DE NOTATION:
- 9-10: Article TRÈS pertinent (Blender, logiciels 3D, IA générative, anime majeur)
- 7-8: Article pertinent (tech intéressante, gaming, VFX, sécurité)
- 5-6: Moyennement pertinent (tech générale, entertainment)
- 3-4: Peu pertinent (hors sujet mais tangentiel)
- 0-2: Pas pertinent (politique, sport, finance pure, etc.)

BONUS:
+2 si article sur Blender, ZBrush, Substance, Houdini, Unreal Engine
+1 si article sur anime (Dragon Ball, Naruto, One Piece, Chainsaw Man)
+1 si article sur IA générative (Stable Diffusion, Midjourney)
+1 si exclusivité, interview, ou annonce majeure

MALUS:
-2 si article trop généraliste sans angle technique
-1 si déjà couvert partout (buzz sans substance)

Réponds UNIQUEMENT avec un JSON valide:
{{"score": <0-10>, "reason": "<raison courte en français>"}}"""


def analyze_relevance(article: dict) -> dict:
    """
    Analyze the relevance of an article for jurojin.net.

    Args:
        article: Dict with title, summary, source_name

    Returns:
        Dict with score (0-10) and reason
    """
    client = get_groq_client()
    if not client:
        logger.error("Groq client not available")
        return {'score': 0, 'reason': 'Groq API non configurée'}

    # Apply keyword boosts first (before AI analysis)
    config = load_feeds_config()
    boost_keywords = config.get('boost_keywords', {})

    title_lower = article.get('title', '').lower()
    summary_lower = article.get('summary', '').lower()
    text = f"{title_lower} {summary_lower}"

    keyword_boost = 0
    for keyword in boost_keywords.get('high_boost', []):
        if keyword.lower() in text:
            keyword_boost = max(keyword_boost, 3)
            break
    if keyword_boost < 3:
        for keyword in boost_keywords.get('medium_boost', []):
            if keyword.lower() in text:
                keyword_boost = max(keyword_boost, 2)
                break
    if keyword_boost < 2:
        for keyword in boost_keywords.get('low_boost', []):
            if keyword.lower() in text:
                keyword_boost = max(keyword_boost, 1)
                break

    try:
        prompt = RELEVANCE_PROMPT.format(
            title=article.get('title', ''),
            source=article.get('source_name', ''),
            summary=article.get('summary', '')[:500]
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON response
        if "```" in content:
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
            if json_match:
                content = json_match.group(1).strip()

        if not content.startswith('{'):
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                content = json_match.group(0)

        result = json.loads(content)

        # Add keyword boost (capped at 10)
        final_score = min(10, result.get('score', 0) + keyword_boost)

        logger.info(f"Relevance for '{article.get('title', '')[:50]}...': {final_score} (AI: {result.get('score', 0)}, boost: +{keyword_boost})")

        return {
            'score': final_score,
            'reason': result.get('reason', 'Score ajusté avec boost keywords')
        }

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error for relevance: {e}")
        # Fallback: use keyword boost only
        return {
            'score': min(5 + keyword_boost, 10),
            'reason': f'Score basé sur keywords (boost +{keyword_boost})'
        }
    except Exception as e:
        logger.error(f"Error analyzing relevance: {e}")
        return {'score': 0, 'reason': f'Erreur: {str(e)[:50]}'}


def analyze_batch(articles: list, batch_size: int = 10) -> list:
    """
    Analyze relevance for a batch of articles.

    Args:
        articles: List of article dicts
        batch_size: Number of articles to process per batch

    Returns:
        List of (article_id, score, reason) tuples
    """
    results = []

    for i, article in enumerate(articles):
        logger.info(f"Analyzing article {i+1}/{len(articles)}: {article.get('title', '')[:40]}...")

        result = analyze_relevance(article)
        results.append((
            article.get('id'),
            result['score'],
            result['reason']
        ))

        # Small delay to avoid rate limits (Groq is generous but still)
        if (i + 1) % batch_size == 0:
            import time
            time.sleep(1)

    return results
