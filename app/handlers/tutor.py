from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta

from sqlalchemy import select
from models.models import Tutor, Student, Lesson, Homework
from services.scheduler import schedule_lesson_notification, remove_lesson_notification

router = Router()

# Состояния FSM для многошаговых процессов
class AddStudentState(StatesGroup):
    waiting_for_id = State()
    waiting_for_name = State()
    waiting_for_tz = State()

class ScheduleLessonState(StatesGroup):
    waiting_for_student = State()
    waiting_for_datetime = State()
    waiting_for_link = State()

class RescheduleLessonState(StatesGroup):
    waiting_for_lesson_id = State()
    waiting_for_new_time = State()

class HomeworkAssignState(StatesGroup):
    waiting_for_student = State()
    waiting_for_text = State()

@router.message(Command("добавить_ученика"))
async def cmd_add_student(message: Message, state: FSMContext, session):
    # Команда доступна только для зарегистрированных репетиторов
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Команда доступна только для репетиторов.")
        return
    await message.answer("Введите Telegram ID ученика (число):")
    await state.set_state(AddStudentState.waiting_for_id)

@router.message(AddStudentState.waiting_for_id)
async def add_student_get_id(message: Message, state: FSMContext, session):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Пожалуйста, отправьте числовой ID.")
        return
    student_tg_id = int(text)
    await state.update_data(new_student_tg_id=student_tg_id)
    await message.answer("Теперь отправьте имя ученика (как вы будете его называть).")
    await state.set_state(AddStudentState.waiting_for_name)

@router.message(AddStudentState.waiting_for_name)
async def add_student_get_name(message: Message, state: FSMContext, session):
    name = message.text.strip()
    data = await state.get_data()
    student_tg_id = data.get("new_student_tg_id")
    if not name:
        await message.answer("Имя не может быть пустым. Введите имя ученика:")
        return
    # Проверяем, не зарегистрирован ли уже ученик с таким Telegram ID
    result_stud = await session.execute(select(Student).where(Student.telegram_id == student_tg_id))
    existing = result_stud.scalars().first()
    if existing:
        await message.answer("Ученик с таким Telegram ID уже зарегистрирован.")
        await state.clear()
        return
    await state.update_data(new_student_name=name)
    await message.answer("Укажите часовой пояс ученика (смещение от UTC, например +3 или -5):")
    await state.set_state(AddStudentState.waiting_for_tz)

@router.message(AddStudentState.waiting_for_tz)
async def add_student_get_tz(message: Message, state: FSMContext, session):
    tz_text = message.text.strip()
    # Парсим смещение часового пояса
    try:
        offset = int(tz_text)
    except:
        tz_text = tz_text.replace("UTC", "").strip()
        try:
            offset = int(tz_text)
        except:
            await message.answer("Пожалуйста, укажите часовой пояс числом, например +3 или -5.")
            return
    if offset < -12 or offset > 14:
        await message.answer("Укажите часовой пояс в диапазоне -12 ... +14.")
        return
    data = await state.get_data()
    student_tg_id = data.get("new_student_tg_id")
    name = data.get("new_student_name")
    # Находим репетитора
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Произошла ошибка: не найден ваш аккаунт репетитора.")
        await state.clear()
        return
    new_student = Student(telegram_id=student_tg_id, name=name, timezone_offset=offset, tutor_id=tutor.id)
    session.add(new_student)
    await message.answer(f"Ученик «{name}» добавлен (ID: {student_tg_id}, часовой пояс UTC{'+' if offset>=0 else ''}{offset}).")
    # Пробуем уведомить ученика, если он начал диалог с ботом
    try:
        await message.bot.send_message(student_tg_id, f"Вас добавил ваш репетитор {tutor.full_name or 'репетитор'}. Теперь вы будете получать уведомления о занятиях и домашних заданиях.")
    except Exception:
        await message.answer("⚠️ Внимание: бот не смог отправить сообщение ученику. Убедитесь, что ученик начал диалог с ботом.")
    await state.clear()

