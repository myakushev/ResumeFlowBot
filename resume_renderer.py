from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

TEMPLATES_DIR = Path(__file__).parent / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"])
)

def render_resume_html(data: dict) -> str:
    template = env.get_template("resume.html.j2")
    return template.render(**data)

def html_to_pdf_bytes(html: str, base_url: str) -> bytes:
    return HTML(string=html, base_url=base_url).write_pdf()