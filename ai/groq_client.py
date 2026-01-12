"""
Groq API client for generating accroches using AI.
"""

import logging
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY, GROQ_MODEL, CHAR_LIMITS
from core.database import cache_accroche, get_cached_accroche, get_previous_accroches
from ai.templates import get_fallback_accroche, truncate_accroche

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es un community manager pour Jurojin.net, blog tech/3D/CGI.
Tu generes des accroches pour republier d'anciens articles.
Ton : expert decontracte, jamais putaclic, jamais d'emojis.
Pas de hashtags.
Reponds uniquement avec l'accroche, sans guillemets ni explication."""


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


def generate_accroche(article_id: int, title: str, excerpt: str,
                      platform: str, use_cache: bool = True) -> str:
    """
    Generate an accroche for an article.

    First checks the cache, then tries Groq API, falls back to templates.

    Args:
        article_id: The database article ID
        title: Article title
        excerpt: Article excerpt
        platform: Target platform ('twitter' or 'facebook')
        use_cache: Whether to check cache first

    Returns:
        Generated accroche string
    """
    max_chars = CHAR_LIMITS.get(platform, 280)

    # Check cache first
    if use_cache:
        cached = get_cached_accroche(article_id, platform)
        if cached:
            logger.info(f"Using cached accroche for article {article_id}")
            return truncate_accroche(cached, max_chars)

    # Get previous accroches to avoid repetition
    previous_accroches = get_previous_accroches(article_id, platform)

    # Try Groq API
    client = get_groq_client()
    if client:
        try:
            accroche = _generate_with_groq(
                client, title, excerpt, platform, max_chars, previous_accroches
            )
            if accroche:
                # Cache the generated accroche
                cache_accroche(article_id, platform, accroche)
                return accroche
        except Exception as e:
            logger.error(f"Groq API error: {e}")

    # Fallback to templates
    logger.info(f"Using fallback template for article {article_id}")
    return get_fallback_accroche(title, max_chars, previous_accroches)


def _generate_with_groq(client: Groq, title: str, excerpt: str,
                        platform: str, max_chars: int,
                        previous_accroches: list) -> Optional[str]:
    """
    Generate accroche using Groq API.

    Args:
        client: Groq client instance
        title: Article title
        excerpt: Article excerpt
        platform: Target platform
        max_chars: Maximum character limit
        previous_accroches: List of previous accroches to avoid

    Returns:
        Generated accroche or None if failed
    """
    # Build user prompt
    previous_str = ""
    if previous_accroches:
        previous_str = f"\nAccroches deja utilisees (a ne pas repeter) : {', '.join(previous_accroches[:5])}"

    user_prompt = f"""Genere une accroche {platform} pour cet article.
Titre : {title}
Extrait : {excerpt[:300]}
Max {max_chars} caracteres.{previous_str}"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )

        accroche = response.choices[0].message.content.strip()

        # Remove quotes if present
        if accroche.startswith('"') and accroche.endswith('"'):
            accroche = accroche[1:-1]
        if accroche.startswith("'") and accroche.endswith("'"):
            accroche = accroche[1:-1]

        # Ensure it fits within limits
        accroche = truncate_accroche(accroche, max_chars)

        logger.info(f"Generated accroche via Groq: {accroche[:50]}...")
        return accroche

    except Exception as e:
        logger.error(f"Error generating with Groq: {e}")
        return None


def generate_multiple_accroches(article_id: int, title: str, excerpt: str,
                                platform: str, count: int = 3) -> list:
    """
    Generate multiple accroches for an article (for caching).

    Args:
        article_id: The database article ID
        title: Article title
        excerpt: Article excerpt
        platform: Target platform
        count: Number of accroches to generate

    Returns:
        List of generated accroches
    """
    max_chars = CHAR_LIMITS.get(platform, 280)
    accroches = []

    client = get_groq_client()
    if not client:
        # Use templates only
        for _ in range(count):
            accroche = get_fallback_accroche(title, max_chars, accroches)
            accroches.append(accroche)
            cache_accroche(article_id, platform, accroche)
        return accroches

    # Generate with Groq
    for i in range(count):
        try:
            accroche = _generate_with_groq(
                client, title, excerpt, platform, max_chars, accroches
            )
            if accroche and accroche not in accroches:
                accroches.append(accroche)
                cache_accroche(article_id, platform, accroche)
            else:
                # Fallback for this iteration
                fallback = get_fallback_accroche(title, max_chars, accroches)
                accroches.append(fallback)
                cache_accroche(article_id, platform, fallback)
        except Exception as e:
            logger.error(f"Error generating accroche {i+1}: {e}")
            fallback = get_fallback_accroche(title, max_chars, accroches)
            accroches.append(fallback)
            cache_accroche(article_id, platform, fallback)

    return accroches
