import logging
import uuid
import os
import base64
import shutil
import json
import copy
import re
import aiohttp
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
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

PLATFORMS = [Platform.SENSOR, Platform.CALENDAR]

IMAGES_DIR = "www/community/jottick/images"
CALENDAR_DIR = "www/community/jottick/calendar"
POINTS_DIR = "www/community/jottick/points"


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


MAX_UPLOAD_SIZE = 50 * 1024 * 1024


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

            if len(content) > MAX_UPLOAD_SIZE:
                return web.json_response({"success": False, "error": f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)}MB"}, status=400)

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
            
            image_url = f"/local/community/jottick/images/{safe_filename}"
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
                    note["updatedAt"] = now_iso()
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
                            note["updatedAt"] = now_iso()
                            
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
    requires_auth = True

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def get(self, request, filename):
        try:
            safe_filename = re.sub(r'[^a-zA-Z0-9_-]', '', filename)
            if not safe_filename:
                safe_filename = "jottick_calendar"

            calendar_path = self.coordinator.hass.config.path(CALENDAR_DIR)
            file_path = os.path.join(calendar_path, f"{safe_filename}.ics")

            resolved_path = os.path.realpath(file_path)
            resolved_calendar_path = os.path.realpath(calendar_path)
            if not resolved_path.startswith(resolved_calendar_path):
                return web.Response(text="Invalid filename", status=400)

            if not os.path.exists(file_path):
                await self.coordinator.export_ical(filename=safe_filename)

            def read_file():
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()

            content = await self.coordinator.hass.async_add_executor_job(read_file)

            return web.Response(
                text=content,
                content_type="text/calendar; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_filename}.ics"'
                }
            )

        except Exception as e:
            _LOGGER.error(f"Calendar export error: {e}")
            return web.Response(text=f"Error: {e}", status=500)


