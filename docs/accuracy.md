# Accuracy plan

How Rflow gets from ~24% word errors on the owner's voice towards the ~6% Parakeet scores on public benchmarks. Researched
on 2026-09-30 (papers, the sherpa-onnx and NeMo sources, other dictation apps, and measurements on the dev laptop); this
is the summary the phases follow. Every step is kept only if the reading test says it helps (Phase 10).

## Where the 24% comes from

- **Not mainly the model.** Parakeet-unified 0.6B averages about 5.9% on NVIDIA's benchmarks
  ([model card](https://huggingface.co/nvidia/parakeet-unified-en-0.6b)); the best open 2B models are only 0.3-0.6 points
  better and much slower on a CPU. It is also robust to ordinary noise: 6.3% clean, 8.2% at 5 dB SNR
  ([v3 card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)).
- **Names and tech terms.** On specialised vocabulary, error rates of 88-90% on the terms against 6-19% on the other words
  of the same audio are typical ([LREC 2026](https://preview.aclanthology.org/ingest-lrec/2026.lrec-main.32/)). The
  owner's errors are exactly these: commit → clot, Claude → cloud, Tamil → Tamar.
- **The capture path** (measured on the dev laptop):
  - Opening the microphone takes 350-450 ms, and Rflow opens it on each key press, so first words can be lost.
  - MME, PortAudio's default on Windows, reports 44.1 kHz for every microphone. It hides that the Bluetooth earbuds deliver
    16 kHz, or 8 kHz in narrowband call mode, which keeps nothing above 4 kHz. 2 of 6 early recordings were like that.
  - The input level on the default path is low.

## Target pipeline

| Stage | Do | Don't |
|---|---|---|
| Capture | WASAPI at the device's own rate, resampled once by Rflow; a warm stream with 300-500 ms kept from before the key press and 200-400 ms after release (opt-in, idle timeout); detect Bluetooth / narrowband and warn; test Windows' default, Speech and RAW modes by word errors | MME; opening the mic cold per press; keeping a Bluetooth mic warm; neural bandwidth extension |
| Conditioning | Measure level, clipping, noise and bandwidth of every recording; remove DC; gain only when very quiet | Neural denoising by default: it made Whisper, Parakeet and Gemini worse in all 40 conditions of one study ([arXiv 2512.17562](https://arxiv.org/abs/2512.17562v1)); AGC; compression |
| VAD | Trim only the ends (~300 ms padding, tuned by testing); skip recognition when there's no speech; retry once when audible audio comes back empty | Cutting pauses inside the dictation |
| ASR | sherpa-onnx hotwords from Your words (modified beam search, BPE modelling unit, a bpe.vocab, words passed per recording), [PR #3077](https://github.com/k2-fsa/sherpa-onnx/pull/3077) | Beam search without hotwords (rarely helps) |
| Word-list correction | Sound-alike matching of low-confidence words against Your words; the user's own rules last | Replacing common words |
| LLM | Only when a word is uncertain; only the few relevant words from the list; uncertain words marked; answers that change too much rejected | Sending every dictation: it doubled word errors on good Parakeet text in one study ([arXiv 2509.25048](https://arxiv.org/html/2509.25048v1)) |

An optional "noisy room" mode (GTCRN from sherpa-onnx, only below ~5 dB SNR, with 30-50% of the original mixed back in)
is the only enhancement worth trying; mixing the original back in is what recovers the loss
([arXiv 2404.14860](https://arxiv.org/html/2404.14860v1)).

## Phases

| Phase | Work | Judged by |
|---|---|---|
| 10 Accuracy lab | Five sets of sentences (A-B tuning, C-E test), session notes, audio measurements, `sst eval` with names-and-terms errors, 95% ranges, degradations | The ranges are narrow enough to see a 2-3 point change |
| 11 Capture | WASAPI, warm stream with lead-in and tail, Bluetooth detection, raw mode, peak to -1 dBFS, retry of empty results | Fewer lost first and last words; no empty results; raw vs Windows mode by reading test |
| 12 Hotwords | bpe.vocab read out of NVIDIA's .nemo, Your words as hotwords (beam 4, score 1.0), a guard against repeated words | Names and terms errors down, other words flat, few terms put in wrongly |
| 13-16 Speech building blocks | The speech model chosen like the AI cleanup: Parakeet, Whisper turbo on this computer, "Scan my computer", cloud and server models (the owner's plan of 2026-10-01; see HANDOFF) | Each model compared on the owner's own recordings with `sst eval --engine` |
| 17 Correction | Word confidence, sound-alike matching, the confidence-gated LLM | Word errors down with no rise on the other words; LLM calls and time |
| later | Learning from the user's edits after pasting | Real dictations as a growing test set |

## Measuring (Phase 10)

- 30 sentences are ~360 words: about ±2.3 points of noise at 24%. Read several sets; 150 sentences (~1,800 words) narrow it
  a lot.
- Tune on sets A and B only; judge on C-E. Suggested words come from A and B, so the test isn't learned by heart.
- Some test sentences use "cloud", "clot" and "publishing" literally, so a fix that forces "Claude" everywhere counts as
  an error ("Terms put in wrongly").
- `sst eval` replays the same recordings through every setup. Recognition is cached per engine configuration.
  `--degrade narrowband` and `--degrade gain:-20` show what a worse microphone would do without recording again.
- A setup is "better" only when its whole 95% range against the first setup is below zero. The range comes from a
  paired bootstrap over the recordings, drawn within each session.

## Results so far (the owner, laptop microphone, 150 sentences)

| Setup | Word errors | Names and terms | Other words |
|---|---|---|---|
| Phase 10 baseline | 9.2% (7.1-11.6%) | 40% | 7.5% |
| Phase 11: peak to -1 dBFS, retry empty | **8.2%** (6.5-10.1%); test sets 6.7% → 6.0% | 40% | 6.4% |
| Phase 12: Your words as hotwords (31 names and terms) | **7.1%** (5.6-8.6%); test sets 6.0% → **5.4%** | **24.5%** | 6.1% |
| The same audio at phone quality (`--degrade narrowband`) | 12.6%, +3.4 points (+1.4 to +5.5) | 50% | 10.5% |

- Set B, read while Bluetooth headphones were connected (the laptop microphone still recorded), lost 5 first words and
  had 2 sentences decode to nothing; the other sets had neither.
- The laptop microphone's audio is processed by Windows or the driver: about a quarter of each recording is exact
  digital silence. A quiet room measured -91 dBFS processed and -60 dBFS in raw mode. Whether raw is better for
  Parakeet is the next reading test.
- Names were the largest problem (Parakeet → Parkit, Vercel → Versal, Groq → Grog, Qwen → Quinn). Hotwords (phase 12)
  halve those errors, but only for the words in Your words: with the owner's 2 words the gain was 8.2% → 8.1%.
- Hotword strength, chosen on the tuning sets (errors there / on the test sets / names put in wrongly): 0.5: 9.8% / 5.7%
  / 2; **1.0: 9.3% / 5.4% / 2**; 1.5: 10.0% / 5.2% / 13 (Claude ×8, Bluetooth ×4); 2.0: 14.0% / 6.9% / 58; 2.5 repeats
  names ("Claude Claude Claude"). Beam search alone didn't help (8.5%).

## Open questions to settle on the owner's voice

- How much of the 24% is names and terms, and how much everything else (the first `sst eval` answers it).
- What narrowband costs Parakeet (`--degrade narrowband` on laptop-mic sets, and a set read with the earbuds).
- ~~Whether the PR #3077 hotwords work with the unified model's decoder~~: they do (phase 12).
- Whether the Python binding exposes token probabilities for word confidence.
- If the other-word errors stay far above 6% after Phases 11-12, the voice or accent itself is the cause, and only another
  model or adaptation would help.

## Not done yet in the bench

- Resampling whole sessions rather than recordings once there are many sessions with different microphones
  ([arXiv 1912.09508](https://ar5iv.arxiv.org/html/1912.09508)).
- Separate counts of substitutions, deletions and insertions, and of words lost at the start or end of a recording (Phase 11
  will need these).
- A test set from real dictations with corrected text.
