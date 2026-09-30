"""The reading test's material: the sentences to read, the test sessions on disk, and a fair word comparison.

The user reads one set of sentences per session (the window's Reading test records them into BENCH_DIR/<date_time>/,
with session.json saying which set and which microphone). sst.evaluate replays the recordings through any setup and
scores them. There are five sets of 30: A and B are for tuning (finding words for "Your words", trying prompts), C to E
are a held-out test that nothing is tuned on, so an improvement measured there is real and not learned by heart.
"""
import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from sst import __version__

BENCH_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "sst" / "bench"
SESSION_FILE = "session.json"

# Everyday dictation with the kind of words this user says: names, tools, tech terms, numbers. Set A is the original
# 30 sentences, so tests read before the sets existed stay comparable.
SENTENCES = [
    "Can you review the pull request before lunch and leave a comment if anything looks wrong?",
    "I merged the fix into main and the release workflow published the new installer.",
    "Please create a merge commit instead of squashing, so the history stays readable.",
    "The tray app shows a small pill at the bottom of the screen while I am speaking.",
    "Let's move the stand-up meeting to Thursday morning and share the notes in Slack.",
    "Open a new branch for phase seven, then push three small commits to GitHub.",
    "Rflow uses the Parakeet model to turn my voice into text on this laptop.",
    "The website on Vercel always links to the latest version of the installer.",
    "I would like to learn Tamil and Japanese, but English comes first for now.",
    "Send the API key to the endpoint only after the test button shows a green result.",
    "Our CI pipeline runs the tests on Windows and checks the code with CodeQL.",
    "Thanks for the quick reply, I will send you the updated draft this afternoon.",
    "The download finished in about 70 seconds and the checksum matched.",
    "Please book a room for 6 people on Friday at 2 o'clock.",
    "If the gateway is slow, the text is typed exactly as it was heard.",
    "I need 20 to 50 names and terms for my personal dictionary.",
    "The microphone on my earbuds sounds worse than the one built into the laptop.",
    "Hold Control and the Windows key, speak, and let go when you are done.",
    "Python makes it easy to write a small script that renames all these files.",
    "We should add a settings file so that people can change the hotkey without the command line.",
    "The model loads in about 2 seconds and transcribes a short sentence almost instantly.",
    "Could you check whether the new version works on the other laptop as well?",
    "Our customers in Japan asked for a Japanese version of the documentation.",
    "I will be working from home tomorrow, so please call me on Teams if it is urgent.",
    "The installer is not code signed yet, so Windows shows a warning the first time.",
    "Deploy the website again after you update the screenshots in the site folder.",
    "My name is Karthi and I build tools that make everyday work a little faster.",
    "Please summarize the three main risks and suggest one next step for each of them.",
    "The quick brown fox jumps over the lazy dog near the riverbank.",
    "Double-check the numbers before the report goes out to the whole team on Monday.",
]

