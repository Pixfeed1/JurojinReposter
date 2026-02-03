"""
EEAT Brief Generator for RSS articles.
Generates comprehensive article briefs for WordPress drafts.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY
from core.database import get_all_articles, find_related_article

logger = logging.getLogger(__name__)

# Groq client
_groq_client = None


def get_groq_client() -> Optional[Groq]:
    """Get or create Groq client."""
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


BRIEF_PROMPT = """Tu es un rédacteur expert SEO pour jurojin.net, un blog francophone sur les arts numériques, la tech, le gaming et la pop culture.

Génère un BRIEF DE RÉDACTION complet pour cet article.

ARTICLE SOURCE:
Titre: {title}
Source: {source}
Résumé: {summary}
Catégorie: {category}

ARTICLES JUROJIN.NET EXISTANTS (pour maillage interne):
{existing_articles}

FORMAT DU BRIEF (en français):
```
BRIEF DE RÉDACTION - [Titre SEO proposé]
=====================================

MOT-CLÉ PRINCIPAL: [mot-clé principal ciblé]
MOTS-CLÉS LONGUE TRAÎNE: [3-5 variations]

ANGLE JUROJIN: [Comment traiter le sujet avec le ton jurojin.net - expert mais décontracté, opinions tranchées]

STRUCTURE PROPOSÉE:
- Introduction (accroche + contexte, 100-150 mots)
- [H2] Section 1: [titre]
- [H2] Section 2: [titre]
- [H2] Section 3: [titre]
- Conclusion (avis tranché + ouverture)

SOURCES EXTERNES (backlinks crédibilité EEAT):
- [2-5 sources officielles: docs, sites éditeurs, .edu, .gov]

MAILLAGE INTERNE (liens vers jurojin.net):
- [Article existant] → ancre SEO suggérée
- (1 lien interne tous les 200-300 mots)
- ANCRES = mots-clés de l'article cible, JAMAIS "cliquez ici"

LONGUEUR CIBLE: [600-2500 mots selon complexité]

TON: Expert décontracté, tutoiement, opinions assumées
INTERDICTIONS: "dans le monde numérique", "il est important de noter", "en conclusion", formules IA génériques
```