@router.message(Command("расписание"))
async def cmd_schedule(message: Message, session):
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Команда доступна только для репетиторов.")
        return
    now = datetime.utcnow()
    result_lessons = await session.execute(select(Lesson).where(
        Lesson.tutor_id == tutor.id, Lesson.is_canceled == False, Lesson.scheduled_time >= now
    ).order_by(Lesson.scheduled_time))
    lessons = result_lessons.scalars().all()
    if not lessons:
        await message.answer("У вас нет запланированных занятий.")
        return
    lines = []
    for lesson in lessons:
        student = await session.get(Student, lesson.student_id)
        if not student:
            continue
        offset = student.timezone_offset
        local_time = lesson.scheduled_time + timedelta(hours=offset)
        time_str = local_time.strftime("%d.%m.%Y %H:%M") + f" (UTC{'+' if offset>=0 else ''}{offset})"
        lines.append(f"ID {lesson.id}: {student.name} – {time_str}")
    schedule_text = "Ваши запланированные занятия:\n" + "\n".join(lines)
    await message.answer(schedule_text)

@router.message(Command("назначить_занятие"))
async def cmd_add_lesson(message: Message, state: FSMContext, session):
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Команда доступна только для репетиторов.")
        return
    # Проверяем, есть ли хотя бы один ученик
    result_students = await session.execute(select(Student).where(Student.tutor_id == tutor.id))
    students = result_students.scalars().all()
    if not students:
        await message.answer("У вас ещё нет учеников. Сначала добавьте ученика командой /добавить_ученика.")
        return
    student_names = ", ".join([s.name for s in students])
    await message.answer(f"Введите имя ученика для занятия (ваши ученики: {student_names})")
    await state.set_state(ScheduleLessonState.waiting_for_student)

@router.message(ScheduleLessonState.waiting_for_student)
async def schedule_lesson_get_student(message: Message, state: FSMContext, session):
    name = message.text.strip()
    if not name:
        await message.answer("Имя не распознано. Попробуйте ещё раз.")
        return
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Ошибка: вы не зарегистрированы как репетитор.")
        await state.clear()
        return
    # Ищем ученика с указанным именем (регистронезависимо)
    result_student = await session.execute(select(Student).where(Student.tutor_id == tutor.id, Student.name.ilike(name)))
    student = result_student.scalars().first()
    if not student:
        await message.answer("Ученик с таким именем не найден. Введите имя ещё раз:")
        return
    await state.update_data(chosen_student_id=student.id)
    await message.answer("Введите дату и время занятия в формате YYYY-MM-DD HH:MM (по часовому поясу ученика):")
    await state.set_state(ScheduleLessonState.waiting_for_datetime)

@router.message(ScheduleLessonState.waiting_for_datetime)
async def schedule_lesson_get_datetime(message: Message, state: FSMContext, session):
    datetime_str = message.text.strip()
    try:
        scheduled_dt_local = datetime.strptime(datetime_str, '''%Y-%m-%d %H:%M''')
    except:
        await message.answer("Не удалось распознать дату и время. Формат должен быть ГГГГ-ММ-ДД ЧЧ:ММ. Попробуйте ещё раз.")
        return
    data = await state.get_data()
    student_id = data.get("chosen_student_id")
    if not student_id:
        await message.answer("Произошла ошибка при выборе ученика.")
        await state.clear()
        return
    student = await session.get(Student, student_id)
    if not student:
        await message.answer("Ошибка: ученик не найден.")
        await state.clear()
        return
    # Конвертируем указанное время из часового пояса ученика в UTC
    offset = student.timezone_offset
    scheduled_dt_utc = scheduled_dt_local - timedelta(hours=offset)
    if scheduled_dt_utc < datetime.utcnow():
        await message.answer("Указанное время уже прошло. Введите будущую дату и время:")
        return
    await state.update_data(scheduled_time_utc=scheduled_dt_utc.isoformat())
    await message.answer("Отправьте ссылку для звонка (или напишите '-' если не требуется):")
    await state.set_state(ScheduleLessonState.waiting_for_link)

