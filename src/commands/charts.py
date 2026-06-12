from os import environ
from time import time
from random import randint, choice
from asyncio import gather, CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, MediaActionsView, TryV2View, files_from_posts, content_from_posts

# Exchange-prefix heuristic for crypto referral buttons: the v2 parser returns
# exchange:ticker full names but no instrument-type field, so crypto is inferred
# from the venue prefix.
CRYPTO_EXCHANGE_PREFIXES = {
	"BINANCE", "COINBASE", "CRYPTO", "COINGECKO", "GECKOTERMINAL", "BYBIT", "OKX",
	"KUCOIN", "KRAKEN", "BITSTAMP", "BITFINEX", "BITMEX", "HUOBI", "GATEIO", "MEXC",
	"UNISWAP", "PANCAKESWAP", "POLONIEX", "BITTREX",
}


class ChartCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		response
	):
		start = time()

		if not response.get("ok"):
			message = response.get("error") or "Requested chart is not available."
			description = get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/charting")
			embed = Embed(title=message, description=description, color=constants.colors["gray"])
			embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass
			return

		posts = response.get("posts", [])
		meta = response.get("meta", {})
		files = files_from_posts(posts, request.authorId)
		content = content_from_posts(posts)

		isLicensed = self.bot.user.id not in constants.PRIMARY_BOTS
		actions = None
		if len(files) != 0:
			symbols = meta.get("resolvedSymbols", [])
			isCryptoRequest = any(s.split(":")[0].upper() in CRYPTO_EXCHANGE_PREFIXES for s in symbols)
			if isCryptoRequest and self.bot.user.id in constants.REFERRALS and not request.is_paid_user():
				referrals = constants.REFERRALS[self.bot.user.id]
				exchangeId = choice(list(referrals.keys()))
				actions = ReferralView(*referrals[exchangeId], user=ctx.author, command=ctx.command.mention, include_v2=not isLicensed)
			else:
				actions = MediaActionsView(user=ctx.author, command=ctx.command.mention, include_v2=not isLicensed)

		requestCheckpoint = time()
		request.set_delay("request", (requestCheckpoint - start) / max(1, len(files)))
		try: await ctx.interaction.edit_original_response(content=content, embeds=[], files=files, view=actions)
		except NotFound: pass
		request.set_delay("response", time() - requestCheckpoint)

		await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(meta.get("requestCount", 1))}}, merge=True)
		await self.log_request_v2("charts", request, meta, telemetry=request.telemetry)
		await self.cleanup(ctx, request, removeView=True, persistView=TryV2View() if len(files) != 0 and not isLicensed else None)

	@slash_command(name="c", description="Pull charts from TradingView.")
	async def c(
		self,
		ctx,
		query: Option(str, "Request arguments starting with ticker id.", name="query"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			if autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			await ctx.defer()
			response = await self.render_via_v2("chart " + query, request)

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, request, response)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /c {query} autodelete:{autodelete}")
			await self.unknown_error(ctx)

class ReferralView(MediaActionsView):
	def __init__(self, label, url, user=None, command=None, include_v2=True):
		super().__init__(user=user, command=command, include_v2=include_v2)
		self.add_item(Button(label=label, url=url, style=ButtonStyle.link))