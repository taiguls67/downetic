from __future__ import annotations

import tkinter as tk
from pathlib import Path

from downloader_app.gui import DownloadManagerApp


def main() -> None:
    root = tk.Tk()
    DownloadManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