@router.message(ScheduleLessonState.waiting_for_link)
async def schedule_lesson_get_link(message: Message, state: FSMContext, session):
    link = message.text.strip()
    if link == "-" or link.lower() == "нет":
        link = None
    data = await state.get_data()
    student_id = data.get("chosen_student_id")
    scheduled_dt_utc = datetime.fromisoformat(data["scheduled_time_utc"])
    if not student_id or not scheduled_dt_utc:
        await message.answer("Ошибка при сохранении занятия.")
        await state.clear()
        return
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Ошибка: вы не зарегистрированы как репетитор.")
        await state.clear()
        return
    # Сохраняем занятие в базе
    new_lesson = Lesson(tutor_id=tutor.id, student_id=student_id, scheduled_time=scheduled_dt_utc, link=link)
    session.add(new_lesson)
    await session.flush()  # получаем new_lesson.id
    lesson_id = new_lesson.id
    # Планируем напоминание о занятии
    schedule_lesson_notification(lesson_id, scheduled_dt_utc)
    # Уведомляем ученика о новом занятии
    student = await session.get(Student, student_id)
    if student:
        offset = student.timezone_offset
        local_time = scheduled_dt_utc + timedelta(hours=offset)
        time_str = local_time.strftime("%d.%m.%Y %H:%M") + f" (UTC{'+' if offset>=0 else ''}{offset})"
        try:
            await message.bot.send_message(student.telegram_id, f"Назначено новое занятие на {time_str}. Ссылка: {link or 'не указана'}")
        except:
            pass
    await message.answer("Занятие запланировано.")
    await state.clear()

@router.message(Command("отменить_занятие"))
async def cmd_cancel_lesson(message: Message, session):
    user_id = message.from_user.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Использование: /отменить_занятие <ID>")
        return
    lesson_id_str = parts[1]
    if not lesson_id_str.isdigit():
        await message.answer("Укажите корректный номер ID занятия.")
        return
    lesson_id = int(lesson_id_str)
    lesson = await session.get(Lesson, lesson_id)
    if not lesson or lesson.is_canceled:
        await message.answer("Занятие с указанным ID не найдено или уже отменено.")
        return
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor or lesson.tutor_id != tutor.id:
        await message.answer("У вас нет прав отменять это занятие.")
        return
    lesson.is_canceled = True
    remove_lesson_notification(lesson_id)
    student = await session.get(Student, lesson.student_id)
    if student:
        offset = student.timezone_offset
        local_time = lesson.scheduled_time + timedelta(hours=offset)
        time_str = local_time.strftime("%d.%m.%Y %H:%M") + f" (UTC{'+' if offset>=0 else ''}{offset})"
        try:
            await message.bot.send_message(student.telegram_id, f"Занятие {time_str} отменено репетитором.")
        except:
            pass
    await message.answer("Занятие отменено.")

