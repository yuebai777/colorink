import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

# Ensure working directory is set to script directory
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
if script_dir not in sys.path:
    sys.path.append(script_dir)
core_dir = os.path.join(script_dir, "core")
if core_dir not in sys.path:
    sys.path.append(core_dir)

from ui.main_window import MainWindow

def main():
    # Initialize QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("Palette Lite")
    
    # Load and apply window icon
    icon_path = os.path.join("icons", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        
    # Launch main window
    window = MainWindow()
    window.show()
    
    # Execute application main loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
