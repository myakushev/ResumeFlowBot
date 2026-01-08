import os
import telebot
import requests
import time
from dotenv import load_dotenv
from mistralai import Mistral

# ---------- Load env ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
# Загружаем ключ из окружения
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

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

COVER_LETTER_PROMPT = """
You are a professional career coach.
Input: {user_text}

Write a short cover letter (3-5 sentences) for a job application based on this experience.
"""

# ---------- Mistral API ----------
def call_mistral(prompt: str):
    """
    Генерация текста через Mistral SDK.
    Возвращает текст ассистента (resume/cover letter) и логирует полный ответ.
    """
    try:
        from mistralai import Mistral
        import os

        with Mistral(api_key=os.getenv("MISTRAL_API_KEY", "")) as mistral:
            response = mistral.chat.complete(
                model="mistral-small-latest",
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )

            # Полный объект для логирования
            print("[MISTRAL FULL RESPONSE OBJ]")
            print(response)

            # JSON-представление для логирования (по возможности)
            if hasattr(response, "json"):
                print("[MISTRAL .model_dump_json()]")
                print(response.model_dump_json())

            # Достаем текст из объекта через правильные атрибуты
            try:
                text_out = response.choices[0].message.content
                return text_out
            except Exception as e:
                print(f"[MISTRAL PARSE ERROR] {e}")
                return "⚠️ Ответ получен, но не удалось выделить текст. Смотри полный лог."
    except Exception as e:
        print(f"[MISTRAL ERROR] {e}")
        return "⚠️ Ошибка генерации. Попробуйте снова."



# ---------- Handlers ----------
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Welcome to ResumeFlow!\n"
        "Send me your resume text or a description of your experience, "
        "and I'll generate a structured resume and short cover letter for you."
    )
    print(f"[BOT] User {message.from_user.id} started the bot.")

@bot.message_handler(func=lambda message: True)
def handle_resume(message):
    user_text = message.text.strip()
    chat_id = message.chat.id

    if not user_text:
        bot.send_message(chat_id, "⚠️ Пожалуйста, отправьте текст вашего резюме или опыта.")
        return

    print(f"[BOT] Received resume from user {message.from_user.id}")
    bot.send_message(chat_id, "✅ Got it! Generating your resume… Please wait.")

    # ---------- Generate resume ----------
    resume_markdown = call_mistral(RESUME_PROMPT.format(user_text=user_text))
    # ---------- Generate cover letter ----------
    cover_letter = call_mistral(COVER_LETTER_PROMPT.format(user_text=user_text))

    # ---------- Send results ----------
    bot.send_message(chat_id, f"📄 **Your Resume (Markdown)**:\n\n{resume_markdown}", parse_mode='Markdown')
    bot.send_message(chat_id, f"✉️ **Short Cover Letter**:\n\n{cover_letter}", parse_mode='Markdown')

    print(f"[BOT] Sent resume and cover letter to user {message.from_user.id}")

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
