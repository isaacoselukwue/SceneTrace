# SceneTrace evaluation summary

Generated 2026-10-29T11:30:46.072Z from commit `93dafb1970accaf8a712e148b4b40a69bdf3eb36` with `make evaluate`.

## Real-video evaluation

Status: **not_measured**. Real clips evaluated: **0**.
No owned, consented, labelled real clips exist yet. Real-video performance is not measured.

## Synthetic smoke check (not real-video evidence)

Two drawn test animations, scored against the renderer's ground truth using recorded live model outputs.
This checks the pipeline end to end; it is not a benchmark and includes the development hero clip.

| Arm | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full system | 1 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| geometry only | 1 | 1 | 0 | 0.5 | 1.0 | 0.6667 |

Actor: 1 correct of 1 matched observable events (0 wrong confident assignments).
Questions: 3 useful answers of 4 observable questions; 1 unobservable question(s), correct abstention 1.0; abstention rate 0.4 over 5 asked.

### Other arms on the same clips

A, Muse Spark on the whole video (no tracks, candidates or evidence frames): TP 1, FP 0, FN 0 on visible-certainty events.
D, the same recorded investigator turns with the reviewer removed: 4 useful answers of 4 observable questions, 1 of 1 correct abstentions, 0 wrong assertions.

## Planted faults (validator)

30 planted faults and 10 clean controls built from the recorded real run records.
Deterministic validator: **30/30 caught**, **0/10 false rejects**.

| Category | Planted | Validator caught |
| --- | ---: | ---: |
| actor substitution | 5 | 5 |
| certainty escalation | 5 | 5 |
| nonexistent event | 5 | 5 |
| stale revision | 5 | 5 |
| time substitution | 5 | 5 |
| wrong predicate or citation | 5 | 5 |

Secondary reviewer alone: measured (verdicts from the live run of 2026-10-29T10:37:40.737Z), 22/30 caught, 3/10 false rejects.
Combined: 30/30 caught, 3/10 false rejects.

## Cost and latency (original live runs of the synthetic clips)

Cold analysis: n=2, median 32929.5 ms (min 25430, max 40429); median cost 89112.5 micro-USD.
Warm questions: n=5, median 18347 ms (min 8490, max 31563); median cost 40869 micro-USD.

Failure example: in the recorded occlusion run the secondary reviewer asked for more evidence on the last-sighting claim, so "Where was the bottle last seen?" was answered only with uncertainty messages.
