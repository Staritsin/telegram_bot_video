import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp
import tempfile
import os
import re
import subprocess
import time
import openai
import threading
import logging
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "2101512357"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@staritsin_school")
CHANNEL_URL = "https://t.me/staritsin_school"
ROCKET_URL = "https://t.me/rocketcontentbot"

bot = telebot.TeleBot(API_TOKEN)
openai.api_key = OPENAI_API_KEY

logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

PLATFORM_PATTERNS = {
    'instagram': r'(https?://)?(www\.)?(instagram\.com|instagr\.am)/',
    'tiktok': r'(https?://)?(www\.)?tiktok\.com/',
    'pinterest': r'(https?://)?(www\.)?pinterest\.',
}

user_links = {}
user_posts = {}
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
    markup.add(InlineKeyboardButton("Проверить подписку", callback_data="check_subscription"))
    return markup

def subscription_guard(func):
    def wrapper(message_or_call, *args, **kwargs):
        user_id = (
            message_or_call.from_user.id
            if hasattr(message_or_call, "from_user")
            else message_or_call.message.chat.id
        )
        if user_id == OWNER_ID:
            return func(message_or_call, *args, **kwargs)
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

def send_processing_status(chat_id, post_title, status, task_id):
    msg = bot.send_message(
        chat_id,
        f"<b>Контент машина</b>\n{post_title}\n\n"
        f"⏳ <b>Обработка вашего Reels</b>\n"
        f"🆔 <b>ID задачи:</b> {task_id}\n"
        f"✍️ <b>Статус:</b> {status}",
        parse_mode="HTML"
    )
    return msg.message_id

def update_processing_status(chat_id, message_id, post_title, status, task_id, done=False):
    prefix = "✅ <b>Задача успешно выполнена!</b>" if done else "⏳ <b>Обработка вашего Reels</b>"
    bot.edit_message_text(
        f"<b>Контент машина</b>\n{post_title}\n\n"
        f"{prefix}\n"
        f"🆔 <b>ID задачи:</b> {task_id}\n"
        f"✍️ <b>Статус:</b> {status}",
        chat_id,
        message_id,
        parse_mode="HTML"
    )

def ensure_mp4(filename):
    new_filename = os.path.splitext(filename)[0] + '.mp4'
    ffmpeg_cmd = [
        'ffmpeg', '-y', '-i', filename,
        '-vf', (
            'scale=w=720:h=1280:force_original_aspect_ratio=decrease,'
            'pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black'
        ),
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        new_filename
    ]
    subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return new_filename

def after_video_sent(chat_id):
    user_links.pop(chat_id, None)
    user_posts.pop(chat_id, None)
    user_state[chat_id] = 'WAITING_FOR_LINK'

def increment_message_count(chat_id):
    user_message_count[chat_id] = user_message_count.get(chat_id, 0) + 1

