import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from dotenv import load_dotenv

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey,
    BigInteger, func, select, and_, or_
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
import enum

load_dotenv()

# ============================================================
# 1. НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '8541378272').split(',')]
CHANNEL_ID = os.getenv('CHANNEL_USERNAME', '@twiqo_channel')
MSK_TZ = pytz.timezone('Europe/Moscow')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# 2. БАЗА ДАННЫХ
# ============================================================

Base = declarative_base()


class ProfileStatus(str, enum.Enum):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    HIDDEN = 'hidden'


class ChatStatus(str, enum.Enum):
    ACTIVE = 'active'
    FINISHED = 'finished'


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    age = Column(Integer)
    gender = Column(String(50))
    city = Column(String(255))
    description = Column(Text)
    photo_file_id = Column(String(255))
    status = Column(String(50), default=ProfileStatus.PENDING)
    is_active = Column(Boolean, default=True)
    is_blocked = Column(Boolean, default=False)
    is_channel_subscriber = Column(Boolean, default=False)
    looking_for_gender = Column(String(50))
    language = Column(String(10), default='ru')
    views_count = Column(Integer, default=0)
    profiles_viewed_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

    interests = relationship('UserInterest', back_populates='user', cascade='all, delete-orphan')
    ratings_given = relationship('Rating', foreign_keys='Rating.from_user_id', back_populates='from_user')
    ratings_received = relationship('Rating', foreign_keys='Rating.to_user_id', back_populates='to_user')


class Interest(Base):
    __tablename__ = 'interests'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True)
    emoji = Column(String(10))
    users = relationship('UserInterest', back_populates='interest')


class UserInterest(Base):
    __tablename__ = 'user_interests'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    interest_id = Column(Integer, ForeignKey('interests.id', ondelete='CASCADE'))
    user = relationship('User', back_populates='interests')
    interest = relationship('Interest', back_populates='users')


class Rating(Base):
    __tablename__ = 'ratings'
    id = Column(Integer, primary_key=True)
    from_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    to_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    rating = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)
    from_user = relationship('User', foreign_keys=[from_user_id], back_populates='ratings_given')
    to_user = relationship('User', foreign_keys=[to_user_id], back_populates='ratings_received')


class ProfileView(Base):
    __tablename__ = 'profile_views'
    id = Column(Integer, primary_key=True)
    viewer_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    profile_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    viewed_date = Column(DateTime, default=datetime.now)


class Match(Base):
    __tablename__ = 'matches'
    id = Column(Integer, primary_key=True)
    user1_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    user2_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    status = Column(String(50), default='active')
    created_at = Column(DateTime, default=datetime.now)


class TemporaryChat(Base):
    __tablename__ = 'temporary_chats'
    id = Column(Integer, primary_key=True)
    user1_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    user2_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    status = Column(String(50), default=ChatStatus.ACTIVE)
    match_id = Column(Integer, ForeignKey('matches.id', ondelete='SET NULL'))
    started_at = Column(DateTime, default=datetime.now)


class Block(Base):
    __tablename__ = 'blocks'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    blocked_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    created_at = Column(DateTime, default=datetime.now)


class Report(Base):
    __tablename__ = 'reports'
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    reported_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    reason = Column(String(255))
    status = Column(String(50), default='pending')
    created_at = Column(DateTime, default=datetime.now)


class WelcomeSettings(Base):
    __tablename__ = 'welcome_settings'
    id = Column(Integer, primary_key=True)
    text = Column(Text, default="👋 Добро пожаловать в Twiqo!")
    photo_file_id = Column(String(255))


# ============================================================
# 3. НАСТРОЙКА БД
# ============================================================

# Для Render используем DATA_DIR, если есть
DATA_DIR = os.getenv('DATA_DIR', '.')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'twiqo.db')
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        interests_list = [
            '🎮 Игры', '🎵 Музыка', '🏋️ Спорт', '💻 IT', '🎬 Фильмы',
            '📚 Книги', '✈️ Путешествия', '🐾 Животные', '⚽ Футбол', '🏎 Автомобили'
        ]
        for i in interests_list:
            result = await session.execute(select(Interest).where(Interest.name == i))
            if not result.scalar_one_or_none():
                session.add(Interest(name=i, emoji=i[0]))

        result = await session.execute(select(WelcomeSettings))
        if not result.scalar_one_or_none():
            session.add(WelcomeSettings())
        await session.commit()


# ============================================================
# 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

async def get_user(telegram_id: int) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()


async def get_user_by_id(user_id: int) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


async def update_user(user: User):
    user.updated_at = datetime.now()
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()


async def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


async def get_welcome() -> WelcomeSettings:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WelcomeSettings))
        return result.scalar_one_or_none()


async def get_user_rating(user_id: int) -> float:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.avg(Rating.rating)).where(Rating.to_user_id == user_id))
        return float(result.scalar() or 0)


async def get_ratings_count(user_id: int) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count(Rating.id)).where(Rating.to_user_id == user_id))
        return result.scalar() or 0


async def check_channel_subscription(user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False


async def send_to_moderation(user_id: int, changed_field: str = ""):
    """Отправляет анкету на модерацию и уведомляет админов"""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return
        
        if user.status in [ProfileStatus.APPROVED, ProfileStatus.HIDDEN]:
            user.status = ProfileStatus.PENDING
            await session.commit()
            
            for admin in ADMIN_IDS:
                try:
                    text = f"🔄 Изменение анкеты!\n"
                    if changed_field:
                        text += f"Изменено поле: {changed_field}\n"
                    text += f"👤 {user.first_name}, {user.age} лет\n📍 {user.city}\n\nТребуется повторная модерация"
                    
                    await bot.send_message(admin, text)
                    if user.photo_file_id:
                        await bot.send_photo(admin, user.photo_file_id)
                    await bot.send_message(admin, "Действия:", reply_markup=mod_kb(user.id))
                except Exception as e:
                    logger.error(f"Ошибка уведомления админа: {e}")

            try:
                await bot.send_message(
                    user.telegram_id, 
                    "📝 Анкета отправлена на повторную модерацию.\n"
                    "Ожидай подтверждения администратора. Это займёт немного времени ⏳"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя: {e}")


async def require_channel_subscription(message: types.Message, user: User) -> bool:
    """
    Проверяет подписку на канал.
    Если не подписан - отправляет сообщение с требованием подписаться.
    Возвращает True если подписан, False если нет.
    """
    if user.is_channel_subscriber:
        return True
    
    is_subscribed = await check_channel_subscription(user.telegram_id)
    if is_subscribed:
        user.is_channel_subscriber = True
        await update_user(user)
        return True
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://{CHANNEL_ID.replace('@', 't.me/')}")],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscribe")]
    ])
    
    text = (
        "📢 **Подпишись на наш канал!**\n\n"
        "Чтобы продолжить пользоваться ботом, нужно подписаться на канал Twiqo:\n"
        f"👉 [{CHANNEL_ID}](https://{CHANNEL_ID.replace('@', 't.me/')})\n\n"
        "🔥 **Почему стоит подписаться?**\n"
        "• Первыми узнавать о новостях и обновлениях\n"
        "• Твоя анкета будет выходить на 1 место в поиске\n"
        "• Участие в розыгрышах и конкурсах\n"
        "• Эксклюзивный контент\n\n"
        "После подписки нажми «✅ Проверить подписку»"
    )
    
    await message.answer(text, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
    return False


# ============================================================
# 5. КЛАВИАТУРЫ
# ============================================================

def main_kb(user: User = None):
    buttons = [
        [KeyboardButton(text="🔎 Найти людей"), KeyboardButton(text="❤️ Симпатии")],
        [KeyboardButton(text="👤 Моя анкета"), KeyboardButton(text="⭐ Мои звёзды")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="⭐ PRO"), KeyboardButton(text="❓ Помощь")]
    ]
    if user and user.telegram_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="🛡 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def back_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True)


