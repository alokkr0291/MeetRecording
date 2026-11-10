// lmr-capture-helper — minimal ScreenCaptureKit + AVAssetWriter recorder.
//
// Protocol: JSON lines on stdin (commands) and stdout (events); diagnostics on stderr.
// See native/macos/PROTOCOL.md. The helper exits when stdin reaches EOF, so a crashed
// parent can never leave a recording running.
//
// Build: scripts/build_helper.sh (swiftc only, no Xcode).

import AppKit
import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

let kHelperVersion = "0.1.0"
let kProtocolVersion = 1

// MARK: - Output / logging

final class Out {
    static let shared = Out()
    private let lock = NSLock()
    private let handle = FileHandle.standardOutput

    func emit(_ fields: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(fields),
              let data = try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
        else {
            Log.error("emit: not a valid JSON object: \(fields)")
            return
        }
        lock.lock()
        defer { lock.unlock() }
        handle.write(data)
        handle.write(Data([0x0A]))
    }

    func event(_ name: String, _ fields: [String: Any] = [:]) {
        var f = fields
        f["event"] = name
        emit(f)
    }

    func error(_ code: String, _ message: String, fatal: Bool = false, extra: [String: Any] = [:]) {
        var f = extra
        f["code"] = code
        f["message"] = message
        f["fatal"] = fatal
        event("error", f)
    }
}

enum Log {
    private static let formatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func info(_ message: String) { write("INFO", message) }
    static func error(_ message: String) { write("ERROR", message) }

    private static func write(_ level: String, _ message: String) {
        let line = "\(formatter.string(from: Date())) \(level) \(message)\n"
        FileHandle.standardError.write(line.data(using: .utf8) ?? Data())
    }
}

// MARK: - Errors

enum HelperError: Error, CustomStringConvertible {
    case badRequest(String)
    case permissionDenied(String)
    case displayNotFound(Int)
    case noDisplays
    case outputExists(String)
    case writerSetup(String)
    case captureFailed(String)

    var code: String {
        switch self {
        case .badRequest: return "bad_command"
        case .permissionDenied: return "permission_denied"
        case .displayNotFound, .noDisplays: return "display_not_found"
        case .outputExists: return "output_exists"
        case .writerSetup: return "writer_setup_failed"
        case .captureFailed: return "capture_failed"
        }
    }

    var exitCode: Int32 {
        switch self {
        case .badRequest: return 2
        case .permissionDenied: return 3
        case .displayNotFound, .noDisplays, .captureFailed: return 4
        case .outputExists, .writerSetup: return 5
        }
    }

    var extra: [String: Any] {
        if case .permissionDenied(let which) = self { return ["permission": which] }
        return [:]
    }

    var description: String {
        switch self {
        case .badRequest(let m): return m
        case .permissionDenied(let which):
            return which == "screen"
                ? "Screen Recording permission is not granted"
                : "Microphone permission is not granted"
        case .displayNotFound(let id): return "display \(id) is not available"
        case .noDisplays: return "no displays available for capture"
        case .outputExists(let p): return "refusing to overwrite existing file \(p)"
        case .writerSetup(let m): return "could not set up media writer: \(m)"
        case .captureFailed(let m): return m
        }
    }
}

func classify(_ error: Error) -> HelperError {
    if let e = error as? HelperError { return e }
    let ns = error as NSError
    if let sc = error as? SCStreamError, sc.code == .userDeclined {
        return .permissionDenied("screen")
    }
    if ns.domain == "com.apple.ScreenCaptureKit.SCStreamErrorDomain", ns.code == -3801 {
        return .permissionDenied("screen")
    }
    return .captureFailed("\(ns.domain) \(ns.code): \(ns.localizedDescription)")
}

// MARK: - Permissions

enum Permissions {
    static func screenPreflight() -> Bool { CGPreflightScreenCaptureAccess() }

    static func microphone() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "granted"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not_determined"
        @unknown default: return "denied"
        }
    }

    /// Screen Recording state cannot be read precisely: the preflight API returns false for both
    /// "never asked" and "denied". Asking ScreenCaptureKit for shareable content is the reliable
    /// check and, on first use, is what triggers the system prompt.
    static func screen() async -> String {
        if screenPreflight() { return "granted" }
        do {
            _ = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            return "granted"
        } catch {
            return "denied"
        }
    }

    static func snapshot() async -> [String: Any] {
        [
            "screen": await screen(),
            "screen_preflight": screenPreflight(),
            "microphone": microphone(),
        ]
    }

    static func request() async -> [String: Any] {
        let screenGranted = CGRequestScreenCaptureAccess()
        var micGranted = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
            micGranted = await AVCaptureDevice.requestAccess(for: .audio)
        }
        var snap = await snapshot()
        snap["screen_request_result"] = screenGranted
        snap["microphone_request_result"] = micGranted
        return snap
    }
}

