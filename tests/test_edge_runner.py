"""v1.1.0 PR-2 — EdgeInferenceRunner.

Three things to verify:

  1. REGISTRY wires `edge` + `rtsp` to the same class.
  2. Lifecycle: setup() starts an RTSP source (when configured),
     teardown() stops it, even when pytest itself raises.
  3. _edge_env() projects QA_* env vars onto EDGE_* env vars that
     generated tests consume via conftest fixtures.

subprocess.Popen + safe_run + start_rtsp_source all get mocked so the
test never actually runs pytest or starts ffmpeg.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---- REGISTRY wiring -----------------------------------------------------

def test_registry_has_edge_alias_pointing_at_edge_runner():
    from mk_qa_master.runners import REGISTRY
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    assert REGISTRY.get("edge") is EdgeInferenceRunner
    assert REGISTRY.get("rtsp") is EdgeInferenceRunner


def test_get_runner_picks_edge_when_qa_runner_is_edge(monkeypatch):
    """QA_RUNNER=edge → get_runner() returns EdgeInferenceRunner.
    Confirms the env-driven dispatch the rest of mk-qa-master uses
    works for the new runner without code changes."""
    monkeypatch.setenv("QA_RUNNER", "edge")
    import importlib
    import mk_qa_master.config as cfg
    importlib.reload(cfg)
    import mk_qa_master.runners as runners
    importlib.reload(runners)

    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    instance = runners.get_runner()
    assert isinstance(instance, EdgeInferenceRunner)


# ---- _edge_env() ---------------------------------------------------------

@pytest.fixture
def _clean_edge_env(monkeypatch):
    """Strip QA_* env vars so each test declares its own state."""
    for k in (
        "QA_RTSP_SOURCE", "QA_RTSP_PORT", "QA_RTSP_PATH",
        "QA_JETSON_HOST", "QA_INFERENCE_ENDPOINT", "QA_MODEL_PATH",
        "QA_MIN_FPS", "QA_LATENCY_SLA_MS", "QA_IOU_THRESHOLD",
        "QA_MEDIAMTX_BIN", "QA_DEVICE_TIMEOUT_S",
        "QA_EDGE_ALLOW_VENDOR_HOSTS",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


def test_edge_env_uses_active_source_url_when_local_source_started(_clean_edge_env, monkeypatch):
    """When start_rtsp_source produced a local URL, the EDGE_RTSP_URL
    points there (not the raw file path)."""
    monkeypatch.setenv("QA_RTSP_SOURCE", "fixtures/factory.mp4")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    # Simulate setup having attached a source.
    runner._source = MagicMock(url="rtsp://localhost:8554/cam")
    env = runner._edge_env()
    assert env["EDGE_RTSP_URL"] == "rtsp://localhost:8554/cam"


def test_edge_env_falls_back_to_raw_source_when_no_local_source(_clean_edge_env, monkeypatch):
    """When source is rtsp:// (pass-through) and we never started a
    local server, EDGE_RTSP_URL is the raw source value."""
    monkeypatch.setenv("QA_RTSP_SOURCE", "rtsp://camera.lan:554/stream")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    env = runner._edge_env()  # _source is None
    assert env["EDGE_RTSP_URL"] == "rtsp://camera.lan:554/stream"


def test_edge_env_carries_numeric_thresholds_as_strings(_clean_edge_env, monkeypatch):
    """Generated tests read these via os.environ which is always str-typed.
    Confirms we don't accidentally pass ints (which would crash int(env[...]) later)."""
    monkeypatch.setenv("QA_MIN_FPS", "30")
    monkeypatch.setenv("QA_LATENCY_SLA_MS", "33.3")
    monkeypatch.setenv("QA_IOU_THRESHOLD", "0.6")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    env = runner._edge_env()
    assert env["EDGE_MIN_FPS"] == "30.0"
    assert env["EDGE_LATENCY_SLA_MS"] == "33.3"
    assert env["EDGE_IOU_THRESHOLD"] == "0.6"
    # All values are str.
    assert all(isinstance(v, str) for v in env.values())


# ---- Lifecycle (setup/teardown) ------------------------------------------

def test_setup_starts_rtsp_source_when_configured(_clean_edge_env, monkeypatch):
    monkeypatch.setenv("QA_RTSP_SOURCE", "fixtures/factory.mp4")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    fake_handle = MagicMock(url="rtsp://localhost:8554/cam")
    with patch(
        "mk_qa_master.runners.edge_inference.start_rtsp_source",
        return_value=fake_handle,
    ) as mock_start:
        runner = EdgeInferenceRunner()
        runner.setup()
        assert runner._source is fake_handle
        mock_start.assert_called_once_with(runner.cfg)


