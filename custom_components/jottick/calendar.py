from datetime import datetime, date, timedelta
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
import logging
import re

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        JotTickNoteCreatedCalendar(coordinator, entry, hass),
        JotTickNoteEditedCalendar(coordinator, entry, hass),
        JotTickNoteRemindersCalendar(coordinator, entry, hass),
        JotTickListCreatedCalendar(coordinator, entry, hass),
        JotTickListEditedCalendar(coordinator, entry, hass),
        JotTickListDueDatesCalendar(coordinator, entry, hass),
        JotTickTaskDueDatesCalendar(coordinator, entry, hass),
    ]

    ical_sources = coordinator.data.get("ical_sources", [])
    for source in ical_sources:
        entities.append(JotTickICalCalendar(coordinator, entry, source, hass))

    async_add_entities(entities)

    if "ical_calendar_entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["ical_calendar_entities"] = {}

    for source in ical_sources:
        hass.data[DOMAIN][entry.entry_id]["ical_calendar_entities"][source["url"]] = True

    @callback
    def handle_ical_update():
        current_sources = coordinator.data.get("ical_sources", [])
        current_urls = {s["url"] for s in current_sources}
        tracked_urls = set(hass.data[DOMAIN][entry.entry_id]["ical_calendar_entities"].keys())

        new_urls = current_urls - tracked_urls
        if new_urls:
            new_entities = []
            for source in current_sources:
                if source["url"] in new_urls:
                    new_entities.append(JotTickICalCalendar(coordinator, entry, source, hass))
                    hass.data[DOMAIN][entry.entry_id]["ical_calendar_entities"][source["url"]] = True
            if new_entities:
                async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(handle_ical_update))


def get_items_with_due_dates_flat(items, parent_path=""):
    result = []
    for i, item in enumerate(items):
        index_path = f"{parent_path}{i}" if not parent_path else f"{parent_path}.{i}"
        if item.get("dueDate"):
            result.append({
                "text": item.get("text", ""),
                "dueDate": item.get("dueDate"),
                "dueTime": item.get("dueTime"),
                "completed": item.get("completed", False),
                "status": item.get("status", ""),
                "index_path": index_path,
            })
        if item.get("children"):
            result.extend(get_items_with_due_dates_flat(item["children"], f"{index_path}."))
    return result


def get_toggle(hass, entity_id, default=True):
    state = hass.states.get(entity_id)
    if state:
        return state.state == "on"
    return default


class BaseJotTickCalendar(CoordinatorEntity, CalendarEntity):

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator)
        self._entry = entry
        self._hass = hass

    @property
    def event(self) -> CalendarEvent | None:
        try:
            events = self._get_events_list()
            if not events:
                return None
            now = dt_util.now()
            today = now.date()
            for evt in sorted(events, key=lambda e: e.start.date() if isinstance(e.start, datetime) else e.start):
                evt_date = evt.start.date() if isinstance(evt.start, datetime) else evt.start
                if evt_date >= today:
                    return evt
            return events[0] if events else None
        except Exception as e:
            _LOGGER.error(f"Error in {self.__class__.__name__}.event: {e}")
            return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        try:
            events = self._get_events_list()
            start_d = start_date.date() if isinstance(start_date, datetime) else start_date
            end_d = end_date.date() if isinstance(end_date, datetime) else end_date

            filtered = []
            for evt in events:
                evt_start = evt.start.date() if isinstance(evt.start, datetime) else evt.start
                evt_end = evt.end.date() if isinstance(evt.end, datetime) else evt.end
                if evt_end >= start_d and evt_start <= end_d:
                    filtered.append(evt)

            return sorted(filtered, key=lambda e: e.start.date() if isinstance(e.start, datetime) else e.start)
        except Exception as e:
            _LOGGER.error(f"Error in {self.__class__.__name__}.async_get_events: {e}")
            return []

    def _get_events_list(self) -> list[CalendarEvent]:
        raise NotImplementedError


class JotTickNoteCreatedCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_note_created"
        self._attr_name = "JotTick Note Created"
        self._attr_icon = "mdi:note-plus"
        self._toggle_entity = "input_boolean.jottick_calendar_show_note_created"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        events = []
        notes = self.coordinator.data.get("notes", [])

        for note in notes:
            note_id = note.get("id", "")
            note_title = note.get("title", "Untitled Note")
            created = note.get("createdAt", "")

            if created:
                try:
                    cdate = datetime.strptime(created[:10], "%Y-%m-%d").date()
                    events.append(CalendarEvent(
                        summary=f"Note created: {note_title}",
                        start=cdate,
                        end=cdate + timedelta(days=1),
                        uid=f"note_created_{note_id}",
                    ))
                except ValueError:
                    pass

        return events


class JotTickNoteEditedCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_note_edited"
        self._attr_name = "JotTick Note Edited"
        self._attr_icon = "mdi:note-edit"
        self._toggle_entity = "input_boolean.jottick_calendar_show_note_edited"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        events = []
        notes = self.coordinator.data.get("notes", [])

        for note in notes:
            note_id = note.get("id", "")
            note_title = note.get("title", "Untitled Note")
            created = note.get("createdAt", "")
            updated = note.get("updatedAt", "")

            if updated and created and updated[:10] != created[:10]:
                try:
                    udate = datetime.strptime(updated[:10], "%Y-%m-%d").date()
                    events.append(CalendarEvent(
                        summary=f"Note edited: {note_title}",
                        start=udate,
                        end=udate + timedelta(days=1),
                        uid=f"note_edited_{note_id}",
                    ))
                except ValueError:
                    pass

        return events


class JotTickNoteRemindersCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_note_reminders"
        self._attr_name = "JotTick Note Reminders"
        self._attr_icon = "mdi:bell"
        self._toggle_entity = "input_boolean.jottick_calendar_show_note_reminders"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        events = []
        notes = self.coordinator.data.get("notes", [])
        note_lookup = {n.get("id"): n for n in notes}

        sched_sensor = self._hass.states.get("sensor.jottick_scheduled_notes")
        if sched_sensor and sched_sensor.attributes:
            schedules = sched_sensor.attributes.get("schedules", {})
            if isinstance(schedules, dict):
                for sched_id, sched in schedules.items():
                    sched_time = sched.get("scheduled_time", "")
                    if not sched_time:
                        continue

                    try:
                        if "T" in sched_time:
                            dt_obj = datetime.fromisoformat(sched_time.replace("Z", "+00:00"))
                            dt_local = dt_util.as_local(dt_obj)
                            end_dt = dt_local + timedelta(minutes=30)
                        else:
                            dt_obj = datetime.strptime(sched_time[:10], "%Y-%m-%d").date()
                            dt_local = dt_obj
                            end_dt = dt_obj + timedelta(days=1)
                    except ValueError:
                        continue

                    note_id = sched.get("note_id", "")
                    note = note_lookup.get(note_id, {})
                    title = sched.get("title", note.get("title", "Scheduled Note"))
                    message = sched.get("message", note.get("content", ""))

                    events.append(CalendarEvent(
                        summary=title,
                        start=dt_local,
                        end=end_dt,
                        description=message,
                        uid=f"note_sched_{sched_id}",
                    ))

        return events


class JotTickListCreatedCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_list_created"
        self._attr_name = "JotTick List Created"
        self._attr_icon = "mdi:playlist-plus"
        self._toggle_entity = "input_boolean.jottick_calendar_show_list_created"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        events = []
        checklists = self.coordinator.data.get("checklists", [])

        for checklist in checklists:
            checklist_id = checklist.get("id", "")
            checklist_title = checklist.get("title", "Untitled List")
            created = checklist.get("createdAt", "")

            if created:
                try:
                    cdate = datetime.strptime(created[:10], "%Y-%m-%d").date()
                    events.append(CalendarEvent(
                        summary=f"List created: {checklist_title}",
                        start=cdate,
                        end=cdate + timedelta(days=1),
                        uid=f"list_created_{checklist_id}",
                    ))
                except ValueError:
                    pass

        return events


class JotTickListEditedCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_list_edited"
        self._attr_name = "JotTick List Edited"
        self._attr_icon = "mdi:playlist-edit"
        self._toggle_entity = "input_boolean.jottick_calendar_show_list_edited"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        events = []
        checklists = self.coordinator.data.get("checklists", [])

        for checklist in checklists:
            checklist_id = checklist.get("id", "")
            checklist_title = checklist.get("title", "Untitled List")
            created = checklist.get("createdAt", "")
            updated = checklist.get("updatedAt", "")

            if updated and created and updated[:10] != created[:10]:
                try:
                    udate = datetime.strptime(updated[:10], "%Y-%m-%d").date()
                    events.append(CalendarEvent(
                        summary=f"List edited: {checklist_title}",
                        start=udate,
                        end=udate + timedelta(days=1),
                        uid=f"list_edited_{checklist_id}",
                    ))
                except ValueError:
                    pass

        return events


class JotTickListDueDatesCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_list_due"
        self._attr_name = "JotTick List Due Dates"
        self._attr_icon = "mdi:calendar-check"
        self._toggle_entity = "input_boolean.jottick_calendar_show_list_due"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        show_completed = get_toggle(self._hass, "input_boolean.jottick_calendar_show_completed", False)
        show_overdue = get_toggle(self._hass, "input_boolean.jottick_calendar_show_overdue", True)

        events = []
        today = date.today()
        checklists = self.coordinator.data.get("checklists", [])

        for checklist in checklists:
            checklist_id = checklist.get("id", "")
            checklist_title = checklist.get("title", "Untitled List")
            items = checklist.get("items", [])

            due_items = get_items_with_due_dates_flat(items)
            for item in due_items:
                is_completed = item.get("completed", False)
                if is_completed and not show_completed:
                    continue

                due_date_str = item.get("dueDate")
                due_time_str = item.get("dueTime")

                if due_date_str is not None and not isinstance(due_date_str, str):
                    due_date_str = str(due_date_str)

                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                is_overdue = due_date < today and not is_completed
                if is_overdue and not show_overdue:
                    continue

                item_text = item.get("text", "")
                if is_completed:
                    summary = f"✓ {item_text}"
                elif is_overdue:
                    summary = f"⚠ {item_text}"
                else:
                    summary = item_text

                if due_time_str:
                    try:
                        start_dt = datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
                        start_dt = dt_util.as_local(start_dt.replace(tzinfo=None))
                        end_dt = start_dt + timedelta(hours=1)
                        events.append(CalendarEvent(
                            summary=summary,
                            start=start_dt,
                            end=end_dt,
                            description=f"From list: {checklist_title}",
                            uid=f"list_{checklist_id}_{item.get('index_path', '')}",
                        ))
                    except ValueError:
                        events.append(CalendarEvent(
                            summary=summary,
                            start=due_date,
                            end=due_date + timedelta(days=1),
                            description=f"From list: {checklist_title}",
                            uid=f"list_{checklist_id}_{item.get('index_path', '')}",
                        ))
                else:
                    events.append(CalendarEvent(
                        summary=summary,
                        start=due_date,
                        end=due_date + timedelta(days=1),
                        description=f"From list: {checklist_title}",
                        uid=f"list_{checklist_id}_{item.get('index_path', '')}",
                    ))

        return events


class JotTickTaskDueDatesCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_calendar_task_due"
        self._attr_name = "JotTick Task Due Dates"
        self._attr_icon = "mdi:calendar-clock"
        self._toggle_entity = "input_boolean.jottick_calendar_show_task_due"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        show_completed = get_toggle(self._hass, "input_boolean.jottick_calendar_show_completed", False)
        show_overdue = get_toggle(self._hass, "input_boolean.jottick_calendar_show_overdue", True)

        events = []
        today = date.today()
        tasks = self.coordinator.data.get("tasks", [])

        for task in tasks:
            task_id = task.get("id", "")
            task_title = task.get("title", "Untitled Task")
            items = task.get("items", [])

            due_items = get_items_with_due_dates_flat(items)
            for item in due_items:
                status = item.get("status", "todo")
                is_completed = status == "completed"
                if is_completed and not show_completed:
                    continue

                due_date_str = item.get("dueDate")
                due_time_str = item.get("dueTime")

                if due_date_str is not None and not isinstance(due_date_str, str):
                    due_date_str = str(due_date_str)

                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                is_overdue = due_date < today and not is_completed
                if is_overdue and not show_overdue:
                    continue

                item_text = item.get("text", "")
                if is_completed:
                    summary = f"✓ {item_text}"
                elif is_overdue:
                    summary = f"⚠ {item_text}"
                else:
                    summary = item_text

                if due_time_str:
                    try:
                        start_dt = datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
                        start_dt = dt_util.as_local(start_dt.replace(tzinfo=None))
                        end_dt = start_dt + timedelta(hours=1)
                        events.append(CalendarEvent(
                            summary=summary,
                            start=start_dt,
                            end=end_dt,
                            description=f"From task: {task_title}\nStatus: {status}",
                            uid=f"task_{task_id}_{item.get('index_path', '')}",
                        ))
                    except ValueError:
                        events.append(CalendarEvent(
                            summary=summary,
                            start=due_date,
                            end=due_date + timedelta(days=1),
                            description=f"From task: {task_title}\nStatus: {status}",
                            uid=f"task_{task_id}_{item.get('index_path', '')}",
                        ))
                else:
                    events.append(CalendarEvent(
                        summary=summary,
                        start=due_date,
                        end=due_date + timedelta(days=1),
                        description=f"From task: {task_title}\nStatus: {status}",
                        uid=f"task_{task_id}_{item.get('index_path', '')}",
                    ))

        return events


