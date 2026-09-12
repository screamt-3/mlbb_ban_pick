# MPL ID Season 18, Week 5 Day 2 — draft review

Source: [MPL Indonesia English VOD](https://www.youtube.com/watch?v=m_sszsoqymE). The six user-supplied timestamps are draft review points, not necessarily draft starts. The broadcast places **blue on the left** and **red on the right**, unlike the MSC/EWC footage used for the earlier board entries.

## Verified pre-swap pick order

Each list reads from that team's **outer edge toward the centre**, using frames before the later player-slot swaps. Blue occupies the left five slots; red occupies the right five. The last red hero was checked against a later locked frame. Approximate final times identify useful VOD review points, not frame-accurate timer certifications.

| Match | User point | Blue / left picks | Red / right picks | Last lock | Final review point |
| --- | --- | --- | --- | --- | --- |
| TLID vs BTR, G1 | [39:21](https://www.youtube.com/watch?v=m_sszsoqymE&t=2361s) | Team Liquid ID: Melissa, Paquito, Gloo, Minotaur, Zhuxin | Bigetron by Vitality: Barats, Selena, Carmilla, Clint, Guinevere | Red: Guinevere | [≈46:50](https://www.youtube.com/watch?v=m_sszsoqymE&t=2810s) |
| BTR vs TLID, G2 | [1:17:00](https://www.youtube.com/watch?v=m_sszsoqymE&t=4620s) | Bigetron by Vitality: Hirara, Gloo, Selena, Hylos, Granger | Team Liquid ID: Carmilla, Paquito, Minotaur, Miya, Rafaela | Red: Rafaela | [≈1:24:30](https://www.youtube.com/watch?v=m_sszsoqymE&t=5070s) |
| AE vs RRQ, G1 | [3:36:55](https://www.youtube.com/watch?v=m_sszsoqymE&t=13015s) | Alter Ego: Atlas, Obsidia, Eudora, Gloo, Nolan | RRQ Hoshi: Paquito, Hirara, Belerick, Miya, Angela | Red: Angela | [≈3:38:55](https://www.youtube.com/watch?v=m_sszsoqymE&t=13135s) |
| AE vs RRQ, G2 | [4:18:14](https://www.youtube.com/watch?v=m_sszsoqymE&t=15494s) | Alter Ego: Hirara, Atlas, Valentina, Esmeralda, Miya | RRQ Hoshi: Uranus, Paquito, Rafaela, Gatotkaca, Yi Sun-Shin | Red: Yi Sun-Shin | [≈4:22:00](https://www.youtube.com/watch?v=m_sszsoqymE&t=15720s) |
| NAVI vs EVOS, G1 | [6:35:00](https://www.youtube.com/watch?v=m_sszsoqymE&t=23700s) | NAVI: Hirara, Gatotkaca, Selena, Minotaur, Miya | EVOS: Belerick, Paquito, Lylia, Barats, Obsidia | Red: Obsidia | [≈6:40:45](https://www.youtube.com/watch?v=m_sszsoqymE&t=24045s) |
| EVOS vs NAVI, G2 | [7:21:13](https://www.youtube.com/watch?v=m_sszsoqymE&t=26473s) | EVOS: Hirara, Carmilla, Cecilion, Esmeralda, Moskov | NAVI: Selena, Paquito, Gatotkaca, Obsidia, Dyrroth | Red: Dyrroth | [≈7:27:43](https://www.youtube.com/watch?v=m_sszsoqymE&t=26863s) |

## What the pick frames establish

All six show the same pick-side batch pattern: **blue 1 → red 2 → blue 2 → red 1**, then after the second ban round **red 1 → blue 2 → red 1**. This is a pick-only sequence; the full ban chronology has not been checked for this overlay. The final red lock is visible because blue already has five portraits when red still has four.

Do not treat the later five-player recap as lock order. For example, BTR G2 shows Hirara–Gloo–Selena–Hylos–Granger before swaps, but the later lineup rearranges Hylos and Gloo. AE G2 and EVOS G2 also visibly rearrange hero portraits after the pick set is complete.

## Incomplete / next review

- **Bans are not populated.** The broadcast's five grayscale ban portraits on each side are small and dark in 720p frames. No caption track is available for this VOD. Guessing 60 hero names would make the board misleading. Revisit ban-phase frames, ideally at 1080p, and check each against an enlarged portrait or a spoken call before adding them.
- Validate whether ban order also mirrors the MSC 15-step template. Do not assign those phase slots until lock transitions are observed.
- Time the exact end-of-draft frame and any swap boundary for each game if timer certification is required. The current final timestamps are approximate visual review anchors.
- Cross-check the newly added hero names and each last lock independently if a second reviewer is available.

## Session handoff

This session sampled all six supplied windows, verified 60 picks and six last locks from broadcast frames, added the six MPL drafts to `dist/drafts.json`, and updated the board to select a separate MPL **pick-only** sequence. MPL cards display a clear “ban portraits pending review” state. The prior eight MSC drafts and their 15-phase sequence remain separate. The source VOD URL is attached per MPL draft so the board's VOD button opens the correct video.

The main remaining work is ban verification. Until then, the MPL rows should remain labelled **picks verified · bans pending** and must not be described as complete ban/pick extraction.