def test_setup_skips_rtsp_when_source_empty(_clean_edge_env):
    """No QA_RTSP_SOURCE → setup() does NOT start anything; runner
    can still run with manually-spawned source for advanced users."""
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    with patch(
        "mk_qa_master.runners.edge_inference.start_rtsp_source",
    ) as mock_start:
        runner = EdgeInferenceRunner()
        runner.setup()
        mock_start.assert_not_called()
        assert runner._source is None


def test_teardown_stops_source_and_clears_handle(_clean_edge_env, monkeypatch):
    monkeypatch.setenv("QA_RTSP_SOURCE", "fixtures/factory.mp4")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    fake_handle = MagicMock(url="rtsp://localhost:8554/cam")
    with patch(
        "mk_qa_master.runners.edge_inference.start_rtsp_source",
        return_value=fake_handle,
    ):
        runner = EdgeInferenceRunner()
        runner.setup()
        runner.teardown()

    fake_handle.stop.assert_called_once()
    assert runner._source is None


def test_teardown_is_safe_when_no_source_started():
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    runner.teardown()  # must not raise
    assert runner._source is None


# ---- _healthcheck_device (v1.2.0 — real probe) ----------------------------


def _ok_response():
    """Builds a successful HTTP response mock (200, raise_for_status no-op)."""
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    return r


def test_healthcheck_raises_on_malformed_target(_clean_edge_env, monkeypatch):
    """A non-HTTP target raises BEFORE the network call so setup aborts
    cleanly. Same shape gate v1.1 used; kept as the first check."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "ftp://wrong-protocol/")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    with pytest.raises(RuntimeError, match="HTTP URL"):
        runner._healthcheck_device()


def test_healthcheck_noop_in_desktop_mode(_clean_edge_env):
    """No remote target set → nothing to probe → no error."""
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    runner._healthcheck_device()  # must not raise


def test_healthcheck_probes_health_endpoint_when_jetson_host_set(
    _clean_edge_env, monkeypatch
):
    """QA_JETSON_HOST=<ip> derives <http://ip:8000/infer> as the
    inference URL; healthcheck should swap the trailing `/infer` for
    `/health` and GET that URL."""
    monkeypatch.setenv("QA_JETSON_HOST", "192.168.1.50")
    fake_requests = MagicMock(get=MagicMock(return_value=_ok_response()))
    with patch.dict("sys.modules", {"requests": fake_requests}):
        from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
        EdgeInferenceRunner()._healthcheck_device()
    # Probed URL = http://192.168.1.50:8000/health (not /infer)
    fake_requests.get.assert_called_once()
    args, kwargs = fake_requests.get.call_args
    assert args[0] == "http://192.168.1.50:8000/health"


def test_healthcheck_probes_inference_url_when_explicitly_set(
    _clean_edge_env, monkeypatch
):
    """QA_INFERENCE_ENDPOINT wins over QA_JETSON_HOST; healthcheck should
    derive /health from whatever URL was given."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "http://dev:9000/infer")
    monkeypatch.setenv("QA_JETSON_HOST", "should-be-ignored")
    fake_requests = MagicMock(get=MagicMock(return_value=_ok_response()))
    with patch.dict("sys.modules", {"requests": fake_requests}):
        from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
        EdgeInferenceRunner()._healthcheck_device()
    args, _ = fake_requests.get.call_args
    assert args[0] == "http://dev:9000/health"


def test_healthcheck_handles_non_infer_path_suffix(_clean_edge_env, monkeypatch):
    """When the inference URL doesn't end with /infer (e.g., user has a
    custom /predict path), we still need to probe a /health endpoint —
    append it to the URL base."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "http://dev/myapi/predict")
    fake_requests = MagicMock(get=MagicMock(return_value=_ok_response()))
    with patch.dict("sys.modules", {"requests": fake_requests}):
        from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
        EdgeInferenceRunner()._healthcheck_device()
    args, _ = fake_requests.get.call_args
    assert args[0] == "http://dev/myapi/predict/health"


def test_healthcheck_raises_on_5xx(_clean_edge_env, monkeypatch):
    """503 from /health → setup aborts with a clear message."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "http://overloaded/infer")
    import requests as real_requests
    bad = MagicMock()
    bad.status_code = 503
    bad.raise_for_status = MagicMock(
        side_effect=real_requests.HTTPError("503"),
    )
    fake_requests = MagicMock(get=MagicMock(return_value=bad))
    # Preserve real exception classes so isinstance/except work.
    fake_requests.HTTPError = real_requests.HTTPError
    fake_requests.Timeout = real_requests.Timeout
    fake_requests.ConnectionError = real_requests.ConnectionError
    with patch.dict("sys.modules", {"requests": fake_requests}):
        from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
        runner = EdgeInferenceRunner()
        with pytest.raises(RuntimeError, match="unreachable"):
            runner._healthcheck_device()


