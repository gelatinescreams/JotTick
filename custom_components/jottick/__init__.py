import logging
import uuid
import os
import base64
import shutil
import json
import re
import aiohttp
from datetime import datetime, timedelta
from typing import Any, Optional

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

IMAGES_DIR = "www/jottick/images"
CALENDAR_DIR = "www/jottick/calendar"


def generate_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_ical_datetime(dt_value) -> tuple:
    if dt_value is None:
        return None, None
    
    if hasattr(dt_value, 'dt'):
        dt_value = dt_value.dt
    
    if isinstance(dt_value, datetime):
        return dt_value.strftime("%Y-%m-%d"), dt_value.strftime("%H:%M")
    elif hasattr(dt_value, 'strftime'):
        return dt_value.strftime("%Y-%m-%d"), None
    
    return None, None


def generate_ical_uid(item_type: str, item_id: str) -> str:
    return f"{item_type}-{item_id}@jottick"


def escape_ical_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def format_ical_datetime(date_str: str, time_str: str = None) -> str:
    if time_str:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.strftime("%Y%m%dT%H%M%S")
    else:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y%m%d")


class JotTickUploadView(HomeAssistantView):
    url = "/api/jottick/upload"
    name = "api:jottick:upload"
    requires_auth = True
    
    def __init__(self, coordinator):
        self.coordinator = coordinator
    
    async def post(self, request):
        try:
            data = await request.post()
            
            note_id = data.get('note_id')
            if not note_id:
                return web.json_response({"success": False, "error": "note_id required"}, status=400)
            
            file_field = data.get('file')
            if not file_field:
                return web.json_response({"success": False, "error": "file required"}, status=400)
            
            filename = file_field.filename
            content = file_field.file.read()
            
            ext = os.path.splitext(filename)[1].lower() or '.jpg'
            allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
            if ext not in allowed_extensions:
                return web.json_response({"success": False, "error": "Invalid file type"}, status=400)
            
            image_id = generate_id()[:8]
            safe_filename = f"{note_id}_{image_id}{ext}"
            
            images_path = self.coordinator.hass.config.path(IMAGES_DIR)
            if not os.path.exists(images_path):
                os.makedirs(images_path, exist_ok=True)
            
            file_path = os.path.join(images_path, safe_filename)
            
            def write_file():
                with open(file_path, 'wb') as f:
                    f.write(content)
            
            await self.coordinator.hass.async_add_executor_job(write_file)
            
            image_url = f"/local/jottick/images/{safe_filename}"
            image_record = {
                "id": image_id,
                "filename": safe_filename,
                "url": image_url,
                "caption": "",
                "addedAt": now_iso()
            }
            
            for note in self.coordinator._data["notes"]:
                if note["id"] == note_id:
                    if "images" not in note:
                        note["images"] = []
                    note["images"].append(image_record)
                    note["updated"] = now_iso()
                    break
            
            await self.coordinator.async_save()
            await self.coordinator.async_request_refresh()
            
            return web.json_response({
                "success": True,
                "image": image_record
            })
            
        except Exception as e:
            _LOGGER.error(f"Upload error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)


class JotTickDeleteImageView(HomeAssistantView):
    url = "/api/jottick/delete-image"
    name = "api:jottick:delete_image"
    requires_auth = True
    
    def __init__(self, coordinator):
        self.coordinator = coordinator
    
    async def post(self, request):
        try:
            data = await request.json()
            note_id = data.get('note_id')
            image_id = data.get('image_id')
            
            if not note_id or not image_id:
                return web.json_response({"success": False, "error": "note_id and image_id required"}, status=400)
            
            for note in self.coordinator._data["notes"]:
                if note["id"] == note_id:
                    if "images" not in note:
                        return web.json_response({"success": False, "error": "No images"}, status=404)
                    
                    for img in note["images"]:
                        if img["id"] == image_id:
                            file_path = self.coordinator.hass.config.path(IMAGES_DIR, img["filename"])
                            if os.path.exists(file_path):
                                await self.coordinator.hass.async_add_executor_job(os.remove, file_path)
                            
                            note["images"].remove(img)
                            note["updated"] = now_iso()
                            
                            await self.coordinator.async_save()
                            await self.coordinator.async_request_refresh()
                            
                            return web.json_response({"success": True})
                    
                    return web.json_response({"success": False, "error": "Image not found"}, status=404)
            
            return web.json_response({"success": False, "error": "Note not found"}, status=404)
            
        except Exception as e:
            _LOGGER.error(f"Delete image error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)


class JotTickCalendarExportView(HomeAssistantView):    
    url = "/api/jottick/calendar/{filename}.ics"
    name = "api:jottick:calendar_export"
    requires_auth = False
    
    def __init__(self, coordinator):
        self.coordinator = coordinator
    
    async def get(self, request, filename):
        try:
            calendar_path = self.coordinator.hass.config.path(CALENDAR_DIR)
            file_path = os.path.join(calendar_path, f"{filename}.ics")
            
            if not os.path.exists(file_path):
                await self.coordinator.export_ical(filename=filename)
            
            def read_file():
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            
            content = await self.coordinator.hass.async_add_executor_job(read_file)
            
            return web.Response(
                text=content,
                content_type="text/calendar; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}.ics"'
                }
            )
            
        except Exception as e:
            _LOGGER.error(f"Calendar export error: {e}")
            return web.Response(text=f"Error: {e}", status=500)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored_data = await store.async_load()
    
    if stored_data is None:
        stored_data = {"notes": [], "checklists": [], "tasks": [], "ical_sources": [], "imported_events": []}
        await store.async_save(stored_data)
    
    if "ical_sources" not in stored_data:
        stored_data["ical_sources"] = []
    if "imported_events" not in stored_data:
        stored_data["imported_events"] = []
    
    images_path = hass.config.path(IMAGES_DIR)
    if not os.path.exists(images_path):
        os.makedirs(images_path, exist_ok=True)
    
    calendar_path = hass.config.path(CALENDAR_DIR)
    if not os.path.exists(calendar_path):
        os.makedirs(calendar_path, exist_ok=True)
    
    coordinator = JotTickCoordinator(hass, store, stored_data)
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "store": store,
        "coordinator": coordinator,
    }
    
    hass.http.register_view(JotTickUploadView(coordinator))
    hass.http.register_view(JotTickDeleteImageView(coordinator))
    hass.http.register_view(JotTickCalendarExportView(coordinator))
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass, entry.entry_id)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class JotTickCoordinator(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, store: Store, initial_data: dict):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=30))
        self.store = store
        self._data = initial_data
        self.data = self._format_data()

    def _format_data(self) -> dict:
        return {
            "notes": self._data.get("notes", []),
            "checklists": self._data.get("checklists", []),
            "tasks": self._data.get("tasks", []),
            "ical_sources": self._data.get("ical_sources", []),
            "imported_events": self._data.get("imported_events", []),
        }

    async def _async_update_data(self) -> dict:
        return self._format_data()

    async def async_save(self):
        await self.store.async_save(self._data)
        self.data = self._format_data()
        self.async_set_updated_data(self.data)
        
    async def create_note(self, title: str, content: str = "", note_id: str = None) -> dict:
        note = {
            "id": note_id if note_id else generate_id(),
            "title": title,
            "content": content,
            "images": [],
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        self._data["notes"].append(note)
        await self.async_save()
        return note

    async def update_note(self, note_id: str, title: str = None, content: str = None) -> dict:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                if title is not None:
                    note["title"] = title
                if content is not None:
                    note["content"] = content
                note["updatedAt"] = now_iso()
                await self.async_save()
                return note
        raise ValueError(f"Note {note_id} not found")

    async def delete_note(self, note_id: str) -> bool:
        for i, note in enumerate(self._data["notes"]):
            if note["id"] == note_id:
                images = note.get("images", [])
                images_path = self.hass.config.path(IMAGES_DIR)
                for img in images:
                    img_file = os.path.join(images_path, img.get("filename", ""))
                    if os.path.exists(img_file):
                        try:
                            os.remove(img_file)
                        except Exception as e:
                            _LOGGER.error(f"Failed to delete image {img_file}: {e}")
                
                del self._data["notes"][i]
                await self.async_save()
                return True
        raise ValueError(f"Note {note_id} not found")

    async def add_note_image(self, note_id: str, image_data: str, filename: str = None, caption: str = "") -> dict:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                if "images" not in note:
                    note["images"] = []
                
                image_id = generate_id()[:8]
                
                ext = ".jpg"
                if image_data.startswith("data:"):
                    mime_part = image_data.split(";")[0]  
                    if "/" in mime_part:
                        mime_type = mime_part.split("/")[1]
                        ext_map = {
                            "jpeg": ".jpg",
                            "jpg": ".jpg",
                            "png": ".png",
                            "gif": ".gif",
                            "webp": ".webp",
                            "bmp": ".bmp",
                        }
                        ext = ext_map.get(mime_type.lower(), ".jpg")
                elif filename:
                    _, file_ext = os.path.splitext(filename)
                    if file_ext:
                        ext = file_ext.lower()
                
                safe_filename = f"{note_id}_{image_id}{ext}"
                
                images_path = self.hass.config.path(IMAGES_DIR)
                if not os.path.exists(images_path):
                    os.makedirs(images_path, exist_ok=True)
                
                file_path = os.path.join(images_path, safe_filename)
                
                try:
                    base64_data = image_data
                    if "," in base64_data:
                        base64_data = base64_data.split(",")[1]
                    
                    padding = 4 - len(base64_data) % 4
                    if padding != 4:
                        base64_data += "=" * padding
                    
                    image_bytes = base64.b64decode(base64_data)
                    
                    def write_file():
                        with open(file_path, "wb") as f:
                            f.write(image_bytes)
                    
                    await self.hass.async_add_executor_job(write_file)
                    
                except Exception as e:
                    _LOGGER.error(f"Failed to save image: {e}")
                    raise ValueError(f"Failed to save image: {e}")
                
                image_record = {
                    "id": image_id,
                    "filename": safe_filename,
                    "url": f"/local/jottick/images/{safe_filename}",
                    "caption": caption,
                    "addedAt": now_iso(),
                }
                
                note["images"].append(image_record)
                note["updatedAt"] = now_iso()
                await self.async_save()
                return image_record
        
        raise ValueError(f"Note {note_id} not found")

    async def add_note_image_from_path(self, note_id: str, source_path: str, caption: str = "") -> dict:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                if "images" not in note:
                    note["images"] = []
                
                if not os.path.exists(source_path):
                    raise ValueError(f"Source file not found: {source_path}")
                
                image_id = generate_id()[:8]
                _, ext = os.path.splitext(source_path)
                if not ext:
                    ext = ".jpg"
                
                safe_filename = f"{note_id}_{image_id}{ext}"
                
                images_path = self.hass.config.path(IMAGES_DIR)
                if not os.path.exists(images_path):
                    os.makedirs(images_path, exist_ok=True)
                
                dest_path = os.path.join(images_path, safe_filename)
                
                try:
                    def copy_file():
                        shutil.copy2(source_path, dest_path)
                    
                    await self.hass.async_add_executor_job(copy_file)
                    
                except Exception as e:
                    _LOGGER.error(f"Failed to copy image: {e}")
                    raise ValueError(f"Failed to copy image: {e}")
                
                image_record = {
                    "id": image_id,
                    "filename": safe_filename,
                    "url": f"/local/jottick/images/{safe_filename}",
                    "caption": caption,
                    "addedAt": now_iso(),
                }
                
                note["images"].append(image_record)
                note["updatedAt"] = now_iso()
                await self.async_save()
                return image_record
        
        raise ValueError(f"Note {note_id} not found")

    async def delete_note_image(self, note_id: str, image_id: str) -> bool:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                images = note.get("images", [])
                for i, img in enumerate(images):
                    if img["id"] == image_id:
                        images_path = self.hass.config.path(IMAGES_DIR)
                        img_file = os.path.join(images_path, img["filename"])
                        if os.path.exists(img_file):
                            try:
                                def delete_file():
                                    os.remove(img_file)
                                await self.hass.async_add_executor_job(delete_file)
                            except Exception as e:
                                _LOGGER.error(f"Failed to delete image file: {e}")
                        
                        del images[i]
                        note["updatedAt"] = now_iso()
                        await self.async_save()
                        return True
                
                raise ValueError(f"Image {image_id} not found in note")
        
        raise ValueError(f"Note {note_id} not found")

    async def update_note_image_caption(self, note_id: str, image_id: str, caption: str) -> dict:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                images = note.get("images", [])
                for img in images:
                    if img["id"] == image_id:
                        img["caption"] = caption
                        note["updatedAt"] = now_iso()
                        await self.async_save()
                        return img
                
                raise ValueError(f"Image {image_id} not found in note")
        
        raise ValueError(f"Note {note_id} not found")

    async def reorder_note_images(self, note_id: str, image_ids: list) -> bool:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                images = note.get("images", [])
                
                img_lookup = {img["id"]: img for img in images}
                
                new_order = []
                for img_id in image_ids:
                    if img_id in img_lookup:
                        new_order.append(img_lookup[img_id])
                
                for img in images:
                    if img["id"] not in image_ids:
                        new_order.append(img)
                
                note["images"] = new_order
                note["updatedAt"] = now_iso()
                await self.async_save()
                return True
        
        raise ValueError(f"Note {note_id} not found")
        
    async def create_checklist(self, title: str, list_type: str = "simple") -> dict:
        checklist = {
            "id": generate_id(),
            "title": title,
            "type": list_type,
            "items": [],
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        self._data["checklists"].append(checklist)
        await self.async_save()
        return checklist

    async def update_checklist(self, checklist_id: str, title: str = None) -> dict:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                if title is not None:
                    checklist["title"] = title
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return checklist
        raise ValueError(f"Checklist {checklist_id} not found")

    async def delete_checklist(self, checklist_id: str) -> bool:
        for i, checklist in enumerate(self._data["checklists"]):
            if checklist["id"] == checklist_id:
                del self._data["checklists"][i]
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def add_checklist_item(self, checklist_id: str, text: str, status: str = None, parent_index: str = None) -> dict:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                new_item = {"text": text, "completed": False, "children": []}
                if status:
                    new_item["status"] = status
                
                if parent_index is not None:
                    items = checklist["items"]
                    indices = [int(i) for i in str(parent_index).split(".")]
                    target = items
                    for idx in indices[:-1]:
                        target = target[idx].setdefault("children", [])
                    target[indices[-1]].setdefault("children", []).append(new_item)
                else:
                    checklist["items"].append(new_item)
                
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return new_item
        raise ValueError(f"Checklist {checklist_id} not found")

    def _get_item_by_index(self, items: list, index_path: str) -> tuple:
        indices = [int(i) for i in str(index_path).split(".")]
        target_list = items
        for idx in indices[:-1]:
            target_list = target_list[idx].setdefault("children", [])
        return target_list, indices[-1]

    async def check_item(self, checklist_id: str, item_index: str) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                target_list[idx]["completed"] = True
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def uncheck_item(self, checklist_id: str, item_index: str) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                target_list[idx]["completed"] = False
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def delete_checklist_item(self, checklist_id: str, item_index: str) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                del target_list[idx]
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def set_checklist_item_due_date(
        self, 
        checklist_id: str, 
        item_index: str, 
        due_date: str, 
        due_time: str = None,
        notify_overdue: bool = False
    ) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                target_list[idx]["dueDate"] = due_date
                if due_time:
                    target_list[idx]["dueTime"] = due_time
                elif "dueTime" in target_list[idx]:
                    del target_list[idx]["dueTime"]
                target_list[idx]["notifyOverdue"] = notify_overdue
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def clear_checklist_item_due_date(self, checklist_id: str, item_index: str) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                if "dueDate" in target_list[idx]:
                    del target_list[idx]["dueDate"]
                if "dueTime" in target_list[idx]:
                    del target_list[idx]["dueTime"]
                if "notifyOverdue" in target_list[idx]:
                    del target_list[idx]["notifyOverdue"]
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")
    
    async def create_task(self, title: str) -> dict:
        task = {
            "id": generate_id(),
            "title": title,
            "items": [],
            "statuses": [
                {"id": "todo", "label": "To Do", "color": "#6b7280", "order": 0},
                {"id": "in_progress", "label": "In Progress", "color": "#3b82f6", "order": 1},
                {"id": "completed", "label": "Completed", "color": "#10b981", "order": 2},
            ],
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        self._data["tasks"].append(task)
        await self.async_save()
        return task

    async def update_task(self, task_id: str, title: str = None) -> dict:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                if title is not None:
                    task["title"] = title
                task["updatedAt"] = now_iso()
                await self.async_save()
                return task
        raise ValueError(f"Task {task_id} not found")

    async def delete_task(self, task_id: str) -> bool:
        for i, task in enumerate(self._data["tasks"]):
            if task["id"] == task_id:
                del self._data["tasks"][i]
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def add_task_item(self, task_id: str, text: str, status: str = "todo", parent_index: str = None) -> dict:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                new_item = {"text": text, "status": status, "children": []}
                
                if parent_index is not None:
                    items = task["items"]
                    indices = [int(i) for i in str(parent_index).split(".")]
                    target = items
                    for idx in indices[:-1]:
                        target = target[idx].setdefault("children", [])
                    target[indices[-1]].setdefault("children", []).append(new_item)
                else:
                    task["items"].append(new_item)
                
                task["updatedAt"] = now_iso()
                await self.async_save()
                return new_item
        raise ValueError(f"Task {task_id} not found")

    async def update_task_item_status(self, task_id: str, item_index: str, status: str) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                target_list, idx = self._get_item_by_index(task["items"], item_index)
                target_list[idx]["status"] = status
                if status == "completed":
                    for child in target_list[idx].get("children", []):
                        child["status"] = "completed"
                task["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def delete_task_item(self, task_id: str, item_index: str) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                target_list, idx = self._get_item_by_index(task["items"], item_index)
                del target_list[idx]
                task["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def create_task_status(self, task_id: str, status_id: str, label: str, color: str = None, order: int = None) -> dict:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                new_status = {
                    "id": status_id,
                    "label": label,
                    "color": color or "#6b7280",
                    "order": order if order is not None else len(task.get("statuses", [])),
                }
                task.setdefault("statuses", []).append(new_status)
                task["updatedAt"] = now_iso()
                await self.async_save()
                return new_status
        raise ValueError(f"Task {task_id} not found")

    async def update_task_status(self, task_id: str, status_id: str, label: str = None, color: str = None, order: int = None) -> dict:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                for status in task.get("statuses", []):
                    if status["id"] == status_id:
                        if label is not None:
                            status["label"] = label
                        if color is not None:
                            status["color"] = color
                        if order is not None:
                            status["order"] = order
                        task["updatedAt"] = now_iso()
                        await self.async_save()
                        return status
                raise ValueError(f"Status {status_id} not found")
        raise ValueError(f"Task {task_id} not found")

    async def delete_task_status(self, task_id: str, status_id: str) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                statuses = task.get("statuses", [])
                for i, status in enumerate(statuses):
                    if status["id"] == status_id:
                        del statuses[i]
                        task["updatedAt"] = now_iso()
                        await self.async_save()
                        return True
                raise ValueError(f"Status {status_id} not found")
        raise ValueError(f"Task {task_id} not found")

    async def set_task_item_due_date(
        self, 
        task_id: str, 
        item_index: str, 
        due_date: str, 
        due_time: str = None,
        notify_overdue: bool = False
    ) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                target_list, idx = self._get_item_by_index(task["items"], item_index)
                target_list[idx]["dueDate"] = due_date
                if due_time:
                    target_list[idx]["dueTime"] = due_time
                elif "dueTime" in target_list[idx]:
                    del target_list[idx]["dueTime"]
                target_list[idx]["notifyOverdue"] = notify_overdue
                task["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def clear_task_item_due_date(self, task_id: str, item_index: str) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                target_list, idx = self._get_item_by_index(task["items"], item_index)
                if "dueDate" in target_list[idx]:
                    del target_list[idx]["dueDate"]
                if "dueTime" in target_list[idx]:
                    del target_list[idx]["dueTime"]
                if "notifyOverdue" in target_list[idx]:
                    del target_list[idx]["notifyOverdue"]
                task["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def import_ical(self, url: str, name: str = None, auto_refresh: bool = True) -> dict:
        try:
            for source in self._data.get("ical_sources", []):
                if source["url"] == url:
                    raise ValueError(f"iCal source already imported: {url}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        raise ValueError(f"Failed to fetch iCal: HTTP {response.status}")
                    ical_data = await response.text()
            
            events = self._parse_ical_data(ical_data, url)
            
            source_record = {
                "id": generate_id()[:8],
                "url": url,
                "name": name or url.split("/")[-1].replace(".ics", ""),
                "auto_refresh": auto_refresh,
                "last_refresh": now_iso(),
                "event_count": len(events),
            }
            
            if "ical_sources" not in self._data:
                self._data["ical_sources"] = []
            self._data["ical_sources"].append(source_record)
            
            if "imported_events" not in self._data:
                self._data["imported_events"] = []
            self._data["imported_events"].extend(events)
            
            await self.async_save()
            
            self.hass.bus.async_fire("jottick_ical_sources_update", {
                "sources": self._data["ical_sources"]
            })
            self.hass.bus.async_fire("jottick_imported_events_update", {
                "events": self._data["imported_events"]
            })
            
            return source_record
            
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Failed to fetch iCal: {e}")
            raise ValueError(f"Failed to fetch iCal: {e}")
        except Exception as e:
            _LOGGER.error(f"Failed to import iCal: {e}")
            raise

    def _parse_ical_data(self, ical_data: str, source_url: str) -> list:
        events = []   
        ical_data = re.sub(r'\r?\n[ \t]', '', ical_data)    
        ical_data = ical_data.replace('\r\n', '\n').replace('\r', '\n')
        vevent_pattern = re.compile(r'BEGIN:VEVENT(.*?)END:VEVENT', re.DOTALL)
        
        for match in vevent_pattern.finditer(ical_data):
            event_data = match.group(1)
            
            event = {
                "id": f"imported_{generate_id()[:8]}",
                "source_url": source_url,
                "editable": False,
            }
            
            uid_match = re.search(r'^UID[;:](.+?)$', event_data, re.MULTILINE)
            if uid_match:
                event["original_uid"] = uid_match.group(1).strip()
            summary_match = re.search(r'^SUMMARY[;:](.+?)$', event_data, re.MULTILINE)
            if summary_match:
                title = summary_match.group(1).strip()
                if ':' in title and not title.startswith('\\'):
                    title = title.split(':', 1)[-1]
                event["title"] = title.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
            else:
                event["title"] = "Untitled Event"
            desc_match = re.search(r'^DESCRIPTION[;:](.+?)$', event_data, re.MULTILINE)
            if desc_match:
                desc = desc_match.group(1).strip()
                if ':' in desc and not desc.startswith('\\'):
                    desc = desc.split(':', 1)[-1]
                event["description"] = desc.replace("\\n", "\n").replace("\\,", ",")
            dtstart_match = re.search(r'^DTSTART[^:\n]*:(\d{8}(?:T\d{6}Z?)?)$', event_data, re.MULTILINE)
            if dtstart_match:
                dt_str = dtstart_match.group(1)
                if len(dt_str) >= 8:
                    event["date"] = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]}"
                    if len(dt_str) >= 15:
                        event["time"] = f"{dt_str[9:11]}:{dt_str[11:13]}"
            

            dtend_match = re.search(r'^DTEND[^:\n]*:(\d{8}(?:T\d{6}Z?)?)$', event_data, re.MULTILINE)
            if dtend_match:
                dt_str = dtend_match.group(1)
                if len(dt_str) >= 15:  
                    event["end_time"] = f"{dt_str[9:11]}:{dt_str[11:13]}"
            

            location_match = re.search(r'^LOCATION[;:](.+?)$', event_data, re.MULTILINE)
            if location_match:
                loc = location_match.group(1).strip()
                if ':' in loc and not loc.startswith('\\'):
                    loc = loc.split(':', 1)[-1]
                event["location"] = loc.replace("\\,", ",")
            
            if "date" in event:
                events.append(event)
                _LOGGER.debug(f"Parsed iCal event: {event.get('title')} on {event.get('date')}")
        
        _LOGGER.info(f"Parsed {len(events)} events from iCal source: {source_url}")
        return events

    async def remove_ical_import(self, url: str) -> bool:
        sources = self._data.get("ical_sources", [])
        for i, source in enumerate(sources):
            if source["url"] == url:
                del sources[i]
                break
        else:
            raise ValueError(f"iCal source not found: {url}")
        
        self._data["imported_events"] = [
            e for e in self._data.get("imported_events", [])
            if e.get("source_url") != url
        ]
        
        await self.async_save()
        
        self.hass.bus.async_fire("jottick_ical_sources_update", {
            "sources": self._data["ical_sources"]
        })
        self.hass.bus.async_fire("jottick_imported_events_update", {
            "events": self._data["imported_events"]
        })
        
        return True

    async def refresh_ical_imports(self) -> dict:
        results = {"refreshed": 0, "errors": []}
        
        for source in self._data.get("ical_sources", []):
            if not source.get("auto_refresh", True):
                continue
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(source["url"], timeout=30) as response:
                        if response.status != 200:
                            results["errors"].append({
                                "url": source["url"],
                                "error": f"HTTP {response.status}"
                            })
                            for event in self._data.get("imported_events", []):
                                if event.get("source_url") == source["url"]:
                                    event["editable"] = True
                            continue
                        ical_data = await response.text()

                self._data["imported_events"] = [
                    e for e in self._data.get("imported_events", [])
                    if e.get("source_url") != source["url"]
                ]

                events = self._parse_ical_data(ical_data, source["url"])
                self._data["imported_events"].extend(events)

                source["last_refresh"] = now_iso()
                source["event_count"] = len(events)
                
                results["refreshed"] += 1
                
            except Exception as e:
                _LOGGER.error(f"Failed to refresh iCal {source['url']}: {e}")
                results["errors"].append({
                    "url": source["url"],
                    "error": str(e)
                })
                for event in self._data.get("imported_events", []):
                    if event.get("source_url") == source["url"]:
                        event["editable"] = True
        
        await self.async_save()
        
        self.hass.bus.async_fire("jottick_ical_sources_update", {
            "sources": self._data["ical_sources"]
        })
        self.hass.bus.async_fire("jottick_imported_events_update", {
            "events": self._data["imported_events"]
        })
        
        return results

    async def export_ical(
        self, 
        filename: str = "jottick_calendar",
        include_notes: bool = True,
        include_lists: bool = True,
        include_tasks: bool = True,
        include_reminders: bool = True
    ) -> str:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//JotTick//Home Assistant//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "X-WR-CALNAME:JotTick Calendar",
        ]
        
        now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        
        if include_notes:
            for note in self._data.get("notes", []):
                created = note.get("createdAt", "")
                updated = note.get("updatedAt", "")
                
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y%m%d")
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('note-created', note['id'])}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{date_str}",
                            f"SUMMARY:📝 Note Created: {escape_ical_text(note.get('title', 'Untitled'))}",
                            f"DESCRIPTION:Note created in JotTick",
                            "CATEGORIES:JotTick,Note,Created",
                            "END:VEVENT",
                        ])
                    except:
                        pass
                
                if updated and updated != created:
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y%m%d")
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('note-edited', note['id'])}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{date_str}",
                            f"SUMMARY:✏️ Note Edited: {escape_ical_text(note.get('title', 'Untitled'))}",
                            f"DESCRIPTION:Note last edited in JotTick",
                            "CATEGORIES:JotTick,Note,Edited",
                            "END:VEVENT",
                        ])
                    except:
                        pass
        
        if include_lists:
            for checklist in self._data.get("checklists", []):
                created = checklist.get("createdAt", "")
                updated = checklist.get("updatedAt", "")
                
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y%m%d")
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('list-created', checklist['id'])}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{date_str}",
                            f"SUMMARY:📋 List Created: {escape_ical_text(checklist.get('title', 'Untitled'))}",
                            f"DESCRIPTION:Checklist created in JotTick",
                            "CATEGORIES:JotTick,List,Created",
                            "END:VEVENT",
                        ])
                    except:
                        pass
                
                if updated and updated != created:
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y%m%d")
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('list-edited', checklist['id'])}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{date_str}",
                            f"SUMMARY:✏️ List Edited: {escape_ical_text(checklist.get('title', 'Untitled'))}",
                            f"DESCRIPTION:Checklist last edited in JotTick",
                            "CATEGORIES:JotTick,List,Edited",
                            "END:VEVENT",
                        ])
                    except:
                        pass
                
                self._export_items_due_dates(lines, checklist.get("items", []), checklist, "list", now_stamp)
        
        if include_tasks:
            for task in self._data.get("tasks", []):
                created = task.get("createdAt", "")
                updated = task.get("updatedAt", "")
                
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y%m%d")
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('task-created', task['id'])}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{date_str}",
                            f"SUMMARY:✅ Task Created: {escape_ical_text(task.get('title', 'Untitled'))}",
                            f"DESCRIPTION:Task list created in JotTick",
                            "CATEGORIES:JotTick,Task,Created",
                            "END:VEVENT",
                        ])
                    except:
                        pass
                
                if updated and updated != created:
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y%m%d")
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('task-edited', task['id'])}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{date_str}",
                            f"SUMMARY:✏️ Task Edited: {escape_ical_text(task.get('title', 'Untitled'))}",
                            f"DESCRIPTION:Task list last edited in JotTick",
                            "CATEGORIES:JotTick,Task,Edited",
                            "END:VEVENT",
                        ])
                    except:
                        pass
                
                self._export_items_due_dates(lines, task.get("items", []), task, "task", now_stamp)
        
        if include_reminders:
            reminder_sensor = self.hass.states.get("sensor.jottick_reminders")
            if reminder_sensor:
                configs = reminder_sensor.attributes.get("configs", {})
                for item_id, config in configs.items():
                    if config.get("enabled"):
                        lines.extend([
                            "BEGIN:VEVENT",
                            f"UID:{generate_ical_uid('reminder', item_id)}",
                            f"DTSTAMP:{now_stamp}",
                            f"DTSTART;VALUE=DATE:{datetime.utcnow().strftime('%Y%m%d')}",
                            f"SUMMARY:🔔 Reminder: {escape_ical_text(config.get('title', 'Reminder'))}",
                            f"DESCRIPTION:JotTick reminder - {config.get('interval', 'recurring')}",
                            "CATEGORIES:JotTick,Reminder",
                            "END:VEVENT",
                        ])
        
        lines.append("END:VCALENDAR")
        
        calendar_path = self.hass.config.path(CALENDAR_DIR)
        if not os.path.exists(calendar_path):
            os.makedirs(calendar_path, exist_ok=True)
        
        file_path = os.path.join(calendar_path, f"{filename}.ics")
        
        def write_ical():
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\r\n".join(lines))
        
        await self.hass.async_add_executor_job(write_ical)
        
        return f"/local/jottick/calendar/{filename}.ics"

    def _export_items_due_dates(self, lines: list, items: list, parent: dict, item_type: str, now_stamp: str, prefix: str = ""):
        for i, item in enumerate(items):
            index_path = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"
            
            due_date = item.get("dueDate")
            if due_date:
                due_time = item.get("dueTime")
                item_text = item.get("text", "Untitled")
                parent_title = parent.get("title", "Untitled")
                
                if due_time:
                    dt_str = format_ical_datetime(due_date, due_time)
                    lines.extend([
                        "BEGIN:VEVENT",
                        f"UID:{generate_ical_uid(f'{item_type}-due', f'{parent["id"]}-{index_path}')}",
                        f"DTSTAMP:{now_stamp}",
                        f"DTSTART:{dt_str}",
                        f"SUMMARY:📅 Due: {escape_ical_text(item_text)}",
                        f"DESCRIPTION:From {item_type}: {escape_ical_text(parent_title)}",
                        f"CATEGORIES:JotTick,{item_type.title()},Due",
                        "END:VEVENT",
                    ])
                else:
                    dt_str = format_ical_datetime(due_date)
                    lines.extend([
                        "BEGIN:VEVENT",
                        f"UID:{generate_ical_uid(f'{item_type}-due', f'{parent["id"]}-{index_path}')}",
                        f"DTSTAMP:{now_stamp}",
                        f"DTSTART;VALUE=DATE:{dt_str}",
                        f"SUMMARY:📅 Due: {escape_ical_text(item_text)}",
                        f"DESCRIPTION:From {item_type}: {escape_ical_text(parent_title)}",
                        f"CATEGORIES:JotTick,{item_type.title()},Due",
                        "END:VEVENT",
                    ])
            
            if item.get("children"):
                self._export_items_due_dates(lines, item["children"], parent, item_type, now_stamp, f"{index_path}.")


