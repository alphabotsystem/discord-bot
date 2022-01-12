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


ICHIBOT_TESTING = [
	414498292655980583, 460731020245991424, 926518026457739304
]


class ChartCommand(BaseCommand):
	# chartGroup = SlashCommandGroup("chart", "Pull charts from TradingView, TradingLite, GoCharting, and more.")

	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		timeframes = task.pop("timeframes")
		for i in range(task.get("requestCount")):
			for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
			payload, chartText = await Processor.process_task("chart", request.authorId, task)

			if payload is None:
				errorMessage = "Requested chart for `{}` is not available.".format(currentTask.get("ticker").get("name")) if chartText is None else chartText
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				actions = None
				if currentTask.get("ticker", {}).get("isTradable") and request.guildId in ICHIBOT_TESTING:
					actions = IchibotView(request, task, userId=request.authorId)
				else:
					actions = ActionsView(userId=request.authorId)
				await ctx.interaction.edit_original_message(content=chartText, file=File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))), view=actions)

		await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(1)}}, merge=True)
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

			arguments = arguments.lower().split()
			outputMessage, task = await Processor.process_chart_arguments(request, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/charting).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /c {} autodelete:{}".format(ctx.author.id, " ".join(arguments), autodelete))


class ActionsView(View):
	def __init__(self, userId=None):
		super().__init__(timeout=None)
		self.userId = userId

	@button(emoji=PartialEmoji.from_str("<:remove_response:929342678976565298>"), style=ButtonStyle.gray)
	async def delete(self, button: Button, interaction: Interaction):
		if self.userId != interaction.user.id: return
		await interaction.message.delete()


class IchibotView(ActionsView):
	def __init__(self, request, task, userId=None):
		super().__init__(userId=userId)
		self.request = request
		self.task = task

	@button(label="Buy", style=ButtonStyle.green)
	async def ichibot_buy(self, button: Button, interaction: Interaction):
		origin = "{}_{}_ichibot".format(self.request.accountId, self.request.authorId)
		socket = Processor.get_direct_ichibot_socket(origin)

		command = "check value askprice"

		availableKeys = list(self.request.accountProperties.get("apiKeys", {}).keys())
		if len(availableKeys) == 0:
			embed = Embed(title="Before you can execute trades via Ichibot, you have to add exchange API keys.", description="You can add API keys for FTX, Binance and Binance Futures to you Alpha Account in your [Ichibot Preferences](https://www.alphabotsystem.com/account/ichibot).", color=constants.colors["gray"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)

		else:
			exchanges = ExchangesView(availableKeys, command)
			embed = Embed(title="Please confirm your buy instruction via Ichibot.", description="You'll be executing `{}` via Ichibot.".format(command), color=constants.colors["pink"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)
			await exchanges.wait()
			exchangeId = exchanges.children[0].values[0]

			# await socket.send_multipart([self.request.accountId.encode(), exchange.get("id").encode(), b"init"])

	@button(label="Sell", style=ButtonStyle.red)
	async def ichibot_sell(self, button: Button, interaction: Interaction):
		origin = "{}_{}_ichibot".format(self.request.accountId, self.request.authorId)
		socket = Processor.get_direct_ichibot_socket(origin)

		command = "check value bidprice"

		availableKeys = list(self.request.accountProperties.get("apiKeys", {}).keys())
		if len(availableKeys) == 0:
			embed = Embed(title="Before you can execute trades via Ichibot, you have to add exchange API keys.", description="You can add API keys for FTX, Binance and Binance Futures to you Alpha Account in your [Ichibot Preferences](https://www.alphabotsystem.com/account/ichibot).", color=constants.colors["gray"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)

		else:
			exchanges = ExchangesView(availableKeys, command)
			embed = Embed(title="Please confirm your sell instruction via Ichibot.", description="You'll be executing `{}` via Ichibot.".format(command), color=constants.colors["pink"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await interaction.response.send_message(embed=embed, view=exchanges, ephemeral=True)
			await exchanges.wait()
			exchangeId = exchanges.children[0].values[0]

			# await socket.send_multipart([self.request.accountId.encode(), exchange.get("id").encode(), b"init"])


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
			"ftx": ["FTX", "<:ftx:929376008107356160>"],
			"binancefutures": ["Binance Futures", "<:binance:929376117108916314>"],
			"binance": ["Binance", "<:binance:929376117108916314>"]
		}
		options = [SelectOption(label=_map[key][0], emoji=PartialEmoji.from_str(_map[key][1]), value=key) for key in sorted(exchanges)]

		super().__init__(
			placeholder="Choose an exchange",
			min_values=1,
			max_values=1,
			options=options,
		)

	async def callback(self, interaction: Interaction):
		embed = Embed(title="Executing your instruction.", description="`{}` is being sent to Ichibot and will be executed momentarily.".format(self.command), color=constants.colors["deep purple"])
		embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
		await interaction.response.edit_message(embed=embed, view=None)
		self._callback()