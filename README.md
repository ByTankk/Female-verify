# Female verification bot

Command: `,verifyfemale @member` (a member ID also works).

Only members with role `1544087768903196841` can use it, including administrators. It grants only role `1544087768890736700`.

The bot preserves everyone who already has the role at startup. It never removes an existing role just because the database has no record of it.

## Recovery update — deploy this version with the same bot token

On its first successful run, the bot preserves current holders, scans available Discord audit history, and restores only the target role removed by **this same bot**, with one of its two previous automatic-enforcement reasons. It checks the latest relevant role change for each person, skips later additions/removals by other people, skips people who left, and skips people who already have the role. It posts a pink recovery summary to `1544087774985060575`.

Keep the existing `/data` volume and token. Give the bot **View Audit Log**, **Manage Roles**, and a role position above the female role. The log channel needs **View Channel**, **Send Messages**, and **Embed Links**. Upload the updated files to GitHub and redeploy. Stop any old bot instance so it cannot keep removing roles during recovery.

Recovery is tracked in the database and does not repeatedly undo future moderation. If a request fails, the bot retries on its next scan; already restored members are skipped. Recovery covers only records still available in Discord's audit history and does not guess missing records. Check Railway logs if no summary appears.

## Upload to GitHub and deploy on Railway

1. Extract the ZIP. Create an empty GitHub repository and upload the files **inside** `role-bot` to the repository root. You should see `bot.py`, `Dockerfile`, `requirements.txt`, and `railway.json` at the top level. Upload the extracted files, not the ZIP. Include the dotfiles if using Git.
2. Complete the Discord setup below (application, token, intents, invite, and role position).
3. In Railway, create a project from your GitHub repository and select this repo. The included Dockerfile installs Python and the dependencies; `railway.json` sets the startup command and restart policy.
4. In the bot service's **Variables**, add `DISCORD_TOKEN` with your bot token as its value. Do not add quotes. Never put the actual token in GitHub. `.env.example` is only a reference; the bot reads real environment variables.
5. Attach a **Volume** to this service with mount path **`/data`**. Railway supplies `RAILWAY_VOLUME_MOUNT_PATH` automatically. The bot writes `/data/grants.sqlite3` so approved grants survive redeploys. It refuses to start on Railway without a volume. The volume must be created in Railway; the JSON file does not create it.
6. Deploy/redeploy after saving the variable and volume. An initial deployment before those are configured may fail; that is expected. Check logs for `Online as ...`.
7. Keep **one replica** and disable Serverless/app sleeping if enabled. This is a background Discord worker: no domain, HTTP port, or HTTP health check is needed.
8. Test `,verifyfemale @member` in Discord. Future GitHub pushes can redeploy the connected service.

If you already ran the bot locally, stop the local instance and migrate its `grants.sqlite3` to the volume before starting the hosted bot. Existing holders are preserved even without the database, but keeping it avoids losing recovery history. Do not commit the database to GitHub.

## Discord setup and optional local run

1. For local use only, install Python 3.10+ from https://www.python.org/downloads/ and enable Add Python to PATH. Railway installs Python through the Dockerfile.
2. Go to https://discord.com/developers/applications and create a New Application.
3. Open Bot, obtain a token using Reset Token, and keep it private.
4. Under Bot > Privileged Gateway Intents, enable **Server Members Intent** and **Message Content Intent**.
5. Under OAuth2 > URL Generator, select the `bot` scope and permissions **Manage Roles**, **View Audit Log**, **View Channels**, **Send Messages**, and **Embed Links**. Open the generated URL to invite the bot. If already installed, enable **Embed Links** for the bot in the command channel.
6. In Server Settings > Roles, move the bot's role above `1544087768890736700`. Ensure it can view and send messages in the command channel.
7. Extract the ZIP and open a terminal inside the `role-bot` folder. Run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bot.py
```

8. Paste the token at the hidden prompt and press Enter. Alternatively, set the `DISCORD_TOKEN` environment variable on your host.
9. Once online, type `,verifyfemale @member` in the server.

Keep the program running. For 24/7 operation use an always-on computer or Python-capable host. Keep `grants.sqlite3` on persistent storage and back it up to retain approvals and recovery history. Run only one instance.

## Behavior and limits

- Command: `,verifyfemale @member`. Missing arguments show a public pink usage embed.
- Only verifier role `1544087768903196841` can use the command; the only granted role is `1544087768890736700`.
- Successful command verification posts a pink palm-tree embed in the command channel.
- A human verifier's manual role grant is accepted and logged in `1544087774985060575` when the audit entry identifies the verifier. The verifier also needs native Discord permission to assign the role manually.
- Existing holders at startup are preserved. Grants made while the bot is offline are also preserved at the next startup; there is no retrospective mass-removal scan.
- Only newly observed role additions are checked. A confirmed unauthorized actor causes removal. Missing, delayed, or inaccessible audit evidence does not cause removal. The bot retries pending checks every two minutes, looking at up to 200 role updates from the last five minutes.
- The verifier's current roles are used when checking audit entries. Keep the bot online for timely checks.
- Other roles remain unchanged. Log embeds do not send mention notifications.
- Run one instance. During recovery, the verification command asks users to wait.

## Verify after deployment

1. Check the pink recovery summary and Railway logs for restored member IDs.
2. Confirm an existing holder keeps the role across a restart.
3. Verify a new member with the command and check the public pink embed.
4. Give the role manually as a verifier and check the log channel.
5. Give it manually without the verifier role: when the audit log identifies that actor, the bot removes it.

## Documentation

- Intents: https://discordpy.readthedocs.io/en/stable/intents.html
- Permissions: https://docs.discord.com/developers/topics/permissions
- Railway configuration: https://docs.railway.com/config-as-code/reference
- Railway volume variable: https://docs.railway.com/variables/reference

Live Discord testing requires your token and server installation.
