import asyncio
import itertools
import os
from datetime import datetime, timedelta

import aiosqlite
import dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext

from callbacks import alert_cb, task_cb
from forms import AlertAddForm, TaskAddForm, TaskMenuForm
from utils import (
    DB_FILE,
    init_db,
    schedule_daily_alert_deletion,
    schedule_daily_task_deletion,
    time_format,
)

dotenv.load_dotenv()

# Bot setup
bot = Bot(token=os.environ["TOKEN"])
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())


async def fetch_tasks(user_id: int):
    tasks_message = "Your tasks:\n"
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    has_tasks = False

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT id, task, completed FROM tasks WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            buttons = []
            task_idx = 1
            async for row in cursor:
                task_id, task, completed = row
                status = "✅" if completed else "❌"
                tasks_message += f"{status} Task {task_idx}: {task}\n"

                if not completed:
                    button = types.InlineKeyboardButton(
                        f"Task {task_idx}",
                        callback_data=task_cb.new(
                            id=task_id, action="open_menu", menu_action=""
                        ),
                    )
                    buttons.append(button)
                    has_tasks = True

                task_idx += 1

            while buttons:
                row = buttons[: keyboard.row_width]
                keyboard.row(*row)
                buttons = buttons[keyboard.row_width :]

    return has_tasks, tasks_message, keyboard


async def fetch_alerts(user_id: int):
    alerts_message = "Your alerts:\n"
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    has_alerts = False

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT id, alert, alert_time, completed FROM alerts WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            buttons = []
            alert_idx = 1
            async for row in cursor:
                alert_id, alert_time, alert, completed = row
                status = "✅" if completed else "❌"
                alerts_message += (
                    f"{status} Alert {alert_idx}: {alert} ({alert_time})\n"
                )

                if not completed:
                    button = types.InlineKeyboardButton(
                        f"Alert {alert_idx}",
                        callback_data=alert_cb.new(
                            id=alert_id, time=alert_time, action="open_menu"
                        ),
                    )
                    buttons.append(button)
                    has_alerts = True

                alert_idx += 1

            while buttons:
                row = buttons[: keyboard.row_width]
                keyboard.row(*row)
                buttons = buttons[keyboard.row_width :]

    return has_alerts, alerts_message, keyboard


# Alert time set command
@dp.message_handler(commands=["set_alarm_time"])
async def set_alarm_time(message: types.Message):
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
            "Please use the correct format HH:MM. For example, /set_alarm_time 09:30"
        )


# Add alert
@dp.message_handler(commands=["add_alert"], state=None)
async def add_alert(message: types.Message):
    await AlertAddForm.alert.set()
    await message.reply("Please send me the alert description.")


