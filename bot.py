import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaVideo
import yt_dlp
import tempfile
import os
import re
import subprocess
import time
import openai
import requests
import threading
import logging
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "2101512357"))  # ваш Telegram user_id
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@staritsin_school")  # ваш канал
CHANNEL_URL = "https://t.me/staritsin_school"
ROCKET_URL = "https://t.me/rocketcontentbot"

bot = telebot.TeleBot(API_TOKEN)
openai.api_key = OPENAI_API_KEY

logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

PLATFORM_PATTERNS = {
    'instagram': r'(https?://)?(www\.)?(instagram\.com|instagr\.am)/',
    'tiktok': r'(https?://)?(www\.)?tiktok\.com/',
    'youtube_shorts': r'(https?://)?(www\.)?youtube\.com/shorts/',
    'vk_clips': r'(https?://)?(www\.)?vk\.com/(clip|video)',
    'pinterest': r'(https?://)?(www\.)?pinterest\.',
    'twitter': r'(https?://)?(www\.)?(twitter\.com|x\.com)/',
}

user_links = {}
user_posts = {}
last_post_text = {}
user_state = {}
user_message_count = {}

def check_subscription(user_id):
    if user_id == OWNER_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.warning(f"Ошибка проверки подписки: {e}")
        return False

def build_subscribe_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Перейти в канал", url=CHANNEL_URL))
    return markup

def subscription_guard(func):
    def wrapper(message_or_call, *args, **kwargs):
        user_id = (
            message_or_call.from_user.id
            if hasattr(message_or_call, "from_user")
            else message_or_call.message.chat.id
        )
        if not check_subscription(user_id):
            chat_id = (
                message_or_call.message.chat.id
                if hasattr(message_or_call, "message")
                else message_or_call.chat.id
            )
            bot.send_message(
                chat_id,
                "❗ Чтобы пользоваться ботом, подпишитесь на канал @staritsin_school",
                reply_markup=build_subscribe_keyboard()
            )
            return
        return func(message_or_call, *args, **kwargs)
    return wrapper

def detect_platform(url):
    for platform, pattern in PLATFORM_PATTERNS.items():
        if re.search(pattern, url):
            logging.info(f"Платформа: {platform}")
            return platform
    logging.info("Платформа: не определена")
    return None

def build_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📥 Скачать видео", callback_data='download_video'),
        InlineKeyboardButton("🎧 Скачать аудио", callback_data='download_audio')
    )
    return markup

def build_rocket_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Ещё больше автоматизации тут", url=ROCKET_URL))
    return markup

def cleanup_files(files):
    for f in files:
        try:
            os.remove(f)
        except Exception:
            pass

def throttle_send_media_group(chat_id, media_files):
    for f in media_files:
        with open(f, 'rb') as file_obj:
            bot.send_video(chat_id, file_obj, supports_streaming=True)
        time.sleep(1)

@bot.message_handler(commands=['rewrite'])
@subscription_guard
def handle_rewrite_command(message):
    chat_id = message.chat.id
    post_text = user_posts.get(chat_id)
    if not post_text or not post_text.strip():
        bot.send_message(chat_id, "Нет текста для рерайта. Сначала скачайте видео с описанием.")
        return
    bot.send_chat_action(chat_id, 'typing')
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты профессиональный копирайтер. Перепиши текст поста, сохранив смысл, но сделай его коротким, цепляющим и эмоциональным для соцсетей на русском языке."},
                {"role": "user", "content": post_text}
            ],
            max_tokens=256,
            temperature=1.0,
        )
        rewritten = response.choices[0].message.content.strip()
        bot.send_message(chat_id, f"✍️ Вот рерайт поста:\n\n{rewritten}")
    except Exception as e:
        logging.error(f"Ошибка рерайта: {e}")
        bot.send_message(chat_id, "⚠️ Не удалось сделать рерайт"      
        )
