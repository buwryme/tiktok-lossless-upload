#!/usr/bin/env python3
"""
TikTok Lossless Patcher - GTK4/Libadwaita UI
GNOME HIG Compliant Application
"""

import sys
import os
import json
import threading
from pathlib import Path

# ensure current script folder is on top of sys.path
SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Gio, GLib, Adw, GObject

# import the patching logic relative to app.py location
try:
    import tiktok_lossless_patch
    
    # ensure fallback if function doesn't exist in module
    patch_video = getattr(tiktok_lossless_patch, 'patch_video', None)
    get_default_config = getattr(tiktok_lossless_patch, 'get_default_config', None)
    
    if patch_video is None or get_default_config is None:
        raise ImportError("patch_video or get_default_config missing in tiktok_lossless_patch")

    HAS_PATCHER = True
except Exception as e:
    print(f"Error loading tiktok_lossless_patch: {e}")
    HAS_PATCHER = False
    
    def patch_video(path, config):
        pass

    def get_default_config():
        return {"re_encode": True, "crf": 18, "codec": "h264"}

CONFIG_PATH = Path.home() / ".config" / "tiktok_patcher.json"

class ConfigManager:
    def __init__(self):
        self.config = get_default_config()
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r') as f:
                    saved = json.load(f)
                for key, value in saved.items():
                    if key in self.config:
                        self.config[key] = value
            except Exception:
                pass

    def save(self, new_config):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        filtered = {k: v for k, v in new_config.items() if k in self.config}
        with open(CONFIG_PATH, 'w') as f:
            json.dump(filtered, f, indent=2)
        self.config = filtered


class PreferencesView(Adw.NavigationPage):
    def __init__(self, config_manager):
        super().__init__()
        self.set_title("Preferences")
        self.set_tag("preferences")

        self.config_manager = config_manager

        # Store original config for change detection
        self.original_config = config_manager.config.copy()
        self.has_changes = False

        # Main toolbar view for proper background
        toolbar_view = Adw.ToolbarView()

        # HeaderBar for preferences
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        # Scrollable content
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_hexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Preferences page
        prefs_page = Adw.PreferencesPage()
        prefs_page.set_icon_name("preferences-system-symbolic")


        # Patching Options Group
        patch_group = Adw.PreferencesGroup()
        patch_group.set_title("Patching Options")
        patch_group.set_description("Control how the video is patched")



        # Re-encode Toggle
        reencode_row = Adw.ActionRow()
        reencode_row.set_title("Re-encode Video")
        reencode_row.set_subtitle("Re-encode video before patching")

        self.reencode_switch = Gtk.Switch()
        self.reencode_switch.set_valign(Gtk.Align.CENTER)
        self.reencode_switch.set_active(config_manager.config.get("re_encode", True))
        self.reencode_switch.connect("notify::active", self.on_field_changed)
        reencode_row.add_suffix(self.reencode_switch)
        reencode_row.set_activatable_widget(self.reencode_switch)
        patch_group.add(reencode_row)

        prefs_page.add(patch_group)

        sw.set_child(prefs_page)
        toolbar_view.set_content(sw)

        # Bottom bar with save button
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_bar.set_margin_start(12)
        bottom_bar.set_margin_end(12)
        bottom_bar.set_margin_bottom(12)
        bottom_bar.set_spacing(6)

        # Spacer to push button to right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bottom_bar.append(spacer)

        # Save button
        self.save_button = Gtk.Button(label="Save")
        self.save_button.set_css_classes(["suggested-action"])
        self.save_button.set_sensitive(False)
        self.save_button.connect("clicked", self.on_save_clicked)
        bottom_bar.append(self.save_button)

        toolbar_view.add_bottom_bar(bottom_bar)

        self.set_child(toolbar_view)

    def on_field_changed(self, widget=None, param=None):
        """Called when any field changes"""
        current = self.get_current_config()
        self.has_changes = (current != self.original_config)
        self.save_button.set_sensitive(self.has_changes)

    def on_save_clicked(self, button):
        """Save button clicked"""
        current_config = self.get_current_config()
        self.config_manager.save(current_config)
        self.original_config = current_config.copy()
        self.has_changes = False
        self.save_button.set_sensitive(False)

    def get_current_config(self):
        cfg = self.config_manager.config.copy()
        cfg["re_encode"] = self.reencode_switch.get_active()
        return cfg


class SelectArea(Gtk.Box):
    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.main_window = main_window

        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_spacing(12)

        # Upload Icon
        icon_image = Gtk.Image.new_from_icon_name("video-x-generic-symbolic")
        icon_image.set_pixel_size(64)
        icon_image.set_css_classes(["accent"])
        icon_image.set_halign(Gtk.Align.CENTER)
        self.append(icon_image)

        # Browse Button
        self.browse_button = Gtk.Button(label="Browse")
        self.browse_button.set_css_classes(["suggested-action", "pill"])
        self.browse_button.set_halign(Gtk.Align.CENTER)
        self.browse_button.connect("clicked", self.main_window.on_select_video)
        self.append(self.browse_button)

        # Dimmed Subtitle
        subtitle_label = Gtk.Label()
        subtitle_label.set_label("Browse videos to patch for TikTok")
        subtitle_label.set_wrap(True)
        subtitle_label.set_justify(Gtk.Justification.CENTER)
        subtitle_label.set_css_classes(["caption", "dim-label"])
        subtitle_label.set_opacity(0.6)
        subtitle_label.set_halign(Gtk.Align.CENTER)
        subtitle_label.set_max_width_chars(35)
        self.append(subtitle_label)