// MARK: - Displays

enum Displays {
    static func shareable() async throws -> SCShareableContent {
        try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    }

    static func name(for id: CGDirectDisplayID) -> String {
        var found: String?
        let lookup = {
            for screen in NSScreen.screens {
                let key = NSDeviceDescriptionKey("NSScreenNumber")
                if let n = screen.deviceDescription[key] as? NSNumber, n.uint32Value == id {
                    found = screen.localizedName
                    return
                }
            }
        }
        if Thread.isMainThread { lookup() } else { DispatchQueue.main.sync(execute: lookup) }
        return found ?? "Display \(id)"
    }

    static func json(_ display: SCDisplay) -> [String: Any] {
        let id = display.displayID
        let mode = CGDisplayCopyDisplayMode(id)
        return [
            "id": Int(id),
            "name": name(for: id),
            "width": display.width,
            "height": display.height,
            "pixel_width": mode?.pixelWidth ?? display.width,
            "pixel_height": mode?.pixelHeight ?? display.height,
            "is_main": id == CGMainDisplayID(),
        ]
    }
}

// MARK: - Start request

struct StartRequest {
    var outputPath: String
    var displayID: CGDirectDisplayID?
    var captureMicrophone = true
    var microphoneDeviceID: String?
    var fps = 30
    var videoBitrateKbps = 6000
    var audioBitrateKbps = 128
    var maxDimension = 1920
    var showsCursor = true
    var statsIntervalS = 5.0
    var minFreeMB = 512.0

    static func parse(_ obj: [String: Any]) throws -> StartRequest {
        guard let path = obj["output_path"] as? String, !path.isEmpty else {
            throw HelperError.badRequest("start requires a non-empty output_path")
        }
        var r = StartRequest(outputPath: path)
        if let d = obj["display_id"] as? Int { r.displayID = CGDirectDisplayID(d) }
        if let b = obj["capture_microphone"] as? Bool { r.captureMicrophone = b }
        if let s = obj["microphone_device_id"] as? String, !s.isEmpty { r.microphoneDeviceID = s }
        if let i = obj["fps"] as? Int { r.fps = max(1, min(60, i)) }
        if let i = obj["video_bitrate_kbps"] as? Int { r.videoBitrateKbps = max(500, i) }
        if let i = obj["audio_bitrate_kbps"] as? Int { r.audioBitrateKbps = max(32, min(320, i)) }
        if let i = obj["max_dimension"] as? Int { r.maxDimension = max(320, i) }
        if let b = obj["shows_cursor"] as? Bool { r.showsCursor = b }
        if let d = obj["stats_interval_s"] as? Double { r.statsIntervalS = max(0.5, d) }
        if let d = obj["min_free_mb"] as? Double { r.minFreeMB = d }
        return r
    }
}

func freeMegabytes(near path: String) -> Double {
    let dir = URL(fileURLWithPath: path).deletingLastPathComponent()
    let keys: Set<URLResourceKey> = [.volumeAvailableCapacityForImportantUsageKey]
    if let values = try? dir.resourceValues(forKeys: keys),
       let capacity = values.volumeAvailableCapacityForImportantUsage {
        return Double(capacity) / 1_048_576
    }
    return -1
}

func fileSize(_ path: String) -> Int {
    (try? FileManager.default.attributesOfItem(atPath: path)[.size] as? Int) ?? 0
}

// MARK: - Recorder

final class Recorder: NSObject, SCStreamDelegate, SCStreamOutput {
    private let request: StartRequest
    private let onFinished: (Int32) -> Void

    private var stream: SCStream?
    private var writer: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var systemInput: AVAssetWriterInput?
    private var micInput: AVAssetWriterInput?

    private let timerQueue = DispatchQueue(label: "lmr.recorder.timer")
    private let videoQueue = DispatchQueue(label: "lmr.recorder.video")
    private let systemQueue = DispatchQueue(label: "lmr.recorder.audio.system")
    private let micQueue = DispatchQueue(label: "lmr.recorder.audio.mic")