class JotTickPrizeUploadView(HomeAssistantView):
    url = "/api/jottick/prize-upload"
    name = "api:jottick:prize_upload"
    requires_auth = True

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def post(self, request):
        try:
            data = await request.post()

            prize_id = data.get('prize_id')
            if not prize_id:
                return web.json_response({"success": False, "error": "prize_id required"}, status=400)

            file_field = data.get('file')
            if not file_field:
                return web.json_response({"success": False, "error": "file required"}, status=400)

            filename = file_field.filename
            content = file_field.file.read()

            if len(content) > MAX_UPLOAD_SIZE:
                return web.json_response({"success": False, "error": f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)}MB"}, status=400)

            ext = os.path.splitext(filename)[1].lower() or '.jpg'
            allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
            if ext not in allowed_extensions:
                return web.json_response({"success": False, "error": "Invalid file type"}, status=400)

            safe_filename = f"prize_{prize_id}{ext}"

            points_path = self.coordinator.hass.config.path(POINTS_DIR)
            if not os.path.exists(points_path):
                os.makedirs(points_path, exist_ok=True)

            file_path = os.path.join(points_path, safe_filename)

            def write_file():
                with open(file_path, 'wb') as f:
                    f.write(content)

            await self.coordinator.hass.async_add_executor_job(write_file)

            image_url = f"/local/community/jottick/points/{safe_filename}"

            for prize in self.coordinator._data["points_prizes"]:
                if prize["id"] == prize_id:
                    prize["image"] = image_url
                    break

            await self.coordinator.async_save()

            self.coordinator.hass.bus.async_fire("jottick_points_prizes_update", {
                "prizes": self.coordinator._data["points_prizes"]
            })

            return web.json_response({
                "success": True,
                "image_url": image_url
            })

        except Exception as e:
            _LOGGER.error(f"Prize upload error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)


class JotTickAchievementUploadView(HomeAssistantView):
    url = "/api/jottick/achievement-upload"
    name = "api:jottick:achievement_upload"
    requires_auth = True

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def post(self, request):
        try:
            data = await request.post()

            achievement_id = data.get('achievement_id')
            if not achievement_id:
                return web.json_response({"success": False, "error": "achievement_id required"}, status=400)

            file_field = data.get('file')
            if not file_field:
                return web.json_response({"success": False, "error": "file required"}, status=400)

            filename = file_field.filename
            content = file_field.file.read()

            if len(content) > MAX_UPLOAD_SIZE:
                return web.json_response({"success": False, "error": f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)}MB"}, status=400)

            ext = os.path.splitext(filename)[1].lower() or '.jpg'
            allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
            if ext not in allowed_extensions:
                return web.json_response({"success": False, "error": "Invalid file type"}, status=400)

            safe_filename = f"achievement_{achievement_id}{ext}"

            points_path = self.coordinator.hass.config.path(POINTS_DIR)
            if not os.path.exists(points_path):
                os.makedirs(points_path, exist_ok=True)

            file_path = os.path.join(points_path, safe_filename)

            def write_file():
                with open(file_path, 'wb') as f:
                    f.write(content)

            await self.coordinator.hass.async_add_executor_job(write_file)

            image_url = f"/local/community/jottick/points/{safe_filename}"

            for achievement in self.coordinator._data.get("achievements", []):
                if achievement["id"] == achievement_id:
                    achievement["image"] = image_url
                    break

            await self.coordinator.async_save()

            self.coordinator.hass.bus.async_fire("jottick_achievements_update", {
                "achievements": self.coordinator._data.get("achievements", [])
            })

            return web.json_response({
                "success": True,
                "image_url": image_url
            })

        except Exception as e:
            _LOGGER.error(f"Achievement upload error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored_data = await store.async_load()
    
    if stored_data is None:
        stored_data = {"notes": [], "checklists": [], "tasks": [], "ical_sources": [], "imported_events": [], "points_users": {}, "points_history": [], "points_prizes": [], "points_admins": []}
        await store.async_save(stored_data)

    if "ical_sources" not in stored_data:
        stored_data["ical_sources"] = []
    if "imported_events" not in stored_data:
        stored_data["imported_events"] = []
    if "points_users" not in stored_data:
        stored_data["points_users"] = {}
    if "points_history" not in stored_data:
        stored_data["points_history"] = []
    if "points_prizes" not in stored_data:
        stored_data["points_prizes"] = []
    if "points_admins" not in stored_data:
        stored_data["points_admins"] = []
    if "achievements" not in stored_data:
        stored_data["achievements"] = []
    if "user_achievements" not in stored_data:
        stored_data["user_achievements"] = {}

    images_path = hass.config.path(IMAGES_DIR)
    if not os.path.exists(images_path):
        os.makedirs(images_path, exist_ok=True)

    calendar_path = hass.config.path(CALENDAR_DIR)
    if not os.path.exists(calendar_path):
        os.makedirs(calendar_path, exist_ok=True)

    points_path = hass.config.path(POINTS_DIR)
    if not os.path.exists(points_path):
        os.makedirs(points_path, exist_ok=True)

    coordinator = JotTickCoordinator(hass, store, stored_data)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "store": store,
        "coordinator": coordinator,
    }

    hass.http.register_view(JotTickUploadView(coordinator))
    hass.http.register_view(JotTickDeleteImageView(coordinator))
    hass.http.register_view(JotTickCalendarExportView(coordinator))
    hass.http.register_view(JotTickPrizeUploadView(coordinator))
    hass.http.register_view(JotTickAchievementUploadView(coordinator))
    
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
        self._note_index = {}
        self._checklist_index = {}
        self._task_index = {}
        self._migrate_data()
        self._rebuild_indexes()
        self.data = self._format_data()

    def _rebuild_indexes(self):
        self._note_index = {n["id"]: n for n in self._data.get("notes", [])}
        self._checklist_index = {c["id"]: c for c in self._data.get("checklists", [])}
        self._task_index = {t["id"]: t for t in self._data.get("tasks", [])}

    def _get_note(self, note_id: str):
        return self._note_index.get(note_id)

    def _get_checklist(self, checklist_id: str):
        return self._checklist_index.get(checklist_id)

    def _get_task(self, task_id: str):
        return self._task_index.get(task_id)

    def _migrate_data(self):
        def fix_item_fields(items):
            for item in items:
                if "points" in item:
                    pts = item["points"]
                    if pts is None or pts == "":
                        del item["points"]
                    elif isinstance(pts, str):
                        try:
                            item["points"] = int(pts)
                        except ValueError:
                            del item["points"]
                if "dueDate" in item:
                    dd = item["dueDate"]
                    if dd is None or dd == "" or dd == 0 or dd == "0":
                        del item["dueDate"]
                    elif not isinstance(dd, str):
                        item["dueDate"] = str(dd)
                    elif len(dd) < 10 or dd[4] != '-':
                        del item["dueDate"]
                if "children" in item and item["children"]:
                    fix_item_fields(item["children"])

        for checklist in self._data.get("checklists", []):
            fix_item_fields(checklist.get("items", []))

        for task in self._data.get("tasks", []):
            fix_item_fields(task.get("items", []))

    def _format_data(self) -> dict:
        return {
            "notes": self._data.get("notes", []),
            "checklists": self._data.get("checklists", []),
            "tasks": self._data.get("tasks", []),
            "ical_sources": self._data.get("ical_sources", []),
            "imported_events": self._data.get("imported_events", []),
            "points_users": self._data.get("points_users", {}),
            "points_history": self._data.get("points_history", []),
            "points_prizes": self._data.get("points_prizes", []),
            "points_admins": self._data.get("points_admins", []),
            "achievements": self._data.get("achievements", []),
            "user_achievements": self._data.get("user_achievements", {}),
        }

    async def _async_update_data(self) -> dict:
        return self._format_data()

    async def async_save(self):
        await self.store.async_save(self._data)
        self._rebuild_indexes()
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
        note = self._get_note(note_id)
        if not note:
            raise ValueError(f"Note {note_id} not found")
        if title is not None:
            note["title"] = title
        if content is not None:
            note["content"] = content
        note["updatedAt"] = now_iso()
        await self.async_save()
        return note

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
                    "url": f"/local/community/jottick/images/{safe_filename}",
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
                    "url": f"/local/community/jottick/images/{safe_filename}",
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

    async def add_checklist_item(self, checklist_id: str, text: str, status: str = None, parent_index: str = None, points: int = None, assigned_to: str = None) -> dict:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                new_item = {"text": text, "completed": False, "children": []}
                if status:
                    new_item["status"] = status
                if points is not None:
                    new_item["points"] = points
                if assigned_to:
                    new_item["assigned_to"] = assigned_to

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
        try:
            indices = [int(i) for i in str(index_path).split(".")]
        except ValueError:
            raise ValueError(f"Invalid item index format: {index_path}. Expected format like '0' or '0.1.2'")
        
        target_list = items
        for i, idx in enumerate(indices[:-1]):
            if idx < 0 or idx >= len(target_list):
                raise ValueError(f"Item index {idx} out of range at level {i}")
            if "children" not in target_list[idx]:
                target_list[idx]["children"] = []
            target_list = target_list[idx]["children"]
        
        final_idx = indices[-1]
        if final_idx < 0 or final_idx >= len(target_list):
            raise ValueError(f"Item index {final_idx} out of range")
        
        return target_list, final_idx

    async def check_item(self, checklist_id: str, item_index: str, user_id: str = None) -> bool:
        checklist = self._get_checklist(checklist_id)
        if not checklist:
            raise ValueError(f"Checklist {checklist_id} not found")
        target_list, idx = self._get_item_by_index(checklist["items"], item_index)
        item = target_list[idx]
        item["completed"] = True

        claim_user = None
        item_points = item.get("points", 0)
        if item_points is not None and item_points != "":
            item_points = int(item_points)
        else:
            item_points = 0
        assigned_to = item.get("assigned_to", "")
        if item_points > 0 and not item.get("points_claimed"):
            claim_user = user_id or assigned_to
            if claim_user and claim_user in self._data["points_users"]:
                item["points_claimed"] = True
                item["points_claimed_by"] = claim_user
                item["points_claimed_at"] = now_iso()
                self._data["points_users"][claim_user]["points"] = self._data["points_users"][claim_user].get("points", 0) + item_points
                self._data["points_users"][claim_user]["lifetime_points"] = self._data["points_users"][claim_user].get("lifetime_points", 0) + item_points
                self._data["points_history"].append({
                    "id": generate_id()[:8],
                    "user_id": claim_user,
                    "user_name": self._data["points_users"][claim_user].get("name", ""),
                    "amount": item_points,
                    "reason": f"Completed: {item.get('text', 'item')}",
                    "item_type": "checklist",
                    "parent_id": checklist_id,
                    "item_index": item_index,
                    "timestamp": now_iso(),
                })
                self.hass.bus.async_fire("jottick_points_users_update", {"users": self._data["points_users"]})
                self.hass.bus.async_fire("jottick_points_history_update", {"history": self._data["points_history"][-50:]})

        checklist["updatedAt"] = now_iso()
        await self.async_save()
        if claim_user:
            await self.check_auto_achievements(claim_user)
        return True

    async def uncheck_item(self, checklist_id: str, item_index: str) -> bool:
        checklist = self._get_checklist(checklist_id)
        if not checklist:
            raise ValueError(f"Checklist {checklist_id} not found")
        target_list, idx = self._get_item_by_index(checklist["items"], item_index)
        target_list[idx]["completed"] = False
        checklist["updatedAt"] = now_iso()
        await self.async_save()
        return True
    
    async def check_all_items(self, checklist_id: str) -> bool:
        def check_recursive(items):
            for item in items:
                item["completed"] = True
                if "children" in item and item["children"]:
                    check_recursive(item["children"])
        
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                check_recursive(checklist["items"])
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def uncheck_all_items(self, checklist_id: str) -> bool:
        def uncheck_recursive(items):
            for item in items:
                item["completed"] = False
                if "children" in item and item["children"]:
                    uncheck_recursive(item["children"])
        
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                uncheck_recursive(checklist["items"])
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
    
    async def update_checklist_item(self, checklist_id: str, item_index: str, text: str = None, completed: bool = None) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                if text is not None:
                    target_list[idx]["text"] = text
                if completed is not None:
                    target_list[idx]["completed"] = completed
                checklist["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Checklist {checklist_id} not found")

    async def reorder_checklist_items(self, checklist_id: str, item_indices: list) -> bool:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                items = checklist["items"]
                try:
                    new_order = [items[int(i)] for i in item_indices]
                except (IndexError, ValueError) as e:
                    raise ValueError(f"Invalid item indices: {e}")
                if len(new_order) != len(items):
                    raise ValueError("Must provide all item indices")
                checklist["items"] = new_order
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

    async def add_task_item(self, task_id: str, text: str, status: str = "todo", parent_index: str = None, points: int = None, assigned_to: str = None) -> dict:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                new_item = {"text": text, "status": status, "children": []}
                if points is not None:
                    new_item["points"] = points
                if assigned_to:
                    new_item["assigned_to"] = assigned_to

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

    async def update_task_item_status(self, task_id: str, item_index: str, status: str, user_id: str = None) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                target_list, idx = self._get_item_by_index(task["items"], item_index)
                item = target_list[idx]
                item["status"] = status

                claim_user = None
                if status == "completed":
                    item_points = item.get("points", 0)
                    if item_points is not None and item_points != "":
                        item_points = int(item_points)
                    else:
                        item_points = 0
                    assigned_to = item.get("assigned_to", "")
                    if item_points > 0 and not item.get("points_claimed"):
                        claim_user = user_id or assigned_to
                        if claim_user and claim_user in self._data["points_users"]:
                            item["points_claimed"] = True
                            item["points_claimed_by"] = claim_user
                            item["points_claimed_at"] = now_iso()
                            self._data["points_users"][claim_user]["points"] = self._data["points_users"][claim_user].get("points", 0) + item_points
                            self._data["points_users"][claim_user]["lifetime_points"] = self._data["points_users"][claim_user].get("lifetime_points", 0) + item_points
                            self._data["points_history"].append({
                                "id": generate_id()[:8],
                                "user_id": claim_user,
                                "user_name": self._data["points_users"][claim_user].get("name", ""),
                                "amount": item_points,
                                "reason": f"Completed: {item.get('text', 'item')}",
                                "item_type": "task",
                                "parent_id": task_id,
                                "item_index": item_index,
                                "timestamp": now_iso(),
                            })
                            self.hass.bus.async_fire("jottick_points_users_update", {"users": self._data["points_users"]})
                            self.hass.bus.async_fire("jottick_points_history_update", {"history": self._data["points_history"][-50:]})

                    def cascade_complete(items):
                        for child in items:
                            child["status"] = "completed"
                            if child.get("children"):
                                cascade_complete(child["children"])
                    cascade_complete(item.get("children", []))

                task["updatedAt"] = now_iso()
                await self.async_save()
                if claim_user:
                    await self.check_auto_achievements(claim_user)
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
    
    async def update_task_item(self, task_id: str, item_index: str, text: str = None, status: str = None) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                target_list, idx = self._get_item_by_index(task["items"], item_index)
                if text is not None:
                    target_list[idx]["text"] = text
                if status is not None:
                    target_list[idx]["status"] = status
                    if status == "completed":
                        def cascade_complete(items):
                            for item in items:
                                item["status"] = "completed"
                                if item.get("children"):
                                    cascade_complete(item["children"])
                        cascade_complete(target_list[idx].get("children", []))
                task["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def reorder_task_items(self, task_id: str, item_indices: list) -> bool:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                items = task["items"]
                try:
                    new_order = [items[int(i)] for i in item_indices]
                except (IndexError, ValueError) as e:
                    raise ValueError(f"Invalid item indices: {e}")
                if len(new_order) != len(items):
                    raise ValueError("Must provide all item indices")
                task["items"] = new_order
                task["updatedAt"] = now_iso()
                await self.async_save()
                return True
        raise ValueError(f"Task {task_id} not found")

    async def duplicate_note(self, note_id: str, new_title: str = None) -> dict:
        for note in self._data["notes"]:
            if note["id"] == note_id:
                new_note = copy.deepcopy(note)
                new_note["id"] = generate_id()
                new_note["title"] = new_title or f"{note['title']} (copy)"
                new_note["createdAt"] = now_iso()
                new_note["updatedAt"] = now_iso()
                self._data["notes"].append(new_note)
                await self.async_save()
                return new_note
        raise ValueError(f"Note {note_id} not found")

    async def duplicate_checklist(self, checklist_id: str, new_title: str = None) -> dict:
        for checklist in self._data["checklists"]:
            if checklist["id"] == checklist_id:
                new_checklist = copy.deepcopy(checklist)
                new_checklist["id"] = generate_id()
                new_checklist["title"] = new_title or f"{checklist['title']} (copy)"
                new_checklist["createdAt"] = now_iso()
                new_checklist["updatedAt"] = now_iso()
                self._data["checklists"].append(new_checklist)
                await self.async_save()
                return new_checklist
        raise ValueError(f"Checklist {checklist_id} not found")

    async def duplicate_task(self, task_id: str, new_title: str = None) -> dict:
        for task in self._data["tasks"]:
            if task["id"] == task_id:
                new_task = copy.deepcopy(task)
                new_task["id"] = generate_id()
                new_task["title"] = new_title or f"{task['title']} (copy)"
                new_task["createdAt"] = now_iso()
                new_task["updatedAt"] = now_iso()
                self._data["tasks"].append(new_task)
                await self.async_save()
                return new_task
        raise ValueError(f"Task {task_id} not found")

    async def create_points_user(self, name: str, user_id: str = None, linked_ha_user: str = None, linked_device: str = None) -> dict:
        if user_id is None:
            user_id = generate_id()[:8]

        if user_id in self._data["points_users"]:
            raise ValueError(f"User ID {user_id} already exists")

        user = {
            "id": user_id,
            "name": name,
            "points": 0,
            "lifetime_points": 0,
            "linked_ha_user": linked_ha_user,
            "linked_device": linked_device,
            "created_at": now_iso(),
        }

        self._data["points_users"][user_id] = user
        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })

        return user

    async def update_points_user(self, user_id: str, name: str = None, linked_ha_user: str = None, linked_device: str = None) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        user = self._data["points_users"][user_id]
        if name is not None:
            user["name"] = name
        if linked_ha_user is not None:
            user["linked_ha_user"] = linked_ha_user
        if linked_device is not None:
            user["linked_device"] = linked_device

        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })

        return user

    async def delete_points_user(self, user_id: str) -> bool:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        del self._data["points_users"][user_id]
        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })

        return True

    async def adjust_user_points(self, user_id: str, amount: int, reason: str = "", admin_id: str = None) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        user = self._data["points_users"][user_id]
        old_points = user["points"]
        user["points"] += amount

        if amount > 0:
            user["lifetime_points"] = user.get("lifetime_points", 0) + amount

        history_entry = {
            "id": generate_id()[:8],
            "user_id": user_id,
            "user_name": user["name"],
            "amount": amount,
            "old_balance": old_points,
            "new_balance": user["points"],
            "reason": reason,
            "type": "adjustment",
            "admin_id": admin_id,
            "timestamp": now_iso(),
        }

        self._data["points_history"].append(history_entry)

        if len(self._data["points_history"]) > 1000:
            self._data["points_history"] = self._data["points_history"][-1000:]

        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })
        self.hass.bus.async_fire("jottick_points_history_update", {
            "history": self._data["points_history"]
        })

        if amount > 0:
            await self.check_auto_achievements(user_id)

        return user

    async def claim_item_points(
        self,
        user_id: str,
        item_type: str,
        parent_id: str,
        item_index: str,
        points: int = None,
        claimed_by_admin: str = None
    ) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        item_text = ""
        parent_title = ""

        if item_type == "checklist":
            for checklist in self._data["checklists"]:
                if checklist["id"] == parent_id:
                    target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                    item = target_list[idx]
                    item_text = item.get("text", "")
                    parent_title = checklist.get("title", "")

                    if item.get("points_claimed"):
                        raise ValueError("Points already claimed for this item")

                    assigned_to = item.get("assigned_to", "")
                    if assigned_to and assigned_to != user_id and not claimed_by_admin:
                        assigned_user = self._data["points_users"].get(assigned_to, {})
                        raise ValueError(f"This item is assigned to {assigned_user.get('name', 'another user')}. Only they can claim it.")

                    if points is None:
                        points = item.get("points", 1)
                        if points is not None and points != "":
                            points = int(points)
                        else:
                            points = 1

                    if points == 0:
                        raise ValueError("This item has no points assigned")

                    item["points_claimed"] = True
                    item["points_claimed_by"] = user_id
                    item["points_claimed_at"] = now_iso()
                    checklist["updatedAt"] = now_iso()
                    break
            else:
                raise ValueError(f"Checklist {parent_id} not found")

        elif item_type == "task":
            for task in self._data["tasks"]:
                if task["id"] == parent_id:
                    target_list, idx = self._get_item_by_index(task["items"], item_index)
                    item = target_list[idx]
                    item_text = item.get("text", "")
                    parent_title = task.get("title", "")

                    if item.get("points_claimed"):
                        raise ValueError("Points already claimed for this item")

                    assigned_to = item.get("assigned_to", "")
                    if assigned_to and assigned_to != user_id and not claimed_by_admin:
                        assigned_user = self._data["points_users"].get(assigned_to, {})
                        raise ValueError(f"This item is assigned to {assigned_user.get('name', 'another user')}. Only they can claim it.")

                    if points is None:
                        points = item.get("points", 5)
                        if points is not None and points != "":
                            points = int(points)
                        else:
                            points = 5

                    if points == 0:
                        raise ValueError("This item has no points assigned")

                    item["points_claimed"] = True
                    item["points_claimed_by"] = user_id
                    item["points_claimed_at"] = now_iso()
                    task["updatedAt"] = now_iso()
                    break
            else:
                raise ValueError(f"Task {parent_id} not found")
        else:
            raise ValueError(f"Unknown item type: {item_type}")

        user = self._data["points_users"][user_id]
        old_points = user["points"]
        user["points"] += points
        user["lifetime_points"] = user.get("lifetime_points", 0) + points

        history_entry = {
            "id": generate_id()[:8],
            "user_id": user_id,
            "user_name": user["name"],
            "amount": points,
            "old_balance": old_points,
            "new_balance": user["points"],
            "reason": f"Completed: {item_text}" if item_text else "Item completed",
            "type": "claim",
            "item_type": item_type,
            "parent_id": parent_id,
            "parent_title": parent_title,
            "item_index": item_index,
            "claimed_by_admin": claimed_by_admin,
            "timestamp": now_iso(),
        }

        self._data["points_history"].append(history_entry)

        if len(self._data["points_history"]) > 1000:
            self._data["points_history"] = self._data["points_history"][-1000:]

        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })
        self.hass.bus.async_fire("jottick_points_history_update", {
            "history": self._data["points_history"]
        })

        await self.check_auto_achievements(user_id)

        return {"user": user, "points_earned": points, "history_entry": history_entry}

    async def set_item_points(self, item_type: str, parent_id: str, item_index: str, points: int) -> bool:
        if item_type == "checklist":
            for checklist in self._data["checklists"]:
                if checklist["id"] == parent_id:
                    target_list, idx = self._get_item_by_index(checklist["items"], item_index)
                    target_list[idx]["points"] = points
                    checklist["updatedAt"] = now_iso()
                    await self.async_save()
                    return True
            raise ValueError(f"Checklist {parent_id} not found")

        elif item_type == "task":
            for task in self._data["tasks"]:
                if task["id"] == parent_id:
                    target_list, idx = self._get_item_by_index(task["items"], item_index)
                    target_list[idx]["points"] = points
                    task["updatedAt"] = now_iso()
                    await self.async_save()
                    return True
            raise ValueError(f"Task {parent_id} not found")

        raise ValueError(f"Unknown item type: {item_type}")

    async def create_prize(self, name: str, cost: int, description: str = "", quantity: int = -1) -> dict:
        prize = {
            "id": generate_id()[:8],
            "name": name,
            "description": description,
            "cost": cost,
            "quantity": quantity,
            "redeemed_count": 0,
            "created_at": now_iso(),
        }

        self._data["points_prizes"].append(prize)
        await self.async_save()

        self.hass.bus.async_fire("jottick_points_prizes_update", {
            "prizes": self._data["points_prizes"]
        })

        return prize

    async def update_prize(self, prize_id: str, name: str = None, cost: int = None, description: str = None, quantity: int = None) -> dict:
        for prize in self._data["points_prizes"]:
            if prize["id"] == prize_id:
                if name is not None:
                    prize["name"] = name
                if cost is not None:
                    prize["cost"] = cost
                if description is not None:
                    prize["description"] = description
                if quantity is not None:
                    prize["quantity"] = quantity

                await self.async_save()

                self.hass.bus.async_fire("jottick_points_prizes_update", {
                    "prizes": self._data["points_prizes"]
                })

                return prize

        raise ValueError(f"Prize {prize_id} not found")

    async def delete_prize(self, prize_id: str) -> bool:
        for i, prize in enumerate(self._data["points_prizes"]):
            if prize["id"] == prize_id:
                del self._data["points_prizes"][i]
                await self.async_save()

                self.hass.bus.async_fire("jottick_points_prizes_update", {
                    "prizes": self._data["points_prizes"]
                })

                return True

        raise ValueError(f"Prize {prize_id} not found")

    async def redeem_prize(self, user_id: str, prize_id: str) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        prize = None
        for p in self._data["points_prizes"]:
            if p["id"] == prize_id:
                prize = p
                break

        if prize is None:
            raise ValueError(f"Prize {prize_id} not found")

        user = self._data["points_users"][user_id]

        if user["points"] < prize["cost"]:
            raise ValueError(f"Insufficient points. Need {prize['cost']}, have {user['points']}")

        if prize["quantity"] != -1 and prize["quantity"] <= prize.get("redeemed_count", 0):
            raise ValueError("Prize is out of stock")

        old_points = user["points"]
        user["points"] -= prize["cost"]
        prize["redeemed_count"] = prize.get("redeemed_count", 0) + 1

        history_entry = {
            "id": generate_id()[:8],
            "user_id": user_id,
            "user_name": user["name"],
            "amount": -prize["cost"],
            "old_balance": old_points,
            "new_balance": user["points"],
            "reason": f"Redeemed: {prize['name']}",
            "type": "redemption",
            "prize_id": prize_id,
            "prize_name": prize["name"],
            "timestamp": now_iso(),
        }

        self._data["points_history"].append(history_entry)

        if len(self._data["points_history"]) > 1000:
            self._data["points_history"] = self._data["points_history"][-1000:]

        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })
        self.hass.bus.async_fire("jottick_points_prizes_update", {
            "prizes": self._data["points_prizes"]
        })
        self.hass.bus.async_fire("jottick_points_history_update", {
            "history": self._data["points_history"]
        })

        return {"user": user, "prize": prize, "history_entry": history_entry}

    async def set_points_admins(self, admin_ids: list) -> list:
        self._data["points_admins"] = admin_ids
        await self.async_save()

        self.hass.bus.async_fire("jottick_points_admins_update", {
            "admins": self._data["points_admins"]
        })

        return admin_ids

    async def add_points_admin(self, admin_id: str) -> list:
        if admin_id not in self._data["points_admins"]:
            self._data["points_admins"].append(admin_id)
            await self.async_save()

            self.hass.bus.async_fire("jottick_points_admins_update", {
                "admins": self._data["points_admins"]
            })

        return self._data["points_admins"]

    async def remove_points_admin(self, admin_id: str) -> list:
        if admin_id in self._data["points_admins"]:
            self._data["points_admins"].remove(admin_id)
            await self.async_save()

            self.hass.bus.async_fire("jottick_points_admins_update", {
                "admins": self._data["points_admins"]
            })

        return self._data["points_admins"]

    async def reset_user_points(self, user_id: str, admin_id: str = None) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        user = self._data["points_users"][user_id]
        old_points = user["points"]
        user["points"] = 0

        history_entry = {
            "id": generate_id()[:8],
            "user_id": user_id,
            "user_name": user["name"],
            "amount": -old_points,
            "old_balance": old_points,
            "new_balance": 0,
            "reason": "Points reset by admin",
            "type": "reset",
            "admin_id": admin_id,
            "timestamp": now_iso(),
        }

        self._data["points_history"].append(history_entry)

        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })
        self.hass.bus.async_fire("jottick_points_history_update", {
            "history": self._data["points_history"]
        })

        return user

    async def deduct_user_points(self, user_id: str, amount: int, reason: str, admin_id: str = None) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        if amount is None:
            raise ValueError("Amount is required")

        if amount <= 0:
            raise ValueError("Amount must be positive")

        user = self._data["points_users"][user_id]
        old_points = user["points"]
        user["points"] = max(0, user["points"] - amount)

        history_entry = {
            "id": generate_id()[:8],
            "user_id": user_id,
            "user_name": user["name"],
            "amount": -amount,
            "old_balance": old_points,
            "new_balance": user["points"],
            "reason": reason or "Points deducted",
            "type": "penalty",
            "admin_id": admin_id,
            "timestamp": now_iso(),
        }

        self._data["points_history"].append(history_entry)

        if len(self._data["points_history"]) > 1000:
            self._data["points_history"] = self._data["points_history"][-1000:]

        await self.async_save()

        self.hass.bus.async_fire("jottick_points_users_update", {
            "users": self._data["points_users"]
        })
        self.hass.bus.async_fire("jottick_points_history_update", {
            "history": self._data["points_history"]
        })

        return {"user": user, "points_deducted": amount, "history_entry": history_entry}

    async def create_achievement(self, name: str, description: str = "", points_threshold: int = 0, achievement_id: str = None) -> dict:
        if achievement_id is None:
            achievement_id = generate_id()[:8]

        for a in self._data.get("achievements", []):
            if a["id"] == achievement_id:
                raise ValueError(f"Achievement ID {achievement_id} already exists")

        achievement = {
            "id": achievement_id,
            "name": name,
            "description": description,
            "image": "",
            "points_threshold": points_threshold,
            "created_at": now_iso(),
        }

        if "achievements" not in self._data:
            self._data["achievements"] = []
        self._data["achievements"].append(achievement)
        await self.async_save()

        self.hass.bus.async_fire("jottick_achievements_update", {
            "achievements": self._data["achievements"]
        })

        return achievement

    async def update_achievement(self, achievement_id: str, name: str = None, description: str = None, points_threshold: int = None) -> dict:
        for achievement in self._data.get("achievements", []):
            if achievement["id"] == achievement_id:
                if name is not None:
                    achievement["name"] = name
                if description is not None:
                    achievement["description"] = description
                if points_threshold is not None:
                    achievement["points_threshold"] = points_threshold

                await self.async_save()

                self.hass.bus.async_fire("jottick_achievements_update", {
                    "achievements": self._data["achievements"]
                })

                return achievement

        raise ValueError(f"Achievement {achievement_id} not found")

    async def delete_achievement(self, achievement_id: str) -> bool:
        achievements = self._data.get("achievements", [])
        for i, achievement in enumerate(achievements):
            if achievement["id"] == achievement_id:
                if achievement.get("image"):
                    img_path = self.hass.config.path(POINTS_DIR, f"achievement_{achievement_id}")
                    for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                        full_path = img_path + ext
                        if os.path.exists(full_path):
                            try:
                                os.remove(full_path)
                            except:
                                pass

                del achievements[i]
                await self.async_save()

                self.hass.bus.async_fire("jottick_achievements_update", {
                    "achievements": self._data["achievements"]
                })

                return True

        raise ValueError(f"Achievement {achievement_id} not found")

    async def award_achievement(self, user_id: str, achievement_id: str, admin_id: str = None) -> dict:
        if user_id not in self._data["points_users"]:
            raise ValueError(f"User {user_id} not found")

        achievement = None
        for a in self._data.get("achievements", []):
            if a["id"] == achievement_id:
                achievement = a
                break

        if achievement is None:
            raise ValueError(f"Achievement {achievement_id} not found")

        if "user_achievements" not in self._data:
            self._data["user_achievements"] = {}
        if user_id not in self._data["user_achievements"]:
            self._data["user_achievements"][user_id] = []

        for ua in self._data["user_achievements"][user_id]:
            if ua["achievement_id"] == achievement_id:
                raise ValueError(f"User already has this achievement")

        user_achievement = {
            "achievement_id": achievement_id,
            "awarded_at": now_iso(),
            "awarded_by": admin_id,
        }

        self._data["user_achievements"][user_id].append(user_achievement)

        await self.async_save()

        self.hass.bus.async_fire("jottick_user_achievements_update", {
            "user_achievements": self._data["user_achievements"]
        })

        user = self._data["points_users"][user_id]
        return {"user": user, "achievement": achievement}

    async def revoke_achievement(self, user_id: str, achievement_id: str) -> bool:
        if user_id not in self._data.get("user_achievements", {}):
            raise ValueError(f"User {user_id} has no achievements")

        user_achievements = self._data["user_achievements"][user_id]
        for i, ua in enumerate(user_achievements):
            if ua["achievement_id"] == achievement_id:
                del user_achievements[i]
                await self.async_save()

                self.hass.bus.async_fire("jottick_user_achievements_update", {
                    "user_achievements": self._data["user_achievements"]
                })

                return True

        raise ValueError(f"User does not have this achievement")

    async def check_auto_achievements(self, user_id: str) -> list:
        if user_id not in self._data.get("points_users", {}):
            return []

        user = self._data["points_users"][user_id]
        lifetime_points = user.get("lifetime_points", 0)

        user_achievement_ids = set()
        for ua in self._data.get("user_achievements", {}).get(user_id, []):
            user_achievement_ids.add(ua["achievement_id"])

        awarded = []
        for achievement in self._data.get("achievements", []):
            threshold = achievement.get("points_threshold", 0)
            if threshold <= 0 or achievement["id"] in user_achievement_ids:
                continue

            if lifetime_points >= threshold:
                if "user_achievements" not in self._data:
                    self._data["user_achievements"] = {}
                if user_id not in self._data["user_achievements"]:
                    self._data["user_achievements"][user_id] = []

                user_achievement = {
                    "achievement_id": achievement["id"],
                    "awarded_at": now_iso(),
                    "awarded_by": "auto",
                }
                self._data["user_achievements"][user_id].append(user_achievement)
                awarded.append(achievement)

        if awarded:
            await self.async_save()
            self.hass.bus.async_fire("jottick_user_achievements_update", {
                "user_achievements": self._data["user_achievements"]
            })

        return awarded

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
                "event_count": len(self._data["imported_events"])
            })

            return source_record
            
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Failed to fetch iCal: {e}")
            raise ValueError(f"Failed to fetch iCal: {e}")
        except Exception as e:
            _LOGGER.error(f"Failed to import iCal: {e}")
            raise

    def _parse_ical_datetime(self, dt_line: str, event_data: str) -> tuple:
        date_str = None
        time_str = None
        is_all_day = False

        if 'VALUE=DATE' in dt_line and 'VALUE=DATE-TIME' not in dt_line:
            is_all_day = True
            match = re.search(r':(\d{8})$', dt_line)
            if match:
                dt_val = match.group(1)
                date_str = f"{dt_val[:4]}-{dt_val[4:6]}-{dt_val[6:8]}"
            return date_str, None, is_all_day

        tzid = None
        tzid_match = re.search(r'TZID=([^:;]+)', dt_line)
        if tzid_match:
            tzid = tzid_match.group(1)

        value_match = re.search(r':(\d{8}(?:T\d{6}Z?)?)$', dt_line)
        if not value_match:
            return None, None, False

        dt_val = value_match.group(1)

        if len(dt_val) >= 8:
            date_str = f"{dt_val[:4]}-{dt_val[4:6]}-{dt_val[6:8]}"

        if len(dt_val) >= 15:
            hour = int(dt_val[9:11])
            minute = int(dt_val[11:13])
            second = int(dt_val[13:15]) if len(dt_val) >= 15 else 0

            is_utc = dt_val.endswith('Z')

            try:
                dt = datetime(
                    int(dt_val[:4]), int(dt_val[4:6]), int(dt_val[6:8]),
                    hour, minute, second
                )

                if is_utc:
                    dt = dt.replace(tzinfo=ZoneInfo('UTC'))
                    local_tz = self.hass.config.time_zone
                    if local_tz:
                        try:
                            dt = dt.astimezone(ZoneInfo(local_tz))
                        except Exception:
                            pass
                elif tzid:
                    try:
                        dt = dt.replace(tzinfo=ZoneInfo(tzid))
                        local_tz = self.hass.config.time_zone
                        if local_tz:
                            dt = dt.astimezone(ZoneInfo(local_tz))
                    except Exception:
                        pass

                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M")
            except Exception as e:
                _LOGGER.debug(f"Error parsing datetime {dt_val}: {e}")
                time_str = f"{dt_val[9:11]}:{dt_val[11:13]}"

        return date_str, time_str, is_all_day

    def _parse_ical_data(self, ical_data: str, source_url: str) -> list:
        events = []

        ical_data = re.sub(r'\r?\n[ \t]', '', ical_data)
        ical_data = ical_data.replace('\r\n', '\n').replace('\r', '\n')

        vtimezone_pattern = re.compile(r'BEGIN:VTIMEZONE(.*?)END:VTIMEZONE', re.DOTALL)
        timezones = {}
        for tz_match in vtimezone_pattern.finditer(ical_data):
            tz_data = tz_match.group(1)
            tzid_match = re.search(r'^TZID:(.+?)$', tz_data, re.MULTILINE)
            if tzid_match:
                timezones[tzid_match.group(1).strip()] = tz_data

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
                uid_val = uid_match.group(1).strip()
                if ':' in uid_val:
                    uid_val = uid_val.split(':', 1)[-1]
                event["original_uid"] = uid_val

            summary_match = re.search(r'^SUMMARY[;:](.+?)$', event_data, re.MULTILINE)
            if summary_match:
                title = summary_match.group(1).strip()
                if ';' in title.split(':')[0] if ':' in title else False:
                    title = title.split(':', 1)[-1] if ':' in title else title
                elif ':' in title and not title.startswith('\\'):
                    parts = title.split(':')
                    if len(parts) > 1 and '=' in parts[0]:
                        title = ':'.join(parts[1:])
                event["title"] = title.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\\\", "\\")
            else:
                event["title"] = "Untitled Event"

            desc_match = re.search(r'^DESCRIPTION[;:](.+?)$', event_data, re.MULTILINE)
            if desc_match:
                desc = desc_match.group(1).strip()
                if ':' in desc:
                    parts = desc.split(':')
                    if len(parts) > 1 and '=' in parts[0]:
                        desc = ':'.join(parts[1:])
                event["description"] = desc.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

            dtstart_match = re.search(r'^(DTSTART[^:\n]*:\S+)$', event_data, re.MULTILINE)
            if dtstart_match:
                dtstart_line = dtstart_match.group(1)
                start_date, start_time, is_all_day = self._parse_ical_datetime(dtstart_line, event_data)
                if start_date:
                    event["date"] = start_date
                    if start_time and not is_all_day:
                        event["time"] = start_time
                    event["all_day"] = is_all_day

            dtend_match = re.search(r'^(DTEND[^:\n]*:\S+)$', event_data, re.MULTILINE)
            if dtend_match:
                dtend_line = dtend_match.group(1)
                end_date, end_time, end_is_all_day = self._parse_ical_datetime(dtend_line, event_data)
                if end_date:
                    event["end_date"] = end_date
                    if end_time and not end_is_all_day:
                        event["end_time"] = end_time

            if "end_date" not in event:
                duration_match = re.search(r'^DURATION:(.+?)$', event_data, re.MULTILINE)
                if duration_match and "date" in event:
                    duration = duration_match.group(1).strip()
                    try:
                        days = 0
                        hours = 0
                        minutes = 0

                        day_match = re.search(r'(\d+)D', duration)
                        if day_match:
                            days = int(day_match.group(1))

                        hour_match = re.search(r'(\d+)H', duration)
                        if hour_match:
                            hours = int(hour_match.group(1))

                        min_match = re.search(r'(\d+)M', duration)
                        if min_match and 'T' in duration:
                            minutes = int(min_match.group(1))

                        start_dt = datetime.strptime(event["date"], "%Y-%m-%d")
                        if event.get("time"):
                            start_dt = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")

                        end_dt = start_dt + timedelta(days=days, hours=hours, minutes=minutes)
                        event["end_date"] = end_dt.strftime("%Y-%m-%d")
                        if hours or minutes:
                            event["end_time"] = end_dt.strftime("%H:%M")
                    except Exception as e:
                        _LOGGER.debug(f"Error parsing duration {duration}: {e}")

            location_match = re.search(r'^LOCATION[;:](.+?)$', event_data, re.MULTILINE)
            if location_match:
                loc = location_match.group(1).strip()
                if ':' in loc:
                    parts = loc.split(':')
                    if len(parts) > 1 and '=' in parts[0]:
                        loc = ':'.join(parts[1:])
                event["location"] = loc.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

            rrule_match = re.search(r'^RRULE:(.+?)$', event_data, re.MULTILINE)
            if rrule_match:
                rrule = rrule_match.group(1).strip()
                event["rrule"] = rrule

                recurring_events = self._expand_rrule(event, rrule)
                if recurring_events:
                    events.extend(recurring_events)
                    continue

            status_match = re.search(r'^STATUS:(.+?)$', event_data, re.MULTILINE)
            if status_match:
                event["status"] = status_match.group(1).strip()

            categories_match = re.search(r'^CATEGORIES[;:](.+?)$', event_data, re.MULTILINE)
            if categories_match:
                cats = categories_match.group(1).strip()
                if ':' in cats:
                    parts = cats.split(':')
                    if len(parts) > 1 and '=' in parts[0]:
                        cats = ':'.join(parts[1:])
                event["categories"] = [c.strip() for c in cats.split(',')]

            if "date" in event:
                events.append(event)
                _LOGGER.debug(f"Parsed iCal event: {event.get('title')} on {event.get('date')}")

        _LOGGER.info(f"Parsed {len(events)} events from iCal source: {source_url}")
        return events

    def _expand_rrule(self, base_event: dict, rrule: str, max_instances: int = 52) -> list:
        events = []

        try:
            rrule_parts = {}
            for part in rrule.split(';'):
                if '=' in part:
                    key, value = part.split('=', 1)
                    rrule_parts[key] = value

            freq = rrule_parts.get('FREQ', 'DAILY')
            count = int(rrule_parts.get('COUNT', max_instances))
            interval = int(rrule_parts.get('INTERVAL', 1))
            until = rrule_parts.get('UNTIL')
            byday = rrule_parts.get('BYDAY', '').split(',') if 'BYDAY' in rrule_parts else []

            count = min(count, max_instances)

            base_date = datetime.strptime(base_event["date"], "%Y-%m-%d")
            base_time = base_event.get("time")

            until_date = None
            if until:
                try:
                    if len(until) == 8:
                        until_date = datetime.strptime(until, "%Y%m%d")
                    elif len(until) >= 15:
                        until_date = datetime.strptime(until[:15], "%Y%m%dT%H%M%S")
                except:
                    pass

            day_map = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}

            current_date = base_date
            generated = 0

            while generated < count:
                if until_date and current_date > until_date:
                    break

                if freq == 'WEEKLY' and byday:
                    week_start = current_date - timedelta(days=current_date.weekday())
                    for day_code in byday:
                        clean_day = ''.join(c for c in day_code if c.isalpha())
                        if clean_day in day_map:
                            day_offset = day_map[clean_day]
                            event_date = week_start + timedelta(days=day_offset)

                            if event_date >= base_date and generated < count:
                                if until_date and event_date > until_date:
                                    continue

                                new_event = base_event.copy()
                                new_event["id"] = f"imported_{generate_id()[:8]}"
                                new_event["date"] = event_date.strftime("%Y-%m-%d")
                                new_event["recurring"] = True
                                events.append(new_event)
                                generated += 1

                    current_date += timedelta(weeks=interval)
                else:
                    new_event = base_event.copy()
                    new_event["id"] = f"imported_{generate_id()[:8]}"
                    new_event["date"] = current_date.strftime("%Y-%m-%d")
                    new_event["recurring"] = True
                    events.append(new_event)
                    generated += 1

                    if freq == 'DAILY':
                        current_date += timedelta(days=interval)
                    elif freq == 'WEEKLY':
                        current_date += timedelta(weeks=interval)
                    elif freq == 'MONTHLY':
                        month = current_date.month + interval
                        year = current_date.year + (month - 1) // 12
                        month = ((month - 1) % 12) + 1
                        day = min(current_date.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
                        current_date = current_date.replace(year=year, month=month, day=day)
                    elif freq == 'YEARLY':
                        current_date = current_date.replace(year=current_date.year + interval)
                    else:
                        break

        except Exception as e:
            _LOGGER.debug(f"Error expanding RRULE {rrule}: {e}")
            return [base_event]

        return events if events else [base_event]

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
            "event_count": len(self._data["imported_events"])
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
            "event_count": len(self._data["imported_events"])
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
        
        return f"/local/community/jottick/calendar/{filename}.ics"

    def _export_items_due_dates(self, lines: list, items: list, parent: dict, item_type: str, now_stamp: str, prefix: str = ""):
        for i, item in enumerate(items):
            index_path = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"

            due_date = item.get("dueDate")
            if due_date and isinstance(due_date, str) and len(due_date) >= 10 and due_date[4] == '-':
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
    
    async def handle_check_all_items(call: ServiceCall):
        coordinator = get_coordinator()
        checklist_id = call.data["checklist_id"]
        await coordinator.check_all_items(checklist_id)

    async def handle_uncheck_all_items(call: ServiceCall):
        coordinator = get_coordinator()
        checklist_id = call.data["checklist_id"]
        await coordinator.uncheck_all_items(checklist_id)
        
    async def handle_delete_checklist(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_checklist(checklist_id=call.data.get("checklist_id"))

    async def handle_add_checklist_item(call: ServiceCall):
        coordinator = get_coordinator()
        points = call.data.get("points")
        if points is not None:
            points = int(points) if str(points).strip() else None
        await coordinator.add_checklist_item(
            checklist_id=call.data.get("checklist_id"),
            text=call.data.get("text"),
            status=call.data.get("status"),
            parent_index=call.data.get("parent_index"),
            points=points,
            assigned_to=call.data.get("assigned_to") or None,
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
    
    async def handle_update_checklist_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_checklist_item(
            checklist_id=call.data.get("checklist_id"),
            item_index=str(call.data.get("item_index")),
            text=call.data.get("text"),
            completed=call.data.get("completed"),
        )

    async def handle_reorder_checklist_items(call: ServiceCall):
        coordinator = get_coordinator()
        indices = call.data.get("item_indices", "")
        if isinstance(indices, str):
            indices = [i.strip() for i in indices.split(",")]
        await coordinator.reorder_checklist_items(
            checklist_id=call.data.get("checklist_id"),
            item_indices=indices,
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
        points = call.data.get("points")
        if points is not None:
            points = int(points) if str(points).strip() else None
        await coordinator.add_task_item(
            task_id=call.data.get("task_id"),
            text=call.data.get("text"),
            status=call.data.get("status", "todo"),
            parent_index=call.data.get("parent_index"),
            points=points,
            assigned_to=call.data.get("assigned_to") or None,
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
    
    async def handle_update_task_item(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_task_item(
            task_id=call.data.get("task_id"),
            item_index=str(call.data.get("item_index")),
            text=call.data.get("text"),
            status=call.data.get("status"),
        )

    async def handle_reorder_task_items(call: ServiceCall):
        coordinator = get_coordinator()
        indices = call.data.get("item_indices", "")
        if isinstance(indices, str):
            indices = [i.strip() for i in indices.split(",")]
        await coordinator.reorder_task_items(
            task_id=call.data.get("task_id"),
            item_indices=indices,
        )

    async def handle_duplicate_note(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.duplicate_note(
            note_id=call.data.get("note_id"),
            new_title=call.data.get("new_title"),
        )

    async def handle_duplicate_checklist(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.duplicate_checklist(
            checklist_id=call.data.get("checklist_id"),
            new_title=call.data.get("new_title"),
        )

    async def handle_duplicate_task(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.duplicate_task(
            task_id=call.data.get("task_id"),
            new_title=call.data.get("new_title"),
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

    async def handle_create_points_user(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_points_user(
            name=call.data.get("name"),
            user_id=call.data.get("user_id"),
            linked_ha_user=call.data.get("linked_ha_user"),
            linked_device=call.data.get("linked_device"),
        )

    async def handle_update_points_user(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_points_user(
            user_id=call.data.get("user_id"),
            name=call.data.get("name"),
            linked_ha_user=call.data.get("linked_ha_user"),
            linked_device=call.data.get("linked_device"),
        )

    async def handle_delete_points_user(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_points_user(user_id=call.data.get("user_id"))

    async def handle_adjust_user_points(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.adjust_user_points(
            user_id=call.data.get("user_id"),
            amount=call.data.get("amount"),
            reason=call.data.get("reason", ""),
            admin_id=call.data.get("admin_id"),
        )

    async def handle_claim_item_points(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.claim_item_points(
            user_id=call.data.get("user_id"),
            item_type=call.data.get("item_type"),
            parent_id=call.data.get("parent_id"),
            item_index=str(call.data.get("item_index")),
            points=call.data.get("points"),
            claimed_by_admin=call.data.get("claimed_by_admin"),
        )

    async def handle_set_item_points(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.set_item_points(
            item_type=call.data.get("item_type"),
            parent_id=call.data.get("parent_id"),
            item_index=str(call.data.get("item_index")),
            points=call.data.get("points"),
        )

    async def handle_create_prize(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_prize(
            name=call.data.get("name"),
            cost=call.data.get("cost"),
            description=call.data.get("description", ""),
            quantity=call.data.get("quantity", -1),
        )

    async def handle_update_prize(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_prize(
            prize_id=call.data.get("prize_id"),
            name=call.data.get("name"),
            cost=call.data.get("cost"),
            description=call.data.get("description"),
            quantity=call.data.get("quantity"),
        )

    async def handle_delete_prize(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_prize(prize_id=call.data.get("prize_id"))

    async def handle_redeem_prize(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.redeem_prize(
            user_id=call.data.get("user_id"),
            prize_id=call.data.get("prize_id"),
        )

    async def handle_set_points_admins(call: ServiceCall):
        coordinator = get_coordinator()
        admin_ids = call.data.get("admin_ids", [])
        if isinstance(admin_ids, str):
            admin_ids = [x.strip() for x in admin_ids.split(",")]
        await coordinator.set_points_admins(admin_ids=admin_ids)

    async def handle_add_points_admin(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.add_points_admin(admin_id=call.data.get("admin_id"))

    async def handle_remove_points_admin(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.remove_points_admin(admin_id=call.data.get("admin_id"))

    async def handle_reset_user_points(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.reset_user_points(
            user_id=call.data.get("user_id"),
            admin_id=call.data.get("admin_id"),
        )

    async def handle_deduct_user_points(call: ServiceCall):
        coordinator = get_coordinator()
        amount = call.data.get("amount")
        if amount is not None:
            amount = int(amount) if str(amount).strip() else None
        await coordinator.deduct_user_points(
            user_id=call.data.get("user_id"),
            amount=amount,
            reason=call.data.get("reason", ""),
            admin_id=call.data.get("admin_id"),
        )

    async def handle_create_achievement(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.create_achievement(
            name=call.data.get("name"),
            description=call.data.get("description", ""),
            points_threshold=call.data.get("points_threshold", 0),
            achievement_id=call.data.get("achievement_id"),
        )

    async def handle_update_achievement(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.update_achievement(
            achievement_id=call.data.get("achievement_id"),
            name=call.data.get("name"),
            description=call.data.get("description"),
            points_threshold=call.data.get("points_threshold"),
        )

    async def handle_delete_achievement(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.delete_achievement(achievement_id=call.data.get("achievement_id"))

    async def handle_award_achievement(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.award_achievement(
            user_id=call.data.get("user_id"),
            achievement_id=call.data.get("achievement_id"),
            admin_id=call.data.get("admin_id"),
        )

    async def handle_revoke_achievement(call: ServiceCall):
        coordinator = get_coordinator()
        await coordinator.revoke_achievement(
            user_id=call.data.get("user_id"),
            achievement_id=call.data.get("achievement_id"),
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
    
    hass.services.async_register(DOMAIN, "check_all_items", handle_check_all_items)
    hass.services.async_register(DOMAIN, "uncheck_all_items", handle_uncheck_all_items)
    hass.services.async_register(DOMAIN, "update_checklist_item", handle_update_checklist_item)
    hass.services.async_register(DOMAIN, "reorder_checklist_items", handle_reorder_checklist_items)
    hass.services.async_register(DOMAIN, "update_task_item", handle_update_task_item)
    hass.services.async_register(DOMAIN, "reorder_task_items", handle_reorder_task_items)
    hass.services.async_register(DOMAIN, "duplicate_note", handle_duplicate_note)
    hass.services.async_register(DOMAIN, "duplicate_checklist", handle_duplicate_checklist)
    hass.services.async_register(DOMAIN, "duplicate_task", handle_duplicate_task)
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

    hass.services.async_register(DOMAIN, "create_points_user", handle_create_points_user)
    hass.services.async_register(DOMAIN, "update_points_user", handle_update_points_user)
    hass.services.async_register(DOMAIN, "delete_points_user", handle_delete_points_user)
    hass.services.async_register(DOMAIN, "adjust_user_points", handle_adjust_user_points)
    hass.services.async_register(DOMAIN, "claim_item_points", handle_claim_item_points)
    hass.services.async_register(DOMAIN, "set_item_points", handle_set_item_points)
    hass.services.async_register(DOMAIN, "create_prize", handle_create_prize)
    hass.services.async_register(DOMAIN, "update_prize", handle_update_prize)
    hass.services.async_register(DOMAIN, "delete_prize", handle_delete_prize)
    hass.services.async_register(DOMAIN, "redeem_prize", handle_redeem_prize)
    hass.services.async_register(DOMAIN, "set_points_admins", handle_set_points_admins)
    hass.services.async_register(DOMAIN, "add_points_admin", handle_add_points_admin)
    hass.services.async_register(DOMAIN, "remove_points_admin", handle_remove_points_admin)
    hass.services.async_register(DOMAIN, "reset_user_points", handle_reset_user_points)
    hass.services.async_register(DOMAIN, "deduct_user_points", handle_deduct_user_points)
    hass.services.async_register(DOMAIN, "create_achievement", handle_create_achievement)
    hass.services.async_register(DOMAIN, "update_achievement", handle_update_achievement)
    hass.services.async_register(DOMAIN, "delete_achievement", handle_delete_achievement)
    hass.services.async_register(DOMAIN, "award_achievement", handle_award_achievement)
    hass.services.async_register(DOMAIN, "revoke_achievement", handle_revoke_achievement)
