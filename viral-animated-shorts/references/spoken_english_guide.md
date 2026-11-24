# Spoken English guide for animated Shorts

Write narration the way a sharp friend tells a wild story out loud, instead of like a press release or a slide deck. For general prose fundamentals, follow [clear-natural-english](../../clear-natural-english/SKILL.md). Apply this guide to `shots[].dialogue_or_vo`, `loop_bridge`, and YouTube metadata.

## Core rules

### 1. Keep grammar intact and vary cadence
- Never drop articles, verbs, objects, or nouns to hit a word-count box. Avoid clipped telegraphese like `"are huge risk"`, `"won't refund"`, `"onto the floor"`, or `"Forty-five million bought"`.
- Vary shot length across the script. Mix quick 4-7 word punchlines with flowing 12-16 word setups so the Short sounds like connected speech instead of 12 identical PowerPoint bullets.

### 2. Separate clean captions from TTS phonetics
- Write `shots[].dialogue_or_vo` in clean display English (`AWS`, `GTA 6`, `911`, `$75,000`, `125`, `Nov 24`) with proper end punctuation so caption chunks break at sentence ends instead of fusing adjacent thoughts (`"rusty scooter it"`).
- Put phonetic fixes in top-level `tts_pronunciations` (applied across the plan) or per-shot `tts_text` (one-off overrides). That keeps neural TTS accurate without splitting `"A / W S"` across caption lines or spelling out `"seventy-five thousand"` on screen:

```json
{
  "tts_pronunciations": { "AWS": "A W S", "GTA 6": "GTA Six", "911": "nine-one-one" },
  "shots": [
    {
      "dialogue_or_vo": "They offered a $75,000 bounty on Nov 24.",
      "tts_text": "They offered a seventy-five thousand dollar bounty on November twenty-fourth."
    }
  ]
}
```

### 3. Use concrete actors, active verbs, and plain speech
- Name who acted and what happened early in the sentence.
- Replace trailing `"-ing"` participle riders (`", catching stolen passwords"`, `", firing tens of thousands of calls"`, `", slapping stickers..."`) with a second active verb (`"and caught"`, `"and fired off"`) or a short second sentence.
- Swap press-release legalese (`"keep full liability"`, `"fired un-mutable emergency alerts nationwide"`, `"personal erasers"`) for everyday speech (`"still on the hook"`, `"blasted an alarm you couldn't mute"`, `"outside erasers"`).

### 4. Cut spoken AI slop and let visuals land the beat
- Strip dead openers (`"Hey guys"`, `"In today's world"`, `"Have you ever wondered"`, `"Buckle up"`), fake contrasts (`"not just X, it's Y"`, `"Forget X — Y"`), and colon reveals (`"Plot twist:"`, `"Here's the catch:"`).
- Cut dramatic echo fragments (`"Total privacy disaster."`, `"Let that sink in."`), stock buzzwords (`delve`, `landscape`, `crucial`, `leverage`, `game-changer`), and fortune-cookie morals (`"When cheap AI looks free, your secrets pay the bill."`).
- State the concrete fact and let a `vine_boom`, `record_scratch`, or visual gag carry the reaction.

### 5. Rotate hooks and loop bridges
- Do not open every Short with `"Because..."` or end every Short with `"Which is why..."` (neural TTS drops pitch on trailing `"why..."` like a dead period).
- Open Shot 1 with a standalone concrete hook that hits cold at `0:00`.
- Rotate natural loop bridges or visual callbacks that lead back into Shot 1 without sounding mechanical.

### 6. Ground names, hold one thread, and clean up YouTube metadata
- Give every unfamiliar proper name a quick role on first mention (`"Education Minister Choi Kyo-jin"`, `"Rockstar writer Lazlow"`, `"Michael's actor"`).
- Stick to one clear story per Short (or set up both beats upfront) instead of bolting unrelated trivia onto the final four shots.
- Apply [clear-natural-english](../../clear-natural-english/SKILL.md) to YouTube titles, descriptions, and pinned comments too. Cut em-dash spam, `"literally"` filler, and recycled openers (`"Be honest:"`, `"Honest question:"`, `"Wait—did you..."`, `"Imagine..."`).

## Before and after rewrites

| Pattern | Cut (AI slop or telegraphese) | Use (natural spoken English) |
| :--- | :--- | :--- |
| **Dropped grammar** | "Outside bots are huge risk, unless the bank owns the bot." / "They read one page, said nope, onto the floor." | "Outside bots are suddenly a huge risk, right until the bank owns one." / "They read one page, said nope, and tossed the script on the floor." |
| **Phonetic leak in captions** | "They baited cheap AI hosts with fake A W S keys." / "Security calls nine-one-one." | Display `dialogue_or_vo`: "They baited cheap AI hosts with fake AWS keys." / "The car's security calls 911." Put `"AWS": "A W S"` and `"911": "nine-one-one"` in `tts_pronunciations`. |
| **Trailing "-ing" + legalese** | "Officials panicked, slapping stickers on shirt buttons." / "Wells Fargo demands proof, or you keep full liability." | "Officials panicked. They taped over students' shirt buttons." / "Wells Fargo wants proof, or you're still on the hook." |
| **Colon reveal** | "Plot twist: those exact banks are pushing their own bots." / "Here's the catch: it only ships with 8 gigs of RAM." | "Meanwhile, those exact same banks are pushing their own bots." / "It only ships with 8 gigs of RAM, so Chrome eats half of it on boot." |
| **Fake contrast** | "Forget method acting — Rockstar casts real legends." / "This isn't just a price hike; it's a shift in gaming." | "No acting class could write lines that raw." / "Sony added $200 to the price tag and still left out the disc drive." |
| **Dramatic fragment** | "Six devices opened it in two minutes. Total privacy disaster." / "They burned $4 billion. Let that sink in." | "Twenty-four days later, a bait link popped up in China, and six devices opened it in two minutes." / "They burned $4 billion in one quarter, and the stock still jumped." |
| **Moral + flat loop** | "When cheap AI looks free, your secrets pay the bill. Which is why..." | "If an AI host looks way too cheap, check who else is reading your prompts." (Close with a visual or thematic callback to Shot 1.) |
| **Missing role + telegraphese** | "Sunday, Minister Choi quit, and President Lee Jae Myung accepted." | "Education Minister Choi Kyo-jin resigned on Sunday, and President Lee Jae Myung accepted." |
| **Dead opener + buzzwords** | "In today's fast-paced AI landscape, let's delve into a crucial, game-changing shift." | "Nvidia charged $40,000 for one chip, and buyers waited six months to get it." |
| **YouTube metadata slop** | "Wait—did you know banks banned bots? Be honest: would you trust an AI with your card?" | "Wells Fargo and JPMorgan now hold you liable for AI bot charges. Would you let an AI agent pay your bills?" |

## What to keep (do not over-correct)

Do not strip the script down into stiff, robotic telegraphese. Keep:
- **Natural contractions** (`it's`, `don't`, `won't`, `you're`, `couldn't`), using uncontracted words (`"He did not pay"`) or a short pause/exclamation for stress. **Avoid ALL-CAPS emphasis on short words** (`US`, `IT`, `IN`, `ON`, `OR`, `AM`, `NOT`), because neural TTS often spells them out as acronyms or state names (`"U.S."`, `"I.T."`, `"Oregon"`).
- **Specific visual comedy** (`"ten roasted chickens"`, `"parallel parks inside a police barricade"`, `"Just steal a rusty scooter. It can't dial the cops."`).
- **Voice and real escalations:** Keep character attitude and real comedic rule-of-three escalations (three escalating gags are comedy; three filler adjectives like `"fast, sleek, and innovative"` are slop).
- **Real contrasts:** Keep a contrast when both halves add new facts or correct a real misconception.

## Scripted drama, multi-character dialogue, and silent beats (`script_genre: "drama"`)

When writing 3-15+ minute multi-act scripted fiction (`video_format: "long_form"`, `script_genre: "drama"`):
- **Use two lanes cleanly:** Commentary/explainer voiceover uses `"script_genre": "commentary"` ("sharp friend telling a wild story"); scripted drama uses `"script_genre": "drama"` so `--lint-plan` preserves dramatic character dialogue while flagging melodrama clichés (`"mark my words"`, `"grave mistake"`, `"played right into my hands"`, `"little did she know"`, `"my blood ran cold"`, `"this ends now"`).
- **Differentiate character rhythm, not just TTS voice:** Give each character distinct sentence length, diction, and honorifics/address forms, and keep `narrator` lines `<= 30-35%` of dramatic episodes. Never double-narrate physical actions already shown in `beats[].action`.
- **Direct silent reaction beats:** Budget 10-15% of dramatic shots as silent holds (`dialogue_or_vo: ""`, `shot_role: "reaction"`, `duration_sec: 0.9-1.8`, paired with SFX or `bgm_cue: "drop_out"`). Avoid `...` ellipses inside `tts_text` (they cause neural TTS pitch drops); use `post_gap_sec: 0.25-0.35` or a shot cut for real dramatic pauses.
- **Keep foreign, historical, and brand names clean in `dialogue_or_vo`:** Put phonetic hints (`"Lin Wanyin": "Lin Wahn-yeen"`, `"Pei Xuan": "Pay Shwen"`, `"Hanfu": "Hahn-foo"`) in top-level `tts_pronunciations` so every Act uses consistent pronunciation while captions stay cleanly spelled. Avoid placing two multi-word phonetic keys adjacent without punctuation.
- **Skip forced Shorts loops:** Close long-form episodes on a concrete cliffhanger (`ending_style: "cliffhanger"`) or a crisp closing thought/End-Screen bridge (`ending_style: "outro"`), never a mechanical `"Which is why..."` loop back to Shot 1.

## Four-point read-aloud check

Run `python3 scripts/tts_and_captions_v2.py --lint-plan shot_plan.json`, then read the script (or each Act) aloud before Stage 2 TTS:
1. **Friend / actor test:** Would a real person or actor say every line out loud without tripping over legalese, missing words, buzzwords, melodrama clichés, or fast-TTS slurs (like `"legend hid"` blurring into `"legend did"`)?
2. **Cadence & silence:** Do shot lengths move between quick punchlines, fuller setups, and breathing/reaction beats instead of repeating the same 10-word wall of speech?
3. **Display vs. TTS:** Does `dialogue_or_vo` look right on screen, with awkward acronyms, foreign names, numbers, and dates moved into `tts_pronunciations` or `tts_text`, and zero risky ALL-CAPS 2-letter words?
4. **Opening and ending:** Does Shot 1 hit cold at `0:00`, and does the closing beat land cleanly (a natural loop for Shorts, or a sharp cliffhanger/outro for long-form episodes)?

