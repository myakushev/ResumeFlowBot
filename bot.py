import os
import time
import json
import logging
from io import BytesIO
from collections import defaultdict

import telebot
from telebot import apihelper
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from mistralai import Mistral

from resume_renderer import render_resume_html, html_to_pdf_bytes

# ---------- Logging setup ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ---------- Session storage ----------
user_sessions = defaultdict(lambda: {"texts": []})

# ---------- Load env ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

# <<< Настраиваем прокси для подключения к нашему новому контейнеру
apihelper.proxy = {'https': 'socks5h://proxy:1080'}
logger.info("Using internal proxy at socks5h://proxy:1080")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

TELEGRAM_MESSAGE_LIMIT = 4000
COVER_LETTER_MAX_CHARS = 1200

# ---------- Prompt ----------
RESUME_JSON_PROMPT = f"""
You are a professional resume analyst and career coach.

INPUT (raw user messages):
{{user_text}}

GOAL:
Return a SINGLE valid JSON object with:
1) Structured resume data (for PDF rendering)
2) A ready-to-send Markdown resume (for Telegram message/file)
3) A short cover letter (plain text)

STRICT RULES:
- Output MUST be only valid JSON. No Markdown outside JSON. No comments. No explanations.
- Do NOT invent facts. Use only input data.
- If something is missing, use empty string "" or empty array [].
- resume_markdown must be readable and well-structured (headings, bullet lists).
- cover_letter: 3–5 sentences, professional tone, plain text (no markdown), max {COVER_LETTER_MAX_CHARS} characters.

ADDITIONAL RULES:
- if full_name is empty, use default name "User Name" as a full_name
- summary should be not longer than 640 symbols, it should be logically well done
- responsibilities/achievements item should be not longer than 400 symbols 

JSON SCHEMA (exact keys):
{{
  "resume_data": {{
    "full_name": "",
    "title": "",
    "summary": "",
    "contacts": {{
      "phone": "",
      "email": "",
      "telegram": "",
      "linkedin": "",
      "location": ""
    }},
    "skills": [],
    "experience": [
      {{
        "company": "",
        "location": "",
        "role": "",
        "period": "",
        "responsibilities": [],
        "achievements": [],
        "tech_stack": []
      }}
    ],
    "education": [
      {{
        "institution": "",
        "location": "",
        "degree": "",
        "field": "",
        "year": ""
      }}
    ],
    "certifications": [],
    "languages": []
  }},
  "resume_markdown": "",
  "cover_letter": ""
}}
""".strip()


def call_mistral(prompt: str, chat_id: int = None) -> str:
    log_prefix = f"[chat:{chat_id}]" if chat_id else "[no_chat]"
    logger.info(f"{log_prefix} Calling Mistral API...")

    try:
        with Mistral(api_key=MISTRAL_API_KEY) as mistral:
            resp = mistral.chat.complete(
                model="mistral-small-latest",
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )
            logger.info(f"{log_prefix} Mistral API response received successfully")
            return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"{log_prefix} Mistral API error: {e}")
        raise


def extract_json(text: str, chat_id: int = None) -> dict:
    """
    Достаёт первый JSON-объект из строки.
    """
    log_prefix = f"[chat:{chat_id}]" if chat_id else "[no_chat]"
    logger.info(f"{log_prefix} Extracting JSON from response...")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.error(f"{log_prefix} No JSON object found in model output")
        logger.debug(f"{log_prefix} Raw text was: {text[:500]}...")
        raise ValueError("No JSON object found in model output")

    try:
        result = json.loads(text[start:end + 1])
        logger.info(f"{log_prefix} JSON extracted successfully")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"{log_prefix} JSON decode error: {e}")
        raise


# ---------- Keyboard ----------
def generate_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("✅ Сгенерировать резюме"),
        KeyboardButton("❌ Очистить ввод")
    )
    return kb


# ---------- Handlers ----------
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    logger.info(f"[chat:{chat_id}] /start command received")
    bot.send_message(
        chat_id,
        "Welcome to ResumeFlow!\n\n"
        "Send your experience/resume text in single or multiple messages.\n"
        "When finished, press «✅ Сгенерировать резюме».",
        reply_markup=generate_keyboard()
    )


@bot.message_handler(func=lambda m: m.text not in ["✅ Сгенерировать резюме", "❌ Очистить ввод"])
def collect_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()

    logger.info(f"[chat:{chat_id}] Text message received from user {user_id}, length: {len(text)} chars")

    if not text:
        logger.warning(f"[chat:{chat_id}] Empty text received, ignoring")
        return

    user_sessions[user_id]["texts"].append(text)
    logger.info(
        f"[chat:{chat_id}] Text added to session. Total texts in session: {len(user_sessions[user_id]['texts'])}")

    bot.send_message(
        chat_id,
        "Текст добавлен. Можете отправить ещё или нажмите «Сгенерировать резюме».",
        reply_markup=generate_keyboard()
    )


