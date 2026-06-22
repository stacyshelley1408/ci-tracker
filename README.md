# CI Tracker

Automated competitive intelligence for B2B software companies. Monitors competitor websites, job boards, review platforms, RSS feeds, and Reddit — classifies signal strength with AI, and sends daily alerts and a weekly digest to your inbox.

Runs entirely on GitHub Actions. No server, no database, no infrastructure to manage.

## What it monitors

- **Website diffs** — detects copy changes on pages you care about (homepage, pricing, product pages, newsroom)
- **Job postings** — tracks new openings, filterable by department and seniority
- **News & press** — monitors RSS/Atom feeds for new articles
- **Review platforms** — G2, Gartner Peer Insights, Capterra, TrustRadius, PeerSpot, Spiceworks
- **Reddit** — searches reddit.com for company mentions via the Tavily API

## How it works

Each day at 11am ET, GitHub Actions runs all signal checks against your configured companies. New signals are classified by Gemini (or Anthropic Claude as an alternative) on a 1–5 importance scale. Events at or above your significance threshold trigger an email alert. Every Monday, a formatted HTML digest summarizes the week.

Snapshots and event history are stored on a separate `data` branch so your main branch stays clean.

## Setup

### 1. Fork this repo

Use the **Use this template** button (or fork) to create your own copy.

### 2. Add secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Recommended | AI signal classification (free tier available at [aistudio.google.com](https://aistudio.google.com)) |
| `ANTHROPIC_API_KEY` | Optional | Claude Haiku as alternative or fallback classifier |
| `TAVILY_API_KEY` | Optional | Reddit mention search (free tier at [tavily.com](https://tavily.com)); the Reddit signal is skipped if unset |
| `SMTP_HOST` | Yes | Email server hostname (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | Yes | SMTP port (e.g. `587` for Gmail) |
| `SMTP_USER` | Yes | Your sending email address |
| `SMTP_PASSWORD` | Yes | App password (not your login password — [create one here](https://myaccount.google.com/apppasswords) for Gmail) |
| `ALERT_EMAIL` | Yes | Where alerts and digests are delivered |

If you skip both AI keys, the classifier falls back to deterministic keyword rules — still useful, just less nuanced.

### 3. Configure companies

Edit `config/companies.yml`. Replace the example companies with the ones you want to track. See the comments in that file for all available options.

At minimum, each company needs:
```yaml
- name: Acme Corp
  base_url: https://www.acmecorp.com
  active: true
```

Add pages, review URLs, news feeds, and signal overrides as needed.

### 4. Push and verify

Push your config changes. In **Actions**, manually trigger **Daily CI Run** via the "Run workflow" button to confirm everything works before waiting for the scheduled run.

On the first run, a `data` branch is created automatically to store snapshots and event history.

## Configuration reference

### Significance threshold

Controls which events generate alert emails. The AI classifier scores each signal 1–5:

| Score | Meaning |
|---|---|
| 5 | Major move — act today (funding, acquisition, exec departure, product launch) |
| 4 | Significant — worth tracking (messaging shift, notable hire, partnership) |
| 3 | Moderate — include in weekly digest |
| 2 | Low signal — logged only |
| 1 | Noise — ignored |

Set `significance_threshold: 4` (default) to receive alerts on major signals. Lower to `3` for your primary tracked company to catch more. Raise to `5` to only hear about major events.

### Watch keywords

Optional per-company list of terms that instruct the classifier to bias scores upward when those terms appear. Use for product names, executive names, strategic terms, or events you never want to miss.

```yaml
watch_keywords:
  - funding
  - acquisition
  - your-product-name
```

### Alert scheduling

Each signal type can alert `daily` (immediate email) or `weekly` (digest only). Override per-company or per-page:

```yaml
signals:
  job_postings:
    alert: daily    # get immediate alerts on new hires
pages:
  - url: /pricing
    alert: daily    # pricing page changes are high priority
  - url: /about
    alert: weekly   # low-priority pages go to digest only
```

### AI classifier

By default, uses Gemini (`gemini-2.0-flash-lite`). To change the model, set a `GEMINI_MODEL` environment variable in your workflow or in the Actions environment. To use Anthropic Claude instead, set `ANTHROPIC_API_KEY` without `GEMINI_API_KEY`.

## Cost

| Component | Cost |
|---|---|
| GitHub Actions | Free (within standard limits for public repos) |
| Gemini (default) | Free tier covers typical usage |
| Anthropic Claude Haiku | ~$1/month at daily run frequency |
| SMTP (Gmail) | Free with app password |

## Local development

```bash
git clone <your-fork>
cd ci-tracker
pip install -r requirements.txt
playwright install chromium

# Create a .env file with your secrets
cp .env.example .env   # then fill in values

cd src
python run.py --mode daily    # run signals + alerts
python run.py --mode weekly   # run signals + alerts + digest
```

## Project structure

```
ci-tracker/
├── .github/workflows/
│   ├── daily.yml         # runs daily at 11am ET
│   └── weekly.yml        # runs Monday at 10am ET
├── config/
│   └── companies.yml     # edit this to configure your targets
├── src/
│   ├── signals/
│   │   ├── messaging.py  # website page diffs
│   │   ├── jobs.py       # job posting tracker
│   │   ├── news.py       # RSS/Atom feed monitor
│   │   ├── reviews.py    # review platform scraper
│   │   └── reddit.py     # Reddit mention search
│   ├── classify.py       # AI signal classification
│   ├── storage.py        # event persistence (JSON on data branch)
│   ├── alert.py          # daily alert emails
│   ├── digest.py         # weekly HTML digest
│   └── run.py            # orchestration entry point
├── requirements.txt
└── README.md
```
