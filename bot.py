"""Restricted prefix command and persistent role enforcement."""
import asyncio
import getpass
import logging
import os
from pathlib import Path
import sqlite3
import sys
from datetime import timedelta

import discord
from discord.ext import commands, tasks

ALLOWED_ROLE_ID = 1544087768903196841
GIVEN_ROLE_ID = 1544087768890736700
LOG_CHANNEL_ID = 1544087774985060575
log = logging.getLogger("role_bot")


def database_path():
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if os.environ.get("RAILWAY_ENVIRONMENT_ID") and not mount:
        raise RuntimeError("Attach a Railway volume at /data before starting the bot.")
    folder = Path(mount) if mount else Path(__file__).parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "grants.sqlite3"


class GrantStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS grants (guild INTEGER, member INTEGER, PRIMARY KEY(guild, member))")
        self.db.execute("CREATE TABLE IF NOT EXISTS recovery (guild INTEGER PRIMARY KEY, cutoff TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
        self.db.commit()

    def approved(self, guild, member):
        return self.db.execute("SELECT 1 FROM grants WHERE guild=? AND member=?", (guild, member)).fetchone() is not None

    def set(self, guild, member, approved):
        with self.db:
            if approved:
                self.db.execute("INSERT OR IGNORE INTO grants VALUES (?, ?)", (guild, member))
            else:
                self.db.execute("DELETE FROM grants WHERE guild=? AND member=?", (guild, member))


def has_role(member, role_id):
    return any(role.id == role_id for role in member.roles)


def recovery_candidates(entries, bot_id, cutoff):
    seen = set()
    reasons = {"Role was not granted through ,verifyfemale",
               "Role was not granted by an authorized female verifier"}
    for entry in entries:  # newest first; later human decisions always win
        target_id = getattr(entry.target, "id", None)
        added = any(r.id == GIVEN_ROLE_ID for r in getattr(entry.after, "roles", []))
        removed = any(r.id == GIVEN_ROLE_ID for r in getattr(entry.before, "roles", []))
        if target_id is None or target_id in seen or not (added or removed):
            continue
        seen.add(target_id)
        if (removed and not added and entry.user_id == bot_id and entry.reason in reasons
                and entry.created_at < cutoff):
            yield target_id


def verification_embed(member, verifier, role, manual=False):
    embed = discord.Embed(
        title="🌴 FEMALE WHITELIST 🌴",
        description=f"{member.mention}, you are now **female whitelisted**!\n\n"
                    "You have been verified by a **female verifier**. 🌴",
        color=0xFF69B4,
    )
    embed.add_field(name="🌴 Your role", value=role.mention)
    embed.add_field(name="🌴 Verified by", value=verifier.mention)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="🌴 Verified through Discord roles" if manual else "🌴 Welcome to the female whitelist!")
    return embed


class RoleBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=",", intents=intents, help_command=None,
                         allowed_mentions=discord.AllowedMentions.none())
        self.grants = GrantStore(database_path())
        self.guard = asyncio.Lock()
        self.initialized = set()
        self.pending = set()

    async def setup_hook(self):
        self.sweep.start()

    async def close(self):
        self.sweep.cancel()
        await super().close()
        self.grants.db.close()

    async def on_ready(self):
        print(f"Online as {self.user}. Command: ,verifyfemale @member")

    async def enforce(self, member):
        # Call while holding guard. Discord failures are logged and retried by sweep.
        if not self.grants.approved(member.guild.id, member.id) and has_role(member, GIVEN_ROLE_ID):
            verifier = await self.manual_verifier(member)
            if verifier is None:
                # Missing/late audit evidence must never strip a legitimate role.
                return
            if verifier is not False:
                channel = self.get_channel(LOG_CHANNEL_ID) or await self.fetch_channel(LOG_CHANNEL_ID)
                if channel.guild.id != member.guild.id:
                    raise RuntimeError("The log channel must belong to the role's server.")
                role = member.guild.get_role(GIVEN_ROLE_ID)
                await channel.send(embed=verification_embed(member, verifier, role, manual=True),
                                   allowed_mentions=discord.AllowedMentions.none())
                self.grants.set(member.guild.id, member.id, True)
                return
            await member.remove_roles(discord.Object(GIVEN_ROLE_ID),
                                      reason="Role was not granted by an authorized female verifier", atomic=True)
            log.info("Removed unapproved role from %s in %s", member.id, member.guild.id)

    async def manual_verifier(self, member):
        # Only the latest relevant audit change can authorize this grant.
        # A short lookback avoids treating an old verification as a new approval.
        cutoff = discord.utils.utcnow() - timedelta(minutes=5)
        async for entry in member.guild.audit_logs(limit=200, action=discord.AuditLogAction.member_role_update,
                                                   after=cutoff, oldest_first=False):
            if getattr(entry.target, "id", None) != member.id:
                continue
            added = any(r.id == GIVEN_ROLE_ID for r in getattr(entry.after, "roles", []))
            removed = any(r.id == GIVEN_ROLE_ID for r in getattr(entry.before, "roles", []))
            if not added and not removed:
                continue
            if removed or entry.user_id is None or (self.user and entry.user_id == self.user.id):
                return None
            try:
                actor = await member.guild.fetch_member(entry.user_id)
            except discord.NotFound:
                return None
            return actor if not actor.bot and has_role(actor, ALLOWED_ROLE_ID) else False
        return None

    async def on_member_update(self, before, after):
        if after.guild.id not in self.initialized:
            return
        added = not has_role(before, GIVEN_ROLE_ID) and has_role(after, GIVEN_ROLE_ID)
        if added:
            self.pending.add((after.guild.id, after.id))
            # Audit entries can arrive after the member update.
            await asyncio.sleep(3)
        async with self.guard:
            if has_role(before, GIVEN_ROLE_ID) and not has_role(after, GIVEN_ROLE_ID):
                self.grants.set(after.guild.id, after.id, False)
            try:
                if added:
                    after = await after.guild.fetch_member(after.id)
                    await self.enforce(after)
            except discord.HTTPException:
                log.exception("Could not enforce role for %s; periodic scan will retry", after.id)

    async def on_member_remove(self, member):
        async with self.guard:
            self.grants.set(member.guild.id, member.id, False)

    async def on_member_join(self, member):
        async with self.guard:
            self.grants.set(member.guild.id, member.id, False)
            # Role additions after joining are handled by member-update events.

    async def recover_and_preserve(self, guild):
        async with self.guard:
            # Preserve existing holders even if the old database is missing.
            async for member in guild.fetch_members(limit=None):
                if has_role(member, GIVEN_ROLE_ID):
                    self.grants.set(guild.id, member.id, True)
            with self.grants.db:
                self.grants.db.execute("INSERT OR IGNORE INTO recovery(guild, cutoff) VALUES (?, ?)",
                                       (guild.id, discord.utils.utcnow().isoformat()))
            cutoff_text, done = self.grants.db.execute(
                "SELECT cutoff, done FROM recovery WHERE guild=?", (guild.id,)).fetchone()
            if not done:
                cutoff = discord.utils.parse_time(cutoff_text)
                entries = [entry async for entry in guild.audit_logs(
                    limit=None, action=discord.AuditLogAction.member_role_update, oldest_first=False)]
                restored = 0
                for member_id in recovery_candidates(entries, self.user.id, cutoff):
                    try:
                        member = await guild.fetch_member(member_id)
                    except discord.NotFound:
                        continue
                    if has_role(member, GIVEN_ROLE_ID):
                        continue
                    # Recheck latest history immediately before changing this member.
                    fresh = [entry async for entry in guild.audit_logs(
                        limit=None, action=discord.AuditLogAction.member_role_update,
                        after=cutoff, oldest_first=False)]
                    if any(getattr(e.target, "id", None) == member_id and
                           any(r.id == GIVEN_ROLE_ID for r in
                               getattr(e.before, "roles", []) + getattr(e.after, "roles", [])) for e in fresh):
                        continue
                    self.grants.set(guild.id, member_id, True)
                    try:
                        await member.add_roles(discord.Object(GIVEN_ROLE_ID), atomic=True,
                                               reason="Restore previous automatic female-role removal by this bot")
                    except Exception:
                        self.grants.set(guild.id, member_id, False)
                        raise
                    restored += 1
                    log.info("Restored female role to %s", member_id)
                channel = self.get_channel(LOG_CHANNEL_ID) or await self.fetch_channel(LOG_CHANNEL_ID)
                if channel.guild.id != guild.id:
                    raise RuntimeError("Recovery log channel is in a different server")
                await channel.send(embed=discord.Embed(
                    title="🌴 FEMALE ROLE RECOVERY 🌴", color=0xFF69B4,
                    description=f"Restored **{restored}** role(s) this run from this bot's previous automatic removals.\n"
                                "Existing role holders are now protected. Later removals by other people were skipped."))
                with self.grants.db:
                    self.grants.db.execute("UPDATE recovery SET done=1 WHERE guild=?", (guild.id,))
            self.initialized.add(guild.id)

    @tasks.loop(minutes=2)
    async def sweep(self):
        # Never sweep existing holders for removal. Only inspect observed new grants.
        for guild in self.guilds:
            if guild.get_role(GIVEN_ROLE_ID) is None:
                continue
            try:
                if guild.id not in self.initialized:
                    await self.recover_and_preserve(guild)
                for guild_id, member_id in list(self.pending):
                    if guild_id != guild.id:
                        continue
                    async with self.guard:
                        try:
                            current = await guild.fetch_member(member_id)
                        except discord.NotFound:
                            self.pending.discard((guild_id, member_id))
                            continue
                        if not has_role(current, GIVEN_ROLE_ID):
                            self.grants.set(guild.id, current.id, False)
                        try:
                            await self.enforce(current)
                            if self.grants.approved(guild_id, member_id) or not has_role(current, GIVEN_ROLE_ID):
                                self.pending.discard((guild_id, member_id))
                        except discord.HTTPException:
                            log.exception("Role enforcement failed for %s; check permissions/hierarchy", current.id)
            except discord.HTTPException:
                log.exception("Member scan failed for guild %s; retrying next scan", guild.id)

    @sweep.before_loop
    async def before_sweep(self):
        await self.wait_until_ready()


