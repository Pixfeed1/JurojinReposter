"""
Configuration settings for Jurojin Social Reposter.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / ".env")

# WordPress API configuration
WORDPRESS_URL = "https://jurojin.net/wp-json/wp/v2"
WORDPRESS_PER_PAGE = 100

# Custom Post Types to synchronize
WORDPRESS_POST_TYPES = [
    {"name": "Articles", "endpoint": "posts", "evergreen": True},
    {"name": "Guides d'achat", "endpoint": "guide", "evergreen": True},
    {"name": "Tests Jeux", "endpoint": "jeu", "evergreen": True},
    {"name": "Glossaire 3D", "endpoint": "glossaire", "evergreen": True},
    {"name": "Animes", "endpoint": "anime", "evergreen": False},
]

# API Keys (set via environment variables or directly here)
AYRSHARE_API_KEY = os.getenv("AYRSHARE_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# Database and logging paths
DATABASE_PATH = BASE_DIR / "data" / "jurojin.db"
LOG_PATH = BASE_DIR / "logs" / "reposter.log"

# Character limits per platform
CHAR_LIMITS = {
    "twitter": 240,
    "facebook": 300,
}

# Ayrshare API endpoint
AYRSHARE_API_URL = "https://api.ayrshare.com/api/post"
