# Telegram News Bot

Posts the latest **AI, Games, Anime and Tech** news to Telegram, in English and (optionally) Arabic.

- Checks 28 news sites (RSS feeds) every 15 minutes.
- Posts only **strong news**. It skips filler (top-10 lists, how-tos, reviews, deals, opinion columns) and prefers stories that several sites report at the same time.
- Never posts **18+ and other unwanted subjects**: sexual content, gambling, alcohol and drugs (list in `config.yaml`).
- Every post gets a **branded banner**: the news photo, the headline in big letters, your logo, a topic badge with a card suit (♠ AI, ♥ Games, ♦ Anime, ♣ Tech), and your channel name.
- Under the banner: the topic, a **TOP STORY** label when 3 or more sites report it, the headline, a short summary, the source, hashtags (`#Games #Nintendo`) and two buttons: **Read full story** and **Share**.
- At most 3 posts per check, strongest first. A quiet check posts nothing rather than something weak.
- Never posts the same news twice, even when another site words it differently.
- Every evening at 21:00 (Saudi time): the day's **top 5**. Every Friday at 20:00: the **top 10 of the week** and a **poll**.
- **Arabic twin channel** (optional): every post is translated with Google Gemini (free plan) and posted with an Arabic banner. A pinned welcome post lets new readers choose English or Arabic.
- Runs free on GitHub Actions. Your PC can be off.

The basic setup takes about 15 minutes. Follow the steps in order. Step 6 (Arabic) is optional.

---

## Step 1: Create the bot

1. In Telegram, open a chat with **@BotFather** (it has a blue check mark).
2. Send `/newbot`.
3. Send a display name, for example `My News Bot`.
4. Send a username. It must end in `bot`, for example `my_news_2026_bot`.
5. BotFather replies with a **token** that looks like `123456789:AAH...`. Copy it and keep it secret. Anyone with this token controls the bot.

## Step 2: Create the channel

1. In Telegram: **New Channel**, then give it a name and a description.
2. Choose **Public** and pick a link, for example `t.me/my_news_channel`. Your channel id is then `@my_news_channel`.
   (A private channel also works. See "Private channel" below.)

## Step 3: Make the bot an admin of the channel

1. Open the channel, tap its name, then **Administrators** (or **Manage Channel > Administrators**).
2. **Add Admin**, search for your bot's username, and select it.
3. Turn on **Post Messages** and **Edit Messages** (Edit is needed to pin the language picker). Save.

## Step 4: Test it on your PC (recommended)

Open PowerShell in this folder and run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

In Notepad, fill in the values and save:

```
TELEGRAM_BOT_TOKEN=123456789:AAH...your token...
TELEGRAM_CHANNEL_ID=@my_news_channel
```

Now run these commands, one at a time:

```powershell
# 1. Checks the token, the channel, and that the bot can post. Sends nothing.
.venv\Scripts\python -m newsbot --check

# 2. Shows the posts it would send right now and saves their banners in a folder. Sends nothing.
.venv\Scripts\python -m newsbot --dry-run --banners-dir banners-preview

# 3. Posts for real (up to 3 posts) and remembers them in data\posted.json.
.venv\Scripts\python -m newsbot
```

If `--check` says "All good", the Telegram side is ready.

## Step 5: Put it on GitHub so it runs by itself

**Public or private repository?** Use **public**. GitHub Actions is free for public repositories. The token is stored in GitHub Secrets, which nobody else can see, even in a public repository.

With the GitHub CLI (`gh`), run this in PowerShell in this folder:

```powershell
git init -b main
git add .
git commit -m "feat: telegram news bot"
gh repo create telegram-news-bot --public --source . --push
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHANNEL_ID
```

Each `gh secret set` asks you to paste the value. Without the CLI: create an empty repository on github.com, push this folder, then add the secrets in **Settings > Secrets and variables > Actions > New repository secret**.

**Start it:** open the repository's **Actions** tab, select **News bot**, then **Run workflow**. After that it runs by itself every 15 minutes. GitHub can start scheduled runs late, and a new repository can take a few hours before its first scheduled run.

`.env` is in `.gitignore`, so your token is never uploaded.

## Step 6 (optional): Arabic channel and language picker

