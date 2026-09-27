"""Application entry point: python main.py"""

import sys


def main() -> int:
    import multiprocessing
    multiprocessing.freeze_support()  # needed when frozen by PyInstaller

    from PySide6.QtWidgets import QApplication

    from media_organizer import APP_NAME, __version__
    from media_organizer.gui.main_window import MainWindow
    from media_organizer.gui.theme import DARK_QSS

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MediaOrganizer")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
