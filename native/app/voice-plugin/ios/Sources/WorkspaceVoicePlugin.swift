import AVFoundation
import Foundation
import Speech
@preconcurrency import Tauri
import UIKit

private struct StartArgs: Decodable {
    let session_id: String
    let locale: String?
    let on_event: Channel
}

private struct EndArgs: Decodable {
    let session_id: String
}

private enum VoiceEvent: Encodable {
    case starting(String)
    case listening(String)
    case transcript(String, text: String, isFinal: Bool)
    case stopped(String)
    case cancelled(String)
    case error(String, message: String)

    private enum Keys: String, CodingKey {
        case type, session_id, text, is_final, message
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: Keys.self)
        let kind: String
        let identity: String
        switch self {
        case .starting(let id): (kind, identity) = ("starting", id)
        case .listening(let id): (kind, identity) = ("listening", id)
        case .transcript(let id, let text, let isFinal):
            (kind, identity) = ("transcript", id)
            try container.encode(text, forKey: .text)
            try container.encode(isFinal, forKey: .is_final)
        case .stopped(let id): (kind, identity) = ("stopped", id)
        case .cancelled(let id): (kind, identity) = ("cancelled", id)
        case .error(let id, let message):
            (kind, identity) = ("error", id)
            try container.encode(message, forKey: .message)
        }
        try container.encode(kind, forKey: .type)
        try container.encode(identity, forKey: .session_id)
    }
}

@MainActor
private final class SpeechOwner: NSObject {
    private struct Authorization {
        let id: String
        let generation: UInt64
        let locale: Locale
        let channel: Channel
        let invoke: Invoke
    }

    private final class Recording {
        let id: String
        let generation: UInt64
        let channel: Channel
        let recognizer: SFSpeechRecognizer
        let engine: AVAudioEngine
        let request: SFSpeechAudioBufferRecognitionRequest
        let task: SFSpeechRecognitionTask
        var audioStopped = false
        var deadline: Task<Void, Never>?

        init(id: String, generation: UInt64, channel: Channel, recognizer: SFSpeechRecognizer,
             engine: AVAudioEngine, request: SFSpeechAudioBufferRecognitionRequest,
             task: SFSpeechRecognitionTask) {
            self.id = id
            self.generation = generation
            self.channel = channel
            self.recognizer = recognizer
            self.engine = engine
            self.request = request
            self.task = task
        }
    }

    private enum State {
        case idle
        case authorizing(Authorization)
        case recording(Recording)
        case finishing(Recording)
    }

    private var state: State = .idle
    private var generation: UInt64 = 0

    override init() {
        super.init()
        NotificationCenter.default.addObserver(
            self, selector: #selector(interrupted(_:)),
            name: UIApplication.willResignActiveNotification, object: nil
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(interrupted(_:)),
            name: UIApplication.didEnterBackgroundNotification, object: nil
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(interrupted(_:)),
            name: AVAudioSession.interruptionNotification, object: nil
        )
    }

