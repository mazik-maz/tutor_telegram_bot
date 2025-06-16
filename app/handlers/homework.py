from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
)
from sqlalchemy import select
from app.keyboards.menus import TUTOR_MENU, STUDENT_MENU
from app.models.models import User, Homework
from app.config import settings
from typing import List
from datetime import datetime

router = Router()

# ────────────────────────  FSM  ──────────────────────── #
class HWCreate(StatesGroup):
    choosing_student = State()
    waiting_text = State()
    waiting_files = State()


class HWAnswer(StatesGroup):
    choosing_hw = State()
    waiting_reply = State()


# ──────────────────────── helpers ──────────────────────── #
def students_kb(students: List[User]) -> ReplyKeyboardMarkup:
    mas = []
    for s in students:
        mas.append([KeyboardButton(text=s.full_name)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb


def cancel_if_requested(msg: Message, state: FSMContext) -> bool:
    """Если пользователь нажал кнопку '↩️ Отмена' — выходим из FSM."""
    if msg.text and msg.text.strip().startswith("↩️"):
        state.clear()
        # Покажем нужное меню
        if "Репетитор" in msg.from_user.full_name or msg.from_user.id in map(
            int, (settings.ALLOWED_TUTOR_IDS or "").split(",")
        ):
            menu = TUTOR_MENU
        else:
            menu = STUDENT_MENU
        msg.answer("Операция отменена.", reply_markup=menu)
        return True
    return False


# ────────────────────────  GIVE HOMEWORK (tutor) ──────────────────────── #
@router.message(Command("give_homework"))
@router.message(F.text == "✏️ Задать ДЗ")
async def give_hw_start(msg: Message, session, state: FSMContext):
    """Начало диалога выдачи домашнего задания (для репетитора)."""
    tutor: User | None = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только репетитору.")
        return
    # Явно получаем список учеников данного репетитора
    students = (await session.execute(
        select(User).where(User.tutor_id == tutor.id, User.is_tutor.is_(False)))
    ).scalars().all()
    if not students:
        await msg.answer("У вас пока нет учеников.")
        return
    await msg.answer("Выберите ученика для ДЗ:", reply_markup=students_kb(students))
    await state.update_data(student_map={s.full_name: s.id for s in students})
    await state.set_state(HWCreate.choosing_student)


@router.message(HWCreate.choosing_student)
async def give_hw_choose_student(msg: Message, state: FSMContext):
    if cancel_if_requested(msg, state):
        return
    data = await state.get_data()
    st_id = data["student_map"].get(msg.text.strip())
    if not st_id:
        await msg.answer("Нажмите кнопку c именем ученика из списка.")
        return
    await state.update_data(student_id=st_id, text="", files=[])
    await msg.answer(
        "Введите текст задания (или отправьте файл/фото). Можно отправлять несколько файлов.\nКогда закончите – отправьте /done.",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[KeyboardButton(text="/done")]]),
    )
    await state.set_state(HWCreate.waiting_text)


@router.message(HWCreate.waiting_text, F.text == "/done")
async def give_hw_finish(msg: Message, session, state: FSMContext):
    data = await state.get_data()
    student_id = data["student_id"]
    tutor: User | None = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    hw = Homework(
        tutor_id=tutor.id,
        student_id=student_id,
        text=data.get("text", ""),
        files=data.get("files", []),
        assigned_at=datetime.utcnow(),
    )
    session.add(hw)
    await session.flush()  # получаем hw.id до коммита
    # Уведомляем ученика о новом ДЗ
    student: User = await session.get(User, student_id)
    await msg.answer("✅ Домашнее задание сохранено и отправлено ученику.", reply_markup=TUTOR_MENU)
    await msg.bot.send_message(
        student.telegram_id,
        f"Вам новое ДЗ от репетитора:\n{hw.text or '<без текста>'}",
        reply_markup=STUDENT_MENU,
    )
    for fid in hw.files:
        try:
            await msg.bot.send_photo(student.telegram_id, fid)
        except Exception:
            await msg.bot.send_document(student.telegram_id, fid)
    await msg.bot.send_message(
        student.telegram_id,
        "Когда будете готовы, отправьте ответ командой /answer_homework.",
    )
    await state.clear()