def rating_kb(profile_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data=f"rate_{profile_id}_1"),
         InlineKeyboardButton(text="2", callback_data=f"rate_{profile_id}_2"),
         InlineKeyboardButton(text="3", callback_data=f"rate_{profile_id}_3"),
         InlineKeyboardButton(text="4", callback_data=f"rate_{profile_id}_4"),
         InlineKeyboardButton(text="5", callback_data=f"rate_{profile_id}_5")],
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip"),
         InlineKeyboardButton(text="🛑 Стоп", callback_data="stop")]
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🛡 Модерация", callback_data="admin_mod")],
        [InlineKeyboardButton(text="🚨 Жалобы", callback_data="admin_reports")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🖼 Приветствие", callback_data="admin_welcome")]
    ])


def mod_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod_ok_{user_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod_no_{user_id}")],
        [InlineKeyboardButton(text="⚠️ На доработку", callback_data=f"mod_work_{user_id}")]
    ])


def chat_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="❌ Завершить чат")],
        [KeyboardButton(text="📱 Поделиться юзом")]
    ], resize_keyboard=True)


def edit_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📸 Фото"), KeyboardButton(text="👤 Имя")],
        [KeyboardButton(text="🎂 Возраст"), KeyboardButton(text="⚧ Пол")],
        [KeyboardButton(text="📍 Город"), KeyboardButton(text="💬 Описание")],
        [KeyboardButton(text="🎮 Интересы")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)


# ============================================================
# 6. FSM
# ============================================================

class RegStates(StatesGroup):
    photo = State()
    name = State()
    age = State()
    gender = State()
    city = State()
    looking = State()
    desc = State()
    interests = State()
    preview = State()


class EditStates(StatesGroup):
    choose = State()
    photo = State()
    name = State()
    age = State()
    gender = State()
    city = State()
    desc = State()
    interests = State()


class AdminStates(StatesGroup):
    broadcast = State()
    broadcast_confirm = State()
    reject_reason = State()
    rework_reason = State()
    welcome_text = State()
    welcome_photo = State()


# ============================================================
# 7. БОТ + НАДЁЖНОЕ ХРАНИЛИЩЕ СОСТОЯНИЙ
# ============================================================

bot = Bot(token=BOT_TOKEN)


class JsonFileStorage(BaseStorage):
    """Сохраняет FSM-состояния в JSON-файл. Устойчиво к перезапускам."""

    def __init__(self, path: str = "fsm_states.json"):
        self.path = path
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки FSM-хранилища: {e}")
            self._data = {}

    async def _save(self):
        async with self._lock:
            try:
                with open(self.path, 'w', encoding='utf-8') as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Ошибка сохранения FSM-хранилища: {e}")

    def _key(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}"

    async def set_state(self, key: StorageKey, state: Optional[str] = None) -> None:
        k = self._key(key)
        if state is None:
            self._data.pop(k, None)
        else:
            state_str = state.state if hasattr(state, 'state') else str(state)
            self._data.setdefault(k, {})['state'] = state_str
        await self._save()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return self._data.get(self._key(key), {}).get('state')

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        k = self._key(key)
        self._data.setdefault(k, {})['data'] = data
        await self._save()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        return self._data.get(self._key(key), {}).get('data', {})

    async def close(self) -> None:
        await self._save()


storage = JsonFileStorage()
dp = Dispatcher(storage=storage)


# ============================================================
# 8. /START
# ============================================================

@dp.message(Command('start'))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    user = await get_user(message.from_user.id)
    welcome = await get_welcome()

    if not user:
        text = welcome.text if welcome else "👋 Добро пожаловать в Twiqo!"
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Создать анкету")]], resize_keyboard=True)
        if welcome and welcome.photo_file_id:
            await message.answer_photo(welcome.photo_file_id, caption=text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        await state.set_state(RegStates.photo)
        await message.answer("📸 Отправь фото", reply_markup=back_kb())
    else:
        is_subscribed = await check_channel_subscription(user.telegram_id)
        if is_subscribed and not user.is_channel_subscriber:
            user.is_channel_subscriber = True
            await update_user(user)
        await message.answer("Главное меню", reply_markup=main_kb(user))


# ============================================================
# 9. РЕГИСТРАЦИЯ
# ============================================================

@dp.message(F.text == "🚀 Создать анкету")
async def create_profile(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if user:
        await message.answer("У тебя уже есть анкета!", reply_markup=main_kb(user))
        return
    await state.set_state(RegStates.photo)
    await message.answer("📸 Отправь фото", reply_markup=back_kb())


@dp.message(RegStates.photo)
async def reg_photo(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Отменено", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Создать анкету")]], resize_keyboard=True))
        return
    if not message.photo:
        await message.answer("Отправь фото", reply_markup=back_kb())
        return
    await state.update_data(photo=message.photo[-1].file_id)
    await state.set_state(RegStates.name)
    await message.answer("👤 Как тебя зовут?", reply_markup=back_kb())


@dp.message(RegStates.name)
async def reg_name(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.photo)
        await message.answer("📸 Отправь фото", reply_markup=back_kb())
        return
    if len(message.text) < 2:
        await message.answer("Слишком коротко", reply_markup=back_kb())
        return
    await state.update_data(name=message.text)
    await state.set_state(RegStates.age)
    await message.answer("🎂 Сколько лет?", reply_markup=back_kb())