    @objc nonisolated private func interrupted(_ notification: Notification) {
        let temporarilyInactive = notification.name == UIApplication.willResignActiveNotification
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            // System permission prompts can temporarily deactivate the app.
            // Preserve that pending request, but cancel it if the app actually
            // enters the background. Active audio still stops on inactivity.
            if temporarilyInactive, case .authorizing = self.state { return }
            self.cancelCurrent()
        }
    }

    func capability(_ invoke: Invoke) {
        let locale = Locale.current
        guard let recognizer = SFSpeechRecognizer(locale: locale) else {
            invoke.resolve(["type": "unavailable", "reason": "Speech recognition is unavailable for this device language."])
            return
        }
        let authorization: String
        switch SFSpeechRecognizer.authorizationStatus() {
        case .notDetermined: authorization = "not_determined"
        case .denied: authorization = "denied"
        case .restricted: authorization = "restricted"
        case .authorized: authorization = "authorized"
        @unknown default: authorization = "unavailable"
        }
        invoke.resolve([
            "type": "native_speech", "locale": locale.identifier,
            "on_device": recognizer.supportsOnDeviceRecognition,
            "authorization": authorization
        ])
    }

    func start(_ invoke: Invoke) {
        guard case .idle = state else {
            invoke.reject("A voice session is already active.")
            return
        }
        let args: StartArgs
        do { args = try invoke.parseArgs(StartArgs.self) }
        catch { invoke.reject("The voice request is invalid."); return }
        guard !args.session_id.isEmpty, args.session_id.utf8.count <= 128,
              (args.locale?.utf8.count ?? 0) <= 64 else {
            invoke.reject("The voice session identity or locale is invalid.")
            return
        }
        generation &+= 1
        let pending = Authorization(
            id: args.session_id,
            generation: generation,
            locale: args.locale.map(Locale.init(identifier:)) ?? Locale.current,
            channel: args.on_event, invoke: invoke
        )
        state = .authorizing(pending)
        send(.starting(pending.id), to: pending.channel)
        let capturedGeneration = pending.generation
        SFSpeechRecognizer.requestAuthorization { [weak self] status in
            DispatchQueue.main.async {
                self?.speechAuthorized(generation: capturedGeneration, authorized: status == .authorized)
            }
        }
    }

    private func speechAuthorized(generation: UInt64, authorized: Bool) {
        guard case .authorizing(let pending) = state, pending.generation == generation else { return }
        guard authorized else {
            failAuthorization(pending, "Speech recognition is not permitted on this device.")
            return
        }
        let completion: (Bool) -> Void = { [weak self] allowed in
            DispatchQueue.main.async { self?.microphoneAuthorized(generation: generation, authorized: allowed) }
        }
        if #available(iOS 17.0, *) {
            AVAudioApplication.requestRecordPermission(completionHandler: completion)
        } else {
            AVAudioSession.sharedInstance().requestRecordPermission(completion)
        }
    }

    private func microphoneAuthorized(generation: UInt64, authorized: Bool) {
        guard case .authorizing(let pending) = state, pending.generation == generation else { return }
        let id = pending.id
        guard authorized else {
            failAuthorization(pending, "Microphone access is not permitted on this device.")
            return
        }
        guard let recognizer = SFSpeechRecognizer(locale: pending.locale), recognizer.isAvailable else {
            failAuthorization(pending, "Speech recognition is currently unavailable.")
            return
        }
        let engine = AVAudioEngine()
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        var installedTap = false
        do {
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.record, mode: .measurement)
            try audio.setActive(true)
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                throw NSError(domain: "WorkspaceVoice", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "No microphone input is available."])
            }
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
                request.append(buffer)
            }
            installedTap = true
            engine.prepare()
            try engine.start()
            let task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                // Only value data crosses into the UI/audio owner. Audio buffers
                // stay with AVAudioEngine and the platform recognition request.
                let text = result?.bestTranscription.formattedString
                let isFinal = result?.isFinal ?? false
                let failure = error?.localizedDescription
                DispatchQueue.main.async {
                    self?.received(generation: generation, text: text, isFinal: isFinal, failure: failure)
                }
            }
            let recording = Recording(id: id, generation: generation, channel: pending.channel, recognizer: recognizer,
                                      engine: engine, request: request, task: task)
            state = .recording(recording)
            recording.deadline = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(60)) }
                catch { return }
                self?.stopCurrent(generation: generation)
            }
            send(.listening(id), to: pending.channel)
            pending.invoke.resolve(["session_id": id])
        } catch {
            engine.stop()
            if installedTap { engine.inputNode.removeTap(onBus: 0) }
            request.endAudio()
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            failAuthorization(pending, "Microphone capture could not start: \(error.localizedDescription)")
        }
    }

    private func failAuthorization(_ pending: Authorization, _ message: String) {
        state = .idle
        send(.error(pending.id, message: message), to: pending.channel)
        pending.invoke.reject(message)
    }

    private func received(generation: UInt64, text: String?, isFinal: Bool, failure: String?) {
        let recording: Recording
        switch state {
        case .recording(let value), .finishing(let value): recording = value
        default: return
        }
        guard recording.generation == generation else { return }
        let id = recording.id
        if let text {
            guard text.utf8.count <= 65_536 else {
                finish(recording, event: .error(id, message: "This spoken command is too long. The last displayed transcript is retained."))
                return
            }
            send(.transcript(id, text: text, isFinal: isFinal), to: recording.channel)
        }
        if isFinal {
            finish(recording, event: .stopped(id))
        } else if let failure {
            finish(recording, event: .error(id, message: failure))
        }
    }

    private func stopAudio(_ recording: Recording) {
        guard !recording.audioStopped else { return }
        recording.audioStopped = true
        recording.engine.stop()
        recording.engine.inputNode.removeTap(onBus: 0)
        recording.request.endAudio()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func stopCurrent(generation: UInt64) {
        guard case .recording(let recording) = state, recording.generation == generation else { return }
        recording.deadline?.cancel()
        stopAudio(recording)
        state = .finishing(recording)
        recording.deadline = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(3)) }
            catch { return }
            guard let self, case .finishing(let current) = state, current.generation == generation else { return }
            finish(current, event: .stopped(current.id))
        }
    }

    func stop(_ invoke: Invoke) {
        guard matchesCurrent(invoke) else { return }
        switch state {
        case .recording(let recording): stopCurrent(generation: recording.generation)
        case .authorizing: cancelCurrent()
        case .idle, .finishing: break
        }
        invoke.resolve()
    }

    func cancel(_ invoke: Invoke) {
        guard matchesCurrent(invoke) else { return }
        cancelCurrent()
        invoke.resolve()
    }

    private func matchesCurrent(_ invoke: Invoke) -> Bool {
        let args: EndArgs
        do { args = try invoke.parseArgs(EndArgs.self) }
        catch { invoke.reject("The voice session identity is missing."); return false }
        let identity: String?
        switch state {
        case .idle: identity = nil
        case .authorizing(let pending): identity = pending.id
        case .recording(let recording), .finishing(let recording): identity = recording.id
        }
        guard identity == nil || identity == args.session_id else {
            invoke.reject("That voice session is no longer current.")
            return false
        }
        return true
    }

    private func cancelCurrent() {
        switch state {
        case .idle: break
        case .authorizing(let pending):
            state = .idle
            send(.cancelled(pending.id), to: pending.channel)
            pending.invoke.reject("Voice input was cancelled.")
        case .recording(let recording), .finishing(let recording):
            finish(recording, event: .cancelled(recording.id))
        }
    }

    private func finish(_ recording: Recording, event: VoiceEvent) {
        state = .idle
        recording.deadline?.cancel()
        stopAudio(recording)
        recording.task.cancel()
        send(event, to: recording.channel)
    }

    private func send(_ event: VoiceEvent, to channel: Channel) {
        // Events contain value-only strings and Booleans with a fixed encoder.
        // A closed webview is handled by Tauri's channel lifetime.
        try? channel.send(event)
    }
}

/// Tauri's Objective-C plugin entry points may arrive from its IPC thread.
/// All microphone lifetime and mutable state belong to the main actor.
final class WorkspaceVoicePlugin: Plugin {
    @MainActor private lazy var speech = SpeechOwner()

    @objc public func capability(_ invoke: Invoke) {
        DispatchQueue.main.async { [self] in speech.capability(invoke) }
    }

    @objc public func start(_ invoke: Invoke) {
        DispatchQueue.main.async { [self] in speech.start(invoke) }
    }

    @objc public func stop(_ invoke: Invoke) {
        DispatchQueue.main.async { [self] in speech.stop(invoke) }
    }

    @objc public func cancel(_ invoke: Invoke) {
        DispatchQueue.main.async { [self] in speech.cancel(invoke) }
    }
}

@_cdecl("init_plugin_workspace_voice")
public func initPlugin() -> Plugin {
    WorkspaceVoicePlugin()
}
