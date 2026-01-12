import os
import time
import json
from io import BytesIO
from collections import defaultdict

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from mistralai import Mistral

# from resume_renderer import render_resume_html, html_to_pdf_bytes

# ---------- Session storage ----------
user_sessions = defaultdict(lambda: {"texts": []})

# ---------- Load env ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

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
1) Structured resume data (for future PDF rendering)
2) A ready-to-send Markdown resume (for Telegram message/file)
3) A short cover letter (plain text)

STRICT RULES:
- Output MUST be only valid JSON. No Markdown outside JSON. No comments. No explanations.
- Do NOT invent facts. Use only input data.
- If something is missing, use empty string "" or empty array [].
- resume_markdown must be readable and well-structured (headings, bullet lists).
- cover_letter: 3–5 sentences, professional tone, plain text (no markdown), max {COVER_LETTER_MAX_CHARS} characters.

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

def call_mistral(prompt: str) -> str:
    with Mistral(api_key=MISTRAL_API_KEY) as mistral:
        resp = mistral.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return resp.choices[0].message.content

def extract_json(text: str) -> dict:
    """
    Достаёт первый JSON-объект из строки.
    Нужен на случай, если модель вдруг добавит лишние символы вокруг JSON.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(text[start:end + 1])

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
    bot.send_message(
        message.chat.id,
        "Welcome to ResumeFlow!\n\n"
        "Send your experience/resume text in single or multiple messages.\n"
        "When finished, press «✅ Сгенерировать резюме».",
        reply_markup=generate_keyboard()
    )

@bot.message_handler(func=lambda m: m.text not in ["✅ Сгенерировать резюме", "❌ Очистить ввод"])
def collect_text(message):
    user_id = message.from_user.id
    print(f"New message from {user_id}: {message.text}")
    text = (message.text or "").strip()
    if not text:
        return

    user_sessions[user_id]["texts"].append(text)

    # --- ДОБАВЛЯЕМ СТРОКУ ДЛЯ ОТЛАДКИ ---
    print("--- Текущее состояние user_sessions ---")
    print(user_sessions)
    print("------------------------------------")

    bot.send_message(
        message.chat.id,
        "Текст добавлен. Можете отправить ещё или нажмите «Сгенерировать резюме».",
        reply_markup=generate_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "❌ Очистить ввод")
def clear_input(message):
    user_id = message.from_user.id
    user_sessions[user_id]["texts"].clear()
    bot.send_message(message.chat.id, "Ввод очищен.", reply_markup=generate_keyboard())

def wrap_markdown_code_block(md: str) -> str:
    """
    Оборачивает Markdown-текст в code block для Telegram.
    Если внутри есть ``` — лучше не слать как блок (сломает разметку).
    """
    if "```" in md:
        return ""
    return f"```markdown\n{md}\n```"

@bot.message_handler(func=lambda m: m.text == "✅ Сгенерировать резюме")
def generate_resume(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    texts = user_sessions[user_id]["texts"]
    if not texts:
        bot.send_message(chat_id, "⚠️ Вы ещё не отправили данные.")
        return

    full_text = "\n\n".join(texts)

    bot.send_message(chat_id, "⏳ Генерирую резюме и сопроводительное письмо…")

    try:
        # 1) Один вызов LLM → один JSON
        prompt = RESUME_JSON_PROMPT.replace("{user_text}", full_text)
        raw = call_mistral(prompt)
        payload = extract_json(raw)

        resume_markdown = (payload.get("resume_markdown") or "").strip()
        cover_letter = (payload.get("cover_letter") or "").strip()

        # На будущее: структурные данные для PDF/шаблона
        resume_data = payload.get("resume_data") or {}

        # 2) Отправка резюме: либо файлом, либо как markdown в code block
        if not resume_markdown:
            bot.send_message(chat_id, "⚠️ Не удалось получить resume_markdown из JSON.")
        else:
            code_block = wrap_markdown_code_block(resume_markdown)

            # Если есть ``` внутри — блок сломается, отправим файлом
            if not code_block:
                file_buffer = BytesIO(resume_markdown.encode("utf-8"))
                file_buffer.name = "resume.md"
                bot.send_document(chat_id, file_buffer, caption="📄 Your resume (Markdown file)")
            else:
                # Учитываем заголовок + обрамление code block
                message_text = f"📄 *Your Resume (Markdown)*\n\n{code_block}"

                if len(message_text) > TELEGRAM_MESSAGE_LIMIT:
                    file_buffer = BytesIO(resume_markdown.encode("utf-8"))
                    file_buffer.name = "resume.md"
                    bot.send_document(chat_id, file_buffer, caption="📄 Your resume (Markdown file)")
                else:
                    bot.send_message(chat_id, message_text, parse_mode="Markdown")

        # 3) Отправка cover letter (лучше plain text, без parse_mode)
        if cover_letter:
            bot.send_message(chat_id, f"✉️ Short Cover Letter\n\n{cover_letter}")

        # Debug (опционально)
        print("[RESUME_DATA STRUCT]")
        print(json.dumps(resume_data, ensure_ascii=False, indent=2))

    except Exception as e:
        print(f"[GENERATE_RESUME ERROR] {e}")
        bot.send_message(chat_id, "⚠️ Ошибка генерации. Попробуйте снова.")

    finally:
        user_sessions[user_id]["texts"].clear()

# ---------- Stable polling ----------
def run_bot():
    print("ResumeFlowBot is running...")
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=30)
        except Exception as e:
            print(f"[BOT ERROR] Polling error: {e}. Reconnecting in 3 seconds...")
            time.sleep(3)

if __name__ == "__main__":
    run_bot()