# InvoiceFlow

InvoiceFlow is a local invoice preparation and PDF generation application. Its active interface is a bundled HTML/CSS/JavaScript dashboard served by a loopback-only FastAPI service. Invoice reading, mapping, calculations, settings, history, and PDF generation remain in the Python backend.

**Repository:** [github.com/Keshavcodes-byte/InvoiceFlow](https://github.com/Keshavcodes-byte/InvoiceFlow)

## Contents

- [Workflow](#workflow)
- [Features](#features)
- [Using InvoiceFlow](#using-invoiceflow)
- [Input data](#input-data)
- [Technology stack](#technology-stack)
- [System requirements and installation](#system-requirements-and-installation)
- [Project structure](#project-structure)
- [Run from source](#run-from-source)
- [Build the Windows application](#build-the-windows-application)
- [Build the Windows installer](#build-the-windows-installer)
- [Screenshots and sample output](#screenshots-and-sample-output)
- [Troubleshooting and known limitations](#troubleshooting-and-known-limitations)
- [Reliability and testing](#reliability-and-testing)
- [Portfolio case study](docs/PORTFOLIO.md)
- [Automation service draft](docs/FREELANCING_SERVICE_DRAFT.md)

## Workflow

```text
CSV, XLSX, XLS, or flat JSON
        v
Backend header detection, column mapping, and validation
        v
Invoice records calculated and rendered by Python workers
        v
One A4 PDF per invoice in the configured output folder
```

InvoiceFlow starts a local FastAPI service on loopback, opens the bundled dashboard in the default browser, and keeps invoice reading, mapping, calculations, settings, history, and PDF generation in Python. File parsing, validation, preview, and exports do not block the dashboard.

## Features

- Reads `.csv`, `.xlsx`, `.xls`, and `.json` files.
- Detects a likely header row within the first 20 rows and matches recognized column-name aliases.
- Maps arbitrary source headers to canonical invoice fields, with customer name and either amount or item description plus unit price required.
- Uses `Decimal` calculations for discounts, tax, shipping, and totals; tax mode, currency, decimal separator, and discount type are explicit user choices.
- Provides Minimal, Corporate Classic, Modern Accent, and Compact Grid PDF templates and a real rendered PDF preview.
- Supports company contact details, GSTIN, colors, logo, signature image, stamp image, and footer configuration for a run.
- Saves validated company branding and workflow preferences in versioned per-user JSON settings. Missing image paths are omitted during rendering.
- Stores invoice generation outcomes in a per-user SQLite database, supports searching by invoice number/customer, opens saved PDFs, and reloads the original source when it is still available.
- Warns when an invoice matches a previous successful generation. The current browser workflow refuses existing filenames; the legacy `generate_pdfs()` API has different replacement behavior (see [known limitations](#troubleshooting-and-known-limitations)).
- Supports safe `{InvoiceNo}`, `{ClientName}`, and `{Date}` filename tokens and detects collisions before rendering.
- Writes rotating per-user logs. SMTP invoice dispatch is an explicit, confirmed action; credentials are read from a named environment variable and are never saved in settings.
- Uses a per-user `exports` folder by default, offers a native folder picker, and refuses to overwrite existing PDFs. Verify the picker in each packaged Windows build; automated tests mock the native dialog.
- Shows validation status, batch progress, and application activity events in the dashboard.
- Provides a PyInstaller build configuration and an Inno Setup installer script.

## Using InvoiceFlow

1. Choose a CSV, XLSX, XLS, or JSON input file.
2. Review the detected source headers and suggested field mappings; change mappings as needed.
3. Validate the rows and review invoice details and configured calculations.
4. Choose company branding and a PDF template, then render and inspect a preview.
5. Choose an output folder and export one invoice or a batch. Check the activity panel for per-invoice results.

## Input data

The input file should contain one invoice record per row for amount-only invoices. For multi-item invoices, rows sharing an invoice number are grouped when item description and unit price are mapped; customer, date, address, email, currency, and tax rate must agree within a group. The application suggests common aliases and provides a manual mapping selector for every supported field.

Customer name is required. Map either an amount column or both item description and unit price. Quantity defaults to one when omitted. Invoice number, date, address, email, currency, discount, and tax rate are optional. When an invoice number is blank, a sequential `Invoice_###.pdf` filename is used. The selected source file is not modified. Amount-only rows remain supported and are represented as a one-line invoice.

### JSON input structure

JSON input accepts either a top-level array of flat invoice objects or an object whose only top-level key is `invoices` and whose value is that array. Each record must contain scalar values; nested objects and arrays are rejected. For example:

```json
{
  "invoices": [
    {
      "Invoice No": "INV-1001",
      "Customer Name": "Apex Logistics",
      "Amount": 41300.00
    }
  ]
}
```

The mapping controls cover invoice number, customer name, address, date, amount, item description, quantity, unit price, discount, tax rate, customer email, and currency. Dates accept ISO values; ambiguous date strings fail validation. Punctuated numeric values use the explicitly selected decimal separator (period or comma).

Multi-item rows are grouped by the mapped invoice-number field and validated for conflicting invoice-level values before export. JSON is available in the file picker.

## Calculation engine foundation

`backend/invoice_calculations.py` defines the typed invoice model, configurable tax rules, and structured calculation results. The UI preparation service and PDF templates use this calculation engine; the renderer does not duplicate calculation logic.

Calculations use `Decimal`; numeric float inputs are converted from their string representation before calculation. Monetary values are rounded to the selected currency's minor-unit precision using `ROUND_HALF_UP` at these points: each extended line amount, each line discount, invoice discount, shipping, tax, grand total, and payment amounts. Current supported currencies (INR, USD, EUR, GBP) use two minor-unit digits.

The calculation order is:

1. Extend quantity by unit price, then round each line to currency precision.
2. Apply each item discount once to that line's extended amount. A percentage is a percent of the extended line amount; a fixed discount is one amount per line, not per unit.
3. Sum the rounded line amounts and subtract rounded item discounts.
4. Apply one invoice-level discount to that post-item-discount merchandise subtotal. A percentage is based on that subtotal; shipping is excluded.
5. Add shipping. Shipping is taxable only when `TaxRule.tax_shipping` is explicitly enabled.
6. For tax-exclusive pricing, compute tax on the configured taxable base and add it. For tax-inclusive pricing, extract tax from the configured inclusive base and do not add it again.
7. Subtract recorded payments to produce amount payable and derive unpaid, partially paid, or paid status.

Tax mode and rate are supplied explicitly. `SINGLE_RATE` represents a configured single VAT or sales-tax rate; `CGST_SGST` treats the configured rate as the combined rate and splits the rounded tax evenly, assigning any rounding remainder to SGST; `IGST` uses the configured full rate. The engine does not infer jurisdiction from address data or claim that a preset is current legal advice. Amount-to-words output is an optional descriptive rendering and is not asserted to be a legal requirement.

Currency formatting is separate from arithmetic. The defaults produce Indian digit grouping for INR, US grouping for USD, German-style grouping for EUR, and UK-style grouping for GBP; callers can select one of the supported formatting locales explicitly.

## Technology stack

- **Python** — application and invoice-processing logic
- **FastAPI** and **Uvicorn** - local-only API and application lifecycle
- **HTML, CSS, and JavaScript** - bundled dashboard in `web/index.html`
- **pandas** — reading and processing tabular records
- **openpyxl** and **xlrd** — Excel file support
- **ReportLab** — PDF generation
- **asyncio and ThreadPoolExecutor** - background request processing without blocking the local API event loop
- **PyInstaller** — packaging the application directory
- **Inno Setup** — Windows installer generation

## System requirements and installation

The packaged application targets 64-bit Windows and opens its dashboard in the system's default browser. The browser is not bundled. InvoiceFlow serves the dashboard from a local service bound to `127.0.0.1` on port `48765`; local firewall or endpoint policies must allow that loopback connection.

The repository does not declare a minimum Python version. Run from source with a Python version compatible with the dependencies in [`requirements.txt`](requirements.txt); the project test suite has been run with Python 3.14.6. The Excel `.xls` reader uses `xlrd`, which is listed in the requirements.

There is no verified public installer download URL in this README. Do not treat a locally ignored executable under `installer/` as a published release. If an installer is published later, link it from the repository's Releases page and update this section with its real URL and tested requirements.

When a separately verified Windows installer is made available by the maintainer, review its release notes, run Setup, confirm the per-user installation location, and select the optional shortcuts you want. The current installer script targets `%LOCALAPPDATA%\Programs\InvoiceFlow`. Use the installed app's uninstaller or Windows installed-app settings to remove that installation. This repository does not currently provide a verified installer download for end users.

No license or contribution policy is included in this repository. Reuse and contribution terms have not been specified; contact the repository owner before relying on a reuse license or assuming a contribution workflow.

## Backend PDF templates

The backend exposes a separate ReportLab template renderer in
`backend/pdf_templates.py` for typed `InvoiceDocument` inputs. It provides
`minimal`, `corporate_classic`, `modern_accent`, and `compact_grid` layouts.
Callers can pass a `BrandingConfig` with company details, hex colors, optional
logo/signature/stamp image paths, and footer text. Missing or unreadable
optional images are omitted. Signature and stamp images are visual artwork;
they do not cryptographically sign a PDF.

The template renderer calls `calculate_invoice()` and formats its returned
amounts. It does not infer tax treatment from customer addresses. The current
calculation model applies tax at invoice level, so the table shows each line's
calculated net amount and the tax breakdown appears in the invoice totals.
Rendering writes to a temporary PDF in the destination folder and replaces the
destination only after ReportLab completes successfully. The browser workflow uses these templates for preview, single-invoice export, and batch export. The
legacy amount-based `generate_pdfs()` backend entry point remains available to
existing scripts.

The Python dependencies are listed in [`requirements.txt`](requirements.txt). PyInstaller and Inno Setup are build tools configured by project files; they are not listed in `requirements.txt`.

## Project structure

```text
InvoiceFlow/
  backend/
    app_logging.py          # Rotating per-user log setup and secret redaction
    app_settings.py         # Versioned settings and per-user data paths
    email_dispatch.py       # Explicit SMTP PDF dispatch
    history_repository.py   # SQLite generation history and duplicates
    invoice_calculations.py  # Decimal calculation engine
    invoice_generator.py     # Legacy ingestion and PDF interface
    invoice_workflow.py      # Mapping, model preparation, and export services
    pdf_templates.py         # Four ReportLab templates
  web/
    index.html               # Bundled HTML/CSS/JavaScript dashboard
  backend/web_app.py        # Loopback API, security, and worker lifecycle
  main.py                    # Application entry point
  requirements.txt           # Python runtime dependencies
  InvoiceFlow.spec           # PyInstaller build configuration
  InvoiceFlow.iss            # Inno Setup installer configuration
  tests/                     # Pytest regression and integration suite
  README.md
```

## Settings, history, and integrations

Settings are stored at `%APPDATA%\InvoiceFlow\settings.json` on Windows, `$XDG_CONFIG_HOME/InvoiceFlow/settings.json` when configured on Linux, and `~/.config/InvoiceFlow/settings.json` otherwise. SQLite history is stored beside settings as `invoice_history.sqlite3`; rotating logs are in `logs`; default PDFs go to `exports`. Set `INVOICEFLOW_DATA_DIR` to isolate development and tests.

History stores invoice number, customer display name, original source path, output path, template, currency, total, timestamp, status, and a short failure summary. It does not copy the original invoice payload. Reloading from history re-reads the original file and requires the user to validate its mappings again. A missing source file cannot be regenerated from the history record.

Filename patterns support only `{InvoiceNo}`, `{ClientName}`, and `{Date}`. Values are sanitized, paths and unknown tokens are rejected, and filename collisions stop the export before a PDF is written. Existing PDFs are never replaced.

SMTP settings can be saved in the Company branding tab. The password is read from the configured environment variable (default `INVOICEFLOW_SMTP_PASSWORD`); no password is persisted. Email sending is manual and asks for confirmation. The application reports SMTP acceptance only; it cannot verify recipient delivery or reading. Live SMTP provider delivery has not been verified by automated tests.

## Implemented and deferred

Settings persistence, SQLite export history/search, duplicate warnings, rotating logs, filename patterns, and mocked SMTP dispatch are implemented. The app currently refuses output collisions rather than offering overwrite. Bulk rendering continues after per-invoice renderer failures, but invalid source data can still block the preparation of the full batch. Merged PDFs and folder hierarchies are not implemented.

Webhook delivery, payment-provider links/status, and WhatsApp delivery are deferred because no provider/account is configured. There is no WhatsApp phone-number mapping in the invoice model. PDF password protection is optional per export and uses ReportLab's 128-bit standard encryption. This is a compatibility feature, not DRM or a strong confidentiality guarantee; PDF permission flags may not be enforced uniformly by readers. Passwords are entered for each export, never stored, and should be shared separately. Cryptographic signing remains deferred; scanned signature/stamp options are decorative images only. PyInstaller and Inno Setup builds must be performed on Windows with their respective build tools available; a test pass is not a packaged-app verification.

`build/` and `dist/` contain generated build output and are ignored by Git. Local invoice spreadsheets and the application `Output/` folder are ignored because they may contain customer data or generated PDFs. The installer executable is written to `installer/` and ignored by the `installer/*.exe` rule.

## Run from source

On Windows, open PowerShell in the project directory. Create a virtual environment, install the listed dependencies, then launch the local service and dashboard:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

If PowerShell blocks activation under your current execution policy, use the environment's interpreter directly instead:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## Build the Windows application

Install the build tools in a dedicated, compatible build environment. The following PowerShell commands send PyInstaller output to a unique temporary staging directory rather than the checked-in project paths:

```powershell
$stage = Join-Path $env:TEMP ("InvoiceFlow-build-" + [guid]::NewGuid().ToString("N"))
python -m PyInstaller --clean --noconfirm --distpath "$stage\dist" --workpath "$stage\build" InvoiceFlow.spec
```

The spec configures a windowed, one-folder application named `InvoiceFlow`. The executable is at `$stage\dist\InvoiceFlow\InvoiceFlow.exe`. Keep the generated staging path and test the packaged app before using it as installer input. Do not install or upgrade dependencies in a shared environment just to build; use an isolated build environment with reviewed versions.

The spec bundles `web/index.html`. The executable serves it from the application bundle while writing settings, logs, history, and PDFs to the per-user data directory.

## Build the Windows installer

The current `InvoiceFlow.iss` references `dist\InvoiceFlow\*`, writes under `installer/`, and declares version `1.0.0`. Compiling it unchanged can package an older directory and overwrite a local installer. Before compiling, review the AppId, version, and source/output paths. For a test build, use a temporary copy of the script, point its source at the verified staged bundle, set a unique test AppId and a separate output directory/name, then compile that copy with Inno Setup's `ISCC.exe`. Do not use the release AppId for an isolated test installation.

The script is configured for a per-user installation under `%LOCALAPPDATA%\Programs\InvoiceFlow`, with a Start Menu shortcut, an optional desktop shortcut, and an optional post-install launch. Test installation and uninstallation in an isolated directory before any release. The project `.gitignore` excludes installer `.exe` files.

## Screenshots and sample output

No genuine application screenshots or sample invoice are currently included. Capture these from a tested build using synthetic, non-customer data before adding them:

1. Dashboard showing the selected synthetic file and column mapping.
2. Validated invoice data and calculation summary.
3. Invoice preview with synthetic customer and company details.
4. Successful export with the output path visible and no real customer files in view.

Store reviewed images under a repository-relative location such as `docs/screenshots/`, add descriptive captions and alt text, and check them for personal data before committing. Do not use mockups as proof of working application behavior.

## Troubleshooting and known limitations

- **Input validation fails:** Confirm that the intended source columns are mapped, required customer and amount/item fields are present, dates use an unambiguous supported form, and numeric punctuation matches the selected decimal separator.
- **An export reports a filename collision:** InvoiceFlow refuses to replace existing PDFs. Choose another output folder or a non-colliding filename pattern.
- **The local dashboard does not open:** The app serves it on `127.0.0.1:48765` and asks the system's default browser to open it. Check whether another process already owns that port and whether local policy allows loopback access. Do not expose the API on a network.
- **Native output-folder picker fails in a packaged build:** Verify that build's bundled Tcl/Tk resources and native dialog on the target Windows machine. Automated tests mock the dialog, so they are not proof that a packaged dialog opens.
- **Windows blocks an unsigned staged executable:** Follow the machine's Code Integrity policy. Do not disable or bypass Windows security to run a build.
- **History cannot recreate an invoice:** History stores a source path and summary, not a copy of the original spreadsheet. The source file must still be available and mappings must be reviewed again.
- **Legacy script integration:** `backend/invoice_generator.py` retains the `generate_pdfs()` compatibility API. Its output behavior differs from the current workflow's collision refusal; callers should test it against a disposable destination and must not point it at customer PDFs they need to preserve.
- **Tax or accounting rules:** InvoiceFlow applies the selected inputs and configurable rules. It does not determine legal tax treatment or certify regulatory compliance; have a qualified adviser approve those rules.

## Reliability and testing

The web dashboard validates mapped inputs before export, reports invalid values and invoice-level conflicts, and checks duplicate output paths before writing. Each PDF is written through a temporary file. Exports run through a single-worker queue; the browser polls progress and per-invoice results.

The pytest suite under `tests/` covers ingestion, calculations, PDF templates, workflow, settings, history, logging, mocked SMTP dispatch, and UI integration.
Run it from the project root with the project environment:

```powershell
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest -q
```

The suite uses temporary test data and includes an XLS fixture test when its fixture dependency is available. A green pytest run verifies the automated suite only; it does not by itself verify the Windows executable, native folder picker, browser launch, Code Integrity policy, or installer. Repeat interactive packaged-app and installer checks for each release candidate and record the actual environment and results. Avoid publishing test totals here unless they are refreshed for the same code revision.

For the project overview and portfolio context, see the [portfolio case study](docs/PORTFOLIO.md). A separate [service description draft](docs/FREELANCING_SERVICE_DRAFT.md) is provided for review and is not a claim of prior client work.

If you are exploring the project, start with the workflow and test suite. Questions about behavior or possible improvements can be raised through the repository's GitHub page; no contribution policy or reuse license is currently declared.
