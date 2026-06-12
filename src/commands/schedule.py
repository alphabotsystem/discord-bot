from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, Permissions
from discord.commands import SlashCommandGroup
from discord.ui import View, button, Button
from discord.errors import NotFound

from helpers import constants
from assets import static_storage

from commands.base import BaseCommand, RedirectView, TryV2View


class ScheduleCommand(BaseCommand):
	scheduleGroup = SlashCommandGroup("schedule", "Schedule bot commands to get automatically posted periodically.", guild_only=True, default_member_permissions=Permissions(manage_messages=True))

	async def migration_notice(self, ctx):
		try:
			embed = Embed(title="Scheduling new posts has moved to v2.", description="To schedule new posts, migrate to alpha.bot v2. Your existing scheduled posts will keep running, and you can review them with </schedule list:1102477488715743237>.", color=constants.colors["deep purple"])
			embed.set_author(name="Migrate to v2", icon_url=self.bot.user.avatar.url)
			try: await ctx.respond(embed=embed, view=TryV2View(), ephemeral=True)
			except NotFound: pass
		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule migration notice")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="chart", description="Deprecated — migrate to v2 to schedule new posts.")
	async def chart(self, ctx):
		await self.migration_notice(ctx)

	@scheduleGroup.command(name="layout", description="Deprecated — migrate to v2 to schedule new posts.")
	async def layout(self, ctx):
		await self.migration_notice(ctx)

	@scheduleGroup.command(name="heatmap", description="Deprecated — migrate to v2 to schedule new posts.")
	async def heatmap(self, ctx):
		await self.migration_notice(ctx)

	@scheduleGroup.command(name="price", description="Deprecated — migrate to v2 to schedule new posts.")
	async def price(self, ctx):
		await self.migration_notice(ctx)

	@scheduleGroup.command(name="fgi", description="Deprecated — migrate to v2 to schedule new posts.")
	async def lookup_fgi(self, ctx):
		await self.migration_notice(ctx)

	@scheduleGroup.command(name="list", description="List all scheduled posts.")
	async def schedule_list(self, ctx):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if totalPostCount[0][0].value == 0:
				embed = Embed(title="You haven't set any scheduled posts yet.", color=constants.colors["gray"])
				embed.set_author(name="Scheduled Posts", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed, ephemeral=True)
				except NotFound: pass

			else:
				embed = Embed(title=f"You've created {totalPostCount[0][0].value} scheduled post{'' if totalPostCount[0][0].value == 1 else 's'} in this community. You can manage them on the community dashboard.", color=constants.colors["light blue"])
				try: await ctx.respond(embed=embed, view=RedirectView(f"https://www.alpha.bot/communities/{request.guildId}?tab=2"), ephemeral=True)
				except NotFound: pass

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule list")
			await self.unknown_error(ctx)


class DeleteView(View):
	def __init__(self, database, pathId, userId=None):
		super().__init__(timeout=None)
		self.database = database
		self.pathId = pathId
		self.userId = userId

	@button(label="Delete", style=ButtonStyle.danger)
	async def delete(self, button: Button, interaction: Interaction):
		if self.userId != interaction.user.id: return
		await self.database.document(self.pathId).delete()
		embed = Embed(title="Scheduled post deleted", color=constants.colors["gray"])
		await interaction.response.edit_message(embed=embed, view=None)