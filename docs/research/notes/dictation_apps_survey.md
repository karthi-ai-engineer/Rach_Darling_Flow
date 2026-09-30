# Survey of dictation-app audio-to-text pipelines, focused on accuracy techniques

Method: I shallow-cloned the current main branches (2026-09-30) of Handy, VoiceInk, OpenWhispr, whisper-writer and Epicenter/Whispering and read their source directly. For the others I used web docs, READMEs, changelogs and HN threads. Source links point at GitHub file paths on `main`. Line numbers are from the 2026-09-30 clones and may drift.

## Q1. What each project's pipeline actually does (capture, VAD, ASR, vocabulary, post-processing, LLM)

### Takeaway
The open-source apps have landed on roughly the same pipeline. The mic is warm or pre-opened, and a pre-roll buffer catches speech that starts before the key is fully down. Silero VAD with onset/hangover smoothing runs, often off by default. Next comes Parakeet (TDT v3 or a unified EN model) or whisper.cpp, and Whisper models get the vocabulary as `initial_prompt`. After ASR comes a regex hallucination and filler filter, then deterministic word replacement (fuzzy for Parakeet), then an optional LLM cleanup. The LLM gets the vocabulary plus app context (selected text, clipboard, window or screen text) and has a strict "don't answer the transcript" system prompt. The commercial apps add context from on-screen text and auto-learn from user corrections.

### Cited Findings