def build_bot():
    bot = RoleBot()

    @bot.command(name="verifyfemale", ignore_extra=False)
    @commands.guild_only()
    async def verifyfemale(ctx, member: discord.Member):
        if ctx.guild.id not in bot.initialized:
            await ctx.send("🌴 Preserving existing roles and recovering previous removals. Please try again shortly.")
            return
        async with bot.guard:
            caller = await ctx.guild.fetch_member(ctx.author.id)
            if not has_role(caller, ALLOWED_ROLE_ID):
                await ctx.send("You do not have the role required to use this command.")
                return
            role = ctx.guild.get_role(GIVEN_ROLE_ID)
            me = await ctx.guild.fetch_member(bot.user.id)
            if role is None or role.managed or role.is_default():
                await ctx.send("The configured role is missing or cannot be assigned.")
                return
            if not me.guild_permissions.manage_roles or role >= me.top_role:
                await ctx.send("The bot needs Manage Roles and its role must be above the female role.")
                return
            target = await ctx.guild.fetch_member(member.id)
            if has_role(target, GIVEN_ROLE_ID):
                bot.grants.set(ctx.guild.id, target.id, True)
                await ctx.send("That member already has the role. Their role has been preserved.")
                return
            # Persist before the request so the gateway event recognizes our grant.
            bot.grants.set(ctx.guild.id, target.id, True)
            try:
                await target.add_roles(role, reason=f",verifyfemale by {caller.id}", atomic=True)
            except Exception:
                bot.grants.set(ctx.guild.id, target.id, False)
                raise
        await ctx.send(embed=verification_embed(member, ctx.author, role))

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Use this command in the server.")
        elif isinstance(error, commands.UserInputError):
            embed = discord.Embed(title="🌴 FEMALE VERIFICATION 🌴",
                                  description="Mention the member you want to verify.\n\n"
                                              "**Usage**\n`,verifyfemale @member`\n\n"
                                              "You can also use their member ID. 🌴",
                                  color=0xFF69B4)
            await ctx.send(embed=embed)
        else:
            original = getattr(error, "original", error)
            log.error("Command failed", exc_info=(type(original), original, original.__traceback__))
            await ctx.send("Could not finish. Check the bot's permissions and role position, then try again.")

    return bot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token and sys.stdin.isatty():
        token = getpass.getpass("Paste your bot token (hidden): ")
    if not token.strip():
        raise SystemExit("Set DISCORD_TOKEN in Railway service Variables, then redeploy.")
    build_bot().run(token.strip())
