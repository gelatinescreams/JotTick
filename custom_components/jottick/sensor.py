from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from datetime import datetime, timedelta
import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def flatten_items(items, prefix=""):
    result = []
    for i, item in enumerate(items):
        index_path = f"{prefix}{i}" if prefix == "" else f"{prefix}.{i}"
        flat_item = {**item, "index_path": index_path}
        result.append(flat_item)
        if item.get("children"):
            result.extend(flatten_items(item["children"], index_path))
    return result


def count_items_recursive(items):
    count = 0
    for item in items:
        count += 1
        if item.get("children"):
            count += count_items_recursive(item["children"])
    return count


def count_completed_recursive(items):
    count = 0
    for item in items:
        if item.get("completed", False) or item.get("status") == "completed":
            count += 1
        if item.get("children"):
            count += count_completed_recursive(item["children"])
    return count


def get_items_with_due_dates(items, today_str=None):
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    result = []
    flat = flatten_items(items)
    for item in flat:
        due_date = item.get("dueDate")
        if due_date:
            is_overdue = due_date < today_str
            is_completed = item.get("completed", False) or item.get("status") == "completed"
            result.append({
                "text": item.get("text", ""),
                "index_path": item.get("index_path"),
                "dueDate": due_date,
                "dueTime": item.get("dueTime"),
                "notifyOverdue": item.get("notifyOverdue", False),
                "isOverdue": is_overdue and not is_completed,
                "isCompleted": is_completed,
            })
    return result


def count_overdue_items(items, today_str=None):
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    count = 0
    for item in items:
        due_date = item.get("dueDate")
        is_completed = item.get("completed", False) or item.get("status") == "completed"
        if due_date and due_date < today_str and not is_completed:
            count += 1
        if item.get("children"):
            count += count_overdue_items(item["children"], today_str)
    return count


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    sensors = [
        JotTickSensor(coordinator, "total_notes", "Total Notes", "mdi:note-text"),
        JotTickSensor(coordinator, "total_checklists", "Total Checklists", "mdi:format-list-checks"),
        JotTickSensor(coordinator, "total_items", "Total Items", "mdi:checkbox-marked"),
        JotTickSensor(coordinator, "completed_items", "Completed Items", "mdi:check-all"),
        JotTickSensor(coordinator, "pending_items", "Pending Items", "mdi:clock-outline"),
        JotTickSensor(coordinator, "completion_rate", "Completion Rate", "mdi:percent"),
        JotTickSensor(coordinator, "total_tasks", "Total Tasks", "mdi:clipboard-list"),
        JotTickSensor(coordinator, "overdue_items", "Overdue Items", "mdi:alert-circle"),
        JotTickSensor(coordinator, "imported_events", "Imported Events", "mdi:calendar-import"),
        JotTickCalendarEventsSensor(coordinator, hass),
    ]
    
    async_add_entities(sensors)
    
    if "entity_ids" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entity_ids"] = set()

    await _update_entities(hass, entry, coordinator, async_add_entities)
    
    def handle_update():
        hass.async_create_task(_update_entities(hass, entry, coordinator, async_add_entities))
    
    entry.async_on_unload(coordinator.async_add_listener(handle_update))


async def _update_entities(hass, entry, coordinator, async_add_entities):
    notes = coordinator.data.get("notes", [])
    checklists = coordinator.data.get("checklists", [])
    tasks = coordinator.data.get("tasks", [])
    
    current_ids = {f"note_{n['id']}" for n in notes}
    current_ids.update({f"list_{c['id']}" for c in checklists})
    current_ids.update({f"task_{t['id']}" for t in tasks})
    
    tracked_ids = hass.data[DOMAIN][entry.entry_id]["entity_ids"]
    
    new_ids = current_ids - tracked_ids
    if new_ids:
        new_entities = []
        
        for note in notes:
            if f"note_{note['id']}" in new_ids:
                new_entities.append(JotTickNoteSensor(coordinator, note['id'], note['title']))
        
        for checklist in checklists:
            if f"list_{checklist['id']}" in new_ids:
                new_entities.append(JotTickChecklistSensor(coordinator, checklist['id'], checklist['title']))
        
        for task in tasks:
            if f"task_{task['id']}" in new_ids:
                new_entities.append(JotTickTaskSensor(coordinator, task['id'], task['title']))
        
        if new_entities:
            async_add_entities(new_entities)
            tracked_ids.update(new_ids)


class JotTickSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, sensor_type, name, icon):
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._attr_name = f"JotTick {name}"
        self._attr_unique_id = f"jottick_{sensor_type}"
        self._attr_icon = icon

    @property
    def native_value(self):
        notes = self.coordinator.data.get("notes", [])
        checklists = self.coordinator.data.get("checklists", [])
        tasks = self.coordinator.data.get("tasks", [])
        
        if self._sensor_type == "total_notes":
            return len(notes)
        elif self._sensor_type == "total_checklists":
            return len(checklists)
        elif self._sensor_type == "total_items":
            total = 0
            for checklist in checklists:
                total += count_items_recursive(checklist.get("items", []))
            return total
        elif self._sensor_type == "completed_items":
            completed = 0
            for checklist in checklists:
                completed += count_completed_recursive(checklist.get("items", []))
            return completed
        elif self._sensor_type == "pending_items":
            total = 0
            completed = 0
            for checklist in checklists:
                items = checklist.get("items", [])
                total += count_items_recursive(items)
                completed += count_completed_recursive(items)
            return total - completed
        elif self._sensor_type == "completion_rate":
            total = 0
            completed = 0
            for checklist in checklists:
                items = checklist.get("items", [])
                total += count_items_recursive(items)
                completed += count_completed_recursive(items)
            return round((completed / total * 100) if total > 0 else 0, 1)
        elif self._sensor_type == "total_tasks":
            return len(tasks)
        elif self._sensor_type == "overdue_items":
            today_str = datetime.now().strftime("%Y-%m-%d")
            overdue = 0
            for checklist in checklists:
                overdue += count_overdue_items(checklist.get("items", []), today_str)
            for task in tasks:
                overdue += count_overdue_items(task.get("items", []), today_str)
            return overdue
        elif self._sensor_type == "imported_events":
            return len(self.coordinator.data.get("imported_events", []))
        return 0

    @property
    def extra_state_attributes(self):
        if self._sensor_type == "overdue_items":
            today_str = datetime.now().strftime("%Y-%m-%d")
            overdue_list = []
            
            for checklist in self.coordinator.data.get("checklists", []):
                due_items = get_items_with_due_dates(checklist.get("items", []), today_str)
                for item in due_items:
                    if item["isOverdue"]:
                        overdue_list.append({
                            **item,
                            "parent_type": "list",
                            "parent_id": checklist["id"],
                            "parent_title": checklist.get("title", ""),
                        })
            
            for task in self.coordinator.data.get("tasks", []):
                due_items = get_items_with_due_dates(task.get("items", []), today_str)
                for item in due_items:
                    if item["isOverdue"]:
                        overdue_list.append({
                            **item,
                            "parent_type": "task",
                            "parent_id": task["id"],
                            "parent_title": task.get("title", ""),
                        })
            
            return {
                "overdue_items": overdue_list,
                "count": len(overdue_list),
            }
        
        elif self._sensor_type == "imported_events":
            return {
                "sources": self.coordinator.data.get("ical_sources", []),
                "event_count": len(self.coordinator.data.get("imported_events", [])),
            }
        
        return None


class JotTickNoteSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, note_id, title):
        super().__init__(coordinator)
        self.note_id = note_id
        self._title = title
        self._attr_name = f"JotTick Note: {title}"
        self._attr_unique_id = f"jottick_note_{note_id}"
        self._attr_icon = "mdi:note-text"

    @property
    def native_value(self):
        note = self._get_note()
        return note['title'] if note else self._title

    @property
    def extra_state_attributes(self):
        note = self._get_note()
        if note:
            images = note.get("images", [])
            return {
                "note_id": self.note_id,
                "content": note.get("content", ""),
                "images": images,
                "image_count": len(images),
                "has_images": len(images) > 0,
                "updated": note.get("updatedAt", ""),
                "created": note.get("createdAt", ""),
            }
        return {
            "note_id": self.note_id,
            "content": "",
            "images": [],
            "image_count": 0,
            "has_images": False,
        }

    @property
    def available(self):
        return self._get_note() is not None

    def _get_note(self):
        for note in self.coordinator.data.get("notes", []):
            if note["id"] == self.note_id:
                return note
        return None


class JotTickChecklistSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, checklist_id, title):
        super().__init__(coordinator)
        self.checklist_id = checklist_id
        self._title = title
        self._attr_name = f"JotTick List: {title}"
        self._attr_unique_id = f"jottick_list_{checklist_id}"
        self._attr_icon = "mdi:format-list-checks"

    @property
    def native_value(self):
        checklist = self._get_checklist()
        if checklist:
            items = checklist.get("items", [])
            completed = count_completed_recursive(items)
            total = count_items_recursive(items)
            return f"{completed}/{total}"
        return "0/0"

    @property
    def extra_state_attributes(self):
        checklist = self._get_checklist()
        if checklist:
            items = checklist.get("items", [])
            completed = count_completed_recursive(items)
            total = count_items_recursive(items)
            today_str = datetime.now().strftime("%Y-%m-%d")
            due_items = get_items_with_due_dates(items, today_str)
            overdue = count_overdue_items(items, today_str)
            
            return {
                "checklist_id": self.checklist_id,
                "title": checklist.get("title", self._title),
                "type": checklist.get("type", "simple"),
                "items": items,
                "flat_items": flatten_items(items),
                "completed": completed,
                "total": total,
                "completion_rate": round((completed / total * 100) if total > 0 else 0, 1),
                "due_items": due_items,
                "overdue_count": overdue,
                "updated": checklist.get("updatedAt", ""),
                "created": checklist.get("createdAt", ""),
            }
        return {
            "checklist_id": self.checklist_id, 
            "title": self._title, 
            "items": [], 
            "flat_items": [],
            "due_items": [],
            "overdue_count": 0,
        }

    @property
    def available(self):
        return self._get_checklist() is not None

    def _get_checklist(self):
        for checklist in self.coordinator.data.get("checklists", []):
            if checklist["id"] == self.checklist_id:
                return checklist
        return None


class JotTickTaskSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, task_id, title):
        super().__init__(coordinator)
        self.task_id = task_id
        self._title = title
        self._attr_name = f"JotTick Task: {title}"
        self._attr_unique_id = f"jottick_task_{task_id}"
        self._attr_icon = "mdi:clipboard-list"

    @property
    def native_value(self):
        task = self._get_task()
        if task:
            items = task.get("items", [])
            completed = self._count_by_status(items, "completed")
            total = count_items_recursive(items)
            return f"{completed}/{total}"
        return "0/0"

    def _count_by_status(self, items, status):
        count = 0
        for item in items:
            if item.get("status") == status:
                count += 1
            if item.get("children"):
                count += self._count_by_status(item["children"], status)
        return count

    @property
    def extra_state_attributes(self):
        task = self._get_task()
        if task:
            items = task.get("items", [])
            raw_statuses = task.get("statuses", [])
            
            statuses = []
            for i, status in enumerate(raw_statuses):
                if isinstance(status, dict):
                    statuses.append({
                        "id": status.get("id"),
                        "name": status.get("label", status.get("id")),
                        "color": status.get("color"),
                        "order": status.get("order", i)
                    })
            
            if not statuses:
                statuses = [
                    {"id": "todo", "name": "To Do", "order": 0, "color": "#6b7280"},
                    {"id": "in_progress", "name": "In Progress", "order": 1, "color": "#3b82f6"},
                    {"id": "completed", "name": "Completed", "order": 2, "color": "#10b981"}
                ]
            
            status_counts = {s["id"]: self._count_by_status(items, s["id"]) for s in statuses}
            total = count_items_recursive(items)
            completed = self._count_by_status(items, "completed")
            today_str = datetime.now().strftime("%Y-%m-%d")
            due_items = get_items_with_due_dates(items, today_str)
            overdue = count_overdue_items(items, today_str)
            
            return {
                "task_id": self.task_id,
                "title": task.get("title", self._title),
                "items": items,
                "flat_items": flatten_items(items),
                "statuses": statuses,
                "status_counts": status_counts,
                "todo": self._count_by_status(items, "todo"),
                "in_progress": self._count_by_status(items, "in_progress"),
                "completed": completed,
                "total": total,
                "completion_rate": round((completed / total * 100) if total > 0 else 0, 1),
                "due_items": due_items,
                "overdue_count": overdue,
                "updated": task.get("updatedAt", ""),
                "created": task.get("createdAt", ""),
            }
        return {
            "task_id": self.task_id,
            "title": self._title,
            "items": [],
            "flat_items": [],
            "statuses": [
                {"id": "todo", "name": "To Do", "order": 0, "color": "#6b7280"},
                {"id": "in_progress", "name": "In Progress", "order": 1, "color": "#3b82f6"},
                {"id": "completed", "name": "Completed", "order": 2, "color": "#10b981"}
            ],
            "due_items": [],
            "overdue_count": 0,
        }

    @property
    def available(self):
        return self._get_task() is not None

    def _get_task(self):
        for task in self.coordinator.data.get("tasks", []):
            if task["id"] == self.task_id:
                return task
        return None


class JotTickCalendarEventsSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, hass):
        super().__init__(coordinator)
        self._hass = hass
        self._attr_name = "JotTick Calendar Events"
        self._attr_unique_id = "jottick_calendar_events"
        self._attr_icon = "mdi:calendar"
        self._colors = {
            'note_created': '#9CCAEB',
            'note_edited': '#6BA5D4',
            'note_reminder': '#F7A700',
            'list_created': '#99C66D',
            'list_edited': '#7AAD52',
            'list_due': '#10B981',
            'task_created': '#8B5CF6',
            'task_edited': '#7C3AED',
            'task_due': '#F9E900',
            'overdue': '#EF4444',
            'completed': '#6B7280',
            'recurring': '#22C55E',
            'reminder': '#F59E0B',
            'imported': '#8B5CF6',
        }

    @property
    def native_value(self):
        events = self._compute_all_events()
        return sum(len(v) for v in events.values())

    @property
    def extra_state_attributes(self):
        all_events = self._compute_all_events()
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        first_of_month = today.replace(day=1)
        if today.month == 12:
            last_of_next_month = today.replace(year=today.year + 1, month=2, day=1) - timedelta(days=1)
        elif today.month == 11:
            last_of_next_month = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_of_next_month = today.replace(month=today.month + 2, day=1) - timedelta(days=1)

        start_str = first_of_month.strftime("%Y-%m-%d")
        end_str = last_of_next_month.strftime("%Y-%m-%d")

        filtered_events = {
            date: evts for date, evts in all_events.items()
            if start_str <= date <= end_str
        }

        return {
            "events": filtered_events,
            "dates_with_events": list(filtered_events.keys()),
            "today": today_str,
            "last_updated": datetime.now().isoformat(),
            "total_events": sum(len(v) for v in all_events.values()),
        }

    def _get_color(self, color_input_entity: str, default_key: str) -> str:
        try:
            state = self._hass.states.get(color_input_entity)
            if state and state.state and state.state not in ('unknown', 'unavailable'):
                return state.state
        except Exception:
            pass
        return self._colors.get(default_key, '#666666')

    def _get_toggle(self, toggle_entity: str, default: bool = True) -> bool:
        try:
            state = self._hass.states.get(toggle_entity)
            if state:
                return state.state == 'on'
        except Exception:
            pass
        return default

    def _add_event(self, events: dict, date: str, event: dict):
        if not date:
            return
        if date not in events:
            events[date] = []
        events[date].append(event)

    def _compute_all_events(self) -> dict:
        events = {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        show_note_created = self._get_toggle('input_boolean.jottick_calendar_show_note_created', True)
        show_note_edited = self._get_toggle('input_boolean.jottick_calendar_show_note_edited', True)
        show_note_reminders = self._get_toggle('input_boolean.jottick_calendar_show_note_reminders', True)
        show_list_created = self._get_toggle('input_boolean.jottick_calendar_show_list_created', True)
        show_list_edited = self._get_toggle('input_boolean.jottick_calendar_show_list_edited', True)
        show_list_due = self._get_toggle('input_boolean.jottick_calendar_show_list_due', True)
        show_task_due = self._get_toggle('input_boolean.jottick_calendar_show_task_due', True)
        show_imported = self._get_toggle('input_boolean.jottick_calendar_show_imported', True)
        show_overdue = self._get_toggle('input_boolean.jottick_calendar_show_overdue', True)
        show_completed = self._get_toggle('input_boolean.jottick_calendar_show_completed', False)
        color_note_created = self._get_color('input_text.jottick_calendar_color_note_created', 'note_created')
        color_note_edited = self._get_color('input_text.jottick_calendar_color_note_edited', 'note_edited')
        color_note_reminder = self._get_color('input_text.jottick_calendar_color_note_reminder', 'note_reminder')
        color_list_created = self._get_color('input_text.jottick_calendar_color_list_created', 'list_created')
        color_list_edited = self._get_color('input_text.jottick_calendar_color_list_edited', 'list_edited')
        color_list_due = self._get_color('input_text.jottick_calendar_color_list_due', 'list_due')
        color_task_created = self._colors['task_created']
        color_task_edited = self._colors['task_edited']
        color_task_due = self._get_color('input_text.jottick_calendar_color_task_due', 'task_due')
        color_overdue = self._get_color('input_text.jottick_calendar_color_task_overdue', 'overdue')
        color_completed = self._get_color('input_text.jottick_calendar_color_completed', 'completed')
        color_recurring = self._colors['recurring']
        color_reminder = self._colors['reminder']
        color_imported = self._get_color('input_text.jottick_calendar_color_imported', 'imported')
        notes = self.coordinator.data.get("notes", [])
        for note in notes:
            note_id = note.get("id", "")
            note_title = note.get("title", "Untitled Note")
            created = note.get("createdAt", "")
            updated = note.get("updatedAt", "")
            if show_note_created and created:
                cdate = created[:10]
                self._add_event(events, cdate, {
                    'type': 'note_created',
                    'color': color_note_created,
                    'title': note_title,
                    'date': cdate,
                    'item_id': note_id,
                    'item_type': 'note'
                })
            if show_note_edited and updated and created and updated[:10] != created[:10]:
                udate = updated[:10]
                self._add_event(events, udate, {
                    'type': 'note_edited',
                    'color': color_note_edited,
                    'title': note_title,
                    'date': udate,
                    'item_id': note_id,
                    'item_type': 'note'
                })
        if show_note_reminders:
            try:
                sched_sensor = self._hass.states.get('sensor.jottick_scheduled_notes')
                if sched_sensor and sched_sensor.attributes:
                    scheduled_notes = sched_sensor.attributes.get('schedules', {})
                    if isinstance(scheduled_notes, dict):
                        for sched_id, sched in scheduled_notes.items():
                            sched_time = sched.get('scheduled_time', '')
                            if sched_time and 'T' in sched_time:
                                sched_date = sched_time[:10]
                                sched_time_only = sched_time[11:16] if len(sched_time) > 11 else ''
                                note_id = sched.get('note_id', '')
                                note_title = 'Scheduled Note'
                                for n in notes:
                                    if n.get('id') == note_id:
                                        note_title = n.get('title', note_title)
                                        break
                                self._add_event(events, sched_date, {
                                    'type': 'note_reminder',
                                    'color': color_note_reminder,
                                    'title': note_title,
                                    'date': sched_date,
                                    'time': sched_time_only,
                                    'item_id': note_id,
                                    'item_type': 'note'
                                })
            except Exception as e:
                _LOGGER.debug(f"Error getting scheduled notes: {e}")
        checklists = self.coordinator.data.get("checklists", [])
        for checklist in checklists:
            list_id = checklist.get("id", "")
            list_title = checklist.get("title", "Untitled List")
            created = checklist.get("createdAt", "")
            updated = checklist.get("updatedAt", "")
            items = checklist.get("items", [])
            if show_list_created and created:
                cdate = created[:10]
                self._add_event(events, cdate, {
                    'type': 'list_created',
                    'color': color_list_created,
                    'title': list_title,
                    'date': cdate,
                    'item_id': list_id,
                    'item_type': 'list'
                })
            if show_list_edited and updated and created and updated[:10] != created[:10]:
                udate = updated[:10]
                self._add_event(events, udate, {
                    'type': 'list_edited',
                    'color': color_list_edited,
                    'title': list_title + ' (edited)',
                    'date': udate,
                    'item_id': list_id,
                    'item_type': 'list'
                })
            if show_list_due:
                due_items = get_items_with_due_dates(items, today_str)
                for item in due_items:
                    ddate = item.get('dueDate', '')
                    if ddate:
                        is_overdue = item.get('isOverdue', False)
                        is_completed = item.get('isCompleted', False)
                        if is_completed and not show_completed:
                            continue
                        if is_overdue and not is_completed and not show_overdue:
                            continue
                        if is_completed:
                            evt_type = 'list_completed'
                            evt_color = color_completed
                        elif is_overdue:
                            evt_type = 'list_overdue'
                            evt_color = color_overdue
                        else:
                            evt_type = 'list_due'
                            evt_color = color_list_due
                        status_icon = '' if is_completed else ('' if is_overdue else '')
                        self._add_event(events, ddate, {
                            'type': evt_type,
                            'color': evt_color,
                            'title': status_icon + item.get('text', ''),
                            'time': item.get('dueTime', ''),
                            'date': ddate,
                            'parent_title': list_title,
                            'item_id': list_id,
                            'item_type': 'list',
                            'is_completed': is_completed
                        })
        tasks = self.coordinator.data.get("tasks", [])
        for task in tasks:
            task_id = task.get("id", "")
            task_title = task.get("title", "Untitled Task")
            created = task.get("createdAt", "")
            updated = task.get("updatedAt", "")
            items = task.get("items", [])
            if show_list_created and created:
                cdate = created[:10]
                self._add_event(events, cdate, {
                    'type': 'task_created',
                    'color': color_task_created,
                    'title': task_title,
                    'date': cdate,
                    'item_id': task_id,
                    'item_type': 'task'
                })
            if show_list_edited and updated and created and updated[:10] != created[:10]:
                udate = updated[:10]
                self._add_event(events, udate, {
                    'type': 'task_edited',
                    'color': color_task_edited,
                    'title': task_title + ' (edited)',
                    'date': udate,
                    'item_id': task_id,
                    'item_type': 'task'
                })
            if show_task_due:
                due_items = get_items_with_due_dates(items, today_str)
                for item in due_items:
                    ddate = item.get('dueDate', '')
                    if ddate:
                        is_overdue = item.get('isOverdue', False)
                        is_completed = item.get('isCompleted', False)
                        if is_completed and not show_completed:
                            continue
                        if is_overdue and not is_completed and not show_overdue:
                            continue
                        if is_completed:
                            evt_type = 'task_completed'
                            evt_color = color_completed
                        elif is_overdue:
                            evt_type = 'task_overdue'
                            evt_color = color_overdue
                        else:
                            evt_type = 'task_due'
                            evt_color = color_task_due
                        status_icon = '' if is_completed else ('' if is_overdue else '')
                        self._add_event(events, ddate, {
                            'type': evt_type,
                            'color': evt_color,
                            'title': status_icon + item.get('text', ''),
                            'time': item.get('dueTime', ''),
                            'date': ddate,
                            'parent_title': task_title,
                            'item_id': task_id,
                            'item_type': 'task',
                            'is_completed': is_completed
                        })
        try:
            rec_sensor = self._hass.states.get('sensor.jottick_recurring')
            if rec_sensor and rec_sensor.attributes:
                recurring_configs = rec_sensor.attributes.get('configs', {})
                if isinstance(recurring_configs, dict):
                    today_dow = datetime.now().weekday()
                    is_weekday = today_dow < 5
                    is_weekend = today_dow >= 5
                    for item_id, config in recurring_configs.items():
                        reset_times = config.get('reset_times', [])
                        days_setting = config.get('days', 'every_day')
                        applies_today = (
                            days_setting == 'every_day' or
                            (days_setting == 'weekdays' and is_weekday) or
                            (days_setting == 'weekends' and is_weekend)
                        )
                        if applies_today and reset_times:
                            item_title = 'Recurring Reset'
                            for c in checklists:
                                if c.get('id') == item_id:
                                    item_title = c.get('title', item_title)
                                    break
                            else:
                                for t in tasks:
                                    if t.get('id') == item_id:
                                        item_title = t.get('title', item_title)
                                        break
                            times_str = ', '.join(reset_times) if isinstance(reset_times, list) else str(reset_times)
                            self._add_event(events, today_str, {
                                'type': 'recurring',
                                'color': color_recurring,
                                'title': item_title,
                                'date': today_str,
                                'time': times_str,
                                'item_id': item_id,
                                'description': 'Resets: ' + times_str
                            })
        except Exception as e:
            _LOGGER.debug(f"Error getting recurring configs: {e}")
        try:
            rem_sensor = self._hass.states.get('sensor.jottick_reminders')
            if rem_sensor and rem_sensor.attributes:
                reminder_configs = rem_sensor.attributes.get('configs', {})
                if isinstance(reminder_configs, dict):
                    today_dow = datetime.now().weekday()
                    is_weekday = today_dow < 5
                    is_weekend = today_dow >= 5
                    for item_id, config in reminder_configs.items():
                        days_setting = config.get('days', 'weekdays')
                        applies_today = (
                            days_setting == 'every_day' or
                            (days_setting == 'weekdays' and is_weekday) or
                            (days_setting == 'weekends' and is_weekend)
                        )
                        if applies_today:
                            item_title = 'Reminder'
                            for c in checklists:
                                if c.get('id') == item_id:
                                    item_title = c.get('title', item_title)
                                    break
                            else:
                                for t in tasks:
                                    if t.get('id') == item_id:
                                        item_title = t.get('title', item_title)
                                        break
                            interval = config.get('interval', '1 hour')
                            start_time = config.get('start', '09:00')
                            end_time = config.get('end', '21:00')
                            self._add_event(events, today_str, {
                                'type': 'reminder',
                                'color': color_reminder,
                                'title': item_title,
                                'date': today_str,
                                'time': start_time + '-' + end_time,
                                'item_id': item_id,
                                'description': 'Every ' + interval
                            })
        except Exception as e:
            _LOGGER.debug(f"Error getting reminder configs: {e}")
        if show_imported:
            imported_events = self.coordinator.data.get("imported_events", [])
            for evt in imported_events:
                evt_date = evt.get('date', '')
                if evt_date:
                    self._add_event(events, evt_date, {
                        'type': 'imported',
                        'color': color_imported,
                        'title': evt.get('title', 'Imported Event'),
                        'date': evt_date,
                        'time': evt.get('time', ''),
                        'location': evt.get('location', ''),
                        'item_type': 'imported',
                        'item_id': evt.get('id', '')
                    })
        return events
