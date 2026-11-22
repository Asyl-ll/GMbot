import functools
import hashlib
import html
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import db
import helpers
from digest import build_digest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reminder-bot")

TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
CRON_SECRET = os.environ["CRON_SECRET"]
BASE_URL = (os.environ.get("BASE_URL") or os.environ["RENDER_EXTERNAL_URL"]).rstrip("/")
MORNING_HOUR = int(os.environ.get("MORNING_HOUR", "8"))
WEBHOOK_SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()[:40]

HELP = (
    "Я бот-напоминалка.\n\n"
    "Чтобы добавить задачу, просто напиши дату и текст:\n"
    "• завтра сдать отчёт\n"
    "• 25.11 купить билеты\n"
    "• пт встреча с куратором\n"
    "• +3 позвонить маме (через 3 дня)\n\n"
    "Команды:\n"
    "/today — план на сегодня\n"
    "/list — все открытые задачи\n"
    "/done 5 — отметить задачу #5 выполненной\n"
    "/del 5 — удалить задачу #5\n"
    "/morning — прислать утреннюю сводку сейчас\n\n"
    "Каждое утро в 8:00 я пришлю погоду в Астане и твои планы."
)

ptb = Application.builder().token(TOKEN).updater(None).build()


def owner_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user and OWNER_ID and user.id == OWNER_ID:
            return await fn(update, ctx)
        if user and not OWNER_ID:
            await update.effective_message.reply_text(
                f"Твой Telegram id: {user.id}\n"
                "Добавь его в переменную OWNER_ID на Render, сервис перезапустится."
            )
        return None

    return wrapper


async def _add(update: Update, text: str) -> None:
    parsed = helpers.parse_task(text, helpers.today_local())
    if not parsed:
        await update.message.reply_text(
            "Не понял дату. Напиши так: «завтра сдать отчёт», «25.11 купить билеты», «пт встреча»."
        )
        return
    due, task = parsed
    task_id = await db.add_task(due, task)
    await update.message.reply_text(f"✅ Добавил #{task_id} на {helpers.fmt_date(due)}:\n{task}")


def _task_id(ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not ctx.args:
        return None
    s = ctx.args[0].lstrip("#")
    return int(s) if s.isdigit() else None


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


@owner_only
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _add(update, " ".join(ctx.args))


@owner_only
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _add(update, update.message.text)


@owner_only
async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = await build_digest(helpers.today_local(), with_weather=False)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_morning(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = await build_digest(helpers.today_local())
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = await db.open_tasks()
    if not rows:
        await update.message.reply_text("Список пуст 🎉")
        return
    today = helpers.today_local()
    lines, current = [], None
    for r in rows:
        if r["due"] != current:
            current = r["due"]
            mark = " ⚠️ просрочено" if current < today else ""
            lines.append(f"\n<b>{helpers.fmt_date(current)}</b>{mark}")
        lines.append(f"#{r['id']} {html.escape(r['text'])}")
    await update.message.reply_text("\n".join(lines).strip(), parse_mode=ParseMode.HTML)


@owner_only
async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    task_id = _task_id(ctx)
    if task_id is None:
        await update.message.reply_text("Напиши номер задачи: /done 5")
        return
    ok = await db.finish(task_id)
    await update.message.reply_text(f"✔️ Задача #{task_id} выполнена" if ok else "Такой открытой задачи нет.")


@owner_only
async def cmd_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    task_id = _task_id(ctx)
    if task_id is None:
        await update.message.reply_text("Напиши номер задачи: /del 5")
        return
    ok = await db.remove(task_id)
    await update.message.reply_text(f"🗑 Задача #{task_id} удалена" if ok else "Такой задачи нет.")


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Ошибка в обработчике", exc_info=ctx.error)


ptb.add_handler(CommandHandler(["start", "help"], cmd_start))
ptb.add_handler(CommandHandler("add", cmd_add))
ptb.add_handler(CommandHandler("today", cmd_today))
ptb.add_handler(CommandHandler("morning", cmd_morning))
ptb.add_handler(CommandHandler("list", cmd_list))
ptb.add_handler(CommandHandler("done", cmd_done))
ptb.add_handler(CommandHandler("del", cmd_del))
ptb.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
ptb.add_error_handler(on_error)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_db()
    await ptb.initialize()
    await ptb.start()
    await ptb.bot.set_my_commands(
        [
            BotCommand("today", "План на сегодня"),
            BotCommand("list", "Все задачи"),
            BotCommand("done", "Отметить выполненной"),
            BotCommand("del", "Удалить задачу"),
            BotCommand("morning", "Утренняя сводка сейчас"),
            BotCommand("help", "Помощь"),
        ]
    )
    # Вебхук НЕ удаляем при остановке: Telegram должен будить спящий сервис сообщениями.
    await ptb.bot.set_webhook(
        url=f"{BASE_URL}/webhook", secret_token=WEBHOOK_SECRET, allowed_updates=["message"]
    )
    log.info("Бот запущен, вебхук: %s/webhook", BASE_URL)
    yield
    await ptb.stop()
    await ptb.shutdown()


api = FastAPI(lifespan=lifespan)


@api.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"ok": True}


@api.post("/webhook")
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403)
    update = Update.de_json(await request.json(), ptb.bot)
    await ptb.update_queue.put(update)
    return Response(status_code=200)


@api.api_route("/tick", methods=["GET", "HEAD"])
async def tick(key: str = "", force: int = 0):
    """Дёргается внешним cron каждые ~10 минут: будит сервис и шлёт утреннюю сводку."""
    if not secrets.compare_digest(key, CRON_SECRET):
        raise HTTPException(status_code=403)
    if not OWNER_ID:
        return {"sent": False, "reason": "OWNER_ID не задан"}

    now = helpers.now_local()
    if not force:
        if not (MORNING_HOUR <= now.hour < MORNING_HOUR + 4):
            return {"sent": False, "reason": "вне окна отправки"}
        if not await db.claim_digest(now.date().isoformat()):
            return {"sent": False, "reason": "сегодня уже отправлено"}
    try:
        text = await build_digest(now.date())
        await ptb.bot.send_message(OWNER_ID, text, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Не удалось отправить сводку")
        if not force:
            await db.release_digest()
        raise HTTPException(status_code=500)
    return {"sent": True}
