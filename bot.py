
import asyncio
import os
import sqlite3
import uuid
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = "Laneofers_bot"

if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db = sqlite3.connect("offers.db")
db.execute("""
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    owner INTEGER,
    link TEXT,
    price TEXT,
    currency TEXT,
    hours INTEGER,
    expires TEXT,
    status TEXT,
    recipient INTEGER
)
""")
db.commit()


class CreateOffer(StatesGroup):
    link = State()
    price = State()
    currency = State()
    hours = State()


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать оффер", callback_data="create")],
        [InlineKeyboardButton(text="Мои офферы", callback_data="my")],
        [InlineKeyboardButton(text="Инструкция", callback_data="help")]
    ])


def offer_keyboard(offer_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Принять",
                callback_data=f"accept:{offer_id}"
            ),
            InlineKeyboardButton(
                text="Отклонить",
                callback_data=f"reject:{offer_id}"
            )
        ]
    ])


@dp.message(CommandStart())
async def start(message: Message):
    args = message.text.split(maxsplit=1)

    if len(args) > 1 and args[1].startswith("offer_"):
        offer_id = args[1][6:]
        offer = db.execute(
            "SELECT * FROM offers WHERE id=?", (offer_id,)
        ).fetchone()

        if not offer:
            await message.answer("Оффер не найден.")
            return

        if datetime.fromisoformat(offer[6]) < datetime.now():
            await message.answer("Срок действия оффера истёк.")
            db.execute(
                "UPDATE offers SET status='Истёк' WHERE id=?",
                (offer_id,)
            )
            db.commit()
            return

        if offer[7] != "Ожидает ответа":
            await message.answer("Этот оффер уже недоступен.")
            return

        await message.answer(
            f"Новый оффер!\n\n"
            f"Подарок: {offer[2]}\n"
            f"Цена: {offer[3]} {offer[4]}\n"
            f"Срок: {offer[5]} ч.\n\n"
            f"После принятия отправка подарка и проверка "
            f"выполняются вручную.",
            reply_markup=offer_keyboard(offer_id)
        )
        return

    await message.answer(
        "Добро пожаловать в Laneofers!\n"
        "Создавай предложения и отслеживай их статус.",
        reply_markup=main_menu()
    )


@dp.callback_query(F.data == "create")
async def create(call: CallbackQuery, state: FSMContext):
    await state.set_state(CreateOffer.link)
    await call.message.answer(
        "Отправь ссылку на подарок:"
    )
    await call.answer()


@dp.message(CreateOffer.link)
async def get_link(message: Message, state: FSMContext):
    if not message.text or not message.text.startswith("http"):
        await message.answer("Отправь корректную ссылку.")
        return

    await state.update_data(link=message.text)
    await state.set_state(CreateOffer.price)
    await message.answer("Укажи цену оффера:")


@dp.message(CreateOffer.price)
async def get_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи положительное число.")
        return

    await state.update_data(price=price)
    await state.set_state(CreateOffer.currency)

    await message.answer(
        "Выбери валюту:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Stars", callback_data="cur:Stars")],
            [InlineKeyboardButton(text="GRAM", callback_data="cur:GRAM")]
        ])
    )


@dp.callback_query(CreateOffer.currency, F.data.startswith("cur:"))
async def get_currency(call: CallbackQuery, state: FSMContext):
    await state.update_data(currency=call.data.split(":")[1])
    await state.set_state(CreateOffer.hours)
    await call.message.answer("На сколько часов действует оффер?")
    await call.answer()


