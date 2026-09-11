import SwiftUI
import UniformTypeIdentifiers
import AppKit

@main
struct FitvidApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("fitvid") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 960, minHeight: 640)
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            switch model.wizard {
            case .ready:
                MainWorkspaceView()
            default:
                WizardView()
            }
        }
        .alert("Error", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }
}

struct WizardView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 20) {
            Text("fitvid")
                .font(.largeTitle.bold())
            Text(stepTitle)
                .font(.title2)
            Text(stepSubtitle)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 480)

            switch model.wizard {
            case .pickFit:
                Button("Choose FIT file…") { model.pickFit() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            case .syncFit:
                FitSyncForm()
                Button("Continue") { model.confirmFitSync() }
                    .buttonStyle(.borderedProminent)
            case .pickVideos:
                Button("Choose videos…") { model.pickVideos() }
                    .buttonStyle(.borderedProminent)
            case .syncCameras:
                DeviceClockList(groups: $model.cameraGroups, title: "Cameras")
                Button("Continue") { model.confirmCameraSync() }
                    .buttonStyle(.borderedProminent)
            case .pickAudio:
                HStack {
                    Button("Choose audio…") { model.pickAudio(skip: false) }
                    Button("Skip") { model.pickAudio(skip: true) }
                        .buttonStyle(.borderedProminent)
                }
            case .syncAudio:
                DeviceClockList(groups: $model.audioGroups, title: "Audio recorders")
                Button("Continue") { model.confirmAudioSync() }
                    .buttonStyle(.borderedProminent)
            case .ready:
                EmptyView()
            }

            if model.isBusy {
                ProgressView("Working…")
            }
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var stepTitle: String {
        switch model.wizard {
        case .pickFit: return "Select activity"
        case .syncFit: return "Sync FIT / GPS watch"
        case .pickVideos: return "Add video clips"
        case .syncCameras: return "Sync camera clocks"
        case .pickAudio: return "Add standalone audio?"
        case .syncAudio: return "Sync audio recorder clocks"
        case .ready: return ""
        }
    }

    private var stepSubtitle: String {
        switch model.wizard {
        case .pickFit:
            return "Pick the .fit file from your GPS watch or bike computer."
        case .syncFit:
            return "Confirm the sync moment and what the watch showed. Optional: enter true wall-clock (phone) time."
        case .pickVideos:
            return "Select one or more videos that cover this activity."
        case .syncCameras:
            return "Videos are grouped by recording device. Enter what each camera showed at the sync moment."
        case .pickAudio:
            return "Optional lav-mic or recorder. Skip if you will use camera audio."
        case .syncAudio:
            return "Enter what each audio recorder showed at the sync moment."
        case .ready:
            return ""
        }
    }
}

struct FitSyncForm: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Form {
            TextField("Device label", text: $model.fitLabel)
            TextField("FIT reference (ISO)", text: $model.fitReferenceISO)
            TextField("Watch showed (ISO)", text: $model.watchClockISO)
            TextField("Wall clock / phone (optional ISO)", text: $model.wallClockISO)
        }
        .frame(maxWidth: 520)
    }
}

struct DeviceClockList: View {
    @Binding var groups: [MediaDeviceGroup]
    let title: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.headline)
            ForEach($groups) { $group in
                VStack(alignment: .leading, spacing: 6) {
                    TextField("Label", text: $group.label)
                    Text(group.files.map { $0.url.lastPathComponent }.joined(separator: ", "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextField("Device clock at sync moment (ISO)", text: $group.deviceClockISO)
                }
                .padding(8)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .frame(maxWidth: 560)
    }
}

struct MainWorkspaceView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 0) {
                FieldPickerView()
                    .frame(width: 280)
                    .background(Color(nsColor: .controlBackgroundColor))
                Divider()
                WorkAreaView()
            }
            .frame(maxHeight: .infinity)
            Divider()
            MediaDockView()
                .frame(minHeight: 260)
        }
    }
}

