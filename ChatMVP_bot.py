#!/usr/bin/env python
import logging
import os
from bisect import bisect_left
from collections import namedtuple
import random
import yaml

from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import ChatMVP

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.WARNING
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


telegram_bot_token = None
my_chat_id = None

last_shown_message = None
job_name = "check_new_posts"
all_posts = {}
post_message_ids = {}
message_id_to_post_num = {}


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    if update.effective_message.chat_id != my_chat_id:
        return
        
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    if update.effective_message.chat_id != my_chat_id:
        return
        
    await update.message.reply_text("Help!")


async def post_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    if update.effective_message.chat_id != my_chat_id:
        return
    
    message_text = update.effective_message.text
    if message_text is None:
        message_text = update.effective_message.caption

    if message_text is None:
        message_text = ""

    if update.effective_message.reply_to_message:
        reply_id = update.effective_message.reply_to_message.message_id
        if reply_id in message_id_to_post_num and message_id_to_post_num[reply_id] in all_posts:
            post_num = message_id_to_post_num[reply_id]
            message_text = f">>{post_num}\n{message_text}"

    if update.effective_message.photo:
        photo_largest = update.effective_message.photo[-1]
        file = await photo_largest.get_file(read_timeout=5, write_timeout=5, connect_timeout=5, pool_timeout=5)
        file_bytes = await file.download_as_bytearray(read_timeout=5, write_timeout=5, connect_timeout=5, pool_timeout=5)
    else:
        file_bytes = None

    try:
        ChatMVP.create_post(message_text, file_bytes)
    except RuntimeError as e:
        await update.message.reply_text(f"Could not create a post: {e}")


def format_citation(citation: ChatMVP.Post):
    text = citation.comment.strip()
    if ChatMVP.bad_words.search(text):
        return '🚫'
    
    first_line = None
    for line in text.splitlines():
        if not line.strip():
            continue

        if ChatMVP.is_link(line) or line.startswith('>'):
            continue

        first_line = line
        break

    if first_line:
        return first_line[:40] + '…' * (len(first_line) > 40)
    
    if citation.pic_url:
        return '📎'
    
    return None


def is_link_to_previous_post(sorted_nums, link_num, post_num):
    index = bisect_left(sorted_nums, post_num) if post_num in all_posts else len(all_posts)
    if index > 0:
        prev_num = sorted_nums[index - 1]
        return link_num == prev_num
    
    return False


markdownv2_escape_chars = set("_*[]()~`>#+-=|{}.!")


def markdownv2_escape(text):
    chars = []
    for char in text:
        if char in markdownv2_escape_chars:
            chars.append(f"\\{char}")
        else:
            chars.append(char)

    return "".join(chars)


TgFormattedPost = namedtuple('TgFormattedPost', ['text', 'citation_msg_id', 'citation_text', 'pic_url'])


def format_post_for_tg(post: ChatMVP.Post, all_posts) -> TgFormattedPost:
    sorted_nums = list(sorted(all_posts.keys()))

    text = post.comment.strip()
    if ChatMVP.bad_words.search(text):
        return None
    
    text_processed = []
    citation = None
    citation_msg_id = None
    for line in text.splitlines():
        if not line.strip():
            continue

        if ChatMVP.is_link(line):
            if not text_processed and citation is None:
                num = int(line[2:])
                if num in all_posts:
                    citation = format_citation(all_posts[num])
                    if citation is not None:
                        citation = markdownv2_escape(citation)
                        if citation != '🚫' and is_link_to_previous_post(sorted_nums, num, post.num):
                            citation = None
                        else:
                            if num in post_message_ids:
                                citation_msg_id = post_message_ids[num]
            
            continue

        if line.startswith('>'):
            text_processed.append(">" + markdownv2_escape(line[1:]))
        else:
            text_processed.append(markdownv2_escape(line))

    text = "\n".join(text_processed)
    if not text and not post.pic_url:
        return None
    
    if text.startswith('>'):
        citation = None

    if citation_msg_id is None and citation is not None:
        text = f">{citation}\n{text}"

    return TgFormattedPost(text, citation_msg_id, citation, post.pic_url)


async def dump_posts(context: ContextTypes.DEFAULT_TYPE, posts: dict) -> None:
    max_posts = 50
    if len(posts) > max_posts:
        posts = dict(sorted(posts.items(), key=lambda item: item[0], reverse=True)[:max_posts])

    for num, post in sorted(posts.items(), key=lambda item: item[0], reverse=False):
        tg_post = format_post_for_tg(post, all_posts)
        try:
            if tg_post is not None:
                if tg_post.pic_url:
                    message = await context.bot.send_photo(my_chat_id, photo=tg_post.pic_url, caption=tg_post.text, parse_mode="MarkdownV2",
                                                        reply_to_message_id=tg_post.citation_msg_id, disable_notification=True)
                else:
                    message = await context.bot.send_message(my_chat_id, text=tg_post.text, parse_mode="MarkdownV2",
                                                            reply_to_message_id=tg_post.citation_msg_id, disable_web_page_preview=True, 
                                                            disable_notification=True)
                
                post_message_ids[num] = message.message_id
                message_id_to_post_num[message.message_id] = num
        except Exception as e:
            logger.error(f"Could not send message: {e}")

        global last_shown_message
        if last_shown_message is None or num > last_shown_message:
            last_shown_message = num


async def check_new_posts(context: ContextTypes.DEFAULT_TYPE) -> None:
    new_posts = ChatMVP.get_updates(last_shown_message)
    all_posts.update(new_posts)
    await dump_posts(context, new_posts)


async def startup_actions(context: ContextTypes.DEFAULT_TYPE) -> None:
    due = 5
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    if current_jobs:
        return False
            
    if last_shown_message is None:
        new_posts = ChatMVP.get_all_posts()
    else:
        new_posts = ChatMVP.get_updates(last_shown_message)

    all_posts.update(new_posts)

    await dump_posts(context, new_posts)
    context.job_queue.run_repeating(check_new_posts, due, chat_id=my_chat_id, name=job_name)
    return True


async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start watching the thread."""
    if update.effective_message.chat_id != my_chat_id:
        return
        
    if await startup_actions(context):
        await update.effective_message.reply_text("Monitoring started.")
    else:
        await update.effective_message.reply_text("Monitoring is already running.")


async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove the job if the user changed their mind."""
    if update.effective_message.chat_id != my_chat_id:
        return
        
    current_jobs = context.job_queue.get_jobs_by_name(job_name)

    if not current_jobs:
        await update.effective_message.reply_text("No monitoring is running.")
        return

    for job in current_jobs:
        job.schedule_removal()

    await update.effective_message.reply_text("Monitoring stopped.")


def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(telegram_bot_token).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("watch", start_monitoring))
    application.add_handler(CommandHandler("unwatch", stop_monitoring))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, post_received))

    application.job_queue.run_once(startup_actions, 0)

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # secure random seed
    random.seed(os.urandom(16))

    if not os.path.exists("settings.yaml"):
        raise RuntimeError("Please create settings.yaml file with your 2ch and Telegram settings.")

    # read user setting YAML file
    with open("settings.yaml", "r") as file:
        settings = yaml.safe_load(file)
    
    ChatMVP.passcode = settings["2ch"]["passcode"]
    ChatMVP.mvp_board = settings["2ch"]["board"]
    ChatMVP.mvp_thread_id = settings["2ch"]["thread"]

    telegram_bot_token = settings["telegram"]["bot_token"]
    my_chat_id = settings["telegram"]["chat_id"]

    main()
