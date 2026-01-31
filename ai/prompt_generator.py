"""
Prompt generator for TrendsToWordPress.
Generates structured article briefs/prompts with SEO backlinks.
"""

import logging
from datetime import datetime
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY, GROQ_MODEL, BASE_DIR
from core.article_matcher import generate_anchor_variations
from core.content_indexer import extract_article_keyword
import yaml

logger = logging.getLogger(__name__)


def load_trends_config() -> dict:
    """Load trends configuration."""
    config_path = BASE_DIR / "config" / "trends_config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_groq_client() -> Optional[Groq]:
    """Get a Groq client instance."""
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not configured")
        return None
    try:
        return Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        logger.error(f"Error creating Groq client: {e}")
        return None


ANALYSIS_PROMPT = """Tu es un expert SEO et stratege de contenu pour jurojin.net (blog arts numeriques, 3D, cinema, gaming).

TREND DETECTEE : {trend}
TYPE : {trend_type}
SCORE INTERET : {interest}/100

ARTICLES EXISTANTS LIES :
{existing_articles}

Analyse cette opportunite. REPONDS UNIQUEMENT avec un objet JSON valide, sans texte avant ni apres :
{{
    "titre_suggere": "Titre optimise SEO en francais (60 chars max)",
    "type_article": "actu|tuto|guide|comparatif|opinion",
    "urgence": "ephemere|durable",
    "pertinence_score": 7,
    "public_cible": "debutant|intermediaire|avance",
    "angle_unique": "Ce qui differencie cet article",
    "longtail_keywords": ["keyword longue traine 1", "keyword 2", "keyword 3"],
    "sections_suggeres": ["Section 1", "Section 2", "Section 3", "Section 4"]
}}"""


