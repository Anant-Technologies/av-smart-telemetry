import SwiftUI
import Charts
import QuickLookThumbnailing
import AppKit
import AVKit
import AVFoundation

// MARK: - Video thumbnail

struct VideoThumbnailView: View {
    let url: URL
    var size: CGSize = CGSize(width: 96, height: 54)

    @State private var image: NSImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.accentColor.opacity(0.12))
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: size.width, height: size.height)
                    .clipped()
            } else if failed {
                Image(systemName: "film")
                    .foregroundStyle(.secondary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .frame(width: size.width, height: size.height)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .task(id: url) { await load() }
    }

    @MainActor
    private func load() async {
        image = nil
        failed = false
        let request = QLThumbnailGenerator.Request(
            fileAt: url,
            size: CGSize(width: size.width * 2, height: size.height * 2),
            scale: NSScreen.main?.backingScaleFactor ?? 2,
            representationTypes: .thumbnail
        )
        do {
            let rep = try await QLThumbnailGenerator.shared.generateBestRepresentation(for: request)
            image = rep.nsImage
        } catch {
            if let fallback = await Self.ffmpegFallback(url: url, width: Int(size.width * 2)) {
                image = fallback
            } else {
                failed = true
            }
        }
    }

    private static func ffmpegFallback(url: URL, width: Int) async -> NSImage? {
        await Task.detached(priority: .utility) {
            let tmp = FileManager.default.temporaryDirectory
                .appendingPathComponent("fitvid-thumb-\(url.lastPathComponent.hashValue).jpg")
            let cli = FitvidCLI()
            do {
                _ = try cli.run(arguments: [
                    "thumbnail", url.path, "--out", tmp.path, "--width", "\(width)",
                ])
                return NSImage(contentsOf: tmp)
            } catch {
                return nil
            }
        }.value
    }
}

// MARK: - Preview player (left of FIT scrubber)

/// AppKit-backed player — SwiftUI `VideoPlayer` crashes on some macOS builds
/// during `_AVKit_SwiftUI` metadata init.
struct AVPlayerNSView: NSViewRepresentable {
    let player: AVPlayer?

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .inline
        view.videoGravity = .resizeAspect
        view.player = player
        return view
    }

    func updateNSView(_ nsView: AVPlayerView, context: Context) {
        if nsView.player !== player {
            nsView.player = player
        }
    }
}

