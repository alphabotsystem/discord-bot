from os import environ
from time import time
from random import randint
from asyncio import CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_chart_arguments, process_task, get_direct_ichibot_socket
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, ActionsView
from commands.ichibot import Ichibot


ICHIBOT_TESTING = [
	414498292655980583, 926518026457739304, # 460731020245991424
]


class ChartCommand(BaseCommand):
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
				payload, responseMessage = await process_task(task, "chart")

				if responseMessage == "requires pro":
					embed = Embed(title=f"The requested chart for `{currentTask.get('ticker').get('name')}` is only available on TradingView Premium.", description="All TradingView Premium charts are bundled with the [Advanced Charting add-on](https://www.alphabotsystem.com/pro/advanced-charting).", color=constants.colors["gray"])
					embed.set_author(name="TradingView Premium", icon_url=static_storage.icon_bw)
					embeds.append(embed)
				elif payload is None:
					errorMessage = f"Requested chart for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
					embeds.append(embed)
				else:
					task["currentPlatform"] = payload.get("platform")
					currentTask = task.get(task.get("currentPlatform"))
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		actions = None
		if len(files) != 0:
			if len(tasks) == 1 and currentTask.get("ticker", {}).get("tradable") is not None and request.guildId in ICHIBOT_TESTING:
				actions = IchibotView(self.bot.loop, currentTask, user=ctx.author)
			else:
				actions = ActionsView(user=ctx.author)

		try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
		except NotFound: pass

		await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(len(tasks))}}, merge=True)
		await self.log_request("charts", request, tasks)
		await self.cleanup(ctx, request, removeView=True)

	@slash_command(name="c", description="Pull charts from TradingView, TradingLite and more. Command for power users.")
	async def c(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("c")

			parts = arguments.split(",")
			tasks = []

			if len(parts) > 5:
				embed = Embed(title="Only up to five requests are allowed per command.", color=constants.colors["gray"])
				embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			for part in parts:
				partArguments = part.lower().split()
				if len(partArguments) == 0: continue

				responseMessage, task = await process_chart_arguments(partArguments[1:], defaultPlatforms, tickerId=partArguments[0].upper())

				if responseMessage is not None:
					description = "[Advanced Charting add-on](https://www.alphabotsystem.com/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else "Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/charting)."
					embed = Embed(title=responseMessage, description=description, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif autodelete is not None and (autodelete < 1 or autodelete > 10):
					embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				tasks.append(task)

			await self.respond(ctx, request, tasks)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /c {arguments} autodelete:{autodelete}")
			await self.unknown_error(ctx)

class IchibotView(ActionsView):
	def __init__(self, eventLoop, task, user=None):
		super().__init__(user=user)
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
			embed = Embed(title="Before you can execute trades via Ichibot, you have to add exchange API keys.", description="You can add API keys for Binance and Binance Futures to you Alpha Account in your [Ichibot Preferences](https://www.alphabotsystem.com/account/trading).", color=constants.colors["gray"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, ephemeral=True)
			return accountId, None, None

		origin = f"{accountId}_{interaction.user.id}_ichibot"
		if origin in Ichibot.sockets:
			socket = Ichibot.sockets.get(origin)
		else:
			socket = get_direct_ichibot_socket(origin)
			Ichibot.sockets[origin] = socket
			self.eventLoop.create_task(Ichibot.process_ichibot_messages(origin, interaction.user))

		tradableMarket = self.task.get("ticker").get("tradable")

		availableKeys = [key for key in accountProperties.get("apiKeys", {}).keys() if key in matches]

		if len(availableKeys) == 0:
			_e = {"binance": "Binance", "binanceusdm": "Binance Futures"}
			orText = ", ".join([_e[e] for e in matches[:-1]]) + " or " + matches[-1]
			andText = ", ".join([_e[e] for e in matches[:-1]]) + " and " + matches[-1]
			embed = Embed(title=f"Add API keys for {orText}.", description=f"`{self.task.get('ticker').get('name')}` is only available on {andText} for which you haven't added API keys yet.", color=constants.colors["gray"])
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
		embed = Embed(title="Please confirm your buy instruction via Ichibot.", description=f"You'll be executing `{quickAction}` via Ichibot.", color=constants.colors["pink"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)
		await exchanges.wait()
		exchangeId = exchanges.children[0].values[0]

		command = f"instrument {self.task.get('ticker').get('tradable').get(exchangeId)}, {quickAction}"

		await socket.send_multipart([accountId.encode(), exchangeId.encode(), b"init"])
		await sleep(10)
		await socket.send_multipart([accountId.encode(), b"", command.encode()])

	@button(label="Sell", style=ButtonStyle.red)
	async def ichibot_sell(self, button: Button, interaction: Interaction):
		accountId, availableKeys, socket = await self.prepare(interaction)
		if socket is None: return

		quickAction = "check value bidprice"

		exchanges = ExchangesView(availableKeys, quickAction)
		embed = Embed(title="Please confirm your sell instruction via Ichibot.", description=f"You'll be executing `{quickAction}` via Ichibot.", color=constants.colors["pink"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)
		await exchanges.wait()
		exchangeId = exchanges.children[0].values[0]

		command = f"instrument {self.task.get('ticker').get('tradable').get(exchangeId)}, {quickAction}"

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
		embed = Embed(title="Instruction is being executed.", description=f"`{self.command}` is being sent to Ichibot and will be executed momentarily.", color=constants.colors["deep purple"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.edit_message(embed=embed, view=None)
		self._callback()