@router.message(Command("перенести_занятие"))
async def cmd_reschedule_lesson(message: Message, state: FSMContext, session):
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Команда доступна только для репетиторов.")
        return
    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Укажите ID занятия, которое нужно перенести:")
        await state.set_state(RescheduleLessonState.waiting_for_lesson_id)
    else:
        lesson_id_str = parts[1]
        if not lesson_id_str.isdigit():
            await message.answer("Укажите корректный ID занятия.")
            return
        lesson_id = int(lesson_id_str)
        lesson = await session.get(Lesson, lesson_id)
        if not lesson or lesson.is_canceled or lesson.tutor_id != tutor.id:
            await message.answer("Занятие не найдено или у вас нет прав на изменение.")
            return
        await state.update_data(reschedule_lesson_id=lesson_id)
        if len(parts) >= 3:
            new_time_str = parts[2]
            try:
                new_dt_local = datetime.strptime(new_time_str, "%Y-%m-%d %H:%M")
            except:
                await message.answer("Формат даты/времени неверен. Введите новую дату и время (ГГГГ-ММ-ДД ЧЧ:ММ):")
                await state.set_state(RescheduleLessonState.waiting_for_new_time)
                return
            student = await session.get(Student, lesson.student_id)
            if student:
                new_dt_utc = new_dt_local - timedelta(hours=student.timezone_offset)
            else:
                new_dt_utc = new_dt_local
            if new_dt_utc < datetime.utcnow():
                await message.answer("Указанное время уже прошло. Введите корректное будущее время:")
                await state.set_state(RescheduleLessonState.waiting_for_new_time)
                return
            lesson.scheduled_time = new_dt_utc
            schedule_lesson_notification(lesson_id, new_dt_utc)
            if student:
                offset = student.timezone_offset
                local_time = new_dt_utc + timedelta(hours=offset)
                time_str = local_time.strftime("%d.%m.%Y %H:%M") + f" (UTC{'+' if offset>=0 else ''}{offset})"
                try:
                    await message.bot.send_message(student.telegram_id, f"Занятие перенесено на {time_str}.")
                except:
                    pass
            await message.answer("Занятие перенесено.")
            await state.clear()
        else:
            await message.answer("Введите новую дату и время занятия (ГГГГ-ММ-ДД ЧЧ:ММ):")
            await state.set_state(RescheduleLessonState.waiting_for_new_time)

@router.message(RescheduleLessonState.waiting_for_lesson_id)
async def reschedule_get_id(message: Message, state: FSMContext, session):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Пожалуйста, введите числовой ID занятия.")
        return
    lesson_id = int(text)
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Ошибка.")
        await state.clear()
        return
    lesson = await session.get(Lesson, lesson_id)
    if not lesson or lesson.is_canceled or lesson.tutor_id != tutor.id:
        await message.answer("Занятие не найдено или недоступно.")
        await state.clear()
        return
    await state.update_data(reschedule_lesson_id=lesson_id)
    await message.answer("Введите новую дату и время занятия (ГГГГ-ММ-ДД ЧЧ:ММ):")
    await state.set_state(RescheduleLessonState.waiting_for_new_time)

@router.message(RescheduleLessonState.waiting_for_new_time)
async def reschedule_get_new_time(message: Message, state: FSMContext, session):
    new_time_str = message.text.strip()
    try:
        new_dt_local = datetime.strptime(new_time_str, "%Y-%m-%d %H:%M")
    except:
        await message.answer("Неправильный формат. Введите дату и время в формате ГГГГ-ММ-ДД ЧЧ:ММ:")
        return
    data = await state.get_data()
    lesson_id = data.get("reschedule_lesson_id")
    lesson = await session.get(Lesson, lesson_id) if lesson_id else None
    if not lesson or lesson.is_canceled:
        await message.answer("Произошла ошибка: занятие не найдено.")
        await state.clear()
        return
    student = await session.get(Student, lesson.student_id)
    if student:
        new_dt_utc = new_dt_local - timedelta(hours=student.timezone_offset)
    else:
        new_dt_utc = new_dt_local
    if new_dt_utc < datetime.utcnow():
        await message.answer("Указанное время уже прошло. Введите новую дату/время:")
        return
    lesson.scheduled_time = new_dt_utc
    schedule_lesson_notification(lesson.id, new_dt_utc)
    if student:
        offset = student.timezone_offset
        local_time = new_dt_utc + timedelta(hours=offset)
        time_str = local_time.strftime("%d.%m.%Y %H:%M") + f" (UTC{'+' if offset>=0 else ''}{offset})"
        try:
            await message.bot.send_message(student.telegram_id, f"Занятие перенесено на {time_str}.")
        except:
            pass
    await message.answer("Занятие перенесено.")
    await state.clear()

