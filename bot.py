
import os
import uuid
import sqlite3
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, Update,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.getenv("BOT_TOKEN")
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
SECRET = os.getenv("WEBHOOK_SECRET", "laneofers_secret_2026")

if not TOKEN or not HOST:
    raise RuntimeError("Нужны BOT_TOKEN и RENDER_EXTERNAL_HOSTNAME")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB = sqlite3.connect("offers.db")
DB.execute("""
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    owner INTEGER,
    link TEXT,
    price TEXT,
    currency TEXT,
    expires TEXT,
    status TEXT,
    recipient INTEGER
)
""")
DB.commit()


class OfferForm(StatesGroup):
    link = State()
    price = State()
    currency = State()
    hours = State()


def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать предложение", callback_data="create")],
        [InlineKeyboardButton(text="Мои предложения", callback_data="my")],
        [InlineKeyboardButton(text="Инструкция", callback_data="help")]
    ])


def offer_buttons(oid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Принять", callback_data=f"accept:{oid}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}")
    ]])


def review_buttons(oid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Подтвердить вручную",
            callback_data=f"complete:{oid}"
        )
    ]])


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    args = message.text.split(maxsplit=1)

    if len(args) > 1 and args[1].startswith("offer_"):
        oid = args[1][6:]
        row = DB.execute(
            "SELECT * FROM offers WHERE id=?", (oid,)
        ).fetchone()

        if not row:
            await message.answer("Предложение не найдено.")
            return

        if datetime.fromisoformat(row[5]) <= datetime.utcnow():
            DB.execute(
                "UPDATE offers SET status='Истёк' WHERE id=?",
                (oid,)
            )
            DB.commit()
            await message.answer("Срок предложения истёк.")
            return

        if row[6] != "Ожидает ответа":
            await message.answer("Предложение уже недоступно.")
            return

        if message.from_user.id == row[1]:
            await message.answer("Это твоё собственное предложение.")
            return

        await message.answer(
            "ПОЛЬЗОВАТЕЛЬСКОЕ ПРЕДЛОЖЕНИЕ\n\n"
            f"Ссылка: {row[2]}\n"
            f"Цена: {row[3]} {row[4]}\n\n"
            "Это не официальный перевод Telegram. "
            "Бот не переводит средства автоматически.",
            reply_markup=offer_buttons(oid)
        )
        return

    await message.answer(
        "Добро пожаловать в Laneofers!\n"
        "Создавай и отслеживай предложения.",
        reply_markup=menu()
    )


@dp.callback_query(F.data == "create")
async def create(call: CallbackQuery, state: FSMContext):
    await state.set_state(OfferForm.link)
    await call.message.answer("Отправь ссылку на подарок:")
    await call.answer()


@dp.message(OfferForm.link)
async def get_link(message: Message, state: FSMContext):
    link = message.text or ""

    if not link.startswith(("https://", "http://")):
        await message.answer("Нужна ссылка, начинающаяся с https://")
        return

    await state.update_data(link=link)
    await state.set_state(OfferForm.price)
    await message.answer("Укажи цену предложения:")


@dp.message(OfferForm.price)
async def get_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи положительное число.")
        return

    await state.update_data(price=price)
    await state.set_state(OfferForm.currency)

    await message.answer(
        "Выбери валюту:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Stars", callback_data="currency:Stars")],
            [InlineKeyboardButton(text="GRAM", callback_data="currency:GRAM")]
        ])
    )


@dp.callback_query(OfferForm.currency, F.data.startswith("currency:"))
async def get_currency(call: CallbackQuery, state: FSMContext):
    await state.update_data(currency=call.data.split(":")[1])
    await state.set_state(OfferForm.hours)
    await call.message.answer("На сколько часов действует предложение? (1–168)")
    await call.answer()


