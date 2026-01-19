import os
import asyncio
import logging
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

# Logging setup
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"])
)


def render_resume_html(resume_data: dict) -> str:
    """
    Рендерит HTML из Jinja-шаблона.
    """
    logger.info("render_resume_html: Starting template rendering...")
    logger.info(f"render_resume_html: Template dir: {TEMPLATE_DIR}")
    logger.info(f"render_resume_html: resume_data keys: {list(resume_data.keys())}")

    try:
        template = env.get_template("resume_pdf.html")
        logger.info("render_resume_html: Template loaded successfully")

        result = template.render(**resume_data)
        logger.info(f"render_resume_html: Template rendered, output length: {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"render_resume_html: Error rendering template: {e}")
        raise


async def _html_to_pdf_playwright(html: str) -> bytes:
    """
    Асинхронно конвертирует HTML в PDF через Playwright.
    """
    logger.info("_html_to_pdf_playwright: Starting PDF conversion...")
    logger.info(f"_html_to_pdf_playwright: Input HTML length: {len(html)} chars")

    try:
        logger.info("_html_to_pdf_playwright: Launching playwright...")
        async with async_playwright() as p:
            logger.info("_html_to_pdf_playwright: Launching Chromium browser...")
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            logger.info("_html_to_pdf_playwright: Browser launched successfully")

            logger.info("_html_to_pdf_playwright: Creating new page...")
            page = await browser.new_page()
            logger.info("_html_to_pdf_playwright: Page created")

            logger.info("_html_to_pdf_playwright: Setting page content...")
            await page.set_content(html, wait_until='networkidle')
            logger.info("_html_to_pdf_playwright: Content set successfully")

            logger.info("_html_to_pdf_playwright: Generating PDF...")
            pdf_bytes = await page.pdf(
                format='Letter',
                print_background=True,
                margin={
                    'top': '40px',
                    'right': '36px',
                    'bottom': '45px',
                    'left': '36px'
                }
            )
            logger.info(f"_html_to_pdf_playwright: PDF generated, size: {len(pdf_bytes)} bytes")

            logger.info("_html_to_pdf_playwright: Closing browser...")
            await browser.close()
            logger.info("_html_to_pdf_playwright: Browser closed, returning PDF")

            return pdf_bytes

    except Exception as e:
        logger.error(f"_html_to_pdf_playwright: Error during PDF conversion: {e}")
        import traceback
        logger.error(f"_html_to_pdf_playwright: Traceback: {traceback.format_exc()}")
        raise


def html_to_pdf_bytes(html: str) -> bytes:
    """
    Синхронная обёртка для конвертации HTML в PDF.
    """
    logger.info("html_to_pdf_bytes: Starting sync wrapper...")

    try:
        loop = asyncio.get_running_loop()
        logger.info("html_to_pdf_bytes: Running loop detected, using ThreadPoolExecutor...")

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _html_to_pdf_playwright(html))
            result = future.result()
            logger.info(f"html_to_pdf_bytes: Got result from executor, size: {len(result)} bytes")
            return result

    except RuntimeError:
        logger.info("html_to_pdf_bytes: No running loop, using asyncio.run directly...")
        result = asyncio.run(_html_to_pdf_playwright(html))
        logger.info(f"html_to_pdf_bytes: Got result from asyncio.run, size: {len(result)} bytes")
        return result