from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
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
            return len(self.coordinator.data.get("tasks", []))
        return 0


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
            return {
                "note_id": self.note_id,
                "content": note.get("content", ""),
                "updated": note.get("updatedAt", ""),
                "created": note.get("createdAt", ""),
            }
        return {"note_id": self.note_id, "content": ""}

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
            return {
                "checklist_id": self.checklist_id,
                "title": checklist.get("title", self._title),
                "type": checklist.get("type", "simple"),
                "items": items,
                "flat_items": flatten_items(items),
                "completed": completed,
                "total": total,
                "completion_rate": round((completed / total * 100) if total > 0 else 0, 1),
                "updated": checklist.get("updatedAt", ""),
                "created": checklist.get("createdAt", ""),
            }
        return {"checklist_id": self.checklist_id, "title": self._title, "items": [], "flat_items": []}

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
        }

    @property
    def available(self):
        return self._get_task() is not None

    def _get_task(self):
        for task in self.coordinator.data.get("tasks", []):
            if task["id"] == self.task_id:
                return task
        return None
