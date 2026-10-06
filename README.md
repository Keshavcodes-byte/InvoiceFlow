# InvoiceFlow

InvoiceFlow is a Windows desktop application that turns invoice records in Excel or CSV files into individual PDF invoices. It is designed to simplify repetitive invoice creation: select a data file, let InvoiceFlow identify and validate the relevant columns, and generate a PDF for each record.

**Repository:** [github.com/Keshavcodes-byte/InvoiceFlow](https://github.com/Keshavcodes-byte/InvoiceFlow)

## Contents

- [Workflow](#workflow)
- [Features](#features)
- [Input data](#input-data)
- [Technology stack](#technology-stack)
- [Project structure](#project-structure)
- [Run from source](#run-from-source)
- [Build the Windows application](#build-the-windows-application)
- [Build the Windows installer](#build-the-windows-installer)
- [Screenshots and sample output](#screenshots-and-sample-output)
- [Reliability and testing](#reliability-and-testing)

## Workflow

```text
Excel (.xlsx/.xls) or CSV
        ↓
Header detection, column matching, and input validation
        ↓
Invoice records processed in a background worker
        ↓
One A4 PDF per non-empty invoice record in an Output folder beside the input file
```

InvoiceFlow looks for a header row near the top of the file and matches column names against supported aliases. This matching is automatic; the current interface does not provide a manual column-mapping step. After validation, choose **Generate PDFs**. The interface displays progress and log messages while the records are processed.

## Features

- Reads `.csv`, `.xlsx`, and `.xls` files.
- Detects a likely header row within the first 20 rows and matches recognized column-name aliases.
- Requires customer name and amount columns; invoice number, date, and address are optional.
- Checks that amounts can be parsed as finite numbers. Blank amounts are treated as zero; rupee symbols and grouping commas in string amounts are removed before parsing.
- Generates a separate A4 PDF for each non-empty invoice record, using the invoice number as the filename when available.
- Creates an `Output` folder beside the selected input file and provides a button to open it after successful generation.
- Shows per-record progress, a status indicator, and a timestamped log in the desktop interface. PDF generation runs on a worker thread.
- Checks for duplicate generated filenames before writing PDFs and uses temporary files before replacing the final output file.
- Provides a PyInstaller build configuration and an Inno Setup installer script.

## Input data

The input file should contain one invoice record per row, with recognizable column names. The application recognizes common aliases such as `Invoice No` or `Invoice Number`, `Customer` or `Customer Name`, `Invoice Date`, and `Amount`, `Total`, or `Grand Total`. Address is optional. Column matching is based on the aliases implemented in `backend/invoice_generator.py`; there is no user-configurable mapping dialog.

The customer name and amount columns are required. Each record is converted to the fields used by the PDF generator: invoice number, customer name, date, amount, and address. When an invoice number is blank, a sequential `Invoice_###.pdf` filename is used. The selected source file is not modified.

## Technology stack

- **Python** — application and invoice-processing logic
- **PySide6** — desktop interface and background worker integration
- **pandas** — reading and processing tabular records
- **openpyxl** and **xlrd** — Excel file support
- **ReportLab** — PDF generation
- **pywinstyles** — optional Windows window styling
- **PyInstaller** — packaging the application directory
- **Inno Setup** — Windows installer generation

The Python dependencies are listed in [`requirements.txt`](requirements.txt). PyInstaller and Inno Setup are build tools configured by project files; they are not listed in `requirements.txt`.

## Project structure

```text
InvoiceFlow/
├── assets/
│   └── Icons/                 # Empty SVG placeholders
├── backend/
│   └── invoice_generator.py   # Input handling, validation, and PDF generation
├── ui/
│   ├── header.py              # Application header
│   ├── hover_card.py          # Reusable card widget
│   ├── input_card.py          # Selected input file display
│   ├── main_window.py         # Main window and UI composition
│   ├── output_console.py      # Timestamped application log
│   └── process_card.py        # File selection, processing, and progress UI
├── main.py                    # Application entry point
├── styles.qss                 # Qt stylesheet
├── requirements.txt           # Python runtime dependencies
├── InvoiceFlow.spec           # PyInstaller build configuration
├── InvoiceFlow.iss            # Inno Setup installer configuration
├── .gitignore
└── README.md
```

`build/` and `dist/` contain generated build output and are ignored by Git. Local invoice spreadsheets and the application `Output/` folder are ignored because they may contain customer data or generated PDFs. The installer executable is written to `installer/` and ignored by the `installer/*.exe` rule.

## Run from source

On Windows, install a compatible Python version and open PowerShell in the project directory. Create and activate a virtual environment, install the listed dependencies, then launch the application:

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

Install PyInstaller in the active build environment, then run the project spec file from the repository root:

```powershell
python -m pip install pyinstaller
python -m PyInstaller --clean --noconfirm InvoiceFlow.spec
```

The spec configures a windowed application named `InvoiceFlow`. The build output is the directory `dist\InvoiceFlow\`, with the executable at `dist\InvoiceFlow\InvoiceFlow.exe`.

The spec includes `styles.qss` beside the entry point in the bundle. The application resolves it relative to `__file__`, so the same path works from the source tree and the packaged application.

## Build the Windows installer

The installer script expects the PyInstaller directory output to exist at `dist\InvoiceFlow\`. Install Inno Setup on Windows and ensure its command-line compiler (`ISCC.exe`) is available on `PATH`. Then compile `InvoiceFlow.iss` from the project root:

```powershell
iscc InvoiceFlow.iss
```

The script packages the contents of `dist\InvoiceFlow\` into an installer named `installer\InvoiceFlow_Setup_v1.0.0.exe`. By default, it installs per-user under `%LOCALAPPDATA%\Programs\InvoiceFlow`, creates a Start Menu shortcut, offers an optional desktop shortcut, and offers to launch InvoiceFlow after setup. The project `.gitignore` excludes installer `.exe` files.

## Screenshots and sample output

Screenshots of the application and a sample invoice will be included in a future update.

## Reliability and testing

The implementation checks file existence and supported extensions, rejects empty input, requires customer-name and amount columns, and reports invalid amounts with a row reference. It also detects duplicate output filenames before writing and checks that a rendered PDF is non-empty before placing it at its destination. Output-folder and file-write failures are surfaced through the application log. Processing runs outside the main UI thread and reports record-level progress.

No automated test suite or test results are included in the repository, so no test coverage or successful test run is claimed here.
