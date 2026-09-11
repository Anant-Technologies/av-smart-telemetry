import Foundation

enum FitvidCLIError: Error, LocalizedError {
    case binaryMissing(String)
    case failed(String)
    case badJSON(String)

    var errorDescription: String? {
        switch self {
        case .binaryMissing(let p): return "fitvid binary not found at \(p)"
        case .failed(let m): return m
        case .badJSON(let m): return "Invalid JSON: \(m)"
        }
    }
}

final class FitvidCLI {
    let binaryURL: URL

    init(binaryURL: URL? = nil) {
        if let binaryURL {
            self.binaryURL = binaryURL
        } else {
            self.binaryURL = Self.resolveBinary()
        }
    }

    static func resolveBinary() -> URL {
        if let res = Bundle.main.resourceURL?
            .appendingPathComponent("fitvid")
            .appendingPathComponent("fitvid"),
           FileManager.default.isExecutableFile(atPath: res.path) {
            return res
        }
        // Dev fallback: PATH / venv
        let candidates = [
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("anant/repos/av-smart-telemetry/.venv/bin/fitvid"),
            URL(fileURLWithPath: "/usr/local/bin/fitvid"),
            URL(fileURLWithPath: "/opt/homebrew/bin/fitvid"),
        ]
        for c in candidates where FileManager.default.isExecutableFile(atPath: c.path) {
            return c
        }
        // which fitvid via /usr/bin/env
        return URL(fileURLWithPath: "/usr/bin/env")
    }

    @discardableResult
    func run(
        arguments: [String],
        onStdoutLine: ((String) -> Void)? = nil
    ) throws -> (exitCode: Int32, stdout: String, stderr: String) {
        let process = Process()
        var args = arguments
        if binaryURL.lastPathComponent == "env" {
            process.executableURL = binaryURL
            args = ["fitvid"] + arguments
        } else {
            process.executableURL = binaryURL
        }
        process.arguments = args

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        try process.run()

        var stdoutData = Data()
        var buffer = Data()
        let outHandle = outPipe.fileHandleForReading
        while true {
            let chunk = outHandle.availableData
            if chunk.isEmpty { break }
            stdoutData.append(chunk)
            buffer.append(chunk)
            while let range = buffer.range(of: Data([0x0A])) {
                let lineData = buffer.subdata(in: buffer.startIndex..<range.lowerBound)
                buffer.removeSubrange(buffer.startIndex...range.lowerBound)
                if let line = String(data: lineData, encoding: .utf8) {
                    onStdoutLine?(line)
                }
            }
        }
        process.waitUntilExit()
        let stderr = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let stdout = String(data: stdoutData, encoding: .utf8) ?? ""
        return (process.terminationStatus, stdout, stderr)
    }

    func inspect(fit: URL) throws -> InspectPayload {
        let result = try run(arguments: ["inspect", fit.path, "--format", "json"])
        guard result.exitCode == 0 else {
            throw FitvidCLIError.failed(result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        let data = Data(result.stdout.utf8)
        do {
            return try JSONDecoder().decode(InspectPayload.self, from: data)
        } catch {
            throw FitvidCLIError.badJSON(error.localizedDescription + "\n" + result.stdout)
        }
    }

    func probeMedia(paths: [URL]) throws -> [[String: Any]] {
        var args = ["probe-media", "--group"]
        args.append(contentsOf: paths.map(\.path))
        let result = try run(arguments: args)
        guard result.exitCode == 0 else {
            throw FitvidCLIError.failed(result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        guard let data = result.stdout.data(using: .utf8),
              let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let devices = obj["devices"] as? [[String: Any]] else {
            throw FitvidCLIError.badJSON(result.stdout)
        }
        return devices
    }

    func series(fit: URL, unitSystem: String = "fps") throws -> SeriesPayload {
        let result = try run(arguments: [
            "series", fit.path,
            "--unit-system", unitSystem,
            "--max-points", "600",
        ])
        guard result.exitCode == 0 else {
            throw FitvidCLIError.failed(result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        let data = Data(result.stdout.utf8)
        do {
            return try JSONDecoder().decode(SeriesPayload.self, from: data)
        } catch {
            throw FitvidCLIError.badJSON(error.localizedDescription + "\n" + result.stdout)
        }
    }

    func compile(
        fit: URL,
        videos: [MediaClipItem],
        audios: [MediaClipItem],
        selectYAML: URL,
        overlayYAML: URL?,
        syncYAML: URL?,
        out: URL,
        padBefore: Double,
        padAfter: Double,
        dryRun: Bool,
        onEvent: @escaping (String) -> Void
    ) throws {
        var args = [
            "compile",
            "--fit", fit.path,
            "--select", selectYAML.path,
            "--out", out.path,
            "--pad-before", String(padBefore),
            "--pad-after", String(padAfter),
            "--json-events",
            "--sync", "none",
        ]
        for v in videos {
            args += ["--video", v.url.path]
            if !v.startISO.isEmpty {
                args += ["--video-start", v.startISO]
            }
            args += ["--video-duration", String(v.duration)]
        }
        for a in audios {
            args += ["--audio", a.url.path]
            if !a.startISO.isEmpty {
                args += ["--audio-start", a.startISO]
            }
            args += ["--audio-duration", String(a.duration)]
        }
        if let overlayYAML {
            args += ["--overlay", overlayYAML.path]
        }
        if let syncYAML {
            args += ["--sync-config", syncYAML.path]
        }
        if dryRun {
            args.append("--dry-run")
        }

        let result = try run(arguments: args) { line in
            onEvent(line)
        }
        if result.exitCode != 0 {
            throw FitvidCLIError.failed(result.stderr.isEmpty ? "generate failed" : result.stderr)
        }
    }
}
