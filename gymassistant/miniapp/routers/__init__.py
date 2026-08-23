"""HTTP-роуты, по модулю на раздел."""
from miniapp.routers import (
    catalog, exercises, notifications, profile, programs, rest, schedule, training,
)

# Порядок важен ровно в одном: статика в main.py монтируется после всех api-роутов.
all_routers = (
    schedule.router,
    training.router,
    rest.router,
    programs.router,
    exercises.router,
    catalog.router,
    profile.router,
    notifications.router,
)
