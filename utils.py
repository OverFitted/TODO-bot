import asyncio
import aiosqlite

DB_FILE = "tasks.db"
time_format = "%H:%M"


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, user_id INTEGER, task TEXT, completed INTEGER DEFAULT 0)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, remind_time TIME)"
        )
        await db.commit()


async def schedule_daily_task_deletion():
    while True:
        await delete_completed_tasks()
        await asyncio.sleep(86400)  # 86400 seconds = 24 hours


async def delete_completed_tasks():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM tasks WHERE completed = 1")
        await db.commit()
