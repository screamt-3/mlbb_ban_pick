# MSC at EWC 2026 Group Stage Day 1 draft analysis

Source: [MSC at EWC 2026 Group Stage 1 English VOD](https://www.youtube.com/watch?v=Ew_-LE64e4w)

Analysed timestamps: `1:03:00`, `1:38:10`, `2:33:00`, `3:10:13`, `4:06:16`, `4:38:00`, `5:38:00`, and `6:13:00`.

## Reading the results

- Red is the left side of this broadcast layout; blue is the right side.
- A confirmed pre-swap list is stored from each team's outer side toward the middle of the screen. This makes consecutive list slices align with the configured draft phases. Lists that have not yet been checked against a pre-swap frame retain their earlier final-screen display order and must not be treated as chronological lock order.
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
- Red / Team Falcons MENA bans: Fanny, Valir, Hirara, Hanzo, Aulus†
- Red / Team Falcons MENA picks: Freya, Chou, Valentina, Alice, Harley
- Blue / Team Vamos bans: Masha, Baxia, Hilda, Atlas, Clint
- Blue / Team Vamos picks: Melissa, Minotaur, Esmeralda, Ling, Eudora
- Last pick: Team Vamos (blue) — Melissa

### 3. Team Falcons PH vs Guangzhou Gaming — Game 1

- Requested point: `2:33:00`
- Certified final composition: `2:38:40.000`
- Red / Team Falcons PH bans: Hanzo, Claude, Fanny, Hayabusa, Yve†
- Red / Team Falcons PH picks: Ling, Dyrroth, Brody, Gloo, Lylia
- Blue / Guangzhou Gaming bans: Lunox, Aurora, Atlas, Obsidia, Hirara
- Blue / Guangzhou Gaming picks: Nolan, Zhuxin, Hilda, Esmeralda, Harith
- Last pick: Guangzhou Gaming (blue) — Nolan

### 4. Guangzhou Gaming vs Team Falcons PH — Game 2

- Requested point: `3:10:13`
- Certified final composition: `3:13:23.570`
- Red / Guangzhou Gaming bans: Martis, Fanny, Esmeralda, Brody, Franco
- Red / Guangzhou Gaming picks: Paquito, Hirara, Yve, Grock, Claude
- Blue / Team Falcons PH bans: Uranus, Barats, Atlas, Clint, Obsidia
- Blue / Team Falcons PH picks: Moskov, Akai, Eudora, Harley, Sora
- Last pick: Team Falcons PH (blue) — Sora

### 5. Team Vitality vs True Rippers — Game 1

- Requested point: `4:06:16`
- Certified final composition: `4:09:14.000`
- Red / Team Vitality bans: Valir, Zhuxin, Guinevere, Esmeralda, Uranus
- Red / Team Vitality picks: Atlas, Akai, Valentina, Paquito, Claude
- Blue / True Rippers bans: Leomord, Masha, Clint, Hirara, Obsidia
- Blue / True Rippers picks: Badang, Alice, Karrie, Ling, Eudora
- Last pick: True Rippers (blue) — Badang

### 6. True Rippers vs Team Vitality — Game 2

- Requested point: `4:38:00`
- Certified final composition: `4:42:07.193`
- Red / True Rippers bans: Clint, Ling, Martis, Claude, Karrie
- Red / True Rippers picks: Uranus, Yi Sun-Shin, Gord, Atlas, Moskov
- Blue / Team Vitality bans: Zhuxin, Aurora, Esmeralda, Obsidia, Guinevere
- Blue / Team Vitality picks: Harith, Chou, Selena, Hirara, Belerick
- Last pick: Team Vitality (blue) — Chou

### 7. Team Spirit vs Ignite Gaming — Game 1

- Requested point: `5:38:00`
- Visually validated final composition: approximately `5:42:00`
- Red / Team Spirit bans: Valir, Atlas, Aurora, Melissa, Brody
- Red / Team Spirit picks: Ling, Zhuxin, Paquito, Esmeralda, Minotaur
- Blue / Ignite Gaming bans: Aulus†, Hanzo, Hirara, Fanny, Obsidia
- Blue / Ignite Gaming picks: Moskov, Uranus, Suyou, Chou, Valentina
- Last pick: Ignite Gaming (blue) — Moskov

The strict detector did not certify this transition because the sampled clip boundary missed enough of the transition window. Manual frames still show nine locked heroes during blue's final pick, then all ten unchanged when both 30-second timers appear.

### 8. Ignite Gaming vs Team Spirit — Game 2

- Requested point: `6:13:00`
- Certified final composition: `6:15:37.153`
- Red / Ignite Gaming bans: Valir, Fanny, Clint, Bane, Leomord
- Red / Ignite Gaming picks: Ling, Brody, Gloo, Lylia, Alice
- Blue / Team Spirit bans: Uranus, Esmeralda, Hanzo, Atlas, Aurora
- Blue / Team Spirit picks: Chou, Barats, Hirara, Freya, Zhuxin
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
- Matched the 80 small ban portraits against the MSC 2026 hero catalog, with three low-confidence identities marked `†`.
- Made the `flying_cloud` centre-map anchor optional. The broadcast changes that emblem between Broken Walls, Expanding Rivers, and Flying Cloud, so it cannot be a required event-wide gate. The stable MSC/EWC lockup remains required.

Next work:

- Add a dedicated ban-portrait reference set or train a classifier on the broadcast's 70-by-70 tinted ban crops.
- Independently verify the three `†` ban identities before committing them as ground truth fixtures.
- Add a fixture whose clip includes the complete Team Spirit vs Ignite Game 1 transition so the strict detector can certify it automatically.
- Persist draft events as state transitions so the complete chronological hero lock sequence can be exported, not only the final composition and last pick.
