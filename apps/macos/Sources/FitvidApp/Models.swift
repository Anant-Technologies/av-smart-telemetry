import Foundation

struct InspectField: Identifiable, Codable, Hashable {
    var id: String { name }
    let name: String
    let label: String
    let category: String
    let count: Int
    let min: Double?
    let max: Double?
    let unit: String?
}

struct FitDeviceInfo: Codable, Hashable {
    let label: String?
    let manufacturer: String?
    let product: AnyCodableValue?
    let serialNumber: AnyCodableValue?

    enum CodingKeys: String, CodingKey {
        case label, manufacturer, product
        case serialNumber = "serial_number"
    }
}

/// Minimal flexible JSON value for product/serial which may be int or string.
enum AnyCodableValue: Codable, Hashable {
    case string(String)
    case int(Int)
    case double(Double)
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let i = try? c.decode(Int.self) { self = .int(i); return }
        if let d = try? c.decode(Double.self) { self = .double(d); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        self = .null
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .int(let i): try c.encode(i)
        case .double(let d): try c.encode(d)
        case .null: try c.encodeNil()
        }
    }

    var stringValue: String {
        switch self {
        case .string(let s): return s
        case .int(let i): return String(i)
        case .double(let d): return String(d)
        case .null: return ""
        }
    }
}

struct LapTime: Codable, Hashable {
    let index: Int
    let startTime: String
    let endTime: String
    enum CodingKeys: String, CodingKey {
        case index
        case startTime = "start_time"
        case endTime = "end_time"
    }
}

// MARK: - FIT series (charts)

struct SeriesPoint: Identifiable, Codable, Hashable {
    var id: String { t }
    let t: String
    let v: Double

    var date: Date {
        LocalTimeSync.parse(t) ?? Date()
    }
}

struct SeriesField: Identifiable, Codable, Hashable {
    var id: String { name }
    let name: String
    let label: String
    let unit: String
    let points: [SeriesPoint]
}

struct SeriesPayload: Codable {
    let sessionStart: String?
    let sessionEnd: String?
    let unitSystem: String?
    let fields: [SeriesField]
    let laps: [LapTime]?

    enum CodingKeys: String, CodingKey {
        case sessionStart = "session_start"
        case sessionEnd = "session_end"
        case unitSystem = "unit_system"
        case fields, laps
    }
}

struct InspectPayload: Codable {
    let sport: String
    let sessionIndex: Int?
    let sessionCount: Int?
    let sessionStart: String
    let sessionEnd: String
    let recordCount: Int
    let laps: Int
    let hasGps: Bool
    let fields: [InspectField]
    let fitDevice: FitDeviceInfo?
    let lapTimes: [LapTime]?

    enum CodingKeys: String, CodingKey {
        case sport
        case sessionIndex = "session_index"
        case sessionCount = "session_count"
        case sessionStart = "session_start"
        case sessionEnd = "session_end"
        case recordCount = "record_count"
        case laps
        case hasGps = "has_gps"
        case fields
        case fitDevice = "fit_device"
        case lapTimes = "lap_times"
    }
}

struct MediaClipItem: Identifiable, Hashable {
    let id: UUID
    var url: URL
    /// Creation-time from probe (before device↔FIT clock offset).
    var metadataStartISO: String
    /// Start after FIT/device offset; user-editable absolute local time.
    var startISO: String
    var duration: Double

    init(
        id: UUID = UUID(),
        url: URL,
        metadataStartISO: String,
        startISO: String? = nil,
        duration: Double
    ) {
        self.id = id
        self.url = url
        self.metadataStartISO = metadataStartISO
        self.startISO = startISO ?? metadataStartISO
        self.duration = duration
    }
}

/// Clip shown in the work-area preview player (left of FIT scrubber).
struct PreviewMedia: Equatable, Identifiable {
    var id: URL { url }
    let url: URL
    let startISO: String
    let isAudio: Bool
}

struct MediaDeviceGroup: Identifiable, Hashable {
    let id: String
    var label: String
    var kind: String // video | audio
    var files: [MediaClipItem]
    var deviceClockISO: String
}