struct MediaPreviewPane: View {
    @EnvironmentObject var model: AppModel
    @State private var player: AVPlayer?
    @State private var pollTimer: Timer?
    @State private var securityScopedURL: URL?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Preview").font(.headline)
                Spacer()
                if model.previewMedia != nil {
                    Button("Close") {
                        tearDownPlayer()
                        model.clearPreview()
                    }
                    .controlSize(.small)
                }
            }

            ZStack {
                Color.black.opacity(0.85)
                AVPlayerNSView(player: player)
                if player == nil {
                    Text("Double-click a video or audio clip to preview")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding()
                        .allowsHitTesting(false)
                } else if model.previewMedia?.isAudio == true {
                    VStack(spacing: 8) {
                        Image(systemName: "waveform")
                            .font(.system(size: 36))
                            .foregroundStyle(.white.opacity(0.85))
                        Text(model.previewMedia?.url.lastPathComponent ?? "Audio")
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.7))
                            .lineLimit(2)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }
                    .allowsHitTesting(false)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if let name = model.previewMedia?.url.lastPathComponent {
                Text(name)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .onChange(of: model.previewMedia) { _, media in
            load(media)
        }
        .onChange(of: model.scrubDate) { _, date in
            // Only seek while the user drags AND preview is actively playing.
            // When paused/finished, scrubber is independent of the video pane.
            guard model.userIsScrubbing else { return }
            guard player?.timeControlStatus == .playing else { return }
            seekPlayer(to: date)
        }
        .onDisappear { tearDownPlayer() }
    }

    private func load(_ media: PreviewMedia?) {
        tearDownPlayer()
        guard let media else { return }
        if media.url.startAccessingSecurityScopedResource() {
            securityScopedURL = media.url
        }
        let item = AVPlayerItem(url: media.url)
        let p = AVPlayer(playerItem: item)
        player = p
        p.play()

        // Poll on the main run loop — more reliable than CMTime observers + Task hops.
        let timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { _ in
            Task { @MainActor in
                guard let player = self.player else { return }
                // Only drive the scrubber while media is playing. When paused or
                // finished, leave the scrubber where the user put it.
                guard player.timeControlStatus == .playing else { return }
                let seconds = CMTimeGetSeconds(player.currentTime())
                guard seconds.isFinite, seconds >= 0 else { return }
                var duration = CMTimeGetSeconds(player.currentItem?.duration ?? .invalid)
                if !duration.isFinite || duration <= 0 {
                    duration = max(seconds, 1)
                }
                let wall: Date
                if let start = LocalTimeSync.parse(media.startISO) {
                    wall = start.addingTimeInterval(seconds)
                } else {
                    wall = Date().addingTimeInterval(seconds)
                }
                model.updateScrubFromPlayer(
                    wall: wall,
                    videoSeconds: seconds,
                    videoDuration: duration
                )
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
    }

    private func seekPlayer(to date: Date?) {
        guard let date,
              let media = model.previewMedia,
              let start = LocalTimeSync.parse(media.startISO),
              let player
        else { return }
        let offset = date.timeIntervalSince(start)
        guard offset >= -0.05 else { return }
        let clamped = max(0, offset)
        let current = CMTimeGetSeconds(player.currentTime())
        if abs(current - clamped) < 0.2 { return }
        player.seek(
            to: CMTime(seconds: clamped, preferredTimescale: 600),
            toleranceBefore: .zero,
            toleranceAfter: .zero
        )
    }

    private func tearDownPlayer() {
        pollTimer?.invalidate()
        pollTimer = nil
        player?.pause()
        player?.replaceCurrentItem(with: nil)
        player = nil
        if let url = securityScopedURL {
            url.stopAccessingSecurityScopedResource()
            securityScopedURL = nil
        }
    }
}

// MARK: - Combined FIT telemetry chart + scrubber

private struct ChartSample: Identifiable {
    let id: String
    let date: Date
    let series: String
    let raw: Double
    /// 0…1 within that series (so different units share one Y axis)
    let normalized: Double
}

struct FitTelemetryGraphsView: View {
    @EnvironmentObject var model: AppModel

    private let palette: [Color] = [
        .orange, .cyan, .green, .pink, .yellow, .purple, .mint, .indigo, .red, .blue,
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("FIT telemetry").font(.headline)
                Spacer()
                if let sport = model.inspect?.sport {
                    Text(sport).font(.caption).foregroundStyle(.secondary)
                }
                if model.isLoadingSeries {
                    ProgressView().controlSize(.mini)
                }
            }

            if visibleFields.isEmpty {
                Text(model.fitURL == nil
                     ? "Load a FIT file to see charts."
                     : "Select telemetry fields to plot.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120, alignment: .center)
            } else {
                Chart {
                    ForEach(chartSamples) { sample in
                        LineMark(
                            x: .value("Time", sample.date),
                            y: .value("Value", sample.normalized)
                        )
                        .foregroundStyle(by: .value("Series", sample.series))
                        .interpolationMethod(.linear)
                        .lineStyle(StrokeStyle(lineWidth: 1.75))
                    }
                }
                .chartForegroundStyleScale(domain: legendNames, range: colors(for: legendNames))
                .chartLegend(position: .bottom, alignment: .leading, spacing: 8)
                .chartYAxis {
                    AxisMarks(position: .leading, values: [0, 0.5, 1]) { val in
                        AxisGridLine()
                        AxisValueLabel {
                            if let d = val.as(Double.self) {
                                Text(d == 0 ? "min" : d == 1 ? "max" : "")
                                    .font(.caption2)
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 6)) { _ in
                        AxisGridLine()
                        AxisValueLabel(format: .dateTime.hour().minute())
                    }
                }
                .frame(minHeight: 200)
                .padding(8)
                // Overlay scrubber line — drag horizontally to scrub / seek preview.
                .overlay(alignment: .topLeading) {
                    GeometryReader { geo in
                        let x = geo.size.width * model.scrubProgress
                        Path { path in
                            path.move(to: CGPoint(x: x, y: 0))
                            path.addLine(to: CGPoint(x: x, y: geo.size.height))
                        }
                        .stroke(style: StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                        .foregroundStyle(Color.primary.opacity(0.7))

                        if let scrubDate = model.scrubDate {
                            Text(scrubDate, format: .dateTime.hour().minute().second())
                                .font(.system(.caption2, design: .monospaced))
                                .padding(.horizontal, 4)
                                .padding(.vertical, 2)
                                .background(.ultraThinMaterial, in: Capsule())
                                .position(x: min(max(x, 40), geo.size.width - 40), y: 14)
                        }

                        Color.clear
                            .contentShape(Rectangle())
                            .gesture(
                                DragGesture(minimumDistance: 0)
                                    .onChanged { value in
                                        let w = max(geo.size.width, 1)
                                        model.userScrub(toProgress: value.location.x / w)
                                    }
                                    .onEnded { _ in
                                        model.endUserScrub()
                                    }
                            )
                    }
                }
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .animation(.linear(duration: 0.05), value: model.scrubProgress)

                Text(model.previewMedia == nil
                     ? "Drag on the chart to move the scrubber."
                     : "Drag on the chart to scrub; playback drives the line while playing.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)

                scrubReadout
            }
        }
        .onChange(of: visibleFields.map(\.name)) { _, _ in
            if model.previewMedia != nil { return }
            if model.scrubDate == nil, let start = domainStart {
                model.scrubDate = start
                model.scrubProgress = 0
            }
        }
    }

    private var visibleFields: [SeriesField] {
        let selected = model.selectedFieldNames
        let all = model.seriesFields
        if selected.isEmpty { return [] }
        return all.filter { selected.contains($0.name) }
    }

    private var legendNames: [String] {
        visibleFields.map { field in
            field.unit.isEmpty ? field.label : "\(field.label) (\(field.unit))"
        }
    }

    private var domainStart: Date? {
        visibleFields.flatMap(\.points).map(\.date).min()
    }

    private var domainEnd: Date? {
        visibleFields.flatMap(\.points).map(\.date).max()
    }

    private var chartSamples: [ChartSample] {
        visibleFields.flatMap { field -> [ChartSample] in
            let vals = field.points.map(\.v)
            let minV = vals.min() ?? 0
            let maxV = vals.max() ?? 1
            let span = max(maxV - minV, 1e-9)
            let series = field.unit.isEmpty ? field.label : "\(field.label) (\(field.unit))"
            return field.points.map { pt in
                ChartSample(
                    id: "\(field.name)-\(pt.t)",
                    date: pt.date,
                    series: series,
                    raw: pt.v,
                    normalized: (pt.v - minV) / span
                )
            }
        }
    }

    private func colors(for names: [String]) -> [Color] {
        names.enumerated().map { palette[$0.offset % palette.count] }
    }

    @ViewBuilder
    private var scrubReadout: some View {
        let t = model.scrubDate ?? domainStart
        VStack(alignment: .leading, spacing: 6) {
            Text("Values at scrubber")
                .font(.caption.weight(.semibold))
            if let t {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 140), spacing: 8)],
                    alignment: .leading,
                    spacing: 6
                ) {
                    ForEach(Array(visibleFields.enumerated()), id: \.element.id) { idx, field in
                        let value = nearestValue(in: field, to: t)
                        HStack(spacing: 6) {
                            Circle()
                                .fill(palette[idx % palette.count])
                                .frame(width: 8, height: 8)
                            Text(field.label)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer(minLength: 4)
                            Text(formatValue(value, unit: field.unit))
                                .font(.system(.caption, design: .monospaced))
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color(nsColor: .windowBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                }
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func nearestValue(in field: SeriesField, to date: Date) -> Double? {
        field.points.min(by: {
            abs($0.date.timeIntervalSince(date)) < abs($1.date.timeIntervalSince(date))
        })?.v
    }

    private func formatValue(_ value: Double?, unit: String) -> String {
        guard let value else { return "—" }
        let num: String
        if abs(value) >= 100 { num = String(format: "%.0f", value) }
        else if abs(value) >= 10 { num = String(format: "%.1f", value) }
        else { num = String(format: "%.2f", value) }
        return unit.isEmpty ? num : "\(num) \(unit)"
    }
}
