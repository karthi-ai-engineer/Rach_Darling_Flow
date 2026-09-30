# Post-ASR Correction and Rigorous Accuracy Evaluation for Rflow

Scope: what to do after Parakeet (sherpa-onnx) produces text, and how to measure whether each pipeline change actually helps on one speaker. Research budget was ~17 tool calls; some sub-questions are only partially sourced (see Gaps).

## 1. LLM-based ASR error correction (GER, N-best, phonetic hints, vocabulary, confidence markup)

### Takeaway
LLM correction gives large WER gains when it is given more than the 1-best (N-best lists, selectively retrieved phonetically similar vocabulary, or per-word confidence), but "naive" whole-transcript correction can over-correct and even double WER on out-of-domain data. The best-supported design for Rflow: correct only when/where confidence is low, feed the LLM a short, retrieved, phonetically-matched vocabulary list plus confidence markup, and check the output against guardrails.

### Cited Findings
- HyPoradise (NeurIPS 2023 Datasets & Benchmarks) is the first open benchmark for LLM-based ASR error correction using N-best hypotheses; 334K+ N-best/transcript pairs. — [arXiv 2309.15701](https://arxiv.org/abs/2309.15701v2)
- HyPoradise results with top-5 N-best and LoRA-finetuned LLM (H2T-LoRA): WSJ 4.5%→2.2%, ATIS 8.3%→1.7%, CHiME-4 11.1%→6.6% WER. Models tested: T5 (0.75–3B), LLaMA (7–65B), GPT-3.5-turbo. — [HyPoradise HTML](https://arxiv.org/html/2309.15701v1)
- Oracle bounds (WSJ): baseline 4.5%, best-in-N-best oracle 4.1%, "compositional oracle" (all tokens anywhere in the N-best) 1.2%. So most of the recoverable information is spread across hypotheses rather than in any single one, which is why generative correction beats rescoring. — [HyPoradise HTML](https://arxiv.org/html/2309.15701v1)
- Prompting without finetuning (GPT-3.5, in-context learning): zero-shot on WSJ-dev93 gave 8.5% WER (5.6% relative reduction); with 10 demonstrations, 7.1% (21.1% relative reduction). Few-shot examples help a lot. — [HyPoradise HTML](https://arxiv.org/html/2309.15701v1)
- The LLM can sometimes produce a correct token that is missing from every hypothesis (e.g., "Sinopec" inferred from context). — [HyPoradise](https://arxiv.org/abs/2309.15701v2)
- HyPoradise failure cases: no gain or worse results on LibriSpeech (already-low baseline), SwitchBoard (14.1% vs 15.7%) and CORAAL, attributed to over-fitting and variable utterance length. — [HyPoradise HTML](https://arxiv.org/html/2309.15701v1)
- GenSEC challenge (2024, SLT) Task 1 = post-ASR transcription correction from N-best hypotheses plus recognition scores; participants could re-rank or correct generatively, using open LLMs or APIs. — [arXiv 2409.09785](https://arxiv.org/pdf/2409.09785)
- Named-entity revision pipeline (2025): Whisper → NER (Flair) → pick context entities that have the same entity type *and* sound similar (Double Metaphone) → LLM revises using only those entities. Named-entity WER with Whisper-large-v3 + GPT-4o-mini went 32.3%→22.7% (~30% relative). Non-entity WER stayed at ~7%, so there was little over-correction. The gain held for Whisper small/medium and Canary-1B (e.g., Canary 36.6%→26.8%). — [arXiv 2506.10779](https://arxiv.org/html/2506.10779v1)
- Same paper: giving the LLM the *full* context document raised entity WER to 43.4% (hallucination with long prompts). A summarized context also did worse (38.6%). Replacing entities by phonetic similarity alone, with no LLM reasoning, gave 25.3% vs 22.7% with the LLM. So retrieval must be selective, and the LLM adds value over pure phonetic substitution. — [arXiv 2506.10779](https://arxiv.org/html/2506.10779v1)
- Retrieval variant: the LLM detects named entities, the system retrieves phonetically similar entities from a personal database, and these are fed back for context-aware correction. — [arXiv 2409.15353](https://arxiv.org/abs/2409.15353v1) (search-snippet level only; not fetched in full)
- Confidence markup format that works: `HOW[1.00] MANY[0.85] RAFELLES[0.61]` plus the instruction "Words with lower confidence are more likely to be incorrect." Model: LLaMA-3.1-8B with LoRA (~42M trainable params), trained on 130K pairs. — [arXiv 2509.25048](https://arxiv.org/html/2509.25048v1)
- Confidence-guided results on **Parakeet** outputs: SAP-shared 15.64%→4.95%; SAP-unshared 9.94%→9.47%; TORGO (cross-dataset) 10.83%→10.58%. Naive LLM correction on TORGO *raised* WER from 10.83% to 20.01%. The confidence-aware model made helpful edits 63.8% of the time on low-confidence spans vs 17.5% on high-confidence spans. — [arXiv 2509.25048](https://arxiv.org/html/2509.25048v1)
- Interfacing LLMs with ASR via confidence (Interspeech 2024): only send low-confidence utterances or words to the LLM. LLM correction increased WER on already-good transcripts. A lowest-word-confidence threshold of about 0.7 was a good trade-off, and a sentence-confidence threshold of 0.95 was optimal for Whisper tiny/medium. — [arXiv 2407.21414](https://arxiv.org/pdf/2407.21414); threshold figures from the search summary of [Naderi Interspeech 2024](https://publications.idiap.ch/attachments/papers/2024/Naderi_INTERSPEECH_2024.pdf) (details not verified in full text)
- Jargon benchmark: biased-word WER (B-WER) can be 0.88–0.90 while unbiased WER (U-WER) is 0.06–0.19 on the same data. Putting the correct jargon in the prompt reduced B-WER by 0.50–0.70 absolute. — [LREC 2026 "A Dataset for Evaluating ASR on Specialized Vocabulary"](https://preview.aclanthology.org/ingest-lrec/2026.lrec-main.32/) (search summary; page later returned 404)

### Inferences
- Rflow's LLM step currently gets only the 1-best text, so it cannot use the "compositional" information that gives GER its gains. The cheapest substitutes on sherpa-onnx are (a) per-token confidence (from ys_log_probs, see §3) and (b) phonetic candidate lists from the user's vocabulary. N-best from sherpa-onnx offline transducer greedy search is probably not available without beam search (unverified).
- The "cloud"→"Claude" problem is exactly the case where confidence plus phonetic retrieval helps. If "cloud" has low confidence and Double Metaphone/phoneme distance matches the vocabulary entry "Claude", the prompt can say: `cloud[0.52] (sounds like: Claude?)`. The LLM decides from context, and a real-word error is no longer invisible.
- Recommended prompt design: (1) the transcript with confidence markup only on words below a threshold (e.g., <0.7), (2) at most about 5–20 vocabulary items retrieved per utterance by phonetic similarity, not the full list (full context hurt in 2506.10779), (3) 3–10 few-shot pairs taken from the user's own past corrections (few-shot gave the biggest ICL gain in HyPoradise), (4) an explicit instruction to change only flagged words, fillers and punctuation.
- Gating: skip the LLM when every word is above a high confidence threshold, to avoid over-correction and save latency.
- Guardrails against hallucination: reject the LLM output if its word-level edit distance to the input exceeds X% (for example, more than 30% of words, or more than N words added) and fall back to the deterministic output. Also log every LLM diff so it can be evaluated.
- Latency under 1 s: short prompts (retrieved vocab only), small or fast models (Groq-hosted 8B-class, Claude Haiku / GPT-4o-mini / Gemini Flash class, local 3–8B in Ollama). HyPoradise and 2509.25048 show that 7–8B models can be good correctors, but mainly after finetuning. Zero-shot small models are more likely to over-correct.

### Gaps
- No source found giving measured latency of LLM correction for dictation-length utterances with specific APIs. It needs to be measured in-app.
- No direct 2023–2026 source compared phonetic spelling hints (IPA or "sounds like") in prompts against plain vocab lists with numbers. 2506.10779 uses phonetics only for retrieval/filtering.
- "Whispering LLaMA" (cross-modal GER) was not fetched. It requires audio features inside the LLM, which is not practical for Rflow anyway.
- GenSEC Task 1 winning-team numbers were not retrieved.

## 2. Deterministic correction (phonetic fuzzy matching, replacement rules, learning from edits)

### Takeaway
A deterministic phonetic matcher over the user's vocabulary is a strong, zero-latency baseline. In the named-entity study, phonetic replacement alone captured most of the LLM's gain (25.3% vs 22.7% entity WER from 32.3%). It should run before the LLM, and its candidates should feed the LLM.

### Cited Findings
- Double Metaphone was used to judge whether a context entity "sounds similar" to an ASR-output entity. Replacing entities by phonetic similarity alone, without an LLM, reached 25.3% entity WER, vs 32.3% baseline and 22.7% with the LLM. — [arXiv 2506.10779](https://arxiv.org/html/2506.10779v1)
- Graph-based phonetic correction frameworks combine phonetic-similarity graphs with contextual language understanding for errors caused by phonetic confusion. — search summary pointing to [arXiv 2601.15397](https://arxiv.org/html/2601.15397v1) (not fetched)

### Inferences
- Implementation for Rflow (C#/Windows): for each vocab entry, precompute Double Metaphone codes for single words and for multi-word spans ("Claude Code" ↔ "cloud code"). Slide over 1–3-word windows of the transcript and score candidates with a combined phonetic-code match and normalized edit distance. Auto-replace only when there is a high-precision match, the word is low-confidence, *and* the word is not a common dictionary word. Otherwise pass the candidate to the LLM as a hint.
- Phoneme-level edit distance with a g2p model (e.g., CMUdict lookup with a small g2p fallback) is more precise than Metaphone for vowels. This is an engineering judgment; no benchmark was found.
- User replacement rules (exact/regex, "always X→Y") should run last so they win over the LLM.
- Learning from user edits: capture the pasted text, and if the user edits within N seconds (or through Rflow's history UI), diff pasted vs edited text. Propose (a) new vocab items, (b) replacement rules after repeated identical fixes, (c) few-shot pairs for the LLM prompt. These diffs are also the best source of real-dictation reference transcripts (§5).

### Gaps
- No published comparison of Soundex vs Double Metaphone vs g2p-phoneme distance for ASR vocabulary correction was found within budget.

## 3. ITN, Parakeet formatting, and sherpa-onnx confidence/timestamps

### Takeaway
Parakeet-TDT-0.6B-v2 already outputs punctuation, capitalization and formatting (NVIDIA says it needs no separate PnC or ITN model), so an ITN/WFST stage is optional. sherpa-onnx exposes per-token timestamps and, for offline transducers, per-token probabilities that can be turned into word confidence.

### Cited Findings
- parakeet-tdt-0.6b-v2: 600M-parameter FastConformer-TDT, English, "automatic punctuation and capitalization," accurate word-level timestamps, "robust performance on spoken numbers." NVIDIA states these models "do not require separate language models, punctuation models, or ITN." — [NVIDIA model card](https://build.nvidia.com/nvidia/parakeet-tdt-0_6b-v2/modelcard)
- sherpa-onnx supports `rule-fsts` / rule FARs (e.g., `phone.fst`, `date.fst`, `number.fst`) applied to output text. The main published ITN FSTs are Chinese (WeTextProcessing-built taggers/verbalizers such as `zh_itn_tagger.fst`). — [HF space config using rule-fsts](https://huggingface.co/spaces/stack86/text-to-speech/commit/ef8e4f3fae9cb04f0ef15e56fda7b33636b9c5b0), [WeTextProcessing docs](https://docsearch.algolia.com/mcp/docs/repo/wenet-e2e/wetextprocessing) (the sherpa ITN doc page was 404 at fetch time)
- Mainstream ITN is FST/WFST-based on hand-curated rules; neural ITN is an alternative. — [Neural ITN, arXiv 2102.06380](https://arxiv.org/pdf/2102.06380)
- sherpa-onnx result objects expose `tokens` and `timestamps`, where `timestamps[i]` is the *start* time of `tokens[i]`. End times are not given directly. — [sherpa-onnx discussion #985](https://github.com/k2-fsa/sherpa-onnx/discussions/985)
- sherpa-onnx added token-level confidence (`ys_probs` / `ys_log_probs`) for offline transducer models and exposed it via JNI/Kotlin/Java. — [pub.dev sherpa_onnx changelog](https://pub.dev/packages/sherpa_onnx_linux/changelog), [react-native-sherpa-onnx types](https://www.mintlify.com/xdcobra/react-native-sherpa-onnx/api/stt/types) (search-snippet level)

### Inferences
- Word confidence: group BPE tokens into words (SentencePiece "▁" marks a word start), then take min or mean of token probs as the word confidence. Min is the usual conservative choice. Word end ≈ next word's start time. Calibrate the threshold on the user's own reading-test data by plotting P(word wrong | conf bin).
- Check whether the C# API Rflow uses exposes `ys_log_probs` for the Parakeet TDT model. If not, it may need a newer sherpa-onnx version (unverified).
- For ITN: rely on Parakeet v2 plus a small hand-written C# rule layer for Rflow-specific formats ("at gmail dot com" → "@gmail.com", "new line", "period"). Porting NeMo English ITN FSTs into sherpa rule-fsts is possible in principle, but no ready-made English rule-fst was found.
- Evaluation implication: because Parakeet emits numerals and punctuation, WER normalization must map numbers↔words (Rflow already does this), or ITN changes will register as fake errors.

### Gaps
- Could not confirm whether Rflow's Parakeet model variant (v2 vs v3, int8) outputs `ys_log_probs` through sherpa-onnx's C API.
- The sherpa-onnx ITN docs page URL tried (`k2-fsa.github.io/sherpa/onnx/itn/`) returned 404.

## 4. Evaluation metrics, normalization, and statistics for one speaker

### Takeaway
Report normalized WER with bootstrap CIs, **plus** B-WER (errors on vocabulary/entity words) and U-WER, CER, and an over-correction count, on fixed recordings replayed offline. With one speaker, resample by *utterance/recording session*, and use paired comparisons on the same audio. 30 sentences only detects large changes. Grow to 100–200+ utterances with held-out splits.

### Cited Findings
- Whisper `EnglishTextNormalizer` removes fillers (hmm, mm, mhm, uh, um), expands contractions ("won't"→"will not"), expands titles ("dr"→"doctor"), normalizes numbers to digits (EnglishNumberNormalizer), maps British→American spelling, strips most punctuation and bracketed text, and preserves currency/% symbols. — [whisper/normalizers/english.py](https://github.com/openai/whisper/blob/main/whisper/normalizers/english.py)
- B-WER (errors on biasing-list/jargon words) and U-WER (other words) separate failure modes that overall WER hides (B-WER 0.88–0.90 vs U-WER 0.06–0.19). — [LREC 2026 dataset paper](https://preview.aclanthology.org/ingest-lrec/2026.lrec-main.32/)
- Bootstrap CIs for ASR WER: Bisani & Ney, ICASSP 2004, with the BootLog tool. — [BootLog](https://www-i6.informatik.rwth-aachen.de/~bisani/bootlog.html)
- Plain utterance-level bootstrap underestimates variance when utterance errors are correlated (same speaker or topic). Blockwise bootstrap resamples blocks instead. Paired statistic: ΔWER = Σ(errors_B − errors_A) / Σ(ref words), computed on each resample, with B = 1000 resamples and a 2.5–97.5 percentile CI (percentile and Gaussian CIs agree for large B). — [Blockwise bootstrap, arXiv 1912.09508](https://ar5iv.arxiv.org/html/1912.09508)
- Dependence among utterances can be modeled explicitly (graphical lasso) to find blocks before bootstrapping. — [arXiv 2209.05281](https://arxiv.org/pdf/2209.05281)

### Inferences
- **Normalization:** keep Rflow's normalizer, but make it Whisper-equivalent (contractions, fillers, British/American spelling, numbers). Apply the *same* normalizer to raw ASR and to every LLM output. Because the LLM removes fillers, filler removal must be in the normalizer, or raw ASR is penalized for words the reference omits. Store the normalizer version with every result.
- **Metrics per run:** WER (S/D/I breakdown), CER, B-WER / vocab-term recall (was each vocabulary word in the reference produced exactly?), U-WER, over-correction rate (words correct in raw ASR but wrong after LLM), LLM change rate, and latency.
- **Statistics:** for each pair of configs on the same recordings, compute per-utterance error counts and a paired bootstrap of ΔWER, resampling whole *sessions* (blocks) when sessions differ in mic/noise and otherwise utterances. Call a change real only if the 95% CI excludes 0. A sign test on per-utterance "better/worse/same" counts is a simple extra check. NIST sclite's MAPSSWE / matched-pairs test is the classical alternative (not fetched this session; see Gaps).
- **Sample size (rough):** 30 sentences × ~12 words ≈ 360 words. At 24% WER, the binomial SE of WER is about √(0.24·0.76/360) ≈ 2.3 points absolute, and more with correlation. Paired designs shrink this a lot, because most errors are shared. Still, detecting a 2–3 point absolute change reliably likely needs about 1,000–2,000 reference words (about 100–200 utterances, 10–20 minutes of speech). This is a back-of-envelope estimate, not from a source.
- **Anti-overfitting:** split the material three ways: (1) *vocab-dev*, used to build and tune vocab, prompts and thresholds, (2) *test*, frozen reading sentences never used for tuning, (3) *real-dictation set*, the user's own saved recordings with corrected transcripts. Vocab suggestions from the reading test must not be evaluated on the same sentences. Rotate or refresh the test set when it is used many times.
- **Conditions:** record the test set with at least 2 mics (headset vs laptop array) and at least 2 noise conditions (quiet, fan/TV), and report each separately. Add a new session on a different day, since voice varies from day to day.
- **Latency:** log end-of-speech (key release/VAD end) → ASR done → LLM done → paste complete. Report p50/p95 over at least 100 dictations. The LLM hop usually dominates, so report latency with and without gating.

### Gaps
- NIST SCTK/sclite docs (MAPSSWE, sign test, Wilcoxon) and jiwer / Open ASR Leaderboard normalizer docs were not fetched within budget. The leaderboard is known to use a Whisper-style English normalizer, but that was not verified this session.
- No source gave a minimum utterance count for single-speaker WER significance. The estimate above is my own calculation.

## 5. Ablation matrix and offline replay methodology

### Takeaway
Freeze audio and references once, then replay every pipeline configuration offline against the same WAVs. That makes each toggle a paired comparison, and gains can be attributed to capture, preprocessing, ASR/biasing, deterministic correction and LLM separately.

### Cited Findings
- Confidence gating changes which utterances reach the LLM and alters WER vs threshold, so the threshold is itself a parameter to sweep. — [arXiv 2407.21414](https://arxiv.org/pdf/2407.21414)
- Context size (full vs selective vocab) changes the direction of the result: full context hurt, selective retrieval helped. — [arXiv 2506.10779](https://arxiv.org/html/2506.10779v1)
- Paired ΔWER bootstrap is the right statistic for comparing configurations on the same utterances. — [arXiv 1912.09508](https://ar5iv.arxiv.org/html/1912.09508)

### Inferences
- **Stored data per utterance:** raw 16 kHz WAV (pre-preprocessing, plus post-VAD if applicable), reference text, tags (mic, noise, session/date, split), and for each config: hypothesis, tokens, timestamps, token probs, LLM prompt, LLM output, and timings.
- **Ablation axes (toggle one at a time from a fixed baseline, then combine the winners):**
  1. Capture: mic A/B, gain/AGC on/off. These must be recorded separately, because the axis cannot be replayed.
  2. Preprocessing: none / denoise / VAD trim / normalization.
  3. ASR: Parakeet version/quantization (fp32 vs int8), decoding method, hotwords/contextual biasing on/off, hotword score.
  4. Deterministic: phonetic vocab matcher off/on (auto-replace vs hint-only), replacement rules on/off.
  5. LLM: off / provider×model / prompt variant (plain, +vocab-all, +vocab-retrieved, +confidence markup, +few-shot) / gating threshold (never, <0.7, always).
- **Report table columns:** config id, WER [95% CI], ΔWER vs baseline [paired CI], B-WER, U-WER, CER, over-correction count, LLM invocation rate, p50/p95 latency, and cost per 100 dictations.
- Keep ASR outputs cached so LLM variants re-run without re-decoding. Pin LLM temperature to 0 and record the model version string. Run cloud LLMs 2–3 times to measure nondeterminism.
- Treat the existing 30-sentence reading test as a quick smoke test and vocab-discovery tool. The authoritative number should come from the larger frozen test set plus real dictation logs.

### Gaps
- No source evaluated single-user dictation pipelines end to end. The matrix design is synthesized from the component papers above plus standard experimental practice.