@dp.message(RegStates.age)
async def reg_age(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.name)
        await message.answer("👤 Как тебя зовут?", reply_markup=back_kb())
        return
    try:
        age = int(message.text)
        if age < 13 or age > 99:
            await message.answer("13-99", reply_markup=back_kb())
            return
        await state.update_data(age=age)
        await state.set_state(RegStates.gender)
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="👦 Парень"), KeyboardButton(text="👧 Девушка")],
            [KeyboardButton(text="⚪ Другое"), KeyboardButton(text="🤐 Не указывать")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("⚧ Пол?", reply_markup=kb)
    except ValueError:
        await message.answer("Введи число", reply_markup=back_kb())


@dp.message(RegStates.gender)
async def reg_gender(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.age)
        await message.answer("🎂 Сколько лет?", reply_markup=back_kb())
        return
    g_map = {"👦 Парень": "male", "👧 Девушка": "female", "⚪ Другое": "other", "🤐 Не указывать": "not_specified"}
    if message.text not in g_map:
        await message.answer("Выбери из списка", reply_markup=back_kb())
        return
    await state.update_data(gender=g_map[message.text])
    await state.set_state(RegStates.city)
    await message.answer("📍 Город?", reply_markup=back_kb())


@dp.message(RegStates.city)
async def reg_city(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.gender)
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="👦 Парень"), KeyboardButton(text="👧 Девушка")],
            [KeyboardButton(text="⚪ Другое"), KeyboardButton(text="🤐 Не указывать")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("⚧ Пол?", reply_markup=kb)
        return
    if len(message.text) < 2:
        await message.answer("Нормальный город", reply_markup=back_kb())
        return
    await state.update_data(city=message.text)
    await state.set_state(RegStates.looking)
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👦 Парни"), KeyboardButton(text="👧 Девушки")],
        [KeyboardButton(text="👥 Все")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    await message.answer("👥 Кого ищешь?", reply_markup=kb)


@dp.message(RegStates.looking)
async def reg_looking(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.city)
        await message.answer("📍 Город?", reply_markup=back_kb())
        return
    l_map = {"👦 Парни": "male", "👧 Девушки": "female", "👥 Все": "all"}
    if message.text not in l_map:
        await message.answer("Выбери из списка", reply_markup=back_kb())
        return
    await state.update_data(looking=l_map[message.text])
    await state.set_state(RegStates.desc)
    await message.answer("💬 Расскажи о себе", reply_markup=back_kb())


@dp.message(RegStates.desc)
async def reg_desc(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.looking)
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="👦 Парни"), KeyboardButton(text="👧 Девушки")],
            [KeyboardButton(text="👥 Все")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("👥 Кого ищешь?", reply_markup=kb)
        return
    if len(message.text) < 10:
        await message.answer("Минимум 10 символов", reply_markup=back_kb())
        return
    await state.update_data(desc=message.text)
    await state.set_state(RegStates.interests)
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
        [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
        [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
        [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
        [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
        [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    await message.answer("🎮 Интересы:", reply_markup=kb)


@dp.message(RegStates.interests)
async def reg_interests(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.desc)
        await message.answer("💬 Расскажи о себе", reply_markup=back_kb())
        return

    data = await state.get_data()
    interests = data.get('interests', [])
    std = ["🎮 Игры", "🎵 Музыка", "🏋️ Спорт", "💻 IT", "🎬 Фильмы", "📚 Книги", "✈️ Путешествия", "🐾 Животные", "⚽ Футбол", "🏎 Автомобили"]

    if message.text == "✅ Готово":
        if not interests:
            await message.answer("Выбери хотя бы один интерес")
            return
        await state.update_data(interests=interests)
        await state.set_state(RegStates.preview)
        await show_preview(message, state)
        return

    if message.text == "➕ Свой интерес":
        await message.answer("Напиши свой интерес:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))
        return

    if message.text in std:
        name = message.text.split(' ', 1)[-1]
        if name not in interests:
            interests.append(name)
            await state.update_data(interests=interests)
            await message.answer(f"✅ Добавлено: {name}")
        else:
            await message.answer(f"Уже есть: {name}")

        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
            [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
            [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
            [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
            [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("Еще?", reply_markup=kb)
        return

    if len(message.text) >= 2:
        interests.append(message.text)
        await state.update_data(interests=interests)
        await message.answer(f"✅ Добавлено: {message.text}")
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
            [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
            [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
            [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
            [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("Еще?", reply_markup=kb)
    else:
        await message.answer("Слишком коротко")


async def show_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    interests = "\n".join([f"• {i}" for i in data.get('interests', [])])
    text = f"👤 {data.get('name')}, {data.get('age')} лет\n📍 {data.get('city')}\n\n💬 {data.get('desc')}\n\n🎮 {interests}"
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="✅ Отправить")],
        [KeyboardButton(text="✏️ Изменить"), KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    if data.get('photo'):
        await message.answer_photo(data['photo'], caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@dp.message(RegStates.preview)
async def reg_preview_action(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.set_state(RegStates.interests)
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
            [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
            [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
            [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
            [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("🎮 Интересы:", reply_markup=kb)
        return

    if message.text == "✏️ Изменить":
        await state.set_state(RegStates.name)
        await message.answer("👤 Введи имя:", reply_markup=back_kb())
        return

    if message.text == "✅ Отправить":
        data = await state.get_data()

        async with AsyncSessionLocal() as session:
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=data.get('name'),
                age=data.get('age'),
                gender=data.get('gender'),
                city=data.get('city'),
                description=data.get('desc'),
                photo_file_id=data.get('photo'),
                looking_for_gender=data.get('looking'),
                status=ProfileStatus.PENDING
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            for name in data.get('interests', []):
                result = await session.execute(select(Interest).where(Interest.name == name))
                interest = result.scalar_one_or_none()
                if interest:
                    session.add(UserInterest(user_id=user.id, interest_id=interest.id))
            await session.commit()

        await state.clear()
        
        text = (
            "⏳ Анкета отправлена на модерацию!\n\n"
            "📢 **Подпишись на наш канал, чтобы:**\n"
            "• Первым узнать о модерации ✅\n"
            "• Твоя анкета вышла в ТОП 🔥\n"
            "• Следить за новостями и обновлениями\n\n"
            f"👉 [{CHANNEL_ID}](https://{CHANNEL_ID.replace('@', 't.me/')})\n\n"
            "После подписки нажми «✅ Проверить подписку»"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://{CHANNEL_ID.replace('@', 't.me/')}")],
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscribe")]
        ])
        
        await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown", disable_web_page_preview=True)
        await message.answer("📢 Подпишись на канал, чтобы не пропустить новости!", reply_markup=kb)

        for admin in ADMIN_IDS:
            try:
                await bot.send_message(admin, f"🆕 Новая анкета!\n👤 {data.get('name')}, {data.get('age')} лет\n📍 {data.get('city')}")
                if data.get('photo'):
                    await bot.send_photo(admin, data['photo'])
                await bot.send_message(admin, "Действия:", reply_markup=mod_kb(user.id))
            except Exception:
                pass


# ============================================================
# 10. ПРОВЕРКА ПОДПИСКИ (Callback)
# ============================================================

@dp.callback_query(F.data == "check_subscribe")
async def check_subscribe(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала создай анкету!")
        return
    
    is_subscribed = await check_channel_subscription(user.telegram_id)
    
    if is_subscribed:
        user.is_channel_subscriber = True
        await update_user(user)
        await callback.message.edit_text(
            "✅ Спасибо за подписку! Ты в курсе всех новостей Twiqo. 🔥\n\n"
            "Твоя анкета теперь будет выше в поиске! 🚀",
            reply_markup=None
        )
        await callback.answer("✅ Подписка подтверждена!")
        
        if user.status == ProfileStatus.PENDING:
            await callback.message.answer(
                "⏳ Жди модерации анкеты. Мы уведомим тебя!",
                reply_markup=main_kb(user)
            )
        else:
            await callback.message.answer("Главное меню", reply_markup=main_kb(user))
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://{CHANNEL_ID.replace('@', 't.me/')}")],
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscribe")]
        ])
        await callback.message.edit_text(
            "❌ Ты ещё не подписался на канал!\n\n"
            f"👉 Подпишись: [{CHANNEL_ID}](https://{CHANNEL_ID.replace('@', 't.me/')})\n\n"
            "После подписки нажми «✅ Проверить подписку»",
            reply_markup=kb,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        await callback.answer("❌ Подписка не найдена")


# ============================================================
# 11. МОДЕРАЦИЯ
# ============================================================

@dp.callback_query(F.data.startswith('mod_'))
async def mod_action(callback: types.CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    action, user_id = callback.data.split('_')[1], int(callback.data.split('_')[2])

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            await callback.answer("Пользователь не найден")
            return

        if action == 'ok':
            user.status = ProfileStatus.APPROVED
            await session.commit()
            await callback.message.edit_text(f"✅ {user.first_name} одобрен")
            try:
                await bot.send_message(
                    user.telegram_id, 
                    "🎉 Анкета одобрена!\n\n"
                    "Теперь ты можешь искать новых знакомых!\n"
                    "Используй кнопку «🔎 Найти людей» в главном меню.",
                    reply_markup=main_kb(user)
                )
            except Exception:
                pass

        elif action == 'no':
            await callback.message.edit_text(f"❌ Причина отклонения для {user.first_name}:")
            await state.set_state(AdminStates.reject_reason)
            await state.update_data(user_id=user_id)

        elif action == 'work':
            await callback.message.edit_text(f"⚠️ Причина доработки для {user.first_name}:")
            await state.set_state(AdminStates.rework_reason)
            await state.update_data(user_id=user_id)

    await callback.answer()


@dp.message(AdminStates.reject_reason)
async def reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('user_id')
    reason = message.text

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.status = ProfileStatus.REJECTED
            await session.commit()
            try:
                await bot.send_message(
                    user.telegram_id, 
                    f"❌ Анкета отклонена\n\nПричина: {reason}\n\n"
                    "Ты можешь исправить анкету и отправить её снова."
                )
            except Exception:
                pass

    await state.clear()
    await message.answer("✅ Отправлено", reply_markup=admin_kb())


@dp.message(AdminStates.rework_reason)
async def rework_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('user_id')
    reason = message.text

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.status = ProfileStatus.PENDING
            await session.commit()
            try:
                await bot.send_message(
                    user.telegram_id, 
                    f"⚠️ Анкета на доработку\n\nПричина: {reason}\n\n"
                    "Исправь анкету через «👤 Моя анкета» → «✏️ Изменить»"
                )
            except Exception:
                pass

    await state.clear()
    await message.answer("✅ Отправлено", reply_markup=admin_kb())


# ============================================================
# 12. ПОИСК
# ============================================================

@dp.message(F.text == "🔎 Найти людей")
async def find_people(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создай анкету через /start")
        return
    
    if user.status != ProfileStatus.APPROVED:
        status_text = {
            'pending': '⏳ на модерации',
            'rejected': '❌ отклонена',
            'hidden': '👻 скрыта'
        }.get(user.status, 'неизвестен')
        await message.answer(f"❌ Анкета не одобрена. Статус: {status_text}")
        return
    
    # Проверяем подписку на канал
    if not await require_channel_subscription(message, user):
        return
    
    # Сбрасываем FSM состояние
    await state.clear()
    
    await search_next(message, user)


async def search_next(message: types.Message, user: User):
    async with AsyncSessionLocal() as session:
        now_msk = datetime.now(MSK_TZ)
        today = now_msk.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC).replace(tzinfo=None)

        viewed = await session.execute(
            select(ProfileView.profile_id).where(
                ProfileView.viewer_id == user.id,
                ProfileView.viewed_date >= today
            )
        )
        viewed_ids = [v[0] for v in viewed]

        blocked = await session.execute(
            select(Block.blocked_user_id).where(Block.user_id == user.id)
        )
        blocked_ids = [b[0] for b in blocked]

        query = select(User).where(
            User.id != user.id,
            User.status == ProfileStatus.APPROVED,
            User.is_active == True,
            User.id.notin_(viewed_ids) if viewed_ids else True,
            User.id.notin_(blocked_ids) if blocked_ids else True
        )

        if user.looking_for_gender and user.looking_for_gender != 'all':
            query = query.where(User.gender == user.looking_for_gender)

        if user.city:
            query = query.where(User.city == user.city)

        query = query.order_by(User.is_channel_subscriber.desc()).limit(50)
        result = await session.execute(query)
        profiles = result.scalars().all()

        if profiles:
            # Увеличиваем счётчик просмотренных анкет
            user.profiles_viewed_count += 1
            await session.commit()
            
            # Проверяем, не набрал ли пользователь 10 просмотров
            if user.profiles_viewed_count >= 10 and not user.is_channel_subscriber:
                user.profiles_viewed_count = 0
                await session.commit()
                await require_channel_subscription(message, user)
                return
            
            await show_profile(message, user, profiles[0])
            return

        # Расширенный поиск
        query = select(User).where(
            User.id != user.id,
            User.status == ProfileStatus.APPROVED,
            User.is_active == True,
            User.id.notin_(viewed_ids) if viewed_ids else True,
            User.id.notin_(blocked_ids) if blocked_ids else True
        )

        if user.looking_for_gender and user.looking_for_gender != 'all':
            query = query.where(User.gender == user.looking_for_gender)

        if user.age:
            query = query.where(User.age.between(user.age - 2, user.age + 2))

        query = query.order_by(User.is_channel_subscriber.desc()).limit(50)
        result = await session.execute(query)
        profiles = result.scalars().all()

        if profiles:
            user.profiles_viewed_count += 1
            await session.commit()
            
            if user.profiles_viewed_count >= 10 and not user.is_channel_subscriber:
                user.profiles_viewed_count = 0
                await session.commit()
                await require_channel_subscription(message, user)
                return
            
            await show_profile(message, user, profiles[0])
        else:
            await message.answer("😊 Нет новых анкет")


async def show_profile(message: types.Message, user: User, profile: User):
    async with AsyncSessionLocal() as session:
        session.add(ProfileView(viewer_id=user.id, profile_id=profile.id))
        profile.views_count += 1
        session.add(profile)
        await session.commit()

    rating = await get_user_rating(profile.id)
    rating_text = f"⭐ {rating:.1f}" if rating > 0 else "⭐ Нет оценок"

    interests = ""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Interest).join(UserInterest).where(UserInterest.user_id == profile.id)
        )
        ints = result.scalars().all()
        if ints:
            interests = "\n" + "\n".join([f"{i.emoji} {i.name}" for i in ints])

    text = f"👤 {profile.first_name}, {profile.age} лет\n📍 {profile.city or 'Не указан'}\n\n💬 {profile.description or ''}{interests}\n\n{rating_text}"

    if profile.photo_file_id:
        await message.answer_photo(profile.photo_file_id, caption=text, reply_markup=rating_kb(profile.id))
    else:
        await message.answer(text, reply_markup=rating_kb(profile.id))


# ============================================================
# 13. ОЦЕНКИ
# ============================================================

@dp.callback_query(F.data.startswith('rate_'))
async def rate_user(callback: types.CallbackQuery):
    _, profile_id, rating = callback.data.split('_')
    profile_id, rating = int(profile_id), int(rating)

    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала регистрация")
        return

    if user.id == profile_id:
        await callback.answer("Нельзя себя")
        return

    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(Rating).where(
                Rating.from_user_id == user.id,
                Rating.to_user_id == profile_id
            )
        )
        ex = existing.scalar_one_or_none()
        if ex:
            ex.rating = rating
        else:
            session.add(Rating(from_user_id=user.id, to_user_id=profile_id, rating=rating))
        await session.commit()

    await callback.answer(f"✅ {rating}/5")

    if rating >= 4:
        async with AsyncSessionLocal() as session:
            mutual = await session.execute(
                select(Rating).where(
                    Rating.from_user_id == profile_id,
                    Rating.to_user_id == user.id,
                    Rating.rating >= 4
                )
            )
            if mutual.scalar_one_or_none():
                match = Match(user1_id=user.id, user2_id=profile_id)
                session.add(match)
                await session.commit()
                await session.refresh(match)

                chat = TemporaryChat(user1_id=user.id, user2_id=profile_id, match_id=match.id)
                session.add(chat)
                await session.commit()

                for uid in [user.id, profile_id]:
                    try:
                        await bot.send_message(uid, "❤️ Взаимная симпатия!\nЧат начался!", reply_markup=chat_kb())
                    except Exception:
                        pass

    await search_next(callback.message, user)


@dp.callback_query(F.data == 'skip')
async def skip_profile(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if user:
        await search_next(callback.message, user)
    await callback.answer()


@dp.callback_query(F.data == 'stop')
async def stop_search(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    await callback.message.answer("🛑 Поиск остановлен", reply_markup=main_kb(user))
    await callback.answer()


# ============================================================
# 14. РЕДАКТИРОВАНИЕ
# ============================================================

async def edit_start(message: types.Message, state: FSMContext, user: User):
    await message.answer("✏️ Что изменить?", reply_markup=edit_kb())
    await state.set_state(EditStates.choose)


@dp.message(EditStates.choose)
async def edit_choose(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        user = await get_user(message.from_user.id)
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return

    field_map = {
        "📸 Фото": "photo", "👤 Имя": "name", "🎂 Возраст": "age",
        "⚧ Пол": "gender", "📍 Город": "city", "💬 Описание": "desc",
        "🎮 Интересы": "interests"
    }

    if message.text not in field_map:
        await message.answer("Выбери из списка")
        return

    field = field_map[message.text]
    await state.update_data(edit_field=field)

    if field == "gender":
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="👦 Парень"), KeyboardButton(text="👧 Девушка")],
            [KeyboardButton(text="⚪ Другое"), KeyboardButton(text="🤐 Не указывать")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("Новый пол:", reply_markup=kb)
        await state.set_state(EditStates.gender)
    elif field == "interests":
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
            [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
            [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
            [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
            [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("Новые интересы:", reply_markup=kb)
        await state.set_state(EditStates.interests)
    else:
        msgs = {"photo": "📸 Отправь новое фото:", "name": "👤 Введи новое имя:", "age": "🎂 Введи новый возраст:", "city": "📍 Введи новый город:", "desc": "💬 Введи новое описание:"}
        await message.answer(msgs.get(field, "Введи:"), reply_markup=back_kb())
        if field == "photo":
            await state.set_state(EditStates.photo)
        elif field == "name":
            await state.set_state(EditStates.name)
        elif field == "age":
            await state.set_state(EditStates.age)
        elif field == "city":
            await state.set_state(EditStates.city)
        elif field == "desc":
            await state.set_state(EditStates.desc)


@dp.message(EditStates.photo)
async def edit_photo(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return
    if not message.photo:
        await message.answer("Отправь фото", reply_markup=back_kb())
        return
    user = await get_user(message.from_user.id)
    if not user:
        return
    user.photo_file_id = message.photo[-1].file_id
    await update_user(user)
    await state.clear()
    await message.answer("✅ Фото обновлено", reply_markup=main_kb(user))
    await send_to_moderation(user.id, "Фото")


@dp.message(EditStates.name)
async def edit_name(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return
    if len(message.text) < 2:
        await message.answer("Слишком коротко", reply_markup=back_kb())
        return
    user = await get_user(message.from_user.id)
    user.first_name = message.text
    await update_user(user)
    await state.clear()
    await message.answer(f"✅ Имя: {message.text}", reply_markup=main_kb(user))
    await send_to_moderation(user.id, "Имя")


@dp.message(EditStates.age)
async def edit_age(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return
    try:
        age = int(message.text)
        if age < 13 or age > 99:
            await message.answer("13-99", reply_markup=back_kb())
            return
        user = await get_user(message.from_user.id)
        user.age = age
        await update_user(user)
        await state.clear()
        await message.answer(f"✅ Возраст: {age}", reply_markup=main_kb(user))
        await send_to_moderation(user.id, "Возраст")
    except ValueError:
        await message.answer("Введи число", reply_markup=back_kb())


@dp.message(EditStates.gender)
async def edit_gender(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return
    g_map = {"👦 Парень": "male", "👧 Девушка": "female", "⚪ Другое": "other", "🤐 Не указывать": "not_specified"}
    if message.text not in g_map:
        await message.answer("Выбери из списка", reply_markup=back_kb())
        return
    user = await get_user(message.from_user.id)
    user.gender = g_map[message.text]
    await update_user(user)
    await state.clear()
    await message.answer("✅ Пол обновлен", reply_markup=main_kb(user))
    await send_to_moderation(user.id, "Пол")


@dp.message(EditStates.city)
async def edit_city(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return
    if len(message.text) < 2:
        await message.answer("Нормальный город", reply_markup=back_kb())
        return
    user = await get_user(message.from_user.id)
    user.city = message.text
    await update_user(user)
    await state.clear()
    await message.answer(f"✅ Город: {message.text}", reply_markup=main_kb(user))
    await send_to_moderation(user.id, "Город")


@dp.message(EditStates.desc)
async def edit_desc(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return
    if len(message.text) < 10:
        await message.answer("Минимум 10 символов", reply_markup=back_kb())
        return
    user = await get_user(message.from_user.id)
    user.description = message.text
    await update_user(user)
    await state.clear()
    await message.answer("✅ Описание обновлено", reply_markup=main_kb(user))
    await send_to_moderation(user.id, "Описание")


@dp.message(EditStates.interests)
async def edit_interests(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        user = await get_user(message.from_user.id)
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb(user))
        return

    data = await state.get_data()
    interests = data.get('interests', [])
    std = ["🎮 Игры", "🎵 Музыка", "🏋️ Спорт", "💻 IT", "🎬 Фильмы", "📚 Книги", "✈️ Путешествия", "🐾 Животные", "⚽ Футбол", "🏎 Автомобили"]

    if message.text == "✅ Готово":
        user = await get_user(message.from_user.id)
        if not user:
            return

        async with AsyncSessionLocal() as session:
            await session.execute(UserInterest.__table__.delete().where(UserInterest.user_id == user.id))
            for name in interests:
                result = await session.execute(select(Interest).where(Interest.name == name))
                interest = result.scalar_one_or_none()
                if interest:
                    session.add(UserInterest(user_id=user.id, interest_id=interest.id))
            await session.commit()

        await state.clear()
        await message.answer("✅ Интересы обновлены", reply_markup=main_kb(user))
        await send_to_moderation(user.id, "Интересы")
        return

    if message.text == "➕ Свой интерес":
        await message.answer("Напиши свой интерес:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))
        return

    if message.text in std:
        name = message.text.split(' ', 1)[-1]
        if name not in interests:
            interests.append(name)
            await state.update_data(interests=interests)
            await message.answer(f"✅ Добавлено: {name}")
        else:
            await message.answer(f"Уже есть: {name}")

        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
            [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
            [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
            [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
            [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("Еще?", reply_markup=kb)
        return

    if len(message.text) >= 2:
        interests.append(message.text)
        await state.update_data(interests=interests)
        await message.answer(f"✅ Добавлено: {message.text}")
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🏋️ Спорт"), KeyboardButton(text="💻 IT")],
            [KeyboardButton(text="🎬 Фильмы"), KeyboardButton(text="📚 Книги")],
            [KeyboardButton(text="✈️ Путешествия"), KeyboardButton(text="🐾 Животные")],
            [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏎 Автомобили")],
            [KeyboardButton(text="✅ Готово"), KeyboardButton(text="➕ Свой интерес")],
            [KeyboardButton(text="🔙 Назад")]
        ], resize_keyboard=True)
        await message.answer("Еще?", reply_markup=kb)
    else:
        await message.answer("Слишком коротко")


# ============================================================
# 15. МОЯ АНКЕТА
# ============================================================

@dp.message(F.text == "👤 Моя анкета")
async def show_my_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return

    rating = await get_user_rating(user.id)
    count = await get_ratings_count(user.id)

    async with AsyncSessionLocal() as session:
        matches = await session.execute(
            select(func.count(Match.id)).where(
                or_(Match.user1_id == user.id, Match.user2_id == user.id)
            )
        )
        matches_count = matches.scalar() or 0

        result = await session.execute(
            select(Interest).join(UserInterest).where(UserInterest.user_id == user.id)
        )
        interests = result.scalars().all()

    interests_text = "\n".join([f"{i.emoji} {i.name}" for i in interests]) or "Не указаны"
    
    status_text = {
        'pending': '⏳ На модерации',
        'approved': '✅ Одобрена',
        'rejected': '❌ Отклонена',
        'hidden': '👻 Скрыта'
    }.get(user.status, '❓ Неизвестно')

    text = f"👤 {user.first_name}, {user.age} лет\n📍 {user.city}\n📊 Статус: {status_text}\n\n💬 {user.description}\n\n🎮 {interests_text}\n\n⭐ {rating:.1f} ({count} оценок)\n❤️ {matches_count} симпатий\n👁 {user.views_count} просмотров"

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="✏️ Изменить"), KeyboardButton(text="⏸ Скрыть анкету")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)

    if user.photo_file_id:
        await message.answer_photo(user.photo_file_id, caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@dp.message(F.text == "⏸ Скрыть анкету")
async def hide_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        return
    if user.status == ProfileStatus.HIDDEN:
        user.status = ProfileStatus.APPROVED
    else:
        user.status = ProfileStatus.HIDDEN
    await update_user(user)
    await message.answer("✅ Готово", reply_markup=main_kb(user))


@dp.message(F.text == "✏️ Изменить")
async def edit_start_handler(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return
    await edit_start(message, state, user)


# ============================================================
# 16. МОИ СИМПАТИИ, ЗВЁЗДЫ, СТАТИСТИКА
# ============================================================

@dp.message(F.text == "❤️ Симпатии")
async def show_matches(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).join(Match, or_(
                Match.user1_id == user.id, Match.user2_id == user.id
            )).where(Match.status == 'active')
        )
        matches = result.scalars().all()

    if not matches:
        await message.answer("Нет симпатий")
        return

    text = "❤️ Твои симпатии:\n\n"
    for m in matches:
        text += f"👤 {m.first_name}, {m.age} лет\n"
    await message.answer(text)


@dp.message(F.text == "⭐ Мои звёзды")
async def show_my_stars(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return

    rating = await get_user_rating(user.id)
    count = await get_ratings_count(user.id)

    async with AsyncSessionLocal() as session:
        dist = await session.execute(
            select(Rating.rating, func.count(Rating.id))
            .where(Rating.to_user_id == user.id)
            .group_by(Rating.rating)
        )
        dist = {r[0]: r[1] for r in dist}

    text = f"⭐ Рейтинг: {rating:.2f} / 5\n⭐ Оценок: {count}\n\nРаспределение:\n"
    for i in range(5, 0, -1):
        text += f"  {i}⭐ — {dist.get(i, 0)}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Кто оценил", callback_data="show_raters")]
    ])
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == 'show_raters')
async def show_raters(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Нет")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User, Rating.rating)
            .join(Rating, Rating.from_user_id == User.id)
            .where(Rating.to_user_id == user.id)
            .order_by(Rating.rating.desc())
            .limit(20)
        )
        raters = result.all()

    if not raters:
        await callback.message.edit_text("Никто не оценил")
        return

    text = "👁 Тебя оценили:\n\n"
    for r, rating in raters:
        text += f"{rating}⭐ {r.first_name}\n"
    await callback.message.edit_text(text)


@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return

    days = (datetime.now() - user.created_at).days

    async with AsyncSessionLocal() as session:
        matches = await session.execute(
            select(func.count(Match.id)).where(
                or_(Match.user1_id == user.id, Match.user2_id == user.id)
            )
        )
        matches_count = matches.scalar() or 0

        chats = await session.execute(
            select(func.count(TemporaryChat.id)).where(
                or_(TemporaryChat.user1_id == user.id, TemporaryChat.user2_id == user.id)
            )
        )
        chats_count = chats.scalar() or 0

    text = f"📊 Статистика:\n\n👁 Просмотров: {user.views_count}\n⭐ Оценок: {await get_ratings_count(user.id)}\n⭐ Рейтинг: {await get_user_rating(user.id):.2f}\n❤️ Симпатий: {matches_count}\n💬 Чатов: {chats_count}\n📅 Дней: {days}"
    await message.answer(text)


# ============================================================
# 17. НАСТРОЙКИ
# ============================================================

@dp.message(F.text == "⚙️ Настройки")
async def show_settings(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Язык", callback_data="set_lang")],
        [InlineKeyboardButton(text="🚫 Заблокированные", callback_data="set_blocks")],
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data="set_delete")]
    ])
    await message.answer("⚙️ Настройки:", reply_markup=kb)


@dp.callback_query(F.data == 'set_lang')
async def set_lang(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")]
    ])
    await callback.message.edit_text("Выбери язык:", reply_markup=kb)


@dp.callback_query(F.data.startswith('lang_'))
async def change_lang(callback: types.CallbackQuery):
    lang = callback.data.split('_')[1]
    user = await get_user(callback.from_user.id)
    if user:
        user.language = lang
        await update_user(user)
    await callback.message.edit_text(f"✅ Язык: {lang}")
    await callback.answer()


@dp.callback_query(F.data == 'set_blocks')
async def show_blocks(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Нет")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).join(Block, Block.blocked_user_id == User.id)
            .where(Block.user_id == user.id)
        )
        blocks = result.scalars().all()

    if not blocks:
        await callback.message.edit_text("Нет заблокированных")
        return

    text = "🚫 Заблокированные:\n\n"
    for b in blocks:
        text += f"👤 {b.first_name}\n"
    await callback.message.edit_text(text)


@dp.callback_query(F.data == 'set_delete')
async def delete_account(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="del_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_settings")]
    ])
    await callback.message.edit_text("Удалить аккаунт?", reply_markup=kb)


@dp.callback_query(F.data == 'del_confirm')
async def confirm_delete(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if user:
        user.is_active = False
        await update_user(user)
        await callback.message.edit_text("🗑 Аккаунт удален. /start для восстановления")


@dp.callback_query(F.data == 'back_settings')
async def back_settings(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if user:
        await callback.message.edit_text("Главное меню", reply_markup=main_kb(user))
    await callback.answer()


# ============================================================
# 18. АДМИНКА
# ============================================================

@dp.message(F.text == "🛡 Админ-панель")
async def admin_panel(message: types.Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return
    await message.answer("Админ-панель:", reply_markup=admin_kb())


@dp.callback_query(F.data == 'admin_stats')
async def admin_stats(callback: types.CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    async with AsyncSessionLocal() as session:
        total = await session.execute(select(func.count(User.id)))
        total = total.scalar() or 0
        pending = await session.execute(select(func.count(User.id)).where(User.status == ProfileStatus.PENDING))
        pending = pending.scalar() or 0
        approved = await session.execute(select(func.count(User.id)).where(User.status == ProfileStatus.APPROVED))
        approved = approved.scalar() or 0
        matches = await session.execute(select(func.count(Match.id)))
        matches = matches.scalar() or 0
        subscribers = await session.execute(select(func.count(User.id)).where(User.is_channel_subscriber == True))
        subscribers = subscribers.scalar() or 0

    text = f"📊 Статистика:\n👥 Всего: {total}\n🛡 На модерации: {pending}\n✅ Одобрено: {approved}\n❤️ Мэтчей: {matches}\n📢 Подписчиков канала: {subscribers}"
    await callback.message.edit_text(text, reply_markup=admin_kb())


@dp.callback_query(F.data == 'admin_mod')
async def admin_mod(callback: types.CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    async with AsyncSessionLocal() as session:
        pending = await session.execute(select(User).where(User.status == ProfileStatus.PENDING))
        pending = pending.scalars().all()

    if not pending:
        await callback.message.edit_text("Нет анкет на модерации", reply_markup=admin_kb())
        return

    text = "🛡 Анкеты на модерации:\n\n"
    for u in pending:
        text += f"👤 {u.first_name}, {u.age} лет, {u.city}\n"
    await callback.message.edit_text(text, reply_markup=admin_kb())


@dp.callback_query(F.data == 'admin_reports')
async def admin_reports(callback: types.CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    async with AsyncSessionLocal() as session:
        reports = await session.execute(select(Report).where(Report.status == 'pending'))
        reports = reports.scalars().all()

    if not reports:
        await callback.message.edit_text("Нет жалоб", reply_markup=admin_kb())
        return

    text = "🚨 Жалобы:\n\n"
    for r in reports:
        reporter = await get_user_by_id(r.reporter_id)
        reported = await get_user_by_id(r.reported_user_id)
        text += f"От {reporter.first_name if reporter else '?'} на {reported.first_name if reported else '?'}\nПричина: {r.reason}\n\n"
    await callback.message.edit_text(text, reply_markup=admin_kb())


# ============================================================
# 19. АДМИН - РАССЫЛКА
# ============================================================

@dp.callback_query(F.data == 'admin_broadcast')
async def admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    await callback.message.edit_text("📢 Введи сообщение для рассылки:")
    await state.set_state(AdminStates.broadcast)
    await callback.answer()


@dp.message(AdminStates.broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")]
    ])
    await message.answer(f"Отправить?\n\n{message.text}", reply_markup=kb)
    await state.set_state(AdminStates.broadcast_confirm)


@dp.callback_query(F.data == 'broadcast_send')
async def broadcast_send(callback: types.CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    data = await state.get_data()
    text = data.get('text')

    async with AsyncSessionLocal() as session:
        users = await session.execute(select(User.telegram_id).where(User.is_active == True))
        users = users.scalars().all()

    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 {text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await callback.message.edit_text(f"✅ Отправлено {sent} пользователям", reply_markup=admin_kb())
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == 'broadcast_cancel')
async def broadcast_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Отменено", reply_markup=admin_kb())
    await state.clear()
    await callback.answer()


# ============================================================
# 20. АДМИН - ПРИВЕТСТВИЕ
# ============================================================

@dp.callback_query(F.data == 'admin_welcome')
async def admin_welcome(callback: types.CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    welcome = await get_welcome()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="welcome_text")],
        [InlineKeyboardButton(text="📸 Изменить фото", callback_data="welcome_photo")],
        [InlineKeyboardButton(text="🔄 Сбросить", callback_data="welcome_reset")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])

    text = f"🖼 Приветствие\n\n📝 {welcome.text[:50]}...\n📸 {'✅' if welcome.photo_file_id else '❌'}"
    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == 'welcome_text')
async def welcome_text(callback: types.CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    await callback.message.edit_text("📝 Введи новый текст:")
    await state.set_state(AdminStates.welcome_text)
    await callback.answer()


@dp.message(AdminStates.welcome_text)
async def process_welcome_text(message: types.Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return

    async with AsyncSessionLocal() as session:
        welcome = await session.execute(select(WelcomeSettings))
        welcome = welcome.scalar_one_or_none()
        if welcome:
            welcome.text = message.text
            session.add(welcome)
            await session.commit()

    await state.clear()
    await message.answer("✅ Текст обновлен", reply_markup=admin_kb())


@dp.callback_query(F.data == 'welcome_photo')
async def welcome_photo(callback: types.CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    await callback.message.edit_text("📸 Отправь новое фото:")
    await state.set_state(AdminStates.welcome_photo)
    await callback.answer()


@dp.message(AdminStates.welcome_photo, F.photo)
async def process_welcome_photo(message: types.Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return

    async with AsyncSessionLocal() as session:
        welcome = await session.execute(select(WelcomeSettings))
        welcome = welcome.scalar_one_or_none()
        if welcome:
            welcome.photo_file_id = message.photo[-1].file_id
            session.add(welcome)
            await session.commit()

    await state.clear()
    await message.answer("✅ Фото обновлено", reply_markup=admin_kb())


@dp.message(AdminStates.welcome_photo)
async def welcome_photo_err(message: types.Message):
    await message.answer("Отправь фото")


@dp.callback_query(F.data == 'welcome_reset')
async def welcome_reset(callback: types.CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return

    async with AsyncSessionLocal() as session:
        welcome = await session.execute(select(WelcomeSettings))
        welcome = welcome.scalar_one_or_none()
        if welcome:
            welcome.text = "👋 Добро пожаловать в Twiqo!"
            welcome.photo_file_id = None
            session.add(welcome)
            await session.commit()

    await callback.message.edit_text("✅ Сброшено", reply_markup=admin_kb())
    await callback.answer()


@dp.callback_query(F.data == 'admin_back')
async def admin_back(callback: types.CallbackQuery):
    await callback.message.edit_text("Админ-панель:", reply_markup=admin_kb())
    await callback.answer()


# ============================================================
# 21. ПРОЧИЕ КНОПКИ ГЛАВНОГО МЕНЮ
# ============================================================

@dp.message(F.text == "⭐ PRO")
async def pro_handler(message: types.Message):
    await message.answer(
        "⭐ **PRO-статус скоро появится!**\n\n"
        "🔥 Преимущества PRO:\n"
        "• Анкета в ТОПЕ поиска\n"
        "• Безлимитные просмотры\n"
        "• Расширенный поиск\n"
        "• Специальный значок\n\n"
        f"📢 Подпишись на канал, чтобы узнать первым: [{CHANNEL_ID}](https://{CHANNEL_ID.replace('@', 't.me/')})",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


@dp.message(F.text == "❓ Помощь")
async def help_handler(message: types.Message):
    await message.answer(
        "❓ **Помощь**\n\n"
        "1. Создай анкету через /start или кнопку «Создать анкету»\n"
        "2. Ожидай модерации (админ проверит анкету)\n"
        "3. Используй «🔎 Найти людей» для поиска\n"
        "4. Оценивай людей (4-5⭐ для симпатии)\n"
        "5. При взаимной симпатии открывается чат\n"
        "6. Поделись юзом в чате через кнопку\n\n"
        f"📢 Подпишись на канал, чтобы быть в ТОПЕ: [{CHANNEL_ID}](https://{CHANNEL_ID.replace('@', 't.me/')})",
        parse_mode="Markdown"
    )


@dp.message(F.text == "🔙 Назад")
async def back_handler(message: types.Message):
    user = await get_user(message.from_user.id)
    if user:
        await message.answer("Главное меню", reply_markup=main_kb(user))
    else:
        await message.answer("Главное меню", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Создать анкету")]], resize_keyboard=True))


# ============================================================
# 22. ОСНОВНОЙ ОБРАБОТЧИК
# ============================================================

@dp.message()
async def handle_messages(message: types.Message, state: FSMContext):
    # Игнорируем команды
    if message.text and message.text.startswith('/'):
        return

    # Если это кнопка "Найти людей" — обрабатываем отдельно
    if message.text == "🔎 Найти людей":
        await find_people(message, state)
        return

    # Проверяем активное состояние
    current_state = await state.get_state()
    if current_state is not None:
        return

    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return

    # Проверяем активный чат
    async with AsyncSessionLocal() as session:
        chat_result = await session.execute(
            select(TemporaryChat).where(
                or_(
                    TemporaryChat.user1_id == user.id,
                    TemporaryChat.user2_id == user.id
                ),
                TemporaryChat.status == ChatStatus.ACTIVE
            )
        )
        chat = chat_result.scalar_one_or_none()

        if chat:
            partner_id = chat.user2_id if chat.user1_id == user.id else chat.user1_id
            partner = await session.get(User, partner_id)

            if message.text == "❌ Завершить чат":
                chat.status = ChatStatus.FINISHED
                await session.commit()
                await message.answer("Чат завершен", reply_markup=main_kb(user))
                try:
                    await bot.send_message(partner.telegram_id, "Собеседник завершил чат", reply_markup=main_kb(partner))
                except Exception:
                    pass
                return

            if message.text == "📱 Поделиться юзом":
                if user.username:
                    await bot.send_message(partner.telegram_id, f"📱 @{user.username}")
                    await message.answer(f"✅ Отправлено: @{user.username}")
                else:
                    await message.answer("❌ Нет username. Установи в Telegram")
                return

            # Пересылка сообщений в чат
            try:
                if message.text:
                    await bot.send_message(partner.telegram_id, f"💬 {message.text}")
                elif message.photo:
                    await bot.send_photo(partner.telegram_id, message.photo[-1].file_id, caption="📸 Фото")
                elif message.sticker:
                    await bot.send_sticker(partner.telegram_id, message.sticker.file_id)
                elif message.voice:
                    await bot.send_voice(partner.telegram_id, message.voice.file_id)
                elif message.video:
                    await bot.send_video(partner.telegram_id, message.video.file_id)
                elif message.audio:
                    await bot.send_audio(partner.telegram_id, message.audio.file_id)
                elif message.document:
                    await bot.send_document(partner.telegram_id, message.document.file_id)
            except Exception as e:
                logger.error(f"Ошибка отправки в чат: {e}")
                await message.answer("Ошибка отправки сообщения")
            return

        # Если не в чате и не в состоянии, игнорируем неизвестные сообщения
        if message.text and message.text not in ["❤️ Симпатии", "👤 Моя анкета", "⭐ Мои звёзды", "📊 Статистика", "⚙️ Настройки", "⭐ PRO", "❓ Помощь", "🛡 Админ-панель", "✏️ Изменить", "⏸ Скрыть анкету", "🔙 Назад"]:
            await message.answer("Неизвестная команда. Используй меню.", reply_markup=main_kb(user))


# ============================================================
# 23. СБРОС ПРОСМОТРОВ
# ============================================================

async def reset_views():
    while True:
        now = datetime.now(MSK_TZ)
        next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait = (next_day - now).total_seconds()
        await asyncio.sleep(wait)

        async with AsyncSessionLocal() as session:
            await session.execute(ProfileView.__table__.delete())
            await session.commit()
            logger.info("🔄 Просмотры сброшены")


# ============================================================
# 24. ВЕБ-СЕРВЕР ДЛЯ ПИНГА (Render Health Check)
# ============================================================

async def start_web_server():
    try:
        from aiohttp import web
        app = web.Application()
        app.router.add_get('/', lambda request: web.Response(text="OK"))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()
        logger.info("🌐 Web server started on port 8080")
    except Exception as e:
        logger.warning(f"⚠️ Web server не запущен: {e}")


# ============================================================
# 25. ЗАПУСК
# ============================================================

async def main():
    await init_db()
    logger.info("🤖 Бот запущен")
    
    # Запускаем веб-сервер для пинга (если aiohttp установлен)
    try:
        await start_web_server()
    except Exception as e:
        logger.warning(f"⚠️ Веб-сервер не запущен: {e}")
    
    asyncio.create_task(reset_views())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
