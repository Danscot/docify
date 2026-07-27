# Docify 🚀

> Upload any PDF → clone its visual style → generate unlimited styled documents at scale.

Docify is a Django web application built on a 4-step AI pipeline:

```
📄 Source PDF
    ↓  Step 1  pdf2image → high-res PNG pages
    ↓  Step 2  Vision AI → template.json (layout, colors, fonts)
    ↓  Step 3  Code AI   → pixel-perfect HTML/CSS
    ↓  Step 4  Playwright → production-ready PDF
```

---

## Quick Start

```bash
git clone / unzip the project
cd docify
chmod +x setup.sh && ./setup.sh

# Add your API key
nano .env   # set GEMMA_API_KEY=...

source .venv/bin/activate
python manage.py runserver
```

Open http://127.0.0.1:8000

---

## Usage

1. **Upload a PDF** → Templates → Upload Template
2. Wait ~1–3 min for the style analysis to complete
3. **Generate a Document** → Documents → Generate Document
   - Pick the template, add a title, paste your content
4. Wait ~30–60 sec → download or preview your PDF
5. Use **Refine** on any document to apply targeted style tweaks

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | (dev key) | Set a real secret in production |
| `DEBUG` | `True` | Set `False` in production |
| `GEMMA_API_KEY` | — | Google AI Studio or compatible API key |
| `GEMMA_BASE_URL` | Google AI Studio OpenAI compat URL | Override for Vertex AI / local Ollama |
| `GEMMA_MODEL` | `gemini-2.0-flash` | Any vision+code capable model |

---

## Project Structure

```
docify/
├── manage.py
├── setup.sh
├── requirements.txt
├── .env.example
├── docify/          # Django project config
│   ├── settings.py
│   └── urls.py
├── core/            # Main Django app
│   ├── models.py    # Template + GeneratedDocument
│   ├── views.py     # All views + status polling endpoints
│   ├── forms.py     # Upload + Create + Refine forms
│   ├── pipeline.py  # Async thread wrappers around src/
│   ├── admin.py
│   └── templates/core/
│       ├── base.html
│       ├── dashboard.html
│       ├── template_list.html
│       ├── template_upload.html
│       ├── template_detail.html
│       ├── document_list.html
│       ├── document_create.html
│       └── document_detail.html
├── src/             # 4-step pipeline modules
│   ├── step1_ingest.py
│   ├── step2_analyze.py
│   ├── step3_generate.py
│   └── step4_render.py
└── static/
    ├── css/main.css
    └── js/main.js
```

---

## Production Notes

- Replace the background threading in `pipeline.py` with **Celery + Redis** for reliability at scale
- Set `DEBUG=False` and run `python manage.py collectstatic`
- Use **gunicorn** as the WSGI server
- Swap SQLite for **PostgreSQL**
