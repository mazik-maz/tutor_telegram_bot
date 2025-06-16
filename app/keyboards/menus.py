from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# tutor main menu
TUTOR_MENU = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, keyboard=[
    [KeyboardButton(text="➕ Добавить урок"), KeyboardButton(text="✏️ Изменить урок")],
    [KeyboardButton(text="📚 Расписание"), KeyboardButton(text="📒 Все ДЗ")],
    [KeyboardButton(text="✏️ Задать ДЗ"), KeyboardButton(text="✅ Проверить ДЗ")],
    [KeyboardButton(text="➕ Ученика"), KeyboardButton(text="👥 Мои ученики")],
    []
])

# student main menu
STUDENT_MENU = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, keyboard=[
    [KeyboardButton(text="📚 Мои занятия")],
    [KeyboardButton(text="📒 Мои ДЗ")],
    [KeyboardButton(text="📨 Отправить решение")]
])

ROLE_CHOICE_KB = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="Я ученик"), KeyboardButton(text="Я репетитор")]
])