@router.message(HWCreate.waiting_text, F.text | F.photo | F.document)
async def give_hw_collect_files(msg: Message, state: FSMContext):
    # собираем file_id любых media
    if msg.photo:
        fid = msg.photo[-1].file_id
        data = await state.get_data()
        files = data.get("files", [])
        files.append(fid)
        await state.update_data(files=files)
    elif msg.document:
        fid = msg.document.file_id
        data = await state.get_data()
        files = data.get("files", [])
        files.append(fid)
        await state.update_data(files=files)
    else:
        if cancel_if_requested(msg, state):
            return
        # конкатенируем, если текст приходит несколькими сообщениями
        data = await state.get_data()
        new_text = (data.get("text") or "") + ("\n" if data.get("text") else "") + msg.text
        await state.update_data(text=new_text)


# ────────────────────────  ANSWER HOMEWORK (student) ──────────────────────── #
@router.message(Command("answer_homework"))
@router.message(F.text == "📨 Отправить решение")
async def hw_answer_start(msg: Message, session, state: FSMContext):
    student: User | None = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not student or student.is_tutor:
        await msg.answer("Эта команда для учеников.")
        return
    hws = (await session.execute(
        select(Homework).where(Homework.student_id == student.id, Homework.answered_at.is_(None))
    )).scalars().all()
    if not hws:
        await msg.answer("Нет заданий, на которые нужно ответить.", reply_markup=STUDENT_MENU)
        return
    mas = []
    mapping: dict[str, int] = {}
    for hw in hws:
        title = hw.text[:30] if hw.text else f"ДЗ #{hw.id}"
        mas.append([KeyboardButton(text=title)])
        mapping[title] = hw.id
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    await state.update_data(hw_map=mapping)
    await msg.answer("Выберите задание:", reply_markup=kb)
    await state.set_state(HWAnswer.choosing_hw)


@router.message(HWAnswer.choosing_hw)
async def hw_answer_choose(msg: Message, state: FSMContext):
    if cancel_if_requested(msg, state):
        return
    mapping = (await state.get_data())["hw_map"]
    hw_id = mapping.get(msg.text.strip())
    if not hw_id:
        await msg.answer("Нажмите кнопку из списка.")
        return
    await state.update_data(hw_id=hw_id, answer_text="", answer_files=[])
    await msg.answer(
        "Отправьте ответ (текст/файлы). Завершите /done.",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[KeyboardButton(text="/done")]]),
    )
    await state.set_state(HWAnswer.waiting_reply)
    

@router.message(HWAnswer.waiting_reply, F.text == "/done")
async def hw_answer_finish(msg: Message, session, state: FSMContext):
    data = await state.get_data()
    hw: Homework = await session.get(Homework, data["hw_id"])
    hw.answer_text = data.get("answer_text")
    hw.answer_files = data.get("answer_files")
    hw.answered_at = datetime.utcnow()
    await msg.answer("✅ Ответ отправлен репетитору.", reply_markup=STUDENT_MENU)
    tutor: User = await session.get(User, hw.tutor_id)
    await msg.bot.send_message(
        tutor.telegram_id,
        f"📨 Ученик {msg.from_user.full_name} отправил решение по ДЗ #{hw.id}.",
        reply_markup=TUTOR_MENU,
    )
    if hw.answer_text:
        await msg.bot.send_message(tutor.telegram_id, hw.answer_text)
    for fid in hw.answer_files or []:
        try:
            await msg.bot.send_photo(tutor.telegram_id, fid)
        except Exception:
            await msg.bot.send_document(tutor.telegram_id, fid)
    await state.clear()


@router.message(HWAnswer.waiting_reply, F.text | F.photo | F.document)
async def hw_answer_collect_files(msg: Message, state: FSMContext):
    if msg.photo:
        fid = msg.photo[-1].file_id
        data = await state.get_data()
        files = data.get("answer_files", [])
        files.append(fid)
        await state.update_data(answer_files=files)
    elif msg.document:
        fid = msg.document.file_id
        data = await state.get_data()
        files = data.get("answer_files", [])
        files.append(fid)
        await state.update_data(answer_files=files)
    else:
        if cancel_if_requested(msg, state):
            return
        data = await state.get_data()
        txt = (data.get("answer_text") or "") + ("\n" if data.get("answer_text") else "") + msg.text
        await state.update_data(answer_text=txt)