def test_healthcheck_raises_on_timeout_with_clear_message(
    _clean_edge_env, monkeypatch
):
    """Timeout → RuntimeError that mentions QA_DEVICE_TIMEOUT_S so the
    user knows which knob to turn."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "http://slow/infer")
    import requests as real_requests
    fake_requests = MagicMock(
        get=MagicMock(side_effect=real_requests.Timeout("simulated")),
    )
    fake_requests.HTTPError = real_requests.HTTPError
    fake_requests.Timeout = real_requests.Timeout
    fake_requests.ConnectionError = real_requests.ConnectionError
    with patch.dict("sys.modules", {"requests": fake_requests}):
        from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
        runner = EdgeInferenceRunner()
        with pytest.raises(RuntimeError, match="QA_DEVICE_TIMEOUT_S"):
            runner._healthcheck_device()


def test_healthcheck_respects_device_timeout_s_env_var(
    _clean_edge_env, monkeypatch
):
    """QA_DEVICE_TIMEOUT_S override propagates to requests.get(timeout=)."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "http://dev/infer")
    monkeypatch.setenv("QA_DEVICE_TIMEOUT_S", "120")
    fake_requests = MagicMock(get=MagicMock(return_value=_ok_response()))
    with patch.dict("sys.modules", {"requests": fake_requests}):
        from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
        EdgeInferenceRunner()._healthcheck_device()
    _, kwargs = fake_requests.get.call_args
    assert kwargs["timeout"] == 120


# ---- run_tests ------------------------------------------------------------

def test_run_tests_runs_setup_pytest_teardown_in_order(_clean_edge_env, monkeypatch):
    """The full sequence is observable: setup → safe_run(pytest) →
    teardown. We assert via call order on a mock collecting each step."""
    monkeypatch.setenv("QA_RTSP_SOURCE", "fixtures/factory.mp4")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    calls: list[str] = []

    fake_handle = MagicMock(url="rtsp://localhost:8554/cam")
    fake_handle.stop = lambda: calls.append("teardown")

    def fake_start(_cfg):
        calls.append("setup")
        return fake_handle

    def fake_safe_run(cmd, **kw):
        calls.append("pytest")
        return MagicMock(returncode=0, stdout="ok", stderr="")

    with patch(
        "mk_qa_master.runners.edge_inference.start_rtsp_source",
        side_effect=fake_start,
    ), patch(
        "mk_qa_master.runners.edge_inference.safe_run",
        side_effect=fake_safe_run,
    ):
        runner = EdgeInferenceRunner()
        result = runner.run_tests()

    assert calls == ["setup", "pytest", "teardown"]
    assert result["exit_code"] == 0
    assert result["rtsp_url"] == "rtsp://localhost:8554/cam"
    assert result["backend_mode"] == "desktop"


def test_run_tests_teardown_runs_even_when_setup_fails(_clean_edge_env, monkeypatch):
    """setup() raising → no pytest invocation, but teardown still
    runs and the runner returns a clean error envelope."""
    monkeypatch.setenv("QA_INFERENCE_ENDPOINT", "not-an-http-url")
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    pytest_called = {"yes": False}

    def trip_safe_run(*_a, **_kw):
        pytest_called["yes"] = True
        return MagicMock()

    with patch(
        "mk_qa_master.runners.edge_inference.safe_run",
        side_effect=trip_safe_run,
    ):
        runner = EdgeInferenceRunner()
        result = runner.run_tests()

    assert pytest_called["yes"] is False
    assert result["exit_code"] == 2
    assert "setup failed" in result["stderr_tail"]


