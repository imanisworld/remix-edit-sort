#!/usr/bin/env python3
"""
Step 2 — Detect BPM for every track, with no installs.

Decodes with macOS's built-in `afconvert` (CoreAudio), then finds the tempo from
a spectral-flux onset envelope and its autocorrelation using numpy. No ffmpeg,
no aubio, no Homebrew.

Octave correction is genre-aware, which is the whole trick: a 160 BPM footwork
track autocorrelates just as strongly at 80, so a global guess gets it wrong
half the time. Knowing the genre first makes the choice obvious.

Usage:
    python3 detect_bpm.py [--tracks outputs/tracks.json] [--out outputs/bpm.json]
                          [--limit N] [--redetect] [--jobs 4]
"""

# Infrastructure: mutates only files this module owns (locks / its own temp files).
SAFETY_CLASSIFICATION = "infrastructure"
import os, sys, json, wave, argparse, tempfile, subprocess
from concurrent.futures import ThreadPoolExecutor

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is required (it ships with the system python3 here)")

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "..", "references", "genre-rules.json")))
BPM_RANGES = {k: v for k, v in RULES["bpm_ranges"].items() if isinstance(v, list)}

SR = 22050          # plenty for beat tracking, and 2x faster than 44.1k
HOP, WIN = 256, 1024
ANALYSIS_SEC = 120  # two minutes from the middle is plenty
SKIP_SEC = 30       # skip intros: long ambient/spoken intros wreck the estimate


def decode(path):
    """mp3/m4a/wav -> mono 22.05k wav via CoreAudio. Returns temp path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    r = subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", path, tmp.name],
        capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or os.path.getsize(tmp.name) < 1024:
        os.unlink(tmp.name)
        raise RuntimeError((r.stderr or "afconvert failed").strip()[:160])
    return tmp.name


def load_samples(wav_path):
    with wave.open(wav_path, "rb") as w:
        n, ch, sr = w.getnframes(), w.getnchannels(), w.getframerate()
        start = min(int(SKIP_SEC * sr), max(0, n - int(30 * sr)))
        w.setpos(max(0, start))
        frames = w.readframes(min(int(ANALYSIS_SEC * sr), n - max(0, start)))
    x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if ch == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    return x, sr


def onset_envelope(x, sr):
    """Half-wave-rectified spectral flux — the standard onset novelty curve."""
    nframes = 1 + (len(x) - WIN) // HOP
    if nframes < 16:
        return None, None
    idx = np.arange(WIN)[None, :] + HOP * np.arange(nframes)[:, None]
    frames = x[idx] * np.hanning(WIN)[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.diff(np.log1p(mag * 100.0), axis=0)
    flux[flux < 0] = 0                      # onsets only, not decays
    env = flux.sum(axis=1)
    return env - env.mean(), sr / HOP


def tempo_candidates(env, fps, lo=60.0, hi=200.0, top=4):
    """Autocorrelate the onset envelope; return [(bpm, strength), ...]."""
    n = len(env)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    spec = np.fft.rfft(env, nfft)
    ac = np.fft.irfft(spec * np.conj(spec), nfft)[:n]
    ac /= (ac[0] + 1e-9)

    lag_min = max(2, int(round(60.0 * fps / hi)))
    lag_max = min(n - 1, int(round(60.0 * fps / lo)))
    if lag_max <= lag_min:
        return []
    lags = np.arange(lag_min, lag_max + 1)
    score = ac[lag_min:lag_max + 1].copy()
    # A real tempo also peaks at 2x/3x/4x the lag. Summing those in separates
    # true periodicity from a one-off correlation bump.
    for mult in (2, 3, 4):
        h = lags * mult
        ok = h < n
        score[ok] += 0.5 * ac[h[ok]] / mult

    bpms = 60.0 * fps / lags
    out, taken = [], []
    for i in np.argsort(score)[::-1]:
        b = float(bpms[i])
        if any(abs(b - t) < 3.0 for t in taken):
            continue
        taken.append(b)
        out.append((round(b, 1), round(float(score[i]), 4)))
        if len(out) >= top:
            break
    return out


def fit_to_genre(cands, genre):
    """Pick the candidate — or its half/double — that lands in the genre's range."""
    lo, hi = BPM_RANGES.get(genre or "", BPM_RANGES["_default"])
    centre = (lo * hi) ** 0.5

    best = None
    for bpm, strength in cands:
        for mult, penalty in ((1.0, 0.0), (2.0, 0.06), (0.5, 0.06), (4.0, 0.15), (0.25, 0.15)):
            v = bpm * mult
            if v < 50 or v > 210:
                continue
            in_range = lo <= v <= hi
            dist = abs(np.log(v / centre))     # distance from centre, log space
            fit = strength - penalty - (0.0 if in_range else 0.35 * dist)
            if best is None or fit > best[0]:
                best = (fit, round(v, 1), strength, mult, in_range)

    if best is None:
        return None, 0.0, "no candidate"
    _, bpm, strength, mult, in_range = best
    conf = min(1.0, max(0.0, strength))
    if not in_range:
        conf *= 0.6
    note = "octave-corrected" if mult != 1.0 else ("in-range" if in_range else "out-of-range")
    return bpm, round(conf, 2), note