class JotTickICalCalendar(BaseJotTickCalendar):

    def __init__(self, coordinator, entry: ConfigEntry, source: dict, hass: HomeAssistant):
        super().__init__(coordinator, entry, hass)
        self._source = source
        self._source_url = source.get("url", "")
        self._source_name = source.get("name", "Imported")

        safe_name = re.sub(r"[^a-z0-9_]", "_", self._source_name.lower())
        safe_name = re.sub(r"_+", "_", safe_name).strip("_")

        self._attr_unique_id = f"{entry.entry_id}_calendar_ical_{source.get('id', safe_name)}"
        self._attr_name = f"JotTick iCal {self._source_name}"
        self._attr_icon = "mdi:calendar-import"
        self._toggle_entity = "input_boolean.jottick_calendar_show_imported"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return get_toggle(self._hass, self._toggle_entity, True)

    def _get_events_list(self) -> list[CalendarEvent]:
        if not get_toggle(self._hass, self._toggle_entity, True):
            return []

        events = []
        imported_events = self.coordinator.data.get("imported_events", [])

        for evt in imported_events:
            if evt.get("source_url") != self._source_url:
                continue

            evt_date_str = evt.get("date", "")
            if not evt_date_str:
                continue

            evt_time_str = evt.get("time", "")
            evt_end_time_str = evt.get("end_time", "")

            try:
                evt_date = datetime.strptime(evt_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            if evt_time_str:
                try:
                    start_dt = datetime.strptime(f"{evt_date_str} {evt_time_str}", "%Y-%m-%d %H:%M")
                    start_dt = dt_util.as_local(start_dt.replace(tzinfo=None))

                    if evt_end_time_str:
                        end_dt = datetime.strptime(f"{evt_date_str} {evt_end_time_str}", "%Y-%m-%d %H:%M")
                        end_dt = dt_util.as_local(end_dt.replace(tzinfo=None))
                        if end_dt <= start_dt:
                            end_dt = start_dt + timedelta(hours=1)
                    else:
                        end_dt = start_dt + timedelta(hours=1)

                    events.append(CalendarEvent(
                        summary=evt.get("title", "Imported Event"),
                        start=start_dt,
                        end=end_dt,
                        description=evt.get("description", ""),
                        location=evt.get("location", ""),
                        uid=evt.get("id", evt.get("original_uid", "")),
                    ))
                except ValueError:
                    events.append(CalendarEvent(
                        summary=evt.get("title", "Imported Event"),
                        start=evt_date,
                        end=evt_date + timedelta(days=1),
                        description=evt.get("description", ""),
                        location=evt.get("location", ""),
                        uid=evt.get("id", evt.get("original_uid", "")),
                    ))
            else:
                events.append(CalendarEvent(
                    summary=evt.get("title", "Imported Event"),
                    start=evt_date,
                    end=evt_date + timedelta(days=1),
                    description=evt.get("description", ""),
                    location=evt.get("location", ""),
                    uid=evt.get("id", evt.get("original_uid", "")),
                ))

        return events

    @property
    def available(self) -> bool:
        sources = self.coordinator.data.get("ical_sources", [])
        return any(s.get("url") == self._source_url for s in sources)
