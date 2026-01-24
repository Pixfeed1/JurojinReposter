"""
Facebook content generator using Groq API.
Generates accroches adapted for Facebook with intelligent linking.
"""

import logging
from datetime import datetime
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY, GROQ_MODEL
from core.database import find_related_article, get_all_articles

logger = logging.getLogger(__name__)


FACEBOOK_PROMPT = """Tu es le community manager de Jurojin.net, blog francais sur la 3D, le cinema et la culture geek.
Date actuelle : {current_date}

Genere UNE accroche Facebook pour cet article.

REGLES :
- Ton : decontracte mais pro, passionne, expert accessible
- ZERO emoji
- ZERO putaclic, ZERO superlatifs vides
- Longueur : 150-300 caracteres max
- Langue : francais
- IMPORTANT : Si tu mentionnes une annee, nous sommes en {current_year}

STRUCTURE :
1. Hook (phrase d'accroche, max 150 chars) - question OU fait surprenant
2. Contexte court (1 phrase)
3. Question engageante pour inciter au clic

{linking_note}

ARTICLE :
Categorie : {category}
Titre : {title}
Extrait : {excerpt}

Posts deja utilises (ne pas repeter) : {previous_posts}

Reponds UNIQUEMENT avec l'accroche, sans guillemets ni explication."""


LINKING_PROMPT = """Tu es le curateur de jurojin.net.

Un article externe parle de : {external_title}

Voici un article potentiellement lie sur jurojin.net :
Titre : {related_title}
URL : {related_url}

La correspondance est-elle pertinente pour le lecteur ?
Reponds uniquement par OUI ou NON."""


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


def find_intelligent_link(title: str, category: str, client: Optional[Groq] = None) -> Optional[dict]:
    """
    Find a related jurojin.net article using keyword matching,
    then optionally validate with Groq for semantic relevance.

    Returns:
        dict with 'url' and 'title' if found, None otherwise
    """
    related = find_related_article(title, category)

    if not related:
        return None

    # If Groq available, validate the match semantically
    if client:
        try:
            prompt = LINKING_PROMPT.format(
                external_title=title,
                related_title=related['title'],
                related_url=related['url']
            )
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.1
            )
            answer = response.choices[0].message.content.strip().upper()
            if "NON" in answer:
                logger.info(f"Groq rejected link: '{related['title']}' not relevant to '{title}'")
                return None
        except Exception as e:
            logger.warning(f"Groq linking validation failed: {e}, using keyword match")

    return {"url": related['url'], "title": related['title']}


def generate_facebook_content(title: str, excerpt: str, category: str,
                               previous_posts: list = None) -> dict:
    """
    Generate Facebook content with intelligent linking.

    Args:
        title: Article title
        excerpt: Article excerpt
        category: Article category slug
        previous_posts: Previously used accroches

    Returns:
        {"accroche": str, "linked_url": str or None}
    """
    if previous_posts is None:
        previous_posts = []

    client = get_groq_client()
    if not client:
        fallback = f"{title[:200]}"
        return {"accroche": fallback, "linked_url": None}

    now = datetime.now()
    current_date = now.strftime("%d %B %Y")
    current_year = now.year

    # Find a related article for linking
    linked = find_intelligent_link(title, category, client)
    linking_note = ""
    if linked:
        linking_note = f'Integre naturellement cette reference a un article lie : "On en parlait ici : {linked["url"]}"'

    previous_str = ", ".join(previous_posts[:3]) if previous_posts else "Aucun"

    prompt = FACEBOOK_PROMPT.format(
        category=category,
        title=title,
        excerpt=excerpt[:400],
        previous_posts=previous_str,
        linking_note=linking_note,
        current_date=current_date,
        current_year=current_year
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.7
        )

        accroche = response.choices[0].message.content.strip()

        # Remove quotes if present
        if accroche.startswith('"') and accroche.endswith('"'):
            accroche = accroche[1:-1]
        if accroche.startswith("'") and accroche.endswith("'"):
            accroche = accroche[1:-1]

        # Truncate if too long
        if len(accroche) > 350:
            accroche = accroche[:347] + "..."

        logger.info(f"Generated Facebook accroche ({len(accroche)} chars)")
        return {
            "accroche": accroche,
            "linked_url": linked['url'] if linked else None
        }

    except Exception as e:
        logger.error(f"Error generating Facebook content: {e}")
        fallback = f"{title[:200]}"
        return {"accroche": fallback, "linked_url": None}
