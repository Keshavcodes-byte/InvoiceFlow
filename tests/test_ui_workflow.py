import queue
import time

from backend.background_worker import BackgroundWorker, WorkerEvent


def test_application_entry_point_invokes_web_server_runner():
    import main

    called = []
    assert main.main(lambda: called.append("started") or 7) == 7
    assert called == ["started"]


def test_worker_reports_success_and_failure_without_ui_callbacks():
    events = queue.Queue()
    worker = BackgroundWorker(events, "ok", lambda value: value + 1, 4)
    worker.start()
    worker.join(timeout=2)
    success = events.get_nowait()
    assert success == WorkerEvent("success", "ok", 5)

    def fail():
        raise ValueError("synthetic failure")

    worker = BackgroundWorker(events, "bad", fail)
    worker.start()
    worker.join(timeout=2)
    failure = events.get_nowait()
    assert failure.kind == "error"
    assert failure.operation == "bad"
    assert str(failure.payload) == "synthetic failure"


def test_web_dashboard_startup_and_core_workflow(tmp_path):
    import json
    import time

    from fastapi.testclient import TestClient
    from backend.web_app import create_app

    app = create_app(data_dir=tmp_path / "app-data")
    with TestClient(app, base_url="http://127.0.0.1:48765") as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "InvoiceFlow" in page.text
        assert "Content-Security-Policy" in page.headers
        csrf = client.cookies.get("if_csrf")
        headers = {"Origin": "http://127.0.0.1:48765", "X-CSRF-Token": csrf}
        uploaded = client.put(
            "/api/source",
            content=json.dumps([{"Invoice No": "WEB-42", "Customer Name": "Web Client", "Amount": "123.45"}]),
            headers={**headers, "X-File-Name": "sample.json", "Content-Type": "application/json"},
        )
        assert uploaded.status_code == 200, uploaded.text
        mapping = uploaded.json()["mapping"]
        validated = client.post("/api/validate", headers=headers, json={"mapping": mapping, "options": {}, "template": "minimal"})
        assert validated.status_code == 200, validated.text
        assert validated.json()["invoices"][0]["customer_name"] == "Web Client"
        assert validated.json()["invoices"][0]["formatted_total"]
        started = client.post("/api/jobs/single", headers=headers, json={"mapping": mapping, "options": {}, "template": "modern_accent"})
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert job["status"] == "complete", job
        history = client.get("/api/history", params={"query": "WEB-42"}).json()["items"]
        assert len(history) == 1 and history[0]["template"] == "modern_accent"
        rendered = client.get(f"/api/history/{history[0]['id']}/file")
        assert rendered.status_code == 200 and rendered.content.startswith(b"%PDF-")