Génère le brief complet maintenant."""


def find_internal_links(title: str, category: str, limit: int = 3) -> list:
    """Find existing jurojin.net articles for internal linking."""
    links = []

    # Get all articles
    articles = get_all_articles(include_excluded=False)

    # Extract keywords from title
    keywords = [w.lower() for w in title.split() if len(w) > 4]

    # Score articles by keyword match
    scored = []
    for article in articles:
        article_title = article.get('title', '').lower()
        article_category = article.get('category', '')

        score = 0
        # Category match
        if category and article_category and category.lower() in article_category.lower():
            score += 2

        # Keyword matches
        for keyword in keywords:
            if keyword in article_title:
                score += 3

        if score > 0:
            scored.append((score, article))

    # Sort by score and take top matches
    scored.sort(key=lambda x: x[0], reverse=True)

    for score, article in scored[:limit]:
        # Generate SEO anchor suggestion
        anchor = _suggest_anchor(article['title'])
        links.append({
            'url': article['url'],
            'title': article['title'],
            'anchor': anchor
        })

    return links


def _suggest_anchor(title: str) -> str:
    """Suggest an SEO-friendly anchor text from article title."""
    # Remove common stop words and keep meaningful parts
    stop_words = {'le', 'la', 'les', 'un', 'une', 'des', 'de', 'du', 'et', 'ou', 'pour', 'dans', 'avec', 'sur', 'par'}
    words = title.lower().split()

    # Take first 4-6 meaningful words
    meaningful = [w for w in words if w not in stop_words and len(w) > 2][:5]

    if meaningful:
        return ' '.join(meaningful)
    return title[:50]


def generate_brief(article: dict) -> dict:
    """
    Generate an EEAT brief for an article.

    Args:
        article: Dict with title, summary, source_name, category

    Returns:
        Dict with brief data
    """
    client = get_groq_client()
    if not client:
        logger.error("Groq client not available")
        return _fallback_brief(article)

    # Find internal links
    internal_links = find_internal_links(
        article.get('title', ''),
        article.get('category', '')
    )

    # Format internal links for prompt
    links_text = ""
    if internal_links:
        links_text = "\n".join([
            f"- {link['title'][:60]}... ({link['url']})"
            for link in internal_links
        ])
    else:
        links_text = "(Aucun article similaire trouvé)"

    try:
        prompt = BRIEF_PROMPT.format(
            title=article.get('title', ''),
            source=article.get('source_name', ''),
            summary=article.get('summary', '')[:800],
            category=article.get('category', 'Actualités'),
            existing_articles=links_text
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )

        brief_text = response.choices[0].message.content.strip()

        # Extract key info from brief
        title_match = re.search(r'BRIEF DE RÉDACTION - (.+?)[\n=]', brief_text)
        keyword_match = re.search(r'MOT-CLÉ PRINCIPAL:\s*(.+?)[\n]', brief_text)
        longtail_match = re.search(r'MOTS-CLÉS LONGUE TRAÎNE:\s*(.+?)[\n]', brief_text)
        angle_match = re.search(r'ANGLE JUROJIN:\s*(.+?)(?:\n\n|\nSTRUCTURE)', brief_text, re.DOTALL)
        length_match = re.search(r'LONGUEUR CIBLE:\s*(\d+)', brief_text)

        brief_title = title_match.group(1).strip() if title_match else article.get('title', '')

        return {
            'title': brief_title,
            'keyword_main': keyword_match.group(1).strip() if keyword_match else '',
            'keywords_longtail': longtail_match.group(1).strip() if longtail_match else '',
            'angle': angle_match.group(1).strip() if angle_match else '',
            'structure': '',  # Could parse this too
            'external_sources': '',
            'internal_links': json.dumps([{
                'url': l['url'],
                'title': l['title'],
                'anchor': l['anchor']
            } for l in internal_links]),
            'word_count_target': int(length_match.group(1)) if length_match else 1500,
            'tone_notes': 'Expert décontracté, tutoiement, opinions assumées',
            'full_brief': brief_text
        }

    except Exception as e:
        logger.error(f"Error generating brief: {e}")
        return _fallback_brief(article)


def _fallback_brief(article: dict) -> dict:
    """Generate a basic brief when AI fails."""
    title = article.get('title', 'Article sans titre')

    brief_text = f"""BRIEF DE RÉDACTION - {title}
=====================================

MOT-CLÉ PRINCIPAL: {title.split()[0] if title else 'à définir'}

MOTS-CLÉS LONGUE TRAÎNE: à rechercher

ANGLE JUROJIN: Couvrir l'actualité avec un regard expert et des opinions tranchées.

STRUCTURE PROPOSÉE:
- Introduction (accroche + contexte)
- [H2] Les points clés
- [H2] Analyse et implications
- [H2] Notre avis
- Conclusion

SOURCES EXTERNES:
- Source originale: {article.get('source_name', 'N/A')}

MAILLAGE INTERNE:
- À définir selon les articles existants

LONGUEUR CIBLE: 800-1200 mots

TON: Expert décontracté
"""

    return {
        'title': title,
        'keyword_main': '',
        'keywords_longtail': '',
        'angle': '',
        'structure': '',
        'external_sources': article.get('source_name', ''),
        'internal_links': '[]',
        'word_count_target': 1000,
        'tone_notes': 'Expert décontracté',
        'full_brief': brief_text
    }


def format_brief_for_wordpress(brief_data: dict, source_article: dict) -> str:
    """Format the brief as HTML for WordPress draft."""
    html = f"""
<div style="background: #f5f5f5; padding: 20px; border-left: 4px solid #0073aa; margin-bottom: 20px;">
<h2>📋 Brief de rédaction</h2>
<p><strong>Source:</strong> <a href="{source_article.get('url', '#')}" target="_blank">{source_article.get('source_name', 'Source')}</a></p>
<p><strong>Date source:</strong> {source_article.get('published_at', 'N/A')}</p>
<p><strong>Score pertinence:</strong> {source_article.get('relevance_score', 'N/A')}/10</p>
</div>

<pre style="background: #fff; padding: 20px; border: 1px solid #ddd; white-space: pre-wrap; font-family: monospace;">
{brief_data.get('full_brief', 'Brief non disponible')}
</pre>

<hr>
<p><em>Brief généré automatiquement par JurojinReposter - Module Veille RSS</em></p>
"""
    return html
