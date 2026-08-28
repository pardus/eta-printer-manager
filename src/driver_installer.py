import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
import subprocess 
from gi.repository import Gtk, GLib, Vte, Pango
from src.logger import logger
from src.locale_config import _

# Brand / Model -> Debian Package Mapping
DRIVER_PACKAGE_MAP = {
    "brother": "printer-driver-brlaser",
    "hp": "hplip",
    "epson": "printer-driver-escpr",
    "canon": "printer-driver-c2esp",
    "samsung": "printer-driver-splix",
    "xerox": "printer-driver-splix",
    "lexmark": "printer-driver-gutenprint",
    "kyocera": "printer-driver-gutenprint"
}

class DynamicDriverInstaller:
    """Checks missing printer driver packages and installs them on-demand via apt/pkexec."""

    @staticmethod
    def is_package_installed(package_name: str) -> bool:
        """Checks if a debian package is installed on the system."""
        try:
            res = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", package_name],
                capture_output=True,
                text=True
            )
            return "install ok installed" in res.stdout
        except Exception as e:
            logger.error(f"Error checking status for package {package_name}: {e}")
            return False

    @staticmethod
    def get_required_packages(make_and_model: str) -> list:
        """Determines all required driver packages based on printer brand keywords in string."""
        if not make_and_model:
            return []
        
        make_model_lower = make_and_model.lower()
        required_pkgs = []

        for brand, pkg in DRIVER_PACKAGE_MAP.items():
            if brand in make_model_lower and pkg not in required_pkgs:
                required_pkgs.append(pkg)
                
        return required_pkgs

    @classmethod
    def check_and_install_driver(cls, parent_window, make_and_model: str) -> bool:
        """Prompts user via GTK3 dialog and installs missing packages in a background thread with a non-blocking spinner."""
        required_pkgs = cls.get_required_packages(make_and_model)
        missing_pkgs = [pkg for pkg in required_pkgs if not cls.is_package_installed(pkg)]

        if not missing_pkgs:
            return True

        top_win = parent_window
        if hasattr(parent_window, 'get_toplevel'):
            top_win = parent_window.get_toplevel()
        if not isinstance(top_win, Gtk.Window):
            top_win = None

        pkgs_str = ", ".join(missing_pkgs)

        # 1. User Confirmation Dialog
        dialog = Gtk.MessageDialog(
            transient_for=top_win,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Driver Package Required")
        )
        dialog.format_secondary_text(
            _("The following package(s) are required for '{0}':\n\n{1}\n\nDo you want to install them now?").format(make_and_model, pkgs_str)
        )

        response = dialog.run()
        dialog.destroy()

        if response != Gtk.ResponseType.YES:
            logger.info("Driver installation cancelled by user.")
            return False

        # 2. Create VTE Terminal Dialog
        prog_dialog = Gtk.Dialog(
            title=_("Installing Driver Package..."),
            transient_for=top_win,
            flags=Gtk.DialogFlags.MODAL
        )
        prog_dialog.set_default_size(650, 400)

        box = prog_dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(10)

        label = Gtk.Label(
            label=_("Installing required driver package(s): {0}").format(pkgs_str)
        )
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)

        # Create VTE Terminal Widget
        terminal = Vte.Terminal()
        font_desc = Pango.FontDescription("Monospace 10")
        terminal.set_font(font_desc)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.add(terminal)
        box.pack_start(scrolled_window, True, True, 0)

        install_result = {"success": False}

        # 3. Handle Child Process Termination
        def on_child_exited(vte_term, status):
            logger.info(f"VTE installation process exited with status code: {status}")
            install_result["success"] = (status == 0)
            if status == 0:
                prog_dialog.destroy()
                Gtk.main_quit()

        terminal.connect("child-exited", on_child_exited)

        prog_dialog.show_all()

        # 4. Command Execution via pkexec and apt-get inside VTE
        cmd = ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y"] + missing_pkgs

        try:
            logger.info(f"Spawning VTE process for driver installation: {pkgs_str}")
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                None,       # Working directory
                cmd,        # Command list
                None,       # Environment variables
                GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                None, None, # Child setup
                -1,         # Timeout
                None,       # Cancellable
                None,       # Callback
                None        # User data
            )
        except Exception as e:
            logger.error(f"Failed to spawn VTE process for driver installation: {e}")
            prog_dialog.destroy()
            return False

        # Keep GTK Event Loop Running until process finishes
        Gtk.main()

        return install_result["success"]