# Add alert
@dp.message_handler(state=AlertAddForm.alert)
async def add_alert_time(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data["alert"] = message.text

    await AlertAddForm.time.set()
    await message.reply(
        "Please send me the alert time in format HH:MM. For example, 09:30"
    )


# Adding single task
@dp.message_handler(state=AlertAddForm.time)
async def process_add_alert(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data["time"] = message.text

    async with aiosqlite.connect(DB_FILE) as db:
        alert_time = datetime.strptime(data["time"], time_format).time()
        alert_time_str = alert_time.strftime(time_format)  # Convert to string
        await db.execute(
            "INSERT INTO alerts (user_id, alert, alert_time) VALUES (?, ?, ?)",
            (message.from_user.id, data["alert"], alert_time_str),
        )
        await db.commit()
    await state.finish()
    await message.reply("Alert added!")


# Start command
@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    await message.reply(
        "Welcome! Add tasks by sending me a message in the format 'task 1, task 2, task 3'."
    )


# Adding single task handler
@dp.message_handler(commands=["add_task"], state=None)
async def start_add_task(message: types.Message):
    await TaskAddForm.task.set()
    await message.reply("Please send me the task.")


# Adding single task
@dp.message_handler(state=TaskAddForm.task)
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


async def remind_user_tasks(user_id: int):
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


async def remind_user_alert(user_id: int, alert_id: int, alert: str, alert_time: str):
    alert_message = f"You've got new alert for {alert_time}: {alert}"
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    # FIXME: ValueError: Symbol ':' is defined as the separator and can't be used in parts' values
    alert_time_fix = alert_time.replace(":", "-")
    keyboard.add(
        types.InlineKeyboardButton(
            "✅",
            callback_data=alert_cb.new(id=alert_id, time=alert_time_fix, action="done"),
        ),
        types.InlineKeyboardButton(
            "💤",
            callback_data=alert_cb.new(id=alert_id, time=alert_time_fix, action="snooze"),
        ),
    )
    await bot.send_message(user_id, alert_message, reply_markup=keyboard)


# TODO: show completed tasks separatly
@dp.message_handler(commands=["tasks"])
async def show_tasks(message: types.Message):
    has_tasks, tasks_message, keyboard = await fetch_tasks(user_id=message.from_user.id)

    if not has_tasks:
        tasks_message = "You have no tasks!"
        await message.reply(tasks_message)
    else:
        await message.reply(tasks_message, reply_markup=keyboard)


@dp.callback_query_handler(task_cb.filter(action="show_tasks"))
async def back_to_tasks(query: types.CallbackQuery, callback_data: dict):
    has_tasks, tasks_message, keyboard = await fetch_tasks(user_id=query.from_user.id)

    if not has_tasks:
        tasks_message = "You have no tasks!"
        await query.message.edit_text(tasks_message)
    else:
        await query.message.edit_text(tasks_message, reply_markup=keyboard)

    task_count = len(
        list(itertools.chain.from_iterable(list(keyboard.values.values())[0]))
    )
    await query.answer(
        f"You have {'no' if not has_tasks else task_count} task{'s' if task_count > 1 else ''}!"
    )


# TODO: show completed alerts separatly
@dp.message_handler(commands=["alerts"])
async def show_alerts(message: types.Message):
    has_alerts, alerts_message, keyboard = await fetch_alerts(
        user_id=message.from_user.id
    )

    if not has_alerts:
        alerts_message = "You have no alerts!"
        await message.reply(alerts_message)
    else:
        await message.reply(alerts_message, reply_markup=keyboard)


@dp.callback_query_handler(alert_cb.filter(action="show_alerts"))
async def back_to_alerts(query: types.CallbackQuery, callback_data: dict):
    has_alerts, alerts_message, keyboard = await fetch_alerts(
        user_id=query.from_user.id
    )

    if not has_alerts:
        alerts_message = "You have no alerts!"
        await query.message.edit_text(alerts_message)
    else:
        await query.message.edit_text(alerts_message, reply_markup=keyboard)

    alert_count = len(
        list(itertools.chain.from_iterable(list(keyboard.values.values())[0]))
    )
    await query.answer(
        f"You have {'no' if not has_alerts else alert_count} alert{'s' if alert_count > 1 else ''}!"
    )


# Marking tasks as completed
@dp.callback_query_handler(task_cb.filter(action="done"))
async def complete_task(query: types.CallbackQuery, callback_data: dict):
    task_id = callback_data["id"]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
        await db.commit()

    await bot.send_message(query.from_user.id, "Task marked as completed!")

    has_tasks, tasks_message, keyboard = await fetch_tasks(user_id=query.from_user.id)

    if not has_tasks:
        tasks_message = "You have no tasks!"
        await query.message.edit_text(tasks_message)
    else:
        await query.message.edit_text(tasks_message, reply_markup=keyboard)

    await query.answer("Task marked as completed!")


# Openning task menu
@dp.callback_query_handler(task_cb.filter(action="open_menu"))
async def task_menu(query: types.CallbackQuery, callback_data: dict):
    task_id = callback_data["id"]
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        types.InlineKeyboardButton(
            "✅",
            callback_data=task_cb.new(
                id=task_id, action="done", menu_action="mark_done"
            ),
        ),
        types.InlineKeyboardButton(
            "✏️",
            callback_data=task_cb.new(id=task_id, action="edit", menu_action="edit"),
        ),
        types.InlineKeyboardButton(
            "🗑",
            callback_data=task_cb.new(
                id=task_id, action="delete", menu_action="delete"
            ),
        ),
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 Back",
            callback_data=task_cb.new(id=task_id, action="show_tasks", menu_action=""),
        )
    )

    await query.message.edit_text(
        "Select an action for the task:", reply_markup=keyboard
    )


# Editing tasks
@dp.callback_query_handler(task_cb.filter(menu_action="edit"))
async def start_editing_task(
    query: types.CallbackQuery, callback_data: dict, state: FSMContext
):
    await TaskMenuForm.editing.set()
    await state.update_data(task_id=callback_data["id"])
    await query.message.reply("Please send the new task text.")


@dp.message_handler(state=TaskMenuForm.editing)
async def process_task_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    new_text = message.text

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE tasks SET task = ? WHERE id = ?", (new_text, task_id))
        await db.commit()

    await state.finish()
    await message.reply("Task updated!")


# Deleting tasks
@dp.callback_query_handler(task_cb.filter(menu_action="delete"))
async def confirm_delete_task(
    query: types.CallbackQuery, callback_data: dict, state: FSMContext
):
    await TaskMenuForm.confirming_deletion.set()
    await state.update_data(task_id=callback_data["id"])
    await query.message.reply("Are you sure you want to delete this task? Yes/No")


@dp.message_handler(state=TaskMenuForm.confirming_deletion)
async def delete_task(message: types.Message, state: FSMContext):
    confirmation = message.text.lower()
    if confirmation in ["yes", "y"]:
        data = await state.get_data()
        task_id = data["task_id"]

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            await db.commit()

        await message.reply("Task deleted!")
    else:
        await message.reply("Task deletion cancelled.")

    await state.finish()


# Daily task reminder
async def daily_reminder():
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

            async with db.execute(
                "SELECT user_id, id, alert, alert_time FROM alerts"
            ) as alert_cursor:
                alerts = await alert_cursor.fetchall()
                for user_id, alert_id, alert, alert_time_str in alerts:
                    alert_time = datetime.strptime(alert_time_str, time_format).time()
                    if (
                        now.time() >= alert_time
                        and now.time()
                        < (
                            datetime.combine(datetime.today(), alert_time)
                            + timedelta(minutes=1)
                        ).time()
                    ) or True:
                        await remind_user_alert(
                            user_id, alert_id, alert, alert_time_str
                        )

        await asyncio.sleep(60)  # Check every minute


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    loop.create_task(schedule_daily_task_deletion())
    loop.create_task(schedule_daily_alert_deletion())
    loop.create_task(daily_reminder())

    executor.start_polling(dp, skip_updates=True)
