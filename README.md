# Jurojin Social Reposter

Outil Python pour republier automatiquement les anciens articles du blog Jurojin.net sur Twitter et Facebook avec des accroches generees par IA.

## Installation

### 1. Cloner le projet

```bash
git clone <repository-url>
cd jurojin-reposter
```

### 2. Creer un environnement virtuel

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate     # Windows
```

### 3. Installer les dependances

```bash
pip install -r requirements.txt
```

### 4. Configurer les API

Editez `config/settings.py` ou definissez les variables d'environnement :

```bash
export AYRSHARE_API_KEY="votre-cle-ayrshare"
export GROQ_API_KEY="votre-cle-groq"
```

## Utilisation

### Synchroniser les articles WordPress

```bash
# Sync incremental (articles recents)
python -m cli.sync

# Sync complet (tous les articles)
python -m cli.sync --full
```

### Poster manuellement

```bash
# Poster sur Twitter
python -m cli.post --platform twitter

# Poster sur Facebook
python -m cli.post --platform facebook

# Mode simulation (dry-run)
python -m cli.post --platform twitter --dry-run

# Forcer un article specifique
python -m cli.post --platform twitter --article-id 123
```

### Scheduler (appele par cron)

```bash
python -m cli.scheduler
```

### Gestion

```bash
# Afficher les statistiques
python -m cli.manage --stats

# Afficher la queue de publication
python -m cli.manage --queue

# Afficher l'historique des reposts
python -m cli.manage --history 20

# Afficher les meilleurs articles
python -m cli.manage --top 10 --platform twitter

# Exclure un article
python -m cli.manage --exclude 123

# Inclure un article exclu
python -m cli.manage --include 123

# Forcer un article (boost de priorite)
python -m cli.manage --force 456

# Recalculer tous les scores
python -m cli.manage --recalculate
```

## Configuration CRON

Voir `crontab.txt` pour un exemple de configuration.

```bash
# Editer la crontab
crontab -e

# Sync WordPress tous les dimanches a 3h
0 3 * * 0 cd /path/to/jurojin-reposter && python -m cli.sync

# Scheduler toutes les heures
0 * * * * cd /path/to/jurojin-reposter && python -m cli.scheduler
```

## Configuration

### config/scoring.yaml

Definit :
- Les categories evergreen (score bonus)
- Les categories exclues
- Les poids de scoring
- L'intervalle minimum entre reposts

### config/scheduling.yaml

Definit :
- Les horaires de publication par plateforme
- La frequence de publication
- Les parametres de retry

## Logique de Scoring

Chaque article recoit un score base sur :

| Critere | Points |
|---------|--------|
| Categorie evergreen | +50 |
| Article > 6 mois | +30 |
| Jamais reposte | +40 |
| Pas reposte depuis 90j | +30 |
| Word count > 1000 | +20 |
| Priority boost | +boost |
| Article < 3 mois | -30 |
| Categorie exclue | score = 0 |
| Article exclu | score = 0 |

## Architecture

```
jurojin-reposter/
├── config/
│   ├── settings.py        # Cles API, URLs
│   ├── scoring.yaml       # Categories evergreen, poids
│   └── scheduling.yaml    # Horaires publication
├── core/
│   ├── database.py        # Connexion SQLite
│   ├── scoring.py         # Calcul du score
│   ├── selector.py        # Selection du meilleur article
│   └── scheduler.py       # Logique horaires + retry
├── sources/
│   └── wordpress.py       # API WordPress
├── publishers/
│   └── ayrshare.py        # API Ayrshare
├── ai/
│   ├── groq_client.py     # Generation accroches
│   └── templates.py       # Templates fallback
├── cli/
│   ├── sync.py            # Commande sync
│   ├── post.py            # Commande post
│   ├── scheduler.py       # Commande scheduler
│   └── manage.py          # Commande gestion
├── data/                  # Base SQLite
└── logs/                  # Fichiers de log
```

## APIs utilisees

- **WordPress REST API** : Recuperation des articles
- **Groq API** : Generation d'accroches via LLM (gratuit)
- **Ayrshare API** : Publication sur Twitter et Facebook

## Licence

MIT
