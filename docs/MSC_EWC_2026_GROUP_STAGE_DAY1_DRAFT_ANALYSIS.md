# MSC at EWC 2026 Group Stage Day 1 draft analysis

Source: [MSC at EWC 2026 Group Stage 1 English VOD](https://www.youtube.com/watch?v=Ew_-LE64e4w)

Analysed timestamps: `1:03:00`, `1:38:10`, `2:33:00`, `3:10:13`, `4:06:16`, `4:38:00`, `5:38:00`, and `6:13:00`.

## Reading the results

- Red is the left side of this broadcast layout; blue is the right side.
- Every list is stored from its team's outer side toward the middle of the screen in the last picking states before `ADJUST`. This makes consecutive list slices align with the configured draft phases. Game 1 is user-confirmed; Games 2–8 were checked against retained or locally extracted broadcast frames.
- `Last pick` is taken from the last one-team `PICKING` state immediately before both middle timers change to the 30-second `ADJUST` state.
- Pick names and last picks are high-confidence because their names are printed on the HUD. Ban names are visual portrait matches against the repository's MSC 2026 hero catalog. `†` marks the few portrait matches that deserve a second independent check before being used as labelled training truth.

## Extracted drafts

### 1. Team Vamos vs Team Falcons MENA — Game 1

- Requested point: `1:03:00`
- Certified final composition: `1:10:26.189`
- Ordering: user-confirmed pre-swap, from each team's outer side toward the middle
- Red / Team Vamos bans: Marcel, Phoveus, Zhuxin, Moskov, Belerick
- Red / Team Vamos picks: Atlas, Guinevere, Eudora, Lapu-Lapu, Claude
- Blue / Team Falcons MENA bans: Fanny, Freya, Hirara, Yu Zhong, Paquito
- Blue / Team Falcons MENA picks: Valentina, Arlott, Akai, Chou, Bruno
- Last pick: Team Falcons MENA (blue) — Bruno

### 2. Team Falcons MENA vs Team Vamos — Game 2

- Requested point: `1:38:10`
- Certified final composition: `1:44:01.995`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / Team Falcons MENA bans: Fanny, Valir, Hirara, Hanzo, Aulus†
- Red / Team Falcons MENA picks: Freya, Chou, Valentina, Alice, Harley
- Blue / Team Vamos bans: Clint, Atlas, Hilda, Baxia, Masha
- Blue / Team Vamos picks: Eudora, Ling, Esmeralda, Minotaur, Melissa
- Last pick: Team Vamos (blue) — Melissa

### 3. Team Falcons PH vs Guangzhou Gaming — Game 1

- Requested point: `2:33:00`
- Certified final composition: `2:38:40.000`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / Team Falcons PH bans: Hanzo, Claude, Fanny, Hayabusa, Yve†
- Red / Team Falcons PH picks: Ling, Dyrroth, Brody, Gloo, Lylia
- Blue / Guangzhou Gaming bans: Hirara, Obsidia, Atlas, Aurora, Lunox
- Blue / Guangzhou Gaming picks: Harith, Esmeralda, Hilda, Zhuxin, Nolan
- Last pick: Guangzhou Gaming (blue) — Nolan

### 4. Guangzhou Gaming vs Team Falcons PH — Game 2

- Requested point: `3:10:13`
- Certified final composition: `3:13:23.570`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / Guangzhou Gaming bans: Martis, Fanny, Esmeralda, Brody, Franco
- Red / Guangzhou Gaming picks: Hirara, Claude, Yve, Paquito, Grock
- Blue / Team Falcons PH bans: Obsidia, Clint, Atlas, Barats, Uranus
- Blue / Team Falcons PH picks: Akai, Eudora, Harley, Moskov, Sora
- Last pick: Team Falcons PH (blue) — Sora

### 5. Team Vitality vs True Rippers — Game 1

- Requested point: `4:06:16`
- Certified final composition: `4:09:14.000`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / Team Vitality bans: Valir, Zhuxin, Guinevere, Esmeralda, Uranus
- Red / Team Vitality picks: Atlas, Akai, Valentina, Paquito, Claude
- Blue / True Rippers bans: Obsidia, Hirara, Clint, Masha, Leomord
- Blue / True Rippers picks: Eudora, Ling, Karrie, Alice, Badang
- Last pick: True Rippers (blue) — Badang

### 6. True Rippers vs Team Vitality — Game 2

- Requested point: `4:38:00`
- Certified final composition: `4:42:07.193`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / True Rippers bans: Clint, Ling, Martis, Claude, Karrie
- Red / True Rippers picks: Atlas, Uranus, Yi Sun-Shin, Moskov, Gord
- Blue / Team Vitality bans: Guinevere, Obsidia, Esmeralda, Aurora, Zhuxin
- Blue / Team Vitality picks: Selena, Hirara, Belerick, Harith, Chou
- Last pick: Team Vitality (blue) — Chou

### 7. Team Spirit vs Ignite Gaming — Game 1

- Requested point: `5:38:00`
- Visually validated final composition: approximately `5:42:00`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / Team Spirit bans: Valir, Atlas, Aurora, Melissa, Brody
- Red / Team Spirit picks: Ling, Zhuxin, Paquito, Esmeralda, Minotaur
- Blue / Ignite Gaming bans: Obsidia, Fanny, Hirara, Hanzo, Aulus†
- Blue / Ignite Gaming picks: Valentina, Chou, Suyou, Uranus, Moskov
- Last pick: Ignite Gaming (blue) — Moskov

The strict detector did not certify this transition because the sampled clip boundary missed enough of the transition window. Manual frames still show nine locked heroes during blue's final pick, then all ten unchanged when both 30-second timers appear.

### 8. Ignite Gaming vs Team Spirit — Game 2

- Requested point: `6:13:00`
- Certified final composition: `6:15:37.153`
- Ordering: frame-reviewed pre-swap, from each team's outer side toward the middle
- Red / Ignite Gaming bans: Valir, Fanny, Clint, Bane, Leomord
- Red / Ignite Gaming picks: Ling, Brody, Gloo, Lylia, Alice
- Blue / Team Spirit bans: Aurora, Atlas, Hanzo, Esmeralda, Uranus
- Blue / Team Spirit picks: Zhuxin, Freya, Hirara, Barats, Chou
- Last pick: Team Spirit (blue) — Chou

## Two-timer validation conclusion

Yes: comparing frames immediately before and after both middle timers appear is the correct final-state check for this overlay.

A robust extractor should:

1. Find the last single-sided `PICKING` state.
2. Record which side is active and which hero changes the total from nine to ten.
3. Find the first frame with both `ADJUST` timers near 30 seconds.
4. Require all ten hero identities to agree across the boundary.
5. Require the dual-timer result to remain stable for several samples.

This preserves the last chronological pick while also validating the final composition. Reading only the post-swap frame loses chronological pick order because teams can move heroes between player slots during `ADJUST`.

## Session after-action review

Completed:

- Downloaded focused VOD windows around all eight requested timestamps.
- Ran the current MSC layout detector and located seven strict final-state certifications plus one manual boundary validation.
- Verified all eight pick compositions from printed HUD labels.
- Reconstructed the last pick in all eight games from the pre-adjust transition.
- Reconstructed the full 15-phase draft sequence for all eight games by reading both sides from the outer edge toward the centre before swapping begins.
- Matched the 80 small ban portraits against the MSC 2026 hero catalog, with three low-confidence identities marked `†`.
- Made the `flying_cloud` centre-map anchor optional. The broadcast changes that emblem between Broken Walls, Expanding Rivers, and Flying Cloud, so it cannot be a required event-wide gate. The stable MSC/EWC lockup remains required.

Next work:

- Add a dedicated ban-portrait reference set or train a classifier on the broadcast's 70-by-70 tinted ban crops.
- Independently verify the three `†` ban identities before committing them as ground truth fixtures.
- Add a fixture whose clip includes the complete Team Spirit vs Ignite Game 1 transition so the strict detector can certify it automatically.
- Persist frame-level draft events as state transitions so the reconstructed phase sequence can be verified independently of slot-order inference.
