use glib::object::Cast;
use gtk::prelude::*;
use gtk::{
    Align, Application, ApplicationWindow, Adjustment, Box as GtkBox, Button, CheckButton,
    DropDown, Entry, FileDialog, Label, Orientation, Paned, ScrolledWindow, Separator,
    SpinButton, TextBuffer, TextView,
};
use chrono::{DateTime, FixedOffset, Local, TimeZone};
use serde::Deserialize;
use std::cell::RefCell;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::rc::Rc;

#[derive(Debug, Clone, Deserialize)]
struct InspectField {
    name: String,
    label: String,
    category: String,
    unit: Option<String>,
    min: Option<f64>,
    max: Option<f64>,
}

#[derive(Debug, Clone, Deserialize)]
struct InspectPayload {
    sport: String,
    has_gps: bool,
    #[serde(default)]
    session_start: Option<String>,
    #[serde(default)]
    fit_device: Option<FitDeviceInfo>,
    fields: Vec<InspectField>,
}

#[derive(Debug, Clone, Deserialize)]
struct FitDeviceInfo {
    #[serde(default)]
    label: Option<String>,
}

#[derive(Clone, Copy, PartialEq)]
enum WizardStep {
    Fit,
    SyncFit,
    Videos,
    SyncCameras,
    Audio,
    SyncAudio,
    Ready,
}

#[derive(Clone, Debug)]
struct MediaClip {
    path: PathBuf,
    metadata_start: String,
    start: String,
    duration: f64,
}

#[derive(Clone, Debug)]
struct DeviceGroup {
    id: String,
    label: String,
    kind: String,
    clips: Vec<MediaClip>,
    device_clock: String,
}

#[derive(Clone)]
struct State {
    step: WizardStep,
    fit: Option<PathBuf>,
    inspect: Option<InspectPayload>,
    selected: Vec<String>,
    include_map: bool,
    /// "fps" (US customary, default) or "metric"
    unit_system: String,
    mode: String,
    threshold_field: String,
    threshold_value: f64,
    pad_before: f64,
    pad_after: f64,
    output: PathBuf,
    log: String,
    fit_label: String,
    fit_reference: String,
    watch_clock: String,
    wall_clock: String,
    camera_groups: Vec<DeviceGroup>,
    audio_groups: Vec<DeviceGroup>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            step: WizardStep::Fit,
            fit: None,
            inspect: None,
            selected: Vec::new(),
            include_map: false,
            unit_system: "fps".into(),
            mode: "videos".into(),
            threshold_field: "speed".into(),
            threshold_value: 0.0,
            pad_before: 2.0,
            pad_after: 2.0,
            output: PathBuf::from("highlight.mp4"),
            log: String::new(),
            fit_label: "FIT device".into(),
            fit_reference: String::new(),
            watch_clock: String::new(),
            wall_clock: String::new(),
            camera_groups: Vec::new(),
            audio_groups: Vec::new(),
        }
    }
}

fn margin(widget: &impl WidgetExt, px: i32) {
    widget.set_margin_top(px);
    widget.set_margin_bottom(px);
    widget.set_margin_start(px);
    widget.set_margin_end(px);
}

fn fitvid_bin() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            let bundled = parent.join("../lib/fitvid/fitvid");
            if bundled.is_file() {
                return bundled;
            }
        }
    }
    PathBuf::from("fitvid")
}

fn run_inspect(fit: &Path) -> Result<InspectPayload, String> {
    let out = Command::new(fitvid_bin())
        .args(["inspect", &fit.to_string_lossy(), "--format", "json"])
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).to_string());
    }
    serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
}

fn display_unit(field: &str, unit_system: &str, fallback: &str) -> String {
    let fps = unit_system != "metric";
    match (fps, field) {
        (true, "speed") => "mph".into(),
        (true, "altitude") => "ft".into(),
        (true, "distance") => "mi".into(),
        (true, "temperature") | (true, "core_temperature") => "°F".into(),
        (true, "vertical_oscillation") | (true, "step_length") => "in".into(),
        (false, "speed") => "km/h".into(),
        (false, "altitude") | (false, "distance") => "m".into(),
        (false, "temperature") | (false, "core_temperature") => "°C".into(),
        _ => fallback.to_string(),
    }
}

fn convert_si(field: &str, raw: f64, unit_system: &str) -> f64 {
    if unit_system != "metric" {
        return match field {
            "speed" => raw * 2.236936,
            "altitude" => raw * 3.28084,
            "distance" => raw / 1609.344,
            "temperature" | "core_temperature" => raw * 9.0 / 5.0 + 32.0,
            "vertical_oscillation" | "step_length" => raw / 25.4,
            _ => raw,
        };
    }
    if field == "speed" {
        raw * 3.6
    } else {
        raw
    }
}

fn format_range_val(v: f64) -> String {
    let a = v.abs();
    if a >= 100.0 {
        format!("{v:.0}")
    } else if a >= 10.0 {
        format!("{v:.1}")
    } else {
        format!("{v:.3}")
    }
}

fn range_caption(field: &InspectField, unit_system: &str) -> String {
    let fallback = field.unit.clone().unwrap_or_default();
    let unit = display_unit(&field.name, unit_system, &fallback);
    let mn = field
        .min
        .map(|v| format_range_val(convert_si(&field.name, v, unit_system)))
        .unwrap_or_else(|| "-".into());
    let mx = field
        .max
        .map(|v| format_range_val(convert_si(&field.name, v, unit_system)))
        .unwrap_or_else(|| "-".into());
    if unit.is_empty() {
        format!("{mn} – {mx}")
    } else {
        format!("{unit}  {mn} – {mx}")
    }
}

