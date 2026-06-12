from os import environ
from asyncio import gather, CancelledError
from traceback import format_exc

from discord import Embed
from discord.commands import slash_command, Option
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage

from commands.base import BaseCommand


class PriceCommand(BaseCommand):
	def price_embed(self, result):
		"""Builds the Discord embed for a single price snapshot returned by the v2 price endpoint.

		`result` is the parsed JSON from `fetch_price_via_v2`: `{"ok": True, "price", "change", ...}`
		on success or `{"ok": False, "error"}` for a user-facing miss."""
		if not result.get("ok"):
			embed = Embed(title=result.get("error") or "Requested quote is not available.", color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
			return embed

		change = result.get("change")
		title = "{}{}".format(result.get("price", ""), f" *({change})*" if change else "")
		changeRaw = result.get("changeRaw")
		if changeRaw is None:
			color = constants.colors["deep purple"]
		else:
			color = constants.colors["green"] if changeRaw >= 0 else constants.colors["red"]

		embed = Embed(title=title, description=result.get("priceConverted"), color=color)
		embed.set_author(name=result.get("title"), icon_url=result.get("thumbnailUrl"))
		embed.set_footer(text=result.get("exchange") or result.get("source"))
		return embed

	@slash_command(name="price", description="Fetch stock and crypto prices, forex rates, and other instrument data.")
	async def price(
		self,
		ctx,
		query: Option(str, "Ticker id of an asset, optionally followed by a venue. Up to 5, comma-separated.", name="query")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			parts = query.split(",")

			if len(parts) > 5:
				embed = Embed(title="Only up to 5 requests are allowed per command.", color=constants.colors["gray"])
				embed.set_author(name="Too many requests", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			tasks = []
			for part in parts:
				tokens = part.lower().split()
				if len(tokens) == 0: continue
				ticker = tokens[0]
				venue = " ".join(tokens[1:]) or None
				tasks.append(self.fetch_price_via_v2(ticker, venue))

			if len(tasks) == 0:
				embed = Embed(title="No ticker provided.", description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/prices"), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			[results, _] = await gather(
				gather(*tasks),
				ctx.defer()
			)

			embeds = [self.price_embed(result) for result in results]
			try: await ctx.interaction.edit_original_response(embeds=embeds)
			except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"p": Increment(len(embeds))}}, merge=True)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /price {query}")
			await self.unknown_error(ctx)