    private let lock = NSLock()
    private var sessionStart = CMTime.invalid
    private var lastPTS = CMTime.invalid
    private var stopping = false
    private var finished = false
    private var videoFrames = 0
    private var videoSkippedIncomplete = 0
    private var videoDroppedNotReady = 0
    private var systemBuffers = 0
    private var systemDroppedEarly = 0
    private var systemDroppedNotReady = 0
    private var micBuffers = 0
    private var micDroppedEarly = 0
    private var micDroppedNotReady = 0
    private var firstVideoPTS = CMTime.invalid
    private var firstSystemPTS = CMTime.invalid
    private var firstMicPTS = CMTime.invalid
    private var statsTimer: DispatchSourceTimer?
    private var startDate = Date()
    private var width = 0
    private var height = 0

    init(request: StartRequest, onFinished: @escaping (Int32) -> Void) {
        self.request = request
        self.onFinished = onFinished
        super.init()
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }

    private func finishSnapshot() -> (started: Bool, last: CMTime, start: CMTime) {
        locked { (sessionStart.isValid, lastPTS, sessionStart) }
    }

    private func markFinished() {
        locked { finished = true }
    }

    // MARK: start

    func start() {
        Task { await self.startAsync() }
    }

    private func startAsync() async {
        do {
            try preflightMicrophoneAndOutput()
            if request.captureMicrophone,
               AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                let granted = await AVCaptureDevice.requestAccess(for: .audio)
                if !granted { throw HelperError.permissionDenied("microphone") }
            }
            let content = try await Displays.shareable()
            let display = try pickDisplay(from: content)
            try setUpWriter(for: display)
            try setUpStream(display: display)
            guard let stream = stream else { throw HelperError.captureFailed("stream missing") }
            try await stream.startCapture()
            startDate = Date()
            startStatsTimer()
            Out.shared.event("started", [
                "output_path": request.outputPath,
                "display_id": Int(display.displayID),
                "width": width,
                "height": height,
                "fps": request.fps,
                "capture_microphone": request.captureMicrophone,
                "tracks": trackDescription(),
            ])
        } catch {
            let e = classify(error)
            Log.error("start failed: \(e.code) \(e.description)")
            Out.shared.error(e.code, e.description, fatal: true, extra: e.extra)
            await abortAfterStartFailure()
            onFinished(e.exitCode)
        }
    }

    private func trackDescription() -> [[String: Any]] {
        var tracks: [[String: Any]] = [
            ["index": 0, "kind": "video", "codec": "h264"],
            ["index": 1, "kind": "audio", "role": "system", "codec": "aac"],
        ]
        if request.captureMicrophone {
            tracks.append(["index": 2, "kind": "audio", "role": "microphone", "codec": "aac"])
        }
        return tracks
    }

    private func preflightMicrophoneAndOutput() throws {
        if request.captureMicrophone {
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized, .notDetermined: break
            default: throw HelperError.permissionDenied("microphone")
            }
        }
        if FileManager.default.fileExists(atPath: request.outputPath) {
            throw HelperError.outputExists(request.outputPath)
        }
    }

    private func pickDisplay(from content: SCShareableContent) throws -> SCDisplay {
        if let wanted = request.displayID {
            guard let d = content.displays.first(where: { $0.displayID == wanted }) else {
                throw HelperError.displayNotFound(Int(wanted))
            }
            return d
        }
        let main = CGMainDisplayID()
        if let d = content.displays.first(where: { $0.displayID == main }) { return d }
        guard let d = content.displays.first else { throw HelperError.noDisplays }
        return d
    }

    private func setUpWriter(for display: SCDisplay) throws {
        let mode = CGDisplayCopyDisplayMode(display.displayID)
        var pw = mode?.pixelWidth ?? display.width
        var ph = mode?.pixelHeight ?? display.height
        let longest = max(pw, ph)
        if longest > request.maxDimension {
            let scale = Double(request.maxDimension) / Double(longest)
            pw = Int(Double(pw) * scale)
            ph = Int(Double(ph) * scale)
        }
        width = max(2, pw - pw % 2)
        height = max(2, ph - ph % 2)

        let url = URL(fileURLWithPath: request.outputPath)
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        } catch {
            throw HelperError.writerSetup("cannot create output directory: \(error.localizedDescription)")
        }
        let w: AVAssetWriter
        do {
            w = try AVAssetWriter(outputURL: url, fileType: .mov)
        } catch {
            throw HelperError.writerSetup(error.localizedDescription)
        }
        // Fragmented movie: if the process dies, everything up to the last fragment stays readable.
        w.movieFragmentInterval = CMTime(seconds: 5, preferredTimescale: 600)
        w.shouldOptimizeForNetworkUse = false

        let videoSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: request.videoBitrateKbps * 1000,
                AVVideoExpectedSourceFrameRateKey: request.fps,
                AVVideoMaxKeyFrameIntervalKey: request.fps * 2,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                AVVideoAllowFrameReorderingKey: false,
            ],
        ]
        let v = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
        v.expectsMediaDataInRealTime = true
        v.metadata = [titleItem("Screen")]
        guard w.canAdd(v) else { throw HelperError.writerSetup("cannot add video input") }
        w.add(v)

        let audioSettings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 48000,
            AVNumberOfChannelsKey: 2,
            AVEncoderBitRateKey: request.audioBitrateKbps * 1000,
        ]
        let s = AVAssetWriterInput(mediaType: .audio, outputSettings: audioSettings)
        s.expectsMediaDataInRealTime = true
        s.metadata = [titleItem("System Audio")]
        guard w.canAdd(s) else { throw HelperError.writerSetup("cannot add system audio input") }
        w.add(s)

        if request.captureMicrophone {
            let m = AVAssetWriterInput(mediaType: .audio, outputSettings: audioSettings)
            m.expectsMediaDataInRealTime = true
            m.metadata = [titleItem("Microphone")]
            guard w.canAdd(m) else { throw HelperError.writerSetup("cannot add microphone input") }
            w.add(m)
            micInput = m
        }

        guard w.startWriting() else {
            throw HelperError.writerSetup("startWriting failed: \(w.error?.localizedDescription ?? "unknown")")
        }
        writer = w
        videoInput = v
        systemInput = s
    }

    private func titleItem(_ title: String) -> AVMetadataItem {
        let item = AVMutableMetadataItem()
        item.identifier = .commonIdentifierTitle
        item.value = title as NSString
        item.extendedLanguageTag = "und"
        return item
    }

    private func setUpStream(display: SCDisplay) throws {
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.width = width
        cfg.height = height
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(request.fps))
        cfg.pixelFormat = kCVPixelFormatType_32BGRA
        cfg.showsCursor = request.showsCursor
        cfg.queueDepth = 6
        cfg.capturesAudio = true
        cfg.sampleRate = 48000
        cfg.channelCount = 2
        cfg.excludesCurrentProcessAudio = true
        cfg.captureMicrophone = request.captureMicrophone
        if let id = request.microphoneDeviceID { cfg.microphoneCaptureDeviceID = id }

        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: videoQueue)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: systemQueue)
        if request.captureMicrophone {
            try s.addStreamOutput(self, type: .microphone, sampleHandlerQueue: micQueue)
        }
        stream = s
    }

    private func abortAfterStartFailure() async {
        if let s = stream {
            try? await s.stopCapture()
            stream = nil
        }
        if let w = writer, w.status == .writing {
            w.cancelWriting()  // nothing was recorded; removes the empty container
        }
    }

    // MARK: sample handling

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        switch type {
        case .screen:
            handleVideo(sampleBuffer)
        case .audio:
            if let input = systemInput { handleAudio(sampleBuffer, input: input, isMic: false) }
        case .microphone:
            if let input = micInput { handleAudio(sampleBuffer, input: input, isMic: true) }
        @unknown default:
            break
        }
    }

    private func handleVideo(_ sb: CMSampleBuffer) {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false)
                as? [[SCStreamFrameInfo: Any]],
              let info = attachments.first,
              let raw = info[.status] as? Int,
              let status = SCFrameStatus(rawValue: raw)
        else { return }
        guard status == .complete, CMSampleBufferGetImageBuffer(sb) != nil else {
            lock.lock(); videoSkippedIncomplete += 1; lock.unlock()
            return
        }
        let pts = CMSampleBufferGetPresentationTimeStamp(sb)
        guard ensureSession(at: pts), let input = videoInput else { return }
        guard let w = writer, w.status == .writing else { writerFailureCheck(); return }
        if input.isReadyForMoreMediaData {
            if input.append(sb) {
                lock.lock()
                videoFrames += 1
                if !firstVideoPTS.isValid { firstVideoPTS = pts }
                if !lastPTS.isValid || pts > lastPTS { lastPTS = pts }
                lock.unlock()
            } else {
                writerFailureCheck()
            }
        } else {
            lock.lock(); videoDroppedNotReady += 1; lock.unlock()
        }
    }

    private func handleAudio(_ sb: CMSampleBuffer, input: AVAssetWriterInput, isMic: Bool) {
        let pts = CMSampleBufferGetPresentationTimeStamp(sb)
        guard ensureSession(at: pts) else { return }
        let duration = CMSampleBufferGetDuration(sb)
        let end = duration.isValid && duration.isNumeric ? pts + duration : pts
        lock.lock(); let start = sessionStart; lock.unlock()
        if end < start {
            lock.lock()
            if isMic { micDroppedEarly += 1 } else { systemDroppedEarly += 1 }
            lock.unlock()
            return
        }
        guard let w = writer, w.status == .writing else { writerFailureCheck(); return }
        if input.isReadyForMoreMediaData {
            if input.append(sb) {
                lock.lock()
                if isMic {
                    micBuffers += 1
                    if !firstMicPTS.isValid { firstMicPTS = pts }
                } else {
                    systemBuffers += 1
                    if !firstSystemPTS.isValid { firstSystemPTS = pts }
                }
                if !lastPTS.isValid || end > lastPTS { lastPTS = end }
                lock.unlock()
            } else {
                writerFailureCheck()
            }
        } else {
            lock.lock()
            if isMic { micDroppedNotReady += 1 } else { systemDroppedNotReady += 1 }
            lock.unlock()
        }
    }

    /// Starts the writer session on the first buffer of any kind. All three sources share the
    /// ScreenCaptureKit host clock, so later buffers land at their true offsets.
    private func ensureSession(at pts: CMTime) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if stopping || finished { return false }
        if sessionStart.isValid { return true }
        guard let w = writer, w.status == .writing else { return false }
        w.startSession(atSourceTime: pts)
        sessionStart = pts
        Log.info("writer session started at pts \(pts.seconds)")
        return true
    }

    private func writerFailureCheck() {
        guard let w = writer, w.status == .failed else { return }
        lock.lock(); let already = stopping || finished; lock.unlock()
        if already { return }
        let ns = w.error as NSError?
        var code = "write_failed"
        if let ns = ns {
            if ns.domain == AVFoundationErrorDomain, ns.code == AVError.diskFull.rawValue { code = "disk_full" }
            if let under = ns.userInfo[NSUnderlyingErrorKey] as? NSError,
               under.domain == NSPOSIXErrorDomain, under.code == Int(ENOSPC) { code = "disk_full" }
        }
        Out.shared.error(code, "media writer failed: \(ns?.localizedDescription ?? "unknown")", fatal: true)
        stop(reason: code, exitCode: 5)
    }

    // MARK: stats

    private func startStatsTimer() {
        let t = DispatchSource.makeTimerSource(queue: timerQueue)
        t.schedule(deadline: .now() + request.statsIntervalS, repeating: request.statsIntervalS)
        t.setEventHandler { [weak self] in self?.emitStats() }
        t.resume()
        statsTimer = t
    }

    private func counters() -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        var c: [String: Any] = [
            "video_frames": videoFrames,
            "video_skipped_incomplete": videoSkippedIncomplete,
            "video_dropped_not_ready": videoDroppedNotReady,
            "system_audio_buffers": systemBuffers,
            "system_audio_dropped_early": systemDroppedEarly,
            "system_audio_dropped_not_ready": systemDroppedNotReady,
            "mic_audio_buffers": micBuffers,
            "mic_audio_dropped_early": micDroppedEarly,
            "mic_audio_dropped_not_ready": micDroppedNotReady,
            "session_started": sessionStart.isValid,
        ]
        if sessionStart.isValid {
            if firstVideoPTS.isValid { c["first_video_offset_s"] = (firstVideoPTS - sessionStart).seconds }
            if firstSystemPTS.isValid { c["first_system_audio_offset_s"] = (firstSystemPTS - sessionStart).seconds }
            if firstMicPTS.isValid { c["first_mic_audio_offset_s"] = (firstMicPTS - sessionStart).seconds }
            if lastPTS.isValid { c["media_duration_s"] = (lastPTS - sessionStart).seconds }
        }
        return c
    }

    private func emitStats() {
        var f = counters()
        f["elapsed_s"] = Date().timeIntervalSince(startDate)
        f["bytes"] = fileSize(request.outputPath)
        let free = freeMegabytes(near: request.outputPath)
        f["free_mb"] = free
        Out.shared.event("stats", f)
        if free >= 0, free < request.minFreeMB {
            Out.shared.event("warning", [
                "code": "low_disk_space",
                "message": "free disk space is \(Int(free)) MB, below the \(Int(request.minFreeMB)) MB threshold",
                "free_mb": free,
            ])
        }
        writerFailureCheck()
    }

    // MARK: stop

    func stop(reason: String = "requested", exitCode: Int32 = 0) {
        lock.lock()
        if stopping { lock.unlock(); return }
        stopping = true
        lock.unlock()
        statsTimer?.cancel()
        statsTimer = nil
        Out.shared.event("stopping", ["reason": reason])
        // Watchdog: a hung stopCapture/finishWriting must not keep the process alive forever.
        timerQueue.asyncAfter(deadline: .now() + 25) { [weak self] in
            guard let self = self else { return }
            self.lock.lock(); let done = self.finished; self.lock.unlock()
            if !done {
                Out.shared.error("stop_timeout", "finalization did not complete within 25 s", fatal: true)
                self.onFinished(6)
            }
        }
        Task { await self.stopAsync(reason: reason, exitCode: exitCode) }
    }

    private func stopAsync(reason: String, exitCode: Int32) async {
        if let s = stream {
            do { try await s.stopCapture() } catch { Log.error("stopCapture: \(error)") }
        }
        // Let in-flight sample handlers drain before finishing the inputs.
        videoQueue.sync {}
        systemQueue.sync {}
        micQueue.sync {}
        await finishWriter(reason: reason, exitCode: exitCode)
    }

    private func finishWriter(reason: String, exitCode: Int32) async {
        let (started, last, start) = finishSnapshot()

        var finalized = false
        var failure: String?
        if let w = writer {
            if w.status == .writing, started {
                videoInput?.markAsFinished()
                systemInput?.markAsFinished()
                micInput?.markAsFinished()
                if last.isValid, last > start { w.endSession(atSourceTime: last) }
                await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                    w.finishWriting { c.resume() }
                }
                finalized = w.status == .completed
                if !finalized { failure = w.error?.localizedDescription ?? "writer status \(w.status.rawValue)" }
            } else if w.status == .writing {
                w.cancelWriting()
                failure = "no media samples were received; nothing to save"
            } else {
                failure = w.error?.localizedDescription ?? "writer status \(w.status.rawValue)"
            }
        } else {
            failure = "writer was never created"
        }

        var f = counters()
        f["reason"] = reason
        f["finalized"] = finalized
        f["output_path"] = request.outputPath
        f["bytes"] = fileSize(request.outputPath)
        f["duration_s"] = (started && last.isValid) ? max(0, (last - start).seconds) : 0
        f["elapsed_s"] = Date().timeIntervalSince(startDate)
        f["error"] = failure ?? NSNull()
        Out.shared.event("stopped", f)

        markFinished()
        onFinished(finalized ? exitCode : max(exitCode, 5))
    }

    // MARK: SCStreamDelegate

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        let ns = error as NSError
        lock.lock(); let already = stopping || finished; lock.unlock()
        if already { return }
        Log.error("stream stopped with error: \(ns)")
        Out.shared.error(
            "stream_stopped", "capture stream stopped: \(ns.localizedDescription)", fatal: true,
            extra: ["domain": ns.domain, "os_code": ns.code])
        stop(reason: "stream_error", exitCode: 4)
    }
}

