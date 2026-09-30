# Audio capture front end on Windows (Rflow) and how it affects ASR accuracy

Scope: host API, Windows audio effects/RAW mode, Bluetooth, sample rate/resampling, levels, DC/HPF, start latency/pre-roll/tail, automatic mic quality ranking. Research done 2026-09-30.

**Local measurements (marked [LOCAL])** were run on the owner's laptop (Windows 11 26200, `C:\dev\rflow\.venv`, sounddevice 0.5.6, PortAudio V19.7.0-devel). Test scripts are in the session scratchpad (`latency.py`, `raw.py`). Only the built-in Intel SST mic array was opened. The Bluetooth mic was NOT opened, so headphone playback was not disturbed. The level and spectrum numbers come from ambient room noise with nobody speaking. Use them to compare configurations, not as speech SNR.

---

## 1. Host API choice (MME vs DirectSound vs WASAPI vs WDM-KS) and Windows effects / RAW mode

### Takeaway
Rflow should switch from MME to **WASAPI shared mode**. WASAPI reports the device's true mixer rate: 48 kHz for the built-in array and **16 kHz for the Bluetooth headset**. MME reports a fake 44.1 kHz for every device and resamples silently. MME's resampler has a documented history of low quality. WASAPI RAW mode (which bypasses APO effects) and the "Speech" stream category can both be set from Python today through sounddevice's hidden `_streaminfo` struct. RAW mode needs `auto_convert=True` or the device's native channel count. RAW vs Speech should be tested A/B on the owner's voice and not assumed.