fn overlay_yaml(state: &State) -> String {
    let unit = if state.unit_system == "metric" {
        "metric"
    } else {
        "fps"
    };
    let formats: &[(&str, &str)] = if unit == "metric" {
        &[
            ("speed", "{value:.1f} km/h"),
            ("heart_rate", "{value:.0f} bpm"),
            ("grade", "{value:.1f}%"),
            ("power", "{value:.0f} W"),
            ("cadence", "{value:.0f}"),
            ("altitude", "{value:.0f} m"),
            ("distance", "{value:.0f} m"),
            ("temperature", "{value:.1f} °C"),
            ("core_temperature", "{value:.1f} °C"),
        ]
    } else {
        &[
            ("speed", "{value:.1f} mph"),
            ("heart_rate", "{value:.0f} bpm"),
            ("grade", "{value:.1f}%"),
            ("power", "{value:.0f} W"),
            ("cadence", "{value:.0f}"),
            ("altitude", "{value:.0f} ft"),
            ("distance", "{value:.2f} mi"),
            ("temperature", "{value:.1f} °F"),
            ("core_temperature", "{value:.1f} °F"),
        ]
    };
    let mut s = format!("overlay:\n  unit_system: {unit}\n  text:\n");
    let fields: Vec<&InspectField> = state
        .inspect
        .as_ref()
        .map(|i| {
            i.fields
                .iter()
                .filter(|f| state.selected.iter().any(|n| n == &f.name))
                .collect()
        })
        .unwrap_or_default();
    if fields.is_empty() {
        s.push_str("    []\n");
    } else {
        for (i, f) in fields.iter().enumerate() {
            let fmt = formats
                .iter()
                .find(|(n, _)| *n == f.name)
                .map(|(_, v)| *v)
                .unwrap_or("{value}");
            let y = (0.88 - i as f64 * 0.065).max(0.04);
            s.push_str(&format!("    - field: {}\n", f.name));
            s.push_str(&format!("      format: \"{fmt}\"\n"));
            s.push_str(&format!("      label: \"{}\"\n", f.label));
            s.push_str(&format!("      unit_system: {unit}\n"));
            s.push_str(&format!("      position: [0.02, {y:.3}]\n"));
        }
    }
    if state.include_map && state.inspect.as_ref().map(|i| i.has_gps).unwrap_or(false) {
        s.push_str(
            "  map:\n    style: route-only\n    anchor: bottom-right\n    width_px: 280\n    height_px: 280\n",
        );
    }
    s
}

fn sync_yaml(state: &State) -> String {
    let mut s = String::from("sync:\n  fit_generator:\n");
    s.push_str(&format!("    label: \"{}\"\n", state.fit_label.replace('"', "\\\"")));
    s.push_str(&format!("    fit_reference: \"{}\"\n", state.fit_reference));
    s.push_str(&format!(
        "    watch_clock: \"{}\"\n",
        if state.watch_clock.is_empty() {
            &state.fit_reference
        } else {
            &state.watch_clock
        }
    ));
    if state.wall_clock.is_empty() {
        s.push_str("    wall_clock: null\n");
    } else {
        s.push_str(&format!("    wall_clock: \"{}\"\n", state.wall_clock));
    }
    s.push_str("  devices:\n");
    let all: Vec<&DeviceGroup> = state
        .camera_groups
        .iter()
        .chain(state.audio_groups.iter())
        .collect();
    if all.is_empty() {
        s.push_str("    []\n");
    } else {
        for g in all {
            s.push_str(&format!("    - id: {}\n", g.id));
            s.push_str(&format!("      kind: {}\n", g.kind));
            s.push_str(&format!("      label: \"{}\"\n", g.label.replace('"', "\\\"")));
            // Starts already corrected in the UI via --video-start / --audio-start
            s.push_str("      offset_seconds: 0\n");
            s.push_str("      files:\n");
            for c in &g.clips {
                s.push_str(&format!("        - {}\n", c.path.display()));
            }
        }
    }
    s
}

fn select_yaml(state: &State) -> String {
    if state.mode == "threshold" {
        format!(
            "select:\n  - type: threshold\n    field: {}\n    op: \">\"\n    value: {}\n    min_duration: 5\n",
            state.threshold_field, state.threshold_value
        )
    } else if state.mode == "laps" {
        "select:\n  - type: laps\n".into()
    } else {
        "select:\n  - type: videos\n".into()
    }
}

#[derive(Clone, Debug)]
struct ProbedGroup {
    id: String,
    label: String,
    clips: Vec<MediaClip>,
}

fn run_probe_media(paths: &[PathBuf], fallback_start: &str) -> Result<Vec<ProbedGroup>, String> {
    if paths.is_empty() {
        return Ok(Vec::new());
    }
    let mut cmd = Command::new(fitvid_bin());
    cmd.arg("probe-media").arg("--group");
    for p in paths {
        cmd.arg(p);
    }
    let out = cmd.output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).to_string());
    }
    #[derive(Deserialize)]
    struct ProbeFile {
        devices: Vec<ProbeDevice>,
    }
    #[derive(Deserialize)]
    struct ProbeDevice {
        id: String,
        label: String,
        #[serde(default)]
        files: Vec<String>,
        #[serde(default)]
        probes: Vec<ProbeClip>,
    }
    #[derive(Deserialize)]
    struct ProbeClip {
        path: Option<String>,
        start_time: Option<String>,
        #[serde(default)]
        duration: f64,
    }
    let payload: ProbeFile = serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())?;
    Ok(payload
        .devices
        .into_iter()
        .map(|d| {
            let probe_by_path: std::collections::HashMap<String, ProbeClip> = d
                .probes
                .into_iter()
                .filter_map(|p| p.path.clone().map(|path| (path, p)))
                .collect();
            let files = if d.files.is_empty() {
                probe_by_path.keys().cloned().collect()
            } else {
                d.files
            };
            let clips = files
                .into_iter()
                .map(|path| {
                    let (start, duration) = probe_by_path
                        .get(&path)
                        .map(|p| {
                            (
                                p.start_time
                                    .clone()
                                    .unwrap_or_else(|| fallback_start.to_string()),
                                p.duration,
                            )
                        })
                        .unwrap_or_else(|| (fallback_start.to_string(), 0.0));
                    MediaClip {
                        path: PathBuf::from(path),
                        metadata_start: start.clone(),
                        start,
                        duration,
                    }
                })
                .collect();
            ProbedGroup {
                id: d.id,
                label: d.label,
                clips,
            }
        })
        .collect())
}

