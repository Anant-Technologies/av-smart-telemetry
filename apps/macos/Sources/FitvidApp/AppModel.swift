import Foundation
import AppKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class AppModel: ObservableObject {
    enum WizardStep: Equatable {
        case pickFit
        case syncFit
        case pickVideos
        case syncCameras
        case pickAudio
        case syncAudio
        case ready
    }

    @Published var wizard: WizardStep = .pickFit
    @Published var fitURL: URL?
    @Published var inspect: InspectPayload?
    @Published var selectedFieldNames: Set<String> = []
    @Published var includeMap = false
    /// Overlay measurement system: fps (US customary, default) or metric.
    @Published var unitSystem: UnitSystem = .fps
    @Published var selectMode: SelectMode = .videos
    @Published var thresholdField: String?
    @Published var thresholdOp = ">"
    @Published var thresholdValue: Double = 0
    @Published var thresholdMinDuration: Double = 5
    @Published var padBefore: Double = 2
    @Published var padAfter: Double = 2
    @Published var outputURL: URL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Desktop/highlight.mp4")
    @Published var logText = ""
    @Published var isBusy = false
    @Published var errorMessage: String?

    // Sync (host-local ISO times from inspect / user clocks)
    @Published var fitLabel = "FIT device"
    @Published var fitReferenceISO = ""
    @Published var watchClockISO = ""
    @Published var wallClockISO = ""
    @Published var cameraGroups: [MediaDeviceGroup] = []
    @Published var audioGroups: [MediaDeviceGroup] = []

    private let cli = FitvidCLI()

    var videoClips: [MediaClipItem] { cameraGroups.flatMap(\.files) }
    var audioClips: [MediaClipItem] { audioGroups.flatMap(\.files) }

    var selectedFields: [InspectField] {
        guard let inspect else { return [] }
        return inspect.fields.filter { selectedFieldNames.contains($0.name) }
    }

    func appendLog(_ line: String) {
        logText += line + "\n"
    }

    func pickFit() {
        let panel = NSOpenPanel()
        if let fitType = UTType(filenameExtension: "fit") {
            panel.allowedContentTypes = [fitType]
        } else {
            panel.allowedContentTypes = []
            panel.allowsOtherFileTypes = true
        }
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Choose a FIT activity file"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        setFit(url)
    }

    func setFit(_ url: URL) {
        fitURL = url
        isBusy = true
        errorMessage = nil
        Task {
            do {
                let payload = try await Task.detached { [cli] in
                    try cli.inspect(fit: url)
                }.value
                inspect = payload
                selectedFieldNames = Set(
                    payload.fields
                        .filter { ["speed", "heart_rate", "grade"].contains($0.name) }
                        .map(\.name)
                )
                includeMap = payload.hasGps
                thresholdField = payload.fields.first(where: { $0.name == "speed" })?.name
                    ?? payload.fields.first?.name
                if let f = payload.fields.first(where: { $0.name == thresholdField }) {
                    thresholdValue = f.max ?? 0
                }
                fitLabel = payload.fitDevice?.label ?? "FIT device"
                fitReferenceISO = payload.sessionStart
                watchClockISO = payload.sessionStart
                wallClockISO = ""
                wizard = .syncFit
                appendLog("Inspected \(url.lastPathComponent): \(payload.fields.count) fields (times in local TZ)")
            } catch {
                errorMessage = error.localizedDescription
                appendLog("Inspect failed: \(error.localizedDescription)")
            }
            isBusy = false
        }
    }

    func confirmFitSync() {
        wizard = .pickVideos
    }

    func pickVideos() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.movie, .mpeg4Movie, .quickTimeMovie]
        panel.allowsMultipleSelection = true
        panel.message = "Add video clips"
        guard panel.runModal() == .OK, !panel.urls.isEmpty else { return }
        replaceVideos(with: panel.urls)
        probeCameras()
    }

    func addVideos() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.movie, .mpeg4Movie, .quickTimeMovie]
        panel.allowsMultipleSelection = true
        guard panel.runModal() == .OK else { return }
        let existing = Set(videoClips.map(\.url))
        let added = panel.urls.filter { !existing.contains($0) }
        guard !added.isEmpty else { return }
        replaceVideos(with: videoClips.map(\.url) + added)
        if wizard == .ready || wizard == .syncCameras {
            probeCameras(goNext: false)
        }
    }

    private func replaceVideos(with urls: [URL]) {
        // Temporary placeholders until probe fills start/duration
        cameraGroups = [
            MediaDeviceGroup(
                id: "camera-pending",
                label: "Camera",
                kind: "video",
                files: urls.map { MediaClipItem(url: $0, metadataStartISO: fitReferenceISO, duration: 0) },
                deviceClockISO: fitReferenceISO
            )
        ]
    }

    func removeVideoClip(_ clipID: UUID) {
        for i in cameraGroups.indices {
            cameraGroups[i].files.removeAll { $0.id == clipID }
        }
        cameraGroups.removeAll { $0.files.isEmpty }
        videoURLsSyncNote()
    }

    func removeAudioClip(_ clipID: UUID) {
        for i in audioGroups.indices {
            audioGroups[i].files.removeAll { $0.id == clipID }
        }
        audioGroups.removeAll { $0.files.isEmpty }
    }

    private func videoURLsSyncNote() {
        // no-op helper kept for readability at call sites
    }

    func probeCameras(goNext: Bool = true) {
        isBusy = true
        let paths = videoClips.map(\.url)
        let ref = fitReferenceISO
        Task {
            do {
                let devices = try await Task.detached { [cli] in
                    try cli.probeMedia(paths: paths)
                }.value
                cameraGroups = Self.groupsFromProbe(devices, kind: "video", fallbackClock: ref)
                if goNext {
                    wizard = .syncCameras
                } else {
                    applyDeviceOffsets(to: &cameraGroups)
                }
            } catch {
                cameraGroups = [
                    MediaDeviceGroup(
                        id: "camera-1",
                        label: "Camera",
                        kind: "video",
                        files: paths.map { MediaClipItem(url: $0, metadataStartISO: ref, duration: 0) },
                        deviceClockISO: ref
                    )
                ]
                appendLog("probe-media failed: \(error.localizedDescription)")
                if goNext {
                    wizard = .syncCameras
                } else {
                    applyDeviceOffsets(to: &cameraGroups)
                }
            }
            isBusy = false
        }
    }

    func confirmCameraSync() {
        applyDeviceOffsets(to: &cameraGroups)
        wizard = .pickAudio
    }

    func confirmAudioSync() {
        applyDeviceOffsets(to: &audioGroups)
        wizard = .ready
    }

    /// Bake FIT/device clock offsets into each clip's editable startISO.
    func applyDeviceOffsets(to groups: inout [MediaDeviceGroup]) {
        let spine = LocalTimeSync.spineISO(
            fitReference: fitReferenceISO,
            watchClock: watchClockISO.isEmpty ? fitReferenceISO : watchClockISO,
            wallClock: wallClockISO
        )
        for gi in groups.indices {
            let deviceClock = groups[gi].deviceClockISO.isEmpty
                ? spine
                : groups[gi].deviceClockISO
            for fi in groups[gi].files.indices {
                let meta = groups[gi].files[fi].metadataStartISO
                groups[gi].files[fi].startISO = LocalTimeSync.correctedStart(
                    metadataISO: meta,
                    spineISO: spine,
                    deviceClockISO: deviceClock
                )
            }
        }
    }

    func pickAudio(skip: Bool) {
        if skip {
            audioGroups = []
            wizard = .ready
            return
        }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.audio, .mp3, .wav]
        panel.allowsMultipleSelection = true
        panel.message = "Add standalone audio (optional)"
        if panel.runModal() == .OK {
            audioGroups = [
                MediaDeviceGroup(
                    id: "recorder-pending",
                    label: "Audio recorder",
                    kind: "audio",
                    files: panel.urls.map { MediaClipItem(url: $0, metadataStartISO: fitReferenceISO, duration: 0) },
                    deviceClockISO: fitReferenceISO
                )
            ]
        }
        if audioClips.isEmpty {
            audioGroups = []
            wizard = .ready
        } else {
            probeAudio()
        }
    }

    func addAudio() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.audio, .mp3, .wav]
        panel.allowsMultipleSelection = true
        guard panel.runModal() == .OK else { return }
        let existing = Set(audioClips.map(\.url))
        let added = panel.urls.filter { !existing.contains($0) }
        guard !added.isEmpty else { return }
        let all = audioClips.map(\.url) + added
        audioGroups = [
            MediaDeviceGroup(
                id: "recorder-pending",
                label: "Audio recorder",
                kind: "audio",
                files: all.map { MediaClipItem(url: $0, metadataStartISO: fitReferenceISO, duration: 0) },
                deviceClockISO: fitReferenceISO
            )
        ]
        probeAudio(goNext: false)
    }

    func probeAudio(goNext: Bool = true) {
        isBusy = true
        let paths = audioClips.map(\.url)
        let ref = fitReferenceISO
        Task {
            do {
                let devices = try await Task.detached { [cli] in
                    try cli.probeMedia(paths: paths)
                }.value
                audioGroups = Self.groupsFromProbe(devices, kind: "audio", fallbackClock: ref, idSuffix: "-audio")
                if goNext {
                    wizard = .syncAudio
                } else {
                    applyDeviceOffsets(to: &audioGroups)
                }
            } catch {
                audioGroups = [
                    MediaDeviceGroup(
                        id: "recorder-1",
                        label: "Audio recorder",
                        kind: "audio",
                        files: paths.map { MediaClipItem(url: $0, metadataStartISO: ref, duration: 0) },
                        deviceClockISO: ref
                    )
                ]
                if goNext {
                    wizard = .syncAudio
                } else {
                    applyDeviceOffsets(to: &audioGroups)
                }
            }
            isBusy = false
        }
    }

    func pickOutput() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.mpeg4Movie]
        panel.nameFieldStringValue = "highlight.mp4"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        outputURL = url
    }

    func runCompile(dryRun: Bool) {
        guard let fitURL, !videoClips.isEmpty else {
            errorMessage = "FIT and at least one video are required"
            return
        }
        isBusy = true
        errorMessage = nil
        let selectBody = ConfigBuilder.selectYAML(
            mode: selectMode,
            thresholdField: thresholdField,
            thresholdOp: thresholdOp,
            thresholdValue: thresholdValue,
            thresholdMinDuration: thresholdMinDuration
        )
        let overlayBody = ConfigBuilder.overlayYAML(
            selected: selectedFields,
            includeMap: includeMap && (inspect?.hasGps ?? false),
            unitSystem: unitSystem
        )
        let syncBody = ConfigBuilder.syncYAML(
            fitLabel: fitLabel,
            fitReferenceISO: fitReferenceISO,
            watchClockISO: watchClockISO.isEmpty ? fitReferenceISO : watchClockISO,
            wallClockISO: wallClockISO.isEmpty ? nil : wallClockISO,
            cameras: cameraGroups,
            recorders: audioGroups
        )
        let videos = videoClips
        let audios = audioClips
        let out = outputURL
        let padB = padBefore
        let padA = padAfter
        let overlayNeeded = !selectedFields.isEmpty || includeMap

        Task {
            do {
                let tmp = FileManager.default.temporaryDirectory
                let selectURL = tmp.appendingPathComponent("fitvid-select-\(UUID().uuidString).yaml")
                let overlayURL = tmp.appendingPathComponent("fitvid-overlay-\(UUID().uuidString).yaml")
                let syncURL = tmp.appendingPathComponent("fitvid-sync-\(UUID().uuidString).yaml")
                try selectBody.write(to: selectURL, atomically: true, encoding: .utf8)
                try overlayBody.write(to: overlayURL, atomically: true, encoding: .utf8)
                try syncBody.write(to: syncURL, atomically: true, encoding: .utf8)

                try await Task.detached { [cli] in
                    try cli.compile(
                        fit: fitURL,
                        videos: videos,
                        audios: audios,
                        selectYAML: selectURL,
                        overlayYAML: overlayNeeded ? overlayURL : nil,
                        syncYAML: syncURL,
                        out: out,
                        padBefore: padB,
                        padAfter: padA,
                        dryRun: dryRun
                    ) { line in
                        Task { @MainActor in
                            self.appendLog(line)
                        }
                    }
                }.value
                appendLog(dryRun ? "Dry run complete." : "Wrote \(out.path)")
            } catch {
                errorMessage = error.localizedDescription
                appendLog("Error: \(error.localizedDescription)")
            }
            isBusy = false
        }
    }

    private static func groupsFromProbe(
        _ devices: [[String: Any]],
        kind: String,
        fallbackClock: String,
        idSuffix: String = ""
    ) -> [MediaDeviceGroup] {
        devices.enumerated().map { i, d in
            var id = d["id"] as? String ?? "device-\(i)"
            if !idSuffix.isEmpty && !id.hasSuffix(idSuffix) {
                id += idSuffix
            }
            let label = d["label"] as? String ?? id
            let probes = d["probes"] as? [[String: Any]] ?? []
            let filePaths = d["files"] as? [String] ?? []
            let probeByPath = Dictionary(
                uniqueKeysWithValues: probes.compactMap { p -> (String, [String: Any])? in
                    guard let path = p["path"] as? String else { return nil }
                    return (path, p)
                }
            )
            let files: [MediaClipItem] = filePaths.map { path in
                let p = probeByPath[path]
                let start = (p?["start_time"] as? String) ?? fallbackClock
                let duration: Double
                if let d = p?["duration"] as? Double {
                    duration = d
                } else if let n = p?["duration"] as? NSNumber {
                    duration = n.doubleValue
                } else {
                    duration = 0
                }
                return MediaClipItem(
                    url: URL(fileURLWithPath: path),
                    metadataStartISO: start,
                    duration: duration
                )
            }
            return MediaDeviceGroup(
                id: id,
                label: label,
                kind: kind,
                files: files,
                deviceClockISO: fallbackClock
            )
        }
    }
}
