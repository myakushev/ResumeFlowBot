import os
import telebot
import time
from dotenv import load_dotenv
from mistralai import Mistral
from collections import defaultdict
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from io import BytesIO

# ---------- Session storage ----------
user_sessions = defaultdict(lambda: {
    "texts": [],
})

# ---------- Load env ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

TELEGRAM_MESSAGE_LIMIT = 4000
COVER_LETTER_MAX_CHARS = 800

# ---------- Prompts ----------
RESUME_PROMPT = """
You are a professional resume formatter.
Input: {user_text}

Output a structured Markdown resume with these fields:
- Full Name
- Title
- Summary
- Skills (bullet list)
- Experience (company, role, period, achievements)
- Education
- Languages
- Contacts

Do not invent any facts. Use only the information from the user.
"""

COVER_LETTER_PROMPT = f"""
You are a professional career coach.
Input: {{user_text}}

Write a short cover letter for a job application.
Rules:
- 3–5 sentences
- Maximum {COVER_LETTER_MAX_CHARS} characters
- Clear, professional tone
- No markdown, plain text only
"""

# ---------- Mistral API ----------
def call_mistral(prompt: str) -> str:
    """
    Генерация текста через Mistral SDK.
    Возвращает текст ассистента.
    """
    try:
        with Mistral(api_key=MISTRAL_API_KEY) as mistral:
            response = mistral.chat.complete(
                model="mistral-small-latest",
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )

            print("[MISTRAL FULL RESPONSE OBJ]")
            print(response)

            if hasattr(response, "model_dump_json"):
                print("[MISTRAL RESPONSE JSON]")
                print(response.model_dump_json())

            return response.choices[0].message.content

    except Exception as e:
        print(f"[MISTRAL ERROR] {e}")
        return "⚠️ Ошибка генерации. Попробуйте снова."

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
        "👋 Welcome to ResumeFlow!\n\n"
        "Send your experience or resume text.\n"
        "You can send multiple messages.\n\n"
        "When finished, press «✅ Сгенерировать резюме».",
        reply_markup=generate_keyboard()
    )
    print(f"[BOT] User {message.from_user.id} started the bot.")

@bot.message_handler(func=lambda message: message.text not in [
    "✅ Сгенерировать резюме",
    "❌ Очистить ввод"
])
def collect_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()

    if not text:
        return

    user_sessions[user_id]["texts"].append(text)

    bot.send_message(
        chat_id,
        "✍️ Текст добавлен. Можете отправить ещё или нажмите «Сгенерировать резюме».",
        reply_markup=generate_keyboard()
    )

    print(f"[BOT] Collected text chunk from user {user_id}")

@bot.message_handler(func=lambda message: message.text == "❌ Очистить ввод")
def clear_input(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    user_sessions[user_id]["texts"].clear()

    bot.send_message(
        chat_id,
        "🧹 Ввод очищен. Можете начать заново.",
        reply_markup=generate_keyboard()
    )

    print(f"[BOT] Cleared session for user {user_id}")

@bot.message_handler(func=lambda message: message.text == "✅ Сгенерировать резюме")
def generate_resume(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    texts = user_sessions[user_id]["texts"]

    if not texts:
        bot.send_message(chat_id, "⚠️ Вы ещё не отправили данные.")
        return

    full_text = "\n\n".join(texts)

    bot.send_message(chat_id, "⏳ Генерирую резюме и сопроводительное письмо…")

    print(f"[BOT] Generating resume for user {user_id}")

    resume_markdown = call_mistral(
        RESUME_PROMPT.format(user_text=full_text)
    )

    cover_letter = call_mistral(
        COVER_LETTER_PROMPT.format(user_text=full_text)
    )

    # ---------- Send resume ----------
    if len(resume_markdown) > TELEGRAM_MESSAGE_LIMIT:
        file_buffer = BytesIO(resume_markdown.encode("utf-8"))
        file_buffer.name = "resume.md"

        bot.send_document(
            chat_id,
            file_buffer,
            caption="📄 Your resume (Markdown file)"
        )

        print(f"[BOT] Resume sent as file to user {user_id}")
    else:
        bot.send_message(
            chat_id,
            f"📄 *Your Resume (Markdown)*\n\n{resume_markdown}",
            parse_mode="Markdown"
        )

    # ---------- Send cover letter (always text) ----------
    bot.send_message(
        chat_id,
        f"✉️ *Short Cover Letter*\n\n{cover_letter}",
        parse_mode="Markdown"
    )

    user_sessions[user_id]["texts"].clear()

    print(f"[BOT] Sent resume and cover letter to user {user_id}")

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