fn parse_iso(s: &str) -> Option<DateTime<FixedOffset>> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    if let Ok(dt) = DateTime::parse_from_rfc3339(t) {
        return Some(dt);
    }
    let prefix = if t.len() >= 19 { &t[..19] } else { t };
    let naive = chrono::NaiveDateTime::parse_from_str(prefix, "%Y-%m-%dT%H:%M:%S")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(prefix, "%Y-%m-%d %H:%M:%S"))
        .ok()?;
    Local
        .from_local_datetime(&naive)
        .single()
        .map(|dt| dt.fixed_offset())
}

fn format_iso(dt: DateTime<FixedOffset>) -> String {
    dt.with_timezone(&Local)
        .format("%Y-%m-%dT%H:%M:%S%:z")
        .to_string()
}

fn spine_iso(state: &State) -> String {
    if !state.wall_clock.trim().is_empty() {
        return state.wall_clock.clone();
    }
    let watch = if state.watch_clock.is_empty() {
        &state.fit_reference
    } else {
        &state.watch_clock
    };
    let Some(ref_t) = parse_iso(&state.fit_reference) else {
        return state.fit_reference.clone();
    };
    let watch_t = parse_iso(watch).unwrap_or(ref_t);
    let delta = watch_t - ref_t;
    format_iso(ref_t + delta)
}

fn apply_device_offsets(state: &State, groups: &mut [DeviceGroup]) {
    let spine = spine_iso(state);
    let Some(spine_t) = parse_iso(&spine) else {
        return;
    };
    for g in groups.iter_mut() {
        let device_clock = if g.device_clock.trim().is_empty() {
            spine.clone()
        } else {
            g.device_clock.clone()
        };
        let Some(device_t) = parse_iso(&device_clock) else {
            continue;
        };
        let offset = spine_t - device_t;
        for clip in &mut g.clips {
            if let Some(meta) = parse_iso(&clip.metadata_start) {
                clip.start = format_iso(meta + offset);
            } else {
                clip.start = clip.metadata_start.clone();
            }
        }
    }
}

fn append_log(buffer: &TextBuffer, state: &Rc<RefCell<State>>, line: &str) {
    state.borrow_mut().log.push_str(line);
    if !line.ends_with('\n') {
        state.borrow_mut().log.push('\n');
    }
    buffer.set_text(&state.borrow().log);
}

fn fill_device_cards(
    device_cards: &GtkBox,
    groups: &[DeviceGroup],
    is_video: bool,
    state: &Rc<RefCell<State>>,
) {
    while let Some(c) = device_cards.first_child() {
        device_cards.remove(&c);
    }
    for g in groups {
        let card = GtkBox::new(Orientation::Vertical, 4);
        let label_entry = Entry::new();
        label_entry.set_text(&g.label);
        let clock_entry = Entry::new();
        clock_entry.set_text(&g.device_clock);
        clock_entry.set_placeholder_text(Some("Device clock at sync moment (ISO)"));
        let files_lbl = Label::new(Some(
            &g.clips
                .iter()
                .filter_map(|c| c.path.file_name().and_then(|s| s.to_str()))
                .collect::<Vec<_>>()
                .join(", "),
        ));
        files_lbl.add_css_class("dim-label");
        files_lbl.set_wrap(true);
        let id_owned = g.id.clone();
        let state_l = state.clone();
        label_entry.connect_changed(move |e| {
            let text = e.text().to_string();
            let mut st = state_l.borrow_mut();
            let list = if is_video {
                &mut st.camera_groups
            } else {
                &mut st.audio_groups
            };
            if let Some(g) = list.iter_mut().find(|g| g.id == id_owned) {
                g.label = text;
            }
        });
        let id_owned2 = g.id.clone();
        let state_c = state.clone();
        clock_entry.connect_changed(move |e| {
            let text = e.text().to_string();
            let mut st = state_c.borrow_mut();
            let list = if is_video {
                &mut st.camera_groups
            } else {
                &mut st.audio_groups
            };
            if let Some(g) = list.iter_mut().find(|g| g.id == id_owned2) {
                g.device_clock = text;
            }
        });
        card.append(&label_entry);
        card.append(&files_lbl);
        card.append(&clock_entry);
        device_cards.append(&card);
    }
}

fn apply_probe_groups(
    state: &mut State,
    probed: Vec<ProbedGroup>,
    kind: &str,
    fallback_files: &[PathBuf],
) {
    let target = if kind == "video" {
        &mut state.camera_groups
    } else {
        &mut state.audio_groups
    };
    target.clear();
    if probed.is_empty() && !fallback_files.is_empty() {
        let id = if kind == "video" {
            "camera-1"
        } else {
            "recorder-1"
        };
        let label = if kind == "video" {
            "Camera"
        } else {
            "Audio recorder"
        };
        let clips = fallback_files
            .iter()
            .map(|p| MediaClip {
                path: p.clone(),
                metadata_start: state.fit_reference.clone(),
                start: state.fit_reference.clone(),
                duration: 0.0,
            })
            .collect();
        target.push(DeviceGroup {
            id: id.into(),
            label: label.into(),
            kind: kind.into(),
            clips,
            device_clock: state.fit_reference.clone(),
        });
        return;
    }
    for (i, mut g) in probed.into_iter().enumerate() {
        let mut id = g.id;
        if kind == "audio" && !id.ends_with("-audio") {
            id = format!("{id}-audio");
        }
        if id.is_empty() {
            id = format!("{kind}-{i}");
        }
        if g.label.is_empty() {
            g.label = id.clone();
        }
        target.push(DeviceGroup {
            id,
            label: g.label,
            kind: kind.into(),
            clips: g.clips,
            device_clock: state.fit_reference.clone(),
        });
    }
}

fn all_video_clips(state: &State) -> Vec<MediaClip> {
    state
        .camera_groups
        .iter()
        .flat_map(|g| g.clips.clone())
        .collect()
}

fn all_audio_clips(state: &State) -> Vec<MediaClip> {
    state
        .audio_groups
        .iter()
        .flat_map(|g| g.clips.clone())
        .collect()
}

