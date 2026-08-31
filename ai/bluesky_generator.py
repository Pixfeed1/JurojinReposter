"""
Bluesky content generator using Groq API.
Generates posts adapted to article category with appropriate hashtags.
"""

import logging
from datetime import datetime
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY, GROQ_MODEL
from ai.hashtags import get_fallback_hashtags, get_hashtag_rule

logger = logging.getLogger(__name__)

THREAD_PROMPT = """Tu es le community manager de Jurojin.net, blog francais sur la 3D, le cinema et la culture geek.
Date actuelle : {current_date}

Genere un thread Bluesky de 4 posts pour cet article.

REGLES :
- Ton : passionne, expert mais accessible, JAMAIS commercial ou putaclic
- Max 280 caracteres par post (pour laisser place au lien sur le dernier)
- 1-2 emojis max par post, pas plus
- Le dernier post contient [LIEN] qui sera remplace par l'URL
- Hashtags UNIQUEMENT sur le dernier post. {hashtag_rule}
- IMPORTANT : Si tu mentionnes une annee, nous sommes en {current_year}

STRUCTURE DU THREAD :
Post 1 : Hook accrocheur (question OU fait surprenant)
Post 2 : Point cle #1 de l'article
Post 3 : Point cle #2 ou insight
Post 4 : Conclusion + [LIEN] + hashtags

ARTICLE :
Categorie : {category}
Titre : {title}
Extrait : {excerpt}

Reponds UNIQUEMENT avec les 4 posts separes par ---"""

SIMPLE_PROMPT = """Tu es le community manager de Jurojin.net, blog francais sur la 3D, le cinema et la culture geek.
Date actuelle : {current_date}

Genere UN post Bluesky pour cet article.

REGLES ABSOLUES :
- Ton : passionne, expert mais accessible, JAMAIS commercial
- INTERDITS : "vous ne devinerez jamais", superlatifs vides, ton putaclic, emojis excessifs
- Max 250 caracteres (laisser place au lien)
- 1-2 emojis max, ou zero
- Terminer par [LIEN]
- Hashtags a la fin. {hashtag_rule}
- IMPORTANT : Si tu mentionnes une annee, nous sommes en {current_year}

FORMATS (choisis le plus adapte) :

1. QUESTION ENGAGEANTE
"[Question qui interpelle] [Contexte court] [LIEN] [Hashtags]"

2. FAIT INTERESSANT
"[Fait surprenant ou insight] [LIEN] [Hashtags]"

3. AVIS TRANCHE
"[Opinion assumee + argument court] [LIEN] [Hashtags]"

ARTICLE :
Categorie : {category}
Type : {post_type}
Titre : {title}
Extrait : {excerpt}

Posts deja utilises (ne pas repeter le meme angle) : {previous_posts}

Reponds UNIQUEMENT avec le post, sans explication."""


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


def generate_bluesky_content(title: str, excerpt: str, category: str,
                              post_type: str, word_count: int,
                              previous_posts: list = None) -> dict:
    """
    Generate Bluesky content adapted to the category.

    Args:
        title: Article title
        excerpt: Article excerpt
        category: Article category slug
        post_type: Article post type
        word_count: Article word count
        previous_posts: Previously used posts to avoid repetition

    Returns:
        {
            "type": "simple" | "thread",
            "posts": ["post1", "post2", ...]
        }
    """
    if previous_posts is None:
        previous_posts = []

    # Determine if thread or simple based on word count
    is_thread = word_count >= 1500

    client = get_groq_client()
    if not client:
        logger.warning("FALLBACK: Groq client unavailable, using template post")
        return {"type": "simple", "posts": [_build_fallback(title, category, post_type)]}

    try:
        now = datetime.now()
        current_date = now.strftime("%d %B %Y")
        current_year = now.year
        hashtag_rule = get_hashtag_rule(category, post_type)

        if is_thread:
            prompt = THREAD_PROMPT.format(
                category=category,
                title=title,
                excerpt=excerpt[:400],
                current_date=current_date,
                current_year=current_year,
                hashtag_rule=hashtag_rule
            )
        else:
            previous_str = ", ".join(previous_posts[:3]) if previous_posts else "Aucun"
            prompt = SIMPLE_PROMPT.format(
                category=category,
                post_type=post_type,
                title=title,
                excerpt=excerpt[:300],
                previous_posts=previous_str,
                current_date=current_date,
                current_year=current_year,
                hashtag_rule=hashtag_rule
            )

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.8
        )

        content = response.choices[0].message.content.strip()

        if is_thread:
            posts = [p.strip() for p in content.split("---") if p.strip()]
            logger.info(f"Generated Bluesky thread with {len(posts)} posts")
            return {"type": "thread", "posts": posts}
        else:
            # Remove quotes if present
            if content.startswith('"') and content.endswith('"'):
                content = content[1:-1]
            if content.startswith("'") and content.endswith("'"):
                content = content[1:-1]
            logger.info("Generated Bluesky simple post")
            return {"type": "simple", "posts": [content]}

    except Exception as e:
        logger.error(f"Error generating Bluesky content: {e}")
        logger.warning("FALLBACK: Groq call failed, using template post")
        return {"type": "simple", "posts": [_build_fallback(title, category, post_type)]}


def _build_fallback(title: str, category: str, post_type: str) -> str:
    """Template post used when Groq is unavailable, with category-aware hashtags."""
    hashtags = get_fallback_hashtags(category, post_type)
    suffix = f" {hashtags}" if hashtags else ""
    return f"{title[:200]} [LIEN]{suffix}"