@bot.message_handler(func=lambda m: m.text == "❌ Очистить ввод")
def clear_input(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    texts_count = len(user_sessions[user_id]["texts"])
    user_sessions[user_id]["texts"].clear()

    logger.info(f"[chat:{chat_id}] Input cleared. Removed {texts_count} texts from session")
    bot.send_message(chat_id, "Ввод очищен.", reply_markup=generate_keyboard())


def wrap_markdown_code_block(md: str) -> str:
    """
    Оборачивает Markdown-текст в code block для Telegram.
    """
    if "```" in md:
        return ""
    return f"```markdown\n{md}\n```"


@bot.message_handler(func=lambda m: m.text == "✅ Сгенерировать резюме")
def generate_resume(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    logger.info(f"[chat:{chat_id}] ========== GENERATE RESUME STARTED ==========")

    texts = user_sessions[user_id]["texts"]
    if not texts:
        logger.warning(f"[chat:{chat_id}] No texts in session, aborting")
        bot.send_message(chat_id, "⚠️ Вы ещё не отправили данные.")
        return

    logger.info(f"[chat:{chat_id}] Processing {len(texts)} text(s) from session")
    full_text = "\n\n".join(texts)
    logger.info(f"[chat:{chat_id}] Combined text length: {len(full_text)} chars")

    msg = bot.send_message(chat_id, "⏳ Анализирую ваши данные...")

    try:
        # 1) Вызов LLM
        logger.info(f"[chat:{chat_id}] Step 1: Calling LLM...")
        prompt = RESUME_JSON_PROMPT.replace("{user_text}", full_text)
        raw = call_mistral(prompt, chat_id)
        logger.info(f"[chat:{chat_id}] LLM response length: {len(raw)} chars")

        # 2) Парсинг JSON
        logger.info(f"[chat:{chat_id}] Step 2: Parsing JSON...")
        payload = extract_json(raw, chat_id)

        resume_markdown = (payload.get("resume_markdown") or "").strip()
        cover_letter = (payload.get("cover_letter") or "").strip()
        resume_data = payload.get("resume_data") or {}

        logger.info(
            f"[chat:{chat_id}] Parsed data - markdown: {len(resume_markdown)} chars, cover_letter: {len(cover_letter)} chars")
        logger.info(f"[chat:{chat_id}] resume_data keys: {list(resume_data.keys())}")
        logger.info(f"[chat:{chat_id}] full_name in resume_data: '{resume_data.get('full_name', 'NOT FOUND')}'")

        # 3) Отправка резюме в Markdown
        logger.info(f"[chat:{chat_id}] Step 3: Sending Markdown version...")
        bot.edit_message_text("📄 Отправляю текстовую версию...", chat_id, msg.message_id)

        if not resume_markdown:
            logger.warning(f"[chat:{chat_id}] resume_markdown is empty!")
            bot.send_message(chat_id, "⚠️ Не удалось получить resume_markdown из JSON.")
        else:
            code_block = wrap_markdown_code_block(resume_markdown)
            if not code_block or len(f"📄 *Your Resume (Markdown)*\n\n{code_block}") > TELEGRAM_MESSAGE_LIMIT:
                logger.info(f"[chat:{chat_id}] Sending markdown as file (too long or contains code blocks)")
                file_buffer = BytesIO(resume_markdown.encode("utf-8"))
                file_buffer.name = "resume.md"
                bot.send_document(chat_id, file_buffer, caption="📄 Ваше резюме (Markdown файл)")
            else:
                logger.info(f"[chat:{chat_id}] Sending markdown as message")
                message_text = f"📄 *Your Resume (Markdown)*\n\n{code_block}"
                bot.send_message(chat_id, message_text, parse_mode="Markdown")

        # 4) Отправка cover letter
        if cover_letter:
            logger.info(f"[chat:{chat_id}] Step 4: Sending cover letter...")
            bot.send_message(chat_id, f"✉️ Short Cover Letter\n\n{cover_letter}")
        else:
            logger.warning(f"[chat:{chat_id}] No cover letter to send")

        # 5) Генерация PDF
        logger.info(f"[chat:{chat_id}] Step 5: PDF generation check...")
        logger.info(f"[chat:{chat_id}] Checking if full_name exists: '{resume_data.get('full_name')}'")

        if resume_data.get("full_name"):
            logger.info(f"[chat:{chat_id}] full_name found, starting PDF generation...")

            try:
                bot.edit_message_text("📄 Генерирую PDF-версию...", chat_id, msg.message_id)

                # Загрузка шрифтов
                logger.info(f"[chat:{chat_id}] Loading fonts...")
                import base64

                try:
                    with open("assets/fonts/Roboto-Regular.ttf", "rb") as f:
                        resume_data["font_roboto_regular"] = base64.b64encode(f.read()).decode('utf-8')
                    logger.info(f"[chat:{chat_id}] Roboto-Regular loaded")
                except FileNotFoundError as e:
                    logger.error(f"[chat:{chat_id}] Font file not found: {e}")
                    raise

                try:
                    with open("assets/fonts/Roboto-Bold.ttf", "rb") as f:
                        resume_data["font_roboto_bold"] = base64.b64encode(f.read()).decode('utf-8')
                    logger.info(f"[chat:{chat_id}] Roboto-Bold loaded")
                except FileNotFoundError as e:
                    logger.error(f"[chat:{chat_id}] Font file not found: {e}")
                    raise

                try:
                    with open("assets/fonts/Roboto-Italic.ttf", "rb") as f:
                        resume_data["font_roboto_italic"] = base64.b64encode(f.read()).decode('utf-8')
                    logger.info(f"[chat:{chat_id}] Roboto-Italic loaded")
                except FileNotFoundError as e:
                    logger.error(f"[chat:{chat_id}] Font file not found: {e}")
                    raise

                logger.info(f"[chat:{chat_id}] All fonts loaded successfully")

                # Рендеринг HTML
                logger.info(f"[chat:{chat_id}] Rendering HTML template...")
                try:
                    rendered_html = render_resume_html(resume_data)
                    logger.info(f"[chat:{chat_id}] HTML rendered successfully, length: {len(rendered_html)} chars")
                except Exception as e:
                    logger.error(f"[chat:{chat_id}] HTML rendering error: {e}")
                    import traceback
                    logger.error(f"[chat:{chat_id}] Traceback: {traceback.format_exc()}")
                    raise

                # Конвертация в PDF
                logger.info(f"[chat:{chat_id}] Converting HTML to PDF...")
                try:
                    pdf_bytes = html_to_pdf_bytes(rendered_html)
                    logger.info(f"[chat:{chat_id}] PDF generated successfully, size: {len(pdf_bytes)} bytes")
                except Exception as e:
                    logger.error(f"[chat:{chat_id}] PDF conversion error: {e}")
                    import traceback
                    logger.error(f"[chat:{chat_id}] Traceback: {traceback.format_exc()}")
                    raise

                # Отправка PDF
                logger.info(f"[chat:{chat_id}] Sending PDF to user...")
                safe_name = "".join(c for c in resume_data["full_name"] if c.isalnum() or c in " _").rstrip()
                pdf_filename = f"Resume_{safe_name}.pdf"
                logger.info(f"[chat:{chat_id}] PDF filename: {pdf_filename}")

                pdf_file_buffer = BytesIO(pdf_bytes)
                pdf_file_buffer.name = pdf_filename
                bot.send_document(chat_id, pdf_file_buffer, caption="📄✨ Ваше резюме в формате PDF")
                logger.info(f"[chat:{chat_id}] PDF sent successfully!")

                bot.delete_message(chat_id, msg.message_id)

            except Exception as e:
                logger.error(f"[chat:{chat_id}] PDF generation failed: {e}")
                import traceback
                logger.error(f"[chat:{chat_id}] Full traceback:\n{traceback.format_exc()}")
                bot.send_message(chat_id, "⚠️ Не удалось сгенерировать PDF-версию резюме. Но текстовые версии готовы!")
        else:
            logger.warning(f"[chat:{chat_id}] full_name is empty or missing, skipping PDF generation")
            logger.warning(
                f"[chat:{chat_id}] resume_data content: {json.dumps(resume_data, ensure_ascii=False, indent=2)[:1000]}")
            bot.delete_message(chat_id, msg.message_id)

        logger.info(f"[chat:{chat_id}] ========== GENERATE RESUME COMPLETED ==========")

    except Exception as e:
        logger.error(f"[chat:{chat_id}] CRITICAL ERROR in generate_resume: {e}")
        import traceback
        logger.error(f"[chat:{chat_id}] Full traceback:\n{traceback.format_exc()}")
        bot.edit_message_text("⚠️ Ошибка генерации. Попробуйте снова.", chat_id, msg.message_id)

    finally:
        texts_count = len(user_sessions[user_id]["texts"])
        user_sessions[user_id]["texts"].clear()
        logger.info(f"[chat:{chat_id}] Session cleared, removed {texts_count} texts")


# ---------- Stable polling ----------
def run_bot():
    logger.info("========================================")
    logger.info("ResumeFlowBot is starting...")
    logger.info("========================================")

    while True:
        try:
            logger.info("Starting infinity_polling...")
            bot.infinity_polling(timeout=10, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Polling error: {e}. Reconnecting in 3 seconds...")
            time.sleep(3)


if __name__ == "__main__":
    run_bot()