fn build_media_source_card(
    group: &DeviceGroup,
    is_video: bool,
    state: &Rc<RefCell<State>>,
    refresh: &Rc<dyn Fn()>,
) -> GtkBox {
    let card = GtkBox::new(Orientation::Vertical, 4);
    let title = Label::new(Some(&group.label));
    title.set_halign(Align::Start);
    title.add_css_class("heading");
    let clock = Label::new(Some(&format!("Device clock: {}", group.device_clock)));
    clock.set_halign(Align::Start);
    clock.add_css_class("dim-label");
    clock.set_wrap(true);
    card.append(&title);
    card.append(&clock);
    for clip in &group.clips {
        let row = GtkBox::new(Orientation::Vertical, 2);
        let name_row = GtkBox::new(Orientation::Horizontal, 6);
        let name = clip
            .path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("media");
        let name_lbl = Label::new(Some(name));
        name_lbl.set_halign(Align::Start);
        name_lbl.set_hexpand(true);
        let remove = Button::with_label("×");
        let clip_path = clip.path.clone();
        let state_r = state.clone();
        let refresh_r = refresh.clone();
        remove.connect_clicked(move |_| {
            let mut st = state_r.borrow_mut();
            let list = if is_video {
                &mut st.camera_groups
            } else {
                &mut st.audio_groups
            };
            for g in list.iter_mut() {
                g.clips.retain(|c| c.path != clip_path);
            }
            list.retain(|g| !g.clips.is_empty());
            drop(st);
            refresh_r();
        });
        name_row.append(&name_lbl);
        name_row.append(&remove);
        row.append(&name_row);

        let start_lbl = Label::new(Some("Start (local ISO)"));
        start_lbl.set_halign(Align::Start);
        let start_entry = Entry::new();
        start_entry.set_text(&clip.start);
        let clip_path2 = clip.path.clone();
        let state_s = state.clone();
        start_entry.connect_changed(move |e| {
            let text = e.text().to_string();
            let mut st = state_s.borrow_mut();
            let list = if is_video {
                &mut st.camera_groups
            } else {
                &mut st.audio_groups
            };
            for g in list.iter_mut() {
                if let Some(c) = g.clips.iter_mut().find(|c| c.path == clip_path2) {
                    c.start = text.clone();
                }
            }
        });
        row.append(&start_lbl);
        row.append(&start_entry);

        let dur_row = GtkBox::new(Orientation::Horizontal, 6);
        dur_row.append(&Label::new(Some("Duration (s)")));
        let adj = Adjustment::new(clip.duration.max(0.1), 0.1, 86400.0, 0.5, 1.0, 0.0);
        let dur_spin = SpinButton::new(Some(&adj), 0.5, 1);
        let clip_path3 = clip.path.clone();
        let state_d = state.clone();
        dur_spin.connect_value_changed(move |s| {
            let val = s.value();
            let mut st = state_d.borrow_mut();
            let list = if is_video {
                &mut st.camera_groups
            } else {
                &mut st.audio_groups
            };
            for g in list.iter_mut() {
                if let Some(c) = g.clips.iter_mut().find(|c| c.path == clip_path3) {
                    c.duration = val;
                }
            }
        });
        dur_row.append(&dur_spin);
        row.append(&dur_row);

        if clip.metadata_start != clip.start {
            let meta = Label::new(Some(&format!("meta {}", clip.metadata_start)));
            meta.set_halign(Align::Start);
            meta.add_css_class("dim-label");
            row.append(&meta);
        }
        card.append(&row);
    }
    card
}

fn collect_files(list: &gtk::gio::ListModel) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    for i in 0..list.n_items() {
        if let Some(obj) = list.item(i) {
            if let Ok(f) = obj.downcast::<gtk::gio::File>() {
                if let Some(p) = f.path() {
                    paths.push(p);
                }
            }
        }
    }
    paths
}

