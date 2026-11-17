"""Restricted prefix command and persistent role enforcement."""
import asyncio
import getpass
import logging
import os
from pathlib import Path
import sqlite3
import sys

import discord
from discord.ext import commands, tasks

ALLOWED_ROLE_ID = 1544087768903196841
GIVEN_ROLE_ID = 1544087768890736700
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


class RoleBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=",", intents=intents, help_command=None,
                         allowed_mentions=discord.AllowedMentions.none())
        self.grants = GrantStore(database_path())
        self.guard = asyncio.Lock()

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
            await member.remove_roles(discord.Object(GIVEN_ROLE_ID),
                                      reason="Role was not granted through ,verifyfemale", atomic=True)
            log.info("Removed unapproved role from %s in %s", member.id, member.guild.id)

    async def on_member_update(self, before, after):
        async with self.guard:
            if has_role(before, GIVEN_ROLE_ID) and not has_role(after, GIVEN_ROLE_ID):
                self.grants.set(after.guild.id, after.id, False)
            try:
                await self.enforce(after)
            except discord.HTTPException:
                log.exception("Could not enforce role for %s; periodic scan will retry", after.id)

    async def on_member_remove(self, member):
        async with self.guard:
            self.grants.set(member.guild.id, member.id, False)

    async def on_member_join(self, member):
        async with self.guard:
            self.grants.set(member.guild.id, member.id, False)
            try:
                await self.enforce(member)
            except discord.HTTPException:
                log.exception("Could not enforce joining member")

    @tasks.loop(minutes=2)
    async def sweep(self):
        # Startup and periodic reconciliation also catch grants made while offline.
        for guild in self.guilds:
            if guild.get_role(GIVEN_ROLE_ID) is None:
                continue
            try:
                async for member in guild.fetch_members(limit=None):
                    async with self.guard:
                        # Refresh after taking the lock so command writes cannot race this snapshot.
                        current = await guild.fetch_member(member.id)
                        if not has_role(current, GIVEN_ROLE_ID):
                            self.grants.set(guild.id, current.id, False)
                        try:
                            await self.enforce(current)
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
                if bot.grants.approved(ctx.guild.id, target.id):
                    await ctx.send("That member is already verified.")
                else:
                    await bot.enforce(target)
                    await ctx.send("Removed an unapproved role. Run the command again to verify this member.")
                return
            # Persist before the request so the gateway event recognizes our grant.
            bot.grants.set(ctx.guild.id, target.id, True)
            try:
                await target.add_roles(role, reason=f",verifyfemale by {caller.id}", atomic=True)
            except Exception:
                bot.grants.set(ctx.guild.id, target.id, False)
                raise
        embed = discord.Embed(
            title="🌴 FEMALE WHITELIST 🌴",
            description=(
                f"{member.mention}, you are now **female whitelisted**!\n\n"
                "You have been verified by a **female verifier**. 🌴"
            ),
            color=0xFF69B4,
        )
        embed.add_field(name="🌴 Your role", value=role.mention, inline=True)
        embed.add_field(name="🌴 Verified by", value=ctx.author.mention, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="🌴 Welcome to the female whitelist!")
        await ctx.send(embed=embed)

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Use this command in the server.")
        elif isinstance(error, commands.UserInputError):
            await ctx.send("Usage: ,verifyfemale @member (a member ID also works)")
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
