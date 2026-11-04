# Corrected EIDETIC: operator-policy comparison protocol

This is an experiment framework, not a result about strategy quality. It does not change
`corrected-loo-v2`, cosine distance, `k=5`, the CsmithEdge seeds, mutation rules, or
the compiler differential oracle. `raw_knn` remains an auxiliary metric.

## Policies

- `random` (`random_uniform_v1`): uniformly sample `m` distinct arms. Reward is logged but does not affect selection.
- `beta-ts` (`beta_ts_eq3_v1`): use the existing `52_ts_state.py` Beta sampling and update. For a complete selected chain `S`, each arm receives `r/|S|`: `alpha += r/|S|`, `beta += 1-r/|S|`.
- `discounted-ts` (`discounted_beta_ts_v1`): sample each arm from its Beta distribution and select top `m`. After **each accepted mutant**, first discount every arm's evidence toward the Beta(1,1) prior: `alpha_a = 1 + gamma*(alpha_a-1)`, `beta_a = 1 + gamma*(beta_a-1)`. Then update each arm in the complete selected chain using the same fractional rule as Beta TS. Default `gamma=0.97`; unsuccessful mutation or rejected program gives no update. `0<gamma<1` is required.

`92_corrected_eidetic.py` owns seed selection, mutation, compiler gate, embedding,
reward, archive and logging for all policies. Each run must have a **new** run directory;
it creates its own state, archive, cursor-equivalent RNG state, cache and results. `--comparison-mode`
requires the canonical 55-arm file and clean_diversify_syntax seed pool; it also rejects
non-v2 reward/view/pipeline, non-`k=5`, or non-split RNG. For real runs it requires
DashScope `text-embedding-v4` at 1024 dimensions. The manifest records a
`comparison_config_sha256` excluding the policy name; it must match among the
three runs of each paired replicate.

`--rng-mode split-v1` creates three independent local streams from `random_seed`: seed
selection and mutation seed streams are *paired by attempt* across policies, while the
operator-selection stream is policy-specific. No stream or mutable state is shared
between runs. The default `beta-ts --rng-mode legacy` preserves the previously tested
single-RNG behavior; comparison runs **must** use split-v1 and therefore will not have
the exact arm sequence of earlier Beta TS pilots.

## Uniform mock command

From the project root, run the following command separately with
`POLICY=random`, `POLICY=beta-ts`, and `POLICY=discounted-ts`. Set `RUN_DIR` to a
different, nonexistent path each time. These commands use **mock** embedding only:

```bash
python3 scripts/92_corrected_eidetic.py \
  --run-dir "$RUN_DIR" \
  --seed-dir data/seeds/diverse/clean_diversify_syntax \
  --arms-file config/arms_list.txt \
  --iterations 3 --max-attempts 20 --m 1 --k 5 \
  --random-seed 73 --calibration-seed 73 --calibration-cap 128 \
  --policy "$POLICY" --discount-gamma 0.97 \
  --rng-mode split-v1 --comparison-mode \
  --reward-version corrected-loo-v2 \
  --view-mode program-content-v2 --embedding-pipeline complete-chunk-v2 \
  --provider mock --model comparison-mock --allow-mock
```

Read the result with `python3 scripts/93_summarize_corrected_run.py "$RUN_DIR"`.
The summary reports rewards in separate fallback/calibrated stages, raw KNN,
selected/attempted/applied arm counts, effective mutation rate, accepted rate,
API requests and usage, elapsed time, and the latest within-archive diversity metrics.
Each accepted event contains its complete `diversity` snapshot.

`coverage_gt_008` means **within the accepted set**, the fraction of programs
whose nearest *other accepted program* has cosine distance >0.08. Higher means
more internal dispersion. This is RQ3's Coverage@0.08 definition. It is **not**
`holdout_hit@0.08`, the fraction of an independent held-out set lying within
distance <=0.08 of the selected set. No independent holdout is supplied to this
online runner; it must be evaluated separately and named differently.

## Actual mock smoke test

Using all 55 configured arms, `m=1`, `k=5`, seed 73 and the command above:

| Policy | Attempts | Accepted | API requests | State updates | Archive entries |
|---|---:|---:|---:|---:|---:|
| random | 3 | 3 | 0 | 3 | 3 |
| beta-ts | 4 | 3 | 0 | 3 | 3 |
| discounted-ts | 5 | 3 | 0 | 3 | 3 |

These are framework checks, **not** diversity or strategy-effectiveness results;
mock embedding is 8-dimensional and has no relation to the real model's geometry.
The three runs have identical model/reward/view/seed/arms configurations and the
same seed and mutation RNG values for attempts 1–3. Their run directories and
state/archive files are distinct.

## Budget for a later real pilot (not authorized or started)

A modest repeated comparison would use 3 policies x 3 independent seeds x 20
accepted programs = 9 runs, with independent run directories and a fixed maximum
of 70 mutation attempts and 80 actual embedding requests per run. This is a
**proposal**, not an instruction to execute. The prior real pilot used 29
requests, 65,581 returned tokens, and about 67.3 seconds of request time for 10
accepted programs. Linear planning gives roughly 522 requests and 1.18 million
returned tokens for 180 accepted programs; the proposed hard ceiling would be
720 requests total. Length variation, invalidity rate, failures, and archive
computing time make these estimates uncertain. Without a provider bill/rate and
future server usage, currency cost is not verifiable. A run must stop rather than
expand either cap. Any real API comparison requires separate explicit approval.
