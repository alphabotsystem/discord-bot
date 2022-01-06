from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction
from discord.commands import SlashCommandGroup, Option
from discord.ui import View, button, Button

from google.cloud.firestore import DELETE_FIELD

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class PaperCommand(BaseCommand):
	paperGroup = SlashCommandGroup("paper", "Trade stocks and cryptocurrencies with paper money.")

	async def respond(
		self,
		ctx,
		request,
		task,
		orderType
	):
		if request.is_registered():
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[2:], tickerId=arguments[1].upper(), isPaperTrade=True, excluded=["CoinGecko", "Serum", "LLD"])
			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/paper-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			currentTask = task.get(task.get("currentPlatform"))

			payload, quoteText = await Processor.process_task("candle", request.authorId, task)

			if payload is None or len(payload.get("candles", [])) == 0:
				errorMessage = "Requested paper {} order for `{}` could not be executed.".format(orderType.replace("-", " "), currentTask.get("ticker").get("name")) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				currentPlatform = payload.get("platform")
				currentTask = task.get(currentPlatform)
				ticker = currentTask.get("ticker")
				exchange = ticker.get("exchange")

				outputTitle, outputMessage, paper, pendingOrder = await paperTrader.process_trade(request.accountProperties["paperTrader"], orderType, currentPlatform, currentTask, payload)

				if pendingOrder is None:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name=outputTitle, icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				confirmationText = "Do you want to place a paper {} order of {} {} at {}?".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.get("base"), pendingOrder.priceText)
				embed = discord.Embed(title=confirmationText, description=pendingOrder.conversionText, color=constants.colors["pink"])
				embed.set_author(name="Paper order confirmation", icon_url=pendingOrder.parameters.get("thumbnailUrl"))
				orderConfirmationMessage = await ctx.interaction.edit_original_message(embed=embed)
				lockedUsers.add(request.authorId)

				def confirm_order(m):
					if m.author.id == request.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response in ["y", "yes", "sure", "confirm", "execute"]: return True
						elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception

				try:
					await bot.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					lockedUsers.discard(request.authorId)
					embed = discord.Embed(title="Paper order has been canceled.", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
					try: await orderConfirmationMessage.edit(embed=embed)
					except: pass
				else:
					lockedUsers.discard(request.authorId)
					for platform in task.get("platforms"): task[platform]["ticker"].pop("tree")
					paper = paperTrader.post_trade(paper, orderType, currentPlatform, currentTask, payload, pendingOrder)

					pendingOrder.parameters["request"] = task
					if paper["globalLastReset"] == 0: paper["globalLastReset"] = int(time())
					await database.document("accounts/{}".format(request.accountId)).set({"paperTrader": paper}, merge=True)
					if pendingOrder.parameters["parameters"][1]:
						openOrders = await database.collection("details/openPaperOrders/{}".format(request.accountId)).get()
						if len(openOrders) >= 50:
							embed = discord.Embed(title="You can only create up to 50 pending paper trades.", color=constants.colors["gray"])
							embed.set_author(name="Maximum number of open paper orders reached", icon_url=static_storage.icon_bw)
							await ctx.interaction.edit_original_message(embed=embed)
							return
						await database.document("details/openPaperOrders/{}/{}".format(request.accountId, str(uuid4()))).set(pendingOrder.parameters)
					else:
						await database.document("details/paperOrderHistory/{}/{}".format(request.accountId, str(uuid4()))).set(pendingOrder.parameters)

					successMessage = "Paper {} order of {} {} at {} was successfully {}.".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.get("base"), pendingOrder.priceText, "executed" if pendingOrder.parameters["parameters"][0] else "placed")
					embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)

		else:
			embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
			embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
			await ctx.interaction.edit_original_message(embed=embed)

	@paperGroup.command(name="buy", description="Fetch paper trading balance.")
	async def paper_balance(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		level: Option(float, "Trigger price for the alert.", name="price"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the volume from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default=""),
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			arguments = paperTrader.argument_cleanup(requestSlice).split(" ")

			self.respond(ctx, request, task, "buy", arguments)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper buy")

	@paperGroup.command(name="balance", description="Fetch paper trading balance.")
	async def paper_balance(
		self,
		ctx,
	):
		try:
			if messageRequest.is_registered():
				paperOrders = await database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get()
				paperBalances = messageRequest.accountProperties["paperTrader"].get("balance", {})

				embed = discord.Embed(title="Paper balance:", color=constants.colors["deep purple"])
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
						payload, quoteText = await Processor.process_conversion(messageRequest, asset, "USD", holding)
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

				lastResetTimestamp = messageRequest.accountProperties["paperTrader"]["globalLastReset"]
				resetCount = messageRequest.accountProperties["paperTrader"]["globalResetCount"]

				openOrdersValue = 0
				for element in paperOrders:
					order = element.to_dict()
					if order["orderType"] in ["buy", "sell"]:
						currentPlatform = order["request"].get("currentPlatform")
						paperRequest = order["request"].get(currentPlatform)
						ticker = paperRequest.get("ticker")
						payload, quoteText = await Processor.process_conversion(messageRequest, ticker.get("quote") if order["orderType"] == "buy" else ticker.get("base"), "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
						openOrdersValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0
						holdingAssets.add(currentPlatform + "_" + ticker.get("base"))

				if openOrdersValue > 0:
					totalValue += openOrdersValue
					valueText = "{:,.4f} USD".format(openOrdersValue)
					embed.add_field(name="Locked up in open orders:", value=valueText, inline=True)

				embed.description = "Holding {} asset{} with estimated total value of {:,.2f} USD and {:+,.2f} % ROI.{}".format(len(holdingAssets), "" if len(holdingAssets) == 1 else "s", totalValue, (totalValue / 10000 - 1) * 100, " Trading since {} with {} balance reset{}.".format(Utils.timestamp_to_date(lastResetTimestamp), resetCount, "" if resetCount == 1 else "s") if resetCount != 0 else "")
				await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper balance")

	@paperGroup.command(name="orders", description="Fetch open paper orders.")
	async def paper_orders(
		ctx
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			if request.is_registered():
				paperOrders = await self.database.collection("details/openPaperOrders/{}".format(request.accountId)).get()
				if len(paperOrders) == 0:
					embed = discord.Embed(title="No open paper orders.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					for i, element in enumerate(paperOrders):
						order = element.to_dict()
						currentPlatform = order["request"].get("currentPlatform")
						paperRequest = order["request"].get(currentPlatform)
						ticker = paperRequest.get("ticker")

						quoteText = ticker.get("quote")
						side = order["orderType"].replace("-", " ")

						embed = discord.Embed(title="Paper {} {} {} at {} {}".format(side, order["amountText"], ticker.get("base"), order["priceText"], quoteText), color=constants.colors["deep purple"])
						await ctx.interaction.edit_original_message(embed=embed, view=DeleteView(database=self.database, authorId=request.authorId, pathId=request.accountId, orderId=element.id))

			else:
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper orders")

	@paperGroup.command(name="history", description="Fetch open paper trading history.")
	async def paper_history(
		ctx
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			if request.is_registered():
				paperHistory = await self.database.collection("details/paperOrderHistory/{}".format(request.accountId)).limit(50).get()
				if len(paperHistory) == 0:
					embed = discord.Embed(title="No paper trading history.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					embed = discord.Embed(title="Paper trading history:", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

					for element in paperHistory:
						order = element.to_dict()
						currentPlatform = order["request"].get("currentPlatform")
						paperRequest = order["request"].get(currentPlatform)
						ticker = paperRequest.get("ticker")

						side = ""
						if order["orderType"] == "buy": side = "Bought"
						elif order["orderType"] == "sell": side = "Sold"
						elif order["orderType"].startswith("stop"): side = "Stop sold"
						embed.add_field(name="{} {} {} at {} {}".format(side, order["amountText"], ticker.get("base"), order["priceText"], ticker.get("quote")), value="{} ● id: {}".format(Utils.timestamp_to_date(order["timestamp"] / 1000), element.id), inline=False)

					await ctx.interaction.edit_original_message(embed=embed)
			
			else:
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper history")

	@paperGroup.command(name="leaderboard", description="Check Alpha's Paper Trader leaderboard.")
	async def paper_leaderboard(
		ctx
	):
		return
		try:
			request = await self.create_request(ctx, autodelete=-1)
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
						payload, quoteText = await Processor.process_conversion(request, asset, "USD", holding)
						totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

				paperOrders = await self.database.collection("details/openPaperOrders/{}".format(account.id)).get()
				for element in paperOrders:
					order = element.to_dict()
					if order["orderType"] in ["buy", "sell"]:
						currentPlatform = order["request"].get("currentPlatform")
						paperRequest = order["request"].get(currentPlatform)
						ticker = paperRequest.get("ticker")
						payload, quoteText = await Processor.process_conversion(request, ticker.get("quote") if order["orderType"] == "buy" else ticker.get("base"), "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
						totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

				topBalances.append((totalValue, properties["paperTrader"]["globalLastReset"], properties["oauth"]["discord"]["userId"]))

			topBalances.sort(reverse=True)

			embed = discord.Embed(title="Paper trading leaderboard:", color=constants.colors["deep purple"])
			embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

			for index, (balance, lastReset, authorId) in enumerate(topBalances[:10]):
				embed.add_field(name="#{}: <@!{}> with {} USD".format(index + 1, authorId, balance), value="Since {}".format(Utils.timestamp_to_date(lastReset)), inline=False)

			await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper leaderboard")

	@paperGroup.command(name="reset", description="Reset paper trading balance.")
	async def paper_reset(
		self,
		ctx,
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			if not request.is_registered():
				embed = Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

			elif request.accountProperties["paperTrader"]["globalLastReset"] == 0 and request.accountProperties["paperTrader"]["globalResetCount"] == 0:
				embed = Embed(title="You have to start trading before you can reset your paper balance.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			elif request.accountProperties["paperTrader"]["globalLastReset"] + 604800 < time():
				embed = Embed(title="Do you really want to reset your paper balance? This cannot be undone.", description="Paper balance can only be reset once every seven days. Your last public reset date will be publicly visible.", color=constants.colors["pink"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				resetBalanceMessage = await ctx.interaction.edit_original_message(embed=embed)
				lockedUsers.add(request.authorId)

				def confirm_order(m):
					if m.author.id == request.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response in ["y", "yes", "sure", "confirm", "execute"]: return True
						elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception

				try:
					await bot.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					lockedUsers.discard(request.authorId)
					embed = Embed(title="Paper balance reset canceled.", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
					await resetBalanceMessage.edit(embed=embed)
				else:
					lockedUsers.discard(request.authorId)

					async def delete_collection(collectionRef, batchSize):
						docs = await collectionRef.limit(batchSize).get()
						deleted = 0

						for doc in docs:
							await doc.reference.delete()
							deleted += 1

						if deleted >= batchSize:
							return await delete_collection(collectionRef, batchSize)

					await delete_collection(self.database.collection("details/openPaperOrders/{}".format(request.accountId)), 300)
					await delete_collection(self.database.collection("details/paperOrderHistory/{}".format(request.accountId)), 300)

					paper = {
						"globalResetCount": request.accountProperties["paperTrader"]["globalResetCount"] + 1,
						"globalLastReset": int(time()),
						"balance": DELETE_FIELD
					}
					await self.database.document("accounts/{}".format(request.accountId)).set({"paperTrader": paper}, merge=True)

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

	async def process_trade(self, paper, orderType, currentPlatform, request, payload):
		outputTitle = None
		outputMessage = None

		ticker = request.get("ticker")
		if ticker.get("isReversed"):
			outputTitle = "Cannot trade on an inverse ticker."
			outputMessage = "Try flipping the base and the quote currency, then try again with an inverse order."
			return outputTitle, outputMessage, paper, None

		isLimitOrder = {"id": "isLimitOrder", "value": "limitOrder"} in request.get("preferences")
		isAmountPercent = {"id": "isAmountPercent", "value": "amountPercent"} in request.get("preferences")
		isPricePercent = {"id": "isPricePercent", "value": "pricePercent"} in request.get("preferences")
		if not isLimitOrder:
			execPrice = payload["candles"][-1][4]
		elif isLimitOrder and len(request.get("numericalParameters")) != 2:
			outputTitle = "Execution price was not provided."
			outputMessage = "A limit order execution price must be provided."
			return outputTitle, outputMessage, paper, None
		else:
			execPrice = request.get("numericalParameters")[1]
		execAmount = request.get("numericalParameters")[0]

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

		if orderType.endswith("buy"):
			if isPricePercent: execPrice = payload["candles"][-1][4] * (1 - execPrice / 100)
			execAmount = (abs(quoteBalance) / execPrice * (execAmount / 100)) if isAmountPercent else execAmount
		elif orderType.endswith("sell"):
			if isPricePercent: execPrice = payload["candles"][-1][4] * (1 + execPrice / 100)
			execAmount = (baseBalance * (execAmount / 100)) if isAmountPercent else execAmount

		if currentPlatform == "CCXT":
			execPriceText = await TickerParser.get_formatted_price_ccxt(ticker.get("exchange").get("id"), ticker.get("symbol"), execPrice)
			execPrice = float(execPriceText.replace(",", ""))
			execAmountText = await TickerParser.get_formatted_amount_ccxt(ticker.get("exchange").get("id"), ticker.get("symbol"), execAmount)
			thumbnailUrl = ticker.get("image")
		else:
			execPriceText = "{:,.6f}".format(execPrice)
			execAmountText = "{:,.6f}".format(execAmount)
			async with aiohttp.ClientSession() as session:
				async with session.get("https://cloud.iexapis.com/stable/stock/{}/logo?token={}".format(ticker.get("symbol"), environ["IEXC_KEY"])) as resp:
					response = await resp.json()
					thumbnailUrl = response["url"]

		baseValue = execAmount
		quoteValue = execAmount * execPrice

		if execAmount == 0:
			outputTitle = "Insuficient paper order size"
			outputMessage = "Cannot execute an order of 0.0 {}.".format(ticker.get("base"))
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("sell") and baseValue > baseBalance) or (orderType.endswith("buy") and quoteValue * 0.9999999999 > quoteBalance):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Order size of {} {} exeeds your paper wallet balance of {:,.8f} {}.".format(execAmountText, ticker.get("base"), quoteBalance if orderType.endswith("buy") else baseBalance, ticker.get("quote") if orderType.endswith("buy") else ticker.get("base"))
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("buy") and quoteBalance == 0) or (orderType.endswith("sell") and baseBalance == 0):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Your {} balance is empty.".format(ticker.get("quote") if orderType.endswith("buy") else ticker.get("base"))
			return outputTitle, outputMessage, paper, None

		newOrder = {
			"orderType": orderType,
			"amount": execAmount,
			"amountText": execAmountText,
			"price": request.get("numericalParameters")[0] if isPricePercent else execPrice,
			"priceText": execPriceText,
			"timestamp": int(time() * 1000),
			"status": "placed",
			"parameters": [isPricePercent, isLimitOrder],
			"thumbnailUrl": thumbnailUrl
		}
		newOrder["placement"] = "above" if newOrder["price"] > payload["candles"][-1][4] else "below"

		priceText = "{:,.2f} %".format(request.get("numericalParameters")[0]) if isPricePercent else "{} {}".format(execPriceText, ticker.get("quote"))
		conversionText = None if isPricePercent else "{} {} ≈ {:,.6f} {}".format(execAmountText, ticker.get("base"), quoteValue, ticker.get("quote"))

		return None, None, paper, Order(newOrder, priceText=priceText, conversionText=conversionText, amountText=execAmountText)

	def post_trade(self, paper, orderType, currentPlatform, request, payload, pendingOrder):
		ticker = request.get("ticker")
		execPrice = pendingOrder.parameters["price"]
		execAmount = pendingOrder.parameters["amount"]
		isLimitOrder = pendingOrder.parameters["parameters"][1]

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

		pendingOrder.parameters["status"] = "placed" if isLimitOrder else "filled"
		return paper

class Order(object):
	def __init__(self, parameters, priceText, amountText, conversionText):
		self.parameters = parameters
		self.priceText = priceText
		self.amountText = amountText
		self.conversionText = conversionText

class DeleteView(View):
	def __init__(self, database, pathId, orderId):
		super().__init__()
		self.database = database
		self.pathId = pathId
		self.orderId = orderId

	@button(label="Cancel", style=ButtonStyle.red)
	async def delete(self, button: Button, interaction: Interaction):
		try:
			if self.authorId != interaction.user.id: return

			properties = await accountProperties.get(self.pathId)

			order = await database.document("details/openPaperOrders/{}/{}".format(self.pathId, self.orderId)).get()
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

			await database.document("details/openPaperOrders/{}/{}".format(self.pathId, self.orderId)).delete()
			await database.document("accounts/{}".format(self.pathId)).set({"paperTrader": properties["paperTrader"]}, merge=True)

			embed = Embed(title="Paper order has been canceled.", color=constants.colors["gray"])
			await interaction.response.edit_message(embed=embed, view=None)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /paper orders > delete action")