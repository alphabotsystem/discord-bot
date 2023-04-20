from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommand, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import autocomplete_layout_timeframe, process_chart_arguments, process_task

from commands.base import BaseCommand, ActionsView
from commands.ichibot import Ichibot


snapshots = FirestoreClient()


class LayoutWrapper(BaseCommand):
	def __init__(self, bot, create_request, database, logging):
		super().__init__(bot, create_request, database, logging)
		self.timestamp = 0
		self.layouts = {}
		self.observer = snapshots.collection("discord/properties/layouts").on_snapshot(self.listener)
		self.layoutGroup = self.bot.create_group("layout", "Pull a saved public layout from TradingView.", guild_ids=[])

	def listener(self, snapshot, changes, timestamp):
		layouts = {}
		for e in snapshot:
			layout = e.to_dict()
			guildId = int(layout["guildId"])
			label = layout["label"]
			if guildId not in layouts: layouts[guildId] = {}
			layouts[guildId][label] = layout["url"]
		self.layouts = layouts
		self.timestamp = timestamp

		self.bot.loop.create_task(self.update_commands(changes, timestamp))

	async def update_commands(self, changes, timestamp):
		await self.bot.wait_until_ready()
		if timestamp != self.timestamp: return
		print(f"Updating layout commands at {timestamp}")

		guildIds = [g.id for g in self.bot.guilds if g.id in self.layouts]
		old = self.bot.remove_application_command(self.layoutGroup)
		removals = old.guild_ids

		self.layoutGroup = self.bot.create_group("layout", "Pull a saved public layout from TradingView.", guild_ids=guildIds)

		commands = {}
		for guildId, mappings in self.layouts.items():
			commands[guildId] = {}
			for command, url in mappings.items():
				if guildId not in guildIds: continue

				async def wrapper(ctx, tickerId, timeframe, venue):
					await self.layout(ctx, url, tickerId, timeframe, venue)

				handler = SlashCommand(
					wrapper,
					name=command,
					description=f"Pull a public layout called {command} from TradingView.",
					guild_ids=[guildId],
					parent=self.layoutGroup,
					options=[
						Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
						Option(str, "Timeframe and coloring method for the heatmap.", name="timeframe", autocomplete=autocomplete_layout_timeframe, required=False, default=""),
						Option(str, "Venue to pull the price from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default="")
					]
				)
				self.layoutGroup.add_command(handler)
				commands[guildId][command] = handler

				try: removals.remove(guildId)
				except ValueError: pass

		await self.bot.sync_commands(check_guilds=removals)

	async def layout(self, ctx, url, tickerId, timeframe, venue):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			arguments = [timeframe, venue]
			[(responseMessage, task), _] = await gather(
				process_chart_arguments(arguments, ["TradingView Relay"], tickerId=tickerId.upper()),
				ctx.defer()
			)

			if responseMessage is not None:
				description = "[Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else "Detailed guide with examples is available on [our website](https://www.alpha.bot/features/charting)."
				embed = Embed(title=responseMessage, description=description, color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, url, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /layout {url} {tickerId} timeframe:{timeframe} venue:{venue}")
			await self.unknown_error(ctx)

	async def respond(
		self,
		ctx,
		url,
		request,
		task
	):
		if request.flow_available():
			start = time()
			files, embeds = [], []

			task["TradingView Relay"]["url"] = url

			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, responseMessage = await process_task(task, "chart", origin=request.origin)

				if payload is None:
					errorMessage = f"Requested chart for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					task["currentPlatform"] = payload.get("platform")
					currentTask = task.get(task.get("currentPlatform"))
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

			actions = None
			if len(files) != 0:
				actions = ActionsView(user=ctx.author, command=ctx.command.mention)

			requestCheckpoint = time()
			request.set_delay("request", (requestCheckpoint - start) / (len(files) + len(embeds)))
			try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
			except NotFound: pass
			request.set_delay("response", time() - requestCheckpoint)

			await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(1)}}, merge=True)
			await self.log_request("layouts", request, [task], telemetry=request.telemetry)
			await self.cleanup(ctx, request, removeView=True)

		else:
			embed = discord.Embed(title=":gem: TradingView Layouts are available for $10.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/tradingview-layouts).", color=constants.colors["deep purple"])
			# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass

