from __future__ import annotations

from typing import Any

from .config import load_config
from .models import unavailable
from .secrets import current_secret_store


def _client():
    try:
        import obsws_python as obs
    except Exception as exc:
        raise RuntimeError(f"obsws-python unavailable: {exc}") from exc
    cfg = load_config().get("obs", {})
    if not cfg.get("enabled", True):
        raise PermissionError("OBS integration is disabled in config")
    host = str(cfg.get("host", "127.0.0.1"))
    port = int(cfg.get("port", 4455))
    password = current_secret_store().get("obs-websocket-password") or ""
    return obs.ReqClient(host=host, port=port, password=password, timeout=3)


def call(action: str, **kwargs) -> dict[str, Any]:
    try:
        cfg = load_config().get("obs", {})
        if not cfg.get("enabled", True):
            return {"status": "PERMISSION_DENIED", "reason": "OBS integration is disabled in config"}
        if action == "start_streaming" and not cfg.get("allow_public_streaming", False):
            return {
                "status": "PERMISSION_DENIED",
                "reason": "Public streaming is disabled. Set obs.allow_public_streaming=true first.",
            }
        client = _client()
        if action == "status":
            version = client.get_version()
            stats = client.get_stats()
            return {"status": "OK", "obs_version": getattr(version, "obs_version", None), "websocket_version": getattr(version, "obs_web_socket_version", None), "stats": vars(stats)}
        if action == "version":
            value = client.get_version()
            return {"status": "OK", "data": vars(value)}
        if action == "scenes":
            value = client.get_scene_list()
            return {"status": "OK", "current": value.current_program_scene_name, "scenes": value.scenes}
        if action == "current_scene":
            value = client.get_current_program_scene()
            return {"status": "OK", "scene": value.current_program_scene_name}
        if action == "set_scene":
            client.set_current_program_scene(str(kwargs["scene"]))
            return {"status": "OK", "scene": kwargs["scene"]}
        if action == "sources":
            scene = kwargs.get("scene") or client.get_current_program_scene().current_program_scene_name
            value = client.get_scene_item_list(scene)
            return {"status": "OK", "scene": scene, "items": value.scene_items}
        if action in {"source_show", "source_hide"}:
            scene = kwargs.get("scene") or client.get_current_program_scene().current_program_scene_name
            target = str(kwargs["source"])
            items = client.get_scene_item_list(scene).scene_items
            item = next((x for x in items if x.get("sourceName") == target), None)
            if not item:
                return {"status": "FAILED", "reason": f"Source not found: {target}"}
            client.set_scene_item_enabled(scene, int(item["sceneItemId"]), action == "source_show")
            return {"status": "OK", "source": target, "visible": action == "source_show"}
        if action == "start_recording": client.start_record(); return {"status": "OK"}
        if action == "stop_recording": value = client.stop_record(); return {"status": "OK", "output": getattr(value, "output_path", None)}
        if action == "pause_recording": client.pause_record(); return {"status": "OK"}
        if action == "resume_recording": client.resume_record(); return {"status": "OK"}
        if action == "recording_status": value = client.get_record_status(); return {"status": "OK", "data": vars(value)}
        if action == "start_streaming": client.start_stream(); return {"status": "OK", "approval_class": "high"}
        if action == "stop_streaming": client.stop_stream(); return {"status": "OK", "approval_class": "high"}
        if action == "streaming_status": value = client.get_stream_status(); return {"status": "OK", "data": vars(value)}
        if action == "stats": value = client.get_stats(); return {"status": "OK", "data": vars(value)}
        return {"status": "FAILED", "reason": f"Unknown OBS action: {action}"}
    except Exception as exc:
        return unavailable(
            f"OBS websocket unavailable: {exc}",
            "obs",
            "Start OBS, enable Tools > WebSocket Server Settings, and store its password in the OS keyring through Local Ops",
        )