@dp.message(OfferForm.hours)
async def get_hours(message: Message, state: FSMContext):
    try:
        hours = int(message.text)
        if not 1 <= hours <= 168:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи число от 1 до 168.")
        return

    data = await state.get_data()
    oid = uuid.uuid4().hex[:10]
    expires = datetime.utcnow() + timedelta(hours=hours)

    DB.execute(
        """INSERT INTO offers
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            oid, message.from_user.id, data["link"],
            str(data["price"]), data["currency"],
            expires.isoformat(), "Ожидает ответа", None
        )
    )
    DB.commit()

    link = f"https://t.me/Laneofers_bot?start=offer_{oid}"

    await message.answer(
        "Предложение создано!\n\n"
        f"Номер: {oid}\n"
        f"Цена: {data['price']} {data['currency']}\n"
        f"Срок: {hours} ч.\n"
        "Статус: Ожидает ответа\n\n"
        f"Ссылка для получателя:\n{link}",
        reply_markup=menu()
    )
    await state.clear()


@dp.callback_query(F.data.startswith("accept:"))
async def accept(call: CallbackQuery):
    oid = call.data.split(":")[1]
    row = DB.execute(
        "SELECT * FROM offers WHERE id=?", (oid,)
    ).fetchone()

    if not row or row[6] != "Ожидает ответа":
        await call.answer("Предложение недоступно.", show_alert=True)
        return

    if datetime.fromisoformat(row[5]) <= datetime.utcnow():
        DB.execute("UPDATE offers SET status='Истёк' WHERE id=?", (oid,))
        DB.commit()
        await call.answer("Срок истёк.", show_alert=True)
        return

    if call.from_user.id == row[1]:
        await call.answer("Нельзя принять своё предложение.", show_alert=True)
        return

    DB.execute(
        "UPDATE offers SET status='Ожидает отправки', recipient=? WHERE id=?",
        (call.from_user.id, oid)
    )
    DB.commit()

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "Предложение принято!\n\n"
        f"Ссылка: {row[2]}\n\n"
        "Если подарок действительно отправлен, "
        f"напиши /sent {oid}.\n"
        "Не отправляй ничего, пока не проверишь условия."
    )

    await bot.send_message(
        row[1], f"Предложение {oid} принято. Ожидается отправка подарка."
    )
    await call.answer("Принято!")


@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    oid = call.data.split(":")[1]
    row = DB.execute(
        "SELECT * FROM offers WHERE id=?", (oid,)
    ).fetchone()

    if not row or row[6] != "Ожидает ответа":
        await call.answer("Предложение недоступно.", show_alert=True)
        return

    if call.from_user.id == row[1]:
        await call.answer("Нельзя отклонить своё предложение.", show_alert=True)
        return

    DB.execute(
        "UPDATE offers SET status='Отклонён' WHERE id=?", (oid,)
    )
    DB.commit()

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Предложение отклонено.")
    await bot.send_message(row[1], f"Предложение {oid} отклонено.")
    await call.answer("Отклонено.")


@dp.message(Command("sent"))
async def sent(message: Message):
    parts = message.text.split()

    if len(parts) != 2:
        await message.answer("Использование: /sent ID")
        return

    oid = parts[1]
    row = DB.execute(
        "SELECT * FROM offers WHERE id=?", (oid,)
    ).fetchone()

    if not row or row[7] != message.from_user.id:
        await message.answer("Предложение не найдено.")
        return

    if row[6] != "Ожидает отправки":
        await message.answer("Неверный статус предложения.")
        return

    DB.execute(
        "UPDATE offers SET status='Ожидает проверки' WHERE id=?", (oid,)
    )
    DB.commit()

    await bot.send_message(
        row[1],
        f"Получатель сообщил об отправке подарка по предложению {oid}.\n"
        "Проверь информацию самостоятельно.",
        reply_markup=review_buttons(oid)
    )
    await message.answer("Сообщение отправлено создателю на проверку.")


@dp.callback_query(F.data.startswith("complete:"))
async def complete(call: CallbackQuery):
    oid = call.data.split(":")[1]
    row = DB.execute(
        "SELECT * FROM offers WHERE id=?", (oid,)
    ).fetchone()

    if not row or row[1] != call.from_user.id:
        await call.answer("Только создатель может подтвердить.", show_alert=True)
        return

    if row[6] != "Ожидает проверки":
        await call.answer("Неверный статус.", show_alert=True)
        return

    DB.execute(
        "UPDATE offers SET status='Выполнен' WHERE id=?", (oid,)
    )
    DB.commit()

    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(
        row[7], f"Предложение {oid} отмечено создателем как выполненное."
    )
    await call.answer("Статус обновлён.")


@dp.callback_query(F.data == "my")
async def my_offers(call: CallbackQuery):
    rows = DB.execute(
        """SELECT id, price, currency, status FROM offers
        WHERE owner=? ORDER BY rowid DESC LIMIT 15""",
        (call.from_user.id,)
    ).fetchall()

    if not rows:
        await call.message.answer("Предложений пока нет.")
    else:
        text = "Твои предложения:\n\n"
        for r in rows:
            text += f"ID: {r[0]}\nЦена: {r[1]} {r[2]}\nСтатус: {r[3]}\n\n"
        await call.message.answer(text)

    await call.answer()


@dp.callback_query(F.data == "help")
async def help_menu(call: CallbackQuery):
    await call.message.answer(
        "Инструкция:\n\n"
        "1. Создай предложение.\n"
        "2. Укажи ссылку, цену и валюту.\n"
        "3. Отправь ссылку получателю.\n"
        "4. Получатель принимает или отклоняет.\n"
        "5. После отправки подарка он сообщает об этом.\n"
        "6. Создатель вручную проверяет и меняет статус.\n\n"
        "Автоматических переводов денег нет."
    )
    await call.answer()


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Создание отменено.", reply_markup=menu())


async def webhook_handler(request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
        raise web.HTTPForbidden()

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return web.Response(text="OK")


async def health(request):
    return web.Response(text="Laneofers is running")


async def on_startup(app):
    await bot.set_webhook(
        url=f"https://{HOST}/webhook",
        secret_token=SECRET
    )


async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


app = web.Application()
app.router.add_get("/", health)
app.router.add_post("/webhook", webhook_handler)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000))
    )