def analyze(path, genre):
    wav = None
    try:
        wav = decode(path)
        x, sr = load_samples(wav)
        env, fps = onset_envelope(x, sr)
        if env is None:
            return {"bpm": None, "confidence": 0.0, "note": "too short"}
        cands = tempo_candidates(env, fps)
        bpm, conf, note = fit_to_genre(cands, genre)
        return {"bpm": bpm, "confidence": conf, "note": note,
                "candidates": cands, "genre_used": genre}
    except Exception as e:
        return {"bpm": None, "confidence": 0.0, "note": f"error: {e}"}
    finally:
        if wav and os.path.exists(wav):
            os.unlink(wav)


def reconcile(res, tagged, genre):
    """Existing tag vs. detection. They usually differ only by an octave —
    SoundCloud tags trap and ghettotech half-time. Keep whichever value sits in
    the genre's range, and record the disagreement either way."""
    if not tagged:
        return res
    det = res.get("bpm")
    if not det:
        return {**res, "bpm": tagged, "confidence": 0.85, "note": "existing ID3 tag"}
    if abs(det - tagged) / tagged < 0.04:
        return {**res, "bpm": tagged, "confidence": max(res["confidence"], 0.95),
                "note": "detection agrees with existing tag"}
    lo, hi = BPM_RANGES.get(genre or "", BPM_RANGES["_default"])
    octave = abs(det - tagged * 2) / tagged < 0.08 or abs(det * 2 - tagged) / tagged < 0.08
    if octave and lo <= det <= hi and not (lo <= tagged <= hi):
        return {**res, "tag_bpm": tagged, "conflict": "octave",
                "note": f"detected {det}; tag said {tagged} (half/double)"}
    return {**res, "bpm": tagged, "tag_bpm": tagged, "detected_bpm": det,
            "conflict": "disagree", "confidence": 0.5,
            "note": f"kept tag {tagged}; detection said {det}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default="outputs/tracks.json")
    ap.add_argument("--out", default="outputs/bpm.json")
    ap.add_argument("--limit", type=int, default=0, help="stop after N (quick sample)")
    ap.add_argument("--redetect", action="store_true", help="ignore cached results")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    if not os.path.exists(args.tracks):
        sys.exit(f"{args.tracks} not found — run scan_library.py first")
    tracks = json.load(open(args.tracks))["tracks"]

    cache = {}
    if os.path.exists(args.out) and not args.redetect:
        cache = json.load(open(args.out))

    todo = [t for t in tracks if not (t["path"] in cache and cache[t["path"]].get("bpm"))]
    if args.limit:
        todo = todo[:args.limit]

    print(f"{len(tracks) - len(todo)} already cached", file=sys.stderr)
    print(f"Detecting BPM for {len(todo)} tracks ({args.jobs} at a time)…", file=sys.stderr)

    def work(t):
        return t["path"], reconcile(analyze(t["path"], t.get("genre")),
                                    t.get("existing_bpm"), t.get("genre"))

    done = 0
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for path, res in pool.map(work, todo):
            cache[path] = res
            done += 1
            if done % 50 == 0:
                print(f"  …{done}/{len(todo)}", file=sys.stderr)
                json.dump(cache, open(args.out, "w"), indent=2, ensure_ascii=False)

    json.dump(cache, open(args.out, "w"), indent=2, ensure_ascii=False)

    ok = [v for v in cache.values() if v.get("bpm")]
    low = [v for v in ok if v.get("confidence", 0) < 0.35]
    oct_fix = [v for v in ok if v.get("conflict") == "octave"]
    disagree = [v for v in ok if v.get("conflict") == "disagree"]
    print(f"\n  {len(ok)}/{len(cache)} tracks have a BPM", file=sys.stderr)
    print(f"  {len(oct_fix)} octave-corrected away from their old tag", file=sys.stderr)
    print(f"  {len(disagree)} genuinely disagree with their old tag (tag kept)", file=sys.stderr)
    print(f"  {len(low)} low-confidence — review before writing", file=sys.stderr)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
