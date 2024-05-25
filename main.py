import asyncio
import logging
import os
import re
import subprocess
from typing import List

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Defaults

ENV = {}
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            key, value = line.strip().split("=")
            ENV[key] = value
else:
    ENV = os.environ

ALLOWED_USERS = ENV.get("ALLOWED_USERS", "").split(",")
ALLOWED_USERS = [int(user_id) for user_id in ALLOWED_USERS]
DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=ENV.get("TG_LOG_LEVEL", "INFO"))


async def reply_with_long_text(update: Update, text: str, **kwargs):
    MAX_LENGTH = 200
    for i in list(range(0, len(text), MAX_LENGTH))[:2]:
        await update.message.reply_text(text[i: i + MAX_LENGTH], **kwargs)


async def handle_command_output(update: Update, proc, name):
    try:
        # wait for the download to complete
        stdout, stderr = await proc.communicate()
        if stderr:
            await reply_with_long_text(
                update,
                f"Error: {stderr.decode()}",
                reply_to_message_id=update.message.message_id,
            )
        await reply_with_long_text(
            update,
            f"Output: {stdout.decode()}",
            reply_to_message_id=update.message.message_id,
        )
    except BaseException as e:
        print(e)
        await update.message.reply_text(
            f"{name} completed.",
            reply_to_message_id=update.message.message_id,
        )


# wrapper for permission check:
def permission_check(func):
    async def wrapper(update: Update, context: ContextTypes):
        user_id = update.message.from_user.id
        if user_id not in ALLOWED_USERS:
            await update.message.reply_text(
                "You are not allowed to use this bot.",
                reply_to_message_id=update.message.message_id,
            )
            return
        return await func(update, context)

    return wrapper


download_link_locks = {}


@permission_check
async def download(update: Update, context: ContextTypes):
    # check if magnet link, this ensures no command injection
    magnet_link = context.args[0]
    if not re.match(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]*", magnet_link):
        await update.message.reply_text(
            "Invalid magnet link.", reply_to_message_id=update.message.message_id
        )
        return

    if magnet_link in download_link_locks:
        await update.message.reply_text(
            "Already downloading.", reply_to_message_id=update.message.message_id
        )

    try:
        # download the torrent with aria2c
        proc = await asyncio.create_subprocess_exec(
            "aria2c",
            "-x",
            "16",
            "-s",
            "16",
            f"--dir={DOWNLOAD_DIR}",
            "--conf-path",
            "./aria2.conf",
            "--seed-time=0",
            "--summary-interval=0",
            "--disable-ipv6",
            magnet_link,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        await update.message.reply_markdown_v2(
            f"Downloading torrent from magnet link: `{magnet_link}`",
            reply_to_message_id=update.message.message_id,
        )
        await handle_command_output(update, proc, "Download")
        del download_link_locks[magnet_link]
    except BaseException as e:
        print(e)
        await update.message.reply_text(
            "Download failed.", reply_to_message_id=update.message.message_id
        )
        del download_link_locks[magnet_link]


def find_all(cur_path: str, ls_all: bool) -> List[str]:
    res = []
    for folder in os.listdir(cur_path):
        if os.path.isdir(os.path.join(cur_path, folder)):
            res += find_all(os.path.join(cur_path, folder), ls_all)

    for file in os.listdir(cur_path):
        if not os.path.isdir(os.path.join(cur_path, file)):
            res.append(os.path.join(cur_path, file))

    return res


@permission_check
async def ls(update: Update, context: ContextTypes):
    ls_all = context.args and context.args[0] == "all"
    files = find_all(DOWNLOAD_DIR, ls_all)
    files.sort(
        key=lambda x: os.path.getmtime(x), reverse=True
    )
    if not ls_all:
        files = files[:10]
    return_msg = (
        "Files in downloads: (latest 10, all?)\n"
        if not ls_all
        else "All files in downloads:\n"
    )
    for file in files:
        return_msg += f"{file}\n"
    await update.message.reply_text(
        return_msg,
        reply_to_message_id=update.message.message_id,
    )


@permission_check
async def rm(update: Update, context: ContextTypes):
    file_name = " ".join(context.args)
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    if not os.path.exists(file_path):
        await update.message.reply_text(
            f"File not found: {file_name}",
            reply_to_message_id=update.message.message_id,
        )
        return
    os.remove(file_path)
    await update.message.reply_text(
        f"File deleted: {file_name}",
        reply_to_message_id=update.message.message_id,
    )


@permission_check
async def send_file(update: Update, context: ContextTypes):
    file_name = " ".join(context.args)
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    if not os.path.exists(file_path):
        await update.message.reply_text(
            f"File not found: {file_name}",
            reply_to_message_id=update.message.message_id,
        )
        return
    try:
        await update.message.reply_document(
            open(file_path, "rb"),
            caption=f"File: {file_name}",
            reply_to_message_id=update.message.message_id,
            read_timeout=6000,
            connect_timeout=6000,
            write_timeout=6000,
        )
    except BaseException as e:
        print(e)
        await update.message.reply_text(
            f"Error sending file: {file_name}",
            reply_to_message_id=update.message.message_id,
        )


compress_filename_locks = {}


@permission_check
async def compress(update: Update, context: ContextTypes):
    file_name = " ".join(context.args)

    if file_name in compress_filename_locks:
        await update.message.reply_text(
            f"File is already being compressed: {file_name}",
            reply_to_message_id=update.message.message_id,
        )
        return

    compress_filename_locks[file_name] = True
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    compressed_file_path = os.path.join(DOWNLOAD_DIR, f"compressed_{file_name}")
    if not os.path.exists(file_path):
        await update.message.reply_text(
            f"File not found: {file_name}",
            reply_to_message_id=update.message.message_id,
        )
        return

    if os.path.exists(compressed_file_path):
        await update.message.reply_text(
            f"Compressed file already exists: {compressed_file_path}",
            reply_to_message_id=update.message.message_id,
        )
        del compress_filename_locks[file_name]
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            file_path,
            "-vf",
            "scale=1080:-1",
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "veryfast",
            compressed_file_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        await update.message.reply_markdown_v2(
            f"Compressing file: `{file_name}`",
            reply_to_message_id=update.message.message_id,
        )

        # wait for the compression to complete
        stdout, stderr = await proc.communicate()
        del compress_filename_locks[file_name]

        await handle_command_output(update, proc, "Compress")
    except BaseException as e:
        print(e)
        await update.message.reply_text(
            f"Error compressing file: {file_name}",
            reply_to_message_id=update.message.message_id,
        )
        del compress_filename_locks[file_name]


handlers = [
    CommandHandler(command="download", callback=download),
    CommandHandler(command="ls", callback=ls),
    CommandHandler(command="rm", callback=rm),
    CommandHandler(command="send", callback=send_file),
    CommandHandler(command="compress", callback=compress),
]

application = (
    ApplicationBuilder()
    .token(ENV.get("TELEGRAM_BOT_TOKEN", ""))
    .defaults(Defaults(block=False))
    .build()
)
for handler in handlers:
    application.add_handler(handler)
application.run_polling()
