# Telegram News Bot

Posts the latest **AI, Games, Anime and Tech** news to one Telegram channel.

- Checks 28 news sites (RSS feeds) every 30 minutes.
- Posts only **strong news**: it skips filler (top-10 lists, how-tos, reviews, deals, opinion columns) and prefers stories that several sites report at the same time.
- Each post has the article image, a topic line with an emoji (`🎮 GAMES`), a **TOP STORY** label when 3 or more sites report it, the title in bold, a short summary, the source with a "Read more" link, a hashtag and your channel name.
- At most 3 posts per check, strongest first. A quiet half hour posts nothing rather than something weak.
- Never posts the same news twice, even when another site words it differently.
- Every evening at 21:00 (Saudi time) it posts the day's top 5 stories.
- Runs free on GitHub Actions. Your PC can be off.

The setup takes about 15 minutes. Follow the steps in order.

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
3. Make sure **Post Messages** is turned on. Save.

## Step 4: Test it on your PC (recommended)

Open PowerShell in this folder and run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

In Notepad, fill in the two values and save:

```
TELEGRAM_BOT_TOKEN=123456789:AAH...your token...
TELEGRAM_CHANNEL_ID=@my_news_channel
```

Now run these three commands, one at a time:

```powershell
# 1. Checks the token, the channel, and that the bot can post. Sends nothing.
.venv\Scripts\python -m newsbot --check

# 2. Shows the posts it would send right now. Sends nothing, saves nothing.
.venv\Scripts\python -m newsbot --dry-run

# 3. Posts for real (up to 4 posts) and remembers them in data\posted.json.
.venv\Scripts\python -m newsbot
```

If `--check` says "All good", the Telegram side is ready.

## Step 5: Put it on GitHub so it runs every 30 minutes

**Public or private repository?** Use **public**. GitHub Actions is free for public repositories. The token is stored in GitHub Secrets, which nobody else can see, even in a public repository. A private repository also works, but the runs (about 1,440 a month) use up most of your free monthly Actions allowance.

With the GitHub CLI (`gh`), run this in PowerShell in this folder:

```powershell
git init -b main
git add .
git commit -m "feat: telegram news bot"
gh repo create telegram-news-bot --public --source . --push
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHANNEL_ID
```

Each `gh secret set` asks you to paste the value.

Without the CLI: create an empty repository on github.com, push this folder to it, then open **Settings > Secrets and variables > Actions > New repository secret** and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHANNEL_ID`.

**Start it:** open the repository's **Actions** tab, select **News bot**, then **Run workflow**. The first run posts right away. After that it runs by itself every 30 minutes. Scheduled runs can start a few minutes late when GitHub is busy.

`.env` is in `.gitignore`, so your token is never uploaded.

---

## Changing things

Everything is in `config.yaml`, with a comment on every line:

- **Sources:** add, remove or turn off feeds under each topic. Extra sources are already listed with `#` in front of them; remove the `#` to turn one on.
- **How many posts:** `max_posts_per_run` (posts per check; there are 2 checks per hour).
- **How strong a story must be:** `min_score`. A story gets 2 points for every extra site that reports it, 1 point for a strong word in its title (announced, delayed, trailer, season 2, ...) and 1 point for a big name of its topic (Nintendo, OpenAI, One Piece, ...). Lower it for more posts, raise it for fewer. Anime has its own lower `min_score`, because fewer anime sites exist.
- **Big names:** `big_names` under each topic.
- **What counts as filler:** `filters` (feed categories, link parts, and title patterns). **Strong words:** `filters` > `strong_words`.
- **The look:** `style` (your channel name at the bottom of posts, the TOP STORY label) and `emoji` under each topic.
- **Evening top 5:** `digest` (on/off, hour, your time zone, how many stories, title).
- **How old a story can be:** `max_age_hours`.
- **Summary length:** `summary_max_chars`.
- **New topic:** copy a topic block, change `name`, `hashtag`, `emoji`, `big_names` and `feeds`.

To see what a change does without posting, run `.venv\Scripts\python -m newsbot --dry-run -v`. The `-v` adds a line for every filler story it skipped and why.

**Send the top 5 now:** Actions tab > News bot > Run workflow > tick **Send the daily top stories post now**. On your PC: `.venv\Scripts\python -m newsbot --digest-now` (add `--dry-run` to only see it).

Run `.venv\Scripts\python -m newsbot --dry-run` after a change to see the result. Then commit and push the change.