class MainView(Adw.NavigationPage):
    def __init__(self, main_window):
        super().__init__()
        self.set_title("TikTok Patcher")
        self.set_tag("main")
        self.main_window = main_window

        toolbar_view = Adw.ToolbarView()

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_title(True)
        toolbar_view.add_top_bar(header)

        # Main content box
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.set_hexpand(True)
        box.set_spacing(16)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)

        # Centered Selection Area Box
        self.select_area = SelectArea(self.main_window)
        self.select_area.set_vexpand(True)
        box.append(self.select_area)

        # Bottom gear button container
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_box.set_halign(Gtk.Align.CENTER)
        bottom_box.set_valign(Gtk.Align.END)

        self.settings_button = Gtk.Button()
        self.settings_button.set_icon_name("preferences-system-symbolic")
        self.settings_button.set_css_classes(["circular"])
        self.settings_button.set_tooltip_text("Settings")
        self.settings_button.connect("clicked", self.main_window.on_open_settings)
        bottom_box.append(self.settings_button)

        box.append(bottom_box)

        # Progress area
        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.progress_box.set_visible(False)
        self.progress_box.set_spacing(6)
        self.progress_box.set_halign(Gtk.Align.CENTER)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_halign(Gtk.Align.CENTER)
        self.progress_bar.set_hexpand(False)
        self.progress_bar.set_size_request(200, -1)

        self.status_label = Gtk.Label()
        self.status_label.set_css_classes(["caption"])
        self.status_label.set_halign(Gtk.Align.CENTER)

        self.progress_box.append(self.progress_bar)
        self.progress_box.append(self.status_label)
        box.append(self.progress_box)

        toolbar_view.set_content(box)
        self.set_child(toolbar_view)

    def set_inputs_enabled(self, enabled: bool):
        self.select_area.set_sensitive(enabled)
        self.settings_button.set_sensitive(enabled)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(500, 650)
        self.set_title("TikTok Patcher")

        self.config_manager = ConfigManager()
        self.pulse_timeout_id = None
        self.current_input_path = None

        # Navigation view for full-screen page transitions
        self.navigation_view = Adw.NavigationView()

        # Main Page
        self.main_view = MainView(self)
        self.navigation_view.add(self.main_view)

        # Preferences Page
        self.preferences_view = PreferencesView(self.config_manager)

        self.set_content(self.navigation_view)

    def on_select_video(self, button):
        if not self.main_view.select_area.get_sensitive():
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Select Video")
        dialog.set_modal(True)

        filter_video = Gtk.FileFilter()
        filter_video.set_name("Video files")
        filter_video.add_mime_type("video/*")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_video)
        dialog.set_filters(filters)

        dialog.open(self, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                self.process_file(path)
        except Exception:
            pass

    def pulse_progress(self):
        self.main_view.progress_bar.pulse()
        return True

    def process_file(self, path):
        self.main_view.set_inputs_enabled(False)

        if not HAS_PATCHER:
            self.show_error("Patching module not available")
            return

        self.current_input_path = path
        self.main_view.progress_box.set_visible(True)
        self.main_view.status_label.set_label("Processing...")

        if self.pulse_timeout_id is None:
            self.pulse_timeout_id = GLib.timeout_add(250, self.pulse_progress)

        config = self.config_manager.config

        def do_patch():
            try:
                patch_video(path, config)
                GLib.idle_add(self.on_processing_complete, True)
            except Exception as e:
                GLib.idle_add(self.on_processing_complete, False, str(e))

        thread = threading.Thread(target=do_patch, daemon=True)
        thread.start()

    def on_processing_complete(self, success, error_msg=None):
        if self.pulse_timeout_id is not None:
            GLib.source_remove(self.pulse_timeout_id)
            self.pulse_timeout_id = None

        self.main_view.progress_box.set_visible(False)
        
        if success:
            self.main_view.status_label.set_label("Done!")
            
            input_stem = Path(self.current_input_path).stem if self.current_input_path else "file"
            
            dialog = Adw.AlertDialog()
            dialog.set_heading("Processing finished")
            dialog.set_body(f"Saved to {input_stem}_tiktok.mp4")
            
            dialog.add_response("ok", "OK")
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", self.on_dialog_closed)
            dialog.present(self)
        else:
            self.show_error(error_msg or "Processing failed")

    def show_error(self, message):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Error")
        dialog.set_body(message)
        dialog.add_response("ok", "OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DEFAULT)
        dialog.connect("response", self.on_dialog_closed)
        dialog.present(self)

    def on_dialog_closed(self, dialog, response):
        self.main_view.set_inputs_enabled(True)

    def on_open_settings(self, button):
        if self.main_view.settings_button.get_sensitive():
            self.navigation_view.push(self.preferences_view)


class TiktokPatcherApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="net.buwryy.TiktokPatcher",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self):
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()


def main():
    app = TiktokPatcherApp()
    app.run(sys.argv)

if __name__ == "__main__":
    main()