def analyze_opportunity(opportunity: dict) -> dict:
    """
    Use Groq to analyze an opportunity and suggest article structure.
    """
    client = get_groq_client()
    if not client:
        # Fallback to basic analysis
        return {
            'titre_suggere': f"{opportunity['trend']} : Guide complet",
            'type_article': 'guide',
            'urgence': 'durable',
            'pertinence_score': 7,
            'public_cible': 'intermediaire',
            'angle_unique': f"Article approfondi sur {opportunity['trend']}",
            'longtail_keywords': [opportunity['trend']],
            'sections_suggeres': ['Introduction', 'Fondamentaux', 'Techniques avancees', 'Conclusion']
        }

    # Format existing articles for context
    existing = ""
    for i, article in enumerate(opportunity.get('related_articles', [])[:5], 1):
        existing += f"{i}. {article['title']} ({article['url']})\n"
    if not existing:
        existing = "Aucun article existant directement lie"

    prompt = ANALYSIS_PROMPT.format(
        trend=opportunity['trend'],
        trend_type=opportunity.get('trend_type', 'general'),
        interest=opportunity.get('interest', opportunity.get('score', 50)),
        existing_articles=existing
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON response - handle various formats
        import json
        import re

        # Try to extract JSON from the response
        # Handle ```json ... ``` blocks
        if "```" in content:
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
            if json_match:
                content = json_match.group(1).strip()

        # Try to find JSON object in response
        if not content.startswith('{'):
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                content = json_match.group(0)

        result = json.loads(content)
        logger.info(f"AI analysis successful for: {opportunity['trend']}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for {opportunity['trend']}: {e}")
        logger.debug(f"Raw content: {content[:500] if 'content' in dir() else 'N/A'}")
        return {
            'titre_suggere': f"{opportunity['trend']} : Guide complet {datetime.now().year}",
            'type_article': 'guide',
            'urgence': 'durable',
            'pertinence_score': 7,
            'public_cible': 'intermediaire',
            'angle_unique': f"Tout savoir sur {opportunity['trend']} en {datetime.now().year}",
            'longtail_keywords': [opportunity['trend'], f"tutoriel {opportunity['trend']}", f"{opportunity['trend']} debutant"],
            'sections_suggeres': ['Introduction', 'Fondamentaux', 'Techniques', 'Cas pratiques', 'Conclusion']
        }
    except Exception as e:
        logger.error(f"Error analyzing opportunity: {e}")
        return {
            'titre_suggere': f"{opportunity['trend']} : Ce qu'il faut savoir",
            'type_article': 'actu',
            'urgence': 'ephemere',
            'pertinence_score': 6,
            'public_cible': 'intermediaire',
            'angle_unique': f"Actualite sur {opportunity['trend']}",
            'longtail_keywords': [opportunity['trend']],
            'sections_suggeres': ['Contexte', 'Les faits', 'Analyse', 'Conclusion']
        }


def generate_backlinks_section(related_articles: list, trend_keyword: str) -> str:
    """
    Generate the backlinks section with SEO-optimized anchors.
    """
    if not related_articles:
        return "Aucun article existant a linker (opportunite de nouveau contenu)"

    lines = []
    for i, article in enumerate(related_articles[:5], 1):
        primary_keyword = article.get('primary_keyword', '')
        if not primary_keyword:
            primary_keyword = extract_article_keyword(article)

        anchors = generate_anchor_variations(primary_keyword, article['title'])
        anchor = anchors[0] if anchors else primary_keyword

        lines.append(f"""{i}. Article cible : "{article['title']}"
   URL : {article['url']}
   Mot-cle de cet article : "{primary_keyword}"
   Ancre a utiliser : "{anchor}"
""")

    return "\n".join(lines)


def get_word_count(article_type: str) -> tuple:
    """Get min/max word count for article type."""
    config = load_trends_config()
    types = config.get('article_types', {})
    type_config = types.get(article_type, types.get('actu', {}))
    return type_config.get('min_words', 800), type_config.get('max_words', 1200)


def generate_structured_prompt(opportunity: dict, analysis: dict) -> str:
    """
    Generate the complete structured prompt for WordPress draft.
    """
    trend = opportunity['trend']
    related_articles = opportunity.get('related_articles', [])

    # Get word count range
    article_type = analysis.get('type_article', 'actu')
    min_words, max_words = get_word_count(article_type)

    # Generate backlinks section
    backlinks_section = generate_backlinks_section(related_articles, trend)

    # Format long-tail keywords
    longtail = analysis.get('longtail_keywords', [trend])
    longtail_formatted = "\n".join([f"- {kw}" for kw in longtail])

    # Format sections
    sections = analysis.get('sections_suggeres', ['Introduction', 'Contenu principal', 'Conclusion'])
    sections_formatted = "\n".join([f"- [Section {i}] : {s}" for i, s in enumerate(sections, 1)])

    # Build backlinks instruction for prompt
    backlinks_instruction = ""
    if related_articles:
        backlinks_instruction = "BACKLINKS INTERNES OBLIGATOIRES :\nIntegrer ces liens avec les ancres SEO indiquees :\n"
        for i, article in enumerate(related_articles[:3], 1):
            primary_keyword = article.get('primary_keyword', extract_article_keyword(article))
            anchors = generate_anchor_variations(primary_keyword, article['title'])
            anchor = anchors[0] if anchors else primary_keyword
            backlinks_instruction += f'- Ancre "{anchor}" → lien vers {article["url"]}\n'

    prompt = f"""=== BRIEF ARTICLE ===

📌 SUJET : {analysis.get('titre_suggere', trend)}
📊 SCORE PERTINENCE : {analysis.get('pertinence_score', 7)}/10
⏰ URGENCE : {analysis.get('urgence', 'durable').capitalize()}
📝 TYPE : {article_type.capitalize()}
📏 LONGUEUR CIBLE : {min_words}-{max_words} mots

🎯 MOT-CLE PRINCIPAL : {trend}
🔑 LONGUE TRAINE A INTEGRER :
{longtail_formatted}

🔗 MAILLAGE INTERNE - BACKLINKS AVEC ANCRES SEO :

{backlinks_section}

🌐 SOURCES EXTERNES A CITER :
1. Documentation officielle du sujet
2. Sites reference du domaine (80.lv, 3DVF, etc.)

=== PROMPT POUR GENERATION DE L'ARTICLE ===

Ecris un article de type {article_type.upper()} de {min_words}-{max_words} mots pour jurojin.net, blog expert en arts numeriques.

SUJET : {analysis.get('titre_suggere', trend)}
ANGLE UNIQUE : {analysis.get('angle_unique', 'Article approfondi et expert')}

TON ET STYLE :
- Expert mais accessible
- Pro mais decontracte
- Vouvoiement
- Pincee cool sans etre familier
- Opinions personnelles assumees
- Utiliser "je", "dans mon experience", "j'ai teste"

STRUCTURE :
- Accroche percutante (PAS de phrase generique)
{sections_formatted}
- Conclusion avec avis personnel + call-to-action

EEAT OBLIGATOIRE :
- Mentionner une experience concrete avec le sujet
- 1-2 anecdotes personnelles
- Avis tranche, pas neutre

MOTS-CLES - INTEGRATION NATURELLE :
- Principal : "{trend}" → 3-5 fois (1x intro, 1x H2 minimum)
- Longue traine : {', '.join([f'"{kw}"' for kw in longtail[:3]])} → 1-2 fois chacune

{backlinks_instruction}

INTERDICTIONS ABSOLUES :
- Phrases IA generiques : "joue un role crucial", "dans le paysage actuel", "il est important de noter", "force est de constater"
- Listes a puces > 5 items
- Intro qui resume tout
- Ton neutre sans position
- Paragraphes de meme longueur
- Mots : "indeniablement", "assurement", "il convient de"

PUBLIC CIBLE : {analysis.get('public_cible', 'intermediaire').capitalize()}
"""

    return prompt


def generate_article_brief(opportunity: dict) -> dict:
    """
    Generate a complete article brief for an opportunity.

    Returns:
        {
            'title': str,           # Draft title for WordPress
            'content': str,         # The structured prompt
            'category': str,        # Suggested category
            'tags': list,           # Suggested tags
            'opportunity': dict     # Original opportunity data
        }
    """
    logger.info(f"Generating brief for trend: {opportunity['trend']}")

    # Analyze with AI
    analysis = analyze_opportunity(opportunity)

    # Generate the prompt
    prompt_content = generate_structured_prompt(opportunity, analysis)

    # Determine WordPress category
    category = opportunity.get('category', '')
    if not category:
        # Map article type to category
        type_to_category = {
            'tuto': 'Tutoriels',
            'guide': 'Guides',
            'actu': 'Actualites',
            'comparatif': 'Tests',
            'opinion': 'Actualites'
        }
        category = type_to_category.get(analysis.get('type_article', 'actu'), 'Actualites')

    # Generate tags from keywords
    tags = [opportunity['trend']]
    tags.extend(analysis.get('longtail_keywords', [])[:3])

    config = load_trends_config()
    prefix = config.get('wordpress', {}).get('draft_title_prefix', '[BRIEF]')

    return {
        'title': f"{prefix} {analysis.get('titre_suggere', opportunity['trend'])}",
        'content': prompt_content,
        'category': category,
        'tags': tags,
        'article_type': analysis.get('type_article', 'actu'),
        'urgency': analysis.get('urgence', 'durable'),
        'score': analysis.get('pertinence_score', 7),
        'opportunity': opportunity
    }
