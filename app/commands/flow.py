from os import environ
from time import time
from random import randint
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand
from commands.ichibot import Ichibot


class FlowCommand(BaseCommand):
	flowGroup = SlashCommandGroup("flow", "Pull aggregated stocks orderflow.")

	async def respond(
		self,
		ctx,
		request,
		task
	):
		if request.flow_available():
			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, chartText = await Processor.process_task("chart", request.authorId, task)

				if payload is None:
					errorMessage = "Requested orderflow data for `{}` is not available.".format(currentTask.get("ticker").get("name")) if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					currentTask = task.get(payload.get("platform"))
					actions = ActionsView(userId=request.authorId)
					await ctx.interaction.edit_original_message(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))), view=actions)

			await self.database.document("discord/statistics").set({request.snapshot: {"flow": Increment(1)}}, merge=True)
			await self.cleanup(ctx, request, removeView=True)
		elif request.is_pro():
			if not message.author.bot and message.channel.permissions_for(message.author).administrator:
				embed = discord.Embed(title=":microscope: Alpha Flow is disabled.", description="You can enable Alpha Flow feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(request.guildId), color=constants.colors["gray"])
				embed.set_author(name="Alpha Flow", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = discord.Embed(title=":microscope: Alpha Flow is disabled.", description="You can enable Alpha Flow feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
				embed.set_author(name="Alpha Flow", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

		else:
			embed = discord.Embed(title=":gem: Alpha Flow is available to Alpha Pro users or communities for only $15.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
			embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
			await ctx.interaction.edit_original_message(embed=embed)

	async def flow_proxy(self, ctx, tickerId, autodelete):
		request = await self.create_request(ctx, autodelete=autodelete)
		if request is None: return

		embed = Embed(title="Flow command is being updated, and is currently unavailable.", description="An updated flow command is coming after slash commands are stable, which is the priority. All Alpha Pro subscribers using Alpha Flow during August and September 2021 will receive reimbursment in form of credit, or a refund if requested. No charges were made since then. All trials will also be reset.", color=constants.colors["gray"])
		await ctx.interaction.edit_original_message(embed=embed)
		return

		arguments = []
		outputMessage, task = await Processor.process_chart_arguments(request, arguments, tickerId=tickerId, platformQueue=["Alpha Flow"])

		if outputMessage is not None:
			embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/flow).", color=constants.colors["gray"])
			embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
			return
		elif autodelete is not None and (autodelete < 1 or autodelete > 10):
			embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
			await ctx.interaction.edit_original_message(embed=embed)
			return

		self.respond(ctx, request, task)

	@flowGroup.command(name="overview", description="Pull aggregated stocks orderflow overview.")
	async def flow_overview(
		self,
		ctx,
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			await self.flow_proxy(ctx, "options", autodelete)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /flow overview autodelete:{}".format(ctx.author.id, autodelete))
			await self.unknown_error(ctx)

	@flowGroup.command(name="search", description="Pull aggregated orderflow of a single stock.")
	async def flow_(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			await self.flow_proxy(ctx, tickerId.upper(), autodelete)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /flow seearch {} autodelete:{}".format(ctx.author.id, tickerId, autodelete))
			await self.unknown_error(ctx)


class ActionsView(View):
	def __init__(self, userId=None):
		super().__init__(timeout=None)
		self.userId = userId

	@button(emoji=PartialEmoji.from_str("<:remove_response:929342678976565298>"), style=ButtonStyle.gray)
	async def delete(self, button: Button, interaction: Interaction):
		if self.userId != interaction.user.id: return
		await interaction.message.delete()