**How often it checks** is set in `.github/workflows/news-bot.yml` (`cron: "7,37 * * * *"` means minutes 7 and 37 of every hour, UTC).

**Pause the bot:** Actions tab > News bot > the `...` menu > **Disable workflow**. Enable it the same way.

## How it works

1. Downloads every feed in `config.yaml`. A broken feed is logged and skipped; the others still post.
2. Keeps stories newer than `max_age_hours` that are not in `data/posted.json`, and drops filler.
3. Drops a story that is the same news as something already posted, even when the words differ (it compares titles and first sentences, and counts rare words like a game's name more than common words).
4. Groups the same story from different sites together and scores it (see `min_score` above). Stories below the bar wait: if more sites report them later, they can still make it.
5. Picks up to `max_posts_per_run` stories: the topic with the strongest story first, then topics take turns.
6. Posts each story as a photo with a caption. If a feed has no image, it uses the image from the article page. If Telegram rejects the image, it sends a text post with a link preview instead.
   - If Telegram rejects a story completely, the bot remembers that and does not try it again.
   - If the network fails, the story is tried again in the next run.
   - If Telegram gives no clear answer (a timeout or a server error after sending), the bot counts the story as posted, so it can never appear twice.
7. Saves `data/posted.json` after every post. On GitHub, the workflow commits that file back to the repository, so the bot remembers between runs. You will see commits named "chore: update posted news list"; that is normal. Run `git pull` before you push your own changes.

## Private channel

A private channel has no `@username`, so the bot needs its number:

1. Add the bot as an admin (Step 3).
2. Post any message in the channel.
3. Run `.venv\Scripts\python -m newsbot --find-chat-id` (with only `TELEGRAM_BOT_TOKEN` filled in `.env`).
4. Copy the number it shows (it starts with `-100`) into `TELEGRAM_CHANNEL_ID`.

## Troubleshooting

| What you see | What to do |
|---|---|
| `bot token was rejected (401)` | The token is wrong or was revoked. In @BotFather send `/mybots`, pick your bot, open **API Token**, and update `.env` or the GitHub secret. |
| `chat not found` | `TELEGRAM_CHANNEL_ID` is wrong, or the bot is not in the channel. Public channel: use `@username`. Private: use `--find-chat-id`. |
| `not an admin` / `need administrator rights` | Make the bot an admin with **Post Messages** turned on (Step 3). |
| `Feed failed: Games/IGN (HTTP 403)` | That site blocked the bot for now. The other feeds still post. If it keeps happening, turn that feed off in `config.yaml`. |
| `All news feeds failed` | Internet problem, or the sites are all blocking. Check again later. |
| `Nothing could be posted this run` (the run is red) | Every post this run was refused. Read the `WARNING` lines just above it in the log. |
| `Could not save data/posted.json` | Settings > Actions > General > Workflow permissions: choose **Read and write permissions**, then run the workflow again. |
| `The posted-stories file could not be read` | Nothing to do. The bot set the broken file aside and re-learned the current stories without posting them; the next run is normal. |
| No posts, `0 chosen for this run` | Nothing new since the last check. Normal. |
| The channel feels too quiet | Lower `min_score` in `config.yaml` (for example to 1), or raise `max_posts_per_run`. The log line `... weak stories skipped for now` shows how many stories missed the bar. |
| No top 5 post in the evening | It needs at least `min_stories` posts from the last 24 hours. The log says `Digest skipped` with the reason. |
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
| `cli.py` | Command line: normal run, `--dry-run`, `--check`, `--find-chat-id` |
| `runner.py` | One run from start to end |
| `feeds.py` | Download and read RSS/Atom feeds |
| `images.py` | Find an image in the feed or on the article page |
| `selector.py` | Pick which stories to post (fresh, not filler, not a repeat, strong enough, strongest first) |
| `quality.py` | Filler rules, finding the same story on several sites, scoring |
| `digest.py` | The evening top 5 post |
| `formatter.py` | Build the post text (topic line, TOP STORY, headline, summary, link), within Telegram's 1024-character caption limit |
| `telegram.py` | Telegram Bot API client with retries and clear setup errors |
| `poster.py` | Send a photo post, or fall back to a text post |
| `state.py` | Remember posted stories in `data/posted.json` |
| `config.py` | Read and check `config.yaml` |
| `text.py` | Clean HTML, shorten text, split sentences, normalise links |