**Handy (cjpais/Handy, Rust/Tauri)**
- Recording goes through a `SmoothedVad` wrapper around Silero (`silero_vad_v4.onnx`, threshold 0.5) or an experimental "Earshot" backend. Constants are `VAD_PREFILL_MS = 450` (pre-roll kept before speech onset), `VAD_ONSET_MS = 60` (consecutive voiced time needed to start speech), `VAD_OFFLINE_HANGOVER_MS = 450` and `VAD_STREAMING_HANGOVER_MS = 1650`. — [vad/mod.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/vad/mod.rs), [bin/cli.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/bin/cli.rs)
- How `SmoothedVad` works: every frame is buffered into a ring of `prefill+1` frames. When the onset count is reached, the whole prefill buffer is emitted along with the current frame, so the first syllable isn't lost. After speech, a hangover counter keeps frames flowing. Non-speech frames are dropped, so VAD cuts silence rather than just trimming the ends. A `tail_report()` diagnostic logs "withheld tail N frames (~ms, K voiced)" at stop to help find cut-off final words. — [vad/smoothed.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/vad/smoothed.rs), [recorder.rs ~L886-898](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/audio/recorder.rs)
- Settings include `vad_enabled` (default true), `vad_backend`, `always_on_microphone` (keeps the stream open), `mute_while_recording`, and `extra_recording_buffer_ms` (default **0**). When set, the extra buffer keeps recording for N ms after the key is released, to catch trailing audio. — [settings.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/settings.rs), [managers/audio.rs ~L993-1010](https://github.com/cjpais/Handy/blob/main/src-tauri/src/managers/audio.rs)
- Models: Whisper Small/Medium/Turbo/Large (GPU when available) and Parakeet V3 ("CPU-optimized … automatic language detection"). The README now recommends a "Parakeet Unified EN 0.6B" GGUF (Q8_0, 731 MB). Parakeet is loaded with Int8 quantization. — [README](https://github.com/cjpais/Handy/blob/main/README.md), [managers/transcription.rs ~L639-647](https://github.com/cjpais/Handy/blob/main/src-tauri/src/managers/transcription.rs)
- Vocabulary handling depends on the model:
  - For Whisper-family models that support `Feature::InitialPrompt`, custom words go in as `initial_prompt = custom_words.join(", ")`.
  - Otherwise (for example Parakeet), `apply_custom_words` runs after ASR. It builds n-grams of the transcript and scores each against every custom word using normalised Levenshtein distance. When the two words match by Soundex, the score is multiplied by 0.3 as a phonetic boost. Case and punctuation are kept.
  - The default `word_correction_threshold` is **0.18**, and the maximum difference allowed is `max(len*0.25, 2)` characters.
  - Soundex is skipped for numeric or non-ASCII keys.
  - [transcription.rs ~L1242-1314, L1772-1777](https://github.com/cjpais/Handy/blob/main/src-tauri/src/managers/transcription.rs), [audio_toolkit/text.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/text.rs), [settings.rs `default_word_correction_threshold`](https://github.com/cjpais/Handy/blob/main/src-tauri/src/settings.rs)
- Post-processing: `remove_filler_words` (language-gated filler lists, capitals restored when a leading filler is removed), `collapse_stutters`, and `normalize_transcription_output`. The optional LLM step uses a `${output}` prompt template, strips `<think>` blocks and invisible Unicode, and can use a local Apple Intelligence model. — [text.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/text.rs), [actions.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/actions.rs)
- From the author on HN: "Bluetooth devices do not work super well at the moment" (1–2 s latency with AirPods Max). Workarounds were the built-in mic or the always-on-microphone option. A PR to show confidence scores for uncertain words was pending. — [HN: Handy](https://news.ycombinator.com/item?id=46628397)

**VoiceInk (Beingpax/VoiceInk, Swift/macOS)**
- The documented pipeline order is "transcribe → filter → format → word-replace → AI enhance → deliver → save". — [TranscriptionPipeline.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Recording/Workflows/TranscriptionPipeline.swift)
- whisper.cpp parameters:
  - General: `no_context = true`, `single_segment = false`, `temperature = 0.2`, and `initial_prompt` set from the vocabulary.
  - Built-in Silero VAD: `threshold 0.50`, `min_speech_duration_ms 250`, `min_silence_duration_ms 100`, `speech_pad_ms 30`, `samples_overlap 0.1`.
  - [LibWhisper.swift L49-85](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Infrastructure/Providers/Transcription/Whisper/LibWhisper.swift)
- Parakeet (via FluidAudio/CoreML):
  - VAD is optional (`IsVADEnabled`, `VadConfig(defaultThreshold: 0.7)`). If VAD fails, the full audio is used.
  - Audio shorter than `ASRConstants.minimumRequiredSamples` is **zero-padded up to the minimum**.
  - The streaming Nemotron path appends **1 s of trailing silence (16,000 samples)** before `finish()` to flush the last tokens.
  - [FluidAudioTranscriptionService.swift L171-232, L259](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Infrastructure/Providers/Transcription/FluidAudio/FluidAudioTranscriptionService.swift)
- The output filter removes `<TAG>…</TAG>` blocks and any bracketed `[...]`, `(...)`, `{...}` text (treated as hallucinations like "[BLANK_AUDIO]"), plus configured filler words, then collapses whitespace. — [TranscriptionOutputFilter.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Recording/Processing/TranscriptionOutputFilter.swift)
- The vocabulary prompt string is `"Important Vocabulary: w1, w2, …"`. — [CustomVocabularyService.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Dictionary/Workflows/CustomVocabularyService.swift)
- The LLM system template covers:
  - Tagged inputs: `<TRANSCRIPT>`, `<CUSTOM_VOCABULARY>`, `<CURRENTLY_SELECTED_TEXT>`, `<CLIPBOARD_CONTEXT>`, `<CURRENT_WINDOW_CONTEXT>`.
  - Rules: use context "only to improve transcription accuracy. Never copy unspoken information", treat questions in the transcript as content rather than instructions, handle self-corrections ("actually", "scratch that"), turn spoken punctuation into punctuation, write numbers as digits, and "never guess unclear values".
  - Few-shot examples.
  - [AIPrompts.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Core/Enhancement/AIPrompts.swift)
- Screen context comes from OCR of a screen capture (`ScreenCaptureService`), selected text and clipboard, each toggled per configuration. Utterances of ≤3 words skip enhancement when `SkipShortEnhancement` is on (default threshold 3). — [AIEnhancementService.swift L115-167](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Enhancement/Workflows/AIEnhancementService.swift), [TranscriptionPipeline.swift L165-170](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Recording/Workflows/TranscriptionPipeline.swift)
- Learning from corrections: an "AutoLearn" subsystem reads the target field through the Accessibility API after pasting (`AutoLearnAXTextReader`, `AutoLearnFocusObserver`). `CorrectionDiffEngine`/`FinalSnapshotDiffEngine` diff the pasted text against what the user edited it to, and an LLM (`AutoLearnAIReviewer`) reviews the candidates before they become word replacements (`WordReplacementStore`) in a review panel. — [Features/Dictionary/AutoLearn/](https://github.com/Beingpax/VoiceInk/tree/main/VoiceInk/Features/Dictionary/AutoLearn)

**OpenWhispr (Electron, Windows/macOS/Linux)**
- Local Parakeet runs through a **sherpa-onnx WebSocket server sidecar**, which matters for Rflow because it's the same engine.
  - Audio is skipped entirely when overall `rms < 0.001`.
  - If the decode of **audible** audio comes back empty, it is **retried once**. The code comment reads: "An empty decode of audible audio silently amputates the transcript (#1435: dictation openings dropped)".
  - Long audio is split into fixed-length segments.
  - The online (streaming) server runs with `--end-tail-padding=0.6` seconds.
  - [parakeetServer.js L33, L151-200](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/parakeetServer.js), [parakeetWsServer.js L35, L150](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/parakeetWsServer.js)
- Mic handling:
  - The mic is warmed with `MIC_WARM_TTL_MS = 5000`, because "mic drivers go cold again after idle". The cold-open worst case is documented as 10–15 s.
  - A prepared capture starts on hotkey key-down with a pre-roll buffer (`PRE_ROLL_MAX_AGE_MS = 2000`; stale pre-roll is discarded).
  - [micWarmState.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/micWarmState.js), [preparedMicCapture.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/preparedMicCapture.js)
- getUserMedia is called with `echoCancellation:false, noiseSuppression:false, autoGainControl:false`. The code comments give the reasons: "Chromium's AGC on Windows mutates the system mic volume via WASAPI (#476)" and "noise suppression off to avoid latency and speech distortion". — [audioManager.js ~L1050-1056](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/audioManager.js)
- The speech gate (`localSpeechGate.js`) uses RMS and peak thresholds (`SILENCE_RMS 0.002`, `SPEECH_WINDOW_RMS 0.003`, `PEAK 0.02`). Its AudioContext can stall on Windows with Bluetooth headsets, which caused false "No audio detected" (#1125). — [localSpeechGate.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/localSpeechGate.js), [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Changelog items relevant to accuracy:
  - "No more clipped first words. Cold microphone opens could swallow the beginning of a recording; the mic is warmed" (#845, #1493).
  - "VAD is now opt-in" (off by default).
  - "Realtime streaming waits for the transcript tail … no longer races a fixed delay" (#1573).
  - The local engine now decodes a **raw 16 kHz copy kept from mic open (pre-roll included)** instead of Opus-compressed WebM. Raw and Opus audio "differed in about one word in a hundred, in the raw copy's favour" (#2179).
  - sherpa-onnx 1.13.8: "Cohere Transcribe now returns an empty result for silent audio instead of hallucinating" (#1951).
  - [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Whisper-specific handling:
  - `entropy_thold 2.8` and `logprob_thold -1.25` (the whisper.cpp defaults are 2.4 and -1.0). These "cut the hallucinated-tail rate from 2.25% to 0.06% over 4,814 real dictations" ("Thank you for watching").
  - Dictionary-echo detection: whisper.cpp "can echo its prompt instead of transcribing". Such output is retried without the dictionary and without VAD (#1960).
  - The Whisper prompt is limited to about 224 tokens. Local whisper.cpp keeps the *last* 224 tokens (#1632).
  - [whisperServer.js L35-47](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/whisperServer.js), [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Auto-learn from corrections exists. After #2178, "corrections that resolve to a common English word are no longer learned", because "why"→"what" swaps polluted the dictionary. Screen context is sent as a screenshot (max edge 1568 px) to vision LLMs. — [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md), [screenContextCapture.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/screenContextCapture.js)

**whisper-writer (savbell)**
- Uses WebRTC VAD at aggressiveness 2 with 30 ms frames. `silence_duration` defaults to 900 ms, and the VAD starts after a 150 ms delay "to avoid mistaking the sound of key pressing for voice". Recordings shorter than `min_duration` (default 100 ms) are discarded. faster-whisper options are `initial_prompt`, `condition_on_previous_text`, `temperature` and `vad_filter`. — [result_thread.py L115-181](https://github.com/savbell/whisper-writer/blob/main/src/result_thread.py), [transcription.py](https://github.com/savbell/whisper-writer/blob/main/src/transcription.py), [config_schema.yaml](https://github.com/savbell/whisper-writer/blob/main/src/config_schema.yaml)

**WhisperLive (collabora)**
- Offers server-side Silero VAD (`use_vad`), `initial_prompt`, and `hotwords`, a comma-separated string "passed directly to faster-whisper's keyword boosting". — [README](https://github.com/collabora/WhisperLive/blob/main/README.md)

**nerd-dictation (ideasman42)**
- Uses the VOSK (Kaldi) engine, with optional "numbers as digits" conversion that handles recited sequences such as phone numbers, and `--vosk-grammar-file` to restrict the vocabulary. — [readme.rst](https://github.com/ideasman42/nerd-dictation/blob/main/readme.rst)

**Vibe (thewh1teagle)**
- Supports Whisper, Nemotron 3.5 and Parakeet TDT v3, plus speaker diarization. It is a file-transcription app rather than a hotkey dictation app. — [README](https://github.com/thewh1teagle/vibe/blob/main/README.md)

**Whispering / Epicenter**
- Has a VAD-triggered recording mode (`toggleVadRecording`, states "armed and waiting for speech") alongside manual recording. — [apps/whispering/src/lib/commands.ts](https://github.com/EpicenterHQ/epicenter/blob/main/apps/whispering/src/lib/commands.ts), [recording-states.ts](https://github.com/EpicenterHQ/epicenter/blob/main/apps/whispering/src/lib/constants/audio/recording-states.ts)

**Superwhisper (commercial)**
- Vocabulary:
  - "Keep vocabulary lists short … A long list can hurt transcription."
  - "The vocabulary prompt sent to the transcription model can sometimes lead to confusion."
  - Text replacements are applied after transcription.
  - [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt)
- Hallucinations: "Enable Silence Removal … removes most silence-related hallucinations". Context is limited to the modes that need it because unrelated context can mislead the AI. — [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt)
- Context types:
  - Selected text, captured when recording starts.
  - Clipboard, copied within 3 s before or during dictation.
  - Application context ("text from active input fields, names, and title from your active window"), captured after transcription and before AI processing.
  - [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt)
- Other settings: "Automatic volume adjustment" so quiet input isn't read as silence, pinning a microphone "to prevent switching when Bluetooth connects", and "Voice Model Active Duration" (10 s–1 h) to keep the model loaded. Local models include Whisper sizes, Parakeet Multilanguage (494 MB) and Parakeet Realtime. — [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt)

**Wispr Flow (commercial)**
- Context Awareness collects "app info, textbox contents (before, selected, and after the cursor), on-screen text, variable and file names in coding apps, [and] conversation history". It uses names visible on screen, such as email recipients, "to recognize proper nouns and preserve capitalization", and it "matches casing, spacing, and punctuation to surrounding text". — [Wispr docs: Context Awareness](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness)
- Apps are sorted into Email, Work messaging, Personal messaging or Other for tone. Password, sensitive and numeric-only fields and URL bars are excluded. — [Wispr docs](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness)
- The docs say context "works from text and does not take screenshots". A Willow-authored comparison claims Flow captures "periodic screenshots". The two conflict, and I prefer the official docs. — [Wispr docs](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness); contradicted by [Willow blog](https://willowvoice.com/blog/wispr-flow-review-voice-dictation)
- The pipeline is ASR plus Llama-based transcript enhancement on Baseten, running "end-to-end in under 700 milliseconds", optimised for p99 latency. — [Baseten case study](https://www.baseten.co/resources/customers/wispr-flow/)
- Personal Dictionary: "If you correct a transcription by typing over it, Flow notices and adds the corrected spelling automatically" (secondary source). — [Search result summary of Wispr/third-party guides](https://sidsaladi.substack.com/p/wispr-flow-101-the-complete-guide)

**Aqua Voice**
- Uses Avalon, a proprietary ASR model launched on 2025-08-22 that replaced a Whisper-based pipeline. It was trained on "human-computer interaction speech (prompts, code, email)". This comes from a secondary review. — [Spokenly review](https://spokenly.app/blog/aqua-voice-review), [aquavoice.com](https://aquavoice.com/)

**Willow Voice**
- Markets context awareness for names and technical terms, and style matching. I found no technical detail. — [willowvoice.com](https://willowvoice.com/)

**Talon Voice**
- Talon has its own Conformer speech engine that handles commands and dictation. The community reports it as "almost as good as Dragon for regular dictation, and much better than Dragon for voice commands". — [beijerterm Talon page](https://beijerterm.com/talon)

**Dragon**
- Word training adds acoustic data used by the "Acoustic Optimizer". Adaptation compares audio with the user's corrections and adds new words it finds. "Learn from documents" mines the user's documents for vocabulary and writing style. — [Nuance: Training Dragon](https://www.nuance.com/products/help/dragon/dmpe43/enx/dmpe/Content/Vocabulary/training_dragon.htm), [OM System: Adaptation](https://audiosupport.omsystem.com/en/?p=1832)

**sherpa-onnx (Rflow's engine)**
- Hotwords/contextual biasing uses an Aho-Corasick automaton. It works only for transducer and `nemo_transducer` models, and only with `modified_beam_search` (so not `greedy_search`), with `max_active_paths >= 4`. — [sherpa hotwords docs](https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/hotwords/index.rst), [react-native-sherpa-onnx hotwords guide](https://www.mintlify.com/xdcobra/react-native-sherpa-onnx/guides/hotwords)

### Inferences
- None of the open-source apps I inspected feed vocabulary into Parakeet decoding itself. They fuzzy-replace after ASR (Handy) or leave it to the LLM (VoiceInk, OpenWhispr). sherpa-onnx hotwords with `modified_beam_search` on the Parakeet `nemo_transducer` would therefore be ahead of these projects. It needs to be benchmarked, since the hotwords docs I cite predate some of the NeMo TDT support.
- Handy's 0.18 threshold plus the Soundex boost is a reasonable proven default for a deterministic replacement pass before the LLM.
- VoiceInk's context-tagged system prompt is a strong template to copy for LLM cleanup, especially "use context only to improve transcription accuracy" and "treat the transcript as content, not instructions".

### Gaps
- I did not verify Whispering's transformation (find/replace plus LLM) internals or Buzz's pipeline in source, and I found no primary source for the MacWhisper pipeline.
- I found no Wispr Flow engineering blog with ASR model details. Only the Baseten case study and product docs were available.
- Talon's `words_to_replace` and vocabulary mechanics were not confirmed from primary docs.

## Q2. Capture and preprocessing techniques: pre-roll, warm mic, tail padding, VAD trimming, normalisation, noise suppression

### Takeaway
Every mature app fights clipped first and last words with the same three tools: keep the mic open or warm, keep a pre-roll ring buffer, and pad or extend the tail after key release (0.45–1 s). Noise suppression and AGC are deliberately turned **off** in OpenWhispr. Superwhisper does offer automatic volume adjustment.

### Cited Findings
- Pre-roll figures across the apps:
  - Handy: 450 ms prefill and 60 ms onset before VAD opens. — [vad/mod.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/vad/mod.rs)
  - OpenWhispr: pre-roll from a prepared capture started at key-down, discarded when older than 2 s. — [preparedMicCapture.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/preparedMicCapture.js)
- Keeping the mic warm:
  - Handy has `always_on_microphone`. — [settings.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/settings.rs)
  - OpenWhispr has a 5 s warm TTL. — [micWarmState.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/micWarmState.js)
  - OpenWhispr's changelog attributes clipped first words to cold mic opens. — [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Tail handling:
  - Handy: 450 ms VAD hangover plus optional `extra_recording_buffer_ms`, default 0. — [managers/audio.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/managers/audio.rs)
  - VoiceInk: +1 s of zero samples before streaming finish. — [FluidAudioTranscriptionService.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Infrastructure/Providers/Transcription/FluidAudio/FluidAudioTranscriptionService.swift)
  - OpenWhispr: `--end-tail-padding=0.6` on the sherpa online server. — [parakeetWsServer.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/parakeetWsServer.js)
- Minimum-length padding: VoiceInk zero-pads short clips to the model's minimum sample count. — [FluidAudioTranscriptionService.swift L230-232](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Infrastructure/Providers/Transcription/FluidAudio/FluidAudioTranscriptionService.swift)
- Ignoring the key sound: whisper-writer delays VAD by 150 ms. — [result_thread.py](https://github.com/savbell/whisper-writer/blob/main/src/result_thread.py)
- Audio processing:
  - OpenWhispr disables echo cancellation, noise suppression and AGC. — [audioManager.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/audioManager.js)
  - Superwhisper offers "Automatic volume adjustment" and "Remove Silence". — [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt)
- Lossless audio to the local ASR: OpenWhispr found Opus-compressed audio produced slightly worse transcripts, about 1 word in 100. — [CHANGELOG #2179](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Handy mutes system output while recording (`mute_while_recording`) so speaker audio doesn't bleed into the mic. — [managers/audio.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/managers/audio.rs)

### Inferences
- For Rflow: keep the WASAPI stream open, or warm it on hotkey down; keep a ring buffer of about 300–500 ms; continue capturing about 200–450 ms after release (or pad about 0.5–1 s of zeros); and pass raw float PCM to sherpa-onnx. Avoid noise suppression and AGC by default.
- VAD that drops interior silence is optional. OpenWhispr made VAD opt-in after it caused clipping, so if Rflow uses it, it should only trim the ends and keep generous padding.

### Gaps
- No project published an A/B accuracy measurement of pre-roll or tail-padding values. The chosen numbers appear to be empirical.

## Q3. Model choice and what users find most accurate on CPU

### Takeaway
On CPU, Parakeet TDT 0.6B is the de facto default. v2 is slightly better for English and v3 is multilingual. Users describe accuracy versus Whisper as roughly comparable, with Parakeet much faster. The apps expose a model picker with sizes and downloads.

### Cited Findings
- Open ASR Leaderboard WER: Parakeet v2 6.05% vs v3 6.34%. Parakeet v3 is about 6.3% vs about 7.4% for Whisper Large V3 (secondary source). — [Spokenly: Parakeet models](https://spokenly.app/blog/parakeet-models), [HF model card v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- On HN, users say Parakeet "feels much more accurate in practice than whisper" and others say it's "not really more accurate … but it's much faster". It runs faster than realtime on CPU. — [HN thread](https://news.ycombinator.com/item?id=46641361)
- Handy users call Parakeet v3 "stunningly fast (near-instant)" and describe the accuracy differences versus Whisper as "immaterial". — [HN: Handy](https://news.ycombinator.com/item?id=46628397)
- Handy ships Parakeet V3 as "CPU-optimized" and now recommends a Parakeet Unified EN GGUF. VoiceInk and Vibe offer Parakeet TDT v3 and Nemotron streaming. OpenWhispr now recommends "Orukeet" and adds Cohere Transcribe as a local model. — [Handy README](https://github.com/cjpais/Handy/blob/main/README.md), [Vibe README](https://github.com/thewh1teagle/vibe/blob/main/README.md), [OpenWhispr CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)

### Inferences
- For an English-only Windows CPU app, Parakeet TDT v2 (int8) is probably the most accurate choice, with v3 for multilingual users. Rflow should offer both.

### Gaps
- I found no independent CPU benchmark of int8 sherpa-onnx Parakeet versus fp32 accuracy loss for dictation-style audio.

## Q4. Known accuracy pitfalls from issues and docs

### Takeaway
The recurring failures are clipped first words (cold mic, VAD onset), dropped last words (stop too early or decoder not flushed), Whisper hallucinations on silence and prompt echo, Bluetooth delay or low quality, vocabulary lists that are too long, and auto-learn polluting the dictionary.

### Cited Findings
- Handy #1983 (v0.9.6, Windows, Aug 2026): Parakeet Unified/TDT "sometimes miss the last word in a sentence". It still happens on reprocessing with a different model, which points to capture or endpoint handling. The issue is closed without a documented fix. — [Handy #1983](https://github.com/cjpais/Handy/issues/1983)
- OpenWhispr #1435: an empty Parakeet decode of audible audio dropped dictation openings. The fix is to retry. — [parakeetServer.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/parakeetServer.js)
- OpenWhispr #845 and #1493: clipped first words from cold mic opens. — [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Bluetooth:
  - Handy has a 1–2 s delay. — [HN: Handy](https://news.ycombinator.com/item?id=46628397)
  - OpenWhispr's AudioContext stalls on Windows with Bluetooth headsets. — [CHANGELOG #1125](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
  - Superwhisper recommends pinning the mic so connecting Bluetooth doesn't switch it. — [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt)
- Whisper hallucinations:
  - The "Thank you for watching" tails are fixed with entropy and logprob thresholds. — [whisperServer.js](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/whisperServer.js)
  - Prompt echo returns dictionary words (#1960). — [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
  - VoiceInk strips bracketed text. — [TranscriptionOutputFilter.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Recording/Processing/TranscriptionOutputFilter.swift)
- Vocabulary lists: Superwhisper warns long lists hurt accuracy. OpenWhispr found dictionaries silently truncated by prompt-length caps (#1632). — [Superwhisper docs](https://superwhisper.com/docs/llms-full.txt), [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Auto-learn pollution: common-word swaps ("why"→"what") were learned as dictionary entries (#2178). — [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)
- Short utterances:
  - VoiceInk skips LLM enhancement for ≤3 words, optionally.
  - whisper-writer discards clips under 100 ms.
  - VoiceInk pads short audio.
  - [TranscriptionPipeline.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Features/Recording/Workflows/TranscriptionPipeline.swift), [result_thread.py](https://github.com/savbell/whisper-writer/blob/main/src/result_thread.py)
- Numbers and formatting: VoiceInk's LLM prompt says to write spoken numbers as digits and to "never guess unclear values". nerd-dictation has deterministic numbers-to-digits conversion. — [AIPrompts.swift](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/Core/Enhancement/AIPrompts.swift), [nerd-dictation readme](https://github.com/ideasman42/nerd-dictation/blob/main/readme.rst)
- Accessibility side effect: OpenWhispr's auto-learn monitor forced `AXEnhancedUserInterface` on, which broke focus in Chromium apps (#1116). The fix was to monitor read-only. — [CHANGELOG](https://github.com/OpenWhispr/openwhispr/blob/main/CHANGELOG.md)

### Inferences
- For Rflow: log a VAD or tail diagnostic like Handy's `tail_report`, retry empty decodes of non-silent audio, filter auto-learn candidates against a common-word list, and use a UI Automation text reader on Windows as the counterpart to the macOS AX-based auto-learn.

### Gaps
- I did not locate specific Handy issues about first-word clipping. Search results were noisy, and Handy's issue tracker should be searched directly.
- No quantitative data on how much Bluetooth HFP 8/16 kHz audio degrades Parakeet WER.
