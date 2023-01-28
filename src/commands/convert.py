from os import environ
from asyncio import gather, CancelledError
from traceback import format_exc

from discord import Embed
from discord.commands import slash_command, Option
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_conversion

from commands.base import BaseCommand


class ConvertCommand(BaseCommand):
	@slash_command(name="convert", description="Convert between currencies, rates and assets.")
	async def convert(
		self,
		ctx,
		fromTicker: Option(str, "Ticker to convert from.", name="from", autocomplete=BaseCommand.autocomplete_from_ticker),
		toTicker: Option(str, "Ticker to convert to.", name="to", autocomplete=BaseCommand.autocomplete_to_ticker),
		amount: Option(float, "Amount to convert.", name="amount")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			platforms = request.get_platform_order_for("convert")
			[(payload, responseMessage), _] = await gather(
				process_conversion(request, fromTicker.upper(), toTicker.upper(), amount, platforms),
				ctx.defer()
			)

			if payload is None:
				errorMessage = "Requested conversion is not available." if responseMessage is None else responseMessage
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Conversion not available", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
			else:
				embed = Embed(title=f"{payload['quotePrice']} ≈ {payload['quoteConvertedPrice']}", description=f"{payload['quoteVolume']} {payload['quoteConvertedVolume']}", color=constants.colors[payload["messageColor"]])
				embed.set_author(name="Conversion", icon_url=self.bot.user.avatar.url)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"convert": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /convert {fromTicker} {toTicker} {amount}")
			await self.unknown_error(ctx)