enum LocalTimeSync {
    private static let isoWithFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parse(_ s: String) -> Date? {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return nil }
        if let d = isoWithFrac.date(from: t) { return d }
        if let d = iso.date(from: t) { return d }
        // Fallback: treat as local wall without offset
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.timeZone = .current
        df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        if let d = df.date(from: String(t.prefix(19))) { return d }
        df.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return df.date(from: String(t.prefix(19)))
    }

    static func format(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        f.timeZone = .current
        return f.string(from: date)
    }

    /// Spine time for media offsets: wall if set, else FIT reference (local).
    static func spineISO(
        fitReference: String,
        watchClock: String,
        wallClock: String
    ) -> String {
        let wall = wallClock.trimmingCharacters(in: .whitespacesAndNewlines)
        if !wall.isEmpty { return wall }
        // If watch differs from reference, shift reference by (watch - ref)
        guard let ref = parse(fitReference) else { return fitReference }
        let watch = parse(watchClock.isEmpty ? fitReference : watchClock) ?? ref
        let delta = watch.timeIntervalSince(ref)
        return format(ref.addingTimeInterval(delta))
    }

    /// offset = spine - deviceClock; corrected = metadata + offset
    static func correctedStart(metadataISO: String, spineISO: String, deviceClockISO: String) -> String {
        guard let meta = parse(metadataISO),
              let spine = parse(spineISO),
              let device = parse(deviceClockISO.isEmpty ? spineISO : deviceClockISO)
        else { return metadataISO }
        let offset = spine.timeIntervalSince(device)
        return format(meta.addingTimeInterval(offset))
    }
}

enum UnitSystem: String, CaseIterable, Identifiable {
    case fps = "FPS"
    case metric = "Metric"
    var id: String { rawValue }
    /// Value written into overlay YAML (`fps` | `metric`).
    var yamlValue: String {
        switch self {
        case .fps: return "fps"
        case .metric: return "metric"
        }
    }
}

enum UnitDisplay {
    /// Convert FIT SI min/max into the active measurement system for picker captions.
    static func rangeCaption(field: InspectField, system: UnitSystem) -> String {
        let unit = displayUnit(for: field.name, system: system, fallback: field.unit)
        let mn = field.min.map { format($0, field: field.name, system: system) } ?? "-"
        let mx = field.max.map { format($0, field: field.name, system: system) } ?? "-"
        if unit.isEmpty { return "\(mn) – \(mx)" }
        return "\(unit)  \(mn) – \(mx)"
    }

    static func displayUnit(for name: String, system: UnitSystem, fallback: String?) -> String {
        switch (system, name) {
        case (.fps, "speed"): return "mph"
        case (.fps, "altitude"): return "ft"
        case (.fps, "distance"): return "mi"
        case (.fps, "temperature"), (.fps, "core_temperature"): return "°F"
        case (.fps, "vertical_oscillation"), (.fps, "step_length"): return "in"
        case (.metric, "speed"): return "km/h"
        case (.metric, "altitude"), (.metric, "distance"): return "m"
        case (.metric, "temperature"), (.metric, "core_temperature"): return "°C"
        default: return fallback ?? ""
        }
    }

    static func convertSI(_ raw: Double, field: String, system: UnitSystem) -> Double {
        if system == .fps {
            switch field {
            case "speed": return raw * 2.236936
            case "altitude": return raw * 3.28084
            case "distance": return raw / 1609.344
            case "temperature", "core_temperature": return raw * 9.0 / 5.0 + 32.0
            case "vertical_oscillation", "step_length": return raw / 25.4
            default: return raw
            }
        }
        if field == "speed" { return raw * 3.6 } // m/s → km/h
        return raw
    }

    private static func format(_ raw: Double, field: String, system: UnitSystem) -> String {
        let v = convertSI(raw, field: field, system: system)
        if abs(v) >= 100 { return String(format: "%.0f", v) }
        if abs(v) >= 10 { return String(format: "%.1f", v) }
        return String(format: "%.3g", v)
    }
}

enum FieldDefaults {
    static let formatsMetric: [String: String] = [
        "speed": "{value:.1f} km/h",
        "heart_rate": "{value:.0f} bpm",
        "grade": "{value:.1f}%",
        "power": "{value:.0f} W",
        "cadence": "{value:.0f}",
        "altitude": "{value:.0f} m",
        "distance": "{value:.0f} m",
        "temperature": "{value:.1f} °C",
        "core_temperature": "{value:.1f} °C",
    ]
    static let formatsFPS: [String: String] = [
        "speed": "{value:.1f} mph",
        "heart_rate": "{value:.0f} bpm",
        "grade": "{value:.1f}%",
        "power": "{value:.0f} W",
        "cadence": "{value:.0f}",
        "altitude": "{value:.0f} ft",
        "distance": "{value:.2f} mi",
        "temperature": "{value:.1f} °F",
        "core_temperature": "{value:.1f} °F",
    ]

