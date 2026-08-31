"""
Category-aware hashtag selection, shared by all content generators.

Fixes the bug where fallback posts always carried #3D #CGI #Blender3D,
even on articles that had nothing to do with 3D (e.g. network tutorials).

Rule of thumb: a wrong hashtag is worse than no hashtag. If the category
is unknown, fallbacks emit NO hashtags at all.
"""

import logging

logger = logging.getLogger(__name__)

# Hashtags par categorie WordPress (slug -> hashtags)
# Utilise pour les fallbacks ET pour orienter les exemples donnes a l'IA.
CATEGORY_HASHTAGS = {
    # 3D / creation
    "blender": "#Blender3D #b3d #3DArt",
    "logiciels-3d": "#3D #CGI #Software",
    "cinema-4d": "#Cinema4D #C4D #3D",
    "after-effects": "#AfterEffects #MotionDesign",
    "vfx": "#VFX #VisualEffects",
    "animation": "#Animation #MotionDesign",
    # Culture / media
    "cinema": "#Cinema #Film",
    "anime": "#Anime #Manga",
    "musique": "#Musique #Music",
    "jeux-video": "#JeuxVideo #Gaming",
    # Generaliste — PAS de hashtags 3D ici : ces categories couvrent
    # tous les sujets (reseau, web, materiel, astuces...)
    "guide-dachat": "#GuideAchat #Hardware #Tech",
    "tutoriels": "#Tuto #Astuce",
    "actualites": "#Tech #Actu",
    "internet": "#Internet #Web",
    "informatique": "#Informatique #Tech",
}

# Hashtags par post type (utilises si la categorie ne donne rien)
POST_TYPE_HASHTAGS = {
    "guide": "#GuideAchat #Tech",
    "jeu": "#JeuxVideo #Gaming",
    "glossaire": "#3D #Glossaire",
    "anime": "#Anime #Manga",
}


def get_fallback_hashtags(category: str = "", post_type: str = "") -> str:
    """
    Return hashtags matching the article's category for fallback posts.

    Returns an empty string when nothing matches: a post without hashtags
    is always better than a post with off-topic hashtags.
    """
    category = (category or "").strip().lower()
    post_type = (post_type or "").strip().lower()

    if category in CATEGORY_HASHTAGS:
        return CATEGORY_HASHTAGS[category]

    if post_type in POST_TYPE_HASHTAGS:
        return POST_TYPE_HASHTAGS[post_type]

    logger.warning(
        f"No hashtags mapped for category='{category}' post_type='{post_type}', "
        "fallback post will carry no hashtags"
    )
    return ""


def get_hashtag_examples(category: str = "", post_type: str = "") -> str:
    """
    Return example hashtags to inject into AI prompts, adapted to the
    article's category so the model is not biased toward 3D on every topic.
    """
    mapped = get_fallback_hashtags(category, post_type)
    if mapped:
        return mapped
    # No mapping: give the model diverse examples covering the whole site,
    # not just 3D, and let it pick from the article's actual subject.
    return "#Tech #Tuto #Cinema #Gaming #Blender3D #Web"


# Consigne commune aux prompts : choisir selon le SUJET, pas selon le blog.
HASHTAG_RULE = (
    "Choisis 2-3 hashtags qui correspondent au SUJET REEL de l'article "
    "(exemples adaptes a sa categorie : {examples}). "
    "N'utilise JAMAIS de hashtags 3D (#3D #CGI #Blender3D...) si l'article "
    "ne parle pas de 3D : un tuto reseau, un film ou une astuce web n'ont "
    "pas de hashtags 3D."
)


def get_hashtag_rule(category: str = "", post_type: str = "") -> str:
    """Full prompt instruction with category-adapted examples."""
    return HASHTAG_RULE.format(examples=get_hashtag_examples(category, post_type))
