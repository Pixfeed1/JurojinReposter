"""
Fallback templates for generating accroches when Groq is unavailable.
"""

import random
from typing import Optional


TEMPLATES = [
    "{title} - a relire sur Jurojin",
    "Un article qui merite qu'on y revienne : {title}",
    "{title} - toujours d'actualite",
    "Si tu l'avais loupe : {title}",
    "Retour sur {title}",
    "{title} - le guide complet",
    "A (re)decouvrir : {title}",
    "{title} - un classique Jurojin",
    "On revient sur : {title}",
    "Focus : {title}",
]


def get_fallback_accroche(title: str, max_chars: int = 280,
                          exclude_accroches: Optional[list] = None) -> str:
    """
    Generate a fallback accroche using templates.

    Args:
        title: The article title
        max_chars: Maximum character limit
        exclude_accroches: List of accroches to avoid

    Returns:
        A generated accroche string
    """
    if exclude_accroches is None:
        exclude_accroches = []

    # Shuffle templates to get variety
    templates = TEMPLATES.copy()
    random.shuffle(templates)

    for template in templates:
        accroche = template.format(title=title)

        # Check length
        if len(accroche) > max_chars:
            # Try to truncate title
            available = max_chars - len(template.format(title='')) - 3  # -3 for "..."
            if available > 20:  # Minimum title length
                truncated_title = title[:available] + "..."
                accroche = template.format(title=truncated_title)
            else:
                continue

        # Check if not in exclusion list
        if accroche not in exclude_accroches:
            return accroche

    # If all templates are excluded, just use the first one with title
    return f"{title[:max_chars - 20]} - sur Jurojin"


def truncate_accroche(accroche: str, max_chars: int) -> str:
    """
    Truncate an accroche to fit within character limit.

    Args:
        accroche: The accroche to truncate
        max_chars: Maximum character limit

    Returns:
        Truncated accroche
    """
    if len(accroche) <= max_chars:
        return accroche

    # Truncate and add ellipsis
    return accroche[:max_chars - 3].rstrip() + "..."
