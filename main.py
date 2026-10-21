"""InvoiceFlow local web application entry point."""
import sys
import os

from backend.web_app import run_web_app


def main(app_runner=run_web_app):
    """Start the loopback service, open the dashboard, and return a status."""
    try:
        return int(app_runner())
    except Exception as error:
        message = f"InvoiceFlow could not start: {error}"
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, "InvoiceFlow Startup Error", 0x10)
            except Exception:
                print(message, file=sys.stderr)
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
