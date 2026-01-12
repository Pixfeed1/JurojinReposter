"""
Thread generator for Twitter content.
Generates either a simple tweet or a thread based on article score and word count.
"""

import logging
from typing import Optional

from groq import Groq

from config.settings import GROQ_API_KEY, GROQ_MODEL, BASE_DIR
import yaml

logger = logging.getLogger(__name__)


def load_twitter_format_config() -> dict:
    """Load Twitter format thresholds from scoring config."""
    config_path = BASE_DIR / "config" / "scoring.yaml"
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config.get('twitter_format', {
        'thread_4': {'min_score': 120, 'min_word_count': 1500},
        'thread_3': {'min_score': 100, 'min_word_count': 1000}
    })


def determine_format(score: int, word_count: int, force_format: Optional[str] = None) -> str:
    """
    Determine the Twitter format based on score and word count.

    Args:
        score: Article score
        word_count: Article word count
        force_format: Force a specific format ('simple', 'thread', or None for auto)

    Returns:
        'thread_4', 'thread_3', or 'simple'
    """
    if force_format == 'simple':
        return 'simple'

    config = load_twitter_format_config()

    thread_4 = config.get('thread_4', {})
    thread_3 = config.get('thread_3', {})

    if force_format == 'thread':
        # Force thread, pick the best one based on content
        if word_count >= thread_4.get('min_word_count', 1500):
            return 'thread_4'
        else:
            return 'thread_3'

    # Auto mode
    if (score >= thread_4.get('min_score', 120) and
        word_count >= thread_4.get('min_word_count', 1500)):
        return 'thread_4'

    if (score >= thread_3.get('min_score', 100) and
        word_count >= thread_3.get('min_word_count', 1000)):
        return 'thread_3'

    return 'simple'


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


SYSTEM_PROMPT = """Tu es un community manager pour Jurojin.net, blog tech/3D/CGI.
Ton : expert decontracte, jamais putaclic.
Pas d'emojis excessifs (1-2 max par tweet).
Ne numerote PAS les tweets (pas de 1/4, 2/4, etc.)"""


THREAD_4_PROMPT = """Genere un thread Twitter de 4 tweets pour cet article.
Titre : {title}
Extrait : {excerpt}

Format :
Tweet 1 (max 240 chars) : Hook accrocheur, donne envie de lire la suite. Termine par →
Tweet 2 (max 240 chars) : Premier point cle ou fait interessant
Tweet 3 (max 240 chars) : Deuxieme point cle ou insight
Tweet 4 (max 200 chars) : Conclusion + "[LIEN]" + 2-3 hashtags pertinents (3D, Blender, VFX, cinema, etc.)

IMPORTANT : Les hashtags vont UNIQUEMENT sur le dernier tweet. Pas plus de 3 hashtags.

Reponds UNIQUEMENT avec les 4 tweets separes par ---"""


THREAD_3_PROMPT = """Genere un thread Twitter de 3 tweets pour cet article.
Titre : {title}
Extrait : {excerpt}

Format :
Tweet 1 (max 240 chars) : Hook accrocheur
Tweet 2 (max 240 chars) : Point cle principal
Tweet 3 (max 200 chars) : Conclusion + "[LIEN]" + 2-3 hashtags pertinents (3D, Blender, VFX, cinema, etc.)

IMPORTANT : Les hashtags vont UNIQUEMENT sur le dernier tweet. Pas plus de 3 hashtags.

Reponds UNIQUEMENT avec les 3 tweets separes par ---"""


SIMPLE_PROMPT = """Genere une accroche Twitter pour cet article.
Titre : {title}
Extrait : {excerpt}
Max 240 caracteres (hashtags inclus).
{previous_note}

Termine avec 2-3 hashtags pertinents (en rapport avec le sujet : 3D, Blender, cinema, VFX, etc.). Pas plus de 3 hashtags.

Reponds uniquement avec l'accroche, sans guillemets ni explication."""


def parse_thread_response(response: str, expected_count: int) -> list:
    """Parse the thread response from Groq."""
    tweets = [t.strip() for t in response.split('---') if t.strip()]

    # Validate and truncate if needed
    result = []
    for i, tweet in enumerate(tweets[:expected_count]):
        max_len = 200 if i == expected_count - 1 else 240
        if len(tweet) > max_len:
            tweet = tweet[:max_len - 3] + "..."
        result.append(tweet)

    return result