fn build_ui(app: &Application) {
    let state = Rc::new(RefCell::new(State::default()));

    let window = ApplicationWindow::builder()
        .application(app)
        .title("fitvid")
        .default_width(1000)
        .default_height(700)
        .build();

    let root = GtkBox::new(Orientation::Vertical, 0);

    let wizard = GtkBox::new(Orientation::Vertical, 16);
    wizard.set_valign(Align::Center);
    wizard.set_halign(Align::Center);
    margin(&wizard, 40);
    let brand = Label::new(Some("fitvid"));
    brand.add_css_class("title-1");
    let wizard_title = Label::new(Some("Select activity"));
    wizard_title.add_css_class("title-2");
    let wizard_sub = Label::new(Some(
        "Pick the .fit file from your GPS watch or bike computer.",
    ));
    wizard_sub.set_wrap(true);

    let fit_sync_box = GtkBox::new(Orientation::Vertical, 8);
    fit_sync_box.set_visible(false);
    let fit_label_entry = Entry::new();
    fit_label_entry.set_placeholder_text(Some("Device label"));
    let fit_ref_entry = Entry::new();
    fit_ref_entry.set_placeholder_text(Some("FIT reference (ISO)"));
    let watch_entry = Entry::new();
    watch_entry.set_placeholder_text(Some("Watch showed (ISO)"));
    let wall_entry = Entry::new();
    wall_entry.set_placeholder_text(Some("Wall clock / phone (optional ISO)"));
    fit_sync_box.append(&Label::new(Some("FIT generator")));
    fit_sync_box.append(&fit_label_entry);
    fit_sync_box.append(&fit_ref_entry);
    fit_sync_box.append(&watch_entry);
    fit_sync_box.append(&wall_entry);

    let device_sync_box = GtkBox::new(Orientation::Vertical, 8);
    device_sync_box.set_visible(false);
    let device_sync_title = Label::new(Some("Devices"));
    device_sync_title.add_css_class("heading");
    let device_cards = GtkBox::new(Orientation::Vertical, 8);
    device_sync_box.append(&device_sync_title);
    device_sync_box.append(&device_cards);

    let wizard_btn = Button::with_label("Choose FIT file…");
    wizard_btn.add_css_class("suggested-action");
    let skip_btn = Button::with_label("Skip");
    skip_btn.set_visible(false);
    let wizard_row = GtkBox::new(Orientation::Horizontal, 12);
    wizard_row.set_halign(Align::Center);
    wizard_row.append(&wizard_btn);
    wizard_row.append(&skip_btn);
    wizard.append(&brand);
    wizard.append(&wizard_title);
    wizard.append(&wizard_sub);
    wizard.append(&fit_sync_box);
    wizard.append(&device_sync_box);
    wizard.append(&wizard_row);

    let main = GtkBox::new(Orientation::Vertical, 0);
    main.set_visible(false);

    let paned = Paned::new(Orientation::Horizontal);
    let field_scroll = ScrolledWindow::new();
    field_scroll.set_size_request(280, -1);
    let field_box = GtkBox::new(Orientation::Vertical, 6);
    margin(&field_box, 8);
    field_scroll.set_child(Some(&field_box));

    let work = GtkBox::new(Orientation::Vertical, 8);
    margin(&work, 12);
    let mode = DropDown::from_strings(&["All videos", "Laps", "Threshold"]);
    let log_buffer = TextBuffer::new(None);
    let log_view = TextView::with_buffer(&log_buffer);
    log_view.set_editable(false);
    log_view.set_monospace(true);
    let log_scroll = ScrolledWindow::new();
    log_scroll.set_vexpand(true);
    log_scroll.set_child(Some(&log_view));
    let dry = Button::with_label("Dry run");
    let compile = Button::with_label("Generate");
    compile.add_css_class("suggested-action");
    let run_row = GtkBox::new(Orientation::Horizontal, 8);
    run_row.append(&dry);
    run_row.append(&compile);
    work.append(&Label::new(Some("Highlights")));
    work.append(&mode);
    work.append(&run_row);
    work.append(&Label::new(Some("Log")));
    work.append(&log_scroll);
    paned.set_start_child(Some(&field_scroll));
    paned.set_end_child(Some(&work));
    paned.set_resize_start_child(false);
    paned.set_vexpand(true);

    let dock = GtkBox::new(Orientation::Horizontal, 12);
    margin(&dock, 8);
    dock.set_size_request(-1, 220);

    let video_col = GtkBox::new(Orientation::Vertical, 4);
    video_col.set_hexpand(true);
    let video_header = GtkBox::new(Orientation::Horizontal, 8);
    video_header.append(&Label::new(Some("Videos by source")));
    let add_video = Button::with_label("+");
    video_header.append(&add_video);
    let video_scroll = ScrolledWindow::new();
    video_scroll.set_vexpand(true);
    video_scroll.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
    let video_host = GtkBox::new(Orientation::Vertical, 8);
    video_scroll.set_child(Some(&video_host));
    video_col.append(&video_header);
    video_col.append(&video_scroll);

    let audio_col = GtkBox::new(Orientation::Vertical, 4);
    audio_col.set_hexpand(true);
    let audio_header = GtkBox::new(Orientation::Horizontal, 8);
    audio_header.append(&Label::new(Some("Audio by source")));
    let add_audio = Button::with_label("+");
    audio_header.append(&add_audio);
    let audio_scroll = ScrolledWindow::new();
    audio_scroll.set_vexpand(true);
    audio_scroll.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
    let audio_host = GtkBox::new(Orientation::Vertical, 8);
    audio_scroll.set_child(Some(&audio_host));
    audio_col.append(&audio_header);
    audio_col.append(&audio_scroll);

    let fit_col = GtkBox::new(Orientation::Vertical, 4);
    fit_col.set_size_request(180, -1);
    fit_col.append(&Label::new(Some("FIT")));
    let fit_host = GtkBox::new(Orientation::Vertical, 4);
    fit_col.append(&fit_host);

    dock.append(&video_col);
    dock.append(&Separator::new(Orientation::Vertical));
    dock.append(&audio_col);
    dock.append(&Separator::new(Orientation::Vertical));
    dock.append(&fit_col);

    main.append(&paned);
    main.append(&Separator::new(Orientation::Horizontal));
    main.append(&dock);
    root.append(&wizard);
    root.append(&main);
    window.set_child(Some(&root));

    let refresh_dock_slot: Rc<RefCell<Option<Rc<dyn Fn()>>>> = Rc::new(RefCell::new(None));

    let refresh_dock = {
        let video_host = video_host.clone();
        let audio_host = audio_host.clone();
        let fit_host = fit_host.clone();
        let state = state.clone();
        let refresh_dock_slot = refresh_dock_slot.clone();
        Rc::new(move || {
            while let Some(c) = video_host.first_child() {
                video_host.remove(&c);
            }
            while let Some(c) = audio_host.first_child() {
                audio_host.remove(&c);
            }
            while let Some(c) = fit_host.first_child() {
                fit_host.remove(&c);
            }

            let call_refresh = {
                let slot = refresh_dock_slot.clone();
                Rc::new(move || {
                    if let Some(f) = slot.borrow().clone() {
                        f();
                    }
                }) as Rc<dyn Fn()>
            };

            let st = state.borrow().clone();
            if st.camera_groups.is_empty() {
                let empty = Label::new(Some("No videos"));
                empty.add_css_class("dim-label");
                video_host.append(&empty);
            } else {
                for g in &st.camera_groups {
                    video_host.append(&build_media_source_card(
                        g,
                        true,
                        &state,
                        &call_refresh,
                    ));
                }
            }
            if st.audio_groups.is_empty() {
                let empty = Label::new(Some("No audio"));
                empty.add_css_class("dim-label");
                audio_host.append(&empty);
            } else {
                for g in &st.audio_groups {
                    audio_host.append(&build_media_source_card(
                        g,
                        false,
                        &state,
                        &call_refresh,
                    ));
                }
            }
            if let Some(fit) = &st.fit {
                let fit_lbl = Label::new(Some(
                    fit.file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("activity.fit"),
                ));
                fit_lbl.set_halign(Align::Start);
                fit_host.append(&fit_lbl);
                let fit_time = Label::new(Some(&st.fit_reference));
                fit_time.set_halign(Align::Start);
                fit_time.add_css_class("dim-label");
                fit_time.set_wrap(true);
                fit_host.append(&fit_time);
            }
        }) as Rc<dyn Fn()>
    };
    *refresh_dock_slot.borrow_mut() = Some(refresh_dock.clone());

    let rebuild_fields_slot: Rc<RefCell<Option<Rc<dyn Fn()>>>> = Rc::new(RefCell::new(None));

    let rebuild_fields = {
        let field_box = field_box.clone();
        let state = state.clone();
        let rebuild_fields_slot = rebuild_fields_slot.clone();
        Rc::new(move || {
            while let Some(c) = field_box.first_child() {
                field_box.remove(&c);
            }
            field_box.append(&Label::new(Some("Telemetry")));
            let units = DropDown::from_strings(&["FPS", "Metric"]);
            units.set_selected(if state.borrow().unit_system == "metric" { 1 } else { 0 });
            let state_u = state.clone();
            let rebuild_slot = rebuild_fields_slot.clone();
            units.connect_selected_notify(move |dd| {
                let next = if dd.selected() == 1 {
                    "metric".to_string()
                } else {
                    "fps".to_string()
                };
                {
                    let mut st = state_u.borrow_mut();
                    if st.unit_system == next {
                        return;
                    }
                    st.unit_system = next;
                }
                if let Some(f) = rebuild_slot.borrow().clone() {
                    f();
                }
            });
            field_box.append(&Label::new(Some("Units")));
            field_box.append(&units);
            let st = state.borrow().clone();
            if let Some(inspect) = st.inspect {
                for f in inspect.fields {
                    let name = f.name.clone();
                    let checked = st.selected.iter().any(|n| n == &name);
                    let caption = range_caption(&f, &st.unit_system);
                    let cb = CheckButton::with_label(&format!("{} ({})", f.label, caption));
                    cb.set_active(checked);
                    let state2 = state.clone();
                    cb.connect_toggled(move |btn| {
                        let mut st = state2.borrow_mut();
                        if btn.is_active() {
                            if !st.selected.iter().any(|n| n == &name) {
                                st.selected.push(name.clone());
                            }
                        } else {
                            st.selected.retain(|n| n != &name);
                        }
                    });
                    field_box.append(&cb);
                }
                if inspect.has_gps {
                    let map = CheckButton::with_label("Route map overlay");
                    map.set_active(st.include_map);
                    let state2 = state.clone();
                    map.connect_toggled(move |btn| {
                        state2.borrow_mut().include_map = btn.is_active();
                    });
                    field_box.append(&map);
                }
            }
        })
    };

    *rebuild_fields_slot.borrow_mut() = Some(rebuild_fields.clone());

    {
        let window = window.clone();
        let state = state.clone();
        let wizard = wizard.clone();
        let main = main.clone();
        let wizard_title = wizard_title.clone();
        let wizard_sub = wizard_sub.clone();
        let skip_btn = skip_btn.clone();
        let wizard_btn = wizard_btn.clone();
        let log_buffer = log_buffer.clone();
        let refresh_dock = refresh_dock.clone();
        let rebuild_fields = rebuild_fields.clone();
        let fit_sync_box = fit_sync_box.clone();
        let device_sync_box = device_sync_box.clone();
        let device_sync_title = device_sync_title.clone();
        let device_cards = device_cards.clone();
        let fit_label_entry = fit_label_entry.clone();
        let fit_ref_entry = fit_ref_entry.clone();
        let watch_entry = watch_entry.clone();
        let wall_entry = wall_entry.clone();
        let wizard_btn_click = wizard_btn.clone();
        wizard_btn_click.connect_clicked(move |_| {
            let step = state.borrow().step;
            match step {
                WizardStep::Fit => {
                    let dialog = FileDialog::new();
                    dialog.open(Some(&window), None::<&gtk::gio::Cancellable>, {
                        let state = state.clone();
                        let wizard_title = wizard_title.clone();
                        let wizard_sub = wizard_sub.clone();
                        let skip_btn = skip_btn.clone();
                        let wizard_btn = wizard_btn.clone();
                        let log_buffer = log_buffer.clone();
                        let fit_sync_box = fit_sync_box.clone();
                        let fit_label_entry = fit_label_entry.clone();
                        let fit_ref_entry = fit_ref_entry.clone();
                        let watch_entry = watch_entry.clone();
                        let wall_entry = wall_entry.clone();
                        move |res| {
                            if let Ok(file) = res {
                                let path = file.path().unwrap_or_default();
                                match run_inspect(&path) {
                                    Ok(payload) => {
                                        {
                                            let mut st = state.borrow_mut();
                                            st.selected = payload
                                                .fields
                                                .iter()
                                                .filter(|f| {
                                                    matches!(
                                                        f.name.as_str(),
                                                        "speed" | "heart_rate" | "grade"
                                                    )
                                                })
                                                .map(|f| f.name.clone())
                                                .collect();
                                            st.include_map = payload.has_gps;
                                            st.threshold_field = payload
                                                .fields
                                                .iter()
                                                .find(|f| f.name == "speed")
                                                .map(|f| f.name.clone())
                                                .or_else(|| {
                                                    payload.fields.first().map(|f| f.name.clone())
                                                })
                                                .unwrap_or_default();
                                            st.threshold_value = payload
                                                .fields
                                                .iter()
                                                .find(|f| f.name == st.threshold_field)
                                                .and_then(|f| f.max)
                                                .unwrap_or(0.0);
                                            st.fit_label = payload
                                                .fit_device
                                                .as_ref()
                                                .and_then(|d| d.label.clone())
                                                .unwrap_or_else(|| "FIT device".into());
                                            st.fit_reference =
                                                payload.session_start.clone().unwrap_or_default();
                                            st.watch_clock = st.fit_reference.clone();
                                            st.wall_clock.clear();
                                            st.fit = Some(path.clone());
                                            st.inspect = Some(payload);
                                            st.step = WizardStep::SyncFit;
                                        }
                                        append_log(
                                            &log_buffer,
                                            &state,
                                            &format!("Inspected {}\n", path.display()),
                                        );
                                        let st = state.borrow();
                                        fit_label_entry.set_text(&st.fit_label);
                                        fit_ref_entry.set_text(&st.fit_reference);
                                        watch_entry.set_text(&st.watch_clock);
                                        wall_entry.set_text("");
                                        drop(st);
                                        wizard_title.set_text("Sync FIT / GPS watch");
                                        wizard_sub.set_text(
                                            "Confirm the sync moment and what the watch showed. Optional: wall-clock.",
                                        );
                                        wizard_btn.set_label("Continue");
                                        skip_btn.set_visible(false);
                                        fit_sync_box.set_visible(true);
                                    }
                                    Err(e) => {
                                        append_log(&log_buffer, &state, &format!("Error: {e}\n"))
                                    }
                                }
                            }
                        }
                    });
                }
                WizardStep::SyncFit => {
                    {
                        let mut st = state.borrow_mut();
                        st.fit_label = fit_label_entry.text().to_string();
                        st.fit_reference = fit_ref_entry.text().to_string();
                        st.watch_clock = watch_entry.text().to_string();
                        st.wall_clock = wall_entry.text().to_string();
                        st.step = WizardStep::Videos;
                    }
                    fit_sync_box.set_visible(false);
                    wizard_title.set_text("Add video clips");
                    wizard_sub
                        .set_text("Select one or more videos that cover this activity.");
                    wizard_btn.set_label("Choose videos…");
                    skip_btn.set_visible(false);
                }
                WizardStep::Videos => {
                    let dialog = FileDialog::new();
                    dialog.open_multiple(Some(&window), None::<&gtk::gio::Cancellable>, {
                        let state = state.clone();
                        let wizard_title = wizard_title.clone();
                        let wizard_sub = wizard_sub.clone();
                        let skip_btn = skip_btn.clone();
                        let wizard_btn = wizard_btn.clone();
                        let device_sync_box = device_sync_box.clone();
                        let device_sync_title = device_sync_title.clone();
                        let device_cards = device_cards.clone();
                        let log_buffer = log_buffer.clone();
                        move |res| {
                            if let Ok(list) = res {
                                let paths = collect_files(&list);
                                if paths.is_empty() {
                                    return;
                                }
                                let fallback = state.borrow().fit_reference.clone();
                                let probed = run_probe_media(&paths, &fallback).unwrap_or_else(|e| {
                                    append_log(
                                        &log_buffer,
                                        &state,
                                        &format!("probe-media failed: {e}\n"),
                                    );
                                    Vec::new()
                                });
                                {
                                    let mut st = state.borrow_mut();
                                    apply_probe_groups(&mut st, probed, "video", &paths);
                                    st.step = WizardStep::SyncCameras;
                                }
                                let groups = state.borrow().camera_groups.clone();
                                fill_device_cards(&device_cards, &groups, true, &state);
                                device_sync_title.set_text("Cameras");
                                device_sync_box.set_visible(true);
                                wizard_title.set_text("Sync camera clocks");
                                wizard_sub.set_text(
                                    "Enter what each camera showed at the sync moment.",
                                );
                                wizard_btn.set_label("Continue");
                                skip_btn.set_visible(false);
                            }
                        }
                    });
                }
                WizardStep::SyncCameras => {
                    {
                        let mut st = state.borrow_mut();
                        apply_device_offsets(&st.clone(), &mut st.camera_groups);
                        st.step = WizardStep::Audio;
                    }
                    device_sync_box.set_visible(false);
                    wizard_title.set_text("Add standalone audio?");
                    wizard_sub.set_text("Optional. Skip if you will use camera audio.");
                    wizard_btn.set_label("Choose audio…");
                    skip_btn.set_visible(true);
                }
                WizardStep::Audio => {
                    let dialog = FileDialog::new();
                    dialog.open_multiple(Some(&window), None::<&gtk::gio::Cancellable>, {
                        let state = state.clone();
                        let wizard = wizard.clone();
                        let main = main.clone();
                        let refresh_dock = refresh_dock.clone();
                        let rebuild_fields = rebuild_fields.clone();
                        let wizard_title = wizard_title.clone();
                        let wizard_sub = wizard_sub.clone();
                        let skip_btn = skip_btn.clone();
                        let wizard_btn = wizard_btn.clone();
                        let device_sync_box = device_sync_box.clone();
                        let device_sync_title = device_sync_title.clone();
                        let device_cards = device_cards.clone();
                        let log_buffer = log_buffer.clone();
                        move |res| {
                            let paths = if let Ok(list) = res {
                                collect_files(&list)
                            } else {
                                Vec::new()
                            };
                            if paths.is_empty() {
                                state.borrow_mut().step = WizardStep::Ready;
                                wizard.set_visible(false);
                                main.set_visible(true);
                                rebuild_fields();
                                refresh_dock();
                                return;
                            }
                            let fallback = state.borrow().fit_reference.clone();
                            let probed = run_probe_media(&paths, &fallback).unwrap_or_else(|e| {
                                append_log(
                                    &log_buffer,
                                    &state,
                                    &format!("probe-media failed: {e}\n"),
                                );
                                Vec::new()
                            });
                            {
                                let mut st = state.borrow_mut();
                                apply_probe_groups(&mut st, probed, "audio", &paths);
                                st.step = WizardStep::SyncAudio;
                            }
                            let groups = state.borrow().audio_groups.clone();
                            fill_device_cards(&device_cards, &groups, false, &state);
                            device_sync_title.set_text("Audio recorders");
                            device_sync_box.set_visible(true);
                            wizard_title.set_text("Sync audio recorder clocks");
                            wizard_sub
                                .set_text("Enter what each audio recorder showed at the sync moment.");
                            wizard_btn.set_label("Continue");
                            skip_btn.set_visible(false);
                        }
                    });
                }
                WizardStep::SyncAudio => {
                    {
                        let mut st = state.borrow_mut();
                        apply_device_offsets(&st.clone(), &mut st.audio_groups);
                        st.step = WizardStep::Ready;
                    }
                    device_sync_box.set_visible(false);
                    wizard.set_visible(false);
                    main.set_visible(true);
                    rebuild_fields();
                    refresh_dock();
                }
                WizardStep::Ready => {}
            }
        });
    }

    {
        let state = state.clone();
        let wizard = wizard.clone();
        let main = main.clone();
        let refresh_dock = refresh_dock.clone();
        let rebuild_fields = rebuild_fields.clone();
        let device_sync_box = device_sync_box.clone();
        skip_btn.connect_clicked(move |_| {
            let mut st = state.borrow_mut();
            st.audio_groups.clear();
            st.step = WizardStep::Ready;
            drop(st);
            device_sync_box.set_visible(false);
            wizard.set_visible(false);
            main.set_visible(true);
            rebuild_fields();
            refresh_dock();
        });
    }

    {
        let state = state.clone();
        mode.connect_selected_notify(move |dd| {
            state.borrow_mut().mode = match dd.selected() {
                1 => "laps".into(),
                2 => "threshold".into(),
                _ => "videos".into(),
            };
        });
    }

    let run_compile = {
        let state = state.clone();
        let log_buffer = log_buffer.clone();
        Rc::new(move |dry: bool| {
            let st = state.borrow().clone();
            let Some(fit) = st.fit.clone() else {
                return;
            };
            if all_video_clips(&st).is_empty() {
                return;
            }
            let tmp = std::env::temp_dir();
            let select_path = tmp.join(format!("fitvid-select-{}.yaml", std::process::id()));
            let overlay_path = tmp.join(format!("fitvid-overlay-{}.yaml", std::process::id()));
            let sync_path = tmp.join(format!("fitvid-sync-{}.yaml", std::process::id()));
            let _ = std::fs::write(&select_path, select_yaml(&st));
            let _ = std::fs::write(&overlay_path, overlay_yaml(&st));
            let _ = std::fs::write(&sync_path, sync_yaml(&st));
            let mut cmd = Command::new(fitvid_bin());
            cmd.arg("compile")
                .arg("--fit")
                .arg(&fit)
                .arg("--select")
                .arg(&select_path)
                .arg("--overlay")
                .arg(&overlay_path)
                .arg("--sync-config")
                .arg(&sync_path)
                .arg("--out")
                .arg(&st.output)
                .arg("--pad-before")
                .arg(st.pad_before.to_string())
                .arg("--pad-after")
                .arg(st.pad_after.to_string())
                .arg("--json-events")
                .arg("--sync")
                .arg("none")
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            for v in all_video_clips(&st) {
                cmd.arg("--video").arg(&v.path);
                if !v.start.is_empty() {
                    cmd.arg("--video-start").arg(&v.start);
                }
                if v.duration > 0.0 {
                    cmd.arg("--video-duration").arg(v.duration.to_string());
                }
            }
            for a in all_audio_clips(&st) {
                cmd.arg("--audio").arg(&a.path);
                if !a.start.is_empty() {
                    cmd.arg("--audio-start").arg(&a.start);
                }
                if a.duration > 0.0 {
                    cmd.arg("--audio-duration").arg(a.duration.to_string());
                }
            }
            if dry {
                cmd.arg("--dry-run");
            }
            match cmd.spawn() {
                Ok(mut child) => {
                    if let Some(out) = child.stdout.take() {
                        for line in BufReader::new(out).lines().flatten() {
                            append_log(&log_buffer, &state, &line);
                        }
                    }
                    let _ = child.wait();
                }
                Err(e) => append_log(&log_buffer, &state, &format!("Error: {e}")),
            }
        })
    };

    {
        let run_compile = run_compile.clone();
        dry.connect_clicked(move |_| run_compile(true));
    }
    {
        let run_compile = run_compile.clone();
        compile.connect_clicked(move |_| run_compile(false));
    }

    {
        let window = window.clone();
        let state = state.clone();
        let refresh_dock = refresh_dock.clone();
        let log_buffer = log_buffer.clone();
        add_video.connect_clicked(move |_| {
            let dialog = FileDialog::new();
            dialog.open_multiple(Some(&window), None::<&gtk::gio::Cancellable>, {
                let state = state.clone();
                let refresh_dock = refresh_dock.clone();
                let log_buffer = log_buffer.clone();
                move |res| {
                    if let Ok(list) = res {
                        let added = collect_files(&list);
                        if added.is_empty() {
                            return;
                        }
                        let mut existing: Vec<PathBuf> = state
                            .borrow()
                            .camera_groups
                            .iter()
                            .flat_map(|g| g.clips.iter().map(|c| c.path.clone()))
                            .collect();
                        existing.extend(added);
                        let fallback = state.borrow().fit_reference.clone();
                        let probed = run_probe_media(&existing, &fallback).unwrap_or_else(|e| {
                            append_log(
                                &log_buffer,
                                &state,
                                &format!("probe-media failed: {e}\n"),
                            );
                            Vec::new()
                        });
                        {
                            let mut st = state.borrow_mut();
                            apply_probe_groups(&mut st, probed, "video", &existing);
                            if st.step == WizardStep::Ready {
                                let snap = st.clone();
                                apply_device_offsets(&snap, &mut st.camera_groups);
                            }
                        }
                    }
                    refresh_dock();
                }
            });
        });
    }
    {
        let window = window.clone();
        let state = state.clone();
        let refresh_dock = refresh_dock.clone();
        let log_buffer = log_buffer.clone();
        add_audio.connect_clicked(move |_| {
            let dialog = FileDialog::new();
            dialog.open_multiple(Some(&window), None::<&gtk::gio::Cancellable>, {
                let state = state.clone();
                let refresh_dock = refresh_dock.clone();
                let log_buffer = log_buffer.clone();
                move |res| {
                    if let Ok(list) = res {
                        let added = collect_files(&list);
                        if added.is_empty() {
                            return;
                        }
                        let mut existing: Vec<PathBuf> = state
                            .borrow()
                            .audio_groups
                            .iter()
                            .flat_map(|g| g.clips.iter().map(|c| c.path.clone()))
                            .collect();
                        existing.extend(added);
                        let fallback = state.borrow().fit_reference.clone();
                        let probed = run_probe_media(&existing, &fallback).unwrap_or_else(|e| {
                            append_log(
                                &log_buffer,
                                &state,
                                &format!("probe-media failed: {e}\n"),
                            );
                            Vec::new()
                        });
                        {
                            let mut st = state.borrow_mut();
                            apply_probe_groups(&mut st, probed, "audio", &existing);
                            if st.step == WizardStep::Ready {
                                let snap = st.clone();
                                apply_device_offsets(&snap, &mut st.audio_groups);
                            }
                        }
                    }
                    refresh_dock();
                }
            });
        });
    }

    window.present();
}

fn main() {
    let app = Application::builder()
        .application_id("com.fitvid.app")
        .build();
    app.connect_activate(build_ui);
    app.run();
}