    static func format(for name: String, system: UnitSystem) -> String {
        let table = system == .fps ? formatsFPS : formatsMetric
        return table[name] ?? "{value}"
    }

    static func position(at index: Int) -> (Double, Double) {
        let y = max(0.04, 0.88 - Double(index) * 0.065)
        return (0.02, y)
    }
}

enum ConfigBuilder {
    static func overlayYAML(
        selected: [InspectField],
        includeMap: Bool,
        unitSystem: UnitSystem = .fps
    ) -> String {
        var lines: [String] = [
            "overlay:",
            "  unit_system: \(unitSystem.yamlValue)",
            "  text:",
        ]
        if selected.isEmpty {
            lines.append("    []")
        } else {
            for (i, field) in selected.enumerated() {
                let fmt = FieldDefaults.format(for: field.name, system: unitSystem)
                let (x, y) = FieldDefaults.position(at: i)
                lines.append("    - field: \(field.name)")
                lines.append("      format: \"\(fmt)\"")
                lines.append("      label: \"\(field.label)\"")
                lines.append("      unit_system: \(unitSystem.yamlValue)")
                lines.append("      position: [\(String(format: "%.3f", x)), \(String(format: "%.3f", y))]")
            }
        }
        if includeMap {
            lines.append("  map:")
            lines.append("    style: route-only")
            lines.append("    anchor: bottom-right")
            lines.append("    width_px: 280")
            lines.append("    height_px: 280")
        }
        return lines.joined(separator: "\n") + "\n"
    }

    static func selectYAML(
        mode: SelectMode,
        thresholdField: String?,
        thresholdOp: String,
        thresholdValue: Double,
        thresholdMinDuration: Double
    ) -> String {
        switch mode {
        case .videos:
            return "select:\n  - type: videos\n"
        case .laps:
            return "select:\n  - type: laps\n"
        case .threshold:
            let field = thresholdField ?? "speed"
            return """
            select:
              - type: threshold
                field: \(field)
                op: "\(thresholdOp)"
                value: \(thresholdValue)
                min_duration: \(thresholdMinDuration)
            """
        case .manual:
            return "select:\n  - type: laps\n"
        }
    }

    static func syncYAML(
        fitLabel: String,
        fitReferenceISO: String,
        watchClockISO: String,
        wallClockISO: String?,
        cameras: [MediaDeviceGroup],
        recorders: [MediaDeviceGroup]
    ) -> String {
        func yamlSafe(_ s: String) -> String {
            // Strip control chars (SwiftUI TextField can insert U+000B on Tab)
            let cleaned = String(s.unicodeScalars.filter { sc in
                sc == "\n" || sc == "\r" || sc == "\t" || sc.value >= 0x20
            }.map { Character($0) })
            return cleaned
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(of: "\"", with: "\\\"")
        }
        var lines: [String] = [
            "sync:",
            "  fit_generator:",
            "    label: \"\(yamlSafe(fitLabel))\"",
            "    fit_reference: \"\(yamlSafe(fitReferenceISO))\"",
            "    watch_clock: \"\(yamlSafe(watchClockISO))\"",
        ]
        if let wall = wallClockISO, !wall.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            lines.append("    wall_clock: \"\(yamlSafe(wall))\"")
        } else {
            lines.append("    wall_clock: null")
        }
        lines.append("  devices:")
        let all = cameras + recorders
        if all.isEmpty {
            lines.append("    []")
        } else {
            for dev in all {
                lines.append("    - id: \(yamlSafe(dev.id))")
                lines.append("      kind: \(yamlSafe(dev.kind))")
                lines.append("      label: \"\(yamlSafe(dev.label))\"")
                // Starts already corrected in the UI via --video-start / --audio-start
                lines.append("      offset_seconds: 0")
                lines.append("      files:")
                for f in dev.files {
                    lines.append("        - \(yamlSafe(f.url.path))")
                }
            }
        }
        return lines.joined(separator: "\n") + "\n"
    }
}

enum SelectMode: String, CaseIterable, Identifiable {
    case videos = "All videos"
    case laps = "Laps"
    case threshold = "Threshold"
    case manual = "Manual"
    var id: String { rawValue }
}
