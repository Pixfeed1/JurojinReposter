"""
Curation filter using Groq API.
Decides whether to share external articles and generates accroches.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY, GROQ_MODEL
from core.database import find_related_article

logger = logging.getLogger(__name__)


FILTER_PROMPT = """Tu es le curateur du blog jurojin.net (arts numeriques, cinema, gaming, tech).
Date actuelle : {current_date}

SOURCE : {source_name}
CATEGORIE : {category}
TITRE : {title}
RESUME : {description}

REGLES :
- Si source 3D/Gaming/Cinema : accepter UNIQUEMENT interviews, exclusivites, making-of, behind the scenes
- Si source Tech US : accepter news importantes, annonces majeures
- Refuser : tutos, guides, listes, clickbait, rumeurs, contenus sponsorises

Reponds en JSON : {{"share": true/false, "reason": "..."}}"""


ACCROCHE_PROMPT = """Tu es le community manager de jurojin.net (arts numeriques, cinema, gaming, tech).
Date actuelle : {current_date}

Genere une accroche Facebook pour partager cet article EXTERNE.

SOURCE : {source_name}
TITRE : {title}
RESUME : {description}

REGLES :
- Ton : decontracte mais pro, passionne
- ZERO emoji, ZERO putaclic
- Si l'article est en anglais, traduis/adapte en francais
- Longueur : 150-300 caracteres
- Structure : hook + contexte + question engageante
- IMPORTANT : Si tu mentionnes une annee, nous sommes en {current_year}
{linking_note}

Reponds UNIQUEMENT avec l'accroche, sans guillemets."""


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


def should_share(item: dict, client: Groq) -> dict:
    """
    Ask Groq whether an external article should be shared.

    Args:
        item: dict with title, description, source_name, category
        client: Groq client

    Returns:
        {"share": bool, "reason": str}
    """
    now = datetime.now()

    prompt = FILTER_PROMPT.format(
        source_name=item['source_name'],
        category=item['category'],
        title=item['title'],
        description=item.get('description', '')[:400],
        current_date=now.strftime("%d %B %Y")
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.1
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON response
        # Handle case where model wraps in markdown code block
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(content)
        share = result.get('share', False)
        reason = result.get('reason', '')

        logger.info(f"Curation filter: {'ACCEPT' if share else 'REJECT'} - {item['title'][:50]}... ({reason})")
        return {"share": share, "reason": reason}

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Curation filter error for '{item['title'][:40]}': {e}")
        return {"share": False, "reason": f"Error: {str(e)}"}


def generate_external_accroche(item: dict, client: Groq) -> dict:
    """
    Generate a Facebook accroche for an external article.
    Includes intelligent linking to jurojin.net articles.

    Args:
        item: dict with title, description, source_name, category, url

    Returns:
        {"accroche": str, "linked_url": str or None}
    """
    now = datetime.now()

    # Find related jurojin article
    related = find_related_article(item['title'], item.get('category'))
    linking_note = ""
    linked_url = None

    if related:
        linking_note = f'Integre naturellement cette reference : "On en parlait ici : {related["url"]}"'
        linked_url = related['url']
        logger.info(f"Found related article: {related['title'][:40]}...")

    prompt = ACCROCHE_PROMPT.format(
        source_name=item['source_name'],
        title=item['title'],
        description=item.get('description', '')[:400],
        linking_note=linking_note,
        current_date=now.strftime("%d %B %Y"),
        current_year=now.year
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.7
        )

        accroche = response.choices[0].message.content.strip()

        # Remove quotes
        if accroche.startswith('"') and accroche.endswith('"'):
            accroche = accroche[1:-1]
        if accroche.startswith("'") and accroche.endswith("'"):
            accroche = accroche[1:-1]

        if len(accroche) > 350:
            accroche = accroche[:347] + "..."

        logger.info(f"Generated external accroche ({len(accroche)} chars)")
        return {"accroche": accroche, "linked_url": linked_url}

    except Exception as e:
        logger.error(f"Error generating external accroche: {e}")
        # Simple fallback
        accroche = f"{item['title'][:200]} (via {item['source_name']})"
        return {"accroche": accroche, "linked_url": linked_url}


def process_external_items(items: list) -> list:
    """
    Process a list of external items: filter with AI then generate accroches.

    Args:
        items: List of dicts from external_feeds.fetch_new_items()

    Returns:
        List of items that passed the filter, with accroche added
    """
    client = get_groq_client()
    if not client:
        logger.warning("Groq client not available, skipping curation")
        return []

    accepted = []

    for item in items:
        # Step 1: AI filter
        result = should_share(item, client)
        if not result['share']:
            continue

        # Step 2: Generate accroche
        content = generate_external_accroche(item, client)
        item['accroche'] = content['accroche']
        item['linked_url'] = content['linked_url']
        accepted.append(item)

    logger.info(f"Curation: {len(accepted)}/{len(items)} items accepted")
    return accepted
