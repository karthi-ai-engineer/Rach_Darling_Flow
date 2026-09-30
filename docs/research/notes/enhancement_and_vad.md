# Speech Enhancement, Dereverberation, VAD and Normalization Before ASR (Rflow: Parakeet 0.6B TDT int8, sherpa-onnx, CPU)

## Q1. Does single-channel speech enhancement (denoising) help or hurt WER for large noise-trained E2E models?

### Takeaway
Recent evidence (2022-2026) consistently shows that off-the-shelf single-channel denoising, applied as an independent front-end, usually does not lower WER for large noise-robust models (Whisper, Parakeet, Gemini) and often raises it, even though signal-quality metrics (PESQ, SI-SDR, STOI) improve. The main cause is "artifact" errors (non-linear distortion), not leftover noise. Mixing part of the original noisy signal back in ("observation adding", OA) or applying weaker masking recovers most of the loss, and can give small net gains.

### Cited Findings
- Iwamoto et al. (Interspeech 2022), "How Bad Are Artifacts?": the paper splits SE error into noise, interference and artifact parts with orthogonal projection. It finds the **artifact component is the main cause of ASR degradation**. Observation adding (enhanced + scaled noisy input) raises the signal-to-artifact ratio monotonically and improves WER on both simulated and real CHiME recordings — [arXiv 2201.06685](https://arxiv.org/abs/2201.06685v2); [ISCA](https://www.isca-archive.org/interspeech_2022/iwamoto22_interspeech.html)
  - Setup: Denoising-TasNet front-end with a Kaldi LF-MMI DNN-HMM back-end, which is not E2E. Rough figures from an automated read of the paper, not verified exactly: simulated noisy about 30% WER, enhanced about 33% (worse), enhanced+OA about 20-25%. On real CHiME-3, enhancement alone gave no gain, while OA with weight ω_obs about 0.3-0.8 gave about 20% relative gain — [ar5iv 2201.06685](https://ar5iv.labs.arxiv.org/html/2201.06685)
- Ochiai et al. 2024, "Rethinking Processing Distortions": on WSJ+CHiME single-talker data, noisy input gave 15.9% WER, the SE-enhanced signal (SNR loss) 16.6% (worse), SE + observation adding 13.0%, and SE trained with the new artifact-boosted SDR loss 13.0%. Reducing artifacts helped WER a lot; reducing residual noise had "relatively little impact." OA did not help when the back-end was trained only on the matched SE output, but did help when it was trained on noisy + enhanced audio — [arXiv 2404.14860](https://arxiv.org/html/2404.14860v1)
- Chondhekar et al. (Dec 2025), "When De-noising Hurts": MetricGAN+ (VoiceBank) denoising was tested before **Whisper, NVIDIA Parakeet, Gemini Flash 2.0 and Parrotlet** on 500 medical recordings × 9 noise conditions (40 configurations). **Noisy audio beat enhanced audio in all 40 configurations.** Enhancement added +1.1 to +46.6 points absolute semantic WER. The authors conclude that modern ASR is robust enough on its own and that SE removes useful acoustic features — [arXiv 2512.17562](https://arxiv.org/abs/2512.17562v1)
- Islam et al. (Mar 2026), "When Denoising Hinders": Meta SAM-Audio enhancement before Whisper tiny through large-v3 **consistently increased WER and CER**. The damage was systematic across most files and grew with model size. Examples: Bengali large-v3 WER went from 0.66 raw to 0.77 enhanced; English (MS-SNSD) tiny went from 0.16 to 0.27. The English large-v3 figures reported by the extractor (1.19 → 1.23) look suspect and are not relied on here — [arXiv 2603.04710](https://arxiv.org/html/2603.04710v1)
- Huo, Zhang, Zhang (2026), "Where Speech Enhancement Hurts Recognition": all six enhancers tested on VoiceBank+DEMAND improved SI-SDR, PESQ and STOI, yet this did not carry over to Whisper-large-v3 WER. With FRCRN, Whisper WER went **from 6.44 to 5.57 when only 25% of the magnitude correction was applied (α=0.25), and back to 6.46 with full correction**. The best amount of correction depends on the recognizer and the acoustic condition — [arXiv 2607.11157](https://arxiv.org/abs/2607.11157)
- Wang et al. 2024, "Bridging the Gap": the authors state that NN-based SE "often introduces artifacts … and harms ASR performance, particularly when SE and ASR are independently trained." They add a lightweight bridge module that uses observation addition. A search snippet reports Whisper-base WER reductions of 6.9% (CMGAN), 2.5% (MP-SENet), 9.3% (DEMUCS) and 7.5% (SEMamba), and Whisper-large-v3 reductions of 2.6-6.5%. These appear to be **with their bridge/OA module**, and the snippet does not make clear whether the numbers are relative or absolute. Treat them as unverified — [arXiv 2406.12699](https://arxiv.org/abs/2406.12699)
- de Oliveira, Peer, Gerkmann (2026): modern ASR with large-scale noisy training and embedded LMs tracks human WER better than older ASR, and a **transducer** model gave the most reliable transcriptions. Its noise robustness and use of context make it less sensitive to front-end acoustics — [arXiv 2605.12107](https://arxiv.org/abs/2605.12107)
- Other 2025-2026 work (e.g., "Parallel Time-Band Mixing with Learned Observation-Adding", "Lightweight Front-end Enhancement … Frame Resampling and Sub-Band Pruning") gets WER gains over noisy input with frozen Whisper back-ends. It does this by building OA into the front-end, which shows that gains take ASR-aware design rather than off-the-shelf denoisers — [arXiv 2608.30326](https://arxiv.org/pdf/2608.30326); [arXiv 2509.21833](https://arxiv.org/pdf/2509.21833)

### Inferences
- For Parakeet TDT, a noise-robust transducer, the expected effect of an off-the-shelf denoiser (RNNoise, DeepFilterNet, GTCRN, Maxine/Broadcast, DNS models) at the moderate SNRs of home and office dictation is **neutral to harmful**. Chondhekar et al. tested Parakeet directly and found harm in every condition.
- If denoising is offered at all, it should be **OA-blended** (e.g., output = enhanced + 0.3-0.5 × noisy), or use a softened mask (the α≈0.25 result), and it should be gated to very low estimated SNR.
- System-level noise suppression (Windows "Voice Focus", NVIDIA Broadcast, Bluetooth headset DSP) creates the same artifact risk before Rflow ever sees the audio. It may be worth advising users to turn off aggressive OS or driver noise suppression and A/B test.

### Gaps
- No study found that measures RNNoise, DeepFilterNet3 or GTCRN specifically in front of Parakeet or FastConformer. Chondhekar et al. used MetricGAN+ only.
- No clean published SNR threshold where denoising starts to help a modern large model. Evidence on the crossover (possibly below 0 dB) is anecdotal or specific to one setup.
- No evidence found on NVIDIA Maxine/Broadcast or Windows Voice Focus effects on WER.

## Q2. Dereverberation (WPE / nara_wpe) for single-channel room ASR

### Takeaway
Single-channel WPE is a linear filter, so it produces few artifacts. Historically it gives small but consistent WER gains in reverberant, far-field conditions. For near-field dictation (laptop mic about 0.5 m, earbuds), the benefit is likely marginal, and there is no direct evidence for Parakeet.

### Cited Findings
- Single-channel WPE as a front-end gave absolute WER reductions of 0.3% (dev) and 0.9% (eval) in a reported system — [arXiv 2102.05259 (VACE-WPE)](https://arxiv.org/pdf/2102.05259)
- End-to-end Transformer ASR with WPE preprocessing reported a 41.5% relative WER reduction (to 16.5% WER) in reverberant single-channel conditions (search-snippet level; strongly reverberant simulated data) — [IEEE 9054029](https://ieeexplore.ieee.org/document/9054029)
- Multichannel WPE plus beamforming front-ends give larger gains (e.g., 21.6% relative on multichannel LibriSpeech playback) but need a mic array — [arXiv 2102.11525](https://ar5iv.labs.arxiv.org/html/2102.11525)

### Inferences
- WPE is linear (a multi-tap prediction filter), so it fits the OA/artifact literature: linear processing mostly avoids the non-linear "artifact" errors that hurt ASR. It is the safest enhancement type to try.
- Its gains shrink when reverberation is mild and the ASR was trained with reverb augmentation. Treat it as an optional experimental toggle, not a default.
- nara_wpe is Python/NumPy. Offline single-channel WPE on a 10 s clip should be tens of ms in native code, but this is **not verified**, and no ready ONNX or C++ path in sherpa-onnx was found.

### Gaps
- No 2023-2026 study of WPE before Whisper or Parakeet in near-field rooms.
- No measured CPU cost of single-channel nara_wpe on 10 s audio found.

## Q3. VAD: which one, effect of trimming silence, hallucination, padding, CPU cost

### Takeaway
VAD trimming mainly guards against hallucination and wasted compute on silence and non-speech. Evidence of its value is strongest for Whisper, which hallucinates on non-speech. For push-to-talk, trim only leading and trailing silence with generous padding (about 300-500 ms). Do not cut internal pauses aggressively, because edge clipping is the main risk. Silero VAD and TEN VAD are both cheap (well under 1% of real time on CPU), and sherpa-onnx ships both.

### Cited Findings
- Whisper hallucinates on non-speech audio. Barański et al. (ICASSP 2025) found a recurring set of hallucinated phrases and used a "bag of hallucinations" post-filter to lower WER. Adding non-speech sounds to speech makes hallucination worse — [arXiv 2501.11378](https://arxiv.org/abs/2501.11378)
- A search summary of the same line of work reports that an effective VAD such as Silero significantly reduces both WER and hallucinations compared with weaker VADs. Exact numbers were not extracted — [arXiv 2501.11378](https://arxiv.org/pdf/2501.11378)
- VAD trade-off: a more aggressive VAD rejects more noise but clips quiet word endings, and no setting removes both problems — [forasoft VAD article](https://www.forasoft.com/learn/audio-for-video/articles-audio/vad-dtx-voice-activity-discontinuous-transmission) (vendor blog, low authority)
- faster-whisper / Silero `speech_pad_ms` default is **400 ms**. Users who see clipped fragments are told to raise it to 500-800 ms — [pyvideotrans VAD docs](https://pyvideotrans.com/en/vad); [faster-whisper issue #364](https://github.com/SYSTRAN/faster-whisper/issues/364)
- Silero VAD: under 1 ms per 30+ ms chunk on one CPU thread; about 2 MB model (JIT/ONNX); 512-sample chunks at 16 kHz; 8/16 kHz; MIT license; ONNX runtime supported — [snakers4/silero-vad](https://github.com/snakers4/silero-vad)
- TEN VAD: RTF 0.0136-0.016 on desktop CPUs (Ryzen 9 5900X 0.0150, M1 0.0160); 306 KB library vs Silero about 2.2 MB; 10/16 ms hops at 16 kHz; Windows supported with ONNX available. It claims better precision/recall than Silero and WebRTC VAD and says Silero "suffers from a delay of several hundred milliseconds" at speech-to-silence transitions and misses short pauses. These are vendor claims — [TEN-framework/ten-vad](https://github.com/TEN-framework/ten-vad)
- sherpa-onnx ships VAD for silero-vad and ten-vad, with APIs in C++, Python, C#, Go, JS and more. TEN VAD integration was done by Fangjun Kuang — [HF TEN VAD](https://huggingface.co/TEN-framework/ten-vad); [sherpa-onnx overview](https://p.codekk.com/detail/c++/k2-fsa/sherpa-onnx)

### Inferences
- 10 s of audio is about 312 Silero chunks at under 1 ms each, so under about 30 ms total. At RTF 0.015, TEN VAD takes about 150 ms of CPU for 10 s if run frame by frame. Both are fine, but if run during recording (streaming) the post-release cost is near zero.
- Transducers like Parakeet TDT do not generate text from an internal LM in the way Whisper's decoder does. The hallucination risk on silence is lower but not zero: noise can still produce short spurious tokens such as "yeah" or "mm". No published measurement for Parakeet was found (see Gaps).
- For push-to-talk, trimming only leading and trailing non-speech (pad 300-500 ms), and possibly shortening internal pauses longer than about 2 s down to about 0.5-1 s, is low-risk. Deleting internal pauses entirely risks cutting word boundaries and affects punctuation, since Parakeet predicts punctuation partly from pauses (inference, not verified).
- Silero's reported slow release (the TEN claim) matters little for offline trimming with padding.
- A useful secondary use of VAD: if no speech is detected, skip ASR entirely and paste nothing. This prevents phantom text from accidental key presses.

### Gaps
- No quantitative study of Parakeet / FastConformer-TDT hallucination on silence or noise-only input.
- No study measuring WER change from trimming internal pauses for transducer models.
- WebRTC VAD CPU and accuracy numbers were not re-verified in this pass. It is generally considered less accurate than Silero, per the TEN VAD comparison.

## Q4. Loudness normalization / AGC before ASR

### Takeaway
No direct 2021-2026 evidence was found either way. The models normalize features internally, so gain normalization should be close to neutral. The main practical risk is clipping or pumping, and the main benefit is rescuing very quiet recordings (e.g., Bluetooth HFP).

### Cited Findings
- None found in this research pass with WER numbers.

### Inferences
- NeMo FastConformer preprocessors use log-mel features with per-feature normalization, so a pure scalar gain mostly cancels out. Very low-level input (quantization, noise floor) and clipped input do matter. Peak or RMS normalization to about -20 dBFS RMS with no limiter pumping is a safe "neutral" step. Aggressive AGC or compressors that raise background noise between words could hurt. Unverified; needs A/B testing.

### Gaps
- No paper or benchmark found quantifying loudness normalization or AGC effects on Parakeet or Whisper WER.

## Q5. Evidence on Parakeet / FastConformer noise robustness

### Takeaway
NVIDIA's own model cards show Parakeet TDT 0.6B is quite robust down to about 5-10 dB SNR (MUSAN noise/music) and degrades steeply at 0 dB and below. For typical home and office dictation, SNR is usually above 10 dB, so the room for gain from preprocessing is small.

### Cited Findings
- parakeet-tdt-0.6b-v3 (MUSAN noise/music) avg WER: clean 6.34%; SNR 10 → 7.12% (−12% relative); SNR 5 → 8.23% (−30%); SNR 0 → 11.66% (−84%); SNR −5 → 19.88% (−214%). Training data includes "noise robust data from various sources," but the augmentation method is not specified. The model supports up to 24 min with full attention (A100) — [HF nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- parakeet-tdt-0.6b-v2: clean 6.05%; SNR 50 6.04%; SNR 25 6.50%; SNR 5 8.39% (−38% relative) — [NexaAI mirror of model card](https://huggingface.co/NexaAI/parakeet-tdt-0.6b-v2-MLX/blob/2ee1d4a079690afc5a907a599fc1708b002e40e7/README.md); [NGC](https://catalog.ngc.nvidia.com/orgs/nvidia/-/collections/parakeet-tdt-0.6b-v2/-)
- Chondhekar et al. included NVIDIA Parakeet among the models where denoising (MetricGAN+) hurt in all conditions — [arXiv 2512.17562](https://arxiv.org/abs/2512.17562v1)

### Inferences
- A denoiser would need to recover more than the roughly 1-2 point WER gap at 5-10 dB **without** adding artifacts. Q1 evidence says off-the-shelf denoisers usually fail that test. The only regime with a plausible net gain is SNR ≤ 0 dB (e.g., loud fan, café, TV).
- The int8 quantized model may be slightly less robust than FP32. Not measured.

### Gaps
- No Open ASR Leaderboard noisy-subset numbers retrieved for Parakeet. No reverberation-robustness numbers on the model card.

## Q6. CPU cost, size and availability of enhancement / VAD models (ONNX, sherpa-onnx)

### Takeaway
GTCRN is the only enhancement model that is both tiny and native to sherpa-onnx at 16 kHz. It would add about 0.7 s of processing for 10 s of audio at the published RTF 0.07, which exceeds the 200 ms budget unless run streaming during recording. DeepFilterNet is 48 kHz-only, which needs resampling. VAD cost is negligible.

### Cited Findings
- GTCRN: 48.2K params, 33.0 MMACs/s; CPU RTF 0.07 on i5-12400. VCTK-DEMAND PESQ 2.87 (vs RNNoise 2.29, DeepFilterNet 2.81); DNS3 blind DNSMOS 3.44 (vs RNNoise 3.15). No ASR/WER results reported — [Xiaobin-Rong/gtcrn](https://github.com/Xiaobin-Rong/gtcrn)
- sherpa-onnx supports speech enhancement with GTCRN (`gtcrn_simple.onnx`, `gtcrn.onnx`) and DPDFNet, with C++, Python, C, C#, Go and Swift APIs — [sherpa-onnx docs index (Algolia)](https://docsearch.algolia.com/mcp/docs/repo/k2-fsa/sherpa-onnx); [react-native-sherpa-onnx enhancement docs](https://mintlify.com/xdcobra/react-native-sherpa-onnx/features/speech-enhancement)
- DeepFilterNet (2/3): "only wav files with a sampling rate of 48kHz are supported"; ships a Rust `deep-filter` binary, a Python package and a LADSPA plugin. No RTF on the README page — [Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)
- Silero VAD: under 1 ms per chunk and about 2 MB. TEN VAD: RTF about 0.015 and 306 KB. Both are in sherpa-onnx (see Q3) — [silero-vad](https://github.com/snakers4/silero-vad); [ten-vad](https://github.com/TEN-framework/ten-vad)

### Inferences
- GTCRN at RTF 0.07 on 10 s is about 700 ms if run after key release. It is only viable streaming during capture: it is causal and streaming-capable in sherpa-onnx, so post-release latency is about zero. The sherpa-onnx C# API makes integration straightforward.
- DeepFilterNet3 needs 16→48→16 kHz resampling and, in practice, a Rust/ONNX build. It is higher quality than GTCRN but heavier, and not justified given the Q1 evidence.
- RNNoise is also 48 kHz native (not re-verified here) and scored lowest in quality in the GTCRN comparison.

### Gaps
- No verified CPU RTF for DeepFilterNet3 or RNNoise on x86 in this pass. No measured RTF for sherpa-onnx `gtcrn_simple` vs `gtcrn` on a laptop CPU.
- The sherpa-onnx speech-enhancement doc page URL tried (k2-fsa.github.io/sherpa/onnx/speech-enhancment/) returned 404, so model details were confirmed only through secondary docs.

## Q7. Recommended practical recipe for Rflow

### Takeaway
Adopt: VAD-based leading and trailing trim with padding, plus a no-speech skip. Keep: mild peak/RMS normalization only if input is very quiet. Skip by default: neural denoising and dereverberation. Optional (off by default, A/B tested): GTCRN run streaming during capture, gated on low estimated SNR, and always blended with the noisy input (OA).

### Cited Findings
- Off-the-shelf SE hurt Parakeet/Whisper in all tested conditions — [arXiv 2512.17562](https://arxiv.org/abs/2512.17562v1); [arXiv 2603.04710](https://arxiv.org/html/2603.04710v1)
- Artifacts are the main cause, and OA or weaker magnitude correction recovers WER (e.g., FRCRN + Whisper-large-v3 6.44 → 5.57 at α=0.25; Ochiai 16.6 → 13.0 with OA) — [arXiv 2607.11157](https://arxiv.org/abs/2607.11157); [arXiv 2404.14860](https://arxiv.org/html/2404.14860v1)
- Parakeet degrades only mildly down to 5-10 dB SNR — [HF parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- VAD padding convention 400 ms default in faster-whisper/Silero — [pyvideotrans VAD docs](https://pyvideotrans.com/en/vad)

### Inferences (concrete recipe)
1. **Default pipeline:** 16 kHz mono capture → DC removal and clip check → Silero VAD (or TEN VAD) through sherpa-onnx, run streaming during capture → trim leading and trailing non-speech with **300-500 ms padding** (start about 400 ms) → if total detected speech is under about 250 ms, skip ASR → optionally cap internal silences over 2 s to about 1 s (off by default until A/B tested) → Parakeet. Expected added post-release latency: under 30 ms.
2. **Normalization:** apply scalar gain only when RMS is below about -35 dBFS, targeting about -20 dBFS. No compressor or AGC pumping. Expected neutral; guards against Bluetooth HFP low levels.
3. **Denoise (optional "noisy environment" toggle, or auto):** estimate SNR from VAD speech vs non-speech frame energy. Only when estimated SNR is below about 5 dB (candidate threshold, derived from the Parakeet card knee between 5 and 0 dB; needs validation), run sherpa-onnx GTCRN streaming during capture, then output = enhanced + 0.3-0.5 × original (OA). Never use DeepFilterNet or RNNoise at full strength.
4. **Dereverberation:** skip. Consider single-channel WPE only as an experiment for far laptop-mic setups.
5. **Advise users** to disable OS or driver noise suppression (Voice Focus, Broadcast, earbud "clear voice") or A/B test it, since it adds artifacts upstream.
6. **Validate:** record about 30-50 real dictations per condition (quiet room, fan/HVAC, laptop mic at arm's length, BT earbuds), hand-transcribe, and compare WER for raw vs VAD-trim vs trim+OA-denoise. Adopt denoise only if it wins at low SNR and is neutral at high SNR.

### Gaps
- The SNR gate (about 5 dB) and OA weight (0.3-0.5) are extrapolations from the Iwamoto/Ochiai/Huo findings and the Parakeet noise table, not measured on Parakeet with GTCRN.
