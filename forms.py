from aiogram.dispatcher.filters.state import State, StatesGroup


class TaskAddForm(StatesGroup):
    task = State()


class TaskMenuForm(StatesGroup):
    editing = State()
    confirming_deletion = State()


class AlertAddForm(StatesGroup):
    alert = State()
    time = State()