def test_run_tests_appends_filter_to_pytest_command(_clean_edge_env):
    """A non-empty filter becomes `-k <filter>` on the pytest command."""
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner

    captured_cmd: list[str] = []

    def fake_safe_run(cmd, **kw):
        captured_cmd.extend(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch(
        "mk_qa_master.runners.edge_inference.safe_run",
        side_effect=fake_safe_run,
    ):
        runner = EdgeInferenceRunner()
        runner.run_tests(filter="test_detect_person")

    assert "-k" in captured_cmd
    assert "test_detect_person" in captured_cmd


# ---- TestRunner interface methods (report wiring) ------------------------

def test_get_report_summary_reads_pytest_json_report(_clean_edge_env, tmp_path, monkeypatch):
    """The summary mirrors the keys other runners expose so reporter
    code doesn't have to special-case edge."""
    # Stub the REPORT_PATH to a fixture file under tmp_path.
    fixture = {
        "summary": {"total": 3, "passed": 2, "failed": 1, "skipped": 0},
        "duration": 1.23,
    }
    fake_report = tmp_path / "report.json"
    fake_report.write_text(json.dumps(fixture))

    monkeypatch.setattr(
        "mk_qa_master.runners.edge_inference.REPORT_PATH", fake_report,
    )
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    summary = runner.get_report_summary()
    assert summary["total"] == 3
    assert summary["passed"] == 2
    assert summary["failed"] == 1
    assert summary["duration"] == 1.23
    assert summary["backend_mode"] == "desktop"


def test_get_failure_details_filters_by_test_id_substring(_clean_edge_env, tmp_path, monkeypatch):
    fixture = {
        "tests": [
            {"nodeid": "tests/test_a.py::test_one", "outcome": "passed"},
            {"nodeid": "tests/test_b.py::test_detect_person",
             "outcome": "failed",
             "call": {"longrepr": "AssertionError", "duration": 0.5}},
            {"nodeid": "tests/test_b.py::test_detect_forklift",
             "outcome": "failed",
             "call": {"longrepr": "AssertionError", "duration": 0.4}},
        ],
    }
    fake_report = tmp_path / "report.json"
    fake_report.write_text(json.dumps(fixture))
    monkeypatch.setattr(
        "mk_qa_master.runners.edge_inference.REPORT_PATH", fake_report,
    )
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    failures = runner.get_failure_details(test_id="person")
    assert len(failures) == 1
    assert failures[0]["nodeid"].endswith("test_detect_person")


def test_generate_test_emits_working_pytest_with_iou_and_throughput(
    _clean_edge_env, tmp_path, monkeypatch
):
    """v1.1 PR-3 swapped the PR-1 placeholder for a real spec §10
    template. The generated file:
      - imports the pytest plugin fixtures (backend / stream / latency)
      - has a label-aware detection test that's skipped without a label
      - has an unconditional throughput test
      - reads EDGE_IOU_THRESHOLD / EDGE_MIN_FPS / EDGE_LATENCY_SLA_MS
        from os.environ (so the runner's _edge_env() populates them)
    """
    monkeypatch.setattr(
        "mk_qa_master.runners.edge_inference.PROJECT_ROOT", tmp_path,
    )
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    rel_path = runner.generate_test(
        description="detect person in factory feed",
        filename="test_edge_factory.py",
        annotations_path="fixtures/factory.annotations.json",
        label="person",
        max_frame=120,
    )
    assert rel_path == "test_edge_factory.py"
    output = (tmp_path / "test_edge_factory.py").read_text()

    # Docstring carries the human description.
    assert "detect person in factory feed" in output
    # Fixtures imported from the pytest plugin module.
    assert "from mk_qa_master.edge.pytest_plugin import" in output
    # Per-label test function uses the label as identifier.
    assert "def test_detect_person" in output
    # Throughput test always present.
    assert "def test_throughput" in output
    # Env-driven thresholds.
    assert "EDGE_IOU_THRESHOLD" in output
    assert "EDGE_MIN_FPS" in output
    assert "EDGE_LATENCY_SLA_MS" in output
    # Annotations path threaded through.
    assert "fixtures/factory.annotations.json" in output


def test_generate_test_without_label_skips_detection(_clean_edge_env, tmp_path, monkeypatch):
    """Calling generate_test with no label still emits a valid file —
    the detection test is decorated with skipif so the throughput test
    is the only thing that runs."""
    monkeypatch.setattr(
        "mk_qa_master.runners.edge_inference.PROJECT_ROOT", tmp_path,
    )
    from mk_qa_master.runners.edge_inference import EdgeInferenceRunner
    runner = EdgeInferenceRunner()
    rel_path = runner.generate_test(
        description="bare throughput baseline",
        filename="test_edge_throughput.py",
    )
    output = (tmp_path / rel_path).read_text()
    # Detection test exists but is skipped by default identifier.
    assert "def test_detect_target" in output
    assert "skipif(not LABEL" in output
    # Throughput test always emitted.
    assert "def test_throughput" in output