def extract_audio_and_transcribe(video_path, chat_id):
    audio_path = video_path.rsplit('.', 1)[0] + '.mp3'
    subprocess.run([
        'ffmpeg', '-y', '-i', video_path, '-vn', '-acodec', 'mp3', audio_path
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        last_post_text[chat_id] = ""
        return ""
    try:
        with open(audio_path, "rb") as audio_file:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
            files = {"file": audio_file}
            data = {"model": "whisper-1", "language": "ru"}
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=60
            )
        if response.status_code == 200:
            text = response.json().get("text", "")
            last_post_text[chat_id] = text
            return text
        else:
            last_post_text[chat_id] = ""
            return ""
    except Exception as e:
        logging.error(f"Ошибка транскрибации: {e}")
        last_post_text[chat_id] = ""
        return ""

def set_user_state(chat_id, state):
    user_state[chat_id] = state

def get_user_state(chat_id):
    return user_state.get(chat_id, None)

def increment_message_count(chat_id):
    user_message_count[chat_id] = user_message_count.get(chat_id, 0) + 1

def after_video_sent(chat_id):
    user_links.pop(chat_id, None)
    user_posts.pop(chat_id, None)
    user_state[chat_id] = 'WAITING_FOR_LINK'

@bot.message_handler(commands=['start'])
@subscription_guard
def send_welcome(message):
    chat_id = message.chat.id
    user_links.pop(chat_id, None)
    user_posts.pop(chat_id, None)
    user_state[chat_id] = 'WAITING_FOR_LINK'
    bot.send_chat_action(chat_id, 'typing')
    time.sleep(0.5)
    welcome_text = (
        "👋 Привет! Отправь мне ссылку на видео 🎥\n"
        "Я помогу скачать видео и посты из Instagram Reels, TikTok, YouTube Shorts, VK Клипы, Pinterest, Twitter.\n\n"
        "⚡ Поддерживаются только вертикальные видео (9:16, 720x1280)."
    )
    bot.send_message(chat_id, welcome_text)

@bot.callback_query_handler(func=lambda call: call.data == 'check_subscription')
@subscription_guard
def handle_check_subscription(call: CallbackQuery):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, "✅ Подписка подтверждена! Можете пользоваться ботом.")
    bot.send_message(
        chat_id,
        "Спасибо за подписку! Теперь отправьте ссылку на видео или пост."
    )
    user_state[chat_id] = 'WAITING_FOR_LINK'

@bot.message_handler(commands=['donate'])
@subscription_guard
def handle_donate(message):
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(0.5)
    bot.send_message(
        message.chat.id,
        "🤝 Поддержать проект можно по ссылке: https://yoomoney.ru/to/410011161892505"
    )
    increment_message_count(message.chat.id)

@bot.message_handler(commands=['rocket'])
@subscription_guard
def handle_rocket(message):
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(0.5)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Хочешь ещё больше автоматизации?", url=ROCKET_URL))
    bot.send_message(
        message.chat.id,
        "🚀 Для ещё большей автоматизации переходи в @rocketcontentbot",
        reply_markup=markup
    )
    increment_message_count(message.chat.id)

@bot.message_handler(func=lambda message: True, content_types=['text'])
@subscription_guard
def handle_link(message):
    url = message.text.strip()
    chat_id = message.chat.id

    if user_state.get(chat_id) == 'WAITING_FOR_DOWNLOAD':
        bot.send_message(chat_id, "⏳ Подожди чуть-чуть, я ещё обрабатываю предыдущее видео...")
        return

    platform = detect_platform(url)
    if not platform:
        bot.reply_to(message, "⚠️ Формат не поддерживается или ссылка не распознана.")
        return

    user_links.pop(chat_id, None)
    user_posts.pop(chat_id, None)
    user_links[chat_id] = url
    user_state[chat_id] = 'WAITING_FOR_DOWNLOAD'

    bot.send_chat_action(chat_id, 'typing')
    time.sleep(0.5)
    bot.send_message(
        chat_id,
        "📥 Файл принят, обрабатываю… Это может занять 5–10 секунд. Наливай чай ☕"
    )
    threading.Thread(target=process_download, args=(chat_id, url)).start()