# Some sentences use a look-alike of a name literally ("cloud", "clot", "publishing"): a fix that turns every "cloud"
# into "Claude" must show up as an error, not as a win.
BLOCKS = {
    "A": SENTENCES,
    "B": [
        "Karthi asked Rahul to review the pull request on GitHub before the stand-up.",
        "Can you move the meeting with the design team to next Tuesday afternoon?",
        "The cloud storage bill went up again this month, so let's clean up old backups.",
        "Claude wrote a first draft of the release notes, and I edited the wording.",
        "Push the branch, open a pull request, and ask Rahul for a review.",
        "I'll send the invoice by Friday once the finance team approves the numbers.",
        "Parakeet runs on the laptop's processor, so the audio never leaves the machine.",
        "The doctor said the small clot in his leg should be gone within a few weeks.",
        "Please rename the folder and update the paths in the configuration file.",
        "We tested Ollama with a small Qwen model and it answered in under a second.",
        "Remind me to call the bank about the new credit card tomorrow morning.",
        "The JSON file stores the settings, and the key is encrypted by Windows.",
        "My sister is publishing her first novel next spring with a small press.",
        "Groq and Gemini both have free tiers that are good enough for testing.",
        "I think the problem is the Bluetooth headset, not the microphone settings.",
        "Let's grab coffee after the workshop and talk about the hiring plan.",
        "The Anthropic key goes on the AI cleanup page, next to the model name.",
        "She answered all 12 questions in less than 15 minutes.",
        "Update the README so new users know how to install Rflow without Python.",
        "We need to finish the quarterly report before the board meeting in March.",
        "PowerToys remaps the Menu key so it works like Control and Windows together.",
        "Could you share the slides from yesterday's presentation in the Teams channel?",
        "Vercel builds the site again every time a commit lands on the main branch.",
        "The kids have a school holiday next week, so I'll work shorter days.",
        "Tamil is spoken in southern India, Sri Lanka and Singapore.",
        "Please check the spelling of every customer name in the contract.",
        "The sherpa-onnx library loads the encoder, the decoder and the joiner.",
        "I'm running about ten minutes late because of traffic on the highway.",
        "PyInstaller bundles Python and every library into one folder for the installer.",
        "Thanks again for your help, it made a real difference to the launch.",
    ],
    "C": [
        "Rahul merged the fix, and the CodeQL check passed on the first try.",
        "Please book a table for four at the Italian place near the office.",
        "We keep the backups in the cloud and a second copy on an external drive.",
        "Ask Claude to summarize the meeting notes in five short bullet points.",
        "The hotkey starts recording when I hold Control and the Windows key.",
        "Our flight lands at 7 in the evening, so we will take a taxi to the hotel.",
        "I need a clean install of Windows on the old laptop before I give it away.",
        "The Parakeet model makes fewer mistakes with the built-in microphone.",
        "Could you commit these changes and push them before the end of the day?",
        "The new policy starts on the first of next month for every employee.",
        "Karthi uses a Japanese keyboard layout on the second laptop.",
        "Whisper and Parakeet are both speech recognition models, but they work differently.",
        "Let me know if the delivery arrives damaged, and we will send a replacement.",
        "The API returns an error when the key is missing or has expired.",
        "My grandmother makes the best lemon rice I have ever tasted.",
        "The team is publishing a short blog post about the new release on Monday.",
        "The decoder can take a list of hotwords for every recording.",
        "Please keep your answers short, because the call is only thirty minutes.",
        "The Slack message said the server would be down for maintenance tonight.",
        "We should compare Gemini and Anthropic on the same one hundred sentences.",
        "The garden needs watering every evening while the weather stays this hot.",
        "NVIDIA trained the model on thousands of hours of English speech.",
        "I forgot my password again, so I had to reset it through email.",
        "Open the Rflow window from the Start menu and check the Settings page.",
        "The blood test showed no sign of a clot, so the doctor sent him home.",
        "Can you ask the landlord when the heating will be repaired?",
        "Groq answered in less than half a second, which is fast enough for dictation.",
        "The Vercel preview link shows the new screenshots in dark mode.",
        "We are planning a trip to Chennai to visit family in December.",
        "The quarterly numbers look good, but travel costs are higher than expected.",
    ],
    "D": [
        "The Ollama server has to be running before you load the models.",
        "Please forward the contract to the legal team and copy me in.",
        "Rahul prefers the laptop microphone because the earbuds sound muffled.",
        "I left my umbrella at the restaurant, can you check if they found it?",
        "The CI pipeline runs the tests on Windows for every pull request.",
        "Our dog needs a walk before it gets dark, so I'll log off early.",
        "Claude and Qwen both fixed the misheard words in the test sentence.",
        "The dark clouds over the hills mean it will rain before lunch.",
        "We moved the installer to GitHub Releases so updates download faster.",
        "Could you print three copies of the agenda for the morning meeting?",
        "I want the dictation to spell Tamil names correctly, like Karthi.",
        "The price of vegetables at the market has gone up a lot this year.",
        "Commit the version bump, tag the release, and push the tag.",
        "He was publishing photos from the trip every day on his blog.",
        "The Bluetooth earbuds switch to call quality when the microphone is on.",
        "Let's schedule the interview for Thursday at 3 in the afternoon.",
        "PyInstaller and Inno Setup together build the Windows installer.",
        "The children built a sandcastle and the waves washed it away.",
        "The JSON report lists every misheard word and how often it happened.",
        "Please call the plumber, the kitchen sink is leaking again.",
        "Anthropic's Haiku model is quick enough to clean up a short sentence.",
        "The WASAPI interface shows the real sample rate of each microphone.",
        "I'll be on holiday for two weeks, so please contact Rahul while I'm away.",
        "The code review took longer than expected because of the new tests.",
        "Teams keeps asking me to update, but the update never finishes.",
        "Remember to water the plants and feed the cat while we are away.",
        "PowerToys and Wispr Flow both listen to Control and Windows.",
        "The evening news said the bridge will be closed for repairs until June.",
        "The Python script reads each recording and writes the results to a file.",
        "Thank you for the lovely dinner, we should do it again soon.",
    ],
    "E": [
        "Karthi and Rahul share the same laptop, each with their own profile.",
        "Could you send me the address of the venue for Saturday's party?",
        "The GitHub workflow publishes the installer when a tag is pushed.",
        "The weather forecast says it will snow in the mountains this weekend.",
        "Ask Claude whether the cloud version or the local version is faster.",
        "Please sign the form and return it to the front desk by noon.",
        "Gemini's Flash model is cheap, but it sometimes rewrites whole sentences.",
        "The train was cancelled, so I worked from the cafe near the station.",
        "Parakeet heard the word commit as clot twice in yesterday's test.",
        "We ordered new chairs for the office because the old ones are broken.",
        "The Vercel site shows the latest version number and the download size.",
        "My brother is learning Japanese before his trip to Tokyo in April.",
        "Sherpa-onnx supports hotwords for NVIDIA transducer models.",
        "Please turn off the lights and lock the door when you leave.",
        "The Rflow pill shows a live level while I am speaking.",
        "We are publishing the survey results on the company website next week.",
        "Groq, Ollama and OpenAI all accept the same request format.",
        "The baby finally fell asleep, so please keep your voice down.",
        "Open the Settings page and choose the laptop microphone instead of the headset.",
        "The meeting ran over by twenty minutes, so I missed the bus.",
        "CodeQL found no problems in the new version of the gateway code.",
        "Can you pick up bread, milk and eggs on your way home?",
        "The Qwen model answered faster than the larger model on the same server.",
        "The museum is free on the first Sunday of every month.",
        "Merge the pull request with a merge commit so the history stays clear.",
        "I sent the updated budget to the finance team this morning.",
        "The Tamil film won three awards at the festival last year.",
        "The Python tests use fake microphones, so they never record real audio.",
        "Please let me know which dates work for you in the first week of May.",
        "The audio sounded thin, as if it came through an old telephone line.",
    ],
}
TUNING = ("A", "B")  # the other sets are the held-out test