### Cited findings
- On Vista and later, MME (waveIn/waveOut) is an emulation layer on top of the new audio stack. "A fault in the MME WaveIn/WaveOut emulation was introduced in Windows Vista: if sample rate conversion is needed, audible noise is sometimes introduced." The internal resampler "defaults to a fast integer-based linear interpolation", which was the lowest-quality mode in earlier Windows versions. A hotfix (KB 2653312) for Win7/2008 allowed switching to high-quality mode. — [Wikipedia: Windows legacy audio components](https://en.wikipedia.org/wiki/Windows_legacy_audio_components). (Secondary source. I found no Microsoft statement on current Win10/11 MME SRC quality.)
- The Windows 10 audio-engine sample rate conversion "requires few resources but measures poorly" (audiophile measurement site, not a primary source). — [The Well-Tempered Computer: SRC](https://thewelltemperedcomputer.com/SW/Windows/SRC.htm)
- Without `paWinWasapiAutoConvert`, WASAPI shared mode does not resample, so the app must use the mixer rate. The `auto_convert` flag lets WASAPI "insert system-level channel matrix mixer and sample rate converter" and "only applies in Shared mode". — [PortAudio pa_win_wasapi.h](https://files.portaudio.com/docs/v19-doxydocs-dev/pa__win__wasapi_8h_source.html); [python-sounddevice WasapiSettings](https://python-sounddevice.readthedocs.io/en/latest/api/platform-specific-settings.html)
- sounddevice's public `WasapiSettings` exposes only `exclusive`, `auto_convert` and `explicit_sample_format`. — [python-sounddevice docs](https://python-sounddevice.readthedocs.io/en/latest/api/platform-specific-settings.html)
- PortAudio's `PaWasapiStreamInfo` also has `streamCategory` (Communications=3 … Speech=9 … Media=11) and `streamOption`. `eStreamOptionRaw` means "bypass WASAPI Audio Engine DSP effects, supported since Windows 8.1" and `eStreamOptionMatchFormat` means "force WASAPI Audio Engine into a stream format, supported since Windows 10". Event-driven WASAPI can reach about 2 ms latency, while polling mode gets about 10–20 ms. — [PortAudio pa_win_wasapi.h](https://files.portaudio.com/docs/v19-doxydocs-dev/pa__win__wasapi_8h_source.html)
- `AUDCLNT_STREAMOPTIONS_RAW`: "a 'raw' stream that bypasses all signal processing except for endpoint specific, always-on processing in the APO, driver, and hardware." Minimum OS is Windows 8.1. — [Microsoft Learn: AUDCLNT_STREAMOPTIONS](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/ne-audioclient-audclnt_streamoptions)
- Signal processing modes: "Raw capture streams must not include any time varying or adaptive processing, such as echo control, automatic gain control, or noise suppression. The only audio processing permitted in raw capture is linear equalization to flatten frequency response." Drivers must support at least Raw or Default mode. RAW is optional and only available when `System.Devices.AudioDevice.RawProcessingSupported` is true. Apps choose a category (for example Speech, "input to personal assistant", or Communications), and the driver maps it to a mode. Apps "cannot find out what mode is used". Windows 11 24H2 adds a "Deep Noise Suppression" (ML) effect type. — [Microsoft Learn: Audio Signal Processing Modes](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-signal-processing-modes)
- Chrome turns off hardware noise suppression while its own echo canceller runs, because upstream processing hurts downstream algorithms. This is an example of stacked processing being counter-productive. — [Chrome blog: Disabling hardware noise suppression](https://developer.chrome.com/blog/disabling-hardware-noise-suppression/)
- [LOCAL] Host APIs on this machine: MME, DirectSound, WASAPI, WDM-KS. `sd.query_devices()` default rates:
  - **MME**: 44100 Hz for every input, including the BT headset. Default low input latency is 90 ms.
  - **DirectSound**: 44100 Hz. Low input latency is 120 ms.
  - **WASAPI**: Intel array at **48000 Hz**, BT "Headset (OnePlus Nord Buds 3r)" at **16000 Hz**. Low input latency is 2–3 ms.
  - So MME hides the fact that the earbuds deliver only 16 kHz (and 8 kHz in narrowband mode), and it upsamples to 44.1 kHz for no benefit.
- [LOCAL] RAW mode works through sounddevice with no library changes:
  ```python
  w = sd.WasapiSettings(auto_convert=True)
  w._streaminfo.streamOption = 1   # eStreamOptionRaw
  ```
  - Without `auto_convert`, a mono RAW stream on the 4-channel Intel SST array fails with `Invalid number of channels [-9998]`, because RAW does no channel mixing. `channels=4` works.
  - `streamCategory = 3` (Communications) at 1 channel also failed with the same error.
  - `streamCategory = 9` (Speech) opened and worked at 1 channel.
- [LOCAL] Ambient-noise level on the Intel array (1.5 s, nobody speaking):
  | Configuration | RMS |
  |---|---|
  | WASAPI default category | 4.9e-5 |
  | WASAPI Speech category | 1.0e-2 |
  | MME @16k | 1.5e-5 |
  | RAW+auto_convert mono | 3.5e-3 |

  The Speech category was about 46 dB hotter on noise than the default category. This strongly suggests that the Speech category enables a different processing chain (for example AGC or beamforming) on this Intel SST driver. The runs were noisy and these are single samples, so the direction is meaningful but the exact values are not.

### Inferences
- MME probably costs little accuracy on a clean 48 kHz mic if the driver resamples well. But it is opaque: Rflow cannot see the real device rate (so it cannot detect BT narrowband from metadata), and it adds about 90 ms of buffering. WASAPI shared mode with a native-rate request removes the OS resampler from the path, and Rflow can then resample with a known high-quality filter.
- RAW vs processed is an empirical question for Parakeet:
  - Parakeet was trained on mostly clean, unprocessed speech, which argues for RAW so that no NS or AGC artifacts are introduced.
  - On a far-field laptop array, RAW gives unbeamformed channels, so you lose the array's beamforming and SNR gain. Picking channel 0 or averaging is not the same as beamforming.
  - Recommended A/B on the owner's voice: WASAPI default, WASAPI Speech category, RAW+auto_convert (mono downmix), and RAW 4-channel (average vs best channel). Measure WER with the same text.
- Exclusive mode brings no accuracy benefit for capture. It locks the device away from other apps (Teams, and so on), so avoid it.
- WDM-KS exposes raw kernel pins, including a separate BT "Hands-Free" pin per paired headset. It is brittle and not needed.

### Gaps
- I found no published WER comparison of RAW vs default-mode (APO-processed) capture for any ASR model. I also found no primary Microsoft doc on MME resampler quality in Win10/11.
- I could not verify which APOs (Intel SST, Realtek, Windows Studio Effects) are active per mode on the owner's machine. Apps can query effects through `IAudioEffectsManager` or the FX discovery APO, which Python does not expose. I did not test with speech.
- I found no documentation on whether Windows Studio Effects "Voice Focus" is bypassed by RAW. By definition RAW should bypass it, unless it runs as always-on endpoint processing.

---

## 2. Bluetooth HFP/HSP inputs: detection, bandwidth, ASR impact, side effects

### Takeaway
Any Bluetooth earbud microphone on Windows runs over HFP:
- narrowband 8 kHz (CVSD): about 4 kHz audio bandwidth
- wideband 16 kHz (mSBC): about 8 kHz audio bandwidth
- LE Audio LC3 (Windows 11 24H2+, newer hardware only): 32 kHz "super wideband"

Narrowband input explains the owner's "no energy above 4 kHz" recordings and is a large WER risk for a 16 kHz-trained model. Rflow should (a) detect BT/HFP from device metadata and the signal, (b) warn and prefer the built-in or USB mic, and (c) note that opening the BT mic forces the earbuds into mono call-quality playback.

### Cited findings
- Windows HFP modes:
  - "Narrowband speech: Operation of HFP at an 8 kilohertz (8kHz) sampling rate" (the doc says "SBC codec", but the HFP spec's narrowband codec is CVSD)
  - "Wideband speech: … 16 kHz … mSBC"
  - Wideband has been supported since Win10 1703 and Win11 21H2, with compatibility improved in 22H2.
  - "Some wideband capable audio devices aren't compatible with the Bluetooth radio in select Windows devices, causing these devices to revert to narrowband mode."

  — [Microsoft Learn: Bluetooth Classic Audio](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-classic-audio)
- Endpoints and switching:
  - Windows 10 creates "{Device} Stereo" (A2DP) and "{Device} Hands-Free" (HFP) endpoints.
  - Windows 11 unifies them. Windows selects HFP when "an application opens the input (microphone) endpoint".
  - "When … an application opens the hands-free microphone input … the device switches into HFP mode". On Win10, stereo output is discarded.
  - On Win11, playback is resampled to the 8/16 kHz HFP rate, and when the mic closes, "the device switches back to A2DP momentarily".

  — [Microsoft Learn: Bluetooth Classic Audio](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-classic-audio)
- Microsoft accessory guidelines require HFP 1.8, Wide Band Speech ("shall"), codec negotiation, and mSBC. Echo cancellation and noise reduction on the headset is "should". Headsets therefore often apply their own NR before transmission. — [Microsoft Learn: Bluetooth Classic audio accessory guidelines](https://learn.microsoft.com/en-us/windows-hardware/design/accessory-guidelines/bluetooth-accessory-guidelines/bluetooth-accessory-guidelines-classic-audio)
- Windows 11 LE Audio: previously HFP was "mono … up to 8-16 kHz". The new LE Audio "super wideband stereo" profile uses LC3 at 32 kHz during calls. It needs Win11 24H2, LE Audio + LC3 support on both the PC radio and the headset, and updated drivers. — [TechRadar](https://www.techradar.com/computing/windows/windows-11-is-finally-fixing-poor-sound-quality-with-bluetooth-headphones-and-pc-gamers-will-be-particularly-happy); [heise](https://heise.de/-10626470)
- ASR impact of narrowband speech:
  - Fricatives (/s/, /f/, /sh/) and aspiration carry energy between 4 and 8 kHz, which telephone audio discards.
  - One source reports that 16 kHz-trained models applied to 8 kHz clinical telephone speech "hit a floor at approximately 34% WER".

  — [Gnani.ai research blog](https://gnani.ai/resources/research/how-we-train-acoustic-models-on-14-million-hours-of-telephonic-speech). This is a vendor blog, the setup is not comparable, and it should be treated as indicative only.
- WSJ 20k-word task with a DNN recognizer: narrowband WER was 8.67%, bandwidth-expanded 8.26%, wideband 8.12%. The gap is small, but that system was built to handle NB input. It does not measure a wideband-only model fed NB audio. — [Li et al., Interspeech 2015](https://www.isca-archive.org/interspeech_2015/li15d_interspeech.html)
- 2026 study of ASR on Indic languages, covering telephone codecs, bit depth, resampling and noise:
  - GSM (narrowband with heavy quantization) "consistently degrades performance".
  - Simulated narrowband/wideband cellular codecs and Opus stay "close to the original 16 kHz".
  - "Bandwidth preservation [is] the dominant factor". Downsampling to 8 kHz and 4 kHz with interpolation back up "incurs moderate WER rises".
  - Neural super-resolution *worsened* WER.

  — [arXiv 2606.09335](https://arxiv.org/pdf/2606.09335) (from the search snippet. I could not retrieve the exact tables.)
- Speaker-recognition work also reports that wideband (16 kHz)-trained models "perform poorly on telephony audio with an 8kHz sampling rate due to the missing higher frequency information". — [Sivaraman et al., Odyssey 2020](https://www.isca-archive.org/odyssey_2020/sivaraman20_odyssey.pdf)
- Other apps:
  - Wispr Flow's help centre lists wireless-mic activation delay as a cause of missing first words. It recommends selecting a specific wired or USB mic instead of "Auto-detect (follows computer default)" and pausing after pressing the hotkey. — [Wispr Flow docs: Missing first words](https://docs.wisprflow.ai/articles/3566082841-fix-missing-first-words-in-transcriptions)
  - Handy's docs say Bluetooth mics add "1 to 2 seconds of activation delay" (search-result summary) and offer an Always-On Microphone setting. — [Handy debug docs](https://handy.computer/docs/debug)
- [LOCAL] Detection signals available from sounddevice on this machine:
  - The BT input shows up as "Headset (OnePlus Nord Buds 3r)" under MME, DirectSound and WASAPI.
  - WDM-KS lists each paired BT headset as `Headset (@System32\drivers\bthhfenum.sys,#2;%1 Hands-Free%0;(<name>))`. The `bthhfenum.sys` / "Hands-Free" string is a reliable HFP marker.
  - The **WASAPI default_samplerate for the BT input is 16000** (8000 would mean narrowband). The built-in array reports 48000.

### Inferences
- Detection recipe, cheap and ordered:
  1. **Metadata.** Look up the WASAPI device with the same name as the selected input. If its `default_samplerate <= 16000`, flag it as HFP. A value of 8000 means narrowband. Also match `bthhfenum`/"Hands-Free" in the WDM-KS names, or the "Headset (" prefix, as a hint.
  2. **Signal.** Per recording, compute the fraction of spectral energy above 3.8 kHz (and above 7.5 kHz) over voiced frames, and the 95% spectral rolloff. Energy above 4 kHz at or near the noise floor means NB. A rolloff near 7.5–8 kHz means WB/16 kHz. The owner's 2/6 phone-quality recordings match the "reverted to narrowband" case, or a device that was only NB.
- Policy:
  - Default to the built-in or USB mic even when the Windows default input is a BT headset.
  - Show a warning ("Bluetooth headset mic = phone quality; accuracy will drop; your headphones will also switch to call audio"), with a one-click switch to the best non-BT mic.
  - Let the user override.
  - If the input is a 16 kHz mSBC mic, the accuracy loss should be much smaller than with 8 kHz. Warn softly in that case.
- Do not use neural bandwidth extension as a fix, because the 2026 study found that it hurt WER.

### Gaps
- I found no published WER for Parakeet-TDT (or other FastConformer models) on 8 kHz-upsampled or HFP-recorded audio. This needs a local experiment: take the owner's good recordings, low-pass or resample them to 8 kHz and back, then re-run WER. That isolates bandwidth from other factors.
- I found no documentation of a Windows property that directly exposes the active HFP codec (CVSD vs mSBC vs LC3) to user-mode apps. Rate-based inference is the practical option.

---

## 3. Sample rate and resampling

### Takeaway
Capture at the device's native WASAPI mixer rate (48 kHz for the built-in mic) and resample once to 16 kHz with a known-good filter: soxr or `scipy.signal.resample_poly(x, 1, 3)`. Do not ask MME for 16 kHz or 44.1 kHz. sherpa-onnx's internal resampler is a reasonable windowed-sinc resampler, so feeding it 48 kHz is also acceptable. Explicit resampling mostly buys determinism and the ability to run analysis (bandwidth, SNR) at 16 kHz.

### Cited findings
- sherpa-onnx `OfflineStream::AcceptWaveform`: when `sampling_rate != 16000`, it builds a Kaldi-style `LinearResample` with `lowpass_cutoff = 0.99 * 0.5 * min_freq` and `lowpass_filter_width = 6`. That is a windowed-sinc anti-alias filter with its cutoff just below the target Nyquist. The code also scales samples by 32768 when `normalize_samples` is false, supports only NeMo `per_feature` normalization, and exposes `dither` and `remove_dc_offset` frame options. — [sherpa-onnx offline-stream.cc](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/offline-stream.cc)
- NeMo FastConformer preprocessor (the family Parakeet-TDT uses): `sample_rate 16000`, `normalize: per_feature`, 25 ms Hann window, 10 ms stride, 80 mel features (Parakeet-TDT-0.6B uses 128 mels), `n_fft 512`, `dither 0.00001`. — [NeMo fastconformer_hybrid_transducer_ctc_bpe.yaml](https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/asr/conf/fastconformer/hybrid_transducer_ctc/fastconformer_hybrid_transducer_ctc_bpe.yaml)
- Handy captures at the device rate through cpal and resamples to 16 kHz itself (`FrameResampler`) before VAD and ASR. — [Handy recorder.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/audio/recorder.rs)
- Resampling quality in MME and the Windows audio engine: see Section 1 (linear-interpolation history; "measures poorly").
- soxr and python-samplerate are standard high-quality SRC libraries on PyPI. — [soxr on PyPI](https://pypi.org/project/soxr/0.5.0.post1)

### Inferences
- For ASR, the main resampling risk is aliasing. With linear or nearest interpolation, content above 8 kHz folds into the 0–8 kHz band. Passband ripple is a minor concern. Any proper polyphase or sinc resampler (soxr "HQ", `resample_poly`, or sherpa's width-6 sinc) is more than enough for log-mel ASR. I found no paper that quantifies the WER difference between these good resamplers, and it is expected to be negligible.
- With WASAPI and no `auto_convert`, requesting 16 kHz fails, which is the correct behavior. Open at the WASAPI `default_samplerate`, then resample in Python: 48k to 16k is exactly 1:3, so `resample_poly(x, 1, 3)`. 44.1k needs `160/441`.
- Recording BT HFP at its native 16 kHz or 8 kHz and then upsampling adds no information. Keep the native rate in logs so the bandwidth detector knows.

### Gaps
- I found no benchmark comparing WER after soxr vs sherpa LinearResample vs Windows SRC.

---

## 4. Levels: gain, clipping, too-quiet, normalization, AGC

### Takeaway
Parakeet's preprocessor applies **per-feature (per-mel-bin) mean/variance normalization over the utterance**, so global gain is mostly irrelevant to the model. Moderate peak or RMS normalization before ASR is harmless but does not help much. What matters:
- no clipping (non-linear distortion)
- signal well above the noise, quantization and dither floor
- no pumping AGC or NS artifacts

Rflow should *measure and warn* on these, and not depend on gain correction.

### Cited findings
- NeMo ASR preprocessor defaults include `normalize: per_feature`, log(mel + 2^-24), 0.97 pre-emphasis and Slaney mel. — [NVIDIA Megatron-core NeMo audio preprocessing docs](https://docs.nvidia.com/megatron-core/developer-guide/latest/apidocs/core/core.models.audio.nemo_audio_preprocessing.html); [NeMo audio_preprocessing.py](https://huggingface.co/camenduru/NeMo/blob/main/nemo/collections/asr/modules/audio_preprocessing.py)
- Per-feature normalization is the only NeMo normalization that sherpa-onnx supports. — [sherpa-onnx offline-stream.cc](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/offline-stream.cc)
- NeMo FastConformer dither is 1e-5 (training-time noise floor). — [NeMo config](https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/asr/conf/fastconformer/hybrid_transducer_ctc/fastconformer_hybrid_transducer_ctc_bpe.yaml)
- AGC raises background noise along with speech ("with autoGainControl enabled you should hear background noise amplified"). — [Chrome blog](https://developer.chrome.com/blog/disabling-hardware-noise-suppression/)
- [LOCAL] Ambient RMS on the built-in mic in the default WASAPI and MME paths was about 1.5e-5 to 5e-5 (about -86 to -96 dBFS). This is close to float and dither-scale values. The Speech category path was about 1e-2 (-40 dBFS). If the owner's speech in the default path is also very quiet, the log-mel floor (2^-24) and dither (1e-5) could dominate quiet phonemes. **Check the peak and RMS of the owner's real recordings.**

### Inferences
- Log-mel plus per-feature normalization makes a constant gain an additive offset in log space, which is then removed. So a signal peaking at -30 dBFS and one peaking at -6 dBFS look almost identical to the model, *as long as* the speech stays well above the 1e-5 dither and 2^-24 log floors and the noise floor. Very quiet capture (peak below about -45 dBFS) is where gain starts to matter. Peak normalization to about -3 dBFS before sherpa is cheap insurance. Apply it once per utterance and linearly, never as AGC.
- Per-utterance normalization statistics include silence. A long silent pre-roll or tail shifts the mean and std. Trim leading and trailing silence beyond about 200–300 ms, or at least keep the pre-roll modest.
- Metrics to compute per recording:
  | Metric | Suggested threshold |
  |---|---|
  | clip ratio (fraction of samples with \|x\| ≥ 0.999) | warn if > 0.1% |
  | peak dBFS | warn if < -40 dBFS "too quiet" |
  | speech RMS vs noise-floor RMS (SNR estimate from VAD or energy percentiles) | warn if < 15 dB |

  These thresholds are heuristics, not sourced.
- AGC: avoid any Rflow-side AGC. OS-side AGC (Speech/Communications category) is a candidate to A/B test (Section 1), not a default.

### Gaps
- I found no published measurement of Parakeet or FastConformer WER vs input level or clipping.

---

## 5. DC offset and high-pass filtering

### Takeaway
It is low priority. NeMo's 0.97 pre-emphasis already acts as a first-order high-pass (about -26 dB at DC), and per-feature normalization absorbs stationary low-frequency energy in the lowest mel bins. A gentle 60–80 Hz HPF (2nd-order Butterworth) is harmless and slightly helps with rumble or handling noise. Do not expect a measurable WER gain.

### Cited findings
- NeMo preprocessor uses 0.97 pre-emphasis by default. — [NVIDIA NeMo audio preprocessing docs](https://docs.nvidia.com/megatron-core/developer-guide/latest/apidocs/core/core.models.audio.nemo_audio_preprocessing.html)
- sherpa-onnx exposes Kaldi's `remove_dc_offset` frame option (per-frame mean subtraction) for its feature extractor. — [sherpa-onnx offline-stream.cc](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/offline-stream.cc)
- Windows' "Constant Tone Removal" APO targets hums and fans. It is part of the processed modes and not of RAW. — [Microsoft Learn: Audio Signal Processing Modes](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-signal-processing-modes)

### Inferences
- Pre-emphasis gives 1 - 0.97 = 0.03 gain at DC, which is -30 dB. That is computed, not sourced. Remove DC before peak normalization anyway, so that an offset does not steal headroom or distort clip or level metrics.
- 50/60 Hz hum sits below the lowest useful mel bands, so its effect on WER is likely small unless it is strong enough to affect normalization statistics.

### Gaps
- I found no ASR study quantifying the benefit of an HPF for modern conformer models.

---

## 6. Start latency, first-word clipping, pre-roll and tail

### Takeaway
Opening a stream per key press costs about 350–450 ms on this laptop's built-in mic before any audio arrives, plus about 85–200 ms of leading zeros on WASAPI. Bluetooth reportedly adds 1–2 s. That easily cuts off the first word. Recommended design:
- a **warm, always-open WASAPI input stream** feeding a ring buffer
- about **300–500 ms pre-roll** prepended on key-down
- about **200–400 ms tail** after key-up

Offer a "release mic when idle" option for privacy and battery, and a policy to never keep a BT mic warm (it forces call-quality playback).

### Cited findings
- [LOCAL] Intel SST array, averages over 2 runs:
  | Host API / mode | Open | start() | First callback | Leading zero samples |
  |---|---|---|---|---|
  | MME | 350–436 ms | 0.3–0.5 ms | 34–47 ms | none |
  | WASAPI shared @48k | 373–379 ms | ~1–3 ms | 15–16 ms | **85–92 ms** |
  | WASAPI + auto_convert @16k | ~355 ms | – | 27–29 ms | 87–89 ms |
  | WASAPI Speech category | – | – | – | 188–198 ms |

  Worst-case dead time with open-per-recording is therefore about 0.4–0.6 s on the built-in mic.
- Handy keeps its cpal stream **always-on after `open()`**. Start and stop only gate which samples are processed. It also uses a 2 s ring buffer (`AUDIO_RING_SECONDS = 2`) and flushes the resampler and VAD tail on stop. — [Handy recorder.rs](https://github.com/cjpais/Handy/blob/main/src-tauri/src/audio_toolkit/audio/recorder.rs)
- Handy's user setting "Always-On Microphone: Keep the microphone active at all times for faster recording response. When disabled (the default), Handy only activates the microphone when you press the shortcut, which adds a small delay." — [Handy debug docs](https://handy.computer/docs/debug). Search summaries of Handy docs say "Bluetooth microphones add 1 to 2 seconds of activation delay" and that Always-On works around it. I could not see that sentence on the fetched page.
- Wispr Flow: missing first words are fixed by pausing after the hotkey "especially on wireless mics" and by holding the hotkey until finished. It also offers a live volume bar to verify capture. — [Wispr Flow docs](https://docs.wisprflow.ai/articles/3566082841-fix-missing-first-words-in-transcriptions)
- Windows 11 switches BT headsets to HFP whenever an app opens the mic, and back to A2DP after it closes. — [Microsoft Learn: Bluetooth Classic Audio](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-classic-audio)

### Inferences
- Warm stream plus pre-roll:
  - Keep a WASAPI stream open on the chosen (non-BT) mic. Write into a ring buffer of about 2 s.
  - On key-down, copy the last 300–500 ms and continue.
  - On key-up, keep recording for 200–400 ms, then cut.
  - Trim the silent parts of the pre-roll and tail with a simple energy or Silero VAD, keeping about 150–250 ms of padding, so per-feature normalization is not dominated by silence.
- Privacy trade-off: an always-open stream keeps the Windows microphone-in-use indicator on and shows the app in Settings > Privacy > Microphone as "currently using". Mitigations:
  - make it opt-in, like Handy's default-off
  - auto-close after N minutes idle and reopen on hotkey (accepting one cold start)
  - never write the ring buffer to disk
  - never keep a BT HFP mic warm, because that would hold earbuds in mono call audio all day
- A middle option: pre-open on hotkey *press* but prepend no audio. This saves nothing because the open cost is the problem. A true warm stream is the only fix for about 400 ms of cold-start delay.
- A cold stream on the WASAPI Speech category adds about 100 ms more leading zeros than the default category. That is relevant if the Speech category is adopted.

### Gaps
- I did not retrieve source for Superwhisper, VoiceInk (macOS, AVAudioEngine) or OpenWhispr (Electron `getUserMedia`/MediaRecorder) capture, so I cannot cite their pre-roll or tail values. I found no public numbers for Wispr Flow's internal buffering.
- I did not measure BT HFP cold-start latency locally. I avoided opening the BT mic so as not to disrupt the owner's headphones.

---

## 7. Automatic microphone quality ranking signals

### Takeaway
Rflow can rank the available inputs using cheap metadata plus a 2–3 s calibration recording, "say: the quick brown fox…". Signals, in priority order:
1. is it Bluetooth/HFP
2. effective bandwidth
3. SNR estimate
4. clipping ratio
5. level

### Cited findings
- The basis for bandwidth as the dominant factor: "bandwidth preservation [is] the dominant factor in telephony ASR efficacy". — [arXiv 2606.09335](https://arxiv.org/pdf/2606.09335)
- The 4–8 kHz band carries fricative and aspiration cues. — [Gnani.ai](https://gnani.ai/resources/research/how-we-train-acoustic-models-on-14-million-hours-of-telephonic-speech)
- The WASAPI native-rate and BT naming signals come from local enumeration (Section 2 [LOCAL]). [Microsoft Learn: Bluetooth Classic Audio](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-classic-audio) confirms the "Hands-Free" naming and the 8/16 kHz rates.
- [LOCAL] A simple ">4 kHz energy fraction" computed on the built-in mic varied from 0.003 to 0.79 across configurations on *ambient noise*. This metric must be computed on **voiced speech frames** (VAD-gated) and relative to the noise floor. On silence it is meaningless.

### Inferences
Suggested scorecard. The thresholds are heuristic starting points to tune against the owner's WER, not sourced values.

| Signal | How | Good | Bad |
|---|---|---|---|
| Transport | WASAPI default_samplerate; `bthhfenum`/"Hands-Free" in the WDM-KS name; "Headset (" name | ≥44.1 kHz, non-BT | 8 kHz (NB HFP) = reject; 16 kHz = warn |
| Effective bandwidth | 95% spectral rolloff and energy ratio 4–8 kHz vs 0.3–4 kHz on voiced frames | rolloff ≥ 7 kHz | rolloff < 4 kHz |
| SNR | speech-frame RMS vs 10th-percentile frame RMS (or VAD non-speech) | > 25 dB | < 15 dB |
| Clipping | fraction of samples with \|x\| ≥ 0.999; runs of ≥3 flat samples | 0 | > 0.1% |
| Level | speech peak dBFS | -20 to -3 | < -40 |
| Processing artifacts (optional) | noise floor drops sharply at speech onset (NS/AGC gating) | stable | gating |

- Run the test once per device on first launch or on device change, persist the result, and auto-select the best device (probably the built-in or USB mic). Show why ("Your earbuds: phone-quality 8 kHz – expect ~2–4x more errors") only if Rflow's own experiment supports the multiplier.
- Log the same metrics for every dictation (cheap) and flag outliers. The owner's 2/6 narrowband recordings would have been caught automatically.

### Gaps
- I found no published "mic quality → WER" model to calibrate thresholds. Rflow should fit them from the owner's own recordings, for example by running the same passage on each mic and path and comparing WER.
- I found no standard, lightweight blind SNR estimator for Python that avoids extra dependencies. WADA-SNR is a known option, but I did not verify a maintained package for it.