# ────────────────────────  LISTS  ──────────────────────── #
@router.message(Command("homeworks"))
@router.message(F.text == "📒 Мои ДЗ")
@router.message(F.text == "📒 Все ДЗ")
async def list_homeworks(msg: Message, session):
    """Вывод всех ДЗ для репетитора или ученика."""
    user: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if user.is_tutor:
        # Загружаем все ДЗ репетитора вместе с данными учеников одним запросом (JOIN)
        rows = (await session.execute(
            select(Homework, User).join(User, Homework.student_id == User.id)
            .where(Homework.tutor_id == user.id))
        ).all()
        lines = ["Все домашние задания"]
        for hw, st in rows:
            status = " ждёт ответа"
            if hw.answered_at:
                status = "⏳ на проверке" if not hw.checked else "✅ проверено"
            short = (hw.text[:25] + "...") if hw.text and len(hw.text) > 25 else (hw.text or "<без текста>")
            lines.append(f"• {st.full_name}: {short} – {status}")
        await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=TUTOR_MENU)
    else:
        hws = (await session.execute(
            select(Homework).where(Homework.student_id == user.id))
        ).scalars().all()
        lines = ["Мои домашние задания"]
        for hw in hws:
            status = "❌ не выполнено"
            if hw.answered_at:
                status = "⏳ на проверке" if not hw.checked else "✅ проверено"
            short = (hw.text[:25] + "...") if hw.text and len(hw.text) > 25 else (hw.text or "<без текста>")
            lines.append(f"• {short} – {status}")
        await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=STUDENT_MENU)


@router.message(Command("pending"))
async def list_pending(msg: Message, session):
    """Вывод ДЗ, ожидающих проверки (для репетитора) или отправленных и непроверенных (для ученика)."""
    user: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if user.is_tutor:
        # Получаем все сданные, но не проверенные ДЗ репетитора вместе с данными учеников
        pending_rows = (await session.execute(
            select(Homework, User).join(User, Homework.student_id == User.id)
            .where(Homework.tutor_id == user.id, Homework.answered_at.is_not(None), Homework.checked.is_(False)))
        ).all()
        if not pending_rows:
            await msg.answer("Нет работ, ожидающих проверки.", reply_markup=TUTOR_MENU)
            return
        lines = ["Ожидают проверки"]
        for hw, st in pending_rows:
            date = hw.answered_at.strftime("%d.%m %H:%M")
            lines.append(f"• #{hw.id} от {st.full_name} ({date})")
        await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=TUTOR_MENU)
    else:
        pending = (await session.execute(
            select(Homework).where(Homework.student_id == user.id, Homework.answered_at.is_not(None), Homework.checked.is_(False)))
        ).scalars().all()
        if not pending:
            await msg.answer("Нет отправленных Вами заданий, ожидающих проверки.", reply_markup=STUDENT_MENU)
            return
        lines = ["Мои отправленные, ещё не проверены:"]
        for hw in pending:
            date = hw.answered_at.strftime("%d.%m %H:%M")
            lines.append(f"• ДЗ #{hw.id} – отправлено {date}")
        await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=STUDENT_MENU)


# ────────────────────────  CHECK  ──────────────────────── #
@router.message(Command("check_homework"))
@router.message(F.text == "✅ Проверить ДЗ")
async def check_homework_cmd(msg: Message, session):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Использование: /check_homework <ID>")
        return
    hw_id = int(parts[1])
    hw: Homework | None = await session.get(Homework, hw_id)
    if not hw:
        await msg.answer("ДЗ не найдено.")
        return
    if hw.checked:
        await msg.answer("Уже отмечено как проверенное.")
        return
    tutor: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if hw.tutor_id != tutor.id:
        await msg.answer("Это не ваше задание.")
        return
    hw.checked = True
    await msg.answer("✅ Отмечено как проверенное.", reply_markup=TUTOR_MENU)
    student: User = await session.get(User, hw.student_id)
    try:
        await msg.bot.send_message(student.telegram_id, f"✅ Ваше ДЗ #{hw.id} проверено!", reply_markup=STUDENT_MENU)
    except Exception:
        pass