// MARK: - Command loop

final class Helper {
    private var recorder: Recorder?
    private let queue = DispatchQueue(label: "lmr.helper.commands")
    private var signalSources: [DispatchSourceSignal] = []
    private var exiting = false

    func run() {
        Out.shared.event("ready", [
            "version": kHelperVersion,
            "protocol": kProtocolVersion,
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "macos": ProcessInfo.processInfo.operatingSystemVersionString,
        ])
        installSignalHandlers()
        let reader = Thread { [weak self] in self?.readLoop() }
        reader.name = "stdin-reader"
        reader.start()
        dispatchMain()
    }

    private func readLoop() {
        while let line = readLine(strippingNewline: true) {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { continue }
            queue.async { [weak self] in self?.handle(line: trimmed) }
        }
        queue.async { [weak self] in self?.handleEOF() }
    }

    private func handle(line: String) {
        guard let data = line.data(using: .utf8),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let cmd = obj["cmd"] as? String
        else {
            Out.shared.error("bad_command", "expected a JSON object with a string \"cmd\" field")
            return
        }
        switch cmd {
        case "ping":
            Out.shared.event("pong")
        case "probe":
            Task {
                Out.shared.event("probe", [
                    "version": kHelperVersion,
                    "protocol": kProtocolVersion,
                    "macos": ProcessInfo.processInfo.operatingSystemVersionString,
                    "permissions": await Permissions.snapshot(),
                ])
            }
        case "request-permissions":
            Task { Out.shared.event("permissions", await Permissions.request()) }
        case "list-displays":
            Task {
                do {
                    let content = try await Displays.shareable()
                    Out.shared.event("displays", ["displays": content.displays.map(Displays.json)])
                } catch {
                    let e = classify(error)
                    Out.shared.error(e.code, e.description, extra: e.extra)
                }
            }
        case "start":
            startRecording(obj)
        case "stop":
            stopRecording()
        default:
            Out.shared.error("unknown_command", "unknown cmd \"\(cmd)\"")
        }
    }

