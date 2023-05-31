from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_chart_arguments, process_task

from commands.base import BaseCommand, ActionsView


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
				payload, responseMessage = await process_task(task, "chart")

				if payload is None:
					errorMessage = f"Requested orderflow data for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
				else:
					currentTask = task.get(payload.get("platform"))
					actions = ActionsView(user=ctx.author, command=ctx.command.mention)
					try: await ctx.interaction.edit_original_response(file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))), view=actions)
					except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"flow": Increment(1)}}, merge=True)
			await self.cleanup(ctx, request, removeView=True)

		else:
			embed = discord.Embed(title=":gem: Options and crypto orderflow are available as a add-on subscription for communities or individuals for only $15.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro).", color=constants.colors["deep purple"])
			# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass

	async def flow_proxy(self, ctx, tickerId, autodelete):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			[(responseMessage, task), _] = await gather(
				process_chart_arguments([], ["Alpha Flow"], tickerId=tickerId),
				ctx.defer()
			)

			if responseMessage is not None:
				embed = discord.Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alpha.bot/pro/flow).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			self.respond(ctx, request, task)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /flow {tickerId} autodelete:{autodelete}")
			await self.unknown_error(ctx)

	@flowGroup.command(name="overview", description="Pull aggregated stocks orderflow overview.")
	async def flow_overview(
		self,
		ctx,
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		await self.flow_proxy(ctx, "options", autodelete)

	@flowGroup.command(name="search", description="Pull aggregated orderflow of a single stock.")
	async def flow_search(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		await self.flow_proxy(ctx, tickerId.upper(), autodelete)