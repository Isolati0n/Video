# Digest: Synthetic test clip

- Video: (synthetic) (00:01:30), channel: yt-digest self-test
- Archive: GitHub Release `SYNTHETIC01` (720p H.264 parts + parts.json)
- Files: [transcript.md](transcript.md), [events.md](events.md), `events.json`, `frames.jsonl.gz` (one JSON per frame at 25 fps, keys documented in README), [calibration.png](calibration.png), `sheets/`
- Sheets: 31 (3x3 tiles of 640x360; white label = 1 fps overview frame, yellow label + coloured border = one of 4 frames at 25 fps around an event, label shows HH:MM:SS.ff and frame number f)
- Events: area_change=3, death=3, life_drop=7, life_refill=11, mana_refill=7, poisoned=4, text=11
- Zoom: comment `/zoom SYNTHETIC01 H:MM:SS-H:MM:SS [fps]` on the issue for full-detail frames.

| sheet | time range | events | transcript |
|---|---|---|---|
| [sheet_0001.jpg](sheets/sheet_0001.jpg) | 00:00:00-00:00:04 | 00:00:04 life_drop: Life drop -37% (100% -> 63%) in 0.32s |  |
| [sheet_0002.jpg](sheets/sheet_0002.jpg) | 00:00:05-00:00:08 | 00:00:06 life_refill: Life refill +37% (63% -> 100%) in 0.12s (rejuvenation potion?)<br>00:00:08 mana_refill: Mana refill +75% (25% -> 100%) in 0.20s (mana/rejuv potion?) |  |
| [sheet_0003.jpg](sheets/sheet_0003.jpg) | 00:00:08-00:00:10 | 00:00:08 mana_refill: Mana refill +75% (25% -> 100%) in 0.20s (mana/rejuv potion?)<br>00:00:10 poisoned: Poisoned (green life orb) for 3.0s |  |
| [sheet_0004.jpg](sheets/sheet_0004.jpg) | 00:00:10-00:00:12 | 00:00:11 text: Text (chat): cur world. Diablo's minions grow stranger.<br>00:00:12 text: Text (chat): e en ¢ ra = |  |
| [sheet_0005.jpg](sheets/sheet_0005.jpg) | 00:00:12-00:00:16 | 00:00:12 text: Text (chat): e en ¢ ra =<br>00:00:15 life_refill: Life refill +28% (72% -> 100%) in 0.04s (rejuvenation potion?) |  |
| [sheet_0006.jpg](sheets/sheet_0006.jpg) | 00:00:16-00:00:19 | 00:00:16 area_change: Area change / loading: HUD gone 2.0s (dark)<br>00:00:18 life_drop: Life drop -100% (100% -> 0%) in 1.04s |  |
| [sheet_0007.jpg](sheets/sheet_0007.jpg) | 00:00:19-00:00:21 | 00:00:18 life_drop: Life drop -100% (100% -> 0%) in 1.04s<br>00:00:20 death: Death? life orb empty for >= 0.4s<br>00:00:21 text: Text (center): You have died |  |
| [sheet_0008.jpg](sheets/sheet_0008.jpg) | 00:00:21-00:00:23 | 00:00:21 text: Text (center): You have died<br>00:00:23 life_refill: Life refill +100% (0% -> 100%) in 0.04s (rejuvenation potion?)<br>00:00:23 mana_refill: Mana refill +47% (53% -> 100%) in 0.04s (mana/rejuv potion?) |  |
| [sheet_0009.jpg](sheets/sheet_0009.jpg) | 00:00:24-00:00:27 | 00:00:23 life_refill: Life refill +100% (0% -> 100%) in 0.04s (rejuvenation potion?)<br>00:00:23 mana_refill: Mana refill +47% (53% -> 100%) in 0.04s (mana/rejuv potion?)<br>00:00:28 life_drop: Life drop -37% (100% -> 63%) in 0.32s |  |
| [sheet_0010.jpg](sheets/sheet_0010.jpg) | 00:00:28-00:00:30 | 00:00:28 life_drop: Life drop -37% (100% -> 63%) in 0.32s<br>00:00:30 life_refill: Life refill +37% (63% -> 100%) in 0.12s (rejuvenation potion?) |  |
| [sheet_0011.jpg](sheets/sheet_0011.jpg) | 00:00:30-00:00:33 | 00:00:30 life_refill: Life refill +37% (63% -> 100%) in 0.12s (rejuvenation potion?)<br>00:00:32 mana_refill: Mana refill +74% (26% -> 100%) in 0.20s (mana/rejuv potion?)<br>00:00:34 poisoned: Poisoned (green life orb) for 3.0s |  |
| [sheet_0012.jpg](sheets/sheet_0012.jpg) | 00:00:34-00:00:35 | 00:00:34 poisoned: Poisoned (green life orb) for 3.0s<br>00:00:35 text: Text (chat): cur world. grow stranger. |  |
| [sheet_0013.jpg](sheets/sheet_0013.jpg) | 00:00:35-00:00:39 | 00:00:36 text: Text (chat): joined cur Diablo's minions grow stronger.<br>00:00:39 life_refill: Life refill +28% (72% -> 100%) in 0.04s (rejuvenation potion?) |  |
| [sheet_0014.jpg](sheets/sheet_0014.jpg) | 00:00:39-00:00:41 | 00:00:39 life_refill: Life refill +28% (72% -> 100%) in 0.04s (rejuvenation potion?)<br>00:00:40 area_change: Area change / loading: HUD gone 2.0s (dark) |  |
| [sheet_0015.jpg](sheets/sheet_0015.jpg) | 00:00:42-00:00:44 | 00:00:42 life_drop: Life drop -100% (100% -> 0%) in 1.04s<br>00:00:44 death: Death? life orb empty for >= 0.4s |  |
| [sheet_0016.jpg](sheets/sheet_0016.jpg) | 00:00:44-00:00:47 | 00:00:44 death: Death? life orb empty for >= 0.4s<br>00:00:45 text: Text (center): You have died |  |
| [sheet_0017.jpg](sheets/sheet_0017.jpg) | 00:00:47-00:00:48 | 00:00:47 life_refill: Life refill +100% (0% -> 100%) in 0.04s (rejuvenation potion?)<br>00:00:47 mana_refill: Mana refill +47% (53% -> 100%) in 0.04s (mana/rejuv potion?) |  |
| [sheet_0018.jpg](sheets/sheet_0018.jpg) | 00:00:49-00:00:53 | 00:00:52 life_drop: Life drop -37% (100% -> 63%) in 0.32s |  |
| [sheet_0019.jpg](sheets/sheet_0019.jpg) | 00:00:53-00:00:56 | 00:00:54 life_refill: Life refill +37% (63% -> 100%) in 0.12s (rejuvenation potion?)<br>00:00:56 mana_refill: Mana refill +75% (25% -> 100%) in 0.20s (mana/rejuv potion?) |  |
| [sheet_0020.jpg](sheets/sheet_0020.jpg) | 00:00:56-00:00:58 | 00:00:56 mana_refill: Mana refill +75% (25% -> 100%) in 0.20s (mana/rejuv potion?)<br>00:00:58 poisoned: Poisoned (green life orb) for 3.0s<br>00:00:59 text: Text (chat): cur Diablo's minions grow stranger. |  |
| [sheet_0021.jpg](sheets/sheet_0021.jpg) | 00:00:59-00:01:00 | 00:00:59 text: Text (chat): cur Diablo's minions grow stranger.<br>00:01:00 text: Text (chat): Playerl joined our world. Diablo's minions grow stranger. |  |
| [sheet_0022.jpg](sheets/sheet_0022.jpg) | 00:01:01-00:01:04 | 00:01:03 life_refill: Life refill +28% (72% -> 100%) in 0.04s (rejuvenation potion?)<br>00:01:04 area_change: Area change / loading: HUD gone 2.0s (dark) |  |
| [sheet_0023.jpg](sheets/sheet_0023.jpg) | 00:01:04-00:01:07 | 00:01:04 area_change: Area change / loading: HUD gone 2.0s (dark)<br>00:01:06 life_drop: Life drop -100% (100% -> 0%) in 1.04s |  |
| [sheet_0024.jpg](sheets/sheet_0024.jpg) | 00:01:07-00:01:09 | 00:01:06 life_drop: Life drop -100% (100% -> 0%) in 1.04s<br>00:01:08 death: Death? life orb empty for >= 0.4s<br>00:01:09 text: Text (center): You have died |  |
| [sheet_0025.jpg](sheets/sheet_0025.jpg) | 00:01:09-00:01:12 | 00:01:09 text: Text (center): You have died<br>00:01:11 life_refill: Life refill +96% (4% -> 100%) in 0.04s (rejuvenation potion?)<br>00:01:11 mana_refill: Mana refill +47% (53% -> 100%) in 0.04s (mana/rejuv potion?) |  |
| [sheet_0026.jpg](sheets/sheet_0026.jpg) | 00:01:12-00:01:16 | 00:01:11 life_refill: Life refill +96% (4% -> 100%) in 0.04s (rejuvenation potion?)<br>00:01:11 mana_refill: Mana refill +47% (53% -> 100%) in 0.04s (mana/rejuv potion?)<br>00:01:16 life_drop: Life drop -37% (100% -> 63%) in 0.32s |  |
| [sheet_0027.jpg](sheets/sheet_0027.jpg) | 00:01:16-00:01:18 | 00:01:16 life_drop: Life drop -37% (100% -> 63%) in 0.32s<br>00:01:18 life_refill: Life refill +37% (63% -> 100%) in 0.12s (rejuvenation potion?) |  |
| [sheet_0028.jpg](sheets/sheet_0028.jpg) | 00:01:19-00:01:22 | 00:01:20 mana_refill: Mana refill +74% (26% -> 100%) in 0.20s (mana/rejuv potion?)<br>00:01:22 poisoned: Poisoned (green life orb) for 3.0s |  |
| [sheet_0029.jpg](sheets/sheet_0029.jpg) | 00:01:22-00:01:23 | 00:01:22 poisoned: Poisoned (green life orb) for 3.0s<br>00:01:23 text: Text (chat): cur world. grow stranger.<br>00:01:24 text: Text (chat): joined cur Diablo's minions grow stronger. |  |
| [sheet_0030.jpg](sheets/sheet_0030.jpg) | 00:01:24-00:01:27 | 00:01:24 text: Text (chat): joined cur Diablo's minions grow stronger.<br>00:01:27 life_refill: Life refill +28% (72% -> 100%) in 0.04s (rejuvenation potion?) |  |
| [sheet_0031.jpg](sheets/sheet_0031.jpg) | 00:01:28-00:01:29 | 00:01:27 life_refill: Life refill +28% (72% -> 100%) in 0.04s (rejuvenation potion?) |  |
