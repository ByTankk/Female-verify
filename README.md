# Female verification bot

Command: `,verifyfemale @member` (a member ID also works).

Only members with role `1544087768903196841` can use it, including administrators. It grants only role `1544087768890736700`.

The bot records approved grants in `grants.sqlite3`. Role changes trigger removal of unapproved grants, and a startup/every-two-minutes scan retries enforcement. Existing holders without a recorded grant are removed on first startup. Removing the role or leaving the server clears approval when observed by the bot.

## Upload to GitHub and deploy on Railway

1. Extract the ZIP. Create an empty GitHub repository and upload the files **inside** `role-bot` to the repository root. You should see `bot.py`, `Dockerfile`, `requirements.txt`, and `railway.json` at the top level. Upload the extracted files, not the ZIP. Include the dotfiles if using Git.
2. Complete the Discord setup below (application, token, intents, invite, and role position).
3. In Railway, create a project from your GitHub repository and select this repo. The included Dockerfile installs Python and the dependencies; `railway.json` sets the startup command and restart policy.
4. In the bot service's **Variables**, add `DISCORD_TOKEN` with your bot token as its value. Do not add quotes. Never put the actual token in GitHub. `.env.example` is only a reference; the bot reads real environment variables.
5. Attach a **Volume** to this service with mount path **`/data`**. Railway supplies `RAILWAY_VOLUME_MOUNT_PATH` automatically. The bot writes `/data/grants.sqlite3` so approved grants survive redeploys. It refuses to start on Railway without a volume. The volume must be created in Railway; the JSON file does not create it.
6. Deploy/redeploy after saving the variable and volume. An initial deployment before those are configured may fail; that is expected. Check logs for `Online as ...`.
7. Keep **one replica** and disable Serverless/app sleeping if enabled. This is a background Discord worker: no domain, HTTP port, or HTTP health check is needed.
8. Test `,verifyfemale @member` in Discord. Future GitHub pushes can redeploy the connected service.

If you already ran the bot locally, stop the local instance and migrate its `grants.sqlite3` to the volume before starting the hosted bot. Without that database, existing holders count as unapproved. Do not commit the database to GitHub.

## Discord setup and optional local run

1. For local use only, install Python 3.10+ from https://www.python.org/downloads/ and enable Add Python to PATH. Railway installs Python through the Dockerfile.
2. Go to https://discord.com/developers/applications and create a New Application.
3. Open Bot, obtain a token using Reset Token, and keep it private.
4. Under Bot > Privileged Gateway Intents, enable **Server Members Intent** and **Message Content Intent**.
5. Under OAuth2 > URL Generator, select the `bot` scope and permissions **Manage Roles**, **View Channels**, **Send Messages**, and **Embed Links**. Open the generated URL to invite the bot. If already installed, enable **Embed Links** for the bot in the command channel.
6. In Server Settings > Roles, move the bot's role above `1544087768890736700`. Ensure it can view and send messages in the command channel.
7. Extract the ZIP and open a terminal inside the `role-bot` folder. Run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bot.py
```

8. Paste the token at the hidden prompt and press Enter. Alternatively, set the `DISCORD_TOKEN` environment variable on your host.
9. Once online, type `,verifyfemale @member` in the server.

Keep the program running. For 24/7 operation use an always-on computer or Python-capable host. Keep `grants.sqlite3` on persistent storage and back it up: losing it makes existing grants unapproved. Run only one instance.

## Behavior and limits

- Replies are ordinary channel messages. They do not send mention notifications.
- Successful verification posts a pink embed with palm tree emojis, the verified member's avatar, their role, and the verifier.
- Other roles are preserved.
- Manual additions and other bots' additions to unapproved members are removed after Discord delivers the event, subject to API delays and permissions.
- Even a user with the allowed role must use this command; manually assigning the role does not approve it.
- If the target already has an unapproved grant, the command removes it and asks you to run the command again.
- The bot cannot remove roles while offline or without Manage Roles/the required hierarchy. Failed removals are logged and retried on the periodic scan.
- On reconnect, it removes holders without saved approval. If a previously approved member has their role removed and re-added entirely while the bot is offline, the saved approval cannot distinguish that from an uninterrupted grant. Keep the bot online for event-based enforcement.
- This enforces grants through this bot. It does not remove other members' native Manage Roles permissions.

## Server checks after setup

1. With the allowed role, verify a member: the target gets the role and keeps it through a restart.
2. Without the allowed role (even as admin), try the command: it must deny access.
3. Manually give an unapproved member the role: it must be removed.
4. Manually give it using an account that has the allowed role: it must still be removed.
5. Remove an approved member's role, wait for the update, then manually re-add it: it must be removed.
6. While the bot is stopped, give an unapproved member the role. Start the bot: its scan must remove it.

## Documentation

- Intents: https://discordpy.readthedocs.io/en/stable/intents.html
- Permissions: https://docs.discord.com/developers/topics/permissions
- Railway configuration: https://docs.railway.com/config-as-code/reference
- Railway volume variable: https://docs.railway.com/variables/reference

Live Discord testing requires your token and server installation.