    private func startRecording(_ obj: [String: Any]) {
        if recorder != nil {
            Out.shared.error("already_recording", "a recording is already active in this helper")
            return
        }
        do {
            let request = try StartRequest.parse(obj)
            let r = Recorder(request: request) { [weak self] code in self?.exit(code) }
            recorder = r
            r.start()
        } catch {
            let e = classify(error)
            Out.shared.error(e.code, e.description, extra: e.extra)
        }
    }

    private func stopRecording() {
        guard let r = recorder else {
            Out.shared.error("not_recording", "no active recording")
            return
        }
        r.stop()
    }

    private func handleEOF() {
        if let r = recorder {
            Log.info("stdin closed; stopping recording")
            r.stop(reason: "stdin_eof")
        } else {
            exit(0)
        }
    }

    private func exit(_ code: Int32) {
        queue.async {
            if self.exiting { return }
            self.exiting = true
            Log.info("exiting with code \(code)")
            Foundation.exit(code)
        }
    }

    private func installSignalHandlers() {
        for sig in [SIGINT, SIGTERM, SIGHUP] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: queue)
            source.setEventHandler { [weak self] in
                guard let self = self else { return }
                if let r = self.recorder {
                    Log.info("signal \(sig) received; stopping recording")
                    r.stop(reason: "signal")
                } else {
                    self.exit(0)
                }
            }
            source.resume()
            signalSources.append(source)
        }
    }
}