@dp.message(CreateOffer.hours)
async def get_hours(message: Message, state: FSMContext):
    try:
        hours = int(message.text)
        if not 1 <= hours <= 168:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи число от 1 до 168.")
        return

    data = await state.get_data()
    offer_id = uuid.uuid4().hex[:10]
    expires = datetime.now() + timedelta(hours=hours)

    db.execute("""
        INSERT INTO offers
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        offer_id, message.from_user.id,
        data["link"], str(data["price"]),
        data["currency"], hours,
        expires.isoformat(), "Ожидает ответа", None
    ))
    db.commit()

    share_link = f"https://t.me/{BOT_USERNAME}?start=offer_{offer_id}"

    await message.answer(
        "Оффер создан!\n\n"
        f"Номер: {offer_id}\n"
        f"Цена: {data['price']} {data['currency']}\n"
        f"Срок: {hours} ч.\n"
        "Статус: Ожидает ответа\n\n"
        "Отправь эту ссылку получателю:\n"
        f"{share_link}",
        reply_markup=main_menu()
    )
    await state.clear()


@dp.callback_query(F.data.startswith("accept:"))
async def accept(call: CallbackQuery):
    offer_id = call.data.split(":")[1]
    offer = db.execute(
        "SELECT * FROM offers WHERE id=?", (offer_id,)
    ).fetchone()

    if not offer or offer[7] != "Ожидает ответа":
        await call.answer("Оффер уже недоступен.", show_alert=True)
        return

    if datetime.fromisoformat(offer[6]) < datetime.now():
        db.execute(
            "UPDATE offers SET status='Истёк' WHERE id=?",
            (offer_id,)
        )
        db.commit()
        await call.answer("Срок оффера истёк.", show_alert=True)
        return

    if call.from_user.id == offer[1]:
        await call.answer("Нельзя принять собственный оффер.", show_alert=True)
        return

    db.execute("""
        UPDATE offers
        SET status='Ожидает отправки', recipient=?
        WHERE id=?
    """, (call.from_user.id, offer_id))
    db.commit()

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "Ты принял оффер!\n\n"
        f"Ссылка на подарок: {offer[2]}\n"
        "После отправки подарка сообщи об этом создателю. "
        "Выполнение будет проверено вручную."
    )

    await bot.send_message(
        offer[1],
        f"Оффер {offer_id} принят!\n"
        "Статус: Ожидает отправки."
    )
    await call.answer("Оффер принят!")


@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    offer_id = call.data.split(":")[1]
    offer = db.execute(
        "SELECT * FROM offers WHERE id=?", (offer_id,)
    ).fetchone()

    if not offer or offer[7] != "Ожидает ответа":
        await call.answer("Оффер уже недоступен.", show_alert=True)
        return

    if call.from_user.id == offer[1]:
        await call.answer("Нельзя отклонить собственный оффер.", show_alert=True)
        return

    db.execute(
        "UPDATE offers SET status='Отклонён' WHERE id=?",
        (offer_id,)
    )
    db.commit()

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Ты отклонил оффер.")
    await bot.send_message(
        offer[1], f"Оффер {offer_id} отклонён."
    )
    await call.answer("Оффер отклонён.")


@dp.callback_query(F.data == "my")
async def my_offers(call: CallbackQuery):
    rows = db.execute(
        "SELECT id, price, currency, status FROM offers "
        "WHERE owner=? ORDER BY rowid DESC LIMIT 15",
        (call.from_user.id,)
    ).fetchall()

    if not rows:
        await call.message.answer("У тебя пока нет офферов.")
    else:
        text = "Твои офферы:\n\n"
        for row in rows:
            text += (
                f"ID: {row[0]}\n"
                f"Цена: {row[1]} {row[2]}\n"
                f"Статус: {row[3]}\n\n"
            )
        await call.message.answer(text)

    await call.answer()


@dp.callback_query(F.data == "help")
async def help_menu(call: CallbackQuery):
    await call.message.answer(
        "Инструкция:\n\n"
        "1. Создай оффер.\n"
        "2. Укажи ссылку, цену и валюту.\n"
        "3. Установи срок действия.\n"
        "4. Отправь получателю ссылку на оффер.\n"
        "5. Получатель принимает или отклоняет предложение.\n"
        "6. Отправка подарка и проверка выполняются вручную.\n\n"
        "Бот не переводит Stars или GRAM."
    )
    await call.answer()


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Создание отменено.", reply_markup=main_menu())


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
