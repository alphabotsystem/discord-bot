from os import environ
from time import time
from random import randint
from asyncio import CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, ActionsView
from commands.ichibot import Ichibot


ICHIBOT_TESTING = [
	414498292655980583, 926518026457739304, # 460731020245991424
]


class ChartCommand(BaseCommand):
	# chartGroup = SlashCommandGroup("chart", "Pull charts from TradingView, TradingLite, GoCharting, and more.")

	async def respond(
		self,
		ctx,
		request,
		tasks
	):
		files, embeds = [], []
		for task in tasks:
			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, chartText = await Processor.process_task("chart", request.authorId, task)

				if payload is None:
					errorMessage = "Requested chart for `{}` is not available.".format(currentTask.get("ticker").get("name")) if chartText is None else chartText
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		actions = None
		if len(files) != 0:
			if len(tasks) == 1 and currentTask.get("ticker", {}).get("tradable") and request.guildId in ICHIBOT_TESTING:
				actions = IchibotView(self.bot.loop, currentTask, userId=request.authorId)
			else:
				actions = ActionsView(userId=request.authorId)

		await ctx.interaction.edit_original_message(embeds=embeds, files=files, view=actions)

		await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(len(tasks))}}, merge=True)
		await self.cleanup(ctx, request, removeView=True)

	@slash_command(name="c", description="Pull charts from TradingView, TradingLite, GoCharting, and more. Command for power users.")
	async def c(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			parts = arguments.split(",")
			tasks = []

			if len(parts) > 5:
				embed = Embed(title="Only up to 5 requests are allowed per command.", color=constants.colors["gray"])
				embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			for part in parts:
				partArguments = part.lower().split()
				if len(partArguments) == 0: continue

				outputMessage, task = await Processor.process_chart_arguments(request, partArguments[1:], tickerId=partArguments[0].upper())

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/charting).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return
				elif autodelete is not None and (autodelete < 1 or autodelete > 10):
					embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
					await ctx.interaction.edit_original_message(embed=embed)
					return

				tasks.append(task)
			
			await self.respond(ctx, request, tasks)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /c {} autodelete:{}".format(ctx.author.id, " ".join(arguments), autodelete))
			await self.unknown_error(ctx)


class IchibotView(ActionsView):
	def __init__(self, eventLoop, task, userId=None):
		super().__init__(userId=userId)
		self.eventLoop = eventLoop
		self.task = task
		self.accountProperties = DatabaseConnector(mode="account")

	async def prepare(self, interaction: Interaction):
		accountId = await self.accountProperties.match(interaction.user.id)
		if accountId is None:
			accountProperties = {}
		else:
			accountProperties = await self.accountProperties.get(accountId, {})

		if not accountProperties.get("apiKeys", {}):
			embed = Embed(title="Before you can execute trades via Ichibot, you have to add exchange API keys.", description="You can add API keys for FTX, Binance and Binance Futures to you Alpha Account in your [Ichibot Preferences](https://www.alphabotsystem.com/account/ichibot).", color=constants.colors["gray"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, ephemeral=True)
			return accountId, None, None

		origin = "{}_{}_ichibot".format(accountId, interaction.user.id)
		if origin in Ichibot.sockets:
			socket = Ichibot.sockets.get(origin)
		else:
			socket = Processor.get_direct_ichibot_socket(origin)
			Ichibot.sockets[origin] = socket
			self.eventLoop.create_task(Ichibot.process_ichibot_messages(origin, interaction.user))

		matches = self.task.get("ticker").get("tradable")
		availableKeys = [key for key in accountProperties.get("apiKeys", {}).keys() if key in matches]

		if len(availableKeys) == 0:
			_e = {"ftx": "FTX", "binance": "Binance", "binanceusdm": "Binance Futures"}
			embed = Embed(title="Add API keys for {}.".format(" or ".join([_e[e] for e in matches.keys()])), description="`{}` is only available on {} for which you haven't added API keys yet.".format(self.task.get("ticker").get("name"), " and ".join([_e[e] for e in matches.keys()])), color=constants.colors["gray"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, ephemeral=True)
			return accountId, None, None

		return accountId, availableKeys, socket

	@button(label="Buy", style=ButtonStyle.green)
	async def ichibot_buy(self, button: Button, interaction: Interaction):
		accountId, availableKeys, socket = await self.prepare(interaction)
		if socket is None: return

		quickAction = "check value askprice"

		exchanges = ExchangesView(availableKeys, quickAction)
		embed = Embed(title="Please confirm your buy instruction via Ichibot.", description="You'll be executing `{}` via Ichibot.".format(quickAction), color=constants.colors["pink"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)
		await exchanges.wait()
		exchangeId = exchanges.children[0].values[0]

		command = "instrument {}, {}".format(self.task.get("ticker").get("tradable").get(exchangeId), quickAction)

		await socket.send_multipart([accountId.encode(), exchangeId.encode(), b"init"])
		await sleep(10)
		await socket.send_multipart([accountId.encode(), b"", command.encode()])

	@button(label="Sell", style=ButtonStyle.red)
	async def ichibot_sell(self, button: Button, interaction: Interaction):
		accountId, availableKeys, socket = await self.prepare(interaction)
		if socket is None: return

		quickAction = "check value bidprice"

		exchanges = ExchangesView(availableKeys, quickAction)
		embed = Embed(title="Please confirm your sell instruction via Ichibot.", description="You'll be executing `{}` via Ichibot.".format(quickAction), color=constants.colors["pink"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)
		await exchanges.wait()
		exchangeId = exchanges.children[0].values[0]

		command = "instrument {}, check value bidprice".format(self.task.get("ticker").get("tradable").get(exchangeId), quickAction)

		await socket.send_multipart([accountId.encode(), exchangeId.encode(), b"init"])
		await sleep(10)
		await socket.send_multipart([accountId.encode(), b"", command.encode()])


class ExchangesView(View):
	def __init__(self, exchanges, command):
		super().__init__(timeout=None)
		self.add_item(ExchangesDropdown(exchanges, command, self.callback))

	def callback(self):
		self.stop()


class ExchangesDropdown(Select):
	def __init__(self, exchanges, command, callback):
		self.command = command
		self._callback = callback
		_map = {
			"ftx": ["FTX", "<:ftx:929376008107356160>", "ftx"],
			"binanceusdm": ["Binance Futures", "<:binance:929376117108916314>", "binancefutures"],
			"binance": ["Binance", "<:binance:929376117108916314>", "binance"]
		}
		options = [SelectOption(label=_map[key][0], emoji=PartialEmoji.from_str(_map[key][1]), value=_map[key][2]) for key in sorted(exchanges)]

		super().__init__(
			placeholder="Choose an exchange",
			min_values=1,
			max_values=1,
			options=options,
		)

	async def callback(self, interaction: Interaction):
		embed = Embed(title="Instruction is being executed.", description="`{}` is being sent to Ichibot and will be executed momentarily.".format(self.command), color=constants.colors["deep purple"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.edit_message(embed=embed, view=None)
		self._callback()