@router.message(Command("задать_дз"))
async def cmd_assign_homework(message: Message, state: FSMContext, session):
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Команда доступна только для репетиторов.")
        return
    result_students = await session.execute(select(Student).where(Student.tutor_id == tutor.id))
    students = result_students.scalars().all()
    if not students:
        await message.answer("У вас нет учеников для выдачи задания.")
        return
    student_names = ", ".join([s.name for s in students])
    await message.answer(f"Введите имя ученика для выдачи задания (ваши ученики: {student_names})")
    await state.set_state(HomeworkAssignState.waiting_for_student)

@router.message(HomeworkAssignState.waiting_for_student)
async def homework_get_student(message: Message, state: FSMContext, session):
    name = message.text.strip()
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Ошибка.")
        await state.clear()
        return
    result_student = await session.execute(select(Student).where(Student.tutor_id == tutor.id, Student.name.ilike(name)))
    student = result_student.scalars().first()
    if not student:
        await message.answer("Ученик не найден. Попробуйте снова ввести имя:")
        return
    # Проверяем отсутствие незавершённого задания у этого ученика
    result_hw = await session.execute(select(Homework).where(Homework.student_id == student.id, Homework.is_checked == False))
    existing_hw = result_hw.scalars().first()
    if existing_hw:
        if not existing_hw.is_submitted:
            await message.answer("⚠️ У этого ученика уже есть выданное задание, которое ещё не выполнено.")
        else:
            await message.answer("⚠️ У этого ученика есть отправленное задание, ожидающее проверки.")
        await state.clear()
        return
    await state.update_data(homework_student_id=student.id)
    await message.answer("Введите текст домашнего задания:")
    await state.set_state(HomeworkAssignState.waiting_for_text)

@router.message(HomeworkAssignState.waiting_for_text)
async def homework_get_text(message: Message, state: FSMContext, session):
    task_text = message.text.strip()
    data = await state.get_data()
    student_id = data.get("homework_student_id")
    if not student_id or not task_text:
        await message.answer("Ошибка при создании задания.")
        await state.clear()
        return
    user_id = message.from_user.id
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == user_id))
    tutor = result_tutor.scalars().first()
    if not tutor:
        await message.answer("Ошибка.")
        await state.clear()
        return
    new_hw = Homework(tutor_id=tutor.id, student_id=student_id, task_text=task_text)
    session.add(new_hw)
    await session.flush()  # получаем new_hw.id
    student = await session.get(Student, student_id)
    if student:
        try:
            await message.bot.send_message(student.telegram_id, f"Вам новое домашнее задание: {task_text}\nКогда будете готовы, отправьте решение сообщением боту.")
        except:
            pass
    await message.answer(f"Домашнее задание для ученика {student.name} задано.")
    await state.clear()

@router.callback_query(lambda c: c.data and c.data.startswith("hw_checked:"))
async def hw_checked_callback(callback: CallbackQuery, session):
    try:
        hw_id = int(callback.data.split(":")[1])
    except:
        await callback.answer("Ошибка данных.")
        return
    hw = await session.get(Homework, hw_id)
    if not hw or hw.is_checked:
        await callback.answer("Задание уже отмечено.", show_alert=True)
        return
    result_tutor = await session.execute(select(Tutor).where(Tutor.telegram_id == callback.from_user.id))
    tutor = result_tutor.scalars().first()
    if not tutor or hw.tutor_id != tutor.id:
        await callback.answer("Нет прав.", show_alert=True)
        return
    hw.is_checked = True
    hw.checked_at = datetime.utcnow()
    student = await session.get(Student, hw.student_id)
    if student:
        try:
            await callback.message.bot.send_message(student.telegram_id, "✅ Ваше домашнее задание проверено.")
        except:
            pass
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass
    await callback.answer("Отмечено как проверенное.")
