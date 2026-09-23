from pathlib import Path

from codex_local_ops.safety import redact_text, sanitize
from codex_local_ops.wsl_ops import path_translate


def test_redacts_common_secrets():
    assert "supersecret" not in redact_text("token=supersecret")
    assert "private" not in redact_text("Authorization: private")


def test_sanitize_redacts_secret_mapping_values():
    result = sanitize({"password": "topsecret", "nested": {"api_key": "abc123"}, "safe": "visible"})
    assert result["password"] == "[REDACTED]"
    assert result["nested"]["api_key"] == "[REDACTED]"
    assert result["safe"] == "visible"


def test_sanitize_keeps_job_session_id():
    result = sanitize({"session_id": "job_0123456789abcdef", "session_token": "topsecret"})
    assert result["session_id"] == "job_0123456789abcdef"
    assert result["session_token"] == "[REDACTED]"


def test_windows_path_translation():
    translated = path_translate(r"D:\Projects\demo")
    assert translated["status"] == "OK"
    assert translated["wsl_path"].lower().endswith("/projects/demo")


def test_server_exposes_bootstrap_tool_inventory():
    from codex_local_ops.server import mcp

    names = set(mcp._tool_manager._tools)  # FastMCP's registered tool inventory
    assert {
        "platform_info",
        "local_system_info",
        "ssh_agent_status",
        "wsl_list",
        "docker_info",
        "config_status",
        "trusted_roots_list",
        "job_start",
        "run_script_async",
        "job_status",
        "job_output",
        "job_cancel",
        "job_list",
        "browser_status",
        "browser_screenshot",
        "desktop_info",
        "desktop_screenshot",
        "video_info",
        "video_convert_async",
        "video_trim_async",
        "video_concat_async",
        "video_resize_async",
        "video_change_fps_async",
        "video_extract_frame_async",
        "video_remove_audio_async",
        "video_extract_audio_async",
        "video_add_audio_async",
        "video_add_subtitles_async",
        "video_to_gif_async",
        "gif_to_video_async",
        "video_contact_sheet",
        "screen_recording_status",
        "obs_status",
        "obs_streaming_status",
        "animation_backends",
        "animation_render",
    } <= names
