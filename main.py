import asyncio
import os
from datetime import datetime, timedelta

import aiosqlite
import dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext

from callbacks import task_cb
from forms import TaskForm
from utils import DB_FILE, init_db, schedule_daily_task_deletion, time_format

dotenv.load_dotenv()

# Bot setup
bot = Bot(token=os.environ["TOKEN"])
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())


# Alert time set command
@dp.message_handler(commands=["set_reminder"])
async def set_reminder(message: types.Message):
    try:
        remind_time = datetime.strptime(message.get_args(), time_format).time()
        async with aiosqlite.connect(DB_FILE) as db:
            remind_time_str = remind_time.strftime(time_format)  # Convert to string
            await db.execute(
                "INSERT INTO users (user_id, remind_time) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET remind_time = ?",
                (message.from_user.id, remind_time_str, remind_time_str),
            )
            await db.commit()
        await message.reply("Your reminder time has been set.")
    except ValueError:
        await message.reply(
            "Please use the correct format HH:MM. For example, /set_reminder 09:30"
        )


# Start command
@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    await message.reply(
        "Welcome! Add tasks by sending me a message in the format 'task 1, task 2, task 3'."
    )


# Adding single task handler
@dp.message_handler(commands=["add_task"], state=None)
async def start_add_task(message: types.Message):
    await TaskForm.task.set()
    await message.reply("Please send me the task.")


# Adding single task
@dp.message_handler(state=TaskForm.task)
async def process_add_task(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data["task"] = message.text
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO tasks (user_id, task) VALUES (?, ?)",
            (message.from_user.id, data["task"]),
        )
        await db.commit()
    await state.finish()
    await message.reply("Task added!")


# Adding tasks
@dp.message_handler(lambda message: "," in message.text)
async def add_tasks(message: types.Message):
    tasks = [task.strip() for task in message.text.split(",")]
    async with aiosqlite.connect(DB_FILE) as db:
        for task in tasks:
            await db.execute(
                "INSERT INTO tasks (user_id, task) VALUES (?, ?)",
                (message.from_user.id, task),
            )
        await db.commit()
    await message.reply("Tasks added!")


# Daily task reminder
async def task_reminder():
    while True:
        now = datetime.now()
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT user_id, remind_time FROM users") as cursor:
                users = await cursor.fetchall()
                for user_id, remind_time_str in users:
                    remind_time = datetime.strptime(remind_time_str, time_format).time()
                    if (
                        now.time() >= remind_time
                        and now.time()
                        < (
                            datetime.combine(datetime.today(), remind_time)
                            + timedelta(minutes=1)
                        ).time()
                    ):
                        await remind_user_tasks(user_id)
        await asyncio.sleep(60)  # Check every minute


async def remind_user_tasks(user_id):
    tasks_message = "Here is your daily task reminder:\n"

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT id, task FROM tasks WHERE user_id = ? AND completed = 0", (user_id,)
        ) as cursor:
            tasks = await cursor.fetchall()
            if tasks:
                task_idx = 1
                for task_id, task in tasks:  # task id for callback if needed later
                    tasks_message += f"Task {task_idx}: {task}\n"
                    task_idx += 1

                await bot.send_message(user_id, tasks_message)


# TODO: show completed tasks separatly
@dp.message_handler(commands=["tasks"])
async def show_tasks(message: types.Message):
    tasks_message = "Your Tasks:\n"
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    has_tasks = False

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT id, task, completed FROM tasks WHERE user_id = ?",
            (message.from_user.id,),
        ) as cursor:
            buttons = []
            task_idx = 1
            async for row in cursor:
                task_id, task, completed = row
                status = "✅" if completed else "❌"
                tasks_message += f"{status} Task {task_idx}: {task}\n"

                if not completed:
                    button = types.InlineKeyboardButton(
                        f"{task_idx}",
                        callback_data=task_cb.new(id=task_id, action="done"),
                    )
                    buttons.append(button)
                    has_tasks = True

                task_idx += 1

            while buttons:
                row = buttons[: keyboard.row_width]
                keyboard.row(*row)
                buttons = buttons[keyboard.row_width :]

    if not has_tasks:
        tasks_message = "You have no tasks!"
        await message.reply(tasks_message)
    else:
        await message.reply(tasks_message, reply_markup=keyboard)


# Marking tasks as completed
@dp.callback_query_handler(task_cb.filter(action="done"))
async def complete_task(query: types.CallbackQuery, callback_data: dict):
    task_id = callback_data["id"]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
        await db.commit()

    await bot.send_message(query.from_user.id, "Task marked as completed!")
    await query.answer("Task marked as completed!")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    loop.create_task(schedule_daily_task_deletion())
    loop.create_task(task_reminder())
    executor.start_polling(dp, skip_updates=True)
