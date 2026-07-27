#!/usr/bin/env python3
"""
PDF Generator CLI — Propulsé par Gemma 4 31B IT + Playwright
============================================================
Usage rapide (pipeline complet) :
  python main.py generate-pdf mon_rapport.pdf "Nouveau contenu..." -o output.pdf

Usage étape par étape :
  python main.py analyze  mon_rapport.pdf
  python main.py generate templates/mon_rapport/template.json "Mon contenu..."
  python main.py render   output/doc/output.html -o final.pdf
  python main.py refine   output/doc/output.html "Agrandir les titres"
  python main.py list-templates
"""

import json
import os
from datetime import datetime
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

from src.step1_ingest import pdf_to_images
from src.step2_analyze import analyze_pages
from src.step3_generate import generate_html, refine_html
from src.step4_render import render_pdf

# ── Config ─────────────────────────────────────────────────────────────────────
app = typer.Typer(
    name="pdf-gen",
    help="🚀 Générateur de PDF intelligent propulsé par Gemma 4 31B IT",
    add_completion=False,
)
console = Console()

UPLOADS_DIR   = Path("uploads")
TEMPLATES_DIR = Path("templates")
OUTPUT_DIR    = Path("output")

def _model() -> str:
    return os.getenv("GEMMA_MODEL", "gemini-3.5-flash")

def _banner():
    console.print(Panel.fit(
        "[bold cyan]PDF Generator[/bold cyan] [dim]• Gemma 4 31B IT + Playwright[/dim]",
        border_style="cyan",
    ))

def _summary(html_path: str | None, pdf_path: str | None):
    t = Table(border_style="green", show_header=False)
    t.add_column("", style="bold")
    t.add_column("", style="cyan")
    if html_path: t.add_row("📄 HTML", html_path)
    if pdf_path:  t.add_row("📕 PDF",  pdf_path)
    console.print(t)


# ── Commands ───────────────────────────────────────────────────────────────────

@app.command("analyze")
def cmd_analyze(
    pdf_path: str = typer.Argument(..., help="Chemin vers le PDF à analyser"),
    name: str = typer.Option(None, "--name", "-n", help="Nom du template"),
    dpi:  int = typer.Option(200, "--dpi", help="Résolution de conversion"),
):
    """📄 Analyse un PDF et extrait son template JSON (étapes 1+2)."""
    _banner()
    p = Path(pdf_path)
    if not p.exists():
        console.print(f"[red]❌ Fichier introuvable : {p}[/red]"); raise typer.Exit(1)

    tpl_name  = name or p.stem
    tpl_dir   = TEMPLATES_DIR / tpl_name
    imgs_dir  = UPLOADS_DIR   / tpl_name

    image_paths = pdf_to_images(str(p), str(imgs_dir), dpi=dpi)
    analyze_pages(image_paths, str(tpl_dir), model_name=_model())

    console.print(f"\n[bold green]✅ Template prêt : {tpl_dir / 'template.json'}[/bold green]")
    console.print(f"[dim]Prochaine étape : python main.py generate {tpl_dir}/template.json \"Votre contenu...\"[/dim]")


@app.command("generate")
def cmd_generate(
    template_path: str = typer.Argument(..., help="Chemin vers le template JSON"),
    content:       str = typer.Argument(..., help="Contenu textuel brut"),
    name:          str = typer.Option(None, "--name", "-n", help="Nom du fichier de sortie"),
    no_render:    bool = typer.Option(False, "--no-render", help="Ne pas générer le PDF"),
):
    """⚙️  Génère un document depuis un template JSON (étapes 3+4)."""
    _banner()
    tp = Path(template_path)
    if not tp.exists():
        console.print(f"[red]❌ Template introuvable : {tp}[/red]"); raise typer.Exit(1)

    template  = json.loads(tp.read_text(encoding="utf-8"))
    out_name  = name or f"doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir   = OUTPUT_DIR / out_name

    html_path = generate_html(template, content, str(out_dir), _model())
    pdf_path  = None if no_render else render_pdf(html_path, str(out_dir / f"{out_name}.pdf"))

    console.print(f"\n[bold green]✅ Terminé ![/bold green]")
    _summary(html_path, pdf_path)