def generate_thread_groq(client: Groq, title: str, excerpt: str,
                         thread_type: str) -> Optional[list]:
    """Generate a thread using Groq API."""
    if thread_type == 'thread_4':
        prompt = THREAD_4_PROMPT.format(title=title, excerpt=excerpt[:400])
        expected_count = 4
    else:
        prompt = THREAD_3_PROMPT.format(title=title, excerpt=excerpt[:400])
        expected_count = 3

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )

        content = response.choices[0].message.content.strip()
        tweets = parse_thread_response(content, expected_count)

        if len(tweets) >= expected_count - 1:  # Allow one less tweet as fallback
            logger.info(f"Generated {len(tweets)} tweets via Groq")
            return tweets
        else:
            logger.warning(f"Got only {len(tweets)} tweets, expected {expected_count}")
            return None

    except Exception as e:
        logger.error(f"Error generating thread with Groq: {e}")
        return None


def generate_simple_groq(client: Groq, title: str, excerpt: str,
                         previous_hooks: list) -> Optional[str]:
    """Generate a simple tweet using Groq API."""
    previous_note = ""
    if previous_hooks:
        previous_note = f"Accroches deja utilisees (a ne pas repeter) : {', '.join(previous_hooks[:3])}"

    prompt = SIMPLE_PROMPT.format(
        title=title,
        excerpt=excerpt[:300],
        previous_note=previous_note
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )

        tweet = response.choices[0].message.content.strip()

        # Remove quotes if present
        if tweet.startswith('"') and tweet.endswith('"'):
            tweet = tweet[1:-1]
        if tweet.startswith("'") and tweet.endswith("'"):
            tweet = tweet[1:-1]

        # Truncate if needed
        if len(tweet) > 240:
            tweet = tweet[:237] + "..."

        logger.info(f"Generated simple tweet via Groq")
        return tweet

    except Exception as e:
        logger.error(f"Error generating simple tweet with Groq: {e}")
        return None


# Fallback templates (avec hashtags sur le dernier tweet/tweet simple)
THREAD_FALLBACK_3 = [
    "{title} - un article qui merite votre attention →",
    "Voici ce qu'il faut retenir de cet article complet sur le sujet.",
    "L'article complet a decouvrir ici [LIEN] #3D #CGI"
]

THREAD_FALLBACK_4 = [
    "{title} - un sujet passionnant a decouvrir →",
    "Premier point cle : cet article couvre en profondeur tous les aspects du sujet.",
    "Deuxieme point : des conseils pratiques et des exemples concrets.",
    "Pour aller plus loin, l'article complet est ici [LIEN] #3D #VFX"
]

SIMPLE_FALLBACK = [
    "{title} - a relire sur Jurojin #3D #CGI",
    "Un article qui merite qu'on y revienne : {title} #Blender #3D",
    "{title} - toujours d'actualite #VFX #3D",
]


def generate_fallback_thread(title: str, thread_type: str) -> list:
    """Generate a fallback thread when Groq is unavailable."""
    import random

    if thread_type == 'thread_4':
        templates = THREAD_FALLBACK_4.copy()
    else:
        templates = THREAD_FALLBACK_3.copy()

    return [t.format(title=title[:100]) for t in templates]


def generate_fallback_simple(title: str) -> str:
    """Generate a fallback simple tweet when Groq is unavailable."""
    import random
    template = random.choice(SIMPLE_FALLBACK)
    return template.format(title=title[:150])


def generate_twitter_content(title: str, excerpt: str, word_count: int,
                             score: int, previous_hooks: list = None,
                             force_format: Optional[str] = None) -> dict:
    """
    Generate Twitter content - either a simple tweet or a thread.

    Args:
        title: Article title
        excerpt: Article excerpt
        word_count: Article word count
        score: Article score
        previous_hooks: List of previously used hooks to avoid
        force_format: Force format ('simple', 'thread', or None for auto)

    Returns:
        {
            "type": "simple" | "thread",
            "format": "simple" | "thread_3" | "thread_4",
            "tweets": ["tweet1", "tweet2", ...],
            "posts_count": 1 | 3 | 4
        }
    """
    if previous_hooks is None:
        previous_hooks = []

    # Determine format
    format_type = determine_format(score, word_count, force_format)

    logger.info(f"Determined format: {format_type} (score={score}, words={word_count})")

    client = get_groq_client()

    if format_type in ('thread_3', 'thread_4'):
        # Generate thread
        tweets = None
        if client:
            tweets = generate_thread_groq(client, title, excerpt, format_type)

        if not tweets:
            logger.info("Using fallback thread templates")
            tweets = generate_fallback_thread(title, format_type)

        return {
            "type": "thread",
            "format": format_type,
            "tweets": tweets,
            "posts_count": len(tweets)
        }

    else:
        # Generate simple tweet
        tweet = None
        if client:
            tweet = generate_simple_groq(client, title, excerpt, previous_hooks)

        if not tweet:
            logger.info("Using fallback simple template")
            tweet = generate_fallback_simple(title)

        return {
            "type": "simple",
            "format": "simple",
            "tweets": [tweet],
            "posts_count": 1
        }