def process_download(chat_id, url):
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
                'quiet': True,
                'noplaylist': True,
                'merge_output_format': 'mp4',
                'format': 'bestvideo[ext=mp4][height<=1280][width<=720][vcodec!*=none]+bestaudio[ext=m4a]/best',
                'postprocessors': [],
                'http_headers': {'User-Agent': 'Mozilla/5.0'},
                'allow_unplayable_formats': False,
                'source_address': '0.0.0.0',
                'concurrent_fragment_downloads': 3,
                'retries': 5,
                'fragment_retries': 5,
                'nocheckcertificate': True,
                'geo_bypass': True,
                'geo_bypass_country': 'RU',
                'geo_bypass_ip_block': '0.0.0.0/0',
                'prefer_ffmpeg': True,
                'external_downloader_args': ['-headers', 'Referer: https://www.google.com/'],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                post_text = info.get('description') or info.get('title') or ""
                user_posts[chat_id] = post_text
                is_carousel = False
                media_files = []
                if 'entries' in info:
                    is_carousel = True
                    for entry in info['entries']:
                        filename = ydl.prepare_filename(entry)
                        filename = ensure_mp4(filename)
                        if os.path.exists(filename) and os.path.getsize(filename) > 0:
                            media_files.append(filename)
                else:
                    filename = ydl.prepare_filename(info)
                    filename = ensure_mp4(filename)
                    if os.path.exists(filename) and os.path.getsize(filename) > 0:
                        media_files.append(filename)

            # (1) Отправить видео (по одному, если карусель)
            if is_carousel and len(media_files) > 1:
                for f in media_files:
                    with open(f, 'rb') as file_obj:
                        bot.send_video(chat_id, file_obj, supports_streaming=True)
                        time.sleep(1)
            else:
                with open(media_files[0], 'rb') as f:
                    bot.send_video(chat_id, f, supports_streaming=True)

            # (2) Текст поста (если есть)
            if user_posts.get(chat_id):
                bot.send_message(chat_id, f"{user_posts[chat_id]}")

            # (3) Кнопка "ещё больше автоматизации"
            bot.send_message(
                chat_id,
                "✅ Видео загружено!",
                reply_markup=build_rocket_keyboard()
            )

            cleanup_files(media_files)
            after_video_sent(chat_id)
    except Exception as e:
        logging.error(f"Ошибка при скачивании/отправке: {e}")
        bot.send_message(chat_id, f"⚠️ Не удалось скачать видео. Вот ссылка: {url}\n{user_posts.get(chat_id, '')}")
        after_video_sent(chat_id)

def ensure_mp4(filename):
    if filename.endswith('.mp4'):
        return filename
    new_filename = os.path.splitext(filename)[0] + '.mp4'
    subprocess.run([
        'ffmpeg', '-y', '-i', filename,
        '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2',
        '-c:a', 'copy', new_filename
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return new_filename

@bot.callback_query_handler(func=lambda call: call.data == 'rewrite_post')
@subscription_guard
def handle_rewrite_post(call: CallbackQuery):
    chat_id = call.message.chat.id
    post_text = user_posts.get(chat_id)
    bot.answer_callback_query(call.id, "🔁 Рерайт запущен")
    if not post_text or not post_text.strip():
        bot.send_message(chat_id, "Нет текста для рерайта.")
        return
    try:
        bot.send_chat_action(chat_id, 'typing')
        time.sleep(1)
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты профессиональный копирайтер. Перепиши текст поста, сохранив смысл, но сделай его коротким, цепляющим и эмоциональным для соцсетей на русском языке."},
                {"role": "user", "content": post_text}
            ],
            max_tokens=256,
            temperature=1.0,
        )
        rewritten = response.choices[0].message.content.strip()
        bot.send_message(chat_id, f"✍️ Вот рерайт поста:\n\n{rewritten}")
    except Exception as e:
        logging.error(f"Ошибка рерайта: {e}")
        bot.send_message(chat_id, "⚠️ Не удалось сделать рерайт. Попробуйте позже.")

if __name__ == '__main__':
    print("Бот запущен")
    bot.infinity_polling()