# Names and tech terms in the sentences, the kind of words people put in "Your words". Errors on them are counted
# apart from errors on other words: a change that fixes names can then be seen even when the total barely moves, and
# a change that inserts them where they weren't said (cloud -> Claude) shows up too.
TERMS = """Karthi Rahul GitHub Claude Parakeet Rflow Vercel Tamil Japanese CodeQL sherpa-onnx PyInstaller Ollama Qwen Groq
Gemini Anthropic OpenAI PowerToys Bluetooth WASAPI JSON API README NVIDIA Whisper Wispr Slack Teams Python commit hotkey
hotwords Haiku Chennai Inno CI""".split()

# Common words: when they're misheard, adding them to "Your words" wouldn't help.
_COMMON = set("""a an and are as at be but by can could did do does for from had has have he her his i if in into is it
its me my no not of on or our she so that the their them then there these they this to too was we were what when
which while who will with would you your""".split())
FILLERS = {"um", "umm", "uh", "uhm", "erm", "hmm", "mm"}
SAME = {"ok": "okay", "ctrl": "control"}  # the same word written two ways ("Control" is often written "Ctrl")
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen " \
        "eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + _number_words(rest))
    rest = n % 1000
    return _number_words(n // 1000) + " thousand" + ("" if rest == 0 else " " + _number_words(rest))


def words(text: str) -> list[str]:
    """Normalised words for a fair comparison: case, punctuation and hyphens don't count, "70" equals "seventy"."""
    text = text.lower().replace("’", "'").replace("-", " ")
    for pattern, full in _CONTRACTIONS:  # "I'll" and "I will" are the same answer; Parakeet often writes the long form
        text = re.sub(pattern, full, text)
    text = text.replace("'", "")
    out = []
    for token in re.findall(r"[a-z]+|\d+", text):
        if token.isdigit() and int(token) < 1_000_000:
            out.extend(_number_words(int(token)).split())
        else:
            out.append(SAME.get(token, token))
    return [w for w in out if w not in FILLERS]  # the sentences have no fillers; hearing one isn't a mistake


# Only the unambiguous ones: "'s" and "'d" can mean two things (it's / its, I'd = I would or I had).
_CONTRACTIONS = [(r"\bwon't\b", "will not"), (r"\bcan't\b", "can not"), (r"\bcannot\b", "can not"),
                 (r"\blet's\b", "let us"), (r"n't\b", " not"), (r"'re\b", " are"), (r"'ve\b", " have"),
                 (r"'ll\b", " will"), (r"\bi'm\b", "i am")]


def compared(reference: str, heard: str) -> tuple[list[str], list[str]]:
    """Both texts as normalised words, with a word written as one or as two joined up ("sandcastle" and "sand
    castle"), so spelling a compound differently isn't counted as two mistakes. Names and terms are not joined: "code
    ql" typed for "CodeQL" is a mistake the user has to fix."""
    ref, hyp = words(reference), words(heard)
    return _join(ref, set(hyp)), _join(hyp, set(ref))


def _join(items: list[str], other: set[str]) -> list[str]:
    out, k = [], 0
    while k < len(items):
        joined = items[k] + items[k + 1] if k + 1 < len(items) else ""
        if joined in other and joined not in _TERM_WORDS and not {items[k], items[k + 1]} <= other:
            out.append(items[k] + items[k + 1])
            k += 2
        else:
            out.append(items[k])
            k += 1
    return out


def term_words(terms=TERMS) -> set[str]:
    """The normalised words of the names and terms ("sherpa-onnx" -> sherpa, onnx), leaving out common words."""
    return {w for term in terms for w in words(term) if w not in _COMMON}


_TERM_WORDS = term_words()


def align(reference: list[str], heard: list[str]) -> list[tuple[str, str]]:
    """Word-by-word alignment (fewest edits): (ref, heard) pairs; "" marks a missing or an extra word."""
    n, m = len(reference), len(heard)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1,
                             cost[i - 1][j - 1] + (reference[i - 1] != heard[j - 1]))
    pairs, i, j = [], n, m
    while i or j:
        # Among equally good moves, pair words that look alike ("tamil" with "them", not with "please"), so the list
        # of misheard words shows what was really confused; unlike words are then a missing or an extra word.
        diagonal = i and j and cost[i][j] == cost[i - 1][j - 1] + (reference[i - 1] != heard[j - 1])
        delete = i and cost[i][j] == cost[i - 1][j] + 1
        insert = j and cost[i][j] == cost[i][j - 1] + 1
        alike = diagonal and (reference[i - 1] == heard[j - 1]
                              or SequenceMatcher(None, reference[i - 1], heard[j - 1]).ratio() >= 0.5)
        if diagonal and (alike or not (delete or insert)):
            pairs.append((reference[i - 1], heard[j - 1]))
            i, j = i - 1, j - 1
        elif insert:
            pairs.append(("", heard[j - 1]))
            j -= 1
        else:
            pairs.append((reference[i - 1], ""))
            i -= 1
    return pairs[::-1]


