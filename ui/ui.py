import gi
import os
import threading

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from src.async_loader import AsyncLoader
from src.cups_backend import CupsBackend
from src.driver_installer import DynamicDriverInstaller
from src.locale_config import _
from src.ppd_selector import PPDSelectorWindow
from src.logger import logger
from src.notifications import NotificationManager
from src.queue_window import PrintQueueWindow
from src.version import __version__


# ── Helpers ──────────────────────────────────────────────────────────

def _resolve_data_file(filename):
    """Returns the first existing path for a data file (dev / cwd / system)."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base_dir, "data", filename),
        os.path.abspath(os.path.join("data", filename)),
        f"/usr/share/pardus/eta-printer-manager/data/{filename}",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def load_css():
    path = _resolve_data_file("style.css")
    if not path:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(path)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_USER,
    )


def get_glade_path():
    return _resolve_data_file("main_window.glade") or ""


def _get_parent_window(widget):
    """Walks up to find the nearest Gtk.Window for dialog parenting."""
    if isinstance(widget, Gtk.Window):
        return widget
    if hasattr(widget, "window") and isinstance(widget.window, Gtk.Window):
        return widget.window
    if hasattr(widget, "get_toplevel"):
        top = widget.get_toplevel()
        if isinstance(top, Gtk.Window):
            return top
    return None


def _make_warning_dialog(parent, title, message):
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        flags=Gtk.DialogFlags.MODAL,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


# ── Add Device Dialog ───────────────────────────────────────────────

class AddDeviceDialog:
    def __init__(self, parent_window, cups_backend):
        self.cups_backend = cups_backend

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("eta-printer-manager")
        self.builder.add_from_file(get_glade_path())

        self.dialog = self.builder.get_object("add_device_dialog")
        self.dialog.set_transient_for(_get_parent_window(parent_window))

        self.entry_name = self.builder.get_object("entry_name")
        self.entry_uri = self.builder.get_object("entry_uri")
        self.combo_driver = self.builder.get_object("combo_driver")
        self.spinner = self.builder.get_object("spinner_discovery")
        self.listbox = self.builder.get_object("listbox_discovered")

        self.combo_driver.append("drv:///sample.drv/generic.ppd", _("Generic PostScript Printer"))
        self.combo_driver.append("drv:///sample.drv/pcl5e.ppd", _("Generic PCL Laser Printer"))
        self.combo_driver.append("everywhere", _("IPP Everywhere (Driverless)"))
        self.combo_driver.append("raw", _("Raw Queue (No Driver)"))
        self.combo_driver.set_active(0)

        self.builder.get_object("btn_browse_ppd").connect("clicked", self._on_browse_ppd)
        self.listbox.connect("row-selected", self._on_device_selected)

        GLib.idle_add(self._start_discovery)

    def _on_browse_ppd(self, _button):
        def on_ppd_selected(ppd_name, model_name):
            if ppd_name:
                label = f"★ {model_name}"
                self.combo_driver.append(ppd_name, label)
                self.combo_driver.set_active_id(ppd_name)

        selector_win = PPDSelectorWindow(parent_window=self.dialog, on_select=on_ppd_selected)
        selector_win.show_all()

    def _start_discovery(self):
        self.spinner.start()

        def worker():
            discovered = []
            try:
                for uri, info in self.cups_backend.discover_devices().items():
                    name = info.get("device-make-and-model", info.get("device-info", uri.rsplit("/", 1)[-1]))
                    if info.get("device-class") != "backend" and name.lower() != "unknown":
                        discovered.append((name, uri))
            except Exception:
                pass
            GLib.idle_add(self._on_discovery_done, discovered)

        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _on_discovery_done(self, devices):
        self.spinner.stop()
        self.spinner.hide()

        if not devices:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label=_("No automatic devices found. Please add manually above."), xalign=0)
            lbl.get_style_context().add_class("card-subtitle")
            lbl.set_margin_top(10)
            lbl.set_margin_bottom(10)
            lbl.set_margin_start(10)
            row.add(lbl)
            row.set_selectable(False)
            self.listbox.add(row)
        else:
            for name, uri in devices:
                row = Gtk.ListBoxRow()
                row.device_name = name
                row.device_uri = uri

                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                hbox.set_margin_top(8)
                hbox.set_margin_bottom(8)
                hbox.set_margin_start(10)
                hbox.set_margin_end(10)

                img = Gtk.Image.new_from_icon_name("printer-symbolic", Gtk.IconSize.MENU)
                img.get_style_context().add_class("blue-icon")
                hbox.pack_start(img, False, False, 0)

                vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                lbl_name = Gtk.Label(label=name, xalign=0)
                lbl_name.get_style_context().add_class("card-title")
                lbl_uri = Gtk.Label(label=uri, xalign=0)
                lbl_uri.get_style_context().add_class("card-subtitle")
                vbox.pack_start(lbl_name, False, False, 0)
                vbox.pack_start(lbl_uri, False, False, 0)
                hbox.pack_start(vbox, True, True, 0)

                row.add(hbox)
                self.listbox.add(row)

        self.listbox.show_all()
        return GLib.SOURCE_REMOVE

    def _on_device_selected(self, _listbox, row):
        if not row or not hasattr(row, "device_uri"):
            return
        clean = row.device_name.replace(" ", "_").replace("-", "_").replace("/", "_")
        self.entry_name.set_text(clean)
        self.entry_uri.set_text(row.device_uri)
        self._auto_match_device(row.device_name, row.device_uri)

    def _auto_match_device(self, dev_name, dev_uri):
        def worker():
            attrs = self.cups_backend.get_printer_attributes_by_uri(dev_uri)
            search = attrs.get("make_and_model") or dev_name
            GLib.idle_add(self._apply_auto_match,
                          attrs.get("suggested_name", ""),
                          self.cups_backend.find_best_ppd(search, device_uri=dev_uri))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_auto_match(self, suggested_name, best_ppd):
        if suggested_name:
            clean = suggested_name.replace(" ", "_").replace("-", "_").replace("/", "_")
            self.entry_name.set_text(clean)
        if not best_ppd:
            return

        model = self.combo_driver.get_model()
        if model:
            it = model.get_iter_first()
            while it:
                nxt = model.iter_next(it)
                if model.get_value(it, 1) and "Auto Matched" in model.get_value(it, 1):
                    model.remove(it)
                it = nxt

        default_ids = {
            "drv:///sample.drv/generic.ppd",
            "drv:///sample.drv/pcl5e.ppd",
            "everywhere",
            "raw",
        }
        if best_ppd in default_ids:
            self.combo_driver.set_active_id(best_ppd)
        else:
            label = f"\u2605 {best_ppd.rsplit('/', 1)[-1]} (Auto Matched)"
            self.combo_driver.prepend(best_ppd, label)
            self.combo_driver.set_active_id(best_ppd)

    def get_result(self):
        ppd = self.combo_driver.get_active_id() or "drv:///sample.drv/generic.ppd"
        return (self.entry_name.get_text().strip(),
                self.entry_uri.get_text().strip(),
                ppd)

    def connect(self, signal, callback):
        self.dialog.connect(signal, callback)

    def show_all(self):
        self.dialog.show_all()

    def destroy(self):
        self.dialog.destroy()


# ── Device Card ──────────────────────────────────────────────────────

class DeviceCard(Gtk.Box):
    def __init__(self, name, device_type, status_text, parent_window, is_default=False, is_paused=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.get_style_context().add_class("device-card")
        self.name = name
        self.device_type = device_type
        self.parent_window = parent_window
        self.is_default = is_default
        self.is_paused = is_paused

        self.set_margin_left(6)
        self.set_margin_right(6)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        icon_name = "printer-symbolic" if device_type == "printer" else "camera-web-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)
        icon.get_style_context().add_class("blue-icon")
        header_box.pack_start(icon, False, False, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=name, xalign=0)
        title.get_style_context().add_class("card-title")
        type_label = _("Printer") if device_type == "printer" else _("Scanner")
        subtitle = Gtk.Label(label=f"{type_label} \u00b7 {status_text}", xalign=0)
        subtitle.get_style_context().add_class("card-subtitle")
        text_box.pack_start(title, False, False, 0)
        text_box.pack_start(subtitle, False, False, 0)
        header_box.pack_start(text_box, True, True, 0)

        self.arrow_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON)
        header_box.pack_end(self.arrow_icon, False, False, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        if device_type == "printer":
            action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            action_box.set_margin_top(8)
            for btn in self._build_printer_actions():
                action_box.pack_start(btn, False, False, 0)
            self.revealer.add(action_box)

        event_box = Gtk.EventBox()
        event_box.add(header_box)
        event_box.connect("button-press-event", self._toggle_reveal)

        self.pack_start(event_box, False, False, 0)
        self.pack_start(self.revealer, False, False, 0)

    def _build_printer_actions(self):
        cups = self.parent_window.cups

        btn_queue = Gtk.Button(label=_("Open Queue"))
        btn_queue.get_style_context().add_class("btn-secondary")
        btn_queue.connect("clicked", lambda w: self._action(_("Open Queue"), self._open_queue))

        btn_test = Gtk.Button(label=_("Test Page"))
        btn_test.get_style_context().add_class("btn-secondary")
        btn_test.connect("clicked", lambda w: self._action(_("Send Test Page"), lambda: cups.print_test_page(self.name)))

        btn_default = Gtk.Button(label=_("Default") if self.is_default else _("Set as Default"))
        btn_default.get_style_context().add_class("btn-secondary")
        if self.is_default:
            btn_default.set_sensitive(False)
        else:
            btn_default.connect("clicked", lambda w: self._action(
                _("Set as Default Printer"),
                lambda: (cups.set_default_printer(self.name), self.parent_window.load_devices()),
            ))

        btn_pause_label = _("Resume") if self.is_paused else _("Pause")
        btn_pause = Gtk.Button(label=btn_pause_label)
        btn_pause.get_style_context().add_class("btn-secondary")
        if self.is_paused:
            btn_pause.connect("clicked", lambda w: self._action(
                _("Resume Printer"),
                lambda: (cups.resume_printer(self.name), self.parent_window.load_devices()),
            ))
        else:
            btn_pause.connect("clicked", lambda w: self._action(
                _("Pause Printer"),
                lambda: (cups.pause_printer(self.name), self.parent_window.load_devices()),
            ))

        btn_delete = Gtk.Button(label=_("Remove"))
        btn_delete.get_style_context().add_class("btn-danger")
        btn_delete.connect("clicked", lambda w: self._action(
            _("Remove Printer"),
            lambda: cups.delete_printer(self.name) and self.parent_window.load_devices(),
        ))

        return btn_queue, btn_test, btn_default, btn_pause, btn_delete

    def _toggle_reveal(self, _widget, _event):
        revealed = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(revealed)
        self.arrow_icon.set_from_icon_name(
            "pan-up-symbolic" if revealed else "pan-down-symbolic",
            Gtk.IconSize.BUTTON,
        )

    def _confirm(self, title, message):
        parent = _get_parent_window(self.parent_window)
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def _action(self, action_name, callback):
        msg = _("Do you want to perform '{1}' action for device '{0}'?").format(self.name, action_name)
        if self._confirm(action_name, msg):
            callback()

    def _open_queue(self):
        win = PrintQueueWindow(printer_name=self.name, parent_window=self.parent_window)
        win.show_all()


# ── Main Window ──────────────────────────────────────────────────────

class MainWindow:
    def __init__(self, cups_backend=None):
        load_css()

        self.cups = cups_backend or CupsBackend()
        self.notifier = NotificationManager()

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("eta-printer-manager")
        self.builder.add_from_file(get_glade_path())

        self.window = self.builder.get_object("main_window")
        self.btn_add_device = self.builder.get_object("btn_add_device")
        self.btn_about = self.builder.get_object("btn_about")
        self.device_list_box = self.builder.get_object("device_list_box")

        self.window.connect("destroy", self._on_destroy)
        self.btn_add_device.connect("clicked", self._on_add_device)
        if self.btn_about:
            self.btn_about.connect("clicked", self._on_about)

        self._last_state_key = None
        self._loading = False
        self._poll_id = GLib.timeout_add_seconds(1, self._poll)

        GLib.idle_add(self.load_devices)

    def connect(self, signal, callback, *args):
        return self.window.connect(signal, callback, *args)

    def show_all(self):
        self.window.show_all()

    # ── Window lifecycle ─────────────────────────────────────────────

    def _on_destroy(self, _widget):
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = None
        Gtk.main_quit()

    def _on_about(self, _button):
        dialog = Gtk.AboutDialog()
        dialog.set_transient_for(self.window)
        dialog.set_modal(True)
        dialog.set_program_name(_("ETA Printer Manager"))
        dialog.set_version(__version__)
        dialog.set_comments(_("Printer management tool for ETA and Pardus systems."))
        dialog.set_website("https://www.pardus.org.tr")
        dialog.set_website_label(_("Pardus Official Website"))
        dialog.set_copyright("\u00a9 T\u00dcB\u0130TAK B\u0130LGEM / Pardus")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_authors([
            "Ali \u0130hsan \u00d6zbek <dev@pardus.org.tr>",
            "Pardus Developers <dev@pardus.org.tr>",
        ])
        dialog.set_logo_icon_name("eta-printer-manager")
        dialog.run()
        dialog.destroy()

    # ── Polling ──────────────────────────────────────────────────────

    @staticmethod
    def _state_key(printers, default):
        if not isinstance(printers, dict):
            return None
        parts = [f"{n}:{a.get('printer-state', 0)}" for n, a in sorted(printers.items())]
        parts.append(f"default:{default}")
        return "|".join(parts)

    def _poll(self):
        if not self._loading:
            self._loading = True
            AsyncLoader.run_async(self._fetch, self._on_poll_done)
        return True

    def _on_poll_done(self, result, error):
        self._loading = False
        if error or not result:
            return
        printers, default = result
        key = self._state_key(printers, default)
        if key != self._last_state_key:
            self._last_state_key = key
            self._rebuild(printers, default)

    # ── Device list ──────────────────────────────────────────────────

    def load_devices(self):
        if self._loading:
            return GLib.SOURCE_REMOVE
        self._loading = True
        self._clear_list()
        AsyncLoader.run_async(self._fetch, self._on_devices_loaded)
        return GLib.SOURCE_REMOVE

    def _fetch(self):
        return self.cups.get_printers(), self.cups.get_default_printer()

    def _clear_list(self):
        for child in self.device_list_box.get_children():
            self.device_list_box.remove(child)
            child.destroy()

    def _rebuild(self, printers, default):
        self._clear_list()
        if not isinstance(printers, dict):
            return
        for name, attrs in printers.items():
            is_default = name == default
            status = CupsBackend.get_printer_status_from_attrs(attrs)
            is_paused = status == "stopped"
            if is_default:
                text = _("Default Printer")
            elif is_paused:
                text = _("Paused")
            else:
                text = _("Idle")
            card = DeviceCard(name, "printer", text, self, is_default=is_default, is_paused=is_paused)
            self.device_list_box.pack_start(card, False, False, 0)
        self.device_list_box.show_all()

    def _on_devices_loaded(self, result, error):
        self._loading = False
        if error:
            logger.error(f"Error loading devices: {error}")
            return
        printers, default = result
        self._last_state_key = self._state_key(printers, default)
        self._rebuild(printers, default)

    # ── Add device ───────────────────────────────────────────────────

    def _on_add_device(self, button):
        button.set_sensitive(False)
        dialog = AddDeviceDialog(self, self.cups)
        parent = _get_parent_window(dialog)

        def on_response(_dlg, response_id):
            if response_id == Gtk.ResponseType.OK:
                name, uri, ppd = dialog.get_result()

                if not name or not uri:
                    _make_warning_dialog(parent, _("Missing Information"),
                                         _("Please fill in both Device Name and Connection Address (URI)."))
                    dialog.destroy()
                    button.set_sensitive(True)
                    return

                if " " in name:
                    _make_warning_dialog(parent, _("Invalid Device Name"),
                                         _("Device name cannot contain spaces. Please use underscores (_) or join words."))
                    dialog.destroy()
                    button.set_sensitive(True)
                    return

                if not DynamicDriverInstaller.check_and_install_driver(dialog, name):
                    dialog.destroy()
                    button.set_sensitive(True)
                    return

                def add_task():
                    result = self.cups.add_printer(name, uri, ppd_name=ppd)
                    GLib.idle_add(self._on_printer_added, result)

                threading.Thread(target=add_task, daemon=True).start()

            dialog.destroy()
            button.set_sensitive(True)

        dialog.connect("response", on_response)
        dialog.show_all()

    def _on_printer_added(self, result):
        success, error_msg = result if isinstance(result, tuple) else (result, None)
        if success:
            self.notifier.notify(_("Success"), _("Device added successfully."))
            self.load_devices()
        else:
            logger.error(f"Printer addition failed: {error_msg}")
            _make_warning_dialog(
                _get_parent_window(self.window),
                _("Failed to Add Device"),
                _("An error occurred while adding the printer:\n\n{0}").format(error_msg or _("Unknown error")),
            )
        return GLib.SOURCE_REMOVE
