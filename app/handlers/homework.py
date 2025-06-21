from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload
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

class HWComment(StatesGroup):
    waiting_comment = State()

# ──────────────────────── helpers ──────────────────────── #
def students_kb(students: List[User]) -> ReplyKeyboardMarkup:
    mas = []
    for s in students:
        mas.append([KeyboardButton(text=s.full_name)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb

def kb_details(hw_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"hw_ok:{hw_id}"),
            InlineKeyboardButton(text="🔄 На доработку", callback_data=f"hw_redo:{hw_id}"),
        ],
        [InlineKeyboardButton(text="↩︎ Назад", callback_data="hw_back")]
    ])

def kb_open(hw_id: int) -> InlineKeyboardMarkup:
    """кнопка «Открыть» в списке работ"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Открыть", callback_data=f"hw_view:{hw_id}")]
    ])

async def _send_files(bot, chat_id: int, file_ids: list[str]):
    for fid in file_ids or []:
        try:
            await bot.send_photo(chat_id, fid)
        except Exception:
            await bot.send_document(chat_id, fid)

async def _send_hw_details(bot, chat_id: int, hw: Homework, *, include_answer: bool):
    await bot.send_message(chat_id, f"<b>ДЗ #{hw.id}</b>\n{hw.text or '<без текста>'}", parse_mode="HTML")
    await _send_files(bot, chat_id, hw.files or [])
    if include_answer:
        if hw.answer_text or hw.answer_files:
            await bot.send_message(chat_id, "Ответ ученика:")
            if hw.answer_text:
                await bot.send_message(chat_id, hw.answer_text)
            await _send_files(bot, chat_id, hw.answer_files or [])
        else:
            await bot.send_message(chat_id, "Ответ ученика пока отсутствует.")

# ────────────────────────  GIVE HOMEWORK (tutor) ──────────────────────── #
@router.message(Command("give_homework"))
@router.message(F.text == "✏️ Задать ДЗ")
async def give_hw_start(msg: Message, session, state: FSMContext):
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
    if msg.text.startswith("↩️"):
        await state.clear()
        await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
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
        answered_at=None,
    )
    session.add(hw)
    await session.flush()
    student: User = await session.get(User, student_id)
    await msg.answer("✅ Домашнее задание сохранено и отправлено ученику.", reply_markup=TUTOR_MENU)
    await _send_hw_details(msg.bot, student.telegram_id, hw, include_answer=False)
    await msg.bot.send_message(
        student.telegram_id,
        "Когда будете готовы, отправьте ответ командой /answer_homework.",
    )
    await state.clear()


@router.message(HWCreate.waiting_text, F.text | F.photo | F.document)
async def give_hw_collect_files(msg: Message, state: FSMContext):
    # собираем file_id любых media
    if msg.photo or msg.document:
        fid = msg.photo[-1].file_id if msg.photo else msg.document.file_id
        data = await state.get_data()
        files = data.get("files", [])
        files.append(fid)
        await state.update_data(files=files)
    else:
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
async def hw_answer_choose(msg: Message, session, state: FSMContext):
    if msg.text.startswith("↩️"):
        await state.clear()
        await msg.answer("Отмена.", reply_markup=STUDENT_MENU)
        return
    mapping = (await state.get_data())["hw_map"]
    hw_id = mapping.get(msg.text.strip())
    if not hw_id:
        await msg.answer("Нажмите кнопку из списка.")
        return
    hw: Homework = await session.get(Homework, hw_id)
    await _send_hw_details(msg.bot, msg.chat.id, hw, include_answer=False)
    await msg.answer(
        "Отправьте решение (текст/файлы). Когда закончите — /done. "
        "Если хотите вернуться позже — /later",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[KeyboardButton(text="/done"), KeyboardButton(text="/later")]]),
    )
    await state.update_data(hw_id=hw_id, answer_text="", answer_files=[])
    await state.set_state(HWAnswer.waiting_reply)
    
@router.message(HWAnswer.waiting_reply, F.text == "/later")
async def answer_later(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Хорошо, вернётесь позже.", reply_markup=STUDENT_MENU)

@router.message(HWAnswer.waiting_reply, F.text == "/done")
async def hw_answer_finish(msg: Message, session, state: FSMContext):
    data = await state.get_data()
    hw: Homework = await session.get(Homework, data["hw_id"])
    hw.answer_text = data.get("answer_text")
    hw.answer_files = data.get("answer_files")
    hw.answered_at = datetime.utcnow()
    await msg.answer("✅ Ответ отправлен репетитору.", reply_markup=STUDENT_MENU)
    tutor: User = await session.get(User, hw.tutor_id)
    await _send_hw_details(msg.bot, tutor.telegram_id, hw, include_answer=True)
    await msg.bot.send_message(
        tutor.telegram_id,
        f"📨 Ученик {msg.from_user.full_name} отправил решение по ДЗ #{hw.id}.",
        reply_markup=TUTOR_MENU,
    )
    await state.clear()


@router.message(HWAnswer.waiting_reply, F.text | F.photo | F.document)
async def hw_answer_collect_files(msg: Message, state: FSMContext):
    if msg.photo or msg.document:
        fid = msg.photo[-1].file_id if msg.photo else msg.document.file_id
        data = await state.get_data()
        files = data.get("answer_files", [])
        files.append(fid)
        await state.update_data(answer_files=files)
    else:
        data = await state.get_data()
        txt = (data.get("answer_text") or "") + ("\n" if data.get("answer_text") else "") + msg.text
        await state.update_data(answer_text=txt)


# ────────────────────────  LISTS  ──────────────────────── #
@router.message(Command("homeworks"))
@router.message(F.text == "📒 Мои ДЗ")
@router.message(F.text == "📒 Все ДЗ")
async def list_homeworks(msg: Message, session):
    user: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if user.is_tutor:
        hws = (
            await session.execute(
                select(Homework)
                .options(selectinload(Homework.student))
                .where(Homework.tutor_id == user.id)
            )
        ).scalars().all()
        header = "<b>Активные ДЗ</b>"
        menu = TUTOR_MENU
    else:
        hws = (await session.execute(
            select(Homework).where(Homework.student_id == user.id))
        ).scalars().all()
        header = "<b>Мои ДЗ</b>"
        menu = STUDENT_MENU

    if not hws:
        await msg.answer("Нет активных ДЗ.", reply_markup=menu)
        return

    lines = [header]
    for hw in hws:
        status = (
            "⌛ ждёт ответа" if hw.answered_at is None else
            "🕒 на проверке" if not hw.answer_files and not hw.answer_text else
            "📬 ответ отправлен"
        )
        who = f" — {hw.student.full_name}" if user.is_tutor else ""
        short = (hw.text[:30] + "…") if hw.text and len(hw.text) > 30 else (hw.text or "—")
        lines.append(f"• #{hw.id}{who}: {short} ({status})")

    await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=menu)

@router.message(Command("pending"))
@router.message(F.text == "✅ Проверить ДЗ")
async def list_pending(msg: Message, session):
    tutor: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not tutor or not tutor.is_tutor:
        return
    
    hws = (
        await session.execute(
            select(Homework)
            .options(selectinload(Homework.student))
            .where(Homework.tutor_id == tutor.id, Homework.answered_at.is_not(None))
        )
    ).scalars().all()

    if not hws:
        await msg.answer("Нет работ на проверку.", reply_markup=TUTOR_MENU)
        return
    for hw in hws:
        title = hw.text[:50] or "—"
        msg.answer(f"{hw}")
        await msg.answer(
            f"<b>ДЗ #{hw.id}</b> от {hw.student.full_name}\n{title}",
            parse_mode="HTML",
            reply_markup=kb_open(hw.id),
        )

@router.callback_query(F.data.startswith("hw_view"))
async def hw_view(cb: CallbackQuery, session):
    hw_id = int(cb.data.split(":")[1])
    hw: Homework | None = await session.get(Homework, hw_id)
    if not hw:
        await cb.answer("Уже обработано.")
        return
    await _send_hw_details(cb.bot, cb.from_user.id, hw, include_answer=True)
    await cb.message.answer("Выберите действие:", reply_markup=kb_details(hw.id))
    await cb.answer()

@router.callback_query(F.data == "hw_back")
async def hw_back(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer("Вернулись к списку.")

@router.callback_query(F.data.startswith("hw_ok"))
async def hw_ok_ask_comment(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    hw_id = int(cb.data.split(":")[1])
    await state.update_data(hw_id=hw_id, action="ok", src_msg=cb.message.message_id)
    await cb.message.answer("Напишите комментарий для ученика (или «-» без комментария):")
    await state.set_state(HWComment.waiting_comment)

@router.callback_query(F.data.startswith("hw_redo"))
async def hw_redo_ask_comment(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    hw_id = int(cb.data.split(":")[1])
    await state.update_data(hw_id=hw_id, action="redo", src_msg=cb.message.message_id)
    await cb.message.answer("Комментарий для ученика (почему на доработку, «-» без):")
    await state.set_state(HWComment.waiting_comment)

# ────────── шаг 2: получаем комментарий ──────────
@router.message(HWComment.waiting_comment)
async def hw_comment_finish(msg: Message, state: FSMContext, session):
    data = await state.get_data()
    hw: Homework | None = await session.get(Homework, data["hw_id"])
    if not hw:
        await msg.answer("Задание уже обработано.")
        await state.clear()
        return

    comment = None if msg.text.strip() == "-" else msg.text.strip()
    student: User = await session.get(User, hw.student_id)

    # действие зависит от начального callback
    if data["action"] == "ok":
        # принять: удаляем из БД
        await session.delete(hw)
        await msg.bot.edit_message_text(
            "✅ Работа принята.",
            chat_id=msg.chat.id,
            message_id=data["src_msg"],
        )
        txt = "Ваше ДЗ проверено и принято!"
        if comment:
            txt += f"\nКомментарий преподавателя: {comment}"
        await msg.bot.send_message(student.telegram_id, txt)
    else:                       # redo
        hw.answered_at = None
        await msg.bot.edit_message_text(
            "🔄 Отправлено на доработку.",
            chat_id=msg.chat.id,
            message_id=data["src_msg"],
        )
        txt = "ДЗ отправлено на доработку."
        if comment:
            txt += f"\nКомментарий преподавателя: {comment}"
        await msg.bot.send_message(student.telegram_id, txt)

    await msg.answer("✅ Готово.", reply_markup=TUTOR_MENU)
    await state.clear()