def process_download(chat_id, url):
    try:
        task_id = int(time.time())
        post_title = "Ваша задача"
        status_msg_id = send_processing_status(chat_id, post_title, "Обрабатываю видео...", task_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            platform = detect_platform(url)
            media_files = []
            post_text = ""

            if platform == 'instagram':
                media_files, post_text = download_instagram_content(url, tmpdir)
                user_posts[chat_id] = post_text
            elif platform == 'pinterest':
                file = download_pinterest_video(url, os.path.join(tmpdir, 'video.mp4'))
                if file and os.path.exists(file):
                    media_files.append(file)
            elif platform == 'tiktok':
                media_files, post_text = download_tiktok_video(url, tmpdir)
                user_posts[chat_id] = post_text

            update_processing_status(chat_id, status_msg_id, post_title, "Готово", task_id, done=True)

            if len(media_files) > 1:
                for f in media_files:
                    with open(f, 'rb') as file_obj:
                        bot.send_video(chat_id, file_obj, supports_streaming=True)
                        time.sleep(1)
            elif len(media_files) == 1:
                with open(media_files[0], 'rb') as f:
                    bot.send_video(chat_id, f, supports_streaming=True)
            else:
                raise Exception("No media files found")

            if user_posts.get(chat_id):
                bot.send_message(chat_id, f"{user_posts[chat_id]}")

            bot.send_message(
                chat_id,
                "✅ Видео загружено!",
                reply_markup=build_rocket_keyboard()
            )
            cleanup_files(media_files)
            after_video_sent(chat_id)
    except Exception as e:
        logging.error(f"Ошибка при скачивании/отправке: {e}")
        post_text = user_posts.get(chat_id, "")
        bot.send_message(chat_id, f"⚠️ Не удалось скачать видео. Вот ссылка: {url}\n{post_text}")
        after_video_sent(chat_id)

def download_tiktok_video(url, tmpdir):
    """
    Скачивает видео из TikTok по ссылке url.
    Возвращает (media_files, post_text).
    """
    import yt_dlp
    import os
    import subprocess

    ydl_opts = {
        'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
        'quiet': True,
        'merge_output_format': 'mp4',
        'format': (
            'bestvideo[ext=mp4][height<=1280][width<=720][vcodec!*=none]+'
            'bestaudio[ext=m4a]/best[ext=mp4][height<=1280][width<=720][vcodec!*=none]/best'
        ),
        'postprocessors': [],
        'http_headers': {'User-Agent': 'Mozilla/5.0'},
        'allow_unplayable_formats': False,
        'prefer_ffmpeg': True,
    }

    media_files = []
    post_text = ""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            post_text = info.get('description') or info.get('title') or ""
            filename = ydl.prepare_filename(info)
            new_filename = os.path.splitext(filename)[0] + '.mp4'
            subprocess.run([
                'ffmpeg', '-y', '-i', filename,
                '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                new_filename
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(new_filename) and os.path.getsize(new_filename) > 0:
                media_files.append(new_filename)
    except Exception as e:
        print(f"Ошибка скачивания TikTok: {e}")
    return media_files, post_text

def download_instagram_content(url, tmpdir):
    """
    Скачивает Reels, пост или карусель из Instagram по ссылке url.
    Требует, чтобы вы были залогинены в Instagram в Chrome на этом компьютере!
    Возвращает (media_files, post_text).
    """
    import yt_dlp
    import os
    import subprocess

    ydl_opts = {
    'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
    'quiet': True,
    'merge_output_format': 'mp4',
    'format': (
        'bestvideo[ext=mp4][height<=1280][width<=720][vcodec!*=none]+'
        'bestaudio[ext=m4a]/best[ext=mp4][height<=1280][width<=720][vcodec!*=none]/best'
    ),
    'postprocessors': [],
    'http_headers': {'User-Agent': 'Mozilla/5.0'},
    'allow_unplayable_formats': False,
    'prefer_ffmpeg': True,
    'cookiefile': 'instagram_cookies.txt',  # <-- используйте этот параметр
}

    media_files = []
    post_text = ""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            post_text = info.get('description') or info.get('title') or ""
            # Карусель (несколько видео/фото)
            if 'entries' in info and info['entries']:
                for entry in info['entries']:
                    filename = ydl.prepare_filename(entry)
                    new_filename = os.path.splitext(filename)[0] + '.mp4'
                    subprocess.run([
                        'ffmpeg', '-y', '-i', filename,
                        '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black',
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                        '-c:a', 'aac', '-b:a', '128k',
                        '-movflags', '+faststart',
                        new_filename
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if os.path.exists(new_filename) and os.path.getsize(new_filename) > 0:
                        media_files.append(new_filename)
            else:
                filename = ydl.prepare_filename(info)
                new_filename = os.path.splitext(filename)[0] + '.mp4'
                subprocess.run([
                    'ffmpeg', '-y', '-i', filename,
                    '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-c:a', 'aac', '-b:a', '128k',
                    '-movflags', '+faststart',
                    new_filename
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if os.path.exists(new_filename) and os.path.getsize(new_filename) > 0:
                    media_files.append(new_filename)
    except Exception as e:
        print(f"Ошибка скачивания Instagram: {e}")
    return media_files, post_text   

def download_pinterest_video(url, output_path):
    ydl_opts = {
        'outtmpl': output_path,
        'quiet': True,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            new_filename = os.path.splitext(filename)[0] + '.mp4'
            subprocess.run([
                'ffmpeg', '-y', '-i', filename,
                '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                new_filename
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return new_filename if os.path.exists(new_filename) else None
    except Exception as e:
        print(f"Ошибка при скачивании Pinterest видео: {e}")
        return None
    
@bot.message_handler(commands=['start'])
@subscription_guard
def send_welcome(message):
    chat_id = message.chat.id
    user_links.pop(chat_id, None)
    user_posts.pop(chat_id, None)
    user_state[chat_id] = 'WAITING_FOR_LINK'

    # ⚡ Используем уже готовое видео с правильным размером (720x1280)
    video_path = 'welcome_ready.mp4'  # заранее подготовленный файл

    if os.path.exists(video_path):
        try:
            with open(video_path, 'rb') as video:
                bot.send_video(chat_id, video, supports_streaming=True)
        except Exception as e:
            print(f'❌ Ошибка при отправке видео: {e}')

    bot.send_chat_action(chat_id, 'typing')
    time.sleep(0.5)
    welcome_text = (
        "👋 Привет! Отправь мне ссылку на видео 🎥\n"
        "Я помогу скачать видео и посты из Instagram, TikTok, Pinterest.\n\n"
        "⚡ Поддерживаются только вертикальные видео (9:16, 720x1280)."
    )
    bot.send_message(chat_id, welcome_text)


@bot.message_handler(commands=['menu'])
@subscription_guard
def show_menu(message):
    chat_id = message.chat.id
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Ещё больше автоматизации тут", url=ROCKET_URL))
    if chat_id == OWNER_ID:
        markup.add(InlineKeyboardButton("Статистика", callback_data="admin_stats"))
    bot.send_message(chat_id, "Главное меню:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def show_admin_stats(call):
    if call.message.chat.id != OWNER_ID:
        bot.answer_callback_query(call.id, "Нет доступа")
        return
    stats = (
        f"👤 Пользователей: {len(user_state)}\n"
        f"📥 Ссылок в очереди: {len(user_links)}\n"
        f"📝 Постов: {len(user_posts)}\n"
        f"🗂️ Всего сообщений: {sum(user_message_count.values()) if 'user_message_count' in globals() else 'N/A'}"
    )
    bot.send_message(call.message.chat.id, f"Статистика бота:\n{stats}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def handle_check_subscription(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id == OWNER_ID or check_subscription(user_id):
        bot.answer_callback_query(call.id, "✅ Подписка подтверждена! Можете пользоваться ботом.")
        bot.send_message(chat_id, "Спасибо за подписку! Теперь отправьте ссылку на видео или пост.")
        user_state[chat_id] = 'WAITING_FOR_LINK'
    else:
        bot.answer_callback_query(call.id, "❌ Подписка не найдена.")
        bot.send_message(
            chat_id,
            "❗ Я не вижу вашу подписку. Проверьте, что вы подписаны на канал @staritsin_school и попробуйте снова.",
            reply_markup=build_subscribe_keyboard()
        )

@bot.message_handler(commands=['donate'])
@subscription_guard
def handle_donate(message):
    chat_id = message.chat.id

    # Сообщение с основными способами оплаты
    markup_main = InlineKeyboardMarkup(row_width=1)
    markup_main.add(
        InlineKeyboardButton("T-Pay / СБП / РФ карта", url="https://pay.cloudtips.ru/p/2a436b20"),
        InlineKeyboardButton("Telegram Stars / Любая карта", url="https://t.me/your_stars_link"),
        InlineKeyboardButton("Crypto", url="https://t.me/your_crypto_link")
    )
    bot.send_message(
        chat_id,
        "Если вам нравятся наши продукты автоматизации, и вы цените, что мы для вас стараемся — будем рады вашей поддержке в виде доната 💙",
        reply_markup=markup_main
    )

    # Сообщение с выбором суммы
    markup_sum = InlineKeyboardMarkup(row_width=3)
    markup_sum.add(
        InlineKeyboardButton("⭐ 10", callback_data="donate_10"),
        InlineKeyboardButton("⭐ 50", callback_data="donate_50"),
        InlineKeyboardButton("⭐ 100", callback_data="donate_100"),
        InlineKeyboardButton("⭐ 200", callback_data="donate_200"),
        InlineKeyboardButton("⭐ 500", callback_data="donate_500"),
        InlineKeyboardButton("⭐ 1000", callback_data="donate_1000"),
        InlineKeyboardButton("⭐ 10000", callback_data="donate_10000")
    )
    bot.send_message(
        chat_id,
        "Какая сумма заставит нас танцевать от счастья прямо сейчас?",
        reply_markup=markup_sum
    )

    # Сообщение с быстрым донатом
    markup_pay = InlineKeyboardMarkup()
    markup_pay.add(InlineKeyboardButton("Заплатить ⭐ 50", url="https://pay.cloudtips.ru/p/2a436b20?amount=50"))
    bot.send_message(
        chat_id,
        "🍩 В качестве благодарности!!! Просто возьми мои деньги!!!",
        reply_markup=markup_pay
    )

# Обработка нажатий на суммы (можно добавить переход на нужную ссылку)
@bot.callback_query_handler(func=lambda call: call.data.startswith("donate_"))
def handle_donate_amount(call):
    amount = call.data.split("_")[1]
    pay_url = f"https://pay.cloudtips.ru/p/2a436b20?amount={amount}"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"Заплатить ⭐ {amount}", url=pay_url))
    bot.send_message(
        call.message.chat.id,
        f"Спасибо за поддержку! Для оплаты {amount} рублей нажмите кнопку ниже 👇",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['rocket'])
@subscription_guard
def handle_rocket(message):
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(0.5)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Ещё больше автоматизации", url=ROCKET_URL))
    bot.send_message(
        message.chat.id,
        "🚀 Для ещё большей автоматизации переходите в @rocketcontentbot",
        reply_markup=markup
    )
    increment_message_count(message.chat.id)

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
        bot.send_message(chat_id, "⚠️ Не удалось сделать рерайт")

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

    threading.Thread(target=process_download, args=(chat_id, url)).start()

if __name__ == '__main__':
    print("Бот запущен")
    bot.infinity_polling()
