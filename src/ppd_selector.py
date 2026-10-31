import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from src.locale_config import _
from src.logger import logger


class PPDSelectorWindow(Gtk.Window):

    def __init__(self, parent_window=None, on_select=None):
        super().__init__(title=_("Select Printer Driver"))
        self.set_border_width(12)
        self.set_default_size(750, 500)
        self.set_modal(True)
        if parent_window:
            self.set_transient_for(parent_window)

        self._on_select = on_select
        self._selected_ppd = None

        self.raw_ppds = {}
        self.structured_data = {}
        self.selected_make = None

        vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(vbox_main)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(250)
        vbox_main.pack_start(paned, True, True, 0)

        # LEFT: Manufacturers
        vbox_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        paned.pack1(vbox_left, resize=False, shrink=False)

        lbl_make = Gtk.Label(label="<b>{}</b>".format(_("1. Select Manufacturer")), xalign=0, use_markup=True)
        vbox_left.pack_start(lbl_make, False, False, 0)

        self.make_search = Gtk.SearchEntry(placeholder_text=_("Filter manufacturers..."))
        self.make_search.connect("search-changed", lambda e: self.make_listbox.invalidate_filter())
        vbox_left.pack_start(self.make_search, False, False, 0)

        scroll_left = Gtk.ScrolledWindow()
        vbox_left.pack_start(scroll_left, True, True, 0)

        self.make_listbox = Gtk.ListBox()
        self.make_listbox.set_filter_func(self._filter_makes)
        self.make_listbox.connect("row-selected", self._on_make_row_selected)
        scroll_left.add(self.make_listbox)

        # RIGHT: Models
        vbox_right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        paned.pack2(vbox_right, resize=True, shrink=False)

        lbl_model = Gtk.Label(label="<b>{}</b>".format(_("2. Select Model / Driver")), xalign=0, use_markup=True)
        vbox_right.pack_start(lbl_model, False, False, 0)

        self.model_search = Gtk.SearchEntry(placeholder_text=_("Filter models..."))
        self.model_search.connect("search-changed", lambda e: self.model_listbox.invalidate_filter())
        vbox_right.pack_start(self.model_search, False, False, 0)

        scroll_right = Gtk.ScrolledWindow()
        vbox_right.pack_start(scroll_right, True, True, 0)

        self.model_listbox = Gtk.ListBox()
        self.model_listbox.set_filter_func(self._filter_models)
        self.model_listbox.connect("row-activated", lambda lb, row: self._confirm_selection())
        scroll_right.add(self.model_listbox)

        # BOTTOM
        self.status_label = Gtk.Label(label=_("Fetching system drivers..."), xalign=0)
        vbox_main.pack_start(self.status_label, False, False, 0)

        hbox_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vbox_main.pack_start(hbox_buttons, False, False, 0)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: self.destroy())
        hbox_buttons.pack_end(btn_cancel, False, False, 0)

        self.btn_select = Gtk.Button(label=_("Select Driver"), sensitive=False)
        self.btn_select.connect("clicked", lambda b: self._confirm_selection())
        hbox_buttons.pack_end(self.btn_select, False, False, 0)

        self._toggle_ui(False)
        threading.Thread(target=self._load_ppds, daemon=True).start()

    def _toggle_ui(self, state):
        self.make_search.set_sensitive(state)
        self.model_search.set_sensitive(state)
        self.make_listbox.set_sensitive(state)

    def _load_ppds(self):
        try:
            from src.cups_backend import CupsBackend
            backend = CupsBackend()
            self.raw_ppds = backend.get_ppds()

            for ppd_name, attrs in self.raw_ppds.items():
                make = attrs.get("ppd-make", "Generic").strip()
                model = attrs.get("ppd-make-and-model", "Unknown Model").strip()

                if make not in self.structured_data:
                    self.structured_data[make] = {}
                self.structured_data[make][model] = ppd_name

            GLib.idle_add(self._populate_makes)
        except Exception as e:
            logger.error(f"Failed to load PPDs: {e}")
            GLib.idle_add(self.status_label.set_text, str(e))

    def _populate_makes(self):
        for make in sorted(self.structured_data.keys(), key=lambda s: s.lower()):
            lbl = Gtk.Label(label=make, xalign=0, margin=8)
            row = Gtk.ListBoxRow()
            row.add(lbl)
            row.make_name = make
            self.make_listbox.add(row)

        self.make_listbox.show_all()
        self._toggle_ui(True)
        self.status_label.set_text(_("System drivers loaded. Please pick a Manufacturer."))
        return GLib.SOURCE_REMOVE

    def _on_make_row_selected(self, _listbox, row):
        if not row:
            return

        for child in self.model_listbox.get_children():
            self.model_listbox.remove(child)

        self.selected_make = row.make_name
        models_dict = self.structured_data.get(self.selected_make, {})

        for model in sorted(models_dict.keys(), key=lambda s: s.lower()):
            row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=6)

            lbl_title = Gtk.Label(label=model, xalign=0)
            lbl_title.set_markup("<b>{}</b>".format(GLib.markup_escape_text(model)))
            lbl_desc = Gtk.Label(label=f"File: {models_dict[model]}", xalign=0)
            lbl_desc.get_style_context().add_class("dim-label")

            row_box.pack_start(lbl_title, False, False, 0)
            row_box.pack_start(lbl_desc, False, False, 0)

            model_row = Gtk.ListBoxRow()
            model_row.add(row_box)
            model_row.ppd_name = models_dict[model]
            model_row.model_name = model
            self.model_listbox.add(model_row)

        self.model_listbox.show_all()
        self.btn_select.set_sensitive(True)
        self.status_label.set_text(_("Showing models for {0}.").format(self.selected_make))

    def _filter_makes(self, row):
        query = self.make_search.get_text().lower()
        return not query or query in row.make_name.lower()

    def _filter_models(self, row):
        query = self.model_search.get_text().lower()
        return not query or query in row.model_name.lower() or query in row.ppd_name.lower()

    def _confirm_selection(self):
        selected_row = self.model_listbox.get_selected_row()
        if selected_row:
            self._selected_ppd = selected_row.ppd_name
            if self._on_select:
                self._on_select(selected_row.ppd_name, selected_row.model_name)
            self.destroy()
        else:
            self.status_label.set_text(_("Please pick a specific model from the right column."))