def errors(reference: str, heard: str) -> tuple[int, int, list[tuple[str, str]]]:
    """(wrong words, words in the reference, the wrong (ref, heard) pairs)."""
    ref, hyp = compared(reference, heard)
    wrong = [pair for pair in align(ref, hyp) if pair[0] != pair[1]]
    return len(wrong), len(ref), wrong


def recordings(folder: Path) -> list[tuple[Path, str]]:
    """The recorded sentences in a test folder: (wav, the sentence that was read), in order."""
    items = []
    for wav in sorted(folder.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if ref.exists():
            items.append((wav, ref.read_text(encoding="utf-8").strip()))
    return items


# ---- test sessions: one folder per set read, named by date and time

def is_session(folder: Path) -> bool:
    return folder.is_dir() and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}", folder.name))


def sessions(root: Path = BENCH_DIR) -> list[Path]:
    """Every test with at least one recording, oldest first. Other folders (other profiles' tests, summaries) don't count."""
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if is_session(p) and recordings(p))


def read_session(folder: Path) -> dict:
    """What session.json says about a test: its set, microphone, audio interface, sample rate, app version. A test
    from before the sets were added read set A."""
    try:
        data = json.loads((folder / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    if data.get("block") not in BLOCKS:
        data["block"] = "A"
    return data


def write_session(folder: Path, block: str, microphone: str, device: dict) -> None:
    """Record which set was read and with what; `device` is Recorder.describe() (device name, host API, rate)."""
    folder.mkdir(parents=True, exist_ok=True)
    data = {"block": block, "microphone": microphone, **device, "version": __version__,
            "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    (folder / SESSION_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def sentences_for(folder: Path) -> list[str]:
    return BLOCKS[read_session(folder)["block"]]


def unfinished(root: Path = BENCH_DIR) -> Path | None:
    """The newest test if it still has sentences to read, so closing the window halfway loses nothing."""
    if not root.exists():
        return None
    newest = max((p for p in root.iterdir() if is_session(p)), default=None)
    return newest if newest and 0 < len(recordings(newest)) < len(sentences_for(newest)) else None


def next_block(root: Path = BENCH_DIR) -> str:
    """The set to read next: the one read completely the fewest times (A first), so a few sessions cover every set,
    and reading them again (e.g. with another microphone) goes round once more."""
    done = {block: 0 for block in BLOCKS}
    for folder in sessions(root):
        block = read_session(folder)["block"]
        if len(recordings(folder)) >= len(BLOCKS[block]):
            done[block] += 1
    return min(BLOCKS, key=lambda block: done[block])  # min() keeps the first of equals: A before B...


def suggest(misheard: dict[tuple[str, str], int], sentences: list[str]) -> list[str]:
    """Names and terms the user said that were misheard, as written in the sentences (e.g. "Tamil", "CodeQL"), most
    frequent first. Only TERMS and words written with a capital inside a sentence count: an ordinary word such as
    "lunch" in Your words would make the recogniser hear it where it wasn't said."""
    spelled, names = {}, term_words()
    for sentence in sentences:
        for k, token in enumerate(re.findall(r"[A-Za-z][A-Za-z']*(?:-[A-Za-z]+)*", sentence)):
            key = token.lower().replace("'", "").replace("-", "")
            spelled.setdefault(key, token)
            if k > 0 and token[0].isupper() and token != "I":
                names.add(key)
    counts: dict[str, int] = {}
    for (said, _heard), times in misheard.items():
        if said in names and said not in _COMMON and len(said) > 2:
            counts[said] = counts.get(said, 0) + times
    return [spelled.get(word, word) for word, _ in sorted(counts.items(), key=lambda item: -item[1])[:20]]
