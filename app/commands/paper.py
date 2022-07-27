from os import environ
from time import time
from uuid import uuid4
from aiohttp import ClientSession
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction
from discord.commands import SlashCommandGroup, Option
from discord.ui import View, button, Button

from google.cloud.firestore import Increment, DELETE_FIELD

from helpers import constants
from assets import static_storage
from helpers.utils import Utils
from Processor import Processor
from TickerParser import TickerParser
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, Confirm


class PaperCommand(BaseCommand):
	paperGroup = SlashCommandGroup("paper", "Trade stocks and cryptocurrencies with paper money.")

	async def respond(
		self,
		ctx,
		request,
		task,
		payload,
		amount,
		level,
		orderType
	):
		currentTask = task.get(task.get("currentPlatform"))
		currentPlatform = payload.get("platform")
		currentTask = task.get(currentPlatform)
		ticker = currentTask.get("ticker")
		exchange = ticker.get("exchange")

		outputTitle, outputMessage, paper, pendingOrder = await self.process_trade(request.accountProperties["paperTrader"], amount, level, orderType, currentPlatform, currentTask, payload)

		if pendingOrder is None:
			embed = Embed(title=outputMessage, color=constants.colors["gray"])
			embed.set_author(name=outputTitle, icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
			return

		confirmation = Confirm(user=ctx.author)
		confirmationText = f"Do you want to place a paper {orderType} order of {pendingOrder.amountText} {ticker.get('base')} at {pendingOrder.priceText}?"
		embed = Embed(title=confirmationText, description=pendingOrder.conversionText, color=constants.colors["pink"])
		embed.set_author(name="Paper order confirmation", icon_url=pendingOrder.parameters.get("thumbnailUrl"))
		await ctx.interaction.edit_original_message(embed=embed, view=confirmation)
		await confirmation.wait()

		if confirmation.value is None or not confirmation.value:
			embed = Embed(title="Paper order has been canceled.", description=f"~~{confirmationText}~~", color=constants.colors["gray"])
			embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed, view=None)

		else:
			embed = Embed(description="Executing your paper order ...", color=constants.colors["deep purple"])
			await ctx.interaction.edit_original_message(embed=embed, view=None)

			for platform in task.get("platforms"): task[platform]["ticker"].pop("tree")
			paper = self.post_trade(paper, orderType, currentPlatform, currentTask, payload, pendingOrder)

			pendingOrder.parameters["request"] = task
			if paper["globalLastReset"] == 0: paper["globalLastReset"] = int(time())
			await self.database.document(f"accounts/{request.accountId}").set({"paperTrader": paper}, merge=True)
			if pendingOrder.parameters["isLimit"]:
				openOrders = await self.database.collection(f"details/openPaperOrders/{request.accountId}").get()
				if len(openOrders) >= 50:
					embed = Embed(title="You can only create up to 50 pending paper trades.", color=constants.colors["gray"])
					embed.set_author(name="Maximum number of open paper orders reached", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return
				await self.database.document(f"details/openPaperOrders/{request.accountId}/{str(uuid4())}").set(pendingOrder.parameters)
			else:
				await self.database.document(f"details/paperOrderHistory/{request.accountId}/{str(uuid4())}").set(pendingOrder.parameters)

			successMessage = f"Paper {orderType} order of {pendingOrder.amountText} {ticker.get('base')} at {pendingOrder.priceText} was successfully {'placed' if pendingOrder.parameters['isLimit'] else 'executed'}."
			embed = Embed(title=successMessage, color=constants.colors["deep purple"])
			embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
			await ctx.interaction.edit_original_message(embed=embed)

		await self.database.document("discord/statistics").set({request.snapshot: {"paper": Increment(1)}}, merge=True)

	async def paper_order_proxy(
		self,
		ctx,
		tickerId,
		amount,
		level,
		assetType,
		orderType
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("paper", assetType=assetType)
			preferredPlatforms = BaseCommand.sources["paper"].get(assetType)
			platforms = [e for e in defaultPlatforms if preferredPlatforms is None or e in preferredPlatforms]

			if request.is_registered():
				if level is not None:
					embed = Embed(title="Limit orders are temporarily unavailable.", color=constants.colors["gray"])
					await ctx.interaction.edit_original_message(embed=embed)
					return

				outputMessage, task = await Processor.process_quote_arguments(request, [], platforms, tickerId=tickerId.upper())
				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/paper-trading).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				currentTask = task.get(task.get("currentPlatform"))
				payload, quoteText = await Processor.process_task("candle", request.authorId, task)

				if payload is None or len(payload.get("candles", [])) == 0:
					errorMessage = f"Requested paper {orderType} order for `{currentTask.get('ticker').get('name')}` could not be executed." if quoteText is None else quoteText
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				await self.respond(ctx, request, task, payload, amount, level, orderType)

			else:
				embed = Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/signup). If you already signed up, [sign in](https://www.alphabotsystem.com/login), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper {orderType} {tickerId} {amount} {level} {assetType}")
			await self.unknown_error(ctx)

	@paperGroup.command(name="buy", description="Execute a paper buy trade.")
	async def paper_buy(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		amount: Option(float, "Trade amount in base currency.", name="amount"),
		level: Option(float, "Limit order price for the trade.", name="price", required=False, default=None),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default="")
	):
		await self.paper_order_proxy(ctx, tickerId, amount, level, assetType, "buy")

	@paperGroup.command(name="sell", description="Execute a paper sell trade.")
	async def paper_sell(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		amount: Option(float, "Trade amount in base currency.", name="amount"),
		level: Option(float, "Limit order price for the trade.", name="price", required=False, default=None),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default="")
	):
		await self.paper_order_proxy(ctx, tickerId, amount, level, assetType, "sell")

	@paperGroup.command(name="balance", description="Fetch paper trading balance.")
	async def paper_balance(
		self,
		ctx,
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if request.is_registered():
				paperOrders = await self.database.collection(f"details/openPaperOrders/{request.accountId}").get()
				paperBalances = request.accountProperties["paperTrader"].get("balance", {})

				embed = Embed(title="Paper balance:", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

				holdingAssets = set()
				totalValue = 0

				for platform, balances in paperBalances.items():
					if platform == "USD": continue
					for asset, holding in balances.items():
						if holding == 0: continue
						ticker, error = await TickerParser.match_ticker(asset, None, platform, "traditional")

						balanceText = ""
						valueText = "No conversion"

						balanceText = "{:,.4f} {}".format(holding, asset)
						payload, quoteText = await Processor.process_conversion(request, asset, "USD", holding, [platform])
						convertedValue = payload["raw"]["quotePrice"][0] if payload is not None else 0
						valueText = "≈ {:,.4f} {}".format(convertedValue, "USD") if payload is not None else "Unavailable"
						totalValue += convertedValue

						embed.add_field(name=balanceText, value=valueText, inline=True)
						holdingAssets.add(platform + "_" +  asset)

				usdBalance = paperBalances.get("USD", 10000)
				balanceText = "{:,.4f} USD".format(usdBalance)
				totalValue += usdBalance
				embed.add_field(name=balanceText, value="Stable in fiat value", inline=True)
				if usdBalance != 0:
					holdingAssets.add("USD")

				lastResetTimestamp = request.accountProperties["paperTrader"]["globalLastReset"]
				resetCount = request.accountProperties["paperTrader"]["globalResetCount"]

				openOrdersValue = 0
				for element in paperOrders:
					order = element.to_dict()
					if order["orderType"] in ["buy", "sell"]:
						currentPlatform = order["request"].get("currentPlatform")
						task = order["request"].get(currentPlatform)
						ticker = task.get("ticker").get("quote") if order["orderType"] == "buy" else task.get("ticker").get("base")
						payload, quoteText = await Processor.process_conversion(request, ticker, "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1), [currentPlatform])
						openOrdersValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0
						holdingAssets.add(currentPlatform + "_" + task.get("ticker").get("base"))

				if openOrdersValue > 0:
					totalValue += openOrdersValue
					valueText = "{:,.4f} USD".format(openOrdersValue)
					embed.add_field(name="Locked up in open orders:", value=valueText, inline=True)

				embed.description = "Holding {} asset{} with estimated total value of {:,.2f} USD and {:+,.2f} % ROI.{}".format(len(holdingAssets), "" if len(holdingAssets) == 1 else "s", totalValue, (totalValue / 10000 - 1) * 100, f" Trading since {Utils.timestamp_to_date(lastResetTimestamp)} with {resetCount} balance reset{'' if resetCount == 1 else 's'}." if resetCount != 0 else "")
				await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/signup). If you already signed up, [sign in](https://www.alphabotsystem.com/login), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper balance")
			await self.unknown_error(ctx)

	@paperGroup.command(name="orders", description="Fetch open paper orders.")
	async def paper_orders(
		self,
		ctx
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if request.is_registered():
				paperOrders = await self.database.collection(f"details/openPaperOrders/{request.accountId}").get()
				totalOrderCount = len(paperOrders)
				if totalOrderCount == 0:
					embed = Embed(title="No open paper orders.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)

				else:
					embed = Embed(title=f"You've set {totalOrderCount} paper order{'' if totalOrderCount == 1 else 's'}.", color=constants.colors["light blue"])
					await ctx.interaction.edit_original_message(embed=embed)

					for i, element in enumerate(paperOrders):
						order = element.to_dict()
						currentPlatform = order["request"].get("currentPlatform")
						task = order["request"].get(currentPlatform)
						ticker = task.get("ticker")

						quoteText = ticker.get("quote")
						side = order["orderType"].replace("-", " ")

						embed = Embed(title=f"Paper {side} {order['amountText']} {ticker.get('base')} at {order['priceText']} {quoteText}", color=constants.colors["deep purple"])
						await ctx.followup.send(embed=embed, view=DeleteView(database=self.database, pathId=request.accountId, orderId=element.id, userId=request.authorId), ephemeral=True)

			else:
				embed = Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/signup). If you already signed up, [sign in](https://www.alphabotsystem.com/login), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper orders")
			await self.unknown_error(ctx)

	@paperGroup.command(name="history", description="Fetch open paper trading history.")
	async def paper_history(
		self,
		ctx
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if request.is_registered():
				paperHistory = await self.database.collection(f"details/paperOrderHistory/{request.accountId}").limit(50).get()
				if len(paperHistory) == 0:
					embed = Embed(title="No paper trading history.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					embed = Embed(title="Paper trading history:", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

					for element in paperHistory:
						order = element.to_dict()
						currentPlatform = order["request"].get("currentPlatform")
						task = order["request"].get(currentPlatform)
						ticker = task.get("ticker")

						side = ""
						if order["orderType"] == "buy": side = "Bought"
						elif order["orderType"] == "sell": side = "Sold"
						elif order["orderType"].startswith("stop"): side = "Stop sold"
						embed.add_field(name=f"{side} {order['amountText']} {ticker.get('base')} at {order['priceText']} {ticker.get('quote')}", value=f"{Utils.timestamp_to_date(order['timestamp'] / 1000)}", inline=False)

					await ctx.interaction.edit_original_message(embed=embed)
			
			else:
				embed = Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/signup). If you already signed up, [sign in](https://www.alphabotsystem.com/login), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper history")
			await self.unknown_error(ctx)

	@paperGroup.command(name="leaderboard", description="Check Alpha's Paper Trader leaderboard.")
	async def paper_leaderboard(
		self,
		ctx
	):
		return
		try:
			request = await self.create_request(ctx)
			if request is None: return

			paperTraders = await self.database.collection("accounts").where("paperTrader.balance", "!=", "").get()
			topBalances = []

			for account in paperTraders:
				properties = account.to_dict()
				balance = properties["paperTrader"]["balance"]
				totalValue = balance.get("USD", 10000)

				for platform, balances in balance.items():
					if platform == "USD": continue
					for asset, holding in balances.items():
						if holding == 0: continue
						payload, quoteText = await Processor.process_conversion(request, asset, "USD", holding, [platform])
						totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

				paperOrders = await self.database.collection(f"details/openPaperOrders/{account.id}").get()
				for element in paperOrders:
					order = element.to_dict()
					if order["orderType"] in ["buy", "sell"]:
						currentPlatform = order["request"].get("currentPlatform")
						task = order["request"].get(currentPlatform)
						ticker = task.get("ticker").get("quote") if order["orderType"] == "buy" else task.get("ticker").get("base")
						payload, quoteText = await Processor.process_conversion(request, ticker, "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1), [currentPlatform])
						totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

				topBalances.append((totalValue, properties["paperTrader"]["globalLastReset"], properties["oauth"]["discord"]["userId"]))

			topBalances.sort(reverse=True)

			embed = Embed(title="Paper trading leaderboard:", color=constants.colors["deep purple"])
			embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

			for index, (balance, lastReset, authorId) in enumerate(topBalances[:10]):
				embed.add_field(name=f"#{index + 1}: <@!{authorId}> with {balance} USD", value=f"Since {Utils.timestamp_to_date(lastReset)}", inline=False)

			await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper leaderboard")
			await self.unknown_error(ctx)

	@paperGroup.command(name="reset", description="Reset paper trading balance.")
	async def paper_reset(
		self,
		ctx,
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if not request.is_registered():
				embed = Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/signup). If you already signed up, [sign in](https://www.alphabotsystem.com/login), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

			elif request.accountProperties["paperTrader"]["globalLastReset"] == 0 and request.accountProperties["paperTrader"]["globalResetCount"] == 0:
				embed = Embed(title="You have to start trading before you can reset your paper balance.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			elif request.accountProperties["paperTrader"]["globalLastReset"] + 604800 < time():
				confirmation = Confirm(user=ctx.author)
				embed = Embed(title="Do you really want to reset your paper balance? This cannot be undone.", description="Paper balance can only be reset once every seven days. Your last public reset date will be publicly visible.", color=constants.colors["pink"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed, view=confirmation)
				await confirmation.wait()

				if confirmation.value is None or not confirmation.value:
					embed = Embed(title="Paper balance reset canceled.", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed, view=None)

				else:
					embed = Embed(description="Deleting your paper trading history ...", color=constants.colors["deep purple"])
					await ctx.interaction.edit_original_message(embed=embed, view=None)

					async def delete_collection(collectionRef, batchSize):
						docs = await collectionRef.limit(batchSize).get()
						deleted = 0

						for doc in docs:
							await doc.reference.delete()
							deleted += 1

						if deleted >= batchSize:
							return await delete_collection(collectionRef, batchSize)

					await delete_collection(self.database.collection(f"details/openPaperOrders/{request.accountId}"), 300)
					await delete_collection(self.database.collection(f"details/paperOrderHistory/{request.accountId}"), 300)

					paper = {
						"globalResetCount": request.accountProperties["paperTrader"]["globalResetCount"] + 1,
						"globalLastReset": int(time()),
						"balance": DELETE_FIELD
					}
					await self.database.document(f"accounts/{request.accountId}").set({"paperTrader": paper}, merge=True)

					embed = Embed(title="Paper balance has been reset successfully.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = Embed(title="Paper balance can only be reset once every seven days.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper reset")
			await self.unknown_error(ctx)

	async def process_trade(self, paper, execAmount, execPrice, orderType, currentPlatform, request, payload):
		outputTitle = None
		outputMessage = None

		ticker = request.get("ticker")

		if "balance" not in paper:
			paper["balance"] = {"USD": 10000, "CCXT": {}, "IEXC": {}}
		if ticker.get("base") in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			baseBalance = paper["balance"].get("USD")
		else:
			baseBalance = paper["balance"][currentPlatform].get(ticker.get("base"), 0)
		if ticker.get("quote") in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			quoteBalance = paper["balance"].get("USD")
		else:
			quoteBalance = paper["balance"][currentPlatform].get(ticker.get("quote"), 0)

		if execPrice is None: execPrice = payload["candles"][-1][4]
		if orderType.endswith("sell"): execAmount = min((baseBalance), execAmount)
		else: execAmount = min((abs(quoteBalance) / execPrice), execAmount)

		if currentPlatform == "CCXT":
			execPriceText = await TickerParser.get_formatted_price_ccxt(ticker.get("exchange").get("id"), ticker.get("symbol"), execPrice)
			execPrice = float(execPriceText.replace(",", ""))
			execAmountText = await TickerParser.get_formatted_amount_ccxt(ticker.get("exchange").get("id"), ticker.get("symbol"), execAmount)
			thumbnailUrl = ticker.get("image")
		else:
			execPriceText = "{:,.6f}".format(execPrice)
			execAmountText = "{:,.6f}".format(execAmount)
			async with ClientSession() as session:
				async with session.get(f"https://cloud.iexapis.com/stable/stock/{ticker.get('symbol')}/logo?token={environ['IEXC_KEY']}") as resp:
					response = await resp.json()
					thumbnailUrl = response["url"]

		baseValue = execAmount
		quoteValue = execAmount * execPrice

		if execAmount == 0:
			outputTitle = "Insuficient paper order size"
			outputMessage = f"Cannot execute an order of 0.0 {ticker.get('base')}."
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("sell") and baseValue > baseBalance) or (orderType.endswith("buy") and quoteValue * 0.9999999999 > quoteBalance):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Order size of {} {} exeeds your paper wallet balance of {:,.8f} {}.".format(execAmountText, ticker.get("base"), quoteBalance if orderType.endswith("buy") else baseBalance, ticker.get("quote") if orderType.endswith("buy") else ticker.get("base"))
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("buy") and quoteBalance == 0) or (orderType.endswith("sell") and baseBalance == 0):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = f"Your {ticker.get('quote') if orderType.endswith('buy') else ticker.get('base')} balance is empty."
			return outputTitle, outputMessage, paper, None

		newOrder = {
			"orderType": orderType,
			"amount": execAmount,
			"amountText": execAmountText,
			"price": execPrice,
			"priceText": execPriceText,
			"timestamp": int(time() * 1000),
			"isLimit": execPrice != payload["candles"][-1][4],
			"thumbnailUrl": thumbnailUrl
		}
		if newOrder["isLimit"]:
			newOrder["placement"] = "above" if newOrder["price"] > payload["candles"][-1][4] else "below"

		priceText = f"{execPriceText} {ticker.get('quote')}"
		conversionText = "{} {} ≈ {:,.6f} {}".format(execAmountText, ticker.get("base"), quoteValue, ticker.get("quote"))

		return None, None, paper, Order(newOrder, priceText=priceText, conversionText=conversionText, amountText=execAmountText)

	def post_trade(self, paper, orderType, currentPlatform, request, payload, pendingOrder):
		ticker = request.get("ticker")
		execPrice = pendingOrder.parameters["price"]
		execAmount = pendingOrder.parameters["amount"]
		isLimitOrder = pendingOrder.parameters["isLimit"]

		base = ticker.get("base")
		quote = ticker.get("quote")
		if base in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			baseBalance = paper["balance"]
			base = "USD"
		else:
			baseBalance = paper["balance"][currentPlatform]
		if quote in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			quoteBalance = paper["balance"]
			quote = "USD"
		else:
			quoteBalance = paper["balance"][currentPlatform]

		if orderType == "buy":
			quoteBalance[quote] = quoteBalance[quote] - execPrice * execAmount
			if not isLimitOrder:
				baseBalance[base] = baseBalance.get(base, 0) + execAmount
		elif orderType == "sell":
			baseBalance[base] = baseBalance[base] - execAmount
			if not isLimitOrder:
				quoteBalance[quote] = quoteBalance.get(quote, 0) + execAmount * execPrice

		return paper


class Order(object):
	def __init__(self, parameters, priceText, amountText, conversionText):
		self.parameters = parameters
		self.priceText = priceText
		self.amountText = amountText
		self.conversionText = conversionText


class DeleteView(View):
	def __init__(self, database, pathId, orderId, userId):
		super().__init__(timeout=None)
		self.database = database
		self.pathId = pathId
		self.orderId = orderId
		self.userId = userId

	@button(label="Cancel", style=ButtonStyle.danger)
	async def delete(self, button: Button, interaction: Interaction):
		if self.userId != interaction.user.id: return
		properties = await DatabaseConnector(mode="account").get(self.pathId)

		order = await self.database.document(f"details/openPaperOrders/{self.pathId}/{self.orderId}").get()
		if order is None: return
		order = order.to_dict()

		currentPlatform = order["request"].get("currentPlatform")
		request = order["request"].get(currentPlatform)
		ticker = request.get("ticker")

		base = ticker.get("base")
		quote = ticker.get("quote")
		if base in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			baseBalance = properties["paperTrader"]["balance"]
			base = "USD"
		else:
			baseBalance = properties["paperTrader"]["balance"][currentPlatform]
		if quote in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			quoteBalance = properties["paperTrader"]["balance"]
			quote = "USD"
		else:
			quoteBalance = properties["paperTrader"]["balance"][currentPlatform]

		if order["orderType"] == "buy":
			quoteBalance[quote] += order["amount"] * order["price"]
		elif order["orderType"] == "sell":
			baseBalance[base] += order["amount"]

		await self.database.document(f"details/openPaperOrders/{self.pathId}/{self.orderId}").delete()
		await self.database.document(f"accounts/{self.pathId}").set({"paperTrader": properties["paperTrader"]}, merge=True)

		embed = Embed(title="Paper order has been canceled.", color=constants.colors["gray"])
		await interaction.response.edit_message(embed=embed, view=None)