1. **Create the Arabic channel** (Step 2), public, for example `t.me/my_news_ar`. Add the bot as an admin with **Post Messages** and **Edit Messages** (Step 3).
2. **Get a free Gemini key:** open [Google AI Studio](https://aistudio.google.com), sign in with your Google account, and create an API key on its **API keys** page. No credit card is needed. The bot makes about 25 to 40 translations a day, well inside the free limits (your limits are shown at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit)).
   - Do not add a billing account to that Google project. Without billing, Google cannot charge you; at the limit the Arabic posts just pause.
   - On the free plan, Google may use the text it translates to improve its products. The bot only sends public news headlines and summaries.
3. **Add two secrets** on GitHub (and the same lines in `.env` to test on your PC):

   ```powershell
   gh secret set TELEGRAM_ARABIC_CHANNEL_ID   # for example @my_news_ar
   gh secret set GEMINI_API_KEY
   ```

4. **Check it:** `.venv\Scripts\python -m newsbot --check` now also checks the Arabic channel and translates one test headline with Gemini.
5. **Pin the language picker:** Actions tab > News bot > Run workflow > tick **Post and pin the 'choose your language' message**. It posts a welcome banner with **English** and **العربية** buttons in both channels and pins it, so it is the first thing new readers see.

From then on, every story goes to both channels. If Gemini's free limit runs out, the Arabic channel pauses and tries again on the next run, and the English channel goes on as normal.

---

## Changing things

Everything is in `config.yaml`, with a comment on every line:

- **Sources:** add, remove or turn off feeds under each topic. Extra sources are already listed with `#` in front of them; remove the `#` to turn one on.
- **How many posts:** `max_posts_per_run` (posts per check; there are 4 checks per hour).
- **How strong a story must be:** `min_score`. A story gets 2 points for every extra site that reports it, 1 point for a strong word in its title (announced, delayed, trailer, season 2, ...) and 1 point for a big name of its topic (Nintendo, OpenAI, One Piece, ...). Lower it for more posts, raise it for fewer. Anime has its own lower `min_score`, because fewer anime sites exist.
- **18+ and unwanted subjects:** `filters` > `blocked`. **Filler:** the other `filters`.
- **The look:** `style` (banner on/off, brand name, channel name, button labels, how many name hashtags) and, under each topic, `emoji`, `suit`, `arabic_name` and `arabic_hashtag`.
- **Arabic labels and titles:** `arabic`.
- **Evening top 5:** `digest` (on/off, hour, your time zone, how many stories, title).
- **Weekly top 10 and poll:** `weekly` (day, hour, how many stories, poll question and answers).
- **The language picker text:** `language_picker`.
- **New topic:** copy a topic block, then change its name, hashtag, emoji, suit, Arabic name and hashtag, big names and feeds.

To see what a change does without posting, run `.venv\Scripts\python -m newsbot --dry-run -v --banners-dir banners-preview`. The `-v` adds a line for every filler story it skipped and why; the banners land in the `banners-preview` folder. Then commit and push the change.

**Send something now** (Actions tab > News bot > Run workflow, or on your PC):

| Tick in "Run workflow" | On your PC |
|---|---|
| Send the daily top stories post now | `.venv\Scripts\python -m newsbot --digest-now` |
| Send the weekly top 10 and its poll now | `.venv\Scripts\python -m newsbot --weekly-now` |
| Post and pin the 'choose your language' message | `.venv\Scripts\python -m newsbot --setup-language-picker` |

Add `--dry-run` on your PC to only see the result.

**How often it checks** is set in `.github/workflows/news-bot.yml` (`cron: "7,22,37,52 * * * *"` means minutes 7, 22, 37 and 52 of every hour, UTC).

**Pause the bot:** Actions tab > News bot > the `...` menu > **Disable workflow**. Enable it the same way.

## How it works

1. Downloads every feed in `config.yaml`. A broken feed is logged and skipped; the others still post.
2. Keeps stories newer than `max_age_hours` that are not in `data/posted.json`, and drops 18+ stories and filler.
3. Drops a story that is the same news as something posted in the last 36 hours, even when the words differ (it compares titles and first sentences, and counts rare words like a game's name more than common words).
4. Groups the same story from different sites together and scores it (see `min_score` above). Stories below the bar wait: if more sites report them later, they can still make it.
5. Picks up to `max_posts_per_run` stories: the topic with the strongest story first, then topics take turns.
6. Draws the banner and posts it with the caption and buttons. If Telegram refuses the banner, it tries the plain news photo, then a text post with a link preview.
   - If Telegram rejects a story completely, the bot remembers that and does not try it again.
   - If the network fails, the story is tried again in the next run.
   - If Telegram gives no clear answer (a timeout or a server error after sending), the bot counts the story as posted, so it can never appear twice.
7. If the Arabic channel is set up, translates the headline and summary with Google Gemini, checks that the answer really is an Arabic translation, and posts the Arabic version.
8. Sends the evening top 5 and the weekly top 10 when they are due. They list stories posted before the current run, so nothing is repeated a moment after it went out.
9. Saves `data/posted.json` after every post. On GitHub, the workflow commits that file back, so the bot remembers between runs. You will see commits named "chore: update posted news list"; that is normal. Run `git pull` before you push your own changes.

## Private channel

A private channel has no `@username`, so the bot needs its number:

1. Add the bot as an admin (Step 3).
2. Post any message in the channel.
3. Run `.venv\Scripts\python -m newsbot --find-chat-id` (with only `TELEGRAM_BOT_TOKEN` filled in `.env`).
4. Copy the number it shows (it starts with `-100`) into `TELEGRAM_CHANNEL_ID`.

The language picker needs both channels to be public, because its buttons link to their `t.me` addresses.

## Troubleshooting

| What you see | What to do |
|---|---|
| `bot token was rejected (401)` | The token is wrong or was revoked. In @BotFather send `/mybots`, pick your bot, open **API Token**, and update `.env` or the GitHub secret. |
| `chat not found` | The channel id is wrong, or the bot is not in the channel. Public channel: use `@username`. Private: use `--find-chat-id`. |
| `not an admin` / `need administrator rights` | Make the bot an admin with **Post Messages** turned on (Step 3). |
| `Arabic channel: ...` (the run is red) | The Arabic channel or the Gemini key has a setup problem; the English channel still posted. Run `--check` on your PC to see which one. |
| `Arabic channel is off` | Only one of `TELEGRAM_ARABIC_CHANNEL_ID` and `GEMINI_API_KEY` is set. Set both. |
| `Gemini's free limit is used up for now` | The Arabic channel pauses and tries again next run. The daily limit resets at midnight Pacific time (about 10:00 to 11:00 Saudi time). English goes on. |
| `Gemini does not know this model` | Google retired the model. Put a newer free Flash-Lite model in `config.yaml` under `arabic`, `model`. |
| `Arabic version skipped: Gemini's answer ...` | Gemini gave an odd answer for one story, so its Arabic post was skipped. Nothing to do. |
| Language picker posted but not pinned | Give the bot **Edit Messages** in that channel (Step 3) and run it again, or pin the post by hand. |
| `Feed failed: Games/IGN (HTTP 403)` | That site blocked the bot for now. The other feeds still post. If it keeps happening, turn that feed off in `config.yaml`. |
| `All news feeds failed` | Internet problem, or the sites are all blocking. Check again later. |
| `Nothing could be posted this run` (the run is red) | Every post this run was refused. Read the `WARNING` lines just above it in the log. |
| `Could not save data/posted.json` | Settings > Actions > General > Workflow permissions: choose **Read and write permissions**, then run the workflow again. |
| `The posted-stories file could not be read` | Nothing to do. The bot set the broken file aside and re-learned the current stories without posting them; the next run is normal. |
| No posts, `0 chosen for this run` | Nothing new since the last check. Normal. |
| The channel feels too quiet | Lower `min_score` in `config.yaml` (for example to 1), or raise `max_posts_per_run`. The log line `... weak stories skipped for now` shows how many stories missed the bar. |
| No top 5 or top 10 post | It needs at least `min_stories` posts from the period. The log says `Digest skipped` or `Weekly skipped` with the reason. |
| The Actions schedule stopped | Actions tab > News bot > **Enable workflow**. |

Every run's log is in the **Actions** tab: open a run, then the **post-news** job.

## For developers

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest --cov          # tests and coverage
.venv\Scripts\python -m ruff check newsbot tests
.venv\Scripts\python -m black --check newsbot tests
```

Code layout (`newsbot/`):

| File | Job |
|---|---|
| `cli.py` | Command line: normal run and all the options above |
| `runner.py` | One run from start to end |
| `feeds.py` | Download and read RSS/Atom feeds |
| `images.py` | Find an image in the feed or on the article page |
| `quality.py` | 18+ and filler rules, finding the same story on several sites, scoring |
| `selector.py` | Pick which stories to post (fresh, allowed, not a repeat, strong enough, strongest first) |
| `banner.py` | Draw the branded banner (English and mirrored Arabic) and the welcome banner |
| `formatter.py` | The caption, the two buttons and the name hashtags, within Telegram's 1024-character caption limit |
| `editions.py` | The English and Arabic labels for each channel |
| `publish.py` | Turn a story into a post for one channel and send it; translate it |
| `translate.py` | Google Gemini client that translates into Arabic and checks each answer |
| `digest.py`, `scheduled.py` | The evening top 5 and the weekly top 10 with its poll |
| `telegram.py` | Telegram Bot API client with retries and clear setup errors |
| `poster.py` | Send the banner, or fall back to the photo, then text |
| `setup_tools.py` | `--check`, `--find-chat-id`, `--setup-language-picker` |
| `state.py` | Remember posted stories in `data/posted.json` |
| `config.py` | Read and check `config.yaml` |
| `text.py` | Clean HTML, shorten text, split sentences, normalise links |

Fonts in `assets/fonts` are Oswald and Noto Kufi Arabic (SIL Open Font License, files included). The logo badge and suit icons in `assets/brand` are cut from the channel logo.
