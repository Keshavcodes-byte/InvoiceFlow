# InvoiceFlow — Portfolio Case Study

InvoiceFlow is a local invoice-preparation application that turns mapped CSV, Excel, or flat JSON records into individual PDF invoices. This case study describes the implementation in this repository; it does not claim customer deployments, paid results, or accounting and tax certification.

## The problem

Small teams may repeat the same steps for every billing cycle: clean a spreadsheet, copy values into an invoice layout, calculate amounts, save each PDF, and check that files have useful names. Manual repetition can consume time and make data-entry errors harder to spot.

## The solution

InvoiceFlow provides a local dashboard for selecting a data file, reviewing suggested or manual column mappings, validating invoice records, choosing calculation and template options, previewing a PDF, and exporting one or more invoices. The source data file remains unchanged. Users supply the tax treatment and other financial choices; the application does not infer jurisdiction or provide tax advice.

## User workflow

1. Select a CSV, XLSX, XLS, or supported flat JSON file.
2. Review detected headers, mapping suggestions, and validation results; map fields when needed.
3. Review grouped invoice data and configured calculation results.
4. Choose branding and one of the supported PDF templates, then inspect the rendered preview.
5. Choose an output folder and export a single invoice or a batch.
6. Review export status and, when needed, search local generation history.

The workflow and installed behavior should be demonstrated with synthetic data in a tested build. No customer screenshots or output files are included in this repository.

## Architecture and engineering choices

- `main.py` starts the application service and reports startup errors.
- `backend/web_app.py` exposes a FastAPI service bound to loopback, serves the bundled dashboard, validates requests, and coordinates background work.
- `web/index.html` contains the browser interface and its client-side behavior.
- `backend/invoice_generator.py` reads CSV and spreadsheet data and preserves the legacy PDF entry point.
- `backend/invoice_workflow.py` handles field mapping, validation, invoice preparation, and export orchestration.
- `backend/invoice_calculations.py` defines typed invoice data and uses `Decimal` for financial calculations.
- `backend/pdf_templates.py` provides four ReportLab layouts and consumes calculated invoice results.
- `backend/app_settings.py`, `backend/history_repository.py`, and `backend/app_logging.py` manage per-user settings, SQLite history, and rotating logs.
- `backend/web_app.py` moves longer operations off the event loop with `asyncio.to_thread` and a single-worker executor.

The browser interface communicates with a local Python service instead of embedding business rules in frontend code. PyInstaller and Inno Setup configuration is kept in `InvoiceFlow.spec` and `InvoiceFlow.iss`.

## Validation, exports, and data handling

The backend supports header detection, recognized aliases, manual mapping, field validation, JSON shape checks, and conflict checks for grouped invoice rows. It rejects ambiguous customer-name matches instead of silently selecting one. Financial calculations use explicit discount, tax, shipping, and currency choices. They do not establish whether a user's chosen treatment is legally correct.

PDF output uses the calculation engine and one of four templates: Minimal, Corporate Classic, Modern Accent, or Compact Grid. Branding can include company details, color choices, and optional images. Renderer output is written through a temporary file before the completed PDF is placed at its destination. The workflow checks existing output names and refuses collisions.

Settings, logs, and history are stored in the user's application-data area by default. History includes invoice/customer labels and file paths, so it is application data that should be handled accordingly. `INVOICEFLOW_DATA_DIR` can point development runs to an isolated directory. The local-only API binding is not a security certification and should not be exposed to a network.

## Testing and release verification

The pytest suite covers ingestion, calculations, PDF templates, workflow, settings, history, logging, email dispatch behavior, and API/UI integration. Run it from the repository root with the project environment:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Automated tests do not replace interactive Windows checks. A release candidate still needs verification of dashboard launch, native folder selection, representative file-to-PDF workflows, output collision handling, clean shutdown, and installer install/uninstall. Unsigned executables can be restricted by Windows Code Integrity policy; do not change system security settings to work around a block. The current installer script and release identity/output configuration should be reviewed before publishing a release.

## What a reviewer can explore

This repository demonstrates a small full-stack Python automation project: multi-format ingestion, normalization and validation, deterministic money calculations, background work, PDF generation, local persistence, API/UI integration, and a Windows packaging path. The tests show how those components can be exercised with temporary inputs rather than real customer invoices.

## Future work

These are ideas, not implemented commitments:

- Add genuine screenshots captured from an accepted build using synthetic records.
- Automate more packaged-Windows acceptance checks, including native dialogs and installer lifecycle.
- Review installer identity, versioning, artifact output paths, and signing/release policy.
- Add additional client-specific templates or input formats only after requirements and edge cases are agreed.

No license or contribution policy is present in the repository. Reuse and contribution terms have not been specified.
