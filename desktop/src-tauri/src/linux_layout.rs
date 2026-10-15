//! GTK's default vertical box stacks child webviews instead of applying their x/y bounds.
//! Keep the shell as the expanding base and place chat above its reserved content area.
use gtk::prelude::*;
use crate::Rect;

pub fn prepare_shell(view: &tauri::Webview) -> tauri::Result<()> {
    view.with_webview(|platform| {
        let native = platform.inner();
        let Some(parent) = native.parent().and_then(|p| p.downcast::<gtk::Box>().ok()) else { return };
        parent.remove(&native);
        let overlay = gtk::Overlay::new();
        overlay.set_hexpand(true);
        overlay.set_vexpand(true);
        native.set_hexpand(true);
        native.set_vexpand(true);
        overlay.add(&native);
        parent.pack_start(&overlay, true, true, 0);
        overlay.show_all();
    })
}

pub fn layout_chat(view: &tauri::Webview, rect: Rect, visible: bool) -> tauri::Result<()> {
    view.with_webview(move |platform| {
        let native = platform.inner();
        if let Some(parent) = native.parent().and_then(|p| p.downcast::<gtk::Box>().ok()) {
            let Some(overlay) = parent.children().into_iter().find_map(|w| w.downcast::<gtk::Overlay>().ok()) else { return };
            parent.remove(&native);
            native.set_halign(gtk::Align::Start);
            native.set_valign(gtk::Align::Start);
            native.set_hexpand(false);
            native.set_vexpand(false);
            overlay.add_overlay(&native);
        }
        if visible && rect.width > 0.0 && rect.height > 0.0 {
            native.set_margin_start(rect.x.max(0.0).round() as i32);
            native.set_margin_top(rect.y.max(0.0).round() as i32);
            native.set_size_request(rect.width.round() as i32, rect.height.round() as i32);
            native.show();
        } else {
            native.hide();
        }
    })
}