async def async_setup_services(hass: HomeAssistant, entry_id: str):
    
    def get_coordinator() -> JotTickCoordinator:
        return hass.data[DOMAIN][entry_id]["coordinator"]
        
    async def handle_create_note(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_note(
            title=call.data.get("title"),
            content=call.data.get("content", ""),
            note_id=call.data.get("note_id"),
        )

    async def handle_update_note(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_note(
            note_id=call.data.get("note_id"),
            title=call.data.get("title"),
            content=call.data.get("content"),
        )

    async def handle_delete_note(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_note(note_id=call.data.get("note_id"))

    async def handle_add_note_image(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.add_note_image(
            note_id=call.data.get("note_id"),
            image_data=call.data.get("image_data"),
            filename=call.data.get("filename"),
            caption=call.data.get("caption", ""),
        )

    async def handle_add_note_image_from_path(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.add_note_image_from_path(
            note_id=call.data.get("note_id"),
            source_path=call.data.get("source_path"),
            caption=call.data.get("caption", ""),
        )

    async def handle_delete_note_image(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_note_image(
            note_id=call.data.get("note_id"),
            image_id=call.data.get("image_id"),
        )

    async def handle_update_note_image_caption(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_note_image_caption(
            note_id=call.data.get("note_id"),
            image_id=call.data.get("image_id"),
            caption=call.data.get("caption", ""),
        )

    async def handle_reorder_note_images(call: ServiceCall):
        coordinator = get_coordinator()
        image_ids = call.data.get("image_ids", [])
        if isinstance(image_ids, str):
            image_ids = [x.strip() for x in image_ids.split(",")]
        await coordinator.reorder_note_images(
            note_id=call.data.get("note_id"),
            image_ids=image_ids,
        )
      
    async def handle_create_checklist(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_checklist(
            title=call.data.get("title"),
            list_type=call.data.get("type", "simple"),
        )

    async def handle_update_checklist(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_checklist(
            checklist_id=call.data.get("checklist_id"),
            title=call.data.get("title"),
        )

    async def handle_delete_checklist(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_checklist(checklist_id=call.data.get("checklist_id"))

    async def handle_add_checklist_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.add_checklist_item(
            checklist_id=call.data.get("checklist_id"),
            text=call.data.get("text"),
            status=call.data.get("status"),
            parent_index=call.data.get("parent_index"),
        )

    async def handle_check_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.check_item(
            checklist_id=call.data.get("checklist_id"),
            item_index=str(call.data.get("item_index")),
        )

    async def handle_uncheck_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.uncheck_item(
            checklist_id=call.data.get("checklist_id"),
            item_index=str(call.data.get("item_index")),
        )

    async def handle_delete_checklist_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_checklist_item(
            checklist_id=call.data.get("checklist_id"),
            item_index=str(call.data.get("item_index")),
        )

    async def handle_set_checklist_item_due_date(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.set_checklist_item_due_date(
            checklist_id=call.data.get("checklist_id"),
            item_index=str(call.data.get("item_index")),
            due_date=call.data.get("due_date"),
            due_time=call.data.get("due_time"),
            notify_overdue=call.data.get("notify_overdue", False),
        )

    async def handle_clear_checklist_item_due_date(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.clear_checklist_item_due_date(
            checklist_id=call.data.get("checklist_id"),
            item_index=str(call.data.get("item_index")),
        )
    
    async def handle_create_task(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_task(title=call.data.get("title"))

    async def handle_update_task(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_task(
            task_id=call.data.get("task_id"),
            title=call.data.get("title"),
        )

    async def handle_delete_task(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_task(task_id=call.data.get("task_id"))

    async def handle_add_task_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.add_task_item(
            task_id=call.data.get("task_id"),
            text=call.data.get("text"),
            status=call.data.get("status", "todo"),
            parent_index=call.data.get("parent_index"),
        )

    async def handle_update_task_item_status(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_task_item_status(
            task_id=call.data.get("task_id"),
            item_index=str(call.data.get("item_index")),
            status=call.data.get("status"),
        )

    async def handle_delete_task_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_task_item(
            task_id=call.data.get("task_id"),
            item_index=str(call.data.get("item_index")),
        )

    async def handle_create_task_status(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_task_status(
            task_id=call.data.get("task_id"),
            status_id=call.data.get("status_id"),
            label=call.data.get("label"),
            color=call.data.get("color"),
            order=call.data.get("order"),
        )

    async def handle_update_task_status(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_task_status(
            task_id=call.data.get("task_id"),
            status_id=call.data.get("status_id"),
            label=call.data.get("label"),
            color=call.data.get("color"),
            order=call.data.get("order"),
        )

    async def handle_delete_task_status(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_task_status(
            task_id=call.data.get("task_id"),
            status_id=call.data.get("status_id"),
        )

    async def handle_set_task_item_due_date(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.set_task_item_due_date(
            task_id=call.data.get("task_id"),
            item_index=str(call.data.get("item_index")),
            due_date=call.data.get("due_date"),
            due_time=call.data.get("due_time"),
            notify_overdue=call.data.get("notify_overdue", False),
        )

    async def handle_clear_task_item_due_date(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.clear_task_item_due_date(
            task_id=call.data.get("task_id"),
            item_index=str(call.data.get("item_index")),
        )

    async def handle_import_ical(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.import_ical(
            url=call.data.get("url"),
            name=call.data.get("name"),
            auto_refresh=call.data.get("auto_refresh", True),
        )

    async def handle_remove_ical_import(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.remove_ical_import(url=call.data.get("url"))

    async def handle_refresh_ical_imports(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.refresh_ical_imports()

    async def handle_export_ical(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.export_ical(
            filename=call.data.get("filename", "jottick_calendar"),
            include_notes=call.data.get("include_notes", True),
            include_lists=call.data.get("include_lists", True),
            include_tasks=call.data.get("include_tasks", True),
            include_reminders=call.data.get("include_reminders", True),
        )

    hass.services.async_register(DOMAIN, "create_note", handle_create_note)
    hass.services.async_register(DOMAIN, "update_note", handle_update_note)
    hass.services.async_register(DOMAIN, "delete_note", handle_delete_note)
    
    hass.services.async_register(DOMAIN, "add_note_image", handle_add_note_image)
    hass.services.async_register(DOMAIN, "add_note_image_from_path", handle_add_note_image_from_path)
    hass.services.async_register(DOMAIN, "delete_note_image", handle_delete_note_image)
    hass.services.async_register(DOMAIN, "update_note_image_caption", handle_update_note_image_caption)
    hass.services.async_register(DOMAIN, "reorder_note_images", handle_reorder_note_images)
    
    hass.services.async_register(DOMAIN, "create_checklist", handle_create_checklist)
    hass.services.async_register(DOMAIN, "update_checklist", handle_update_checklist)
    hass.services.async_register(DOMAIN, "delete_checklist", handle_delete_checklist)
    hass.services.async_register(DOMAIN, "add_checklist_item", handle_add_checklist_item)
    hass.services.async_register(DOMAIN, "check_item", handle_check_item)
    hass.services.async_register(DOMAIN, "uncheck_item", handle_uncheck_item)
    hass.services.async_register(DOMAIN, "delete_checklist_item", handle_delete_checklist_item)
    
    hass.services.async_register(DOMAIN, "set_checklist_item_due_date", handle_set_checklist_item_due_date)
    hass.services.async_register(DOMAIN, "clear_checklist_item_due_date", handle_clear_checklist_item_due_date)
    
    hass.services.async_register(DOMAIN, "create_task", handle_create_task)
    hass.services.async_register(DOMAIN, "update_task", handle_update_task)
    hass.services.async_register(DOMAIN, "delete_task", handle_delete_task)
    hass.services.async_register(DOMAIN, "add_task_item", handle_add_task_item)
    hass.services.async_register(DOMAIN, "update_task_item_status", handle_update_task_item_status)
    hass.services.async_register(DOMAIN, "delete_task_item", handle_delete_task_item)
    hass.services.async_register(DOMAIN, "create_task_status", handle_create_task_status)
    hass.services.async_register(DOMAIN, "update_task_status", handle_update_task_status)
    hass.services.async_register(DOMAIN, "delete_task_status", handle_delete_task_status)
    
    hass.services.async_register(DOMAIN, "set_task_item_due_date", handle_set_task_item_due_date)
    hass.services.async_register(DOMAIN, "clear_task_item_due_date", handle_clear_task_item_due_date)
    
    hass.services.async_register(DOMAIN, "import_ical", handle_import_ical)
    hass.services.async_register(DOMAIN, "remove_ical_import", handle_remove_ical_import)
    hass.services.async_register(DOMAIN, "refresh_ical_imports", handle_refresh_ical_imports)
    hass.services.async_register(DOMAIN, "export_ical", handle_export_ical)
