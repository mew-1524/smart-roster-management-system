from django.contrib import admin

from . import models

for m in (models.Pod, models.Employee, models.Brand, models.Process, models.OperatingRule, models.Assignment,
          models.Holiday, models.Leave, models.CompOff, models.RosterPeriod, models.RosterEntry,
          models.RosterAssignment, models.Conflict, models.ActivityLog, models.RosterSettings):
    admin.site.register(m)
