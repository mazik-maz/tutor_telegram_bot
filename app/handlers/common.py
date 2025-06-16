"""
Общие обработчики:
  • /start  – выбор роли и регистрация
  • /add_tutor – админ добавляет Telegram-ID репетитора
  • /register  – устаревший способ по коду (оставлен для совместимости)
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message
from sqlalchemy import select

from app.config import settings
from app.keyboards.menus import TUTOR_MENU, STUDENT_MENU, ROLE_CHOICE_KB
from app.models.models import User

router = Router()


# ───────────────────────  FSM для /start  ─────────────────────── #
class StartFSM(StatesGroup):
    waiting_role = State()
    waiting_code = State()


# ───────────────────────────  /start  ─────────────────────────── #
@router.message(CommandStart())
async def start_cmd(msg: Message, session, state: FSMContext):
    tg_id = msg.from_user.id
    user: User | None = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar()

    # Повторный визит
    if user:
        if user.is_tutor:
            await msg.answer(
                "С возвращением, репетитор!\n"
                "Используйте меню для работы.\n"
                "Если нужна помощь — @mazik_il",
                reply_markup=TUTOR_MENU,
            )
        else:
            await msg.answer(
                "С возвращением, ученик!\n"
                "Ваш репетитор сообщит о занятиях.",
                reply_markup=STUDENT_MENU,
            )
        return

    # Новый пользователь → спросить роль
    await msg.answer("Добро пожаловать! Кто вы?", reply_markup=ROLE_CHOICE_KB)
    await state.set_state(StartFSM.waiting_role)


# ╭───────────  регистрация ученика  ───────────╮
@router.message(StartFSM.waiting_role, F.text.casefold() == "я ученик")
async def register_student(msg: Message, session, state: FSMContext):
    student = User(
        telegram_id=msg.from_user.id,
        full_name=msg.from_user.full_name,
        is_tutor=False,
    )
    session.add(student)
    await msg.answer(
        "Вы зарегистрированы как ученик.\n"
        "Ожидайте приглашения от репетитора.",
        reply_markup=STUDENT_MENU,
    )
    await state.clear()


# ╭───────────  выбор «я репетитор»  ───────────╮
@router.message(StartFSM.waiting_role, F.text.casefold() == "я репетитор")
async def tutor_role_chosen(msg: Message, state: FSMContext):
    tg_id = msg.from_user.id
    allowed = [int(x) for x in (settings.ALLOWED_TUTOR_IDS or "").split(",") if x]

    # Админ или заранее разрешённый ID → сразу регистрируем
    if tg_id == settings.ADMIN_ID or tg_id in allowed:
        await complete_tutor_registration(msg, session=None)  # session получим позже
        # session=None здесь лишь означает, что мы возьмём новую в функции
        return

    # Иначе запросим код
    await msg.answer("Введите код доступа репетитора:")
    await state.set_state(StartFSM.waiting_code)


# ╭───────────  проверка кода  ───────────╮
@router.message(StartFSM.waiting_code)
async def tutor_code_check(msg: Message, session, state: FSMContext):
    if msg.text.strip() != (settings.TUTOR_REG_CODE or ""):
        await msg.answer("Код неверный. Обратитесь к администратору @mazik_il")
        return
    await complete_tutor_registration(msg, session)
    await state.clear()


# ╭───────────  финал регистрации репетитора  ───────────╮
async def complete_tutor_registration(msg: Message, session):
    """
    Создаёт (или помечает) пользователя как репетитора.
    Если session == None (при мгновенной регистрации через allowed-list),
    откроем новую сессию вручную.
    """
    own_session = False
    if session is None:
        from app.models.db import AsyncSessionLocal
        session = AsyncSessionLocal()
        own_session = True

    async with session:
        tg_id = msg.from_user.id
        tutor: User | None = (
            await session.execute(select(User).where(User.telegram_id == tg_id))
        ).scalar()

        if not tutor:
            tutor = User(
                telegram_id=tg_id,
                full_name=msg.from_user.full_name,
                is_tutor=True,
            )
            session.add(tutor)
        else:
            tutor.is_tutor = True

        await msg.answer(
            "Вы зарегистрированы как репетитор!\n"
            "Доступные функции находятся в меню.",
            reply_markup=TUTOR_MENU,
        )
        await session.commit()

    if own_session:
        await session.close()


# ───────────────────────  /add_tutor  (admin)  ─────────────────────── #
@router.message(Command("add_tutor"))
async def add_tutor_cmd(msg: Message, session):
    """Администратор добавляет любой Telegram-ID в роль репетитора."""
    if msg.from_user.id != settings.ADMIN_ID:
        return  # молча игнорируем

    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Использование: /add_tutor <telegram_id>")
        return

    tg_id = int(parts[1])
    user: User | None = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar()

    if user:
        user.is_tutor = True
    else:
        user = User(telegram_id=tg_id, full_name="", is_tutor=True)
        session.add(user)

    await msg.answer(f"ID {tg_id} теперь репетитор.")
    try:
        await msg.bot.send_message(
            tg_id,
            "Вы добавлены как репетитор администратором. Нажмите /start для завершения регистрации.",
        )
    except Exception:
        pass  # пользователь ещё не писал боту


# ─────────────────  устаревшая /register  (для совместимости) ───────────────── #
@router.message(Command("register"))
async def tutor_register_legacy(msg: Message, session):
    tg_id = msg.from_user.id
    user: User | None = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar()

    if user and user.is_tutor:
        await msg.answer("Вы уже зарегистрированы как репетитор.")
        return

    parts = msg.text.split(maxsplit=1)
    code = parts[1] if len(parts) > 1 else None
    allowed = [int(x) for x in (settings.ALLOWED_TUTOR_IDS or "").split(",") if x]

    if tg_id in allowed or (code and code == settings.TUTOR_REG_CODE):
        if not user:
            user = User(telegram_id=tg_id, full_name=msg.from_user.full_name)
            session.add(user)
        user.is_tutor = True
        await msg.answer("Регистрация репетитора успешна.", reply_markup=TUTOR_MENU)
    else:
        await msg.answer("Неверный код. Обратитесь @mazik_il")