@app.command("render")
def cmd_render(
    html_path: str = typer.Argument(..., help="Chemin vers le HTML"),
    output:    str = typer.Option(None, "--output", "-o", help="Chemin du PDF de sortie"),
):
    """🖨️  Convertit un HTML existant en PDF (étape 4 seule)."""
    _banner()
    hp = Path(html_path)
    if not hp.exists():
        console.print(f"[red]❌ HTML introuvable : {hp}[/red]"); raise typer.Exit(1)

    pdf_path = render_pdf(html_path, output or str(hp.with_suffix(".pdf")))
    console.print(f"\n[bold green]✅ PDF : {pdf_path}[/bold green]")


@app.command("refine")
def cmd_refine(
    html_path:  str  = typer.Argument(..., help="HTML à affiner"),
    feedback:   str  = typer.Argument(..., help="Instructions de correction"),
    no_render: bool  = typer.Option(False, "--no-render", help="Ne pas régénérer le PDF"),
):
    """✏️  Affine un HTML avec des corrections ciblées, puis re-génère le PDF."""
    _banner()
    updated = refine_html(html_path, feedback, _model())
    pdf_path = None if no_render else render_pdf(updated, str(Path(html_path).with_suffix(".pdf")))
    console.print(f"\n[bold green]✅ Raffinement appliqué ![/bold green]")
    _summary(updated, pdf_path)


@app.command("generate-pdf")
def cmd_full_pipeline(
    pdf_source: str = typer.Argument(..., help="PDF source à analyser"),
    content:    str = typer.Argument(..., help="Contenu du nouveau document"),
    output:     str = typer.Option(None, "--output", "-o", help="Chemin du PDF de sortie"),
    dpi:        int = typer.Option(200, "--dpi"),
):
    """🚀 PIPELINE COMPLET : analyse un PDF source, génère un nouveau PDF. Commande principale."""
    _banner()
    console.print(Panel(
        "1️⃣  PDF → Images\n"
        "2️⃣  Images → Template JSON  [dim](Gemma 4 Vision)[/dim]\n"
        "3️⃣  Template + Contenu → HTML/CSS  [dim](Gemma 4 Code)[/dim]\n"
        "4️⃣  HTML → PDF  [dim](Playwright)[/dim]",
        title="Pipeline", border_style="dim",
    ))

    p = Path(pdf_source)
    if not p.exists():
        console.print(f"[red]❌ PDF introuvable : {pdf_source}[/red]"); raise typer.Exit(1)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"gen_{ts}"
    out_dir  = OUTPUT_DIR / out_name

    image_paths = pdf_to_images(str(p), str(UPLOADS_DIR / p.stem), dpi=dpi)
    template    = analyze_pages(image_paths, str(TEMPLATES_DIR / p.stem), model_name=_model())
    html_path   = generate_html(template, content, str(out_dir), _model())
    pdf_path    = render_pdf(html_path, output or str(out_dir / f"{out_name}.pdf"))

    console.print(f"\n[bold green]🎉 Pipeline terminé ![/bold green]")
    _summary(html_path, pdf_path)


@app.command("list-templates")
def cmd_list():
    """📋 Liste tous les templates JSON disponibles."""
    _banner()
    TEMPLATES_DIR.mkdir(exist_ok=True)
    templates = list(TEMPLATES_DIR.rglob("template.json"))
    if not templates:
        console.print("[yellow]Aucun template. Utilisez 'analyze' pour en créer un.[/yellow]")
        return

    t = Table(title="Templates disponibles", border_style="cyan")
    t.add_column("Nom", style="bold cyan")
    t.add_column("Pages", justify="right")
    t.add_column("Couleur principale")
    t.add_column("Chemin", style="dim")

    for tp in templates:
        data  = json.loads(tp.read_text())
        color = data.get("styles", {}).get("primary_color", "?")
        t.add_row(tp.parent.name, str(data.get("total_pages", "?")), color, str(tp))

    console.print(t)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for d in [UPLOADS_DIR, TEMPLATES_DIR, OUTPUT_DIR]:
        d.mkdir(exist_ok=True)
    app()
