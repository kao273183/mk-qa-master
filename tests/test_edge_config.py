"""v1.1.0 PR-1 — EdgeConfig env-var contract.

EdgeConfig reads env at access time (via @property) so tests can
monkeypatch.setenv(...) and immediately see new values without module
reloads. This test file verifies the contract per PRD §11 + the
spec's §2 table.
"""
from __future__ import annotations

import pytest

from mk_qa_master.config import EdgeConfig


@pytest.fixture
def cfg(monkeypatch):
    """Fresh EdgeConfig with every QA_* env var cleared so each test
    declares its own state."""
    for k in (
        "QA_RTSP_SOURCE", "QA_RTSP_PORT", "QA_RTSP_PATH",
        "QA_JETSON_HOST", "QA_INFERENCE_ENDPOINT", "QA_MODEL_PATH",
        "QA_MIN_FPS", "QA_LATENCY_SLA_MS", "QA_IOU_THRESHOLD",
        "QA_MEDIAMTX_BIN", "QA_DEVICE_TIMEOUT_S",
        "QA_EDGE_ALLOW_VENDOR_HOSTS",
    ):
        monkeypatch.delenv(k, raising=False)
    return EdgeConfig()


def test_desktop_mode_when_no_remote_host(cfg):
    """No QA_JETSON_HOST + no QA_INFERENCE_ENDPOINT → desktop_mode
    is True. Drives `make_backend(cfg)` to LocalYolo."""
    assert cfg.desktop_mode is True


def test_jetson_host_flips_to_remote_mode(monkeypatch, cfg):
    """Setting QA_JETSON_HOST flips desktop_mode → False."""
    monkeypatch.setenv("QA_JETSON_HOST", "192.168.1.50")
    assert cfg.desktop_mode is False
    assert cfg.jetson_host == "192.168.1.50"


def test_inference_url_also_flips_off_desktop_mode(monkeypatch, cfg):
    """QA_INFERENCE_ENDPOINT alone (no Jetson host) still counts as
    remote — desktop_mode must be False."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "http://dev:8000/infer")
    assert cfg.desktop_mode is False
    assert cfg.inference_url == "http://dev:8000/infer"


def test_defaults_match_spec_table(cfg):
    """Spec §2 table — defaults the runner inherits when env vars
    aren't set. Locks the numeric defaults so future changes are
    explicit."""
    assert cfg.rtsp_port == 8554
    assert cfg.rtsp_path == "cam"
    assert cfg.model_path == "yolov8n.pt"
    assert cfg.min_fps == 25.0
    assert cfg.latency_sla_ms == 40.0
    assert cfg.iou_threshold == 0.5
    assert cfg.mediamtx_bin == "./mediamtx"
    assert cfg.device_timeout_s == 60


def test_numeric_envs_parse_correctly(monkeypatch, cfg):
    """All numeric env vars are read via int() / float() — confirm a
    realistic override propagates."""
    monkeypatch.setenv("QA_MIN_FPS", "60")
    monkeypatch.setenv("QA_LATENCY_SLA_MS", "16.7")  # 60 fps target
    monkeypatch.setenv("QA_IOU_THRESHOLD", "0.7")
    assert cfg.min_fps == 60.0
    assert cfg.latency_sla_ms == 16.7
    assert cfg.iou_threshold == 0.7


def test_vendor_host_blacklist_default_off(cfg):
    """v1.1 ships the vendor-host blacklist default-on, but the
    *override* (QA_EDGE_ALLOW_VENDOR_HOSTS) defaults to False. That's
    what this property exposes."""
    assert cfg.allow_vendor_hosts is False


def test_vendor_host_blacklist_override_accepts_truthy(monkeypatch, cfg):
    """Match the project-wide pattern of accepting '1' / 'true' / 'yes'."""
    monkeypatch.setenv("QA_EDGE_ALLOW_VENDOR_HOSTS", "true")
    assert cfg.allow_vendor_hosts is True
    monkeypatch.setenv("QA_EDGE_ALLOW_VENDOR_HOSTS", "1")
    assert cfg.allow_vendor_hosts is True
    monkeypatch.setenv("QA_EDGE_ALLOW_VENDOR_HOSTS", "yes")
    assert cfg.allow_vendor_hosts is True