// MARK: - Entry point

let args = Array(CommandLine.arguments.dropFirst())
if args.first == "--version" || args.first == "-v" {
    print("lmr-capture-helper \(kHelperVersion) (protocol \(kProtocolVersion))")
    exit(0)
}
if args.first == "--help" || args.first == "-h" {
    print("""
    lmr-capture-helper \(kHelperVersion)
    Usage:
      lmr-capture-helper                 run the JSON-lines command loop on stdin/stdout
      lmr-capture-helper probe           print permissions and version, then exit
      lmr-capture-helper list-displays   print available displays, then exit
      lmr-capture-helper request-permissions  trigger the macOS permission prompts, then exit
      lmr-capture-helper --version
    """)
    exit(0)
}
if let oneShot = args.first, ["probe", "list-displays", "request-permissions"].contains(oneShot) {
    Task {
        switch oneShot {
        case "probe":
            Out.shared.event("probe", [
                "version": kHelperVersion,
                "protocol": kProtocolVersion,
                "macos": ProcessInfo.processInfo.operatingSystemVersionString,
                "permissions": await Permissions.snapshot(),
            ])
        case "list-displays":
            do {
                let content = try await Displays.shareable()
                Out.shared.event("displays", ["displays": content.displays.map(Displays.json)])
            } catch {
                let e = classify(error)
                Out.shared.error(e.code, e.description, extra: e.extra)
                exit(e.exitCode)
            }
        default:
            Out.shared.event("permissions", await Permissions.request())
        }
        exit(0)
    }
    dispatchMain()
}

let helper = Helper()
helper.run()