struct FieldPickerView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Telemetry")
                .font(.headline)
                .padding(.horizontal)
                .padding(.top)
            List {
                if let inspect = model.inspect {
                    ForEach(grouped(inspect.fields), id: \.0) { category, fields in
                        Section(category.capitalized) {
                            ForEach(fields) { field in
                                Toggle(isOn: Binding(
                                    get: { model.selectedFieldNames.contains(field.name) },
                                    set: { on in
                                        if on { model.selectedFieldNames.insert(field.name) }
                                        else { model.selectedFieldNames.remove(field.name) }
                                    }
                                )) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(field.label)
                                        Text(secondary(field))
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            if model.inspect?.hasGps == true {
                Toggle("Route map overlay", isOn: $model.includeMap)
                    .padding()
            }
        }
    }

    private func grouped(_ fields: [InspectField]) -> [(String, [InspectField])] {
        let order = ["common", "cycling", "running", "other"]
        let dict = Dictionary(grouping: fields, by: \.category)
        return order.compactMap { key in
            guard let vals = dict[key], !vals.isEmpty else { return nil }
            return (key, vals)
        }
    }

    private func secondary(_ f: InspectField) -> String {
        let unit = f.unit ?? ""
        let mn = f.min.map { String(format: "%.3g", $0) } ?? "-"
        let mx = f.max.map { String(format: "%.3g", $0) } ?? "-"
        return "\(unit)  \(mn) – \(mx)"
    }
}

struct WorkAreaView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Highlights").font(.headline)
            Picker("Mode", selection: $model.selectMode) {
                ForEach(SelectMode.allCases) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 420)

            if model.selectMode == .videos {
                Text("Concatenates every video in creation-time order. FIT overlay burns where timestamps overlap; gaps are OK.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if model.selectMode == .threshold {
                HStack {
                    Picker("Field", selection: Binding(
                        get: { model.thresholdField ?? "" },
                        set: { model.thresholdField = $0 }
                    )) {
                        ForEach(model.inspect?.fields ?? []) { f in
                            Text(f.label).tag(f.name)
                        }
                    }
                    TextField("Op", text: $model.thresholdOp).frame(width: 60)
                    TextField("Value", value: $model.thresholdValue, format: .number).frame(width: 100)
                }
            }

            HStack {
                Text("Pad before")
                Slider(value: $model.padBefore, in: 0...30)
                Text("\(Int(model.padBefore))s")
                Text("Pad after")
                Slider(value: $model.padAfter, in: 0...30)
                Text("\(Int(model.padAfter))s")
            }

            HStack {
                TextField("Output", text: Binding(
                    get: { model.outputURL.path },
                    set: { model.outputURL = URL(fileURLWithPath: $0) }
                ))
                Button("…") { model.pickOutput() }
            }

            HStack {
                Button("Dry run") { model.runCompile(dryRun: true) }
                    .disabled(model.isBusy)
                Button("Generate") { model.runCompile(dryRun: false) }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isBusy)
                if model.isBusy { ProgressView().controlSize(.small) }
            }

            Text("Log").font(.headline)
            ScrollView {
                Text(model.logText.isEmpty ? "Ready." : model.logText)
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
        .padding()
    }
}

struct MediaDockView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Videos by source — vertical scroll per group
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Videos by source").font(.caption.weight(.semibold))
                    Spacer()
                    Button("+") { model.addVideos() }
                }
                if model.cameraGroups.isEmpty {
                    Text("No videos")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else {
                    ScrollView(.vertical) {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach($model.cameraGroups) { $group in
                                SourceGroupCard(
                                    group: $group,
                                    systemImage: "film",
                                    onRemove: { model.removeVideoClip($0) }
                                )
                            }
                        }
                        .padding(.trailing, 4)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            Divider()

            // Audio by source
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Audio by source").font(.caption.weight(.semibold))
                    Spacer()
                    Button("+") { model.addAudio() }
                }
                if model.audioGroups.isEmpty {
                    Text("No audio")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else {
                    ScrollView(.vertical) {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach($model.audioGroups) { $group in
                                SourceGroupCard(
                                    group: $group,
                                    systemImage: "waveform",
                                    onRemove: { model.removeAudioClip($0) }
                                )
                            }
                        }
                        .padding(.trailing, 4)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("FIT").font(.caption.weight(.semibold))
                if let fit = model.fitURL {
                    MediaIcon(url: fit, systemImage: "point.topleft.down.curvedto.point.bottomright.up")
                }
                Text(model.fitLabel).font(.caption2)
                Text(model.fitReferenceISO)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .frame(maxWidth: 160, alignment: .leading)
            }
            .frame(width: 170, alignment: .topLeading)
        }
        .padding(10)
    }
}

struct SourceGroupCard: View {
    @Binding var group: MediaDeviceGroup
    let systemImage: String
    let onRemove: (UUID) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(group.label)
                .font(.caption.weight(.semibold))
            Text("Device clock: \(group.deviceClockISO)")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            ForEach($group.files) { $clip in
                MediaClipRow(
                    clip: $clip,
                    systemImage: systemImage,
                    onRemove: { onRemove(clip.id) }
                )
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.primary.opacity(0.12), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct MediaClipRow: View {
    @Binding var clip: MediaClipItem
    let systemImage: String
    let onRemove: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: systemImage)
                .font(.title3)
                .frame(width: 28, height: 28)
                .background(Color.accentColor.opacity(0.15))
                .clipShape(RoundedRectangle(cornerRadius: 5))
            VStack(alignment: .leading, spacing: 4) {
                Text(clip.url.lastPathComponent)
                    .font(.caption)
                    .lineLimit(1)
                    .help(clip.url.path)
                HStack(spacing: 6) {
                    Text("Start").font(.caption2).foregroundStyle(.secondary)
                    TextField("local ISO start", text: $clip.startISO)
                        .font(.system(.caption2, design: .monospaced))
                        .textFieldStyle(.roundedBorder)
                    Text("Dur").font(.caption2).foregroundStyle(.secondary)
                    TextField(
                        "s",
                        value: $clip.duration,
                        format: .number.precision(.fractionLength(1))
                    )
                    .font(.caption2)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 64)
                    Text("s").font(.caption2).foregroundStyle(.secondary)
                }
                if clip.metadataStartISO != clip.startISO {
                    Text("meta \(clip.metadataStartISO)")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }
            Button(action: onRemove) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.borderless)
            .help("Remove from generate")
        }
        .padding(6)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

struct MediaIcon: View {
    let url: URL
    let systemImage: String

    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: systemImage)
                .font(.title)
                .frame(width: 48, height: 40)
                .background(Color.accentColor.opacity(0.15))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            Text(url.lastPathComponent)
                .font(.caption2)
                .lineLimit(1)
                .frame(width: 72)
        }
        .help(url.path)
        .onTapGesture {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }
}
