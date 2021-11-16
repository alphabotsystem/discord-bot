from os import environ
from time import time
from orjson import dumps
import aiohttp

from TickerParser import TickerParser
from helpers.utils import Utils


class Order(object):
	def __init__(self, parameters, priceText, amountText, conversionText):
		self.parameters = parameters
		self.priceText = priceText
		self.amountText = amountText
		self.conversionText = conversionText

class PaperTrader(object):
	def argument_cleanup(self, raw):
		cleanUp = {
			"buy": ["long"],
			"sell": ["short"],
			"stop-sell": ["sell stop", "short stop", "stop"]
		}
		for e in cleanUp:
			for i in cleanUp[e]:
				raw = raw.replace(i, e)

		raw = raw.replace("@", " @ ").replace("%", " % ").replace(",", ".")
		return " ".join(